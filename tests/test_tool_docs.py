"""docs/available-tools.md must list exactly the tools the server registers."""

import pathlib
import re
from unittest.mock import MagicMock

from fastmcp import Client, FastMCP

from src.models import Config
from src.tools.mcp_tools import register_tools

DOC = pathlib.Path("docs/available-tools.md")


def _documented():
    return set(re.findall(r"^\| `([a-z_]+)` \|", DOC.read_text(), re.MULTILINE))


async def test_tool_reference_matches_registered_tools():
    services = {name: MagicMock() for name in (
        "git_manager", "cpg_generator", "codebase_tracker", "query_executor",
        "code_browsing_service", "joern_server_manager",
    )}
    services["config"] = Config()
    mcp = FastMCP("TestServer")
    register_tools(mcp, services)
    async with Client(mcp) as client:
        registered = {t.name for t in await client.list_tools()}

    assert {"undocumented": registered - _documented(), "stale": _documented() - registered} == {
        "undocumented": set(), "stale": set(),
    }
