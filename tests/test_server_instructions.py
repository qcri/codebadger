"""Server instructions give clients a workflow and only name tools that exist."""

import re
from unittest.mock import MagicMock

from fastmcp import Client, FastMCP

import main
from src.models import Config
from src.tools.mcp_tools import register_tools

FIELDS = {"codebase_hash", "method_id"}


async def _registered_tools():
    services = {name: MagicMock() for name in (
        "git_manager", "cpg_generator", "codebase_tracker", "query_executor",
        "code_browsing_service", "joern_server_manager",
    )}
    services["config"] = Config()
    mcp = FastMCP("TestServer")
    register_tools(mcp, services)
    async with Client(mcp) as client:
        return {t.name for t in await client.list_tools()}


async def test_instructions_name_only_registered_tools():
    mentioned = set(re.findall(r"\b([a-z]+(?:_[a-z]+)+)\b", main.SERVER_INSTRUCTIONS)) - FIELDS
    assert mentioned - await _registered_tools() == set()
    assert {"get_backend_status", "generate_cpg", "get_cpg_status"} <= mentioned


def test_server_publishes_the_instructions():
    assert main.mcp.instructions == main.SERVER_INSTRUCTIONS
