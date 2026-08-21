import json

import pytest
from fastmcp import Client, FastMCP

from src.tools.presentation_tools import register_presentation_tools


@pytest.mark.anyio
async def test_workflow_resource_and_prompt_are_discoverable():
    mcp = FastMCP("test")
    register_presentation_tools(mcp)

    async with Client(mcp) as client:
        resources = await client.list_resources()
        assert any(str(resource.uri) == "codebadger://supported-languages" for resource in resources)
        resource = await client.read_resource("codebadger://supported-languages")
        payload = json.loads(resource[0].text)
        assert "c" in payload["languages"]
        assert payload["workflow"]

        prompts = await client.list_prompts()
        assert any(prompt.name == "codebase_overview" for prompt in prompts)
        result = await client.get_prompt("codebase_overview", {"codebase_hash": "demo"})
        assert "demo" in result.messages[0].content.text
