"""Tests for the Format String vulnerability detection tool (CWE-134)."""

import asyncio
from datetime import datetime, timezone
from unittest.mock import MagicMock
import uuid

import pytest

from src.models import Config, CPGConfig, QueryResult, CodebaseInfo
from src.tools.mcp_tools import register_tools

from fastmcp import FastMCP, Client


@pytest.fixture
def fs_services():
    """Create mock services for format string testing."""
    codebase_tracker = MagicMock()
    codebase_hash = str(uuid.uuid4()).replace('-', '')[:16]
    codebase_info = CodebaseInfo(
        codebase_hash=codebase_hash,
        source_type="local",
        source_path="/tmp/test_project",
        language="c",
        cpg_path="/tmp/test.cpg",
        created_at=datetime.now(timezone.utc),
        last_accessed=datetime.now(timezone.utc),
    )
    codebase_tracker.get_codebase.return_value = codebase_info

    query_executor = MagicMock()
    query_executor.last_query = None

    def execute_query_with_tracking(*args, **kwargs):
        if 'query' in kwargs:
            query_executor.last_query = kwargs['query']
        elif len(args) > 2:
            query_executor.last_query = args[2]

        return QueryResult(
            success=True,
            data=[
                """Format String Vulnerability Analysis
============================================================

Found 8 format-string call(s). Analyzing...

Found 2 potential format string vulnerability(ies):
  HIGH confidence:   1
  MEDIUM confidence: 1

--- Issue 1 ---
Confidence: HIGH
Location:   src/main.c:85 in log_startup_message()
Code:       printf(user_format)
Format Arg: user_format
Note:       Format argument is assigned from an external input function.

--- Issue 2 ---
Confidence: MEDIUM
Location:   src/device.c:412 in device_handle_network_command()
Code:       printf(cmd_buf)
Format Arg: cmd_buf
Note:       Format argument is not a string literal — verify it cannot contain user-controlled % directives.

Total: 2 potential format string vulnerability(ies)

Confidence levels:
  HIGH   — format arg assigned directly from a known taint source (getenv, fgets, etc.)
  MEDIUM — format arg is a variable or call result; manual review required
"""
            ],
            row_count=1,
        )

    query_executor.execute_query = execute_query_with_tracking

    cpg = CPGConfig()
    cfg = Config(cpg=cpg)

    services = {
        "codebase_tracker": codebase_tracker,
        "query_executor": query_executor,
        "config": cfg,
        "codebase_hash": codebase_hash,
    }

    return services


@pytest.mark.asyncio
async def test_find_format_string_vulns_success(fs_services):
    """Test basic format string detection returns expected output format."""
    mcp = FastMCP("TestServer")
    register_tools(mcp, fs_services)

    async with Client(mcp) as client:
        res = await client.call_tool(
            "find_format_string_vulns",
            {"codebase_hash": fs_services["codebase_hash"]}
        )
        result = res.content[0].text

        assert "Format String Vulnerability Analysis" in result
        assert "Confidence:" in result
        assert "Location:" in result
        assert "Format Arg:" in result
        assert "Total:" in result


@pytest.mark.asyncio
async def test_find_format_string_vulns_high_confidence(fs_services):
    """Test that HIGH confidence findings are reported for taint source assignments."""
    mcp = FastMCP("TestServer")
    register_tools(mcp, fs_services)

    async with Client(mcp) as client:
        res = await client.call_tool(
            "find_format_string_vulns",
            {"codebase_hash": fs_services["codebase_hash"]}
        )
        result = res.content[0].text

        assert "HIGH" in result
        assert "assigned from an external input function" in result


@pytest.mark.asyncio
async def test_find_format_string_vulns_medium_confidence(fs_services):
    """Test that MEDIUM confidence findings are reported for non-literal format args."""
    mcp = FastMCP("TestServer")
    register_tools(mcp, fs_services)

    async with Client(mcp) as client:
        res = await client.call_tool(
            "find_format_string_vulns",
            {"codebase_hash": fs_services["codebase_hash"]}
        )
        result = res.content[0].text

        assert "MEDIUM" in result
        assert "not a string literal" in result


