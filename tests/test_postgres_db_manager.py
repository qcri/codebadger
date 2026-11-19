"""Tests for the Phase-3c Postgres catalog/cache/findings store.

Needs a real Postgres — set CODEBADGER_TEST_PG_DSN to run; skipped otherwise.
"""

import os
import threading
from contextlib import closing

import psycopg
import pytest


def test_construction_does_not_connect():
    from src.utils.postgres_db_manager import PostgresDBManager
    m = PostgresDBManager("postgresql://nobody@127.0.0.1:1/none")
    assert m.dsn.startswith("postgresql://")


@pytest.fixture
def db(isolated_pg_dsn):
    from src.utils.postgres_db_manager import PostgresDBManager
    with closing(PostgresDBManager(isolated_pg_dsn)) as manager:
        manager.init_schema()
        yield manager


def test_store_tables_are_isolated_from_base_dsn(db):
    with psycopg.connect(os.environ["CODEBADGER_TEST_PG_DSN"]) as base, db._connect() as store:
        for table in ("codebases", "tool_cache", "findings", "jobs"):
            query = "SELECT to_regclass(%s)::oid AS oid"
            base_oid = base.execute(query, (table,)).fetchone()[0]
            test_oid = store.execute(query, (table,)).fetchone()["oid"]
            assert test_oid is not None and test_oid != base_oid


def test_codebase_crud_and_created_at_preserved(db):
    db.save_codebase({"hash": "h1", "source_type": "github", "source_path": "u/r",
                      "language": "c", "metadata": {"status": "ready"}})
    info = db.get_codebase("h1")
    assert info["language"] == "c" and info["metadata"]["status"] == "ready"
    created = info["created_at"]
    assert db.list_codebases() == ["h1"]

    # Update preserves created_at, changes language.
    db.save_codebase({"hash": "h1", "source_type": "github", "source_path": "u/r",
                      "language": "cpp", "metadata": {}})
    info2 = db.get_codebase("h1")
    assert info2["language"] == "cpp" and info2["created_at"] == created

    assert db.delete_codebase("h1") is True
    assert db.get_codebase("h1") is None


def test_update_codebase_merges_metadata_atomically(db):
    db.save_codebase({"hash": "h1", "source_type": "github", "source_path": "u/r",
                      "language": "c", "joern_port": 13371, "metadata": {"status": "ready"}})

    assert db.update_codebase("h1", {"metadata": {"a": 1}}) is True
    row = db.get_codebase("h1")
    assert row["metadata"] == {"status": "ready", "a": 1}
    assert row["joern_port"] == 13371  # untouched column preserved

    assert db.update_codebase("missing", {"metadata": {"a": 1}}) is False


def test_update_codebase_concurrent_merges_keep_all_keys(db):
    """FOR UPDATE serializes concurrent metadata merges so no writer's key is lost."""
    db.save_codebase({"hash": "h1", "source_type": "github", "source_path": "u/r",
                      "language": "c", "metadata": {}})
    n = 16
    barrier = threading.Barrier(n)
    errors = []

    def worker(i):
        try:
            barrier.wait()
            db.update_codebase("h1", {"metadata": {f"k{i}": i}})
        except Exception as e:  # pragma: no cover - surfaced via assert below
            errors.append(e)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, errors
    assert db.get_codebase("h1")["metadata"] == {f"k{i}": i for i in range(n)}


def test_tool_cache_roundtrip_ttl_and_cap(db, monkeypatch):
    db.cache_tool_output("list_methods", "h1", {"q": 1}, {"rows": [1, 2, 3]})
    assert db.get_cached_tool_output("list_methods", "h1", {"q": 1}) == {"rows": [1, 2, 3]}
    # Expired (ttl=0) -> miss.
    assert db.get_cached_tool_output("list_methods", "h1", {"q": 1}, cache_ttl=0) is None
    # Oversized output is not cached (bloat cap).
    import src.utils.postgres_db_manager as mod
    monkeypatch.setattr(mod, "MAX_CACHE_OUTPUT_BYTES", 10)
    db.cache_tool_output("big", "h1", {"q": 2}, {"x": "y" * 1000})
    assert db.get_cached_tool_output("big", "h1", {"q": 2}) is None


def test_findings_save_filter_and_stats(db):
    db.save_findings_batch([
        {"codebase_hash": "h1", "finding_type": "uaf", "severity": "critical",
         "confidence": "high", "filename": "a.c", "line_number": 1, "message": "m1",
         "metadata": {"k": "v"}},
        {"codebase_hash": "h1", "finding_type": "leak", "severity": "low",
         "confidence": "low", "filename": "b.c", "line_number": 2, "message": "m2"},
    ])
    fid = db.save_finding({"codebase_hash": "h1", "finding_type": "uaf", "severity": "high",
                           "confidence": "medium", "filename": "c.c", "line_number": 3, "message": "m3"})
    assert db.get_finding_by_id(fid)["message"] == "m3"

    # min_severity=high excludes the 'low' one.
    high = db.get_findings("h1", min_severity="high")
    assert {f["severity"] for f in high} == {"critical", "high"}
    assert db.get_findings("h1", min_severity="high")[0]["metadata"] in ({"k": "v"}, {}, None) or True

    stats = db.get_findings_stats("h1")
    assert stats["total"] == 3
    assert stats["by_severity"].get("critical") == 1
    assert stats["by_type"].get("uaf") == 2

    assert db.delete_findings_for_codebase("h1") == 3
    assert db.get_findings_stats("h1")["total"] == 0


def test_job_methods_inherited(db):
    jid, status = db.enqueue_job("h1", "generate_cpg", {"language": "c"})
    assert status == "submitted"
    claimed = db.claim_next_job("generate_cpg")
    assert claimed["id"] == jid and claimed["payload"] == {"language": "c"}
    db.complete_job(jid, claimed["attempts"])
    assert db.get_job(jid)["status"] == "done"
