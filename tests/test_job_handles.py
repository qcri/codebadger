"""Job ids, bounded history, and atomic cancellation of queued builds."""

import threading
from contextlib import closing
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.tools.core_tools import CPGGenerationQueue, DurableCPGQueue


@pytest.fixture
def store(isolated_pg_dsn):
    from src.utils.postgres_db_manager import PostgresDBManager
    with closing(PostgresDBManager(isolated_pg_dsn)) as manager:
        manager.init_schema()
        yield manager


def test_duplicate_submission_resolves_to_the_active_job(store):
    job_id, status = store.enqueue_job("h" * 16, "generate_cpg", {})
    assert status == "submitted"
    assert store.enqueue_job("h" * 16, "generate_cpg", {}) == (job_id, "duplicate")


def test_cancel_only_while_queued(store):
    queued, _ = store.enqueue_job("a" * 16, "generate_cpg", {})
    running, _ = store.enqueue_job("b" * 16, "generate_cpg", {})
    claimed = store.claim_next_job("generate_cpg")
    assert claimed["id"] == queued  # oldest first

    assert store.cancel_queued_job(queued) == (False, "running", "a" * 16)  # claim won
    assert store.cancel_queued_job(running) == (True, "cancelled", "b" * 16)
    assert store.cancel_queued_job(running) == (False, "cancelled", "b" * 16)
    assert store.cancel_queued_job(999999) == (False, None, None)
    # A cancelled job frees the codebase for a new submission.
    assert store.enqueue_job("b" * 16, "generate_cpg", {})[1] == "submitted"


def test_cancel_racing_a_claim_has_exactly_one_winner(store):
    for attempt in range(10):
        job_id, _ = store.enqueue_job(f"{attempt:016x}", "generate_cpg", {})
        barrier = threading.Barrier(2)
        results = {}

        def claim():
            barrier.wait()
            results["claim"] = store.claim_next_job("generate_cpg")

        def cancel():
            barrier.wait()
            results["cancel"] = store.cancel_queued_job(job_id)

        threads = [threading.Thread(target=claim), threading.Thread(target=cancel)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        cancelled, status, _ = results["cancel"]
        claimed = results["claim"] is not None and results["claim"]["id"] == job_id
        assert cancelled != claimed, (attempt, results)
        assert status == ("cancelled" if cancelled else "running")
        assert store.get_job(job_id)["status"] == status


def test_history_is_paged_newest_first_and_filterable(store):
    ids = [store.enqueue_job(f"{i:016x}", "generate_cpg", {})[0] for i in range(25)]
    store.cancel_queued_job(ids[0])

    page, total = store.list_jobs(offset=0, limit=10)
    assert total == 25 and [j["id"] for j in page] == ids[::-1][:10]
    assert set(page[0]) == {"id", "codebase_hash", "job_type", "status", "attempts",
                            "created_at", "updated_at", "error"}
    cancelled, n = store.list_jobs(status="cancelled")
    assert n == 1 and cancelled[0]["id"] == ids[0]
    assert store.list_jobs(codebase_hash=f"{3:016x}")[1] == 1


async def test_queues_report_job_ids_only_when_durable():
    durable = DurableCPGQueue.__new__(DurableCPGQueue)
    durable.store = MagicMock()
    durable.store.enqueue_job.return_value = (42, "duplicate")
    durable._maxsize = 0
    assert await durable.submit_job("h", {"services": object(), "x": 1}) == ("duplicate", 42)
    assert durable.store.enqueue_job.call_args.args[2] == {"x": 1}  # services never persisted

    memory = CPGGenerationQueue.__new__(CPGGenerationQueue)
    memory.submit = AsyncMock(return_value="submitted")
    assert await memory.submit_job("h", {}) == ("submitted", None)


async def test_generate_cpg_returns_the_job_handle(tmp_path):
    import json
    import os
    from unittest.mock import patch

    from fastmcp import Client, FastMCP

    from src.models import Config
    from src.tools.core_tools import register_core_tools

    queue = DurableCPGQueue.__new__(DurableCPGQueue)
    queue.store = MagicMock()
    queue.store.enqueue_job.side_effect = [(7, "submitted"), (7, "duplicate")]
    queue._maxsize = 0
    services = {"codebase_tracker": MagicMock(), "config": Config(), "cpg_queue": queue,
                "joern_server_manager": MagicMock(), "git_manager": MagicMock()}
    services["codebase_tracker"].get_codebase.return_value = None
    request = {"source_type": "snippet", "language": "c", "code": "int main(void){return 0;}"}
    with patch("src.tools.core_tools.os.path.abspath", return_value=str(tmp_path)), \
         patch("src.tools.core_tools.os.path.dirname", return_value=str(tmp_path)), \
         patch("src.tools.core_tools.os.path.join", side_effect=os.path.join):
        mcp = FastMCP("TestServer")
        register_core_tools(mcp, services)
        async with Client(mcp) as client:
            first = (await client.call_tool("generate_cpg", request)).structured_content
            again = (await client.call_tool("generate_cpg", request)).structured_content

    assert (first["status"], first["job_id"]) == ("generating", 7)
    assert again["job_id"] == 7 and "already in progress" in again["message"]
