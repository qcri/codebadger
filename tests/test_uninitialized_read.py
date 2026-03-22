"""Tests for the uninitialized read detection tool (CWE-457)."""

from datetime import datetime, timezone
from unittest.mock import MagicMock
import uuid

import pytest

from src.models import Config, CPGConfig, QueryResult, CodebaseInfo
from src.tools.mcp_tools import register_tools

from fastmcp import FastMCP, Client


@pytest.fixture
def uninit_services():
    """Create mock services for uninitialized read testing."""
    codebase_tracker = MagicMock()
    codebase_hash = str(uuid.uuid4()).replace("-", "")[:16]
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
        if "query" in kwargs:
            query_executor.last_query = kwargs["query"]
        elif len(args) > 2:
            query_executor.last_query = args[2]

        return QueryResult(
            success=True,
            data=[
                """Uninitialized Read Analysis
============================================================

Analyzing 12 method(s) for uninitialized reads...

Found 2 potential uninitialized read issue(s):

--- Issue 1 ---
Confidence:  HIGH
CWE:         CWE-457 (Use of Uninitialized Variable)
Variable:    result  (int)
Declared:    src/parser.c:18 in parse_header()
Read at:     src/parser.c:21
Context:     if (result > 0)
Reason:      Variable declared but never assigned before use

--- Issue 2 ---
Confidence:  HIGH
CWE:         CWE-457 (Use of Uninitialized Variable)
Variable:    buf_len  (size_t)
Declared:    src/network.c:44 in recv_packet()
Read at:     src/network.c:47
Context:     memcpy(dst, src, buf_len)
Reason:      Read at line 47 precedes first assignment at line 52

Total: 2 potential uninitialized read issue(s) found
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
async def test_find_uninitialized_reads_success(uninit_services):
    """Test basic uninitialized read detection returns expected output format."""
    mcp = FastMCP("TestServer")
    register_tools(mcp, uninit_services)

    async with Client(mcp) as client:
        res = await client.call_tool(
            "find_uninitialized_reads",
            {"codebase_hash": uninit_services["codebase_hash"]},
        )
        result = res.content[0].text

        assert "Uninitialized Read" in result
        assert "Variable:" in result
        assert "Read at:" in result
        assert "Total:" in result


@pytest.mark.asyncio
async def test_find_uninitialized_reads_cwe_tag(uninit_services):
    """Test that CWE-457 is present in findings."""
    mcp = FastMCP("TestServer")
    register_tools(mcp, uninit_services)

    async with Client(mcp) as client:
        res = await client.call_tool(
            "find_uninitialized_reads",
            {"codebase_hash": uninit_services["codebase_hash"]},
        )
        result = res.content[0].text

        assert "CWE-457" in result


@pytest.mark.asyncio
async def test_find_uninitialized_reads_high_confidence(uninit_services):
    """Test that detected issues are marked HIGH confidence."""
    mcp = FastMCP("TestServer")
    register_tools(mcp, uninit_services)

    async with Client(mcp) as client:
        res = await client.call_tool(
            "find_uninitialized_reads",
            {"codebase_hash": uninit_services["codebase_hash"]},
        )
        result = res.content[0].text

        assert "HIGH" in result


@pytest.mark.asyncio
async def test_find_uninitialized_reads_shows_context(uninit_services):
    """Test that each finding includes a code context snippet."""
    mcp = FastMCP("TestServer")
    register_tools(mcp, uninit_services)

    async with Client(mcp) as client:
        res = await client.call_tool(
            "find_uninitialized_reads",
            {"codebase_hash": uninit_services["codebase_hash"]},
        )
        result = res.content[0].text

        assert "Context:" in result
        assert "Reason:" in result


@pytest.mark.asyncio
async def test_find_uninitialized_reads_shows_declared_and_read_lines(uninit_services):
    """Test that declaration line and read line are both reported."""
    mcp = FastMCP("TestServer")
    register_tools(mcp, uninit_services)

    async with Client(mcp) as client:
        res = await client.call_tool(
            "find_uninitialized_reads",
            {"codebase_hash": uninit_services["codebase_hash"]},
        )
        result = res.content[0].text

        assert "Declared:" in result
        assert "Read at:" in result


@pytest.mark.asyncio
async def test_find_uninitialized_reads_with_filename_filter(uninit_services):
    """Test that the filename filter is embedded in the generated query."""
    mcp = FastMCP("TestServer")
    register_tools(mcp, uninit_services)

    async with Client(mcp) as client:
        await client.call_tool(
            "find_uninitialized_reads",
            {
                "codebase_hash": uninit_services["codebase_hash"],
                "filename": "parser.c",
            },
        )
        assert uninit_services["query_executor"].last_query is not None
        assert "parser.c" in uninit_services["query_executor"].last_query


@pytest.mark.asyncio
async def test_find_uninitialized_reads_with_limit(uninit_services):
    """Test that the limit parameter is embedded in the generated query."""
    mcp = FastMCP("TestServer")
    register_tools(mcp, uninit_services)

    async with Client(mcp) as client:
        await client.call_tool(
            "find_uninitialized_reads",
            {
                "codebase_hash": uninit_services["codebase_hash"],
                "limit": 25,
            },
        )
        assert uninit_services["query_executor"].last_query is not None
        assert "25" in uninit_services["query_executor"].last_query


@pytest.mark.asyncio
async def test_find_uninitialized_reads_invalid_hash(uninit_services):
    """Test error handling for an invalid or missing codebase hash."""
    services = uninit_services
    services["codebase_tracker"].get_codebase.return_value = None

    mcp = FastMCP("TestServer")
    register_tools(mcp, services)

    async with Client(mcp) as client:
        res = await client.call_tool(
            "find_uninitialized_reads",
            {"codebase_hash": "invalid_hash_12345"},
        )
        result = res.content[0].text

        assert "Error" in result or "not found" in result.lower()


@pytest.mark.asyncio
async def test_find_uninitialized_reads_no_issues(uninit_services):
    """Test output when no uninitialized reads are detected."""
    services = uninit_services

    no_issue_result = QueryResult(
        success=True,
        data=[
            """Uninitialized Read Analysis
============================================================

Analyzing 8 method(s) for uninitialized reads...

No uninitialized read issues detected.

Note: This analysis looks for:
  - Local variables that are read before any explicit assignment
  - Local variables declared but never assigned (used with garbage value)

Filtered out:
  - Fixed-size array declarations (tracked by stack overflow analysis)
  - Identifier reads that are the direct LHS of an assignment
"""
        ],
        row_count=1,
    )
    services["query_executor"].execute_query = MagicMock(return_value=no_issue_result)

    mcp = FastMCP("TestServer")
    register_tools(mcp, services)

    async with Client(mcp) as client:
        res = await client.call_tool(
            "find_uninitialized_reads",
            {"codebase_hash": services["codebase_hash"]},
        )
        result = res.content[0].text

        assert "No uninitialized read issues detected" in result


@pytest.mark.asyncio
async def test_find_uninitialized_reads_query_error(uninit_services):
    """Test graceful handling of a query execution error."""
    services = uninit_services

    error_result = QueryResult(success=False, error="Joern connection failed", data=None)
    services["query_executor"].execute_query = MagicMock(return_value=error_result)

    mcp = FastMCP("TestServer")
    register_tools(mcp, services)

    async with Client(mcp) as client:
        res = await client.call_tool(
            "find_uninitialized_reads",
            {"codebase_hash": services["codebase_hash"]},
        )
        result = res.content[0].text

        assert "Error" in result


@pytest.mark.asyncio
async def test_find_uninitialized_reads_never_assigned(uninit_services):
    """Test that a variable declared but never assigned is caught."""
    services = uninit_services

    never_assigned_result = QueryResult(
        success=True,
        data=[
            """Uninitialized Read Analysis
============================================================

Analyzing 5 method(s) for uninitialized reads...

Found 1 potential uninitialized read issue(s):

--- Issue 1 ---
Confidence:  HIGH
CWE:         CWE-457 (Use of Uninitialized Variable)
Variable:    status  (int)
Declared:    src/auth.c:10 in check_auth()
Read at:     src/auth.c:14
Context:     return status
Reason:      Variable declared but never assigned before use

Total: 1 potential uninitialized read issue(s) found
"""
        ],
        row_count=1,
    )
    services["query_executor"].execute_query = MagicMock(return_value=never_assigned_result)

    mcp = FastMCP("TestServer")
    register_tools(mcp, services)

    async with Client(mcp) as client:
        res = await client.call_tool(
            "find_uninitialized_reads",
            {"codebase_hash": services["codebase_hash"]},
        )
        result = res.content[0].text

        assert "never assigned" in result
        assert "CWE-457" in result
