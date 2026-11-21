import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock
import uuid

import pytest
from fastmcp.exceptions import ToolError

from src.models import Config, CPGConfig, QueryResult, CodebaseInfo
from src.config import load_config
from src.tools.taint_analysis_tools import DEFAULT_SOURCES
from src.tools.mcp_tools import register_tools
from src.tools.queries import QueryLoader


from fastmcp import FastMCP, Client


@pytest.fixture
def fake_services():
    # codebase tracker mock
    from src.services.codebase_tracker import CodebaseTracker
    codebase_tracker = MagicMock()
    codebase_hash = str(uuid.uuid4()).replace('-', '')[:16]
    codebase_info = CodebaseInfo(
        codebase_hash=codebase_hash,
        source_type="local",
        source_path="/tmp",
        language="c",
        cpg_path="/tmp/test.cpg",
        created_at=datetime.now(timezone.utc),
        last_accessed=datetime.now(timezone.utc),
    )
    codebase_tracker.get_codebase.return_value = codebase_info

    # query executor mock
    query_executor = MagicMock()

    # Store the last query for test assertions
    query_executor.last_query = None

    def execute_query_with_tracking(*args, **kwargs):
        # Store the query parameter
        if 'query' in kwargs:
            query_executor.last_query = kwargs['query'] 
        elif len(args) > 2:
            query_executor.last_query = args[2]  # query is typically 3rd arg

        row = {
            "node_id": 123,
            "name": "getenv",
            "code": 'char *s = getenv("FOO")',
            "filename": "core.c",
            "lineNumber": 10,
            "method": "main",
        }
        # Location queries return {"total", "rows"} in a JSON envelope (the
        # executor has already stripped the result markers).
        if '"total" -> matches.size' in str(query_executor.last_query):
            import json
            return QueryResult(success=True, data=[json.dumps([{"total": 1, "rows": [row]}])], row_count=1)

        # Return the mock result
        return QueryResult(
            success=True,
            data=[
                {
                    "node_id": 123,
                    "name": "getenv",
                    "code": 'char *s = getenv("FOO")',
                    "filename": "core.c",
                    "lineNumber": 10,
                    "method": "main",
                }
            ],
            row_count=1,
        )

    query_executor.execute_query = execute_query_with_tracking

    # config with taint lists
    cpg = CPGConfig() 
    cpg.taint_sources = {"c": ["getenv", "fgets"]}
    cpg.taint_sinks = {"c": ["system", "popen"]}
    cfg = Config(cpg=cpg)

    services = {
        "codebase_tracker": codebase_tracker,
        "query_executor": query_executor,
        "config": cfg,
        "codebase_hash": codebase_hash,
    }

    return services


@pytest.mark.asyncio
async def test_find_taint_sources_success(fake_services):
    mcp = FastMCP("TestServer")
    register_tools(mcp, fake_services)

    async with Client(mcp) as client:
        res_json = await client.call_tool("find_taint_sources", {"codebase_hash": fake_services["codebase_hash"], "language": "c", "limit": 10})
        import json
        res = json.loads(res_json.content[0].text)

        assert res.get("success") is True
        assert "sources" in res
        assert isinstance(res["sources"], list)
        assert res["total"] == 1
        assert res["sources"][0]["name"] == "getenv"

    query = fake_services["query_executor"].last_query
    assert 'Map("node_id" -> c.id' in query
    assert "(c.id, c.name, c.code" not in query


def test_default_c_sources_exclude_setup_and_handle_calls():
    setup_calls = {"socket", "bind", "listen", "connect", "accept", "fopen"}

    assert setup_calls.isdisjoint(DEFAULT_SOURCES["c"])

    config = load_config("config.example.yaml")
    assert setup_calls.isdisjoint(config.cpg.taint_sources["c"])
    assert setup_calls.isdisjoint(config.cpg.taint_sources["cpp"])


