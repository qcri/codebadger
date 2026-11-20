#!/usr/bin/env python3
"""
Sample client for the CodeBadger MCP server.

1. Generate (or reuse) a CPG for a local codebase.
2. Poll get_cpg_status until the CPG is ready, stopping on failure.
3. List a page of methods and calls.

Usage:
    python examples/sample_client.py [SOURCE_PATH] [--url http://localhost:4242/mcp]

SOURCE_PATH defaults to the bundled core fixture (playground/codebases/core).
"""

import argparse
import asyncio
import logging
import sys
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

DEFAULT_URL = "http://localhost:4242/mcp"
DEFAULT_SOURCE = Path(__file__).resolve().parent.parent / "playground" / "codebases" / "core"


class CPGNotReady(RuntimeError):
    """The CPG build failed or did not finish in time."""


async def call(client, tool, arguments):
    """Call a tool and return its structured reply as a dict."""
    result = await client.call_tool(tool, arguments)
    return result.structured_content or {}


async def wait_until_ready(client, codebase_hash, attempts=150, delay=2.0):
    """Poll get_cpg_status until the CPG is ready; raise on failure or timeout."""
    for attempt in range(1, attempts + 1):
        status = await call(client, "get_cpg_status", {"codebase_hash": codebase_hash})
        state = status.get("status")
        logger.info(f"  Attempt {attempt}/{attempts}: status={state} phase={status.get('phase')}")
        if state == "ready":
            return status
        if state == "failed":
            raise CPGNotReady(
                f"CPG build failed ({status.get('error_code', 'UNKNOWN')}): {status.get('error')}"
            )
        await asyncio.sleep(delay)
    raise CPGNotReady(f"CPG not ready after {attempts} attempts")


async def run(url, source_path, language="c"):
    from fastmcp import Client

    logger.info(f"Server: {url}\nSource: {source_path}")
    async with Client(url) as client:
        # list_tools doubles as a connectivity check; MCP 2026 servers are
        # sessionless and do not answer the legacy ping request.
        tools = await client.list_tools()
        logger.info(f"[1] Server is responding ({len(tools)} tools)")

        logger.info("[2] Generating CPG...")
        generated = await call(client, "generate_cpg", {
            "source_type": "local", "source_path": str(source_path), "language": language,
        })
        if generated.get("status") == "large_project_warning":
            raise CPGNotReady(f"{generated.get('message')} Re-run with a sub-path or force=True.")
        if not generated.get("success") or "codebase_hash" not in generated:
            raise CPGNotReady(f"generate_cpg failed: {generated}")
        codebase_hash = generated["codebase_hash"]
        logger.info(f"    codebase_hash={codebase_hash} status={generated.get('status')}")

        logger.info("[3] Waiting for the CPG...")
        await wait_until_ready(client, codebase_hash)

        logger.info("[4] Listing methods...")
        methods = await call(client, "list_methods", {"codebase_hash": codebase_hash, "page_size": 10})
        if not methods.get("success"):
            raise RuntimeError(f"list_methods failed: {methods}")
        logger.info(f"    {methods.get('total')} methods; first page:")
        for method in methods.get("methods", []):
            logger.info(f"      {method['name']} ({method['filename']}:{method['lineNumber']}) node_id={method['node_id']}")

        logger.info("[5] Listing calls...")
        calls = await call(client, "list_calls", {"codebase_hash": codebase_hash, "page_size": 5})
        if not calls.get("success"):
            raise RuntimeError(f"list_calls failed: {calls}")
        logger.info(f"    {calls.get('total')} calls; first page:")
        for item in calls.get("calls", []):
            logger.info(f"      {item.get('caller')} -> {item.get('callee')}")

        logger.info("Sample client completed successfully.")
        return {"codebase_hash": codebase_hash, "methods": methods.get("total"), "calls": calls.get("total")}


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("source_path", nargs="?", default=str(DEFAULT_SOURCE))
    parser.add_argument("--url", default=DEFAULT_URL)
    parser.add_argument("--language", default="c")
    args = parser.parse_args()
    try:
        asyncio.run(run(args.url, args.source_path, args.language))
    except CPGNotReady as e:
        logger.error(str(e))
        sys.exit(2)
    except KeyboardInterrupt:
        sys.exit(130)


if __name__ == "__main__":
    main()
