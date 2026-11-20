from unittest.mock import MagicMock

import pytest
from fastmcp import Client, FastMCP

from src.models import CodebaseInfo, QueryResult
from src.services.code_browsing_service import CodeBrowsingService
from src.tools.code_browsing_tools import register_code_browsing_tools

CODEBASE_HASH = "553642871dd4251d"
TOOL_ARGUMENTS = {
    "list_methods": {},
    "list_calls": {},
    "list_parameters": {"method_name": "main"},
    "get_type_definition": {"type_name": "request_t"},
}


def _server(query_result: QueryResult) -> FastMCP:
    tracker = MagicMock()
    tracker.get_codebase.return_value = CodebaseInfo(
        codebase_hash=CODEBASE_HASH,
        source_type="local",
        source_path="/workspace/example",
        language="c",
        cpg_path="/workspace/example.cpg",
    )
    executor = MagicMock()
    executor.execute_query.return_value = query_result
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


@pytest.mark.parametrize("tool_name", sorted(TOOL_ARGUMENTS))
@pytest.mark.parametrize(
    ("codebase_hash", "query_result", "error_code"),
    [
        (
            CODEBASE_HASH,
            QueryResult(success=False, error="busy", error_code="SERVER_BUSY"),
            "SERVER_BUSY",
        ),
        (
            CODEBASE_HASH,
            QueryResult(success=False, error="slow", error_code="TIMEOUT"),
            "TIMEOUT",
        ),
        (
            CODEBASE_HASH,
            QueryResult(success=False, error="bad query"),
            "QUERY_ERROR",
        ),
        ("not-a-hash", QueryResult(success=True, data=[]), "VALIDATION_ERROR"),
    ],
)
async def test_browsing_failures_raise_coded_tool_errors(
    tool_name: str,
    codebase_hash: str,
    query_result: QueryResult,
    error_code: str,
) -> None:
    arguments = {"codebase_hash": codebase_hash, **TOOL_ARGUMENTS[tool_name]}
    async with Client(_server(query_result)) as client:
        tools = await client.list_tools()
        schema = next(t for t in tools if t.name == tool_name).output_schema
        result = await client.call_tool(tool_name, arguments, raise_on_error=False)

    assert result.is_error
    assert result.content[0].text.startswith(f"{error_code}: ")
    assert " Hint: " in result.content[0].text
    # Success schemas no longer carry error fields.
    assert "error" not in schema["properties"] and "error_code" not in schema["properties"]


@pytest.mark.parametrize(
    ("tool_name", "arguments"),
    [
        ("get_call_graph", {"method_name": "main"}),
        ("get_cfg", {"method_name": "main"}),
        ("find_bounds_checks", {"buffer_access_location": "main.c:3"}),
    ],
)
@pytest.mark.parametrize(
    ("query_result", "error_code"),
    [
        (
            QueryResult(success=False, error="busy", error_code="SERVER_BUSY"),
            "SERVER_BUSY",
        ),
        (
            QueryResult(success=False, error="slow", error_code="TIMEOUT"),
            "TIMEOUT",
        ),
        (QueryResult(success=False, error="bad query"), "QUERY_ERROR"),
    ],
)
async def test_summary_tool_failures_keep_executor_error_code(
    tool_name: str,
    arguments: dict[str, str],
    query_result: QueryResult,
    error_code: str,
) -> None:
    async with Client(_server(query_result)) as client:
        result = await client.call_tool(
            tool_name,
            {"codebase_hash": CODEBASE_HASH, **arguments},
            raise_on_error=False,
        )

    assert result.is_error
    assert result.content[0].text.startswith(f"{error_code}: ")


async def test_unexpected_failures_become_internal_errors_without_host_paths() -> None:
    service = MagicMock()
    service.list_methods.side_effect = RuntimeError("boom in /mnt/secret/playground/cpgs/x/cpg.bin")
    from src.tools.code_browsing_tools import register_code_browsing_tools

    mcp = FastMCP("TestServer")
    register_code_browsing_tools(mcp, {
        "code_browsing_service": service, "codebase_tracker": MagicMock(), "query_executor": MagicMock(),
    })
    async with Client(mcp) as client:
        result = await client.call_tool("list_methods", {"codebase_hash": CODEBASE_HASH}, raise_on_error=False)

    text = result.content[0].text
    assert result.is_error and text.startswith("INTERNAL_ERROR: boom in ")
    assert "/mnt/secret" not in text


async def test_run_cpgql_query_validation_failure_is_a_validation_error(monkeypatch) -> None:
    from src.utils.cpgql_validator import CPGQLValidator

    monkeypatch.setattr(CPGQLValidator, "validate_query", staticmethod(lambda q: {
        "valid": False, "errors": [{"message": "Unmatched parentheses in query"}],
    }))
    async with Client(_server(QueryResult(success=True, data=[]))) as client:
        result = await client.call_tool(
            "run_cpgql_query",
            {"codebase_hash": CODEBASE_HASH, "query": "cpg.method.name(.l", "validate": True},
            raise_on_error=False,
        )

    assert result.is_error
    assert result.content[0].text.startswith("VALIDATION_ERROR: Query validation failed: ")
