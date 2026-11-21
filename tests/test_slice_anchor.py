"""A missing slice anchor is a coded error, never an empty or substituted slice."""

from unittest.mock import MagicMock

from fastmcp import Client, FastMCP

from src.models import CodebaseInfo, Config, QueryResult
from src.tools.taint_analysis_tools import register_taint_analysis_tools


async def test_missing_anchor_is_a_validation_error_and_not_cached():
    tracker = MagicMock()
    tracker.get_codebase.return_value = CodebaseInfo(
        codebase_hash="a" * 16, source_type="local", source_path="/x", language="c", cpg_path="/cpg")
    executor = MagicMock()
    executor.execute_query.return_value = QueryResult(success=True, data=[
        "ANCHOR_NOT_FOUND: no call to 'missing_call' on line 14 in method report\n"
        "Calls on line 14: show_value"])  # the executor strips the result markers
    db = MagicMock()
    db.get_cached_tool_output.return_value = None
    mcp = FastMCP("TestServer")
    register_taint_analysis_tools(mcp, {"codebase_tracker": tracker, "query_executor": executor,
                                        "config": Config(), "db_manager": db})
    async with Client(mcp) as client:
        result = await client.call_tool("get_program_slice", {
            "codebase_hash": "a" * 16, "location": "audit.c:14:missing_call"}, raise_on_error=False)

    assert result.is_error
    assert result.content[0].text.startswith("VALIDATION_ERROR: no call to 'missing_call' on line 14")
    db.cache_tool_output.assert_not_called()
