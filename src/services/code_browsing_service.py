import logging
import re
from typing import Any, Dict, Optional
from ..defaults import MAX_RESULT_ROWS
from ..exceptions import ValidationError
from ..utils.validators import validate_codebase_hash
from ..utils.query_rendering import escape_scala_string

logger = logging.getLogger(__name__)


def _decode_count(raw: Any) -> Optional[int]:
    """Decode a scalar count returned by Joern across output formats."""
    if isinstance(raw, bool):
        return int(raw)
    if isinstance(raw, (int, float)):
        return int(raw)
    if isinstance(raw, str):
        value = raw.strip()
        try:
            return int(value)
        except ValueError:
            # Joern's Scala REPL may retain the assignment prefix even when
            # QueryExecutor requests a scalar via `.toString`.
            match = re.fullmatch(
                r'val\s+\w+:\s+String\s*=\s*"(-?\d+)"', value
            )
            return int(match.group(1)) if match else None
    if isinstance(raw, (list, tuple)):
        if not raw:
            return 0
        return _decode_count(raw[0])
    if isinstance(raw, dict):
        for key in ("value", "count", "_1"):
            if key in raw:
                return _decode_count(raw[key])
    return None


def _query_failure(message: str, error_code: Optional[str]) -> Dict[str, Any]:
    return {
        "success": False,
        "error": message,
        "error_code": error_code or "QUERY_ERROR",
    }


def _decode_parameter(raw: Any) -> Optional[Dict[str, Any]]:
    """Decode the JSON shapes Joern uses for a parameter tuple."""
    if isinstance(raw, dict):
        if "_1" in raw:
            return {
                "name": raw.get("_1", ""),
                "type": raw.get("_2", ""),
                "index": raw.get("_3", -1),
            }
        if "name" in raw:
            return {
                "name": raw.get("name", ""),
                "type": raw.get("type", raw.get("typeFullName", "")),
                "index": raw.get("index", -1),
            }
        # Depending on the Joern/OverflowDB JSON encoder version, a tuple may
        # be rendered as {"parameterName": ["type", index]}.
        if len(raw) == 1:
            name, values = next(iter(raw.items()))
            if isinstance(values, (list, tuple)):
                return {
                    "name": name,
                    "type": values[0] if values else "",
                    "index": values[1] if len(values) > 1 else -1,
                }

    if isinstance(raw, (list, tuple)) and len(raw) >= 3:
        return {"name": raw[0], "type": raw[1], "index": raw[2]}
    return None


def _decode_parameter_method(raw: Any) -> Optional[Dict[str, Any]]:
    """Decode one method and its parameter list across Joern JSON versions."""
    method_name: Any
    raw_parameters: Any

    identity: Dict[str, Any] = {}
    if isinstance(raw, dict) and "_7" in raw:
        # (name, id, fullName, owner, filename, line, parameters)
        method_name = raw.get("_1", "")
        raw_parameters = raw.get("_7", [])
        identity = {
            "node_id": str(raw.get("_2", "")),
            "fullName": raw.get("_3", ""),
            "owner": raw.get("_4", ""),
            "filename": raw.get("_5", ""),
            "lineNumber": raw.get("_6", -1),
        }
    elif isinstance(raw, dict) and "_1" in raw and "_2" in raw:
        method_name = raw.get("_1", "")
        raw_parameters = raw.get("_2", [])
    elif isinstance(raw, dict) and "method" in raw:
        method_name = raw.get("method", "")
        raw_parameters = raw.get("parameters", [])
    elif isinstance(raw, dict) and len(raw) == 1:
        # Current Joern serializes (methodName, parameterTuples) as
        # {"methodName": [...]} rather than {"_1": ..., "_2": [...]}.
        method_name, raw_parameters = next(iter(raw.items()))
    elif isinstance(raw, (list, tuple)) and len(raw) >= 2:
        method_name, raw_parameters = raw[0], raw[1]
    else:
        return None

    if not isinstance(raw_parameters, (list, tuple)):
        raw_parameters = []
    parameters = []
    for raw_parameter in raw_parameters:
        parameter = _decode_parameter(raw_parameter)
        if parameter is not None:
            parameters.append(parameter)
    return {"method": method_name, **identity, "parameters": parameters}


