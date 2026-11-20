"""Discovery, the generate_cpg schema, and admission share one language list."""

import json
from unittest.mock import MagicMock

import pytest
from fastmcp import Client, FastMCP

from src import defaults
from src.models import Config
from src.tools.argument_errors import ArgumentValidationMiddleware
from src.tools.mcp_tools import register_tools
from src.utils.validators import effective_languages


def _config(languages=None):
    config = Config()
    config.cpg.supported_languages = languages
    return config


@pytest.mark.parametrize(
    ("configured", "expected"),
    [
        (None, defaults.SUPPORTED_LANGUAGES),
        (["rust", "c"], ["rust", "c"]),
        (["c", "cobol"], ["c"]),
        (["cobol"], defaults.SUPPORTED_LANGUAGES),  # nothing usable: fall back, never empty
        ([], defaults.SUPPORTED_LANGUAGES),
    ],
)
def test_effective_languages(configured, expected):
    assert effective_languages(_config(configured)) == list(expected)


async def _surface(config):
    services = {name: MagicMock() for name in (
        "git_manager", "cpg_generator", "codebase_tracker", "query_executor",
        "code_browsing_service", "joern_server_manager",
    )}
    services["config"] = config
    services["codebase_tracker"].get_codebase.return_value = None
    mcp = FastMCP("TestServer")
    mcp.add_middleware(ArgumentValidationMiddleware())
    register_tools(mcp, services)
    client = Client(mcp)
    async with client:
        resource = json.loads((await client.read_resource("codebadger://supported-languages"))[0].text)
        tool = next(t for t in await client.list_tools() if t.name == "generate_cpg")
    enum = next(o["enum"] for o in tool.input_schema["properties"]["language"]["anyOf"] if "enum" in o)
    return mcp, resource["languages"], enum


async def test_defaults_publish_every_supported_language_including_go_and_rust():
    _, discovered, enum = await _surface(_config())

    assert discovered == enum == list(defaults.SUPPORTED_LANGUAGES)
    assert {"go", "rust"} <= set(discovered)


async def test_narrowed_config_changes_discovery_schema_and_admission_together():
    mcp, discovered, enum = await _surface(_config(["c", "rust"]))
    assert discovered == enum == ["c", "rust"]

    async with Client(mcp) as client:
        by_arg = await client.call_tool("generate_cpg", {
            "source_type": "local", "source_path": "/src", "language": "python",
        }, raise_on_error=False)
        by_tag = await client.call_tool("generate_cpg", {
            "source_type": "snippet", "code": '<code language="python">print(1)</code>',
        }, raise_on_error=False)

    assert by_arg.is_error and by_arg.content[0].text.startswith("VALIDATION_ERROR: language: ")
    assert by_tag.is_error
    assert "Unsupported language 'python'" in by_tag.content[0].text
    assert "c, rust" in by_tag.content[0].text
