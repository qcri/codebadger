from unittest.mock import MagicMock

import jsonschema
import pytest
from fastmcp import Client, FastMCP

from src.models import CodebaseInfo, QueryResult
from src.services.code_browsing_service import CodeBrowsingService
from src.tools.code_browsing_tools import register_code_browsing_tools

CODEBASE_HASH = "553642871dd4251d"


def _server(query_result: QueryResult) -> FastMCP:
    tracker = MagicMock()
    tracker.get_codebase.return_value = CodebaseInfo(
        codebase_hash=CODEBASE_HASH,
        source_type="local",
        source_path="/workspace/example",
        language="c",
        cpg_path="/workspace/example.cpg",
    )
    executor = MagicMock()
    executor.execute_query.return_value = query_result
    mcp = FastMCP("TestServer")
    register_code_browsing_tools(
        mcp,
        {"code_browsing_service": CodeBrowsingService(tracker, executor)},
    )
    return mcp


@pytest.mark.parametrize(
    ("codebase_hash", "query_result", "error_code"),
    [
        (
            CODEBASE_HASH,
            QueryResult(success=False, error="busy", error_code="SERVER_BUSY"),
            "SERVER_BUSY",
        ),
        (
            CODEBASE_HASH,
            QueryResult(success=False, error="slow", error_code="TIMEOUT"),
            "TIMEOUT",
        ),
        (
            CODEBASE_HASH,
            QueryResult(success=False, error="bad query"),
            "QUERY_ERROR",
        ),
        ("not-a-hash", QueryResult(success=True, data=[]), "VALIDATION_ERROR"),
    ],
)
async def test_list_methods_failures_match_published_schema(
    codebase_hash: str, query_result: QueryResult, error_code: str
) -> None:
    async with Client(_server(query_result)) as client:
        tools = await client.list_tools()
        schema = next(t for t in tools if t.name == "list_methods").output_schema
        result = await client.call_tool(
            "list_methods", {"codebase_hash": codebase_hash}
        )

    jsonschema.validate(result.structured_content, schema)
    assert result.structured_content["success"] is False
    assert isinstance(result.structured_content["error"], str)
    assert result.structured_content["error_code"] == error_code