@pytest.mark.asyncio
async def test_find_taint_sources_filters_setup_calls_from_existing_config(
    fake_services,
):
    fake_services["config"].cpg.taint_sources = {
        "c": ["getenv", "socket", "bind", "listen"]
    }
    mcp = FastMCP("TestServer")
    register_tools(mcp, fake_services)

    async with Client(mcp) as client:
        await client.call_tool(
            "find_taint_sources",
            {"codebase_hash": fake_services["codebase_hash"], "language": "c"},
        )

    query = fake_services["query_executor"].last_query
    assert "getenv" in query
    assert "socket" not in query
    assert "bind" not in query
    assert "listen" not in query


@pytest.mark.asyncio
async def test_find_taint_sources_allows_explicit_setup_call_opt_in(fake_services):
    mcp = FastMCP("TestServer")
    register_tools(mcp, fake_services)

    async with Client(mcp) as client:
        await client.call_tool(
            "find_taint_sources",
            {
                "codebase_hash": fake_services["codebase_hash"],
                "language": "c",
                "source_patterns": ["socket"],
            },
        )

    assert "socket" in fake_services["query_executor"].last_query


@pytest.mark.asyncio
async def test_find_taint_sources_with_filename_filter(fake_services):
    """Test find_taint_sources with filename parameter"""
    mcp = FastMCP("TestServer")
    register_tools(mcp, fake_services)

    async with Client(mcp) as client:
        # Call with filename filter
        res_json = await client.call_tool(
            "find_taint_sources",
            {
                "codebase_hash": fake_services["codebase_hash"],
                "language": "c",
                "filename": "shell.c",
                "limit": 10,
            }
        )
        import json
        res = json.loads(res_json.content[0].text)

        assert res.get("success") is True
        assert "sources" in res
        assert isinstance(res["sources"], list)
        
        # Verify the query executor was called with a query containing the file filter
        query_executor = fake_services["query_executor"]
        assert query_executor.last_query is not None
        assert "where(_.file.name" in query_executor.last_query
        assert "shell" in query_executor.last_query


def test_query_loader_escapes_scala_string_values():
    query = QueryLoader.load(
        "call_graph",
        method_name='main"; cpg.call.l // {{depth}}',
        depth=2,
        direction="outgoing",
    )

    assert 'val methodName = "main\\"; cpg.call.l // {{depth}}"' in query


def test_auto_taint_query_reports_bounded_flow_counts():
    query = QueryLoader.load(
        "taint_flows_auto",
        source_pattern="getenv",
        sink_pattern="system",
        sanitizer_pattern="",
        file_filter="",
        max_results=20,
    )

    assert ".take(maxResults + 1)" in query
    assert "Matched flow candidates:" in query
    assert "Confirmed flows after sanitizers:" in query
    assert "Counts: matched=" in query
    assert "truncated=" in query


@pytest.mark.asyncio
async def test_find_taint_sources_escapes_filename_for_query(fake_services):
    mcp = FastMCP("TestServer")
    register_tools(mcp, fake_services)

    async with Client(mcp) as client:
        await client.call_tool(
            "find_taint_sources",
            {
                "codebase_hash": fake_services["codebase_hash"],
                "language": "c",
                "filename": 'shell".*',
                "limit": 10,
            }
        )

        query_executor = fake_services["query_executor"]
        assert query_executor.last_query is not None
        assert 'shell\\"' in query_executor.last_query
        assert 'where(_.file.name("(^|.*/)shell\\"\\\\.\\\\*.*"))' in query_executor.last_query


@pytest.mark.asyncio
async def test_find_taint_sinks_success(fake_services):
    mcp = FastMCP("TestServer")
    register_tools(mcp, fake_services)

    async with Client(mcp) as client:
        res_json = await client.call_tool("find_taint_sinks", {"codebase_hash": fake_services["codebase_hash"], "language": "c", "limit": 10})
        import json
        res = json.loads(res_json.content[0].text)

        assert res.get("success") is True
        assert "sinks" in res
        assert isinstance(res["sinks"], list)
        assert res["total"] == 1
        assert res["sinks"][0]["name"] == "getenv"

    query = fake_services["query_executor"].last_query
    assert 'Map("node_id" -> c.id' in query
    assert "(c.id, c.name, c.code" not in query


