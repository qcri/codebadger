"""Tests for the Integer Overflow/Underflow detection tool."""

from datetime import datetime, timezone
from unittest.mock import MagicMock
import uuid

import pytest

from src.models import Config, CPGConfig, QueryResult, CodebaseInfo
from src.tools.mcp_tools import register_tools
from src.tools.export_tools import FindingsParser

from fastmcp import FastMCP, Client


@pytest.fixture
def iof_services():
    """Create mock services for integer overflow testing."""
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

        # Default integer overflow text output
        return QueryResult(
            success=True,
            data=[
                """Integer Overflow/Underflow Analysis
============================================================

Found 20 allocation site(s). Analyzing for integer overflow risks...

Found 3 potential integer overflow/underflow issue(s):

--- Issue 1 ---
Type: Arithmetic in Allocation Size [HIGH]
  Location: parser.c:150 in parse_data()
  Code: malloc(count * elem_size)
  Arithmetic: count * elem_size (multiplication)
  Risk: Unchecked multiplication may wrap around, causing undersized buffer allocation

--- Issue 2 ---
Type: Arithmetic in Allocation Size (via variable) [HIGH]
  Location: image.c:200 in decode_image()
  Code: malloc(total_size)
  Arithmetic: width * height (multiplication)
  Risk: Unchecked multiplication may wrap around, causing undersized buffer allocation

--- Issue 3 ---
Type: Arithmetic in Array Index [MEDIUM]
  Location: utils.c:300 in process_buffer()
  Code: buffer[offset * stride]
  Arithmetic: offset * stride (multiplication)
  Risk: Unchecked multiplication may wrap around, causing out-of-bounds array access

Total: 3 potential integer overflow/underflow issue(s) found

Risk Levels:
  - [HIGH]: Multiplication or left-shift in allocation size without overflow check
  - [MEDIUM]: Addition/subtraction of variables in allocation size, or arithmetic in array index

CWE: CWE-190 (Integer Overflow or Wraparound)
Recommendation: Use overflow-safe functions (calloc, reallocarray) or add explicit
overflow checks before using arithmetic results for allocation sizes or array indices.
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
async def test_find_integer_overflow_success(iof_services):
    """Test basic integer overflow detection returns expected output format."""
    mcp = FastMCP("TestServer")
    register_tools(mcp, iof_services)

    async with Client(mcp) as client:
        res = await client.call_tool(
            "find_integer_overflow",
            {"codebase_hash": iof_services["codebase_hash"]}
        )
        result = res.content[0].text

        # Check output format
        assert "Integer Overflow/Underflow Analysis" in result
        assert "Arithmetic" in result
        assert "Location:" in result
        assert "Risk:" in result


@pytest.mark.asyncio
async def test_find_integer_overflow_with_filename_filter(iof_services):
    """Test integer overflow detection with filename filter."""
    mcp = FastMCP("TestServer")
    register_tools(mcp, iof_services)

    async with Client(mcp) as client:
        res = await client.call_tool(
            "find_integer_overflow",
            {
                "codebase_hash": iof_services["codebase_hash"],
                "filename": "parser.c",
                "limit": 50,
            }
        )
        result = res.content[0].text

        # Check that query was called
        assert iof_services["query_executor"].last_query is not None
        # Check the query contains the filename filter
        assert "parser.c" in iof_services["query_executor"].last_query


@pytest.mark.asyncio
async def test_find_integer_overflow_with_limit(iof_services):
    """Test integer overflow detection respects limit parameter."""
    mcp = FastMCP("TestServer")
    register_tools(mcp, iof_services)

    async with Client(mcp) as client:
        res = await client.call_tool(
            "find_integer_overflow",
            {
                "codebase_hash": iof_services["codebase_hash"],
                "limit": 10,
            }
        )

        # Check that query was executed with limit
        assert iof_services["query_executor"].last_query is not None
        # The limit should be templated into the query
        assert "10" in iof_services["query_executor"].last_query


@pytest.mark.asyncio
async def test_find_integer_overflow_detects_risk_levels(iof_services):
    """Test that integer overflow detection identifies different risk levels."""
    mcp = FastMCP("TestServer")
    register_tools(mcp, iof_services)

    async with Client(mcp) as client:
        res = await client.call_tool(
            "find_integer_overflow",
            {"codebase_hash": iof_services["codebase_hash"]}
        )
        result = res.content[0].text

        # Check risk levels are documented
        assert "[HIGH]" in result
        assert "[MEDIUM]" in result
        assert "Risk Levels:" in result


@pytest.mark.asyncio
async def test_find_integer_overflow_detects_issue_types(iof_services):
    """Test that integer overflow detection identifies different issue types."""
    mcp = FastMCP("TestServer")
    register_tools(mcp, iof_services)

    async with Client(mcp) as client:
        res = await client.call_tool(
            "find_integer_overflow",
            {"codebase_hash": iof_services["codebase_hash"]}
        )
        result = res.content[0].text

        # Check issue types
        assert "Arithmetic in Allocation Size" in result
        assert "Arithmetic in Array Index" in result
        assert "multiplication" in result


@pytest.mark.asyncio
async def test_find_integer_overflow_invalid_hash(iof_services):
    """Test error handling for invalid codebase hash."""
    services = iof_services
    services["codebase_tracker"].get_codebase.return_value = None

    mcp = FastMCP("TestServer")
    register_tools(mcp, services)

    async with Client(mcp) as client:
        res = await client.call_tool(
            "find_integer_overflow",
            {"codebase_hash": "invalid_hash_12345"}
        )
        result = res.content[0].text

        # Should return validation error
        assert "Error" in result or "not found" in result.lower()


@pytest.mark.asyncio
async def test_find_integer_overflow_no_issues_found(iof_services):
    """Test output when no integer overflow issues are detected."""
    services = iof_services

    # Mock empty result
    no_issues_result = QueryResult(
        success=True,
        data=[
            """Integer Overflow/Underflow Analysis
============================================================

Found 15 allocation site(s). Analyzing for integer overflow risks...

No potential integer overflow/underflow issues detected.

Note: This analysis checks for:
  - Unchecked multiplication/left-shift in allocation sizes
  - Unchecked addition/subtraction of two variables in allocation sizes
  - Unchecked multiplication/left-shift in array indices

Filtered out:
  - Constant expressions (sizeof * literal, etc.)
  - Arithmetic guarded by overflow checks (SIZE_MAX, __builtin_*_overflow, etc.)
  - calloc/reallocarray (handle overflow internally)
  - Single-variable + constant additions (e.g., len + 1)
  - Array indices with preceding bounds checks
"""
        ],
        row_count=1,
    )
    services["query_executor"].execute_query = MagicMock(return_value=no_issues_result)

    mcp = FastMCP("TestServer")
    register_tools(mcp, services)

    async with Client(mcp) as client:
        res = await client.call_tool(
            "find_integer_overflow",
            {"codebase_hash": services["codebase_hash"]}
        )
        result = res.content[0].text

        assert "No potential integer overflow/underflow issues detected" in result
        assert "Filtered out:" in result
        assert "calloc/reallocarray" in result


@pytest.mark.asyncio
async def test_find_integer_overflow_no_alloc_calls(iof_services):
    """Test output when no allocation calls exist in codebase."""
    services = iof_services

    no_alloc_result = QueryResult(
        success=True,
        data=[
            """Integer Overflow/Underflow Analysis
============================================================

No allocation calls found in the codebase.
"""
        ],
        row_count=1,
    )
    services["query_executor"].execute_query = MagicMock(return_value=no_alloc_result)

    mcp = FastMCP("TestServer")
    register_tools(mcp, services)

    async with Client(mcp) as client:
        res = await client.call_tool(
            "find_integer_overflow",
            {"codebase_hash": services["codebase_hash"]}
        )
        result = res.content[0].text

        assert "No allocation calls found" in result


@pytest.mark.asyncio
async def test_find_integer_overflow_query_error(iof_services):
    """Test error handling when query execution fails."""
    services = iof_services

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
            "find_integer_overflow",
            {"codebase_hash": services["codebase_hash"]}
        )
        result = res.content[0].text

        assert "Error" in result
        assert "timeout" in result.lower()


@pytest.mark.asyncio
async def test_find_integer_overflow_indirect_arithmetic(iof_services):
    """Test detection of indirect arithmetic (via variable) patterns."""
    services = iof_services

    indirect_result = QueryResult(
        success=True,
        data=[
            """Integer Overflow/Underflow Analysis
============================================================

Found 5 allocation site(s). Analyzing for integer overflow risks...

Found 1 potential integer overflow/underflow issue(s):

--- Issue 1 ---
Type: Arithmetic in Allocation Size (via variable) [HIGH]
  Location: codec.c:80 in decode_frame()
  Code: malloc(frame_size)
  Arithmetic: width * height * bpp (multiplication)
  Risk: Unchecked multiplication may wrap around, causing undersized buffer allocation

Total: 1 potential integer overflow/underflow issue(s) found

Risk Levels:
  - [HIGH]: Multiplication or left-shift in allocation size without overflow check
  - [MEDIUM]: Addition/subtraction of variables in allocation size, or arithmetic in array index
"""
        ],
        row_count=1,
    )
    services["query_executor"].execute_query = MagicMock(return_value=indirect_result)

    mcp = FastMCP("TestServer")
    register_tools(mcp, services)

    async with Client(mcp) as client:
        res = await client.call_tool(
            "find_integer_overflow",
            {"codebase_hash": services["codebase_hash"]}
        )
        result = res.content[0].text

        assert "via variable" in result
        assert "[HIGH]" in result
        assert "multiplication" in result


@pytest.mark.asyncio
async def test_find_integer_overflow_left_shift(iof_services):
    """Test detection of left-shift overflow patterns."""
    services = iof_services

    shift_result = QueryResult(
        success=True,
        data=[
            """Integer Overflow/Underflow Analysis
============================================================

Found 8 allocation site(s). Analyzing for integer overflow risks...

Found 1 potential integer overflow/underflow issue(s):

--- Issue 1 ---
Type: Arithmetic in Allocation Size [HIGH]
  Location: bitstream.c:45 in alloc_buffer()
  Code: malloc(1 << nbits)
  Arithmetic: 1 << nbits (left-shift)
  Risk: Unchecked left-shift may wrap around, causing undersized buffer allocation

Total: 1 potential integer overflow/underflow issue(s) found
"""
        ],
        row_count=1,
    )
    services["query_executor"].execute_query = MagicMock(return_value=shift_result)

    mcp = FastMCP("TestServer")
    register_tools(mcp, services)

    async with Client(mcp) as client:
        res = await client.call_tool(
            "find_integer_overflow",
            {"codebase_hash": services["codebase_hash"]}
        )
        result = res.content[0].text

        assert "left-shift" in result
        assert "[HIGH]" in result


@pytest.mark.asyncio
async def test_find_integer_overflow_cross_function(iof_services):
    """Test detection of cross-function interprocedural overflow patterns."""
    services = iof_services

    cross_func_result = QueryResult(
        success=True,
        data=[
            """Integer Overflow/Underflow Analysis
============================================================

Found 12 allocation site(s). Analyzing for integer overflow risks...

Found 2 potential integer overflow/underflow issue(s):

--- Issue 1 ---
Type: Arithmetic in Allocation Size [HIGH]
  Location: parser.c:150 in parse_data()
  Code: malloc(count * elem_size)
  Arithmetic: count * elem_size (multiplication)
  Risk: Unchecked multiplication may wrap around, causing undersized buffer allocation

--- Issue 2 ---
Type: Cross-Function Arithmetic to Allocation [HIGH] [CROSS-FUNC]
  Location: alloc.c:50 in allocate_buffer()
  Code: malloc(total)
  Arithmetic: width * height (at compute.c:20) [via: compute_size -> allocate_buffer] (multiplication)
  Risk: Unchecked multiplication may wrap around, causing undersized buffer allocation

Total: 2 potential integer overflow/underflow issue(s) found

Risk Levels:
  - [HIGH]: Multiplication or left-shift in allocation size without overflow check
  - [HIGH] [CROSS-FUNC]: Cross-function arithmetic result used in allocation size
  - [MEDIUM]: Addition/subtraction of variables in allocation size, or arithmetic in array index

CWE: CWE-190 (Integer Overflow or Wraparound)
Recommendation: Use overflow-safe functions (calloc, reallocarray) or add explicit
overflow checks before using arithmetic results for allocation sizes or array indices.
"""
        ],
        row_count=1,
    )
    services["query_executor"].execute_query = MagicMock(return_value=cross_func_result)

    mcp = FastMCP("TestServer")
    register_tools(mcp, services)

    async with Client(mcp) as client:
        res = await client.call_tool(
            "find_integer_overflow",
            {"codebase_hash": services["codebase_hash"]}
        )
        result = res.content[0].text

        assert "[CROSS-FUNC]" in result
        assert "Cross-Function Arithmetic to Allocation" in result
        assert "via:" in result
        assert "[HIGH]" in result
        assert "CWE-190" in result


class TestIntegerOverflowParser:
    """Tests for the FindingsParser.parse_integer_overflow_text method."""

    def test_parse_basic_issues(self):
        """Test parsing of basic integer overflow output."""
        text = """Integer Overflow/Underflow Analysis
============================================================

Found 2 potential integer overflow/underflow issue(s):

--- Issue 1 ---
Type: Arithmetic in Allocation Size [HIGH]
  Location: parser.c:150 in parse_data()
  Code: malloc(count * elem_size)
  Arithmetic: count * elem_size (multiplication)
  Risk: Unchecked multiplication may wrap around, causing undersized buffer allocation

--- Issue 2 ---
Type: Arithmetic in Array Index [MEDIUM]
  Location: utils.c:300 in process_buffer()
  Code: buffer[offset * stride]
  Arithmetic: offset * stride (multiplication)
  Risk: Unchecked multiplication may wrap around, causing out-of-bounds array access

Total: 2 potential integer overflow/underflow issue(s) found
"""
        findings = FindingsParser.parse_integer_overflow_text(text, "abc123")

        assert len(findings) == 2

        # Check first finding (HIGH)
        f1 = findings[0]
        assert f1["finding_type"] == "integer_overflow"
        assert f1["severity"] == "high"
        assert f1["confidence"] == "high"
        assert f1["filename"] == "parser.c"
        assert f1["line_number"] == 150
        assert f1["cwe_id"] == 190
        assert "count * elem_size" in f1["message"]
        assert f1["flow_data"]["risk_level"] == "HIGH"

        # Check second finding (MEDIUM)
        f2 = findings[1]
        assert f2["severity"] == "medium"
        assert f2["filename"] == "utils.c"
        assert f2["line_number"] == 300
        assert f2["flow_data"]["risk_level"] == "MEDIUM"

    def test_parse_no_issues(self):
        """Test parsing when no issues are found."""
        text = """Integer Overflow/Underflow Analysis
============================================================

No potential integer overflow/underflow issues detected.
"""
        findings = FindingsParser.parse_integer_overflow_text(text, "abc123")
        assert len(findings) == 0

    def test_parse_indirect_arithmetic(self):
        """Test parsing of indirect (via variable) patterns."""
        text = """--- Issue 1 ---
Type: Arithmetic in Allocation Size (via variable) [HIGH]
  Location: codec.c:80 in decode_frame()
  Code: malloc(frame_size)
  Arithmetic: width * height (multiplication)
  Risk: Unchecked multiplication may wrap around, causing undersized buffer allocation
"""
        findings = FindingsParser.parse_integer_overflow_text(text, "abc123")

        assert len(findings) == 1
        assert findings[0]["severity"] == "high"
        assert findings[0]["filename"] == "codec.c"
        assert findings[0]["line_number"] == 80
        assert "width * height" in findings[0]["message"]

    def test_parse_cross_function(self):
        """Test parsing of cross-function interprocedural patterns."""
        text = """--- Issue 1 ---
Type: Cross-Function Arithmetic to Allocation [HIGH] [CROSS-FUNC]
  Location: alloc.c:50 in allocate_buffer()
  Code: malloc(total)
  Arithmetic: width * height (at compute.c:20) [via: compute_size -> allocate_buffer] (multiplication)
  Risk: Unchecked multiplication may wrap around, causing undersized buffer allocation
"""
        findings = FindingsParser.parse_integer_overflow_text(text, "abc123")

        assert len(findings) == 1
        assert findings[0]["severity"] == "high"
        assert findings[0]["filename"] == "alloc.c"
        assert findings[0]["line_number"] == 50
        assert findings[0]["flow_data"]["cross_function"] is True
        assert findings[0]["flow_data"]["risk_level"] == "HIGH"
        assert "Cross-Function" in findings[0]["message"]

    def test_parse_empty_text(self):
        """Test parsing of empty text."""
        findings = FindingsParser.parse_integer_overflow_text("", "abc123")
        assert len(findings) == 0
