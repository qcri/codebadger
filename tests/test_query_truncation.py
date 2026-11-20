"""run_cpgql_query reports truncation when the server's own row cap cuts results off."""

import json
import re
from contextlib import nullcontext
from unittest.mock import MagicMock

import jsonschema
import pytest
from fastmcp import Client, FastMCP

from src.defaults import MAX_RESULT_ROWS
from src.models import CodebaseInfo
from src.services.code_browsing_service import CodeBrowsingService
from src.services.query_executor import QueryExecutor
from src.tools.code_browsing_tools import register_code_browsing_tools

CODEBASE_HASH = "553642871dd4251d"


def _executor(rows: int):
    """Real QueryExecutor whose Joern client has `rows` matches and honors `.take(n)` like Joern."""

    def run(query: str, timeout: int) -> dict:
        take = re.search(r"\.take\((\d+)\)", query)
        returned = min(rows, int(take.group(1))) if take else rows
        return {"success": True, "stdout": json.dumps(list(range(returned)))}

    client = MagicMock()
    client.check_health.return_value = True
    client.execute_query.side_effect = run
    manager = MagicMock()
    manager.get_server_port.return_value = 2000
    manager.get_or_create_client.return_value = client
    coordinator = MagicMock()
    coordinator.codebase_query_lock.return_value = nullcontext()
    executor = QueryExecutor(joern_server_manager=manager, config={}, coordinator=coordinator)
    return executor, client


def _sent_query(client) -> str:
    return client.execute_query.call_args.args[0]


@pytest.mark.parametrize(("rows", "truncated"), [(MAX_RESULT_ROWS, False), (MAX_RESULT_ROWS + 1, True)])
def test_default_cap_fetches_one_extra_row(rows: int, truncated: bool) -> None:
    executor, client = _executor(rows)

    result = executor.execute_query(CODEBASE_HASH, "/cpg", "cpg.method.name.l")

    assert f".take({MAX_RESULT_ROWS + 1})" in _sent_query(client)
    assert result.row_count == MAX_RESULT_ROWS
    assert result.data == list(range(MAX_RESULT_ROWS))
    assert result.truncated is truncated


def test_dataflow_cap_reports_truncation() -> None:
    executor, client = _executor(51)

    result = executor.execute_query(CODEBASE_HASH, "/cpg", "sink.reachableByFlows(src).l")

    assert ".take(51)" in _sent_query(client)
    assert result.row_count == 50
    assert result.truncated is True


def test_explicit_caller_limit_is_authoritative() -> None:
    executor, client = _executor(5)

    result = executor.execute_query(CODEBASE_HASH, "/cpg", "cpg.method.name.l", limit=5)

    assert ".take(5)" in _sent_query(client)
    assert result.row_count == 5
    assert result.truncated is False


def test_clamped_caller_limit_reports_truncation() -> None:
    executor, client = _executor(MAX_RESULT_ROWS + 1)

    result = executor.execute_query(CODEBASE_HASH, "/cpg", "cpg.method.name.l", limit=10**9)

    assert f".take({MAX_RESULT_ROWS + 1})" in _sent_query(client)
    assert result.row_count == MAX_RESULT_ROWS
    assert result.truncated is True


def _server(executor: QueryExecutor) -> FastMCP:
    tracker = MagicMock()
    tracker.get_codebase.return_value = CodebaseInfo(
        codebase_hash=CODEBASE_HASH,
        source_type="local",
        source_path="/workspace/example",
        language="c",
        cpg_path="/workspace/example.cpg",
    )
    executor.codebase_tracker = tracker
    mcp = FastMCP("TestServer")
    register_code_browsing_tools(
        mcp,
        {
            "code_browsing_service": CodeBrowsingService(tracker, executor),
            "codebase_tracker": tracker,
            "query_executor": executor,
        },
    )
    return mcp


@pytest.mark.parametrize(("rows", "truncated"), [(MAX_RESULT_ROWS, False), (MAX_RESULT_ROWS + 1, True)])
async def test_run_cpgql_query_reports_server_cap(rows: int, truncated: bool) -> None:
    executor, _ = _executor(rows)
    async with Client(_server(executor)) as client:
        tools = await client.list_tools()
        schema = next(t for t in tools if t.name == "run_cpgql_query").output_schema
        result = await client.call_tool(
            "run_cpgql_query", {"codebase_hash": CODEBASE_HASH, "query": "cpg.method.name.l"}
        )

    reply = result.structured_content
    jsonschema.validate(reply, schema)
    assert reply["success"] is True
    assert reply["row_count"] == MAX_RESULT_ROWS
    assert reply.get("truncated", False) is truncated
    assert ("truncation_note" in reply) is truncated
