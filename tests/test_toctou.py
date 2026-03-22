"""Tests for the TOCTOU (Time-of-Check-Time-of-Use) race condition detection tool (CWE-367)."""

from datetime import datetime, timezone
from unittest.mock import MagicMock
import uuid

import pytest

from src.models import Config, CPGConfig, QueryResult, CodebaseInfo
from src.tools.mcp_tools import register_tools

from fastmcp import FastMCP, Client


@pytest.fixture
def toctou_services():
    """Create mock services for TOCTOU testing."""
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
                """TOCTOU (Time-of-Check-Time-of-Use) Analysis
============================================================

Found 3 method(s) containing file-check calls. Analyzing for TOCTOU...

Found 2 potential TOCTOU issue(s):

--- Issue 1 ---
Confidence:   HIGH
CWE:          CWE-367 (Use of Device File in Sensitive Operation)
Function:     check_and_open()  [src/fs_utils.c]
Path arg:     path

  CHECK  [src/fs_utils.c:24]  access(path, R_OK)
  USE    [src/fs_utils.c:28]  open(path, O_RDONLY)

  Window: 4 line(s) between check and use
  Risk:   An attacker may replace/symlink the file between the check and
          the subsequent operation, bypassing the access control decision.

--- Issue 2 ---
Confidence:   HIGH
CWE:          CWE-367 (Use of Device File in Sensitive Operation)
Function:     safe_delete()  [src/cleanup.c]
Path arg:     filename

  CHECK  [src/cleanup.c:57]  stat(filename, &st)
  USE    [src/cleanup.c:62]  unlink(filename)

  Window: 5 line(s) between check and use
  Risk:   An attacker may replace/symlink the file between the check and
          the subsequent operation, bypassing the access control decision.

Total: 2 potential TOCTOU issue(s) found
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
async def test_find_toctou_success(toctou_services):
    """Test basic TOCTOU detection returns expected output format."""
    mcp = FastMCP("TestServer")
    register_tools(mcp, toctou_services)

    async with Client(mcp) as client:
        res = await client.call_tool(
            "find_toctou",
            {"codebase_hash": toctou_services["codebase_hash"]},
        )
        result = res.content[0].text

        assert "TOCTOU" in result
        assert "CHECK" in result
        assert "USE" in result
        assert "Total:" in result


@pytest.mark.asyncio
async def test_find_toctou_access_stat_pattern(toctou_services):
    """Test that access()/stat() followed by open()/unlink() on same path is reported."""
    mcp = FastMCP("TestServer")
    register_tools(mcp, toctou_services)

    async with Client(mcp) as client:
        res = await client.call_tool(
            "find_toctou",
            {"codebase_hash": toctou_services["codebase_hash"]},
        )
        result = res.content[0].text

        assert "access" in result or "stat" in result
        assert "open" in result or "unlink" in result
        assert "CWE-367" in result


@pytest.mark.asyncio
async def test_find_toctou_high_confidence(toctou_services):
    """Test that detected TOCTOU issues are marked HIGH confidence."""
    mcp = FastMCP("TestServer")
    register_tools(mcp, toctou_services)

    async with Client(mcp) as client:
        res = await client.call_tool(
            "find_toctou",
            {"codebase_hash": toctou_services["codebase_hash"]},
        )
        result = res.content[0].text

        assert "HIGH" in result


@pytest.mark.asyncio
async def test_find_toctou_shows_window(toctou_services):
    """Test that the line window between check and use is reported."""
    mcp = FastMCP("TestServer")
    register_tools(mcp, toctou_services)

    async with Client(mcp) as client:
        res = await client.call_tool(
            "find_toctou",
            {"codebase_hash": toctou_services["codebase_hash"]},
        )
        result = res.content[0].text

        assert "Window:" in result
        assert "line(s) between check and use" in result


@pytest.mark.asyncio
async def test_find_toctou_shows_risk_description(toctou_services):
    """Test that the exploitation risk description is present."""
    mcp = FastMCP("TestServer")
    register_tools(mcp, toctou_services)

    async with Client(mcp) as client:
        res = await client.call_tool(
            "find_toctou",
            {"codebase_hash": toctou_services["codebase_hash"]},
        )
        result = res.content[0].text

        assert "symlink" in result or "replace" in result
        assert "access control" in result


@pytest.mark.asyncio
async def test_find_toctou_with_filename_filter(toctou_services):
    """Test that the filename filter is embedded in the generated query."""
    mcp = FastMCP("TestServer")
    register_tools(mcp, toctou_services)

    async with Client(mcp) as client:
        await client.call_tool(
            "find_toctou",
            {
                "codebase_hash": toctou_services["codebase_hash"],
                "filename": "fs_utils.c",
            },
        )
        assert toctou_services["query_executor"].last_query is not None
        assert "fs_utils.c" in toctou_services["query_executor"].last_query


@pytest.mark.asyncio
async def test_find_toctou_with_limit(toctou_services):
    """Test that the limit parameter is embedded in the generated query."""
    mcp = FastMCP("TestServer")
    register_tools(mcp, toctou_services)

    async with Client(mcp) as client:
        await client.call_tool(
            "find_toctou",
            {
                "codebase_hash": toctou_services["codebase_hash"],
                "limit": 20,
            },
        )
        assert toctou_services["query_executor"].last_query is not None
        assert "20" in toctou_services["query_executor"].last_query


@pytest.mark.asyncio
async def test_find_toctou_invalid_hash(toctou_services):
    """Test error handling for an invalid or missing codebase hash."""
    services = toctou_services
    services["codebase_tracker"].get_codebase.return_value = None

    mcp = FastMCP("TestServer")
    register_tools(mcp, services)

    async with Client(mcp) as client:
        res = await client.call_tool(
            "find_toctou",
            {"codebase_hash": "invalid_hash_12345"},
        )
        result = res.content[0].text

        assert "Error" in result or "not found" in result.lower()


@pytest.mark.asyncio
async def test_find_toctou_no_issues(toctou_services):
    """Test output when no TOCTOU patterns are detected."""
    services = toctou_services

    no_issue_result = QueryResult(
        success=True,
        data=[
            """TOCTOU (Time-of-Check-Time-of-Use) Analysis
