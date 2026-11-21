"""
Code Browsing MCP Tools for CodeBadger Server
Tools for exploring and navigating codebase structure
"""

import json
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
from ._common import parse_json_result as _parse_json_result, require_cpg, tool_error, tool_errors, unwrap_result

logger = logging.getLogger(__name__)

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


def _raise_for_selection(graph: dict, method_name: str, node_id: int) -> None:
    """Turn a template's not_found/ambiguous method status into a ValidationError."""
    status = graph.get("status")
    if status == "not_found":
        what = f"method_id {node_id}" if node_id >= 0 else f"method_name '{method_name}'"
        similar = graph.get("suggestions") or []
        extra = f" Similar names: {', '.join(similar)}." if similar else ""
        raise ValidationError(f"no method matches {what}.{extra}")
    if status == "ambiguous":
        choices = "; ".join(
            f"method_id={c['node_id']} {c['full_name']} ({c['filename']}:{c['line']})"
            for c in graph.get("candidates", [])
        )
        raise ValidationError(
            f"method_name '{method_name}' matches {graph.get('count')} methods; "
            f"re-run with method_id set to one of: {choices}"
        )


def _call_graph_response(graph: dict) -> Dict[str, Any]:
    """Structured call graph plus the text rendering existing clients read."""
    root = graph.get("root") or {}
    edges = graph.get("edges") or []
    direction = graph.get("direction", "outgoing")
    capped = graph.get("capped_methods") or []
    address_taken = graph.get("address_taken_sites") or []
    warnings = list(graph.get("warnings") or [])
    recursive = sum(1 for e in edges if e.get("recursive"))

    lines = [f"Call Graph for {root.get('name')} ({direction})", "=" * 60,
             f"Root: {root.get('name')} at {root.get('filename')}:{root.get('line')} (method_id={root.get('node_id')})"]
    lines += [f"WARNING: {w}" for w in warnings]
    for depth in sorted({e["depth"] for e in edges}):
        lines.append(f"\n[DEPTH {depth}]")
        for e in (e for e in edges if e["depth"] == depth):
            sites = e.get("call_sites") or []
            at = ", ".join(f"{c['filename']}:{c['line']}" for c in sites[:3]) or "unresolved"
            if len(sites) > 3:
                at += f" +{len(sites) - 3} more"
            d = e.get("definition") or {}
            tag = " [RECURSIVE]" if e.get("recursive") else ""
            lines.append(f"  {e['from']} → {e['to']} (call at {at}; {e['to']} defined at "
                         f"{d.get('filename')}:{d.get('line')}){tag}")
    lines.append(f"\nTotal: {len(edges)} edges" + (f" ({recursive} recursive)" if recursive else ""))
    for c in capped:
        lines.append(f"CAPPED: {c['name']} (method_id={c['method_id']}) has {c['total']} "
                     f"{'callees' if direction == 'outgoing' else 'callers'}; {c['shown']} shown.")
    if graph.get("edge_cap_hit"):
        lines.append(f"TRUNCATED: stopped at {graph.get('edge_cap')} edges; lower depth or start deeper.")
    if address_taken:
        lines.append(f"\nNo direct callers (likely indirect/callback dispatch). {len(address_taken)} site(s) "
                     "take this method's address:")
        lines += [f"  {a['method']} ({a['filename']}:{a['line']})" for a in address_taken]

    return {
        "success": True,
        "root": root,
        "direction": direction,
        "edges": edges,
        "total": len(edges),
        "returned": len(edges),
        "recursive_edges": recursive,
        "truncated": bool(graph.get("truncated")),
        "capped_methods": capped,
        "address_taken_sites": address_taken,
        "warnings": warnings,
        "summary": "\n".join(lines),
    }


