"""
Code Browsing MCP Tools for CodeBadger Server
Tools for exploring and navigating codebase structure
"""

import logging
import os
import re
from typing import Any, Dict, Optional, Annotated, Literal
from pydantic import Field
from fastmcp.exceptions import ToolError

from ..defaults import MAX_RESULT_ROWS
from ..exceptions import (
            ValidationError,
)
from ..utils.validators import (
    validate_codebase_hash,
    validate_cpgql_query,
)
from .queries import QueryLoader
from ._common import require_cpg, tool_error, tool_errors, unwrap_result

logger = logging.getLogger(__name__)

_DUPLICATE_TYPE_SUFFIX = re.compile(r"<duplicate>[0-9]+$")


def _deduplicate_type_definitions(types):
    """Normalize Joern duplicate suffixes and prefer canonical definitions."""
    unique = {}
    for item in types:
        if not isinstance(item, dict):
            continue

        full_name = str(item.get("fullName") or "")
        canonical_full_name = _DUPLICATE_TYPE_SUFFIX.sub("", full_name)
        normalized = {**item, "fullName": canonical_full_name}
        key = canonical_full_name or str(item.get("name") or "")
        existing = unique.get(key)
        if existing is None:
            unique[key] = (normalized, full_name != canonical_full_name)
            continue

        _, existing_is_duplicate = existing
        candidate_is_duplicate = full_name != canonical_full_name
        if existing_is_duplicate and not candidate_is_duplicate:
            unique[key] = (normalized, False)

    return [definition for definition, _ in unique.values()]


def _get_playground_path() -> str:
    return os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "playground"))



def _resolve_method_selector(method_name: Optional[str], method_id) -> tuple[str, int]:
    """Validate the (method_name, method_id) pair for CFG/call-graph templates.

    method_id is the graph node ID that list_methods returns as `node_id`; it wins
    over method_name. Returns (name_pattern, node_id or -1).
    """
    if method_id is not None and str(method_id).strip() != "":
        text = str(method_id).strip()
        if not text.isdigit():
            raise ValidationError("method_id must be the numeric node_id returned by list_methods")
        return (method_name or "").strip(), int(text)
    if not method_name or not method_name.strip():
        raise ValidationError("Provide method_name or method_id")
    return method_name.strip(), -1


