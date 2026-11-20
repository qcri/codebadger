"""Schema-level argument failures carry the VALIDATION_ERROR code."""

from typing import Literal

import pytest
from fastmcp import Client, FastMCP

from src.tools.argument_errors import ArgumentValidationMiddleware


def _server(with_middleware: bool = True) -> FastMCP:
    mcp = FastMCP("TestServer")
    if with_middleware:
        mcp.add_middleware(ArgumentValidationMiddleware())

    @mcp.tool
    def walk(direction: Literal["outgoing", "incoming"], depth: int = 1) -> str:
        return f"{direction}:{depth}"

    @mcp.tool
    def fail() -> str:
        raise RuntimeError("tool bug")

    return mcp


@pytest.mark.parametrize(
    ("arguments", "field"),
    [
        ({"direction": "sideways"}, "direction: Input should be 'outgoing' or 'incoming'"),
        ({"direction": "outgoing", "depth": "deep"}, "depth: Input should be a valid integer"),
        ({}, "direction: Missing required argument"),
    ],
)
async def test_invalid_arguments_report_validation_error(arguments, field):
    async with Client(_server()) as client:
        result = await client.call_tool("walk", arguments, raise_on_error=False)

    assert result.is_error
    text = result.content[0].text
    assert text.startswith("VALIDATION_ERROR: ")
    assert field.split(":")[0] in text
    assert "errors.pydantic.dev" not in text


async def test_valid_calls_and_tool_errors_pass_through():
    async with Client(_server()) as client:
        ok = await client.call_tool("walk", {"direction": "incoming", "depth": 2})
        failed = await client.call_tool("fail", {}, raise_on_error=False)

    assert ok.data == "incoming:2"
    assert failed.is_error
    assert not failed.content[0].text.startswith("VALIDATION_ERROR")


def test_server_installs_the_middleware():
    import main

    assert any(isinstance(m, ArgumentValidationMiddleware) for m in main.mcp.middleware)
