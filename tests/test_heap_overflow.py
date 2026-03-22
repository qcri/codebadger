"""Tests for the Heap Overflow vulnerability detection tool (CWE-122)."""

import asyncio
from datetime import datetime, timezone
from unittest.mock import MagicMock
import uuid

import pytest

from src.models import Config, CPGConfig, QueryResult, CodebaseInfo
from src.tools.mcp_tools import register_tools

from fastmcp import FastMCP, Client


@pytest.fixture
def ho_services():
    """Create mock services for heap overflow testing."""
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
                """Heap Overflow Analysis
============================================================

Found 12 allocation site(s). Analyzing for heap overflow...

Found 2 potential heap overflow issue(s):

--- Issue 1 ---
Confidence:      HIGH
Allocation Site: malloc(64)
  Location:      src/network.c:210 in network_handle_raw_data()
  Buffer:        buf (size: 64)

Dangerous Write(s):
  [src/network.c:218] memcpy(buf, data, data_len)
    Write size: data_len  |  Reason: Write size 'data_len' not bounded by allocation size '64'

--- Issue 2 ---
Confidence:      HIGH
Allocation Site: malloc(MAX_CMD_LEN)
  Location:      src/device.c:290 in device_process_untrusted_data()
  Buffer:        cmd_buf (size: MAX_CMD_LEN)

Dangerous Write(s):
  [src/device.c:298] strcpy(cmd_buf, src)
    Write size: (unbounded)  |  Reason: Unbounded write (strcpy) — no size limit enforced

Total: 2 potential heap overflow issue(s) found
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
async def test_find_heap_overflow_success(ho_services):
    """Test basic heap overflow detection returns expected output format."""
    mcp = FastMCP("TestServer")
    register_tools(mcp, ho_services)

    async with Client(mcp) as client:
        res = await client.call_tool(
            "find_heap_overflow",
            {"codebase_hash": ho_services["codebase_hash"]}
        )
        result = res.content[0].text

        assert "Heap Overflow Analysis" in result
        assert "Allocation Site:" in result
        assert "Dangerous Write(s):" in result
        assert "Total:" in result


@pytest.mark.asyncio
async def test_find_heap_overflow_unbounded_write(ho_services):
    """Test that unbounded writes (strcpy, gets, sprintf) are detected as HIGH confidence."""
    mcp = FastMCP("TestServer")
    register_tools(mcp, ho_services)

    async with Client(mcp) as client:
        res = await client.call_tool(
            "find_heap_overflow",
            {"codebase_hash": ho_services["codebase_hash"]}
        )
        result = res.content[0].text

        assert "Unbounded write" in result
        assert "strcpy" in result or "gets" in result or "sprintf" in result
        assert "(unbounded)" in result


@pytest.mark.asyncio
async def test_find_heap_overflow_size_mismatch(ho_services):
    """Test that size-mismatched bounded writes are detected."""
    mcp = FastMCP("TestServer")
    register_tools(mcp, ho_services)

    async with Client(mcp) as client:
        res = await client.call_tool(
            "find_heap_overflow",
            {"codebase_hash": ho_services["codebase_hash"]}
        )
        result = res.content[0].text

        assert "not bounded by allocation size" in result
        assert "memcpy" in result


@pytest.mark.asyncio
async def test_find_heap_overflow_shows_buffer_info(ho_services):
    """Test that allocation site and buffer name are shown."""
    mcp = FastMCP("TestServer")
    register_tools(mcp, ho_services)

    async with Client(mcp) as client:
        res = await client.call_tool(
            "find_heap_overflow",
            {"codebase_hash": ho_services["codebase_hash"]}
        )
        result = res.content[0].text

        assert "Buffer:" in result
        assert "size:" in result
        assert "Location:" in result


@pytest.mark.asyncio
async def test_find_heap_overflow_with_filename_filter(ho_services):
    """Test heap overflow detection with filename filter."""
    mcp = FastMCP("TestServer")
    register_tools(mcp, ho_services)

    async with Client(mcp) as client:
        res = await client.call_tool(
            "find_heap_overflow",
            {
                "codebase_hash": ho_services["codebase_hash"],
                "filename": "network.c",
            }
        )
        assert ho_services["query_executor"].last_query is not None
        assert "network.c" in ho_services["query_executor"].last_query


@pytest.mark.asyncio
async def test_find_heap_overflow_with_limit(ho_services):
    """Test heap overflow detection respects limit parameter."""
    mcp = FastMCP("TestServer")
    register_tools(mcp, ho_services)

    async with Client(mcp) as client:
        res = await client.call_tool(
            "find_heap_overflow",
            {
                "codebase_hash": ho_services["codebase_hash"],
                "limit": 20,
            }
        )
        assert ho_services["query_executor"].last_query is not None
        assert "20" in ho_services["query_executor"].last_query


@pytest.mark.asyncio
async def test_find_heap_overflow_invalid_hash(ho_services):
    """Test error handling for invalid codebase hash."""
    services = ho_services
    services["codebase_tracker"].get_codebase.return_value = None

    mcp = FastMCP("TestServer")
    register_tools(mcp, services)

    async with Client(mcp) as client:
        res = await client.call_tool(
            "find_heap_overflow",
            {"codebase_hash": "invalid_hash_12345"}
        )
        result = res.content[0].text

        assert "Error" in result or "not found" in result.lower()


@pytest.mark.asyncio
async def test_find_heap_overflow_no_issues(ho_services):
    """Test output when no heap overflow issues are detected."""
    services = ho_services

    no_issue_result = QueryResult(
        success=True,
        data=[
            """Heap Overflow Analysis
