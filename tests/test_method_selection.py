"""get_cfg / get_call_graph select an exact method by node ID and refuse to guess on ambiguity."""

import json
from unittest.mock import MagicMock

import pytest
from fastmcp import Client, FastMCP

from src.exceptions import ValidationError
from src.models import CodebaseInfo, QueryResult
from src.services.code_browsing_service import CodeBrowsingService
from src.tools.code_browsing_tools import _resolve_method_selector, register_code_browsing_tools
from src.tools.queries import QueryLoader

CODEBASE_HASH = "553642871dd4251d"
CALL_GRAPH_JSON = json.dumps([{
    "status": "ok", "direction": "outgoing", "edges": [], "truncated": False,
    "root": {"node_id": "4242", "name": "init", "full_name": "init", "filename": "a.c", "line": 1},
}])
CFG_JSON = json.dumps([{
    "status": "ok", "total_nodes": 1, "total_edges": 0, "edges": [],
    "method": {"node_id": "4242", "name": "init", "full_name": "init", "filename": "a.c", "line": 1},
    "nodes": [{"node_id": "4242", "kind": "METHOD", "code": "void init()", "line": 1, "out_degree": 0}],
}])


def test_method_id_overrides_name():
    assert _resolve_method_selector("init", "4242") == ("init", 4242)
    assert _resolve_method_selector(None, 7) == ("", 7)
    assert _resolve_method_selector("main", None) == ("main", -1)


@pytest.mark.parametrize("args", [(None, None), ("  ", ""), ("main", "12; cpg.call.l"), (None, "-3")])
def test_invalid_selectors_are_rejected(args):
    with pytest.raises(ValidationError):
        _resolve_method_selector(*args)


@pytest.mark.parametrize("template", ["cfg", "call_graph"])
def test_templates_render_method_id_and_resolve_unique_or_list_choices(template):
    query = QueryLoader.load(
        template, method_name="init", method_id=4242, depth=3, direction="outgoing", max_nodes=10
    )
    assert "val methodId = 4242L" in query
    assert "{{" not in query
    assert "case List(" in query  # unique match proceeds
    assert "describeCandidates" in query  # ambiguous match lists method_id choices


def test_call_graph_tracks_visits_by_node_id():
    query = QueryLoader.load("call_graph", method_name="init", method_id=-1, depth=3, direction="outgoing")
    assert "visited.add(current.id)" in query
    assert "reaches(to, from)" in query  # recursion is reachability, not "seen before"
    assert "nameExact(methodName)" not in query


def _server(executor):
    tracker = MagicMock()
    tracker.get_codebase.return_value = CodebaseInfo(
        codebase_hash=CODEBASE_HASH, source_type="local", source_path="/workspace/example",
        language="c", cpg_path="/workspace/example.cpg",
    )
    mcp = FastMCP("TestServer")
    register_code_browsing_tools(mcp, {
        "code_browsing_service": CodeBrowsingService(tracker, executor),
        "codebase_tracker": tracker,
        "query_executor": executor,
    })
    return mcp


@pytest.mark.parametrize("tool", ["get_cfg", "get_call_graph"])
async def test_tools_send_method_id_to_joern(tool):
    executor = MagicMock()
    # The executor strips the <codebadger_result> envelope before tools see it.
    body = CFG_JSON if tool == "get_cfg" else CALL_GRAPH_JSON
    executor.execute_query.return_value = QueryResult(success=True, data=[body], row_count=1)
    async with Client(_server(executor)) as client:
        result = await client.call_tool(tool, {"codebase_hash": CODEBASE_HASH, "method_id": "4242"})

    assert result.structured_content["success"] is True
    assert "val methodId = 4242L" in executor.execute_query.call_args.kwargs["query"]


@pytest.mark.parametrize("tool", ["get_cfg", "get_call_graph"])
async def test_tools_require_a_method_selector(tool):
    executor = MagicMock()
    async with Client(_server(executor)) as client:
        result = await client.call_tool(tool, {"codebase_hash": CODEBASE_HASH}, raise_on_error=False)

    assert result.is_error
    assert result.content[0].text.startswith("VALIDATION_ERROR: ")
    executor.execute_query.assert_not_called()


