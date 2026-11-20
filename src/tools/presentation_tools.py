"""Discoverable MCP resources and prompts for LLM clients."""

import json


from ..utils.validators import effective_languages


def register_presentation_tools(mcp, config=None):
    """Register static guidance that helps clients choose a bounded workflow."""
    languages = effective_languages(config)

    @mcp.resource(
        "codebadger://supported-languages",
        name="supported_languages",
        title="Supported Languages",
        description="Languages and recommended workflow supported by CodeBadger.",
        mime_type="application/json",
    )
    def supported_languages() -> str:
        return json.dumps(
            {
                "languages": languages,
                "workflow": [
                    "Call get_backend_status (optionally filtered by language or project) to discover indexed codebases.",
                    "Call get_cpg_status with the selected row's codebase_hash.",
                    "Start with bounded list_methods or list_calls queries.",
                    "Use pagination fields and narrow filters before expanding scope.",
                ]
            },
            separators=(",", ":"),
        )

    @mcp.prompt(
        name="codebase_overview",
        title="Codebase Overview",
        description="Guide an LLM through a concise, bounded codebase overview.",
    )
    def codebase_overview(codebase_hash: str) -> str:
        return (
            f"Build a concise overview for codebase '{codebase_hash}'. "
            "First inspect its CPG status, then list a small page of methods "
            "and calls. Report totals, returned counts, and truncation; only "
            "request broader pages when the evidence requires it."
        )
