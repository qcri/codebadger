#!/usr/bin/env python3
"""Smoke-test a deployed CodeBadger MCP over HTTP (no CPG build needed).

Exercises protocol negotiation, tool routing, and structured-result
serialization through the real transport:

    python scripts/mcp_smoke.py [--url http://127.0.0.1:4242/mcp]

Exits non-zero with a message on the first failed check.
"""

import argparse
import asyncio
import sys


async def smoke(url: str) -> None:
    from fastmcp import Client

    async with Client(url, timeout=60) as client:
        tools = {tool.name for tool in await client.list_tools()}
        for required in ("generate_cpg", "get_cpg_status", "get_cpgql_syntax_help"):
            assert required in tools, f"tool {required!r} not registered (got {len(tools)} tools)"

        result = await client.call_tool("get_cpgql_syntax_help", {})
        reply = result.structured_content
        assert not result.is_error, f"get_cpgql_syntax_help failed: {result.content}"
        assert reply and reply.get("success") is True, f"unexpected reply: {reply}"
        assert reply.get("quick_reference"), "structured reply lacks quick_reference"

        bad = await client.call_tool("get_cpg_status", {"codebase_hash": "bad"}, raise_on_error=False)
        assert bad.is_error and bad.content[0].text.startswith("VALIDATION_ERROR: "), (
            f"coded error expected, got: {bad.content}"
        )
    print(f"MCP smoke test passed: {len(tools)} tools, structured and error results OK")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--url", default="http://127.0.0.1:4242/mcp")
    args = parser.parse_args()
    try:
        asyncio.run(smoke(args.url))
    except AssertionError as e:
        print(f"MCP smoke test FAILED: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