@pytest.mark.parametrize("payload, needle", [
    ({"status": "not_found", "suggestions": ["init_ctx"]}, "Similar names: init_ctx"),
    ({"status": "ambiguous", "count": 2, "candidates": [
        {"node_id": "1", "full_name": "a.init", "filename": "a.c", "line": 3},
        {"node_id": "2", "full_name": "b.init", "filename": "b.c", "line": 9}]}, "method_id=2 b.init (b.c:9)"),
])
@pytest.mark.parametrize("tool", ["get_cfg", "get_call_graph"])
async def test_selection_failures_are_validation_errors(tool, payload, needle):
    executor = MagicMock()
    executor.execute_query.return_value = QueryResult(
        success=True, data=[json.dumps([payload])], row_count=1
    )
    async with Client(_server(executor)) as client:
        result = await client.call_tool(tool, {"codebase_hash": CODEBASE_HASH, "method_name": "init"},
                                        raise_on_error=False)
    assert result.is_error
    assert result.content[0].text.startswith("VALIDATION_ERROR: ")
    assert needle in result.content[0].text


async def test_bounds_check_on_an_unsupported_line_is_a_validation_error():
    # Audit bounds8: a line with no buf[i] or sized call is not "unguarded".
    executor = MagicMock()
    executor.execute_query.return_value = QueryResult(
        success=True, data=[json.dumps([{"status": "unsupported", "calls_on_line": ["leaf(n)"]}])], row_count=1)
    async with Client(_server(executor)) as client:
        result = await client.call_tool("find_bounds_checks", {"codebase_hash": CODEBASE_HASH,
                                                               "buffer_access_location": "audit.c:8"},
                                        raise_on_error=False)
    assert result.is_error
    assert result.content[0].text.startswith("VALIDATION_ERROR: no buffer access (buf[i]) or sized call at audit.c:8.")
    assert "leaf(n)" in result.content[0].text


@pytest.mark.parametrize("location, line, column", [("audit.c:23", 23, -1), ("src/a.c:23:35", 23, 35)])
async def test_resolve_location_parses_line_and_optional_column(location, line, column):
    executor = MagicMock()
    executor.execute_query.return_value = QueryResult(success=True, data=[json.dumps([{
        "files": ["audit.c"], "total": 0, "candidates": []}])], row_count=1)
    async with Client(_server(executor)) as client:
        result = await client.call_tool("resolve_location", {"codebase_hash": CODEBASE_HASH, "location": location})
    query = executor.execute_query.call_args.kwargs["query"]
    assert f"val lineNum = {line}" in query and f"val column = {column}" in query
    assert result.structured_content["total"] == 0 and "hint" in result.structured_content


@pytest.mark.parametrize("payload, location, needle", [
    ({"files": [], "total": 0, "candidates": []}, "nope.c:3", "no file in this CPG matches 'nope.c'"),
    (None, "audit.c", "location must be 'file:line' or 'file:line:column'"),
])
async def test_resolve_location_rejects_bad_input(payload, location, needle):
    executor = MagicMock()
    executor.execute_query.return_value = QueryResult(success=True, data=[json.dumps([payload or {}])], row_count=1)
    async with Client(_server(executor)) as client:
        result = await client.call_tool("resolve_location", {"codebase_hash": CODEBASE_HASH, "location": location},
                                        raise_on_error=False)
    assert result.is_error and result.content[0].text.startswith("VALIDATION_ERROR: ")
    assert needle in result.content[0].text


@pytest.mark.parametrize("payload, needle", [
    ({"status": "not_found", "method": "scratch", "variables": ["copy", "x", "x_count"]},
     "no local, parameter or file global 'nope' in scratch; variables there: copy, x, x_count"),
    ({"status": "no_method"}, "no function contains control.c:3"),
])
async def test_find_references_unresolved_variable_is_a_validation_error(payload, needle):
    executor = MagicMock()
    executor.execute_query.return_value = QueryResult(success=True, data=[json.dumps([payload])], row_count=1)
    async with Client(_server(executor)) as client:
        result = await client.call_tool("find_references", {"codebase_hash": CODEBASE_HASH,
                                                            "location": "control.c:3", "variable": "nope"},
                                        raise_on_error=False)
    assert result.is_error and result.content[0].text.startswith("VALIDATION_ERROR: ")
    assert needle in result.content[0].text


async def test_find_references_reports_totals():
    executor = MagicMock()
    ref = {"node_id": "1", "access": "read", "code": "emit(x)", "filename": "control.c", "line": 8,
           "column": 10, "method": "scratch", "method_id": "9"}
    executor.execute_query.return_value = QueryResult(success=True, data=[json.dumps([{
        "status": "ok", "declaration": {"name": "x", "scope": "local"}, "total": 3, "counts": {"read": 3},
        "references": [ref]}])], row_count=1)
    async with Client(_server(executor)) as client:
        r = (await client.call_tool("find_references", {"codebase_hash": CODEBASE_HASH, "location": "control.c:8",
                                                        "variable": "x", "limit": 1})).structured_content
    assert (r["total"], r["returned"], r["truncated"]) == (3, 1, True)
