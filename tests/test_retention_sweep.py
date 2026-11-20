"""The retention sweep deletes expired cache rows and old finished jobs only."""

from contextlib import closing
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

from src.config import load_config
from src.utils.cache_cleanup import CacheCleanupScheduler

HOUR = 3600


def _ago(**delta):
    return (datetime.now(timezone.utc) - timedelta(**delta)).isoformat()


@pytest.fixture
def db(isolated_pg_dsn):
    from src.utils.postgres_db_manager import PostgresDBManager
    with closing(PostgresDBManager(isolated_pg_dsn)) as manager:
        manager.init_schema()
        yield manager


def test_sweep_keeps_recent_history_and_active_work(db):
    for name, created in (("old", _ago(hours=2)), ("fresh", _ago(minutes=5))):
        db.cache_tool_output(name, "h" * 16, {"q": name}, {"success": True})
        with db._connect() as conn:
            conn.execute("UPDATE tool_cache SET created_at = %s WHERE tool_name = %s", (created, name))
            conn.commit()
    jobs = {
        "old-done": ("done", _ago(days=10)), "old-failed": ("failed", _ago(days=10)),
        "new-done": ("done", _ago(hours=1)), "old-queued": ("queued", _ago(days=10)),
        "old-running": ("running", _ago(days=10)),
    }
    with db._connect() as conn:
        for key, (status, updated) in jobs.items():
            conn.execute(
                "INSERT INTO jobs (codebase_hash, job_type, status, payload, attempts, created_at, updated_at) "
                "VALUES (%s, 'generate_cpg', %s, '{}', 1, %s, %s)",
                (key, status, updated, updated),
            )
        conn.commit()

    removed = CacheCleanupScheduler(db, max_age_seconds=HOUR, job_max_age_seconds=7 * 24 * HOUR).sweep()

    assert removed == {"cache": 1, "jobs": 2}
    with db._connect() as conn:
        cache = {r["tool_name"] for r in conn.execute("SELECT tool_name FROM tool_cache").fetchall()}
        left = {r["codebase_hash"] for r in conn.execute("SELECT codebase_hash FROM jobs").fetchall()}
    assert cache == {"fresh"}
    assert left == {"new-done", "old-queued", "old-running"}


def test_one_failing_part_does_not_stop_the_other():
    db = MagicMock()
    db.cleanup_expired_cache.side_effect = RuntimeError("cache table locked")
    db.purge_finished_jobs.return_value = 3

    assert CacheCleanupScheduler(db, job_max_age_seconds=HOUR).sweep() == {"cache": 0, "jobs": 3}
    db.purge_finished_jobs.assert_called_once_with(HOUR)


def test_job_history_is_left_alone_unless_configured():
    db = MagicMock()
    db.cleanup_expired_cache.return_value = 0
    CacheCleanupScheduler(db).sweep()
    db.purge_finished_jobs.assert_not_called()


def test_retention_settings_load_from_yaml_and_env(tmp_path, monkeypatch):
    path = tmp_path / "config.yaml"
    path.write_text("retention:\n  interval_seconds: 600\n  job_max_age_hours: 24\n")
    config = load_config(str(path))
    assert (config.retention.enabled, config.retention.interval_seconds,
            config.retention.cache_max_age_seconds, config.retention.job_max_age_hours) == (True, 600, 3600, 24)

    monkeypatch.setenv("RETENTION_JOB_MAX_AGE_HOURS", "48")
    assert load_config(None).retention.job_max_age_hours == 48
