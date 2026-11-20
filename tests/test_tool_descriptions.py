"""Tool descriptions must describe the replies the tools actually return."""

from unittest.mock import MagicMock

from fastmcp import Client, FastMCP

from src.services.code_browsing_service import CodeBrowsingService
from src.tools.code_browsing_tools import register_code_browsing_tools


async def _tool(name):
    tracker, executor = MagicMock(), MagicMock()
    mcp = FastMCP("TestServer")
    register_code_browsing_tools(mcp, {
        "code_browsing_service": CodeBrowsingService(tracker, executor),
        "codebase_tracker": tracker,
        "query_executor": executor,
    })
    async with Client(mcp) as client:
        return next(t for t in await client.list_tools() if t.name == name)


async def test_run_cpgql_query_description_matches_output_schema():
    tool = await _tool("run_cpgql_query")
    returns = tool.description.split("Returns:", 1)[1].split("Notes:", 1)[0]

    missing = [key for key in tool.output_schema["properties"] if f'"{key}"' not in returns]
    assert missing == []
    assert "stdout" not in returns and "stderr" not in returns
