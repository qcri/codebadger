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


async def test_generate_cpg_description_names_exactly_its_statuses():
    import re

    from src.models import Config
    from src.tools.core_tools import register_core_tools

    mcp = FastMCP("TestServer")
    register_core_tools(mcp, {"codebase_tracker": MagicMock(), "config": Config()})
    async with Client(mcp) as client:
        tool = next(t for t in await client.list_tools() if t.name == "generate_cpg")

    returns = tool.description.split("Returns:", 1)[1].split("Examples:", 1)[0]
    status_line = re.search(r'"status": ([^,]*(?:\|[^,]*)*),', returns, re.S).group(1)
    described = set(re.findall(r'"([a-z_]+)"', status_line))
    assert described == set(tool.output_schema["properties"]["status"]["enum"])