def _paging_hint(total: int, available: int, limit: int, page: int, page_size: int, total_pages: int) -> Optional[str]:
    """How to reach matches this reply does not show, or None when nothing is hidden.

    Pages cover only the `available` matches kept under `limit`; `total` counts all.
    """
    shown_through = min(page * page_size, available)
    if shown_through < available:
        return f"More matches on later pages: request page={page + 1} (pages 1-{total_pages})."
    if available < total:
        wanted = min(total, MAX_RESULT_ROWS)
        past = f"page {page} is past the last page ({total_pages}); " if page > max(total_pages, 1) else ""
        return (
            f"{past}limit={limit} kept {available} of {total} matches. "
            f"Raise limit (up to {wanted}) or narrow the filters to see the rest."
        )
    if page > max(total_pages, 1):
        return f"page {page} is past the last page ({total_pages}); all {total} matches fit on pages 1-{total_pages}."
    return None


def _regex_literal(text: str) -> str:
    """Escape every non-alphanumeric character so Java regex matches it literally."""
    return "".join(ch if ch.isalnum() or ch == " " else "\\" + ch for ch in text)


def _literal_regex(text: str, match: str) -> str:
    """The full-match regex Joern's .code(...) needs for a literal search."""
    if match == "regex":
        return text
    escaped = _regex_literal(text)
    if match == "exact":
        # String literals keep their quotes in `code`; numbers do not.
        return f'"?{escaped}"?'
    return f"(?s).*{escaped}.*"


