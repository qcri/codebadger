"""get_cfg / get_call_graph select an exact method by node ID and refuse to guess on ambiguity."""

from unittest.mock import MagicMock

import pytest
from fastmcp import Client, FastMCP

from src.exceptions import ValidationError
from src.models import CodebaseInfo, QueryResult
from src.services.code_browsing_service import CodeBrowsingService
from src.tools.code_browsing_tools import _resolve_method_selector, register_code_browsing_tools
from src.tools.queries import QueryLoader

CODEBASE_HASH = "553642871dd4251d"


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
    executor.execute_query.return_value = QueryResult(
        success=True, data=["<codebadger_result>\nok\n</codebadger_result>"], row_count=1
    )
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
