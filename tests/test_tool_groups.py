"""Every tool carries one group tag, and operators can disable groups."""

from unittest.mock import MagicMock

from fastmcp import Client, FastMCP

from src.models import Config
from src.tools.mcp_tools import (
    GROUP_TAG_PREFIX, TOOL_GROUPS, apply_disabled_tool_tags, register_tools,
)


def _server(disabled: str = ""):
    services = {name: MagicMock() for name in (
        "git_manager", "codebase_tracker", "query_executor",
        "code_browsing_service", "joern_server_manager",
    )}
    services["config"] = Config()
    mcp = FastMCP("TestServer")
    apply_disabled_tool_tags(mcp, disabled)  # before registration, as in main.py
    register_tools(mcp, services)
    return mcp


async def _tools(mcp):
    async with Client(mcp) as client:
        return {t.name: t for t in await client.list_tools()}


def _tags(tool):
    return set(((tool.meta or {}).get("fastmcp") or {}).get("tags") or ())


async def test_every_tool_has_exactly_one_group():
    tools = await _tools(_server())

    groups = {
        name: {t for t in _tags(tool) if t.startswith(GROUP_TAG_PREFIX)}
        for name, tool in tools.items()
    }
    assert {name: g for name, g in groups.items() if len(g) != 1} == {}
    assert set().union(*TOOL_GROUPS.values()) == set(tools)  # no stale or missing names


async def test_declared_tags_are_kept():
    tools = await _tools(_server())
    assert {"group:detector", "taint", "CWE-78", "security"} <= _tags(tools["find_command_injection_sinks"])


async def test_descriptive_tags_do_not_join_a_group():
    """Disabling the taint group must not hide a detector that merely declares a taint tag."""
    tools = await _tools(_server("taint"))
    assert "find_command_injection_sinks" in tools
    assert "find_taint_flows" not in tools


async def test_disabled_groups_are_hidden_and_refused():
    mcp = _server("admin, RAW, bogus")
    tools = await _tools(mcp)

    assert "remove_cpg" not in tools and "run_cpgql_query" not in tools
    assert "generate_cpg" in tools and "list_methods" in tools
    async with Client(mcp) as client:
        result = await client.call_tool(
            "remove_cpg", {"codebase_hash": "553642871dd4251d"}, raise_on_error=False
        )
    assert result.is_error
    assert "Unknown tool" in result.content[0].text


def test_unknown_groups_are_ignored():
    mcp = MagicMock()
    assert apply_disabled_tool_tags(mcp, "bogus, ,") == set()
    mcp.disable.assert_not_called()


def test_disabling_uses_namespaced_group_tags():
    mcp = MagicMock()
    assert apply_disabled_tool_tags(mcp, "admin,raw") == {"admin", "raw"}
    mcp.disable.assert_called_once_with(tags={"group:admin", "group:raw"})
