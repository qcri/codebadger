"""Tests for the Stack Buffer Overflow vulnerability detection tool (CWE-121)."""

from datetime import datetime, timezone
from unittest.mock import MagicMock
import uuid

import pytest

from src.models import Config, CPGConfig, QueryResult, CodebaseInfo
from src.tools.mcp_tools import register_tools

from fastmcp import FastMCP, Client


@pytest.fixture
def so_services():
    """Create mock services for stack overflow testing."""
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
                """Stack Buffer Overflow Analysis
============================================================

Found 8 fixed-size stack array(s). Analyzing for overflow...

Found 2 potential stack buffer overflow issue(s):

--- Issue 1 ---
Confidence:    HIGH
Stack Buffer:  name (char [64])
  Location:    src/net.c:42 in handle_request()
  Array Size:  64

Dangerous Write(s):
  [src/net.c:50] strcpy(name, user_input)
    Write size: (unbounded)  |  Reason: Unbounded write (strcpy) to fixed-size stack buffer [64]

--- Issue 2 ---
Confidence:    MEDIUM
Stack Buffer:  local (char [32])
  Location:    src/main.c:185 in process_cmd()
  Array Size:  32

Dangerous Write(s):
  [src/main.c:186] memcpy(local, buffer + 5, n - 5)
    Write size: n - 5  |  Reason: Write size 'n - 5' not statically bounded by stack buffer size 32

Total: 2 potential stack buffer overflow issue(s) found
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
async def test_find_stack_overflow_success(so_services):
    """Test basic stack overflow detection returns expected output format."""
    mcp = FastMCP("TestServer")
    register_tools(mcp, so_services)

    async with Client(mcp) as client:
        res = await client.call_tool(
            "find_stack_overflow",
            {"codebase_hash": so_services["codebase_hash"]},
        )
        result = res.content[0].text

        assert "Stack Buffer Overflow Analysis" in result
        assert "Stack Buffer:" in result
        assert "Array Size:" in result
        assert "Dangerous Write(s):" in result
        assert "Total:" in result


@pytest.mark.asyncio
async def test_find_stack_overflow_unbounded_write(so_services):
    """Test that unbounded writes (strcpy, gets, sprintf) are reported as HIGH confidence."""
    mcp = FastMCP("TestServer")
    register_tools(mcp, so_services)

    async with Client(mcp) as client:
        res = await client.call_tool(
            "find_stack_overflow",
            {"codebase_hash": so_services["codebase_hash"]},
        )
        result = res.content[0].text

        assert "HIGH" in result
        assert "Unbounded write" in result
        assert "(unbounded)" in result
        assert "strcpy" in result or "gets" in result or "sprintf" in result


@pytest.mark.asyncio
async def test_find_stack_overflow_size_mismatch(so_services):
    """Test that non-literal write sizes not bounded by the array dimension are flagged."""
    mcp = FastMCP("TestServer")
    register_tools(mcp, so_services)

    async with Client(mcp) as client:
        res = await client.call_tool(
            "find_stack_overflow",
            {"codebase_hash": so_services["codebase_hash"]},
        )
        result = res.content[0].text

        assert "not statically bounded by stack buffer size" in result
        assert "memcpy" in result


@pytest.mark.asyncio
async def test_find_stack_overflow_shows_buffer_info(so_services):
    """Test that buffer name, type, location, and array size are shown."""
    mcp = FastMCP("TestServer")
    register_tools(mcp, so_services)

    async with Client(mcp) as client:
        res = await client.call_tool(
            "find_stack_overflow",
            {"codebase_hash": so_services["codebase_hash"]},
        )
        result = res.content[0].text

        assert "Stack Buffer:" in result
        assert "Location:" in result
        assert "Array Size:" in result


@pytest.mark.asyncio
async def test_find_stack_overflow_with_filename_filter(so_services):
    """Test that the filename filter is embedded in the generated query."""
    mcp = FastMCP("TestServer")
    register_tools(mcp, so_services)

    async with Client(mcp) as client:
        await client.call_tool(
            "find_stack_overflow",
            {
                "codebase_hash": so_services["codebase_hash"],
                "filename": "main.c",
            },
        )
        assert so_services["query_executor"].last_query is not None
        assert "main.c" in so_services["query_executor"].last_query


@pytest.mark.asyncio
async def test_find_stack_overflow_with_limit(so_services):
    """Test that the limit parameter is embedded in the generated query."""
    mcp = FastMCP("TestServer")
    register_tools(mcp, so_services)

    async with Client(mcp) as client:
        await client.call_tool(
            "find_stack_overflow",
            {
                "codebase_hash": so_services["codebase_hash"],
                "limit": 25,
            },
        )
        assert so_services["query_executor"].last_query is not None
        assert "25" in so_services["query_executor"].last_query


@pytest.mark.asyncio
async def test_find_stack_overflow_invalid_hash(so_services):
    """Test error handling for an invalid or missing codebase hash."""
    services = so_services
    services["codebase_tracker"].get_codebase.return_value = None

    mcp = FastMCP("TestServer")
    register_tools(mcp, services)

    async with Client(mcp) as client:
        res = await client.call_tool(
            "find_stack_overflow",
            {"codebase_hash": "invalid_hash_12345"},
        )
        result = res.content[0].text

        assert "Error" in result or "not found" in result.lower()


@pytest.mark.asyncio
async def test_find_stack_overflow_no_issues(so_services):
    """Test output when no stack buffer overflow issues are detected."""
    services = so_services

    no_issue_result = QueryResult(
        success=True,
        data=[
            """Stack Buffer Overflow Analysis