@pytest.mark.asyncio
async def test_find_taint_sinks_defaults_to_focused_c_categories(fake_services):
    fake_services["config"].cpg.taint_sinks = {
        "c": ["system", "sprintf", "malloc", "free", "stat", "bind", "printf"]
    }
    mcp = FastMCP("TestServer")
    register_tools(mcp, fake_services)

    async with Client(mcp) as client:
        result = await client.call_tool(
            "find_taint_sinks",
            {"codebase_hash": fake_services["codebase_hash"], "language": "c"},
        )
        import json

        response = json.loads(result.content[0].text)

    query = fake_services["query_executor"].last_query
    assert 'name("system|sprintf")' in query
    assert response["mode"] == "focused"


@pytest.mark.asyncio
async def test_find_taint_sinks_broad_mode_restores_filtered_categories(fake_services):
    patterns = ["system", "malloc", "free", "stat", "bind", "printf"]
    fake_services["config"].cpg.taint_sinks = {"c": patterns}
    mcp = FastMCP("TestServer")
    register_tools(mcp, fake_services)

    async with Client(mcp) as client:
        result = await client.call_tool(
            "find_taint_sinks",
            {
                "codebase_hash": fake_services["codebase_hash"],
                "language": "c",
                "broad": True,
            },
        )
        import json

        response = json.loads(result.content[0].text)

    query = fake_services["query_executor"].last_query
    for pattern in patterns:
        assert pattern in query
    assert response["mode"] == "broad"


@pytest.mark.asyncio
async def test_find_taint_sinks_explicit_patterns_bypass_focused_filter(fake_services):
    mcp = FastMCP("TestServer")
    register_tools(mcp, fake_services)

    async with Client(mcp) as client:
        await client.call_tool(
            "find_taint_sinks",
            {
                "codebase_hash": fake_services["codebase_hash"],
                "language": "c",
                "sink_patterns": ["free"],
            },
        )

    assert "free" in fake_services["query_executor"].last_query


@pytest.mark.asyncio
async def test_find_taint_sinks_with_filename_filter(fake_services):
    """Test find_taint_sinks with filename parameter"""
    mcp = FastMCP("TestServer")
    register_tools(mcp, fake_services)

    async with Client(mcp) as client:
        # Call with filename filter
        res_json = await client.call_tool(
            "find_taint_sinks",
            {
                "codebase_hash": fake_services["codebase_hash"],
                "language": "c",
                "filename": "main.c",
                "limit": 10,
            }
        )
        import json
        res = json.loads(res_json.content[0].text)

        assert res.get("success") is True
        assert "sinks" in res
        assert isinstance(res["sinks"], list)
        
        # Verify the query executor was called with a query containing the file filter
        query_executor = fake_services["query_executor"]
        assert query_executor.last_query is not None
        assert "where(_.file.name" in query_executor.last_query
        assert "main" in query_executor.last_query


def _flow_envelope(source_id=30064771934, sink_id=30064780656, flows=1):
    """What the manual taint_flows template returns once the executor strips markers."""
    import json
    node = lambda nid, name, code, line: {"node_id": str(nid), "kind": "CALL", "name": name, "code": code,
                                          "filename": "core.c", "lineNumber": line, "columnNumber": 5, "method": "main"}
    summary = ("Taint Flow Analysis\n" + "=" * 60 + "\n"
               f"Source (1 node searched):\n  [{source_id}] getenv(\"FOO\") at core.c:10:5\n"
               f"Sink (1 node searched):\n  [{sink_id}] system(cmd) at core.c:42:5\n\n"
               "--- Flow 1 ---\nSource: getenv(\"FOO\")\n  Location: core.c:10 in main()\n\n"
               "Sink: system(cmd)\n  Location: core.c:42 in main()\n\nSummary: 1 unique flow(s)\n")
    return json.dumps([{"source_nodes": [node(source_id, "getenv", 'getenv("FOO")', 10)],
                        "sink_nodes": [node(sink_id, "system", "system(cmd)", 42)],
                        "flows": flows, "truncated": False, "summary": summary}])


