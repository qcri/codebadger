"""
MCP Tool Definitions for CodeBadger Server

Main entry point that registers all tools from separate modules.
Custom tools are loaded last from custom_tools.py.
"""

import logging

from .core_tools import register_core_tools
from .code_browsing_tools import register_code_browsing_tools
from .taint_analysis_tools import register_taint_analysis_tools
from .presentation_tools import register_presentation_tools

logger = logging.getLogger(__name__)

# One group tag per tool. Operators can hide whole groups with
# CODEBADGER_DISABLED_TOOL_TAGS (e.g. "admin,raw"); see apply_disabled_tool_tags.
TOOL_GROUPS = {
    "build": {"generate_cpg", "get_cpg_status", "get_backend_status"},
    "admin": {"remove_cpg"},
    "browse": {
        "list_methods", "list_calls", "list_parameters", "get_call_graph", "get_cfg",
        "get_type_definition", "get_cpgql_syntax_help",
    },
    "raw": {"run_cpgql_query"},
    "taint": {
        "find_taint_sources", "find_taint_sinks", "find_taint_flows",
        "get_program_slice", "get_variable_flow",
    },
    "detector": {
        "find_bounds_checks", "find_use_after_free", "find_double_free",
        "find_null_pointer_deref", "find_integer_overflow", "find_format_string_vulns",
        "find_heap_overflow", "find_stack_overflow", "find_toctou",
        "find_uninitialized_reads", "find_command_injection_sinks",
    },
}
CUSTOM_GROUP = "custom"  # tools added in custom_tools.py that are not listed above
TOOL_GROUP_NAMES = frozenset(TOOL_GROUPS) | {CUSTOM_GROUP}
# Group tags are namespaced so a tool's own descriptive tags (e.g. "taint" on a
# detector) never pull it into a group.
GROUP_TAG_PREFIX = "group:"


def tool_group(name: str) -> str:
    return next((group for group, names in TOOL_GROUPS.items() if name in names), CUSTOM_GROUP)


def group_tag(group: str) -> str:
    return f"{GROUP_TAG_PREFIX}{group}"


class _GroupTaggingServer:
    """Pass-through to the FastMCP server that adds each tool's group tag at
    registration, keeping any tags the tool declares itself."""

    def __init__(self, mcp):
        self._mcp = mcp

    def tool(self, *args, **kwargs):
        def decorate(fn):
            name = kwargs.get("name") or fn.__name__
            tags = set(kwargs.get("tags") or ()) | {group_tag(tool_group(name))}
            return self._mcp.tool(*args, **{**kwargs, "tags": tags})(fn)
        return decorate

    def __getattr__(self, attr):
        return getattr(self._mcp, attr)


def apply_disabled_tool_tags(mcp, value: str) -> set:
    """Hide (and refuse calls to) every tool in the comma-separated groups."""
    requested = {part.strip().lower() for part in (value or "").split(",") if part.strip()}
    unknown = requested - TOOL_GROUP_NAMES
    if unknown:
        logger.warning(
            "Ignoring unknown tool groups in CODEBADGER_DISABLED_TOOL_TAGS: %s (known: %s)",
            ", ".join(sorted(unknown)), ", ".join(sorted(TOOL_GROUP_NAMES)),
        )
    disabled = requested & TOOL_GROUP_NAMES
    if disabled:
        mcp.disable(tags={group_tag(group) for group in disabled})
        logger.info("Disabled tool groups: %s", ", ".join(sorted(disabled)))
    return disabled


def register_tools(mcp, services: dict):
    """Register all MCP tools with the FastMCP server"""

    mcp = _GroupTaggingServer(mcp)
    register_core_tools(mcp, services)
    register_code_browsing_tools(mcp, services)
    register_taint_analysis_tools(mcp, services)
    register_presentation_tools(mcp, services.get("config"))

    try:
        from .custom_tools import register_custom_tools
        register_custom_tools(mcp, services)
        logger.info("Custom tools registered from custom_tools.py")
    except ImportError:
        logger.debug("custom_tools.py not found — skipping custom tool registration")
    except Exception as e:
        logger.error(f"Failed to register custom tools: {e}", exc_info=True)