class CodeBrowsingService:
    """Service for code browsing operations with caching support"""

    def __init__(
        self, codebase_tracker, query_executor, db_manager=None,
        cache_enabled=True, cache_ttl=300,
    ):
        self.codebase_tracker = codebase_tracker
        self.query_executor = query_executor
        self.db_manager = db_manager
        self.cache_enabled = cache_enabled
        self.cache_ttl = cache_ttl

    def _get_cached_or_execute(
        self, tool_name: str, codebase_hash: str, params: Dict[str, Any], query_func
    ):
        """Helper to check cache, execute query if needed, and cache result"""
        if self.db_manager and self.cache_enabled:
            try:
                cached = self.db_manager.get_cached_tool_output(
                    tool_name, codebase_hash, params, cache_ttl=self.cache_ttl
                )
                if cached is not None:
                    return cached
            except Exception:
                logger.debug("Cache lookup failed for %s", tool_name, exc_info=True)

        result = query_func()

        if self.db_manager and self.cache_enabled and result:
            # Only cache successful results that are not error dicts
            if isinstance(result, dict) and result.get("success", False):
                try:
                    self.db_manager.cache_tool_output(
                        tool_name, codebase_hash, params, result
                    )
                except Exception:
                    logger.debug("Cache write failed for %s", tool_name, exc_info=True)

        return result

    def list_methods(
        self,
        codebase_hash: str,
        name_pattern: Optional[str] = None,
        file_pattern: Optional[str] = None,
        callee_pattern: Optional[str] = None,
        include_external: bool = False,
        limit: int = 1000,
        page: int = 1,
        page_size: int = 100,
    ) -> Dict[str, Any]:

        validate_codebase_hash(codebase_hash)

        # Cache key parameters (excluding pagination)
        cache_params = {
            "name_pattern": name_pattern,
            "file_pattern": file_pattern,
            "callee_pattern": callee_pattern,
            "include_external": include_external,
            "limit": limit,
            # v3: cyclomaticComplexity is McCabe over the method CFG.
            "result_schema_version": 3,
        }

        def execute_query():
            codebase_info = self.codebase_tracker.get_codebase(codebase_hash)
            if not codebase_info:
                raise ValidationError(
                    f"Codebase not found for codebase {codebase_hash}"
                )

            query_parts = ["cpg.method"]
            if not include_external:
                query_parts.append(".isExternal(false)")
            if name_pattern:
                query_parts.append(f'.name("{escape_scala_string(name_pattern)}")')
            if file_pattern:
                query_parts.append(
                    f'.where(_.file.name("{escape_scala_string(file_pattern)}"))'
                )
            if callee_pattern:
                query_parts.append(
                    f'.where(_.callOut.name("{escape_scala_string(callee_pattern)}"))'
                )

            base_query = "".join(query_parts) + ".dedup"
            count_result = self.query_executor.execute_query(
                codebase_hash=codebase_hash,
                cpg_path=codebase_info.cpg_path,
                query=f"{base_query}.size",
                timeout=30,
            )
            if not count_result.success:
                return _query_failure(count_result.error, count_result.error_code)

            total = _decode_count(count_result.data)
            if total is None:
                return _query_failure("Joern returned an invalid method count", None)

            projection = (
                ".map(m => (m.name, m.id, m.fullName, m.signature, m.filename, m.lineNumber.getOrElse(-1), m.lineNumberEnd.getOrElse(-1), m.cfgNode.l.map(_._cfgOut.size).filter(_ > 1).map(_ - 1).sum + 1, m.isExternal))"
            )

            query_limit = max(0, min(limit, 10000))
            query = f"{base_query}{projection}.take({query_limit}).l"

            result = self.query_executor.execute_query(
                codebase_hash=codebase_hash,
                cpg_path=codebase_info.cpg_path,
                query=query,
                timeout=30,
                limit=query_limit,
            )

            if not result.success:
                return _query_failure(result.error, result.error_code)

            methods = []
            for item in result.data:
                if isinstance(item, dict):
                    line_number = item.get("_6", -1)
                    line_number_end = item.get("_7", -1)

                    # Calculate number of lines
                    if line_number != -1 and line_number_end != -1:
                        number_of_lines = line_number_end - line_number + 1
                    else:
                        number_of_lines = 0

                    methods.append(
                        {
                            "name": item.get("_1", ""),
                            "node_id": str(item.get("_2", "")),
                            "fullName": item.get("_3", ""),
                            "signature": item.get("_4", ""),
                            "filename": item.get("_5", ""),
                            "lineNumber": line_number,
                            "lineNumberEnd": line_number_end,
                            "cyclomaticComplexity": item.get("_8", 1),
                            "numberOfLines": number_of_lines,
                            "isExternal": item.get("_9", False),
                        }
                    )
            return {
                "success": True,
                "methods": methods,
                "total": total,
                "truncated": total > query_limit,
            }

        # Get full result (cached or fresh)
        full_result = self._get_cached_or_execute(
            "list_methods", codebase_hash, cache_params, execute_query
        )

        if not full_result.get("success"):
            return full_result

        methods = full_result.get("methods", [])
        total = full_result.get("total", len(methods))
        available = len(methods)  # matches kept for paging (at most `limit`)

        # Pagination
        start_idx = (page - 1) * page_size
        end_idx = start_idx + page_size
        paged_methods = methods[start_idx:end_idx]
        total_pages = (available + page_size - 1) // page_size if page_size > 0 else 1

        response = {
            "success": True,
            "methods": paged_methods,
            "total": total,
            "returned": len(paged_methods),
            # More matches exist beyond this reply: later pages or past `limit`.
            "truncated": start_idx + len(paged_methods) < total,
            "page": page,
            "page_size": page_size,
            "total_pages": total_pages,
        }
        hint = _paging_hint(total, available, limit, page, page_size, total_pages)
        if hint:
            response["hint"] = hint
        return response

    def list_calls(
        self,
        codebase_hash: str,
        caller_pattern: Optional[str] = None,
        callee_pattern: Optional[str] = None,
        limit: int = 1000,
        page: int = 1,
        page_size: int = 100,
    ) -> Dict[str, Any]:

        validate_codebase_hash(codebase_hash)
        cache_params = {
            "caller_pattern": caller_pattern,
            "callee_pattern": callee_pattern,
            "limit": limit,
            # v3: one row per call node, with node_id/columnNumber/caller_id.
            "result_schema_version": 3,
        }

        def execute_query():
            codebase_info = self.codebase_tracker.get_codebase(codebase_hash)
            if not codebase_info or not codebase_info.cpg_path:
                raise ValidationError(f"CPG not found for codebase {codebase_hash}")

            query_parts = ["cpg.call"]
            if callee_pattern:
                query_parts.append(f'.name("{escape_scala_string(callee_pattern)}")')
            if caller_pattern:
                query_parts.append(
                    f'.where(_.method.name("{escape_scala_string(caller_pattern)}"))'
                )

            # Deduplicate call nodes, not projected rows: two identical calls on
            # one line are two call sites.
            nodes_query = "".join(query_parts) + ".dedup"
            projection = (
                ".map(c => (c.method.name, c.name, c.code, c.method.filename, c.lineNumber.getOrElse(-1),"
                " c.id, c.columnNumber.getOrElse(-1), c.method.id))"
            )
            base_query = nodes_query + projection
            count_result = self.query_executor.execute_query(
                codebase_hash=codebase_hash,
                cpg_path=codebase_info.cpg_path,
                query=f"{nodes_query}.size",
                timeout=30,
            )
            if not count_result.success:
                return _query_failure(count_result.error, count_result.error_code)

            total = _decode_count(count_result.data)
            if total is None:
                return _query_failure("Joern returned an invalid call count", None)

            query_limit = max(0, min(limit, 10000))
            query = f"{base_query}.take({query_limit}).l"

            result = self.query_executor.execute_query(
                codebase_hash=codebase_hash,
                cpg_path=codebase_info.cpg_path,
                query=query,
                timeout=30,
                limit=query_limit,
            )

            if not result.success:
                return _query_failure(result.error, result.error_code)

            calls = []
            for item in result.data:
                if isinstance(item, dict):
                    calls.append(
                        {
                            "caller": item.get("_1", ""),
                            "callee": item.get("_2", ""),
                            "code": item.get("_3", ""),
                            "filename": item.get("_4", ""),
                            "lineNumber": item.get("_5", -1),
                            "columnNumber": item.get("_7", -1),
                            "node_id": str(item.get("_6", "")),
                            "caller_id": str(item.get("_8", "")),
                        }
                    )
            return {
                "success": True,
                "calls": calls,
                "total": total,
                "truncated": total > query_limit,
            }

        full_result = self._get_cached_or_execute(
            "list_calls", codebase_hash, cache_params, execute_query
        )

        if not full_result.get("success"):
            return full_result

        calls = full_result.get("calls", [])
        total = full_result.get("total", len(calls))
        available = len(calls)  # matches kept for paging (at most `limit`)

        start_idx = (page - 1) * page_size
        end_idx = start_idx + page_size
        paged_calls = calls[start_idx:end_idx]
        total_pages = (available + page_size - 1) // page_size if page_size > 0 else 1

        response = {
            "success": True,
            "calls": paged_calls,
            "total": total,
            "returned": len(paged_calls),
            # More matches exist beyond this reply: later pages or past `limit`.
            "truncated": start_idx + len(paged_calls) < total,
            "page": page,
            "page_size": page_size,
            "total_pages": total_pages,
        }
        hint = _paging_hint(total, available, limit, page, page_size, total_pages)
        if hint:
            response["hint"] = hint
        return response

    def list_parameters(
        self,
        codebase_hash: str,
        method_name: Optional[str] = None,
        limit: int = 1000,
        method_id: int = -1,
    ) -> Dict[str, Any]:

        validate_codebase_hash(codebase_hash)
        # Bump this when the decoded response contract changes so successful
        # empty results cached by an older decoder are not served indefinitely.
        cache_params = {"method_name": method_name, "method_id": method_id, "limit": limit,
                        "result_schema_version": 3}

        def execute_query():
            codebase_info = self.codebase_tracker.get_codebase(codebase_hash)
            if not codebase_info or not codebase_info.cpg_path:
                raise ValidationError(f"CPG not found for codebase {codebase_hash}")

            query_parts = ["cpg.method"]
            if method_id >= 0:
                query_parts.append(f".filter(_.id == {int(method_id)}L)")
            elif method_name:
                query_parts.append(f'.name("{escape_scala_string(method_name)}")')

            # The owner is the defining type (a Python/Java class); a C function's
            # file-level <global> type is not an owner.
            query_parts.append(
                ".map(m => (m.name, m.id, m.fullName,"
                " m.definingTypeDecl.filterNot(_.name == \"<global>\").map(_.fullName).getOrElse(\"\"),"
                " m.filename, m.lineNumber.getOrElse(-1),"
                " m.parameter.l.sortBy(_.index).map(p => (p.name, p.typeFullName, p.index))))"
            )

            query_limit = max(1, min(int(limit), MAX_RESULT_ROWS))
            # One extra row tells a complete list from a capped one.
            query = "".join(query_parts) + f".take({query_limit + 1}).l"

            result = self.query_executor.execute_query(
                codebase_hash=codebase_hash,
                cpg_path=codebase_info.cpg_path,
                query=query,
                timeout=30,
                limit=query_limit + 1,
            )

            if not result.success:
                return _query_failure(result.error, result.error_code)

            methods = []
            for item in result.data:
                method = _decode_parameter_method(item)
                if method is not None:
                    methods.append(method)
            truncated = len(methods) > query_limit
            methods = methods[:query_limit]
            response = {"success": True, "methods": methods, "total": len(methods),
                        "returned": len(methods), "truncated": truncated}
            if truncated:
                response["hint"] = (f"More than {query_limit} methods match; narrow method_name "
                                    "or pass method_id from list_methods.")
            return response

        return self._get_cached_or_execute(
            "list_parameters", codebase_hash, cache_params, execute_query
        )

    def find_literals(
        self,
        codebase_hash: str,
        text: Optional[str] = None,
        match: str = "contains",
        literal_type: Optional[str] = None,
        file_pattern: Optional[str] = None,
        limit: int = 50,
    ) -> Dict[str, Any]:
        """Literal nodes (strings, numbers) matching `text`, with exact locations.

        match="contains" (default) and "exact" treat `text` literally, so `[retry]`
        means the brackets; "exact" ignores a string literal's surrounding quotes.
        match="regex" passes `text` through as a Java regex over the literal code.
        """
        validate_codebase_hash(codebase_hash)
        if match not in ("contains", "exact", "regex"):
            raise ValidationError("match must be 'contains', 'exact' or 'regex'")
        limit = max(1, min(int(limit), MAX_RESULT_ROWS))
        cache_params = {
            "text": text,
            "match": match,
            "literal_type": literal_type,
            "file_pattern": file_pattern,
            "limit": limit,
            "result_schema_version": 2,
        }

        def execute_query():
            codebase_info = self.codebase_tracker.get_codebase(codebase_hash)
            if not codebase_info or not codebase_info.cpg_path:
                raise ValidationError(f"CPG not found for codebase {codebase_hash}")

            query_parts = ["cpg.literal"]
            if text:
                query_parts.append(f'.code("{escape_scala_string(_literal_regex(text, match))}")')
            if literal_type:
                query_parts.append(
                    f'.typeFullName(".*{escape_scala_string(_regex_literal(literal_type))}.*")'
                )
            if file_pattern:
                query_parts.append(f'.where(_.file.name("{escape_scala_string(file_pattern)}"))')
            nodes_query = "".join(query_parts)

            count_result = self.query_executor.execute_query(
                codebase_hash=codebase_hash,
                cpg_path=codebase_info.cpg_path,
                query=f"{nodes_query}.size",
                timeout=30,
            )
            if not count_result.success:
                return _query_failure(count_result.error, count_result.error_code)
            total = _decode_count(count_result.data)
            if total is None:
                return _query_failure("Joern returned an invalid literal count", None)

            projection = (
                ".map(lit => (lit.code, lit.typeFullName, lit.method.filename, lit.lineNumber.getOrElse(-1),"
                " lit.columnNumber.getOrElse(-1), lit.id, lit.method.name, lit.method.id, lit.method.fullName))"
            )
            result = self.query_executor.execute_query(
                codebase_hash=codebase_hash,
                cpg_path=codebase_info.cpg_path,
                query=f"{nodes_query}{projection}.take({limit}).l",
                timeout=30,
                limit=limit,
            )
            if not result.success:
                return _query_failure(result.error, result.error_code)

            literals = []
            for item in result.data:
                if isinstance(item, dict):
                    literals.append({
                        "value": item.get("_1", ""),
                        "type": item.get("_2", ""),
                        "filename": item.get("_3", ""),
                        "lineNumber": item.get("_4", -1),
                        "columnNumber": item.get("_5", -1),
                        "node_id": str(item.get("_6", "")),
                        "method": item.get("_7", ""),
                        "method_id": str(item.get("_8", "")),
                        "method_full_name": item.get("_9", ""),
                    })
            response = {
                "success": True,
                "literals": literals,
                "total": total,
                "returned": len(literals),
                "truncated": len(literals) < total,
            }
            if len(literals) < total:
                response["hint"] = (
                    f"limit={limit} returned {len(literals)} of {total} literals; raise limit "
                    "(up to {MAX_RESULT_ROWS}) or narrow text, literal_type or file_pattern."
                ).replace("{MAX_RESULT_ROWS}", str(MAX_RESULT_ROWS))
            return response

        return self._get_cached_or_execute(
            "find_literals", codebase_hash, cache_params, execute_query
        )

    def warm_up_cache(self, codebase_hash: str):
        """Run default queries to warm the cache.

        These all target the same CPG, so they serialize on the per-codebase
        query lock regardless — the old ThreadPoolExecutor was false parallelism
        (5 threads idling on the lock). Run them sequentially; a failing query is
        logged and skipped so it doesn't abort the rest. Callers run this off the
        build-worker critical path (see core_tools._schedule_warmup).
        """
        logger.info(f"Warming up cache for codebase {codebase_hash}")
        # Only queries whose cache key matches a default MCP call: list_methods and
        # list_calls with their tool defaults. (list_parameters needs a method name
        # the warm-up can't know, and find_literals always needs search text.)
        tasks = [
            self.list_methods,
            self.list_calls,
        ]
        for func in tasks:
            try:
                func(codebase_hash)
                logger.info(
                    f"Cache warm-up task {func.__name__} completed for {codebase_hash}"
                )
            except Exception as e:
                logger.error(
                    f"Cache warm-up task {func.__name__} failed for {codebase_hash}: {e}"
                )
        logger.info(f"Cache warm-up complete for {codebase_hash}")