@pytest.mark.asyncio
async def test_find_taint_flows_success(fake_services):
    # Setup mock for flow query with text output
    services = fake_services

    # The refactored API returns human-readable text
    flow_result = QueryResult(success=True, data=[_flow_envelope()], row_count=1)

    services["query_executor"].execute_query = MagicMock(return_value=flow_result)
    services["codebase_tracker"].get_codebase.return_value = CodebaseInfo(
        codebase_hash=services["codebase_hash"],
        source_type="local",
        source_path="/path",
        language="c",
        cpg_path="/tmp/test.cpg",
        created_at=datetime.now(timezone.utc),
        last_accessed=datetime.now(timezone.utc),
    )

    mcp = FastMCP("TestServer")
    register_tools(mcp, services)

    async with Client(mcp) as client:
        res_text = await client.call_tool(
            "find_taint_flows",
            {
                "codebase_hash": services["codebase_hash"],
                "source_location": "core.c:10",
                "sink_location": "core.c:42",
                "timeout": 10,
            }
        )
        result = res_text.content[0].text

        # Check text output contains expected information
        assert "Taint Flow Analysis" in result
        assert "getenv" in result
        assert "system" in result
        assert "core.c" in result


@pytest.mark.asyncio
async def test_find_taint_flows_with_node_ids(fake_services):
    """Test that node_id based queries work"""
    services = fake_services

    # The refactored API returns human-readable text
    flow_result = QueryResult(success=True, data=[_flow_envelope()], row_count=1)

    services["query_executor"].execute_query = MagicMock(return_value=flow_result)
    services["codebase_tracker"].get_codebase.return_value = CodebaseInfo(
        codebase_hash=services["codebase_hash"],
        source_type="local",
        source_path="/path",
        language="c",
        cpg_path="/tmp/test.cpg",
        created_at=datetime.now(timezone.utc),
        last_accessed=datetime.now(timezone.utc),
    )

    mcp = FastMCP("TestServer")
    register_tools(mcp, services)

    async with Client(mcp) as client:
        res_text = await client.call_tool(
            "find_taint_flows",
            {
                "codebase_hash": services["codebase_hash"],
                "source_node_id": 30064771934,
                "sink_node_id": 30064780656,
                "timeout": 10,
            }
        )
        result = res_text.content[0].text

        # Check text output contains expected information
        assert "Taint Flow Analysis" in result
        assert "getenv" in result
        assert "system" in result


@pytest.mark.asyncio
async def test_find_taint_flows_validation_error(fake_services):
    """Test that missing source returns validation error"""
    services = fake_services

    services["codebase_tracker"].get_codebase.return_value = CodebaseInfo(
        codebase_hash=services["codebase_hash"],
        source_type="local",
        source_path="/path",
        language="c",
        cpg_path="/tmp/test.cpg",
        created_at=datetime.now(timezone.utc),
        last_accessed=datetime.now(timezone.utc),
    )

    mcp = FastMCP("TestServer")
    register_tools(mcp, services)

    async with Client(mcp) as client:
        # Test with only sink (missing source)
        with pytest.raises(ToolError, match="source"):
            await client.call_tool(
                "find_taint_flows",
                {
                    "codebase_hash": services["codebase_hash"],
                    "sink_location": "core.c:42",
                    "timeout": 10,
                },
            )


def _location_services(fake_services, total, rows):
    import json
    executor = MagicMock()
    executor.execute_query.return_value = QueryResult(
        success=True, data=[json.dumps([{"total": total, "rows": rows}])], row_count=1)
    return {**fake_services, "query_executor": executor}


def _site(node_id, line, col):
    return {"node_id": node_id, "name": "show_value", "code": "show_value(9)", "filename": "audit.c",
            "lineNumber": line, "columnNumber": col, "method": "twice"}


@pytest.mark.asyncio
@pytest.mark.parametrize("tool, key", [("find_taint_sources", "sources"), ("find_taint_sinks", "sinks")])
async def test_location_tools_flag_truncation_only_when_another_match_exists(fake_services, tool, key):
    # Audit sourcescap: one match at limit=1 is complete.
    services = _location_services(fake_services, 1, [_site(1, 12, 5)])
    mcp = FastMCP("TestServer")
    register_tools(mcp, services)
    async with Client(mcp) as client:
        res = (await client.call_tool(tool, {"codebase_hash": fake_services["codebase_hash"], "language": "c",
                                             "limit": 1, f"{key[:-1]}_patterns": ["show_value"]})).structured_content
    assert (res["total"], res["returned"], res["truncated"]) == (1, 1, False)


