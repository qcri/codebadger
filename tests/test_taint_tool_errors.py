"""Taint tools fail with coded MCP errors, never {success: false} results."""

from unittest.mock import MagicMock

import pytest
from fastmcp import Client, FastMCP

from src.models import CodebaseInfo, Config, QueryResult
from src.tools.taint_analysis_tools import register_taint_analysis_tools

CODEBASE_HASH = "553642871dd4251d"


def _server(query_result: QueryResult) -> FastMCP:
    tracker = MagicMock()
    tracker.get_codebase.return_value = CodebaseInfo(
        codebase_hash=CODEBASE_HASH, source_type="local", source_path="/workspace/example",
        language="c", cpg_path="/workspace/example.cpg",
    )
    executor = MagicMock()
    executor.execute_query.return_value = query_result
    config = Config()
    config.cpg.taint_sources, config.cpg.taint_sinks = {}, {}  # as load_config sets them
    mcp = FastMCP("TestServer")
    register_taint_analysis_tools(mcp, {
        "codebase_tracker": tracker, "query_executor": executor, "config": config,
    })
    return mcp


@pytest.mark.parametrize("tool", ["find_taint_sources", "find_taint_sinks"])
@pytest.mark.parametrize(
    ("codebase_hash", "query_result", "code"),
    [
        (CODEBASE_HASH, QueryResult(success=False, error="busy", error_code="SERVER_BUSY"), "SERVER_BUSY"),
        (CODEBASE_HASH, QueryResult(success=False, error="slow", error_code="TIMEOUT"), "TIMEOUT"),
        (CODEBASE_HASH, QueryResult(success=False, error="bad query"), "QUERY_ERROR"),
        ("not-a-hash", QueryResult(success=True, data=[]), "VALIDATION_ERROR"),
    ],
)
async def test_endpoint_tools_raise_coded_errors(tool, codebase_hash, query_result, code):
    async with Client(_server(query_result)) as client:
        result = await client.call_tool(tool, {"codebase_hash": codebase_hash}, raise_on_error=False)

    assert result.is_error
    assert result.content[0].text.startswith(f"{code}: ")
    assert " Hint: " in result.content[0].text


async def test_taint_success_schemas_have_no_error_fields():
    async with Client(_server(QueryResult(success=True, data=[]))) as client:
        tools = await client.list_tools()

    assert [t.name for t in tools if "error" in t.output_schema.get("properties", {})] == []