def register_code_browsing_tools(mcp, services: dict):
    """Register code browsing MCP tools with the FastMCP server"""


    @mcp.tool(
        title="List Methods",
        annotations={"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False},
        output_schema={
            "type": "object",
            "properties": {
                "success": {"type": "boolean"},
                "methods": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string"}, "node_id": {"type": "string"},
                            "fullName": {"type": "string"}, "signature": {"type": "string"},
                            "filename": {"type": "string"}, "lineNumber": {"type": "integer"},
                            "lineNumberEnd": {"type": "integer"},
                            "cyclomaticComplexity": {"type": "number"},
                            "numberOfLines": {"type": "integer"}, "isExternal": {"type": "boolean"},
                        },
                        "additionalProperties": False,
                    },
                },
                "total": {"type": "integer"}, "returned": {"type": "integer"},
                "truncated": {"type": "boolean"}, "page": {"type": "integer"},
                "page_size": {"type": "integer"}, "total_pages": {"type": "integer"},
            },
            "required": ["success"],
            "additionalProperties": False,
        },
        description="""List methods/functions in the codebase.

Discover all methods and functions defined in the analyzed code.

Returns:
    {
        "success": true,
        "methods": [{"name": "main", "filename": "main.c", ...}],
        "total": 1250,
        "returned": 100,
        "truncated": true,
        "page": 1,
        "page_size": 100,
        "total_pages": 10
    }

Notes:
    - Use name_pattern to find specific methods.
    - Use callee_pattern to find usages (e.g., who calls 'malloc').

Examples:
    list_methods(codebase_hash="abc", name_pattern=".*auth.*")
    list_methods(codebase_hash="abc", callee_pattern="memcpy")""",
    )
    @tool_errors("verify the codebase_hash with get_cpg_status and check the regex filters.")
    def list_methods(
        codebase_hash: Annotated[str, Field(description="The codebase hash from generate_cpg")],
        name_pattern: Annotated[Optional[str], Field(description="Optional regex to filter method names (e.g., '.*authenticate.*')")] = None,
        file_pattern: Annotated[Optional[str], Field(description="Optional regex to filter by file path")] = None,
        callee_pattern: Annotated[Optional[str], Field(description="Optional regex to filter for methods that call a specific function (e.g., 'memcpy|free|malloc')")] = None,
        include_external: Annotated[bool, Field(description="Include external/library methods")] = False,
        limit: Annotated[int, Field(description="Maximum number of matching methods made available for pagination; total still reports the exact match count", ge=1, le=MAX_RESULT_ROWS)] = 1000,
        page: Annotated[int, Field(description="Page number (1-based)", ge=1)] = 1,
        page_size: Annotated[int, Field(description="Number of results per page", ge=1, le=1000)] = 100,
    ) -> Dict[str, Any]:
        """Discover all methods and functions defined in the codebase."""
        code_browsing_service = services["code_browsing_service"]
        return code_browsing_service.list_methods(
            codebase_hash=codebase_hash,
            name_pattern=name_pattern,
            file_pattern=file_pattern,
            callee_pattern=callee_pattern,
            include_external=include_external,
            limit=limit,
            page=page,
            page_size=page_size,
        )


    @mcp.tool(
        title="List Calls",
        annotations={"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False},
        output_schema={
            "type": "object",
            "properties": {
                "success": {"type": "boolean"},
                "calls": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "caller": {"type": "string"}, "callee": {"type": "string"},
                            "code": {"type": "string"}, "filename": {"type": "string"},
                            "lineNumber": {"type": "integer"},
                        },
                        "additionalProperties": False,
                    },
                },
                "total": {"type": "integer"}, "returned": {"type": "integer"},
                "truncated": {"type": "boolean"}, "page": {"type": "integer"},
                "page_size": {"type": "integer"}, "total_pages": {"type": "integer"},
            },
            "required": ["success"],
            "additionalProperties": False,
        },
        description="""List function/method calls in the codebase.

Discover call relationships between functions.

Returns:
    {
        "success": true,
        "calls": [
            {"caller": "main", "callee": "printf", "filename": "main.c", "lineNumber": 10}
        ],
        "total": 25000,
        "returned": 100,
        "truncated": true,
        "page": 1,
        "page_size": 100,
        "total_pages": 10
    }

Notes:
    - Useful for finding where specific functions are used.

Examples:
    list_calls(codebase_hash="abc", callee_pattern="strcpy")
    list_calls(codebase_hash="abc", caller_pattern="main")""",
    )
    @tool_errors("verify the codebase_hash with get_cpg_status and check the regex filters.")
    def list_calls(
        codebase_hash: Annotated[str, Field(description="The codebase hash from generate_cpg")],
        caller_pattern: Annotated[Optional[str], Field(description="Optional regex to filter caller method names")] = None,
        callee_pattern: Annotated[Optional[str], Field(description="Optional regex to filter callee method names")] = None,
        limit: Annotated[int, Field(description="Maximum number of matching calls made available for pagination; total still reports the exact match count", ge=1, le=MAX_RESULT_ROWS)] = 1000,
        page: Annotated[int, Field(description="Page number (1-based)", ge=1)] = 1,
        page_size: Annotated[int, Field(description="Number of results per page", ge=1, le=1000)] = 100,
    ) -> Dict[str, Any]:
        """Find function call relationships in the codebase."""
        code_browsing_service = services["code_browsing_service"]
        return code_browsing_service.list_calls(
            codebase_hash=codebase_hash,
            caller_pattern=caller_pattern,
            callee_pattern=callee_pattern,
            limit=limit,
            page=page,
            page_size=page_size,
        )


    @mcp.tool(
        title="Get Call Graph",
        annotations={"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False},
        output_schema={"type": "object", "properties": {"success": {"type": "boolean"}, "summary": {"type": "string"}}, "required": ["success"], "additionalProperties": False},
        description="""Get the call graph for a specific method.

Understand what functions a method calls (outgoing) or what functions
call it (incoming).

Returns:
    A human-readable text summary:
    
    Call Graph for main (outgoing)
    ============================================================
    Root: main at main.c:10
    
    [DEPTH 1]
      main → init (config.c:25)
      main → process (core.c:50)
    
    [DEPTH 2]
      init → load_config (config.c:100)
      process → validate (core.c:120)
    
    Total: 4 edges

Notes:
    - Essential for impact analysis and understanding code dependencies.
    - Returns plain text.
    - Includes file and line number for each call target.
    - Line numbers refer to where the caller function starts, not the specific call site.
    - If method_name matches several methods (overloads, file-static helpers), the
      summary lists up to 10 candidates with their method_id; re-run with one.

Examples:
    get_call_graph(codebase_hash="abc", method_name="main", direction="outgoing")
    get_call_graph(codebase_hash="abc", method_id="12345", direction="incoming")
    get_call_graph(codebase_hash="abc", method_name="vuln_func", direction="incoming")""",
    )
    def get_call_graph(
        codebase_hash: Annotated[str, Field(description="The codebase hash from generate_cpg")],
        method_name: Annotated[Optional[str], Field(description="Name of the method to analyze (regex; must match one method)")] = None,
        depth: Annotated[int, Field(description="How many levels deep to traverse (max recommended: 10)")] = 5,
        direction: Annotated[Literal["outgoing", "incoming"], Field(description="'outgoing' (callees) or 'incoming' (callers)")] = "outgoing",
        detail: Annotated[Literal["compact", "full"], Field(description="Output detail; compact returns a bounded summary")] = "compact",
        method_id: Annotated[Optional[str], Field(description="Exact method node_id from list_methods; overrides method_name")] = None,
    ) -> Dict[str, Any]:
        """Build the call graph showing callers or callees for a method."""
        try:
            validate_codebase_hash(codebase_hash)

            if depth < 1 or depth > 15:
                raise ValidationError("Depth must be between 1 and 15")

            if direction not in ["outgoing", "incoming"]:
                raise ValidationError("Direction must be 'outgoing' or 'incoming'")

            method_name, node_id = _resolve_method_selector(method_name, method_id)

            query_executor = services["query_executor"]

            codebase_info = require_cpg(services, codebase_hash)

            query = QueryLoader.load(
                "call_graph",
                method_name=method_name,
                method_id=node_id,
                depth=depth,
                direction=direction
            )

            result = query_executor.execute_query(
                codebase_hash=codebase_hash,
                cpg_path=codebase_info.cpg_path,
                query=query,
                timeout=120,
                limit=500,
            )

            if not result.success:
                raise ToolError(f"{result.error_code or 'QUERY_ERROR'}: {result.error or 'Call graph query failed'} Hint: verify the CPG is ready and narrow the method/depth filters.")
            summary = unwrap_result(result)
            if detail == "compact" and len(summary) > 2000:
                summary = summary[:2000].rstrip() + "\n...[truncated; use detail='full']"
            return {"success": True, "summary": summary}

        except ValidationError as e:
            logger.error(f"Error getting call graph: {e}")
            raise ToolError(f"VALIDATION_ERROR: {str(e)} Hint: pass method_name or a numeric method_id, check direction, and keep depth between 1 and 15.") from e
        except ToolError:
            raise
        except Exception as e:
            logger.error(f"Unexpected error: {e}", exc_info=True)
            raise ToolError(f"INTERNAL_ERROR: {str(e)} Hint: retry after checking CPG/backend status.") from e


    @mcp.tool(
        title="List Parameters",
        annotations={"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False},
        output_schema={
            "type": "object",
            "properties": {
                "success": {"type": "boolean"},
                "methods": {"type": "array", "items": {"type": "object", "properties": {
                    "method": {"type": "string"},
                    "parameters": {"type": "array", "items": {"type": "object", "properties": {
                        "name": {"type": "string"}, "type": {"type": "string"}, "index": {"type": "integer"},
                    }, "additionalProperties": False}},
                }, "required": ["method", "parameters"], "additionalProperties": False}},
                "total": {"type": "integer"},
            },
            "required": ["success"], "additionalProperties": False,
        },
        description="""List parameters of a specific method.

Get detailed information about method parameters including their names,
types, and order.

Returns:
    {
        "success": true,
        "methods": [
            {
                "method": "authenticate",
                "parameters": [
                    {"name": "username", "type": "string", "index": 1},
                    {"name": "password", "type": "string", "index": 2}
                ]
            }
        ]
    }

Notes:
    - Useful for understanding function signatures.

Examples:
    list_parameters(codebase_hash="abc", method_name="login")""",
    )
    @tool_errors("pass an existing method name (see list_methods).")
    def list_parameters(
        codebase_hash: Annotated[str, Field(description="The codebase hash from generate_cpg")],
        method_name: Annotated[str, Field(description="Name of the method (can be regex pattern)")],
    ) -> Dict[str, Any]:
        """Get parameter names, types, and order for a method."""
        code_browsing_service = services["code_browsing_service"]
        return code_browsing_service.list_parameters(
            codebase_hash=codebase_hash,
            method_name=method_name,
        )


    @mcp.tool(
        title="Run CPGQL Query",
        annotations={"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False},
        output_schema={
            "type": "object",
            "properties": {
                "success": {"type": "boolean"}, "data": {},
                "row_count": {"type": ["integer", "null"]},
                "execution_time": {"type": ["number", "null"]},
                "truncated": {"type": "boolean"}, "truncation_note": {"type": "string"},
                "validation": {},
            },
            "required": ["success"], "additionalProperties": False,
        },
        description="""Execute a raw CPGQL query against the codebase.

Run arbitrary Code Property Graph Query Language (CPGQL) queries
for advanced analysis.

Returns:
    {
        "success": true,
        "data": [...],               # parsed JSON rows (or a scalar for .size queries)
        "row_count": 2,
        "execution_time": 0.06,
        "truncated": true,           # only when the server's row/size cap cut results off
        "truncation_note": "...",
        "validation": {...}          # only when validate=true
    }
    On failure the call returns an MCP error whose text starts with a code:
    "QUERY_ERROR: ..." (compile errors include the compiler message and a
    suggestion), "TIMEOUT", "SERVER_BUSY", "SERVER_UNAVAILABLE", or "VALIDATION_ERROR".

Notes:
    - Power user tool. Requires knowledge of Joern CPGQL.
    - Unbounded queries are capped at 10,000 rows; add .take(n) or filters to narrow.
    - Use get_cpgql_syntax_help for reference.

Examples:
    run_cpgql_query(codebase_hash="abc", query="cpg.method.name.l")""",
    )
    @tool_errors("see get_cpgql_syntax_help; narrow the query or add .take(n).")
    def run_cpgql_query(
        codebase_hash: Annotated[str, Field(description="The codebase hash from generate_cpg")],
        query: Annotated[str, Field(description="The CPGQL query string to execute")],
        timeout: Annotated[Optional[int], Field(description="Optional timeout in seconds")] = None,
        validate: Annotated[bool, Field(description="If true, validate query syntax before executing")] = False,
    ) -> Dict[str, Any]:
        """Run a raw CPGQL query for custom CPG analysis."""
        from ..utils.cpgql_validator import CPGQLValidator

        validate_codebase_hash(codebase_hash)

        if not query or not query.strip():
            raise ValidationError("Query cannot be empty")

        # Security blocklist for the raw-query escape hatch. Always enforced
        # (independent of the optional `validate` syntax check below) — this is
        # the one tool that forwards untrusted text to the Joern Scala REPL.
        # Defense-in-depth: the real boundary is the Joern worker sandbox.
        validate_cpgql_query(query.strip())

        query_executor = services["query_executor"]

        codebase_info = require_cpg(services, codebase_hash)

        validation_result = None
        if validate:
            validation_result = CPGQLValidator.validate_query(query.strip())
            if not validation_result['valid'] and validation_result['errors']:
                problems = [
                    f"{e.get('message', e)}" + (f" ({e['suggestion']})" if e.get("suggestion") else "")
                    if isinstance(e, dict) else str(e)
                    for e in validation_result['errors']
                ]
                raise ValidationError("Query validation failed: " + "; ".join(problems))

        # Dataflow queries (reachableByFlows) legitimately need more time.
        # Use a higher default when the caller didn't specify an explicit timeout.
        from ..services.query_executor import _DATAFLOW_PATTERNS, _DATAFLOW_DEFAULT_TIMEOUT
        _effective_timeout = timeout
        if _effective_timeout is None:
            _effective_timeout = (
                _DATAFLOW_DEFAULT_TIMEOUT
                if any(p in query for p in _DATAFLOW_PATTERNS)
                else 30
            )

        result = query_executor.execute_query(
            codebase_hash=codebase_hash,
            cpg_path=codebase_info.cpg_path,
            query=query.strip(),
            timeout=_effective_timeout,
            limit=None,
        )

        if not result.success:
            message = result.error or "Query failed"
            suggestion = CPGQLValidator.get_error_suggestion(message)
            if suggestion and suggestion.get("solution"):
                message += f" Suggestion: {suggestion['solution']}"
            raise tool_error(
                result.error_code or "QUERY_ERROR", message,
                "see get_cpgql_syntax_help; narrow the query or add .take(n).",
            )

        response = {
            "success": True,
            "data": result.data,
            "row_count": result.row_count,
            "execution_time": getattr(result, "execution_time", None),
        }

        if getattr(result, "truncated", False):
            response["truncated"] = True
            response["truncation_note"] = (
                "Results were capped by the server's size limit; refine the query "
                "(filter by filename, add .take(n), or narrow the traversal) to see the rest."
            )

        if validate and validation_result:
            response["validation"] = validation_result
        return response


    @mcp.tool(
        title="Find Bounds Checks",
        annotations={"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False},
        output_schema={"type": "object", "properties": {"success": {"type": "boolean"}, "summary": {"type": "string"}}, "required": ["success"], "additionalProperties": False},
        description="""Find bounds checks near buffer access.

Verify if buffer accesses have corresponding bounds checks by analyzing
comparison operations involving the index variable.

Returns:
    A human-readable text summary of the bounds check analysis.

Notes:
    - Helps identify potential buffer overflow vulnerabilities.
    - Checks for missing bounds checks or checks that happen too late.
    - filename in buffer_access_location should be relative to the project root (e.g., 'src/parser.c:100').

Examples:
    find_bounds_checks(codebase_hash="abc", buffer_access_location="parser.c:3393")""",
    )
    def find_bounds_checks(
        codebase_hash: Annotated[str, Field(description="The codebase hash from generate_cpg")],
        buffer_access_location: Annotated[str, Field(description="Location of buffer access in format 'filename:line' (e.g., 'parser.c:3393')")],
    ) -> Dict[str, Any]:
        """Check if buffer accesses have proper bounds validation."""
        try:
            validate_codebase_hash(codebase_hash)

            if ":" not in buffer_access_location:
                raise ValidationError(
                    "buffer_access_location must be in format 'filename:line'"
                )

            filename, line_str = buffer_access_location.rsplit(":", 1)
            try:
                line_num = int(line_str)
            except ValueError:
                raise ValidationError(f"Invalid line number: {line_str}")

            query_executor = services["query_executor"]

            codebase_info = require_cpg(services, codebase_hash)

            query = QueryLoader.load(
                "bounds_checks",
                filename=filename,
                line_num=line_num
            )

            result = query_executor.execute_query(
                codebase_hash=codebase_hash,
                cpg_path=codebase_info.cpg_path,
                query=query,
                timeout=30,
            )

            if result.success and result.data:
                # Text queries return a single-element list wrapping the rendered text.
                output = result.data[0] if isinstance(result.data, list) else str(result.data)
                return {"success": True, "summary": output.strip()}
            else:
                raise ToolError(f"{result.error_code or 'QUERY_ERROR'}: {result.error if not result.success else 'No data returned'} Hint: verify the CPG is ready and use a relative filename:line location.")

        except ValidationError as e:
            logger.error(f"Error finding bounds checks: {e}")
            raise ToolError(f"VALIDATION_ERROR: {str(e)} Hint: provide buffer_access_location as filename:line.") from e
        except ToolError:
            raise
        except Exception as e:
            logger.error(f"Unexpected error: {e}", exc_info=True)
            raise ToolError(f"INTERNAL_ERROR: {str(e)} Hint: retry after checking CPG/backend status.") from e

    @mcp.tool(
        title="Get CPGQL Syntax Help",
        annotations={"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False},
        output_schema={
            "type": "object",
            "properties": {
                "success": {"type": "boolean"}, "syntax_helpers": {},
                "error_guide": {}, "quick_reference": {},
            },
            "required": ["success"], "additionalProperties": False,
        },
        description="""Get comprehensive CPGQL syntax help and examples.

Provides syntax documentation, common patterns, node types, and error solutions.

Returns:
    {
        "success": true,
        "syntax_helpers": {...},
        "error_guide": {...},
        "quick_reference": {...}
    }

Notes:
    - Use this to learn how to write queries for run_cpgql_query.

Examples:
    get_cpgql_syntax_help()""",
    )
    @tool_errors("retry; this tool needs no CPG.")
    def get_cpgql_syntax_help() -> Dict[str, Any]:
        """Get CPGQL syntax documentation and common query patterns."""
        from ..utils.cpgql_validator import CPGQLValidator

        helpers = CPGQLValidator.get_syntax_helpers()

        return {
            "success": True,
            "syntax_helpers": helpers,
            "error_guide": {
                "common_errors": [
                    {
                        "error": "matches is not a member of Iterator[String]",
                        "cause": "Trying to call .matches() directly on a stream",
                        "solution": "Use .filter() with lambda: .filter(_.property.matches(\"regex\"))",
                        "examples": [
                            "cpg.method.filter(_.name.matches(\"process.*\")).l",
                            "cpg.call.filter(_.code.matches(\".*malloc.*\")).l",
                        ]
                    },
                    {
                        "error": "value contains is not a member",
                        "cause": "Substring matching syntax error",
                        "solution": "Use inside filter lambda: .filter(_.property.contains(\"text\"))",
                        "examples": [
                            "cpg.literal.filter(_.code.contains(\"password\")).l",
                            "cpg.call.filter(_.code.contains(\"system\")).l",
                        ]
                    },
                    {
                        "error": "not found: value _",
                        "cause": "Lambda syntax error or invalid property access",
                        "solution": "Ensure lambda uses underscore: _ (not $, @, or other symbols)",
                        "examples": [
                            "cpg.method.filter(_.name.nonEmpty).l",
                            "cpg.call.where(_.method.name != \"\").l",
                        ]
                    },
                    {
                        "error": "Unmatched closing parenthesis",
                        "cause": "Syntax error - mismatched parentheses",
                        "solution": "Count opening and closing parentheses - they must match",
                        "examples": [
                            "cpg.method.filter(_.name.matches(\"test.*\")).l",
                        ]
                    },
                ],
                "tips": [
                    "Always use .l or .toJsonPretty at the end to get results",
                    "Use .filter(_) or .where(_) with underscore lambda for conditions",
                    "String literals in filter need quotes: filter(_.name == \"value\")",
                    "Regex patterns must be in quotes and escaped: \".*pattern.*\"",
                    "For better performance, filter before calling .l",
                    "Prefer .dedup.l (or aggregate with .mkString(\",\")) over a bare .name.l: "
                    "a huge unfiltered list of strings is heavy to serialize. The server caps "
                    "oversized results in-memory and sets truncated=True with a truncation_note "
                    "(results are NOT written to a file) — narrow the traversal to see the rest.",
                ]
            },
            "quick_reference": {
                "string_methods": {
                    "exact_match": '.name("exactString")',
                    "regex_match": '.filter(_.name.matches("regex.*"))',
                    "substring_match": '.filter(_.code.contains("substring"))',
                    "case_insensitive": '.filter(_.name.toLowerCase.matches("pattern.*"))',
                    "not_empty": '.filter(_.name.nonEmpty)',
                    "equals": '.filter(_.name == "value")',
                    "not_equals": '.filter(_.name != "value")',
                },
                "common_node_properties": {
                    "method": ["name", "filename", "signature", "lineNumber", "isExternal"],
                    "call": ["name", "code", "filename", "lineNumber"],
                    "literal": ["code", "typeFullName", "filename", "lineNumber"],
                    "parameter": ["name", "typeFullName", "index"],
                    "file": ["name", "hash"],
                },
                "result_formatting": {
                    "json_pretty": '.toJsonPretty  # Pretty-printed JSON',
                    "json_compact": '.toJson  # Compact JSON',
                    "list": '.l  # Scala list (automatically formatted)',
                    "count": '.size  # Get count as number',
                    "single_item": '.head  # Get first result',
                    "optional": '.headOption  # Get optional first result',
                }
            }
        }

    # Semantic analysis tools

    @mcp.tool(
        title="Get Control Flow Graph",
        annotations={"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False},
        output_schema={"type": "object", "properties": {"success": {"type": "boolean"}, "summary": {"type": "string"}}, "required": ["success"], "additionalProperties": False},
        description="""Get control flow graph (CFG) for a method.

Understand the control flow of a method with a human-readable graph.

Returns:
    A human-readable text graph:
    
    Control Flow Graph for main
    ============================================================
    Nodes:
      [1001] ControlStructure: if (x > 0)
      [1002] Return: return x
    
    Edges:
      [1001] -> [1002] [Label: TRUE]

Notes:
    - Essential for understanding loops, conditions, and execution paths.
    - Returns plain text.
    - If method_name matches several methods, the summary lists up to 10
      candidates with their method_id; re-run with one.

Examples:
    get_cfg(codebase_hash="abc", method_name="main")
    get_cfg(codebase_hash="abc", method_id="12345")""",
    )
    def get_cfg(
        codebase_hash: Annotated[str, Field(description="The codebase hash from generate_cpg")],
        method_name: Annotated[Optional[str], Field(description="Name of the method (regex; must match one method)")] = None,
        limit: Annotated[int, Field(description="Maximum CFG nodes to return (for large methods)", ge=1, le=MAX_RESULT_ROWS)] = 100,
        method_id: Annotated[Optional[str], Field(description="Exact method node_id from list_methods; overrides method_name")] = None,
    ) -> Dict[str, Any]:
        """Get nodes and edges representing control flow in a method."""
        try:
            validate_codebase_hash(codebase_hash)
            method_name, node_id = _resolve_method_selector(method_name, method_id)
            query_executor = services["query_executor"]

            codebase_info = require_cpg(services, codebase_hash)

            query = QueryLoader.load(
                "cfg",
                method_name=method_name,
                method_id=node_id,
                max_nodes=limit
            )

            result = query_executor.execute_query(
                codebase_hash=codebase_hash,
                cpg_path=codebase_info.cpg_path,
                query=query,
                timeout=30,
                limit=limit,
            )

            if not result.success:
                raise ToolError(f"{result.error_code or 'QUERY_ERROR'}: {result.error or 'CFG query failed'} Hint: verify the CPG is ready and lower limit for large methods.")
            return {"success": True, "summary": unwrap_result(result)}

        except ValidationError as e:
            logger.error(f"Error getting CFG: {e}")
            raise ToolError(f"VALIDATION_ERROR: {str(e)} Hint: provide a valid codebase hash and a method_name or numeric method_id.") from e
        except ToolError:
            raise
        except Exception as e:
            logger.error(f"Unexpected error getting CFG: {e}", exc_info=True)
            raise ToolError(f"INTERNAL_ERROR: {str(e)} Hint: retry after checking CPG/backend status.") from e


    @mcp.tool(
        title="Diff Two CPGs",
        annotations={"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False},
        output_schema={
            "type": "object",
            "properties": {
                "success": {"type": "boolean"},
                "base": {"type": "object"}, "head": {"type": "object"},
                "warnings": {"type": "array", "items": {"type": "string"}},
                "summary": {"type": "object", "properties": {
                    "added": {"type": "integer"}, "removed": {"type": "integer"},
                    "modified": {"type": "integer"}, "unchanged": {"type": "integer"},
                }, "additionalProperties": False},
                "changes": {"type": "array", "items": {"type": "object"}},
                "total": {"type": "integer"}, "returned": {"type": "integer"},
                "page": {"type": "integer"}, "page_size": {"type": "integer"},
                "total_pages": {"type": "integer"}, "truncated": {"type": "boolean"},
            },
            "required": ["success", "summary", "changes", "total", "truncated"],
            "additionalProperties": False,
        },
        description="""Compare two CPGs method by method, e.g. two commits or two builds of a project.

Build both with generate_cpg (commit=<sha> pins a revision), then pass their hashes.
Each method is fingerprinted from its signature and its own code, without line
numbers: a method that only moved is unchanged, and modified methods report
whether the signature changed. Warnings flag differences that are not source
changes (language, build options, Joern version, unknown provenance).

Returns:
    {"success": true, "base": {"codebase_hash", "label", "commit"}, "head": {...},
     "warnings": [...], "summary": {"added": 0, "removed": 0, "modified": 1, "unchanged": 40},
     "changes": [{"change": "modified", "filename": "kilo.c", "name": "editorDelChar",
                  "signature": "void(void)", "base_signature": "void()",
                  "signature_changed": true, "base_line": 761, "head_line": 761}],
     "total": 1, "returned": 1, "page": 1, "page_size": 50, "total_pages": 1, "truncated": false}

Example:
    diff_cpgs(base_codebase_hash="895d1632702eb6da", head_codebase_hash="0eab029efd1a328e")""",
    )
    @tool_errors("build both CPGs with generate_cpg (poll get_cpg_status until ready) and pass their hashes.")
    def diff_cpgs(
        base_codebase_hash: Annotated[str, Field(description="The older CPG (e.g. the base commit)")],
        head_codebase_hash: Annotated[str, Field(description="The newer CPG (e.g. the head commit)")],
        change: Annotated[Optional[Literal["added", "removed", "modified"]], Field(description="Only list this kind of change")] = None,
        file_pattern: Annotated[Optional[str], Field(description="Only list changes in files matching this regex")] = None,
        page: Annotated[int, Field(description="Page number (1-based)", ge=1)] = 1,
        page_size: Annotated[int, Field(description="Changes per page", ge=1, le=500)] = 50,
    ) -> Dict[str, Any]:
        """Method-level diff between two CPGs."""
        from ..services.cpg_diff import comparability_warnings, diff_methods, fetch_fingerprints
        from .core_tools import _codebase_label

        if base_codebase_hash == head_codebase_hash:
            raise ValidationError("base and head are the same CPG")
        service = services["code_browsing_service"]
        sides = {}
        for role, codebase_hash in (("base", base_codebase_hash), ("head", head_codebase_hash)):
            info = require_cpg(services, codebase_hash)
            fetched = fetch_fingerprints(service, codebase_hash, info.cpg_path)
            if not fetched.get("success"):
                return fetched
            sides[role] = (info, fetched["methods"])
        try:
            pattern = re.compile(file_pattern) if file_pattern else None
        except re.error as e:
            raise ValidationError(f"file_pattern is not a valid regex: {e}")

        (base_info, base_methods), (head_info, head_methods) = sides["base"], sides["head"]
        result = diff_methods(base_methods, head_methods)
        changes = [
            c for c in result["changes"]
            if (not change or c["change"] == change)
            and (not pattern or pattern.search(c["filename"] or ""))
        ]
        start = (page - 1) * page_size
        page_changes = changes[start:start + page_size]

        def side(info):
            meta = info.metadata or {}
            return {"codebase_hash": info.codebase_hash, "label": _codebase_label(info),
                    "commit": (meta.get("provenance") or {}).get("commit"), "language": info.language}

        return {
            "success": True,
            "base": side(base_info),
            "head": side(head_info),
            "warnings": comparability_warnings(
                base_info.metadata or {}, head_info.metadata or {}, base_info.language, head_info.language,
            ),
            "summary": result["summary"],
            "changes": page_changes,
            "total": len(changes),
            "returned": len(page_changes),
            "page": page,
            "page_size": page_size,
            "total_pages": max(1, (len(changes) + page_size - 1) // page_size),
            "truncated": start + len(page_changes) < len(changes),
        }

    @mcp.tool(
        title="Get Type Definition",
        annotations={"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False},
        output_schema={
            "type": "object",
            "properties": {
                "success": {"type": "boolean"},
                "types": {"type": "array", "items": {"type": "object", "properties": {
                    "name": {"type": ["string", "null"]}, "fullName": {"type": ["string", "null"]},
                    "filename": {"type": ["string", "null"]}, "lineNumber": {"type": ["integer", "null"]},
                    "members": {"type": "array", "items": {"type": "object", "additionalProperties": True}},
                }, "additionalProperties": False}},
                "total": {"type": "integer"},
            },
            "required": ["success"], "additionalProperties": False,
        },
        description="""Get type/struct definition with members.

Inspect struct or class memory layouts.

Returns:
    {
        "success": true,
        "types": [
            {
                "name": "UserStruct",
                "members": [{"name": "id", "type": "int"}, {"name": "buf", "type": "char*"}]
            }
        ]
    }

Notes:
    - Essential for understanding buffer sizes and memory layouts.
    - Does not read header files; uses CPG type info.
    - Joern's internal `<duplicate>N` variants are collapsed into the canonical type.

Examples:
    get_type_definition(codebase_hash="abc", type_name=".*request_t.*")""",
    )
    @tool_errors("verify the CPG is ready and check the type_name regex.")
    def get_type_definition(
        codebase_hash: Annotated[str, Field(description="The codebase hash from generate_cpg")],
        type_name: Annotated[str, Field(description="Type name pattern (regex, e.g., '.*Buffer.*')")],
        limit: Annotated[int, Field(description="Maximum types to return", ge=1, le=MAX_RESULT_ROWS)] = 10,
    ) -> Dict[str, Any]:
        """Get struct/class definition with member names and types."""
        validate_codebase_hash(codebase_hash)
        query_executor = services["query_executor"]

        codebase_info = require_cpg(services, codebase_hash)

        query = QueryLoader.load(
            "type_definition",
            type_name=type_name,
            limit=limit
        )

        result = query_executor.execute_query(
            codebase_hash=codebase_hash,
            cpg_path=codebase_info.cpg_path,
            query=query,
            timeout=30,
            limit=limit,
        )

        if not result.success:
            return {
                "success": False,
                "error": result.error,
                "error_code": result.error_code or "QUERY_ERROR",
            }

        types = []
        if result.data:
            for item in result.data:
                if isinstance(item, dict):
                    types.append({
                        "name": item.get("_1"),
                        "fullName": item.get("_2"),
                        "filename": item.get("_3"),
                        "lineNumber": item.get("_4"),
                        "members": item.get("_5", []),
                    })

        types = _deduplicate_type_definitions(types)

        return {
            "success": True,
            "types": types,
            "total": len(types),
        }