============================================================

Found 4 fixed-size stack array(s). Analyzing for overflow...

No potential stack buffer overflow issues detected.

Note: This analysis looks for:
  - Unbounded writes (strcpy, gets, sprintf) to fixed-size stack arrays
  - Bounded writes (memcpy, strncpy, snprintf) where the size argument
    exceeds or is not statically bounded by the declared array dimension

Filtered out:
  - Bounded writes with a literal size <= array dimension
  - Write sizes containing sizeof or matching the array dimension constant
  - Writes guarded by a preceding bounds-check (if comparison)
  - Writes in mutually exclusive branches from the declaration
"""
        ],
        row_count=1,
    )
    services["query_executor"].execute_query = MagicMock(return_value=no_issue_result)

    mcp = FastMCP("TestServer")
    register_tools(mcp, services)

    async with Client(mcp) as client:
        res = await client.call_tool(
            "find_stack_overflow",
            {"codebase_hash": services["codebase_hash"]},
        )
        result = res.content[0].text

        assert "No potential stack buffer overflow issues detected" in result
        assert "Filtered out:" in result


@pytest.mark.asyncio
async def test_find_stack_overflow_no_arrays(so_services):
    """Test output when no fixed-size stack arrays are found."""
    services = so_services

    no_array_result = QueryResult(
        success=True,
        data=[
            """Stack Buffer Overflow Analysis
============================================================

No fixed-size stack array declarations found in the codebase.
"""
        ],
        row_count=1,
    )
    services["query_executor"].execute_query = MagicMock(return_value=no_array_result)

    mcp = FastMCP("TestServer")
    register_tools(mcp, services)

    async with Client(mcp) as client:
        res = await client.call_tool(
            "find_stack_overflow",
            {"codebase_hash": services["codebase_hash"]},
        )
        result = res.content[0].text

        assert "No fixed-size stack array declarations found" in result


@pytest.mark.asyncio
async def test_find_stack_overflow_query_error(so_services):
    """Test error propagation when the Joern query fails."""
    services = so_services

    error_result = QueryResult(
        success=False,
        data=[],
        row_count=0,
        error="Query timeout after 240 seconds",
    )
    services["query_executor"].execute_query = MagicMock(return_value=error_result)

    mcp = FastMCP("TestServer")
    register_tools(mcp, services)

    async with Client(mcp) as client:
        res = await client.call_tool(
            "find_stack_overflow",
            {"codebase_hash": services["codebase_hash"]},
        )
        result = res.content[0].text

        assert "Error" in result
        assert "timeout" in result.lower()


@pytest.mark.asyncio
async def test_find_stack_overflow_multiple_write_ops(so_services):
    """Test detection of various dangerous write operations across multiple issues."""
    services = so_services

    multi_result = QueryResult(
        success=True,
        data=[
            """Stack Buffer Overflow Analysis