============================================================

Found 6 allocation site(s). Analyzing for heap overflow...

No potential heap overflow issues detected.

Note: This analysis looks for:
  - Unbounded writes (strcpy, gets, sprintf) to heap-allocated buffers
  - Sized writes (memcpy, read, recv) where the write size is not
    bounded by or equal to the allocation size

Filtered out:
  - Writes guarded by a bounds check before the write
  - Writes where the size expression matches the allocation size
  - Buffer reassignments between allocation and write
  - Writes in mutually exclusive branches from the allocation
"""
        ],
        row_count=1,
    )
    services["query_executor"].execute_query = MagicMock(return_value=no_issue_result)

    mcp = FastMCP("TestServer")
    register_tools(mcp, services)

    async with Client(mcp) as client:
        res = await client.call_tool(
            "find_heap_overflow",
            {"codebase_hash": services["codebase_hash"]}
        )
        result = res.content[0].text

        assert "No potential heap overflow issues detected" in result
        assert "Unbounded writes" in result
        assert "Filtered out:" in result


@pytest.mark.asyncio
async def test_find_heap_overflow_no_allocations(ho_services):
    """Test output when no heap allocation calls exist in the codebase."""
    services = ho_services

    no_alloc_result = QueryResult(
        success=True,
        data=[
            """Heap Overflow Analysis
============================================================

No heap allocation calls found in the codebase.
"""
        ],
        row_count=1,
    )
    services["query_executor"].execute_query = MagicMock(return_value=no_alloc_result)

    mcp = FastMCP("TestServer")
    register_tools(mcp, services)

    async with Client(mcp) as client:
        res = await client.call_tool(
            "find_heap_overflow",
            {"codebase_hash": services["codebase_hash"]}
        )
        result = res.content[0].text

        assert "No heap allocation calls found" in result


@pytest.mark.asyncio
async def test_find_heap_overflow_query_error(ho_services):
    """Test error handling when query execution fails."""
    services = ho_services

    error_result = QueryResult(
        success=False,
        data=[],
        row_count=0,
        error="Query timeout after 240 seconds"
    )
    services["query_executor"].execute_query = MagicMock(return_value=error_result)

    mcp = FastMCP("TestServer")
    register_tools(mcp, services)

    async with Client(mcp) as client:
        res = await client.call_tool(
            "find_heap_overflow",
            {"codebase_hash": services["codebase_hash"]}
        )
        result = res.content[0].text

        assert "Error" in result
        assert "timeout" in result.lower()


@pytest.mark.asyncio
async def test_find_heap_overflow_multiple_write_ops(ho_services):
    """Test detection of various dangerous write operations."""
    services = ho_services

    multi_write_result = QueryResult(
        success=True,
        data=[
            """Heap Overflow Analysis
============================================================

Found 5 allocation site(s). Analyzing for heap overflow...

Found 3 potential heap overflow issue(s):

--- Issue 1 ---
Confidence:      HIGH
Allocation Site: malloc(128)
  Location:      src/net.c:50 in recv_data()
  Buffer:        packet (size: 128)

Dangerous Write(s):
  [src/net.c:60] recv(sock, packet, pkt_size, 0)
    Write size: pkt_size  |  Reason: Write size 'pkt_size' not bounded by allocation size '128'

--- Issue 2 ---
Confidence:      HIGH
Allocation Site: malloc(256)
  Location:      src/cmd.c:30 in process_cmd()
  Buffer:        outbuf (size: 256)

Dangerous Write(s):
  [src/cmd.c:40] sprintf(outbuf, fmt, arg)
    Write size: (unbounded)  |  Reason: Unbounded write (sprintf) — no size limit enforced

--- Issue 3 ---
Confidence:      MEDIUM
Allocation Site: malloc(len)
  Location:      src/io.c:20 in read_file()
  Buffer:        filebuf (size: len)

Dangerous Write(s):
  [src/io.c:35] read(fd, filebuf, read_len)
    Write size: read_len  |  Reason: Write size 'read_len' not bounded by allocation size 'len'

Total: 3 potential heap overflow issue(s) found
"""
        ],
        row_count=1,
    )
    services["query_executor"].execute_query = MagicMock(return_value=multi_write_result)

    mcp = FastMCP("TestServer")
    register_tools(mcp, services)

    async with Client(mcp) as client:
        res = await client.call_tool(
            "find_heap_overflow",
            {"codebase_hash": services["codebase_hash"]}
        )
        result = res.content[0].text

        assert "recv" in result
        assert "sprintf" in result
        assert "read" in result
        assert "3 potential heap overflow" in result
