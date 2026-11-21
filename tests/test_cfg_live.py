"""Live-Joern semantics for get_cfg (program-analysis audit cases cfg1/cfg100/cfg_absent).

A capped CFG must be the connected start of the graph and report its totals; an
absent or ambiguous method is a selection status, not a graph. Opt-in like
tests/test_detectors_live.py:

  CODEBADGER_LIVE_JOERN=1 python -m pytest tests/test_cfg_live.py -v
"""

import uuid

import pytest

from src.tools.code_browsing_tools import _cfg_response, _parse_json_result
from src.tools.queries import QueryLoader
from tests.test_detectors_live import _dexec, _q, pytestmark, server  # noqa: F401 (fixture)

SRC = r"""int pick(const int *items, int i, int size) {
    if (i < 0 || i >= size) return 0;
    return items[i];
}
int twin(int a) { return a; }
"""
SRC_B = "int twin(int a, int b) { return a + b; }\n"


@pytest.fixture(scope="module")
def project(server):
    proj = "cfg_" + uuid.uuid4().hex[:8]
    _dexec(f"rm -rf /tmp/qh_test/{proj} && mkdir -p /tmp/qh_test/{proj}")
    _dexec(f"cat > /tmp/qh_test/{proj}/pick.c", SRC)
    _dexec(f"cat > /tmp/qh_test/{proj}/other.c", SRC_B)
    imp = _q(f'importCode(inputPath="/tmp/qh_test/{proj}", projectName="{proj}")', timeout=300)
    assert imp.get("success"), imp.get("stderr", "")[:500]
    return proj


def _cfg(project, name, limit):
    QueryLoader.clear_cache()
    query = QueryLoader.load("cfg", method_name=name, method_id=-1, max_nodes=limit)
    res = _q(f'workspace.setActiveProject("{project}")\n' + query)
    assert res.get("success"), res.get("stderr", "")[:800]
    out = res.get("stdout", "")
    assert "<codebadger_result>" in out, out[-3000:]
    body = out.split("<codebadger_result>", 1)[1].split("</codebadger_result>", 1)[0]
    return _parse_json_result(body.encode().decode("unicode_escape").strip())


def test_full_cfg_shows_both_exits_of_the_condition(project):
    r = _cfg_response(_cfg(project, "pick", 100))
    assert not r["truncated"] and r["returned"] == r["total"]
    assert r["returned_edges"] == r["total_edges"] > 0
    kinds = {n["kind"] for n in r["nodes"]}
    assert {"METHOD", "METHOD_RETURN"} <= kinds and "BLOCK" not in kinds
    branch_sources = {e["from"] for e in r["edges"] if e["branch"]}
    cond = [n for n in r["nodes"] if n["node_id"] in branch_sources]
    assert any(n["code"] == "i < 0 || i >= size" for n in cond)
    assert r["method"]["name"] == "pick" and r["method"]["filename"].endswith("pick.c")


def test_capped_cfg_reports_totals_and_starts_at_the_entry(project):
    full = _cfg_response(_cfg(project, "pick", 100))
    one = _cfg_response(_cfg(project, "pick", 1))
    assert one["truncated"] and one["returned"] == 1 and one["total"] == full["total"]
    assert one["nodes"][0]["kind"] == "METHOD" and one["nodes"][0]["out_degree"] == 1
    assert one["returned_edges"] == 0 and one["total_edges"] == full["total_edges"]
    assert "TRUNCATED" in one["summary"]
    three = _cfg_response(_cfg(project, "pick", 3))
    assert three["returned_edges"] == 2  # connected prefix: entry -> i -> 0


def test_absent_and_ambiguous_methods_are_selection_statuses(project):
    assert _cfg(project, "no_such_method", 10)["status"] == "not_found"
    amb = _cfg(project, "twin", 10)
    assert amb["status"] == "ambiguous" and amb["count"] == 2
    assert {c["filename"].rsplit("/", 1)[-1] for c in amb["candidates"]} == {"pick.c", "other.c"}