@pytest.mark.asyncio
async def test_find_format_string_vulns_with_filename_filter(fs_services):
    """Test format string detection with filename filter."""
    mcp = FastMCP("TestServer")
    register_tools(mcp, fs_services)

    async with Client(mcp) as client:
        res = await client.call_tool(
            "find_format_string_vulns",
            {
                "codebase_hash": fs_services["codebase_hash"],
                "filename": "main.c",
            }
        )
        # Verify query was called with filename filter
        assert fs_services["query_executor"].last_query is not None
        assert "main.c" in fs_services["query_executor"].last_query


@pytest.mark.asyncio
async def test_find_format_string_vulns_with_limit(fs_services):
    """Test format string detection respects limit parameter."""
    mcp = FastMCP("TestServer")
    register_tools(mcp, fs_services)

    async with Client(mcp) as client:
        res = await client.call_tool(
            "find_format_string_vulns",
            {
                "codebase_hash": fs_services["codebase_hash"],
                "limit": 5,
            }
        )
        assert fs_services["query_executor"].last_query is not None
        assert "5" in fs_services["query_executor"].last_query


@pytest.mark.asyncio
async def test_find_format_string_vulns_invalid_hash(fs_services):
    """Test error handling for invalid codebase hash."""
    services = fs_services
    services["codebase_tracker"].get_codebase.return_value = None

    mcp = FastMCP("TestServer")
    register_tools(mcp, services)

    async with Client(mcp) as client:
        res = await client.call_tool(
            "find_format_string_vulns",
            {"codebase_hash": "invalid_hash_12345"}
        )
        result = res.content[0].text

        assert "Error" in result or "not found" in result.lower()


@pytest.mark.asyncio
async def test_find_format_string_vulns_no_issues(fs_services):
    """Test output when no format string vulnerabilities are detected."""
    services = fs_services

    no_issue_result = QueryResult(
        success=True,
        data=[
            """Format String Vulnerability Analysis
============================================================

Found 5 format-string call(s). Analyzing...

No format string vulnerabilities detected.
All format-string calls use string literal format arguments.
"""
        ],
        row_count=1,
    )
    services["query_executor"].execute_query = MagicMock(return_value=no_issue_result)

    mcp = FastMCP("TestServer")
    register_tools(mcp, services)

    async with Client(mcp) as client:
        res = await client.call_tool(
            "find_format_string_vulns",
            {"codebase_hash": services["codebase_hash"]}
        )
        result = res.content[0].text

        assert "No format string vulnerabilities detected" in result
        assert "string literal" in result


@pytest.mark.asyncio
async def test_find_format_string_vulns_no_calls_found(fs_services):
    """Test output when no format-string function calls exist."""
    services = fs_services

    no_calls_result = QueryResult(
        success=True,
        data=[
            """Format String Vulnerability Analysis
============================================================

No format-string function calls found in the codebase.
"""
        ],
        row_count=1,
    )
    services["query_executor"].execute_query = MagicMock(return_value=no_calls_result)

    mcp = FastMCP("TestServer")
    register_tools(mcp, services)

    async with Client(mcp) as client:
        res = await client.call_tool(
            "find_format_string_vulns",
            {"codebase_hash": services["codebase_hash"]}
        )
        result = res.content[0].text

        assert "No format-string function calls found" in result


@pytest.mark.asyncio
async def test_find_format_string_vulns_query_error(fs_services):
    """Test error handling when query execution fails."""
    services = fs_services

    error_result = QueryResult(
        success=False,
        data=[],
        row_count=0,
        error="Query timeout after 120 seconds"
    )
    services["query_executor"].execute_query = MagicMock(return_value=error_result)

    mcp = FastMCP("TestServer")
    register_tools(mcp, services)

    async with Client(mcp) as client:
        res = await client.call_tool(
            "find_format_string_vulns",
            {"codebase_hash": services["codebase_hash"]}
        )
        result = res.content[0].text

        assert "Error" in result
        assert "timeout" in result.lower()


@pytest.mark.asyncio
async def test_find_format_string_vulns_confidence_legend(fs_services):
    """Test that confidence level legend is included in output."""
    mcp = FastMCP("TestServer")
    register_tools(mcp, fs_services)

    async with Client(mcp) as client:
        res = await client.call_tool(
            "find_format_string_vulns",
            {"codebase_hash": fs_services["codebase_hash"]}
        )
        result = res.content[0].text

        assert "Confidence levels:" in result
        assert "taint source" in result.lower() or "getenv" in result or "fgets" in result
