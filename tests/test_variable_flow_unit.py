
import sys
import unittest
from unittest.mock import MagicMock, ANY

import json

import pytest
from fastmcp.exceptions import ToolError


def _envelope(summary, **fields):
    """The variable_flow template's success reply once the executor strips markers."""
    return json.dumps([{"summary": summary, "rows": [], "total": 0, "returned": 0, "truncated": False, **fields}])

class TestVariableFlow(unittest.TestCase):
    def test_logic(self):
        # Mock services
        services = {
            "codebase_tracker": MagicMock(),
            "query_executor": MagicMock(),
            "config": MagicMock()
        }
        
        # Mock Config
        services["config"].cpg.taint_sources = {}
        services["config"].cpg.taint_sinks = {}
        
        # Mock CodebaseInfo
        codebase_info = MagicMock()
        codebase_info.cpg_path = "/path/to/cpg"
        codebase_info.language = "c"
        services["codebase_tracker"].get_codebase.return_value = codebase_info
        
        # Mock QueryExecutor result
        query_result = MagicMock()
        query_result.success = True
        query_result.data = [_envelope("""Variable Flow Analysis
======================
Target: variable 'x'
Aliases: p
Dependencies: ...""")]
        services["query_executor"].execute_query.return_value = query_result
        
        # Mock MCP
        mcp = MagicMock()
        registered_functions = {}
        
        def tool_decorator(description=None, **kwargs):
            def decorator(func):
                registered_functions[func.__name__] = func
                return func
            return decorator

        mcp.tool = tool_decorator

        # Import and register
        from src.tools.taint_analysis_tools import register_taint_analysis_tools
        register_taint_analysis_tools(mcp, services)
        
        # Get function
        get_variable_flow = registered_functions["get_variable_flow"]
        
        # Test Call
        result = get_variable_flow(
            codebase_hash="1234567890abcdef",
            location="main.c:10",
            variable="x",
            direction="backward"
        )
        
        self.assertIsInstance(result, dict)
        self.assertIn("Variable Flow Analysis", result["summary"])
        self.assertIn("Aliases: p", result["summary"])

    def test_parameter_detection_output(self):
        # Mock services
        services = {
            "codebase_tracker": MagicMock(),
            "query_executor": MagicMock(),
            "config": MagicMock()
        }
        
        # Mock CodebaseInfo
        codebase_info = MagicMock()
        codebase_info.cpg_path = "/path/to/cpg"
        codebase_info.language = "c"
        services["codebase_tracker"].get_codebase.return_value = codebase_info
        
        # Mock result with parameters
        query_result = MagicMock()
        query_result.success = True
        query_result.data = [_envelope("""Variable Flow Analysis
======================
Target: variable 'item'
Method: foo
Direction: backward

Dependencies:
[Line  400] void * item (parameter)
""")]
        services["query_executor"].execute_query.return_value = query_result
        
        # Mock MCP
        mcp = MagicMock()
        registered_functions = {}
        
        def tool_decorator(description=None, **kwargs):
            def decorator(func):
                registered_functions[func.__name__] = func
                return func
            return decorator

        mcp.tool = tool_decorator

        from src.tools.taint_analysis_tools import register_taint_analysis_tools
        register_taint_analysis_tools(mcp, services)
        get_variable_flow = registered_functions["get_variable_flow"]

        result = get_variable_flow(
             codebase_hash="1234567890abcdef",
             location="test.c:10",
             variable="item",
             direction="backward"
        )
        self.assertIn("(parameter)", result["summary"])
        
        # Verify query executor called with correct args
        services["query_executor"].execute_query.assert_called()
        call_args = services["query_executor"].execute_query.call_args[1]
        self.assertIn("query", call_args)

    def test_cast_assignment_and_field_access(self):
        # Mock services with relevant response
        services = {
            "codebase_tracker": MagicMock(),
            "query_executor": MagicMock(),
            "config": MagicMock()
        }
        services["config"].cpg.taint_sources = {}
        
        # Mock CodebaseInfo
        codebase_info = MagicMock()
        codebase_info.cpg_path = "/path/to/cpg"
        codebase_info.language = "c"
        services["codebase_tracker"].get_codebase.return_value = codebase_info

        # Mock query response pretending to find a cast and field access
        # This confirms that our tool correctly passes through and formats what the query finds
        query_result = MagicMock()
        query_result.success = True
        query_result.data = [_envelope("""Variable Flow Analysis
======================
Target: variable 'list'
Method: test_func
Direction: backward

Dependencies:
[Line   10] list = (Type) ptr; (assignment)
[Line   20] list->field = val; (modification)
""")]
        services["query_executor"].execute_query.return_value = query_result
        
        # Mock MCP
        mcp = MagicMock()
        registered_functions = {}
        def tool_decorator(description=None, **kwargs):
            def decorator(func):
                registered_functions[func.__name__] = func
                return func
            return decorator
        mcp.tool = tool_decorator
        
        from src.tools.taint_analysis_tools import register_taint_analysis_tools
        register_taint_analysis_tools(mcp, services)
        get_variable_flow = registered_functions["get_variable_flow"]
        
        result = get_variable_flow(
            codebase_hash="1234567890abcdef",
            location="file.c:20",
            variable="list",
            direction="backward"
        )
        
        self.assertIn("list = (Type) ptr", result["summary"])
        self.assertIn("list->field = val", result["summary"])
        
        # Check that query loading logic worked (implicitly verified by execute_query being called)


def _tool_returning(text):
    services = {"codebase_tracker": MagicMock(), "query_executor": MagicMock(), "config": MagicMock()}
    info = MagicMock(cpg_path="/path/to/cpg", language="c")
    services["codebase_tracker"].get_codebase.return_value = info
    services["query_executor"].execute_query.return_value = MagicMock(success=True, data=[text])
    registered = {}
    mcp = MagicMock()
    mcp.tool = lambda description=None, **kw: (lambda f: registered.setdefault(f.__name__, f))
    from src.tools.taint_analysis_tools import register_taint_analysis_tools
    register_taint_analysis_tools(mcp, services)
    return registered["get_variable_flow"], services


@pytest.mark.parametrize("text", [
    "VARIABLE_NOT_FOUND: no local or parameter 'nope' in method report\nVariables in report: flag, x\n",
    "ANCHOR_NOT_FOUND: no method contains line 80 in 'audit.c'\n",
])
def test_unresolved_variable_or_line_is_a_validation_error(text):
    tool, _ = _tool_returning(text)
    with pytest.raises(ToolError) as exc:
        tool(codebase_hash="1234567890abcdef", location="audit.c:6", variable="nope")
    assert str(exc.value).startswith("VALIDATION_ERROR: ")
    assert "NOT_FOUND:" not in str(exc.value)


def test_unresolved_results_are_not_cached():
    from src.tools._common import is_error_output
    assert is_error_output("VARIABLE_NOT_FOUND: no local 'x'")


if __name__ == '__main__':
    unittest.main()
