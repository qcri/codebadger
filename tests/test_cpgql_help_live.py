"""Every query the CPGQL syntax help publishes must run (audit cases help/raw_count/raw_first).

Collects each `cpg.` example and each formatting fragment from get_cpgql_syntax_help,
normalizes it exactly as run_cpgql_query does (QueryExecutor._plan_query), runs it on
live Joern and decodes it with _parse_output. Opt-in like tests/test_detectors_live.py:

  CODEBADGER_LIVE_JOERN=1 python -m pytest tests/test_cpgql_help_live.py -v
"""

import asyncio
import uuid
from unittest.mock import MagicMock

import pytest
from fastmcp import Client, FastMCP

from src.services.query_executor import QueryExecutor
from src.tools.code_browsing_tools import register_code_browsing_tools
from tests.test_detectors_live import _dexec, _q, pytestmark, server  # noqa: F401 (fixture)

SRC = "int leaf(int n) { return n + 1; }\nint via(int n) { return leaf(n); }\n"


def _help_queries():
    mcp = FastMCP("t")
    register_code_browsing_tools(mcp, {"code_browsing_service": MagicMock(), "codebase_tracker": MagicMock(),
                                       "query_executor": MagicMock()})

    async def fetch():
        async with Client(mcp) as client:
            return (await client.call_tool("get_cpgql_syntax_help", {})).structured_content

    out = []

    def walk(node):
        if isinstance(node, dict):
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)
        elif isinstance(node, str):
            text = node.split("  #")[0].strip()
            if text.startswith("cpg."):
                out.append(text)
            elif text.startswith(".") and "(" not in text:  # formatting fragment: .size, .head
                out.append('cpg.method.name("leaf").id' + text)
            elif text.startswith("."):  # filter fragment
                out.append("cpg.method" + text + ".name.l")

    walk(asyncio.run(fetch()))
    return list(dict.fromkeys(out))


@pytest.fixture(scope="module")
def project(server):
    proj = "help_" + uuid.uuid4().hex[:8]
    _dexec(f"rm -rf /tmp/qh_test/{proj} && mkdir -p /tmp/qh_test/{proj}")
    _dexec(f"cat > /tmp/qh_test/{proj}/a.c", SRC)
    imp = _q(f'importCode(inputPath="/tmp/qh_test/{proj}", projectName="{proj}")', timeout=300)
    assert imp.get("success"), imp.get("stderr", "")[:500]
    return proj


def _run(project, query):
    qe = QueryExecutor(joern_server_manager=MagicMock(), config={}, coordinator=MagicMock())
    normalized, _ = qe._plan_query(query, None)
    res = _q(f'workspace.setActiveProject("{project}")\n' + normalized)
    assert res.get("success"), res.get("stderr", "")[:800]
    stdout = res.get("stdout", "")
    assert qe._compile_error(stdout) is None, f"{query}: {stdout[-1500:]}"
    return qe._parse_output(stdout)


def test_every_published_example_runs(project):
    queries = _help_queries()
    assert len(queries) > 20
    for query in queries:
        _run(project, query)


def test_formatting_idioms_return_what_the_help_says(project):
    leaf_id = _run(project, 'cpg.method.name("leaf").id.l')
    assert isinstance(leaf_id, list) and len(leaf_id) == 1
    assert _run(project, 'cpg.method.name("leaf|via").size') == 2
    assert _run(project, 'cpg.method.name("leaf").id.head') == leaf_id[0]
    assert _run(project, 'cpg.method.name("leaf").id.headOption') == leaf_id
    assert _run(project, 'cpg.method.name("nope").id.headOption') == []