============================================================

Found 2 method(s) containing file-check calls. Analyzing for TOCTOU...

No TOCTOU patterns detected.

Note: This analysis looks for:
  - A call to access()/stat()/lstat() (or similar) followed by open()/fopen()
    (or another file-operation call) on the same path argument
  - Both calls must appear in the same function
  - The check must textually precede the use (line-number order)
"""
        ],
        row_count=1,
    )
    services["query_executor"].execute_query = MagicMock(return_value=no_issue_result)

    mcp = FastMCP("TestServer")
    register_tools(mcp, services)

    async with Client(mcp) as client:
        res = await client.call_tool(
            "find_toctou",
            {"codebase_hash": services["codebase_hash"]},
        )
        result = res.content[0].text

        assert "No TOCTOU patterns detected" in result


@pytest.mark.asyncio
async def test_find_toctou_no_check_calls(toctou_services):
    """Test output when no file-check function calls are found at all."""
    services = toctou_services

    no_check_result = QueryResult(
        success=True,
        data=[
            """TOCTOU (Time-of-Check-Time-of-Use) Analysis
============================================================

No calls to file-check functions (access, stat, lstat, …) found.
"""
        ],
        row_count=1,
    )
    services["query_executor"].execute_query = MagicMock(return_value=no_check_result)

    mcp = FastMCP("TestServer")
    register_tools(mcp, services)

    async with Client(mcp) as client:
        res = await client.call_tool(
            "find_toctou",
            {"codebase_hash": services["codebase_hash"]},
        )
        result = res.content[0].text

        assert "No calls to file-check functions" in result


@pytest.mark.asyncio
async def test_find_toctou_query_error(toctou_services):
    """Test graceful handling of a query execution error."""
    services = toctou_services

    error_result = QueryResult(success=False, error="Joern connection failed", data=None)
    services["query_executor"].execute_query = MagicMock(return_value=error_result)

    mcp = FastMCP("TestServer")
    register_tools(mcp, services)

    async with Client(mcp) as client:
        res = await client.call_tool(
            "find_toctou",
            {"codebase_hash": services["codebase_hash"]},
        )
        result = res.content[0].text

        assert "Error" in result
