"""
Custom Tools — drop your own detectors here.

Every tool follows three steps:
    1. Validate the CPG is ready via _get_codebase().
    2. Load a query from src/tools/queries/<name>.scala via QueryLoader.load().
    3. Execute it with _run_query() and return the result.

Registration is automatic — mcp_tools.py calls register_custom_tools() on
server start.  Restart the server after editing this file.
"""

import logging
import re
from typing import Any, Dict, List, Optional, Annotated
from pydantic import Field

from ..utils.validators import validate_codebase_hash
from .queries import QueryLoader

logger = logging.getLogger(__name__)


def _get_codebase(services: dict, codebase_hash: str):
    """Return CodebaseInfo or raise ValueError if CPG not ready."""
    validate_codebase_hash(codebase_hash)
    info = services["codebase_tracker"].get_codebase(codebase_hash)
    if not info or not info.cpg_path:
        raise ValueError(f"CPG not found for {codebase_hash}. Run generate_cpg first.")
    return info


def _run_query(
    services: dict,
    codebase_hash: str,
    cpg_path: str,
    query: str,
    *,
    timeout: int = 60,
    tool_name: Optional[str] = None,
    cache_params: Optional[dict] = None,
) -> str:
    """Execute a rendered CPGQL query string with optional DB caching.

    Queries must end with the <codebadger_result> wrapper (see queries/*.scala).
    Returns the extracted text content as a string.
    Raises RuntimeError on query failure.
    """
    db = services.get("db_manager")

    if tool_name and cache_params is not None and db:
        cached = db.get_cached_tool_output(tool_name, codebase_hash, cache_params)
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
        raise RuntimeError(result.error or "Query failed")

    if isinstance(result.data, str):
        output = result.data.strip()
    elif isinstance(result.data, list) and len(result.data) > 0:
        output = result.data[0].strip() if isinstance(result.data[0], str) else str(result.data[0])
    else:
        output = str(result.data)

    if tool_name and cache_params is not None and db:
        try:
            db.cache_tool_output(tool_name, codebase_hash, cache_params, output)
        except Exception:
            pass

    return output


def register_custom_tools(mcp, services: dict) -> None:
    """Register all custom analysis tools with the FastMCP server."""

    @mcp.tool(
        description="""Find potential OS command injection sinks (CWE-78).

Identifies call sites where shell-execution functions receive a non-literal
argument — the minimal syntactic signal that user-controlled data might reach
a command interpreter.  Works across C, C++, Python, Java, JavaScript, Go,
PHP, and Ruby.

Args:
    codebase_hash: Hash returned by generate_cpg.
    language:      Narrow to a language's sink set (c, cpp, python, java,
                   javascript, go, php, ruby).  Auto-detected when omitted.
    filename:      Optional filename to restrict results (substring match).
    max_results:   Upper bound on returned call sites (default 50).

Returns:
    Text report listing each sink call site with location and code snippet,
    followed by a suggested next step (find_taint_flows).

Notes:
    - A non-literal argument is necessary but NOT sufficient to confirm injection.
    - Follow up with find_taint_flows(mode='auto', sink_patterns=[...]).
    - Literal-only calls (e.g., system("ls")) are excluded as safe.

Examples:
    find_command_injection_sinks(codebase_hash="abc123")
    find_command_injection_sinks(codebase_hash="abc123", language="python")
    find_command_injection_sinks(codebase_hash="abc123", filename="handler.c")
""",
        tags={"security", "injection", "CWE-78", "command-injection", "taint"},
    )
    def find_command_injection_sinks(
        codebase_hash: Annotated[str, Field(description="Codebase hash from generate_cpg")],
        language: Annotated[Optional[str], Field(description="Language for sink selection (auto-detected if omitted)")] = None,
        filename: Annotated[Optional[str], Field(description="Optional filename to restrict results")] = None,
        max_results: Annotated[int, Field(description="Maximum sink call sites to return", ge=1, le=200)] = 50,
    ) -> str:
        """Identify shell-execution call sites that receive dynamic arguments."""
        try:
            info = _get_codebase(services, codebase_hash)

            lang = (language or info.language or "c").lower()
            sinks_by_lang: Dict[str, List[str]] = {
                "c":          ["system", "popen", "execl", "execv", "execve", "execlp", "execvp", "execvpe"],
                "cpp":        ["system", "popen", "execl", "execv", "execve"],
                "python":     ["system", "popen", "call", "Popen", "run", "check_output", "check_call"],
                "javascript": ["exec", "execSync", "spawn", "spawnSync", "execFile"],
                "java":       ["exec", "start"],
                "go":         ["Command"],
                "php":        ["exec", "shell_exec", "system", "passthru", "popen", "proc_open"],
                "ruby":       ["system", "exec", "popen", "spawn"],
            }
            sink_names = sinks_by_lang.get(lang, sinks_by_lang["c"])
            sink_pattern = "|".join(re.escape(s) for s in sink_names)

            cache_params = {
                "language": lang,
                "filename": filename or "",
                "max_results": max_results,
                "sink_pattern": sink_pattern,
            }

            query = QueryLoader.load(
                "command_injection_sinks",
                sink_pattern=sink_pattern,
                file_filter=filename or "",
                max_results=max_results,
            )

            return _run_query(
                services, codebase_hash, info.cpg_path, query,
                timeout=90,
                tool_name="find_command_injection_sinks",
                cache_params=cache_params,
            )

        except (ValueError, RuntimeError) as e:
            return f"Error: {e}"
        except Exception as e:
            logger.error(f"find_command_injection_sinks: {e}", exc_info=True)
            return f"Internal Error: {e}"

    # Add your own tools below.
    # Template:
    #
    #   @mcp.tool(
    #       description="""One-line summary.
    #
    #   Args:
    #       codebase_hash: Hash returned by generate_cpg.
    #       ...
    #
    #   Returns:
    #       Text report produced by the query.
    #
    #   Examples:
    #       my_tool(codebase_hash="abc123")
    #   """,
    #       tags={"security", "my-category"},
    #   )
    #   def my_tool(
    #       codebase_hash: Annotated[str, Field(description="...")],
    #   ) -> str:
    #       try:
    #           info = _get_codebase(services, codebase_hash)
    #           query = QueryLoader.load("my_query_name", param1=value1)
    #           return _run_query(
    #               services, codebase_hash, info.cpg_path, query,
    #               timeout=60,
    #               tool_name="my_tool",
    #               cache_params={"param1": value1},
    #           )
    #       except (ValueError, RuntimeError) as e:
    #           return f"Error: {e}"
    #       except Exception as e:
    #           logger.error(f"my_tool: {e}", exc_info=True)
    #           return f"Internal Error: {e}"