def _bounds_response(data: dict, location: str) -> Dict[str, Any]:
    """Structured bounds-check result plus the text rendering."""
    target = data.get("target") or {}
    guards = data.get("guards") or []
    caller_guards = data.get("caller_guards") or []
    status = data.get("status", "no_guard_found")
    lines = [f"Bounds Check Analysis for {location}", "=" * 60]
    if target.get("kind") == "buffer_access":
        lines += [f"Target:   {target.get('code')} (buffer {target.get('buffer')}, index {target.get('index')})"]
    else:
        lines += [f"Target:   {target.get('code')} ({target.get('label', 'sized call')}, size {target.get('size')})"]
    if status == "no_variable":
        lines.append("No variable in the index/size argument; nothing to check (constant).")
    else:
        unsigned = " (unsigned)" if target.get("variable_unsigned") else ""
        lines.append(f"Variable: {target.get('variable')}{unsigned} in {target.get('method')}()")
        lines.append("\nGUARDS")
        for g in guards:
            lines.append(f"  line {g['line']}: {g['condition']}  [{g['kind']}, {g['branch']} branch] "
                         f"=> {', '.join(g['establishes'])}")
        if not guards:
            lines.append("  none controlling this line")
        if caller_guards:
            lines.append("\nCALLER GUARDS")
            for g in caller_guards:
                lines.append(f"  {g['caller']}() line {g['line']}: {g['condition']}  [{g['branch']} branch] "
                             f"=> {', '.join(g['establishes'])}")
        lines.append(f"\nLower bound: {'yes' if data.get('lower_bound') else 'no'}"
                     + ("" if data.get("lower_bound_required", True) else " (not required for a size)")
                     + f" | Upper bound: {'yes' if data.get('upper_bound') else 'no'}")
    lines.append(f"Status: {status.upper()}")
    return {
        "success": True,
        "status": status,
        "target": target,
        "guards": guards,
        "caller_guards": caller_guards,
        "lower_bound": bool(data.get("lower_bound")),
        "upper_bound": bool(data.get("upper_bound")),
        "summary": "\n".join(lines),
    }


