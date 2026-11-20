import pytest
import pytest
from unittest.mock import MagicMock
from src.tools.taint_analysis_tools import register_taint_analysis_tools

@pytest.fixture
def taint_tools(mock_services):
    """Register tools and return a dict of {name: function}"""
    # Create a mock MCP object that captures decorated functions
    mock_mcp = MagicMock()
    tools = {}
    
    def tool_decorator(description=None, **kwargs):
        def decorator(func):
            tools[func.__name__] = func
            return func
        return decorator
    
    mock_mcp.tool.side_effect = tool_decorator
    
    # Register tools
    register_taint_analysis_tools(mock_mcp, mock_services)
    return tools

@pytest.fixture
def mock_services():
    mock_tracker = MagicMock()
    mock_executor = MagicMock()
    mock_config = MagicMock()
    
    # Mock codebase info
    mock_tracker.get_codebase.return_value.cpg_path = "/tmp/test.cpg"
    mock_tracker.get_codebase.return_value.language = "c"
    
    services = {}
    services["codebase_tracker"] = mock_tracker
    services["query_executor"] = mock_executor
    services["config"] = mock_config
    return services

def test_find_taint_flows_missing_sink_message(taint_tools):
    """Test that missing sink triggers the new helpful error message"""
    find_taint_flows = taint_tools["find_taint_flows"]
    
    try:
        result = find_taint_flows(
            codebase_hash="1234567812345678",
            source_location="main.c:10"
        )
        # If it returns a string error message (which it often does in other tools via exception catching):
        assert "CRITICAL ERROR: MISSING SINK" in result
        assert "CORRECT USAGE WORKFLOW" in result
        assert "YOU MUST PROVIDE THIS" in result
    except Exception as e:
        # If it raises exception directly (if my try/except didn't catch it?)
        assert "CRITICAL ERROR: MISSING SINK" in str(e)
        assert "CORRECT USAGE WORKFLOW" in str(e)
        assert "YOU MUST PROVIDE THIS" in str(e)

def test_find_taint_flows_missing_source_message(taint_tools):
    """Test that missing source triggers the new helpful error message"""
    find_taint_flows = taint_tools["find_taint_flows"]
    
    try:
        result = find_taint_flows(
            codebase_hash="1234567812345678",
            sink_location="main.c:20"
        )
        assert "CRITICAL ERROR: MISSING SOURCE" in result
        assert "CORRECT USAGE WORKFLOW" in result
        assert "YOU MUST PROVIDE THIS" in result
    except Exception as e:
        assert "CRITICAL ERROR: MISSING SOURCE" in str(e)
        assert "CORRECT USAGE WORKFLOW" in str(e)
        assert "YOU MUST PROVIDE THIS" in str(e)


LEGACY_ARGS = {"source_pattern": "getenv", "sink_pattern": "system", "depth": 5}


def _mcp_server(services):
    from fastmcp import FastMCP
    from src.tools.argument_errors import ArgumentValidationMiddleware

    mcp = FastMCP("TestServer")
    mcp.add_middleware(ArgumentValidationMiddleware())
    register_taint_analysis_tools(mcp, services)
    return mcp


async def test_find_taint_flows_schema_omits_removed_legacy_args(mock_services):
    from fastmcp import Client

    async with Client(_mcp_server(mock_services)) as client:
        tool = next(t for t in await client.list_tools() if t.name == "find_taint_flows")

    assert set(LEGACY_ARGS).isdisjoint(tool.input_schema["properties"])
    assert "DEPRECATED" not in str(tool.input_schema)


@pytest.mark.parametrize("arg", sorted(LEGACY_ARGS))
async def test_find_taint_flows_rejects_legacy_args_by_name(mock_services, arg):
    from fastmcp import Client

    arguments = {
        "codebase_hash": "1234567812345678",
        "source_location": "main.c:10",
        "sink_location": "main.c:20",
        arg: LEGACY_ARGS[arg],
    }
    async with Client(_mcp_server(mock_services)) as client:
        result = await client.call_tool("find_taint_flows", arguments, raise_on_error=False)

    assert result.is_error
    assert result.content[0].text.startswith(f"VALIDATION_ERROR: {arg}: Unexpected keyword argument")
    mock_services["query_executor"].execute_query.assert_not_called()
