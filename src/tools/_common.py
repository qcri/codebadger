"""Shared helpers for MCP tool implementations.

The taint, browsing, and custom tools all repeat the same two steps: confirm a
CPG is ready for a codebase hash, and turn a QueryExecutor result into the
string the tool returns. These were copy-pasted ~20+ times across the tool
modules (and had drifted), so they live here as single sources of truth.
"""

import functools
import logging
from typing import Optional

from fastmcp.exceptions import ToolError

from ..exceptions import ValidationError
from ..utils.validators import sanitize_error_text, validate_codebase_hash

logger = logging.getLogger(__name__)

# Prefixes a string-returning tool uses to signal failure. Centralized so the
# cache layer (which must not cache failures) stays in lock-step with the tools
# that emit these strings (unwrap_result, the tool try/except handlers).
ERROR_PREFIXES = ("Error:", "Validation Error:", "Internal Error:", "ANCHOR_NOT_FOUND:", "VARIABLE_NOT_FOUND:")


def is_error_output(text) -> bool:
    """True if a string tool result is an error sentinel (so: don't cache it)."""
    return isinstance(text, str) and text.startswith(ERROR_PREFIXES)


def tool_error(code: str, message: str, hint: str) -> ToolError:
    """The one failure shape for MCP tools: an isError result 'CODE: message Hint: ...'."""
    return ToolError(f"{code}: {message} Hint: {hint}")


def tool_errors(hint: str):
    """Map a tool's failures onto tool_error.

    ValidationError -> VALIDATION_ERROR; a returned {"success": False, ...} dict ->
    its error_code (QUERY_ERROR when absent); any other exception -> INTERNAL_ERROR.
    ToolErrors raised by the tool pass through unchanged.
    """
    def decorate(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            try:
                result = fn(*args, **kwargs)
            except ToolError:
                raise
            except ValidationError as e:
                raise tool_error("VALIDATION_ERROR", str(e), hint) from e
            except Exception as e:
                logger.error("Unexpected error in %s: %s", fn.__name__, e, exc_info=True)
                raise tool_error("INTERNAL_ERROR", sanitize_error_text(str(e)), hint) from e
            if isinstance(result, dict) and result.get("success") is False:
                raise tool_error(
                    result.get("error_code") or "QUERY_ERROR",
                    str(result.get("error") or "Request failed"),
                    hint,
                )
            return result
        return wrapper
    return decorate


def cache_policy(services: dict) -> tuple[bool, int]:
    query_config = getattr(services.get("config"), "query", None)
    return (
        getattr(query_config, "cache_enabled", True),
        getattr(query_config, "cache_ttl", 300),
    )


def require_cpg(services: dict, codebase_hash: str):
    """Validate the hash and return its CodebaseInfo, or raise ValidationError.

    Raises ValidationError if the hash is malformed or no built CPG exists yet —
    the standard precondition for every analysis tool.
    """
    validate_codebase_hash(codebase_hash)
    info = services["codebase_tracker"].get_codebase(codebase_hash)
    if not info or not info.cpg_path:
        raise ValidationError(
            f"CPG not found for codebase {codebase_hash}. Generate it first using generate_cpg."
        )
    return info


QUERY_FAILURE_HINT = (
    "verify the CPG is ready (get_cpg_status); retry on SERVER_BUSY, and narrow the "
    "query or raise its timeout on TIMEOUT."
)


def query_failure(result) -> ToolError:
    """The coded MCP error for a failed QueryExecutor result.

    Raised before any rendering or caching, so a timeout or busy server can never
    surface as a successful analysis whose text happens to describe an error.
    """
    return tool_error(
        result.error_code or "QUERY_ERROR",
        sanitize_error_text(result.error or "Query failed", max_len=2000),
        QUERY_FAILURE_HINT,
    )


def unwrap_result(result) -> str:
    """Turn a successful QueryExecutor result into the tool's string output.

    A failed result raises its coded error (query_failure); success extracts the
    text payload from ``result.data`` (a str, a single-element list, or other).
    """
    if not result.success:
        raise query_failure(result)
    data = result.data
    if isinstance(data, str):
        return data.strip()
    if isinstance(data, list) and len(data) > 0:
        first = data[0]
        return first.strip() if isinstance(first, str) else str(first)
    return f"Query returned unexpected format: {type(data)}"


def run_query(
    services: dict,
    codebase_hash: str,
    cpg_path: str,
    query: str,
    *,
    timeout: int = 60,
    tool_name: Optional[str] = None,
    cache_params: Optional[dict] = None,
) -> str:
    """Execute a rendered CPGQL query (with optional DB caching) and return text.

    Returns the extracted <codebadger_result> text. A failed query raises its
    coded ToolError (query_failure), which callers must let propagate. Used by the custom tools; the taint tools use their own
    cache wrapper (_cached_taint_query) instead.
    """
    db = services.get("db_manager")
    cache_enabled, cache_ttl = cache_policy(services)

    if cache_enabled and tool_name and cache_params is not None and db:
        try:
            cached = db.get_cached_tool_output(
                tool_name, codebase_hash, cache_params, cache_ttl=cache_ttl
            )
        except Exception as e:
            logger.debug(f"Cache lookup failed for {tool_name} (non-fatal): {e}")
            cached = None
        if cached is not None:
            logger.debug(f"Cache hit for {tool_name}")
            return cached

    result = services["query_executor"].execute_query(
        codebase_hash=codebase_hash,
        cpg_path=cpg_path,
        query=query,
        timeout=timeout,
    )

    if not result.success:
        raise query_failure(result)

    if isinstance(result.data, str):
        output = result.data.strip()
    elif isinstance(result.data, list) and len(result.data) > 0:
        output = result.data[0].strip() if isinstance(result.data[0], str) else str(result.data[0])
    else:
        output = str(result.data)

    if cache_enabled and tool_name and cache_params is not None and db:
        try:
            db.cache_tool_output(tool_name, codebase_hash, cache_params, output)
        except Exception as e:
            logger.debug(f"Cache write failed for {tool_name} (non-fatal): {e}")

    return output