def _cfg_response(graph: dict) -> Dict[str, Any]:
    """Structured CFG result plus the text rendering existing clients read."""
    method = graph.get("method") or {}
    nodes = graph.get("nodes") or []
    edges = graph.get("edges") or []
    total, total_edges = int(graph.get("total_nodes", len(nodes))), int(graph.get("total_edges", len(edges)))
    truncated = len(nodes) < total

    lines = [f"Control Flow Graph for {method.get('name')} ({method.get('filename')}:{method.get('line')})", "=" * 60]
    lines.append(f"Nodes ({len(nodes)} of {total}):")
    for n in nodes:
        lines.append(f"  [{n['node_id']}] {n['kind']} line {n['line']}: {n['code']}")
    lines.append(f"\nEdges ({len(edges)} of {total_edges}):")
    for e in edges:
        lines.append(f"  [{e['from']}] -> [{e['to']}]" + (" (branch)" if e.get("branch") else ""))
    if not edges:
        lines.append("  (none among the returned nodes)" if total_edges else "  (the method has no control flow edges)")
    if truncated:
        lines.append(f"\nTRUNCATED: showing {len(nodes)} of {total} nodes and {len(edges)} of {total_edges} edges. Raise limit.")

    return {
        "success": True,
        "method": method,
        "nodes": nodes,
        "edges": edges,
        "total": total,
        "returned": len(nodes),
        "total_edges": total_edges,
        "returned_edges": len(edges),
        "truncated": truncated,
        "summary": "\n".join(lines),
    }


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
                "hint": {"type": "string"},
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
    - total counts every match; pages cover the first `limit` matches. When
      matches are hidden, `hint` says whether to request the next page or raise
      limit.
    - cyclomaticComplexity is McCabe's number over the method CFG: 1 + the extra
      exits of every branching node. Each if, loop, ?:, &&, || and switch arm
      adds a path (a three-arm switch with default is 3; `n ? a : b` is 2).

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
                            "lineNumber": {"type": "integer"}, "columnNumber": {"type": "integer"},
                            "node_id": {"type": "string"}, "caller_id": {"type": "string"},
                        },
                        "additionalProperties": False,
                    },
                },
                "total": {"type": "integer"}, "returned": {"type": "integer"},
                "truncated": {"type": "boolean"}, "page": {"type": "integer"},
                "page_size": {"type": "integer"}, "total_pages": {"type": "integer"},
                "hint": {"type": "string"},
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
            {"caller": "main", "callee": "printf", "code": "printf(\"%d\", n)",
             "filename": "main.c", "lineNumber": 10, "columnNumber": 5,
             "node_id": "30064771081", "caller_id": "107374182400"}
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
    - One row per call site: identical calls on one line are separate rows,
      told apart by columnNumber and node_id. Pass node_id to tools that take a
      call-site ID (find_taint_flows source_node_id/sink_node_id); caller_id is
      the enclosing method's node_id (get_cfg, get_call_graph method_id).
    - When matches are hidden, `hint` says whether to page on or raise limit.

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
        output_schema={
            "type": "object",
            "properties": {
                "success": {"type": "boolean"},
                "root": {"type": "object"},
                "direction": {"type": "string"},
                "edges": {"type": "array", "items": {"type": "object"}},
                "total": {"type": "integer"}, "returned": {"type": "integer"},
                "recursive_edges": {"type": "integer"},
                "truncated": {"type": "boolean"},
                "capped_methods": {"type": "array", "items": {"type": "object"}},
                "address_taken_sites": {"type": "array", "items": {"type": "object"}},
                "warnings": {"type": "array", "items": {"type": "string"}},
                "summary": {"type": "string"},
            },
            "required": ["success", "root", "edges", "total", "truncated", "summary"],
            "additionalProperties": False,
        },
        description="""Get the call graph of one method: what it calls (outgoing) or what calls it (incoming).

Returns:
    {"success": true,
     "root": {"node_id", "name", "full_name", "filename", "line", "external"},
     "direction": "outgoing",
     "edges": [{"from_id", "from", "to_id", "to", "depth",
                "call_sites": [{"node_id", "filename", "line", "column"}],
                "definition": {"filename", "line"},
                "recursive": false}],
     "total": 3, "returned": 3, "recursive_edges": 0, "truncated": false,
     "capped_methods": [], "address_taken_sites": [], "warnings": [],
     "summary": "<the same graph as text>"}

Notes:
    - Edges always point caller -> callee. call_sites are where the call is made
      (in the caller); definition is where the callee is defined.
    - recursive=true only when the callee reaches the caller again through the
      edges found (recursion or mutual recursion), not when a callee is shared by
      several callers.
    - Each method lists at most 50 neighbours and the graph at most 500 edges;
      capped_methods names methods whose neighbours were cut, and truncated=true
      flags either cap. Depth is limited by the depth argument.
    - Incoming with no callers lists address_taken_sites: places the method is
      used as a function pointer or callback (likely indirect callers).
    - An unknown method, or a method_name matching several methods, is a
      VALIDATION_ERROR; the ambiguous case lists each candidate's method_id.

Examples:
    get_call_graph(codebase_hash="abc", method_name="main", direction="outgoing")
    get_call_graph(codebase_hash="abc", method_id="12345", direction="incoming")""",
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
        hint = "pass method_name or a numeric method_id, check direction, and keep depth between 1 and 15."
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
            )
            graph = _parse_json_result(unwrap_result(result))
            _raise_for_selection(graph, method_name, node_id)
            response = _call_graph_response(graph)
            if detail == "compact" and len(response["summary"]) > 2000:
                response["summary"] = response["summary"][:2000].rstrip() + "\n...[truncated; use detail='full']"
            return response

        except ValidationError as e:
            logger.error(f"Error getting call graph: {e}")
            raise tool_error("VALIDATION_ERROR", str(e), hint) from e
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
                    "method": {"type": "string"}, "node_id": {"type": "string"},
                    "fullName": {"type": "string"}, "owner": {"type": "string"},
                    "filename": {"type": "string"}, "lineNumber": {"type": "integer"},
                    "parameters": {"type": "array", "items": {"type": "object", "properties": {
                        "name": {"type": "string"}, "type": {"type": "string"}, "index": {"type": "integer"},
                    }, "additionalProperties": False}},
                }, "required": ["method", "parameters"], "additionalProperties": False}},
                "total": {"type": "integer"}, "returned": {"type": "integer"},
                "truncated": {"type": "boolean"}, "hint": {"type": "string"},
            },
            "required": ["success"], "additionalProperties": False,
        },
        description="""List the parameters of a method: names, types and order.

Returns:
    {
        "success": true,
        "methods": [
            {
                "method": "size", "node_id": "107374182402",
                "fullName": "model.py:<module>.A.size", "owner": "model.py:<module>.A",
                "filename": "model.py", "lineNumber": 2,
                "parameters": [
                    {"name": "self", "type": "ANY", "index": 0},
                    {"name": "width", "type": "ANY", "index": 1}
                ]
            }
        ],
        "total": 1, "returned": 1, "truncated": false
    }

Notes:
    - method_id (the node_id from list_methods) selects exactly one method and
      overrides method_name; a name regex may match several methods, each
      identified by node_id, fullName and owner (the defining class; empty for
      a free function).
    - No match is an empty list.

Examples:
    list_parameters(codebase_hash="abc", method_name="login")
    list_parameters(codebase_hash="abc", method_id="107374182402")""",
    )
    @tool_errors("pass an existing method name or the node_id from list_methods as method_id.")
    def list_parameters(
        codebase_hash: Annotated[str, Field(description="The codebase hash from generate_cpg")],
        method_name: Annotated[Optional[str], Field(description="Name of the method (regex)")] = None,
        method_id: Annotated[Optional[str], Field(description="Exact method node_id from list_methods; overrides method_name")] = None,
        limit: Annotated[int, Field(description="Maximum methods to return", ge=1, le=MAX_RESULT_ROWS)] = 100,
    ) -> Dict[str, Any]:
        """Get parameter names, types, and order for a method."""
        name, node_id = _resolve_method_selector(method_name, method_id)
        code_browsing_service = services["code_browsing_service"]
        return code_browsing_service.list_parameters(
            codebase_hash=codebase_hash,
            method_name=name,
            method_id=node_id,
            limit=limit,
        )


    @mcp.tool(
        title="Resolve Location",
        annotations={"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False},
        output_schema={
            "type": "object",
            "properties": {
                "success": {"type": "boolean"},
                "candidates": {"type": "array", "items": {"type": "object", "properties": {
                    "node_id": {"type": "string"}, "kind": {"type": "string"}, "name": {"type": "string"},
                    "code": {"type": "string"}, "filename": {"type": "string"},
                    "line": {"type": "integer"}, "column": {"type": "integer"},
                    "operator": {"type": "boolean"}, "method": {"type": "string"},
                    "method_id": {"type": "string"}, "method_full_name": {"type": "string"},
                }, "additionalProperties": False}},
                "total": {"type": "integer"}, "returned": {"type": "integer"},
                "truncated": {"type": "boolean"}, "hint": {"type": "string"},
            },
            "required": ["success", "candidates", "total", "truncated"], "additionalProperties": False,
        },
        description="""Turn a source location into graph nodes with IDs.

Given 'file:line' or 'file:line:column', list the nodes there with node_id,
kind, code, column and enclosing method. Use it to pick one of several calls on
a line, e.g. two identical show_value(9) calls told apart by column.

Returns:
    {"success": true,
     "candidates": [{"node_id": "30064771087", "kind": "CALL", "name": "show_value",
                     "code": "show_value(9)", "filename": "audit.c", "line": 23,
                     "column": 20, "operator": false, "method": "twice",
                     "method_id": "107374182406", "method_full_name": "twice"}],
     "total": 2, "returned": 2, "truncated": false}

Notes:
    - Default: calls on the line, real calls before operators (`x = ...`, `a < b`).
      include_all=true adds identifiers, literals, returns and control structures.
    - With a column, only nodes whose text covers it are listed, innermost first.
    - Pass node_id to find_taint_flows (source_node_id / sink_node_id); pass
      method_id to get_cfg, get_call_graph or list_parameters.
    - An unknown file is a VALIDATION_ERROR; a known line with no nodes
      (blank, comment, preprocessed out) returns no candidates and a hint.

Examples:
    resolve_location(codebase_hash="abc", location="audit.c:23")
    resolve_location(codebase_hash="abc", location="audit.c:23:35")""",
    )
    @tool_errors("use 'file:line' or 'file:line:column' with a file in this CPG.")
    def resolve_location(
        codebase_hash: Annotated[str, Field(description="The codebase hash from generate_cpg")],
        location: Annotated[str, Field(description="'file:line' or 'file:line:column' (file relative to the project root)")],
        include_all: Annotated[bool, Field(description="Also list identifiers, literals, returns and control structures")] = False,
        limit: Annotated[int, Field(description="Maximum candidates to return", ge=1, le=MAX_RESULT_ROWS)] = 50,
    ) -> Dict[str, Any]:
        """Resolve file:line[:column] to node IDs."""
        validate_codebase_hash(codebase_hash)
        parts = location.rsplit(":", 2)
        if len(parts) == 3 and parts[1].isdigit() and parts[2].isdigit():
            filename, line, column = parts[0], int(parts[1]), int(parts[2])
        else:
            parts = location.rsplit(":", 1)
            if len(parts) != 2 or not parts[1].isdigit():
                raise ValidationError(f"location must be 'file:line' or 'file:line:column', got '{location}'")
            filename, line, column = parts[0], int(parts[1]), -1
        if not filename or line < 1:
            raise ValidationError(f"location must name a file and a line >= 1, got '{location}'")

        codebase_info = require_cpg(services, codebase_hash)
        query = QueryLoader.load("resolve_location", filename=filename, line_num=line, column=column,
                                 include_all=include_all, max_results=limit)
        result = services["query_executor"].execute_query(
            codebase_hash=codebase_hash, cpg_path=codebase_info.cpg_path, query=query, timeout=30,
        )
        data = _parse_json_result(unwrap_result(result))
        if not data.get("files"):
            raise ValidationError(f"no file in this CPG matches '{filename}'")
        candidates = data.get("candidates") or []
        total = int(data.get("total", len(candidates)))
        response = {
            "success": True,
            "candidates": candidates,
            "total": total,
            "returned": len(candidates),
            "truncated": len(candidates) < total,
        }
        if total == 0:
            response["hint"] = (
                f"no {'node covers column ' + str(column) + ' on' if column > 0 else 'call on'} line {line}; "
                + ("drop the column to list the line" if column > 0 else
                   "try include_all=true, a nearby line, or check that the code was compiled into the CPG")
            )
        elif len(candidates) < total:
            response["hint"] = f"{total} candidates; raise limit or add a column to narrow them"
        return response

    @mcp.tool(
        title="Find Literals",
        annotations={"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False},
        output_schema={
            "type": "object",
            "properties": {
                "success": {"type": "boolean"},
                "literals": {"type": "array", "items": {"type": "object", "properties": {
                    "value": {"type": "string"}, "type": {"type": "string"},
                    "filename": {"type": "string"}, "lineNumber": {"type": "integer"},
                    "columnNumber": {"type": "integer"}, "node_id": {"type": "string"},
                    "method": {"type": "string"}, "method_id": {"type": "string"},
                    "method_full_name": {"type": "string"},
                }, "additionalProperties": False}},
                "total": {"type": "integer"}, "returned": {"type": "integer"},
                "truncated": {"type": "boolean"}, "hint": {"type": "string"},
            },
            "required": ["success"], "additionalProperties": False,
        },
        description="""Find string and numeric literals, e.g. to locate where an error message is produced.

match="contains" (default) finds `text` anywhere in the literal and "exact" the
whole literal (ignoring a string's quotes); both treat punctuation literally, so
`[retry]` means the brackets. match="regex" uses `text` as a Java regex over the
literal's code (quotes included for strings).

Returns:
    {"success": true,
     "literals": [{"value": "\"[retry] giving up\"", "type": "char[18]",
                   "filename": "net.c", "lineNumber": 42, "columnNumber": 12,
                   "node_id": "90194313221", "method": "reconnect",
                   "method_id": "107374182410", "method_full_name": "reconnect"}],
     "total": 1, "returned": 1, "truncated": false}

Notes:
    - Follow up with get_call_graph(method_id=...) or get_cfg(method_id=...).
    - literal_type filters by type name (e.g. "char", "int"); file_pattern is a
      regex over file paths.

Examples:
    find_literals(codebase_hash="abc", text="[retry]")
    find_literals(codebase_hash="abc", text="0xdeadbeef", match="exact")""",
    )
    @tool_errors("check text, match ('contains', 'exact' or 'regex') and the regex filters.")
    def find_literals(
        codebase_hash: Annotated[str, Field(description="The codebase hash from generate_cpg")],
        text: Annotated[str, Field(description="Text to look for in literals (punctuation is literal unless match='regex')", min_length=1)],
        match: Annotated[Literal["contains", "exact", "regex"], Field(description="How text is matched")] = "contains",
        literal_type: Annotated[Optional[str], Field(description="Optional type name filter, e.g. 'char' or 'int'")] = None,
        file_pattern: Annotated[Optional[str], Field(description="Optional regex to filter by file path")] = None,
        limit: Annotated[int, Field(description="Maximum literals to return", ge=1, le=MAX_RESULT_ROWS)] = 50,
    ) -> Dict[str, Any]:
        """Locate literals with their enclosing method IDs."""
        return services["code_browsing_service"].find_literals(
            codebase_hash=codebase_hash, text=text, match=match,
            literal_type=literal_type, file_pattern=file_pattern, limit=limit,
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
        output_schema={
            "type": "object",
            "properties": {
                "success": {"type": "boolean"},
                "status": {"type": "string", "enum": ["guarded", "partially_guarded", "no_guard_found", "no_variable"]},
                "target": {"type": "object"},
                "guards": {"type": "array", "items": {"type": "object"}},
                "caller_guards": {"type": "array", "items": {"type": "object"}},
                "lower_bound": {"type": "boolean"}, "upper_bound": {"type": "boolean"},
                "summary": {"type": "string"},
            },
            "required": ["success", "status", "summary"],
            "additionalProperties": False,
        },
        description="""Check whether a buffer access or sized call is guarded by a bounds check.

At filename:line, the target is the first `buf[i]` access, else the first sized
call (memcpy, strncpy, snprintf, malloc, read, ...). The checked variable is the
first identifier in the index or size argument.

Returns:
    {"success": true, "status": "guarded",
     "target": {"kind": "buffer_access", "code": "items[i]", "buffer": "items",
                "index": "i", "variable": "i", "variable_unsigned": false, ...},
     "guards": [{"line": 20, "condition": "i < 0 || i >= size", "branch": "false",
                 "kind": "early_return", "establishes": ["i >= 0", "i < size"]}],
     "caller_guards": [], "lower_bound": true, "upper_bound": true,
     "summary": "<the same as text>"}

Notes:
    - A guard is a condition that controls the target, together with the branch
      that reaches it: the true or false arm of an if, a loop body, or the code
      after an early return. `establishes` is what that branch proves.
    - status: guarded = upper bound (and, for an index, lower bound or an
      unsigned index) established; partially_guarded = some check found;
      no_guard_found = no controlling check on the variable in this method or its
      callers. It is not a proof of a bug: checks inside called helpers and
      relations to the buffer's real length are not modelled.
    - A line with no buffer access or sized call is a VALIDATION_ERROR.

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
            data = _parse_json_result(unwrap_result(result))
            if data.get("status") == "unsupported":
                calls = data.get("calls_on_line") or []
                seen = f" Calls on that line: {', '.join(calls)}." if calls else ""
                raise ValidationError(
                    f"no buffer access (buf[i]) or sized call at {buffer_access_location}.{seen}"
                )
            return _bounds_response(data, buffer_access_location)

        except ValidationError as e:
            logger.error(f"Error finding bounds checks: {e}")
            raise ToolError(f"VALIDATION_ERROR: {str(e)} Hint: point buffer_access_location at a line with buf[i] or a sized call such as memcpy.") from e
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
                            "cpg.call.filter(_.method.name.nonEmpty).l",
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
                    "count": '.size  # Count: data is a number (e.g. 2)',
                    "single_item": '.head  # First result as a value (e.g. a node ID); errors when empty',
                    "optional": '.headOption  # First result as a list of 0 or 1 items; safe when empty',
                }
            }
        }

    # Semantic analysis tools

    @mcp.tool(
        title="Get Control Flow Graph",
        annotations={"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False},
        output_schema={
            "type": "object",
            "properties": {
                "success": {"type": "boolean"},
                "method": {"type": "object"},
                "nodes": {"type": "array", "items": {"type": "object"}},
                "edges": {"type": "array", "items": {"type": "object"}},
                "total": {"type": "integer"}, "returned": {"type": "integer"},
                "total_edges": {"type": "integer"}, "returned_edges": {"type": "integer"},
                "truncated": {"type": "boolean"},
                "summary": {"type": "string"},
            },
            "required": ["success", "method", "nodes", "edges", "total", "returned", "truncated", "summary"],
            "additionalProperties": False,
        },
        description="""Get the control flow graph (CFG) of one method.

Nodes are listed breadth-first from the method entry, so a capped view is the
connected start of the graph. AST-only nodes (blocks, the if/while statement
itself) are not CFG nodes; their conditions and bodies are.

Returns:
    {"success": true,
     "method": {"node_id", "name", "full_name", "filename", "line"},
     "nodes": [{"node_id", "kind", "code", "line", "out_degree"}],
     "edges": [{"from", "to", "branch"}],
     "total": 16, "returned": 16, "total_edges": 17, "returned_edges": 17,
     "truncated": false, "summary": "<the same graph as text>"}

Notes:
    - total/total_edges count the whole method; edges lists only edges between
      returned nodes. A node whose out_degree exceeds its listed edges continues
      past the limit. branch=true marks the edges leaving a node with several
      successors (a condition).
    - An unknown method, or a method_name matching several methods, is a
      VALIDATION_ERROR; the ambiguous case lists each candidate's method_id.

Examples:
    get_cfg(codebase_hash="abc", method_name="main")
    get_cfg(codebase_hash="abc", method_id="12345", limit=500)""",
    )
    def get_cfg(
        codebase_hash: Annotated[str, Field(description="The codebase hash from generate_cpg")],
        method_name: Annotated[Optional[str], Field(description="Name of the method (regex; must match one method)")] = None,
        limit: Annotated[int, Field(description="Maximum CFG nodes to return (for large methods)", ge=1, le=MAX_RESULT_ROWS)] = 100,
        method_id: Annotated[Optional[str], Field(description="Exact method node_id from list_methods; overrides method_name")] = None,
    ) -> Dict[str, Any]:
        """Get nodes and edges representing control flow in a method."""
        hint = "provide a valid codebase hash and a method_name or numeric method_id."
        try:
            validate_codebase_hash(codebase_hash)
            method_name, node_id = _resolve_method_selector(method_name, method_id)
            query_executor = services["query_executor"]

            codebase_info = require_cpg(services, codebase_hash)

            query = QueryLoader.load("cfg", method_name=method_name, method_id=node_id, max_nodes=limit)
            result = query_executor.execute_query(
                codebase_hash=codebase_hash,
                cpg_path=codebase_info.cpg_path,
                query=query,
                timeout=30,
            )
            graph = _parse_json_result(unwrap_result(result))
            _raise_for_selection(graph, method_name, node_id)
            return _cfg_response(graph)

        except ValidationError as e:
            raise tool_error("VALIDATION_ERROR", str(e), hint) from e
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
                    "aliasOf": {"type": ["string", "null"]},
                    "forward_declaration": {"type": "boolean"},
                    "member_total": {"type": "integer"},
                    "members_truncated": {"type": "boolean"},
                    "members": {"type": "array", "items": {"type": "object", "additionalProperties": True}},
                }, "additionalProperties": False}},
                "total": {"type": "integer"}, "returned": {"type": "integer"},
                "truncated": {"type": "boolean"},
            },
            "required": ["success"], "additionalProperties": False,
        },
        description="""Get type/struct/class definitions with their members.

Returns:
    {
        "success": true,
        "types": [
            {
                "name": "UserStruct", "fullName": "UserStruct",
                "filename": "user.h", "lineNumber": 12, "aliasOf": null,
                "forward_declaration": false,
                "member_total": 2, "members_truncated": false,
                "members": [{"name": "id", "type": "int"}, {"name": "buf", "type": "char*"}]
            }
        ],
        "total": 1, "returned": 1, "truncated": false
    }

Notes:
    - Members are in declaration order. member_total counts all of them;
      members_truncated=true means raise member_limit to see the rest.
    - Declarations without members are listed (member_total=0): an empty
      struct, or forward_declaration=true for `struct X;` with no definition in
      the CPG. A type that is not declared at all returns no rows.
    - aliasOf names the target of a typedef. Joern's `<duplicate>N` variants are
      collapsed into the canonical type, and a definition wins over a forward
      declaration of the same name.
    - Uses CPG type info only; types from headers outside the CPG are absent.

Examples:
    get_type_definition(codebase_hash="abc", type_name=".*request_t.*")
    get_type_definition(codebase_hash="abc", type_name="Wide", member_limit=500)""",
    )
    @tool_errors("verify the CPG is ready and check the type_name regex.")
    def get_type_definition(
        codebase_hash: Annotated[str, Field(description="The codebase hash from generate_cpg")],
        type_name: Annotated[str, Field(description="Type name pattern (regex, e.g., '.*Buffer.*')")],
        limit: Annotated[int, Field(description="Maximum types to return", ge=1, le=MAX_RESULT_ROWS)] = 10,
        member_limit: Annotated[int, Field(description="Maximum members to return per type", ge=1, le=MAX_RESULT_ROWS)] = 100,
    ) -> Dict[str, Any]:
        """Get struct/class definition with member names and types."""
        validate_codebase_hash(codebase_hash)
        query_executor = services["query_executor"]

        codebase_info = require_cpg(services, codebase_hash)

        query = QueryLoader.load(
            "type_definition",
            type_name=type_name,
            limit=limit,
            member_limit=member_limit,
        )

        result = query_executor.execute_query(
            codebase_hash=codebase_hash,
            cpg_path=codebase_info.cpg_path,
            query=query,
            timeout=30,
        )
        data = _parse_json_result(unwrap_result(result))

        types = []
        for item in data.get("types") or []:
            members = item.get("members") or []
            member_total = int(item.get("member_total", len(members)))
            types.append({
                "name": item.get("name"),
                "fullName": item.get("fullName"),
                "filename": item.get("filename"),
                "lineNumber": item.get("lineNumber"),
                "aliasOf": item.get("aliasOf") or None,
                "forward_declaration": bool(item.get("forward_declaration", False)),
                "member_total": member_total,
                "members_truncated": len(members) < member_total,
                "members": members,
            })
        total = int(data.get("total", len(types)))
        return {
            "success": True,
            "types": types,
            "total": total,
            "returned": len(types),
            "truncated": len(types) < total,
        }
