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
    assert "Set[Long]()" in query
    assert "visited.contains(current.id)" in query
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
    body = CFG_JSON if tool == "get_cfg" else "ok"
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
async def test_cfg_selection_failures_are_validation_errors(payload, needle):
    executor = MagicMock()
    executor.execute_query.return_value = QueryResult(
        success=True, data=[json.dumps([payload])], row_count=1
    )
    async with Client(_server(executor)) as client:
        result = await client.call_tool("get_cfg", {"codebase_hash": CODEBASE_HASH, "method_name": "init"},
                                        raise_on_error=False)
    assert result.is_error
    assert result.content[0].text.startswith("VALIDATION_ERROR: ")
    assert needle in result.content[0].text
