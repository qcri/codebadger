"""Discoverable MCP resources and prompts for LLM clients."""

import json


SUPPORTED_LANGUAGES = ["c", "cpp", "java", "javascript", "python"]


def register_presentation_tools(mcp):
    """Register static guidance that helps clients choose a bounded workflow."""

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
                "languages": SUPPORTED_LANGUAGES,
                "workflow": [
                    "Call get_backend_status to discover indexed codebases.",
                    "Call get_cpg_status for the selected codebase.",
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
