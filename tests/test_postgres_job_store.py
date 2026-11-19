"""Tests for the Phase-3b Postgres job store.

The roundtrip/SKIP-LOCKED tests need a real Postgres — set CODEBADGER_TEST_PG_DSN
to run them. Construction is always tested (must not connect).
"""

from contextlib import closing
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest


def test_construction_does_not_connect():
    """Constructing the store must not require a live DB."""
    from src.utils.postgres_job_store import PostgresJobStore
    store = PostgresJobStore("postgresql://nobody@127.0.0.1:1/none")  # bogus, never dialed
    assert store.dsn.startswith("postgresql://")


@pytest.fixture
def store(isolated_pg_dsn):
    from src.utils.postgres_job_store import PostgresJobStore
    with closing(PostgresJobStore(isolated_pg_dsn)) as job_store:
        job_store.init_schema()
        yield job_store


def test_enqueue_claim_complete(store):
    jid, status = store.enqueue_job("h1", "generate_cpg", {"language": "c"})
    assert status == "submitted" and jid
    assert store.count_jobs("queued") == 1

    claimed = store.claim_next_job("generate_cpg")
    assert claimed["id"] == jid
    assert claimed["codebase_hash"] == "h1"
    assert claimed["payload"] == {"language": "c"}
    assert store.count_jobs("running") == 1

    store.complete_job(jid, claimed["attempts"], result={"ok": True})
    assert store.get_job(jid)["status"] == "done"


def test_dedup_backpressure_and_skiplocked(store):
    id1, s1 = store.enqueue_job("h1", "generate_cpg", {})
    id2, s2 = store.enqueue_job("h1", "generate_cpg", {})
    assert s1 == "submitted" and s2 == "duplicate" and id1 == id2

    store.enqueue_job("h2", "generate_cpg", {})
    _id, full = store.enqueue_job("h3", "generate_cpg", {}, max_queued=2)
    assert full == "queue_full"

    # Two concurrent-style claims return distinct rows (SKIP LOCKED), oldest first.
    a = store.claim_next_job("generate_cpg")
    b = store.claim_next_job("generate_cpg")
    assert {a["codebase_hash"], b["codebase_hash"]} == {"h1", "h2"}
    assert a["codebase_hash"] == "h1"
    assert store.claim_next_job("generate_cpg") is None


def test_concurrent_enqueues_respect_queue_limit(store):
    store.enqueue_job("baseline", "generate_cpg", {}, max_queued=2)
    with store._connect() as conn:
        conn.execute("""
            CREATE FUNCTION delay_test_enqueue() RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN
                PERFORM pg_sleep(0.2);
                RETURN NEW;
            END;
            $$
        """)
        conn.execute("""
            CREATE TRIGGER delay_test_enqueue BEFORE INSERT ON jobs
            FOR EACH ROW EXECUTE FUNCTION delay_test_enqueue()
        """)
        conn.commit()

    barrier = Barrier(2)

    def submit(codebase_hash):
        barrier.wait()
        return store.enqueue_job(codebase_hash, "generate_cpg", {}, max_queued=2)[1]

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            statuses = list(pool.map(submit, ("parallel-a", "parallel-b")))
    finally:
        with store._connect() as conn:
            conn.execute("DROP TRIGGER delay_test_enqueue ON jobs")
            conn.execute("DROP FUNCTION delay_test_enqueue()")
            conn.commit()

    assert sorted(statuses) == ["queue_full", "submitted"]
    assert store.count_jobs("queued") == 2


def test_expired_claim_is_recovered_and_fenced(store):
    from unittest.mock import Mock

    jid, _ = store.enqueue_job("h1", "generate_cpg", {})
    first = store.claim_next_job("generate_cpg")
    assert store.renew_job_lease(jid, first["attempts"])
    assert store.claim_next_job("generate_cpg") is None
    with store._connect() as conn:
        conn.execute("UPDATE jobs SET lease_expires_at = '-infinity' WHERE id = %s", (jid,))

    assert not store.renew_job_lease(jid, first["attempts"])
    second = store.claim_next_job("generate_cpg")
    assert second["id"] == jid
    assert second["attempts"] == first["attempts"] + 1
    publish = Mock()
    assert not store.renew_job_lease(jid, first["attempts"], publish=publish)
    publish.assert_not_called()
    assert store.renew_job_lease(jid, second["attempts"], publish=publish)
    publish.assert_called_once()
    assert not store.complete_job(jid, first["attempts"], result={"owner": "first"})
    assert store.complete_job(jid, second["attempts"], result={"owner": "second"})
    assert not store.fail_job(jid, first["attempts"], "late failure")
    assert store.get_job(jid)["result"] == '{"owner": "second"}'


def test_schema_upgrade_recovers_legacy_running_job(store):
    jid, _ = store.enqueue_job("h1", "generate_cpg", {})
    with store._connect() as conn:
        conn.execute("ALTER TABLE jobs DROP COLUMN lease_expires_at")
        conn.execute("UPDATE jobs SET status = 'running', attempts = 1 WHERE id = %s", (jid,))

    store.init_schema()
    job = store.claim_next_job("generate_cpg")
    assert (job["id"], job["attempts"]) == (jid, 2)
