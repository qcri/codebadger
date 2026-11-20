"""run_cpgql_query(validate=True) blocks queries with validation errors, and only those."""

from unittest.mock import MagicMock

import pytest
from fastmcp import Client, FastMCP

from src.models import CodebaseInfo, QueryResult
from src.services.code_browsing_service import CodeBrowsingService
from src.tools.code_browsing_tools import register_code_browsing_tools
from src.utils.cpgql_validator import CPGQLValidator

CODEBASE_HASH = "553642871dd4251d"


@pytest.mark.parametrize(
    ("query", "valid"),
    [
        ("cpg.method.name(.l", False),                              # unbalanced paren
        ('cpg.method.name(".*\\\\(.*").l', True),                   # paren inside a regex literal
        ('cpg.call.name("""a(b""").l', True),                       # paren inside a triple-quoted literal
        ('cpg.method.name.l.map(n => n.matches("x"))', True),      # matches outside filter: advice only
        ("cpg.method.name.l", True),
    ],
)
def test_valid_reflects_recorded_errors(query, valid):
    result = CPGQLValidator.validate_query(query)
    assert result["valid"] is valid
    assert bool(result["errors"]) is not valid


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


async def test_validate_blocks_invalid_query_before_execution():
    executor = MagicMock()
    async with Client(_server(executor)) as client:
        result = await client.call_tool(
            "run_cpgql_query",
            {"codebase_hash": CODEBASE_HASH, "query": "cpg.method.name(.l", "validate": True},
            raise_on_error=False,
        )

    assert result.is_error
    assert result.content[0].text.startswith(
        "VALIDATION_ERROR: Query validation failed: "
    )
    assert "Unmatched parentheses in query (Ensure all opening parentheses" in result.content[0].text
    assert "{'type'" not in result.content[0].text
    executor.execute_query.assert_not_called()


async def test_validate_runs_valid_query_and_returns_advice():
    executor = MagicMock()
    executor.execute_query.return_value = QueryResult(success=True, data=["main"], row_count=1)
    async with Client(_server(executor)) as client:
        result = await client.call_tool(
            "run_cpgql_query",
            {"codebase_hash": CODEBASE_HASH, "query": 'cpg.method.name(".*\\\\(.*").l', "validate": True},
        )

    assert result.structured_content["success"] is True
    assert result.structured_content["validation"]["valid"] is True
    executor.execute_query.assert_called_once()
