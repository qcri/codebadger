"""Fixed-choice arguments are published as enums and rejected before any query runs."""

from unittest.mock import MagicMock

import pytest
from fastmcp import Client, FastMCP

from src.models import Config
from src.tools.argument_errors import ArgumentValidationMiddleware
from src.tools.mcp_tools import register_tools

ENUMS = {
    ("generate_cpg", "source_type"): ["local", "github", "snippet"],
    ("get_call_graph", "direction"): ["outgoing", "incoming"],
    ("get_program_slice", "direction"): ["backward", "forward"],
    ("get_variable_flow", "direction"): ["backward", "forward"],
    ("find_taint_flows", "mode"): ["auto"],
}


def _server():
    services = {name: MagicMock() for name in (
        "git_manager", "cpg_generator", "codebase_tracker", "query_executor",
        "code_browsing_service", "joern_server_manager",
    )}
    services["config"] = Config()
    mcp = FastMCP("TestServer")
    mcp.add_middleware(ArgumentValidationMiddleware())
    register_tools(mcp, services)
    return mcp, services


def _enum(schema):
    """Allowed values from an enum, or from a const (pydantic's form for a one-value Literal)."""
    for option in [schema, *schema.get("anyOf", [])]:
        if "enum" in option:
            return option["enum"]
        if "const" in option:
            return [option["const"]]
    return None


async def test_fixed_choices_are_published_as_enums():
    mcp, _ = _server()
    async with Client(mcp) as client:
        tools = {t.name: t for t in await client.list_tools()}

    published = {
        key: _enum(tools[key[0]].input_schema["properties"][key[1]]) for key in ENUMS
    }
    assert published == ENUMS


@pytest.mark.parametrize(("tool", "field"), list(ENUMS))
async def test_values_outside_the_enum_are_rejected_before_querying(tool, field):
    mcp, services = _server()
    arguments = {
        "generate_cpg": {"source_path": "/src", "language": "c"},
        "get_call_graph": {"method_name": "main"},
        "get_program_slice": {"location": "a.c:1"},
        "get_variable_flow": {"location": "a.c:1", "variable": "x"},
        "find_taint_flows": {},
    }[tool]
    if tool != "generate_cpg":
        arguments["codebase_hash"] = "553642871dd4251d"
    arguments[field] = "bogus"

    async with Client(mcp) as client:
        result = await client.call_tool(tool, arguments, raise_on_error=False)

    assert result.is_error
    assert result.content[0].text.startswith(f"VALIDATION_ERROR: {field}: Input should be")
    services["query_executor"].execute_query.assert_not_called()


async def test_every_tool_argument_has_a_description():
    """Clients rely on per-field descriptions; a bare parameter publishes only its type."""
    mcp, _ = _server()
    async with Client(mcp) as client:
        tools = await client.list_tools()

    undocumented = sorted(
        f"{tool.name}.{name}"
        for tool in tools
        for name, schema in tool.input_schema.get("properties", {}).items()
        if not schema.get("description")
    )
    assert undocumented == []


async def test_descriptions_do_not_repeat_argument_docs():
    """Per-argument docs live in field descriptions; an Args: block only repeats them."""
    mcp, _ = _server()
    async with Client(mcp) as client:
        tools = await client.list_tools()

    assert [t.name for t in tools if "\nArgs:\n" in (t.description or "")] == []


async def test_every_result_cap_is_named_limit_and_bounded():
    """One name for result caps across tools, with declared bounds (no silent clamping)."""
    mcp, _ = _server()
    async with Client(mcp) as client:
        tools = await client.list_tools()

    props = {(t.name, name): schema for t in tools for name, schema in t.input_schema["properties"].items()}
    assert [key for key in props if key[1] in ("max_results", "max_nodes")] == []
    unbounded = [key for key, schema in props.items()
                 if key[1] in ("limit", "page_size") and not ("minimum" in schema and "maximum" in schema)]
    assert unbounded == []
    assert [key for key, schema in props.items() if key[1] == "page" and schema.get("minimum") != 1] == []


async def test_old_cap_names_are_rejected():
    mcp, services = _server()
    async with Client(mcp) as client:
        result = await client.call_tool(
            "get_cfg", {"codebase_hash": "553642871dd4251d", "method_name": "main", "max_nodes": 5},
            raise_on_error=False,
        )
    assert result.content[0].text.startswith("VALIDATION_ERROR: max_nodes: Unexpected keyword argument")
    services["query_executor"].execute_query.assert_not_called()


async def test_paged_replies_use_only_the_standard_fields():
    """total/returned/truncated (+ page/page_size/total_pages when paged), nothing tool-specific."""
    mcp, _ = _server()
    async with Client(mcp) as client:
        tools = await client.list_tools()

    legacy = {"available", "result_cap", "has_more", "cpg_count", "cpg_page", "cpg_page_size",
              "cpg_returned", "cpg_total_pages", "cpg_truncated"}
    offenders = {t.name: sorted(legacy & set((t.output_schema or {}).get("properties", {}))) for t in tools}
    assert {k: v for k, v in offenders.items() if v} == {}
    paged = [t for t in tools if "page" in t.input_schema["properties"]]
    for tool in paged:
        assert {"total", "returned", "truncated", "page", "page_size", "total_pages"} <= set(
            tool.output_schema["properties"]), tool.name
