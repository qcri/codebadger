"""Core tools fail with coded MCP errors; CPG state outcomes stay normal results."""

from unittest.mock import MagicMock

from fastmcp import Client, FastMCP

from src.models import Config
from src.tools.core_tools import register_core_tools

HASH = "553642871dd4251d"


def _server(tracker):
    mcp = FastMCP("TestServer")
    register_core_tools(mcp, {
        "codebase_tracker": tracker, "config": Config(), "joern_server_manager": MagicMock(),
    })
    return mcp


async def test_unexpected_failures_become_redacted_internal_errors():
    tracker = MagicMock()
    tracker.get_codebase.side_effect = RuntimeError("db down at /srv/private/pgdata/base")
    async with Client(_server(tracker)) as client:
        results = [
            await client.call_tool(name, {"codebase_hash": HASH}, raise_on_error=False)
            for name in ("get_cpg_status", "remove_cpg")
        ]

    for result in results:
        text = result.content[0].text
        assert result.is_error and text.startswith("INTERNAL_ERROR: db down at ")
        assert "/srv/private" not in text and " Hint: " in text


async def test_removing_an_unknown_cpg_is_a_validation_error():
    tracker = MagicMock()
    tracker.get_codebase.return_value = None
    async with Client(_server(tracker)) as client:
        result = await client.call_tool("remove_cpg", {"codebase_hash": HASH}, raise_on_error=False)

    assert result.is_error
    assert result.content[0].text.startswith(f"VALIDATION_ERROR: Codebase {HASH} not found")


async def test_unknown_cpg_status_is_still_a_result():
    tracker = MagicMock()
    tracker.get_codebase.return_value = None
    async with Client(_server(tracker)) as client:
        result = await client.call_tool("get_cpg_status", {"codebase_hash": HASH})

    assert not result.is_error
    assert result.structured_content["status"] == "not_found"