@pytest.mark.asyncio
async def test_sinks_report_the_full_match_count_beyond_the_limit(fake_services):
    # Audit sinkscap: five show_value calls, limit=1 -> total=5, truncated.
    services = _location_services(fake_services, 5, [_site(30064771087, 23, 20)])
    mcp = FastMCP("TestServer")
    register_tools(mcp, services)
    async with Client(mcp) as client:
        res = (await client.call_tool("find_taint_sinks", {"codebase_hash": fake_services["codebase_hash"],
                                                           "language": "c", "limit": 1,
                                                           "sink_patterns": ["show_value"]})).structured_content
    assert (res["total"], res["returned"], res["truncated"]) == (5, 1, True)
    assert res["sinks"][0]["columnNumber"] == 20
    query = services["query_executor"].execute_query.call_args.kwargs["query"]
    assert ".dedup.l" in query and "matches.take(1)" in query and '"total" -> matches.size' in query


@pytest.mark.asyncio
async def test_find_taint_flows_reports_resolved_endpoints(fake_services):
    fake_services["query_executor"].execute_query = MagicMock(
        return_value=QueryResult(success=True, data=[_flow_envelope(flows=0)], row_count=1))
    mcp = FastMCP("TestServer")
    register_tools(mcp, fake_services)
    async with Client(mcp) as client:
        res = (await client.call_tool("find_taint_flows", {
            "codebase_hash": fake_services["codebase_hash"],
            "source_node_id": 30064771934, "sink_node_id": 30064780656})).structured_content
    assert res["flows"] == 0 and res["truncated"] is False
    assert [n["node_id"] for n in res["source_nodes"]] == ["30064771934"]
    assert res["sink_nodes"][0]["code"] == "system(cmd)"


@pytest.mark.asyncio
async def test_find_taint_flows_missing_endpoint_is_a_validation_error(fake_services):
    # Audit manual80: nothing on audit.c:80 must not read as "no flow".
    fake_services["query_executor"].execute_query = MagicMock(return_value=QueryResult(
        success=True, data=["ENDPOINT_NOT_FOUND: no sink node at audit.c:80 (no call, identifier or literal on that line)"],
        row_count=1))
    mcp = FastMCP("TestServer")
    register_tools(mcp, fake_services)
    async with Client(mcp) as client:
        res = await client.call_tool("find_taint_flows", {
            "codebase_hash": fake_services["codebase_hash"],
            "source_location": "audit.c:12", "sink_location": "audit.c:80"}, raise_on_error=False)
    assert res.is_error
    assert res.content[0].text.startswith("VALIDATION_ERROR: no sink node at audit.c:80")


@pytest.mark.asyncio
async def test_auto_mode_returns_counts_and_flow_paths(fake_services):
    import json
    node = lambda nid, code, line: {"node_id": str(nid), "code": code, "filename": "audit.c", "line": line,
                                    "method": "report"}
    envelope = json.dumps([{
        "sources_matched": 1, "sinks_matched": 5,
        "counts": {"matched": 2, "confirmed": 2, "sanitized": 0, "emitted": 1, "truncated": False},
        "flows": [{"source": node(1, "read_value()", 12), "sink": node(2, "x", 14),
                   "path": [node(3, "x", 12)], "length": 3}],
        "summary": "Auto Taint Flow Analysis\n--- Flow 1 ---\n"}])
    fake_services["query_executor"].execute_query = MagicMock(
        return_value=QueryResult(success=True, data=[envelope], row_count=1))
    mcp = FastMCP("TestServer")
    register_tools(mcp, fake_services)
    async with Client(mcp) as client:
        res = (await client.call_tool("find_taint_flows", {
            "codebase_hash": fake_services["codebase_hash"], "mode": "auto",
            "source_patterns": ["read_value"], "sink_patterns": ["show_value"]})).structured_content
    assert (res["sources_matched"], res["sinks_matched"], res["flows"], res["truncated"]) == (1, 5, 1, False)
    (flow,) = res["flow_paths"]
    assert (flow["source"]["line"], flow["sink"]["line"], flow["length"]) == (12, 14, 3)
    assert res["summary"].startswith("Auto Taint Flow Analysis")