============================================================

Found 10 fixed-size stack array(s). Analyzing for overflow...

Found 4 potential stack buffer overflow issue(s):

--- Issue 1 ---
Confidence:    HIGH
Stack Buffer:  cmd (char [128])
  Location:    src/shell.c:20 in run_cmd()
  Array Size:  128

Dangerous Write(s):
  [src/shell.c:25] gets(cmd)
    Write size: (unbounded)  |  Reason: Unbounded write (gets) to fixed-size stack buffer [128]

--- Issue 2 ---
Confidence:    HIGH
Stack Buffer:  msg (char [256])
  Location:    src/log.c:10 in log_msg()
  Array Size:  256

Dangerous Write(s):
  [src/log.c:15] sprintf(msg, fmt, arg)
    Write size: (unbounded)  |  Reason: Unbounded write (sprintf) to fixed-size stack buffer [256]

--- Issue 3 ---
Confidence:    MEDIUM
Stack Buffer:  pkt (char [512])
  Location:    src/net.c:80 in recv_pkt()
  Array Size:  512

Dangerous Write(s):
  [src/net.c:90] recv(sockfd, pkt, pkt_size, 0)
    Write size: pkt_size  |  Reason: Write size 'pkt_size' not statically bounded by stack buffer size 512

--- Issue 4 ---
Confidence:    MEDIUM
Stack Buffer:  out (char [64])
  Location:    src/fmt.c:5 in format_field()
  Array Size:  64

Dangerous Write(s):
  [src/fmt.c:10] snprintf(out, field_len, "%s", value)
    Write size: field_len  |  Reason: Write size 'field_len' not statically bounded by stack buffer size 64

Total: 4 potential stack buffer overflow issue(s) found
"""
        ],
        row_count=1,
    )
    services["query_executor"].execute_query = MagicMock(return_value=multi_result)

    mcp = FastMCP("TestServer")
    register_tools(mcp, services)

    async with Client(mcp) as client:
        res = await client.call_tool(
            "find_stack_overflow",
            {"codebase_hash": services["codebase_hash"]},
        )
        result = res.content[0].text

        assert "gets" in result
        assert "sprintf" in result
        assert "recv" in result
        assert "snprintf" in result
        assert "4 potential stack buffer overflow" in result


@pytest.mark.asyncio
async def test_find_stack_overflow_literal_size_exceeds_array(so_services):
    """Test that a bounded write with a literal size larger than the array is flagged."""
    services = so_services

    literal_overflow_result = QueryResult(
        success=True,
        data=[
            """Stack Buffer Overflow Analysis
============================================================

Found 2 fixed-size stack array(s). Analyzing for overflow...

Found 1 potential stack buffer overflow issue(s):

--- Issue 1 ---
Confidence:    MEDIUM
Stack Buffer:  buf (char [16])
  Location:    src/util.c:30 in copy_field()
  Array Size:  16

Dangerous Write(s):
  [src/util.c:35] memcpy(buf, src, 32)
    Write size: 32  |  Reason: Write size 32 exceeds stack buffer size 16

Total: 1 potential stack buffer overflow issue(s) found
"""
        ],
        row_count=1,
    )
    services["query_executor"].execute_query = MagicMock(return_value=literal_overflow_result)

    mcp = FastMCP("TestServer")
    register_tools(mcp, services)

    async with Client(mcp) as client:
        res = await client.call_tool(
            "find_stack_overflow",
            {"codebase_hash": services["codebase_hash"]},
        )
        result = res.content[0].text

        assert "exceeds stack buffer size" in result
        assert "32" in result
        assert "16" in result
