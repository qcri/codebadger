"""generate_cpg and remove_cpg publish schemas that cover every reply they build."""

import ast
import pathlib
from unittest.mock import MagicMock

import pytest
from fastmcp import Client, FastMCP

from src.models import Config, SessionStatus
from src.tools.core_tools import _public_codebase_fields, register_core_tools

SOURCE = pathlib.Path("src/tools/core_tools.py").read_text()
TREE = ast.parse(SOURCE)


def _function(name):
    return next(
        n for n in ast.walk(TREE)
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name
    )


def _reply_keys(name):
    """Keys of every dict literal the tool returns (expanding _public_codebase_fields)."""
    keys = set()
    for node in ast.walk(_function(name)):
        if isinstance(node, ast.Return) and isinstance(node.value, ast.Dict):
            for key, value in zip(node.value.keys, node.value.values):
                if key is None and "_public_codebase_fields" in ast.unparse(value):
                    keys |= set(_public_codebase_fields(
                        source_type="local", source_path="/x", language="c"
                    ))
                elif isinstance(key, ast.Constant):
                    keys.add(key.value)
    return keys


def _reply_statuses(name):
    statuses = set()
    for node in ast.walk(_function(name)):
        if isinstance(node, ast.Dict):
            for key, value in zip(node.keys, node.values):
                if isinstance(key, ast.Constant) and key.value == "status":
                    text = ast.unparse(value)
                    if text.startswith("SessionStatus."):
                        statuses.add(getattr(SessionStatus, text.split(".")[1]).value)
                    elif isinstance(value, ast.Constant):
                        statuses.add(value.value)
                    elif isinstance(value, ast.IfExp):
                        for branch in (value.body, value.orelse):
                            t = ast.unparse(branch)
                            statuses.add(getattr(SessionStatus, t.split(".")[1]).value
                                         if t.startswith("SessionStatus.") else ast.literal_eval(t))
    return statuses


async def _schemas():
    mcp = FastMCP("TestServer")
    register_core_tools(mcp, {"codebase_tracker": MagicMock(), "config": Config()})
    async with Client(mcp) as client:
        return {t.name: t.output_schema for t in await client.list_tools()}


@pytest.mark.parametrize("tool", ["generate_cpg", "remove_cpg"])
async def test_schema_declares_every_reply_key_and_status(tool):
    schema = (await _schemas())[tool]

    assert schema["additionalProperties"] is False
    assert _reply_keys(tool) - set(schema["properties"]) == set()
    assert _reply_statuses(tool) - set(schema["properties"]["status"]["enum"]) == set()
