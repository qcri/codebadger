"""Joern compiler diagnostics must surface as QUERY_ERROR, not as successful data."""

import json
from contextlib import nullcontext
from unittest.mock import MagicMock

import pytest
from fastmcp import Client, FastMCP

from src.models import CodebaseInfo
from src.services.code_browsing_service import CodeBrowsingService
from src.services.query_executor import QueryExecutor
from src.tools.code_browsing_tools import register_code_browsing_tools

CODEBASE_HASH = "553642871dd4251d"

# Captured verbatim from Joern on 2026-11-20.
NOT_FOUND = (
    "-- [E008] Not Found Error: -----------------------------------------------------\n"
    "1 |(cpg.methd.name).take(10001).toJsonPretty\n"
    "  | ^^^^^^^^^\n"
    "  |value methd is not a member of io.shiftleft.codepropertygraph.generated.Cpg\n"
    "1 error found"
)
SYNTAX = (
    "-- [E040] Syntax Error: --------------------------------------------------------\n"
    "67 |                   List[io.shiftleft.codepropertygraph.generated.nodes.Method],\n"
    "   |                                                                              ^\n"
    "   |                                        unindent expected, but ',' found\n"
)
PLAIN = "-- Error: ----------------------------------------------------------------------\n1 |x\n  |bad\n1 error found"


def _executor(stdout: str) -> QueryExecutor:
    client = MagicMock()
    client.check_health.return_value = True
    client.execute_query.return_value = {"success": True, "stdout": stdout}
    manager = MagicMock()
    manager.get_server_port.return_value = 2000
    manager.get_or_create_client.return_value = client
    coordinator = MagicMock()
    coordinator.codebase_query_lock.return_value = nullcontext()
    return QueryExecutor(joern_server_manager=manager, config={}, coordinator=coordinator)


@pytest.mark.parametrize("stdout", [NOT_FOUND, SYNTAX, PLAIN, "\x1b[31m" + NOT_FOUND + "\x1b[0m"])
def test_compiler_diagnostics_fail_the_query(stdout):
    result = _executor(stdout).execute_query(CODEBASE_HASH, "/cpg", "cpg.methd.name.l")

    assert result.success is False
    assert result.error_code == "QUERY_ERROR"
    assert result.error.startswith("Query failed to compile: -- ")
    assert result.data is None or result.data == []


def test_long_diagnostics_are_bounded():
    result = _executor(NOT_FOUND + "\n" + "  | explanation\n" * 1000).execute_query(
        CODEBASE_HASH, "/cpg", "cpg.methd.name.l"
    )
    assert len(result.error) <= 2000 + len(" …(truncated)")
    assert result.error.endswith("…(truncated)")


@pytest.mark.parametrize(
    ("stdout", "data"),
    [
        # A compiled query may return text that looks like a diagnostic.
        ("<codebadger_result>\n-- Error: in user code\n</codebadger_result>", ["-- Error: in user code"]),
        (json.dumps(["-- [E008] Not Found Error: quoted in source"], indent=2),
         ["-- [E008] Not Found Error: quoted in source"]),
    ],
)
def test_results_resembling_diagnostics_still_succeed(stdout, data):
    result = _executor(stdout).execute_query(CODEBASE_HASH, "/cpg", "cpg.literal.code.l")

    assert result.success is True
    assert result.data == data


def _server(executor: QueryExecutor) -> FastMCP:
    tracker = MagicMock()
    tracker.get_codebase.return_value = CodebaseInfo(
        codebase_hash=CODEBASE_HASH, source_type="local", source_path="/workspace/example",
        language="c", cpg_path="/workspace/example.cpg",
    )
    executor.codebase_tracker = tracker
    mcp = FastMCP("TestServer")
    register_code_browsing_tools(mcp, {
        "code_browsing_service": CodeBrowsingService(tracker, executor),
        "codebase_tracker": tracker,
        "query_executor": executor,
    })
    return mcp


async def test_run_cpgql_query_reports_compile_error():
    async with Client(_server(_executor(NOT_FOUND))) as client:
        result = await client.call_tool(
            "run_cpgql_query", {"codebase_hash": CODEBASE_HASH, "query": "cpg.methd.name.l"},
            raise_on_error=False,
        )

    assert result.is_error
    text = result.content[0].text
    assert text.startswith("QUERY_ERROR: Query failed to compile")
    assert "value methd is not a member" in text


async def test_broken_template_fails_get_call_graph():
    async with Client(_server(_executor(SYNTAX))) as client:
        result = await client.call_tool(
            "get_call_graph", {"codebase_hash": CODEBASE_HASH, "method_name": "main"},
            raise_on_error=False,
        )

    assert result.is_error
    assert result.content[0].text.startswith("QUERY_ERROR: Query failed to compile")
