"""Live-Joern semantics for get_call_graph (audit graph_out/recursive/graph_in/graph_amb).

A callee shared by two callers is not a cycle; self and mutual recursion are.
Edges carry call sites separately from definitions, and the per-method fan-out cap
is reported. Opt-in like tests/test_detectors_live.py:

  CODEBADGER_LIVE_JOERN=1 python -m pytest tests/test_call_graph_live.py -v
"""

import uuid

import pytest

from src.tools.code_browsing_tools import _call_graph_response, _parse_json_result
from src.tools.queries import QueryLoader
from tests.test_detectors_live import _dexec, _q, pytestmark, server  # noqa: F401 (fixture)

FANOUT = "\n".join(f"void f{i:02d}(void) {{}}" for i in range(55))
SRC = f"""int leaf(int n) {{ return n + 1; }}
int via(int n) {{ return leaf(n); }}
int root(int n) {{
    leaf(n);
    return via(n);
}}
unsigned countdown(unsigned n) {{ return n ? countdown(n - 1) : 0; }}
int ping(int n);
int pong(int n) {{ return n ? ping(n - 1) : 0; }}
int ping(int n) {{ return pong(n); }}
{FANOUT}
void wide(void) {{ {" ".join(f"f{i:02d}();" for i in range(55))} }}
void callback(void) {{}}
void (*handler)(void) = callback;
"""
SRC_B = "static int leaf(int n) { return n; }\n"


@pytest.fixture(scope="module")
def project(server):
    proj = "cg_" + uuid.uuid4().hex[:8]
    _dexec(f"rm -rf /tmp/qh_test/{proj} && mkdir -p /tmp/qh_test/{proj}")
    _dexec(f"cat > /tmp/qh_test/{proj}/graph.c", SRC)
    _dexec(f"cat > /tmp/qh_test/{proj}/other.c", SRC_B)
    imp = _q(f'importCode(inputPath="/tmp/qh_test/{proj}", projectName="{proj}")', timeout=300)
    assert imp.get("success"), imp.get("stderr", "")[:500]
    return proj


def _graph(project, name, direction="outgoing", depth=3):
    QueryLoader.clear_cache()
    query = QueryLoader.load("call_graph", method_name=name, method_id=-1, depth=depth, direction=direction)
    res = _q(f'workspace.setActiveProject("{project}")\n' + query)
    assert res.get("success"), res.get("stderr", "")[:800]
    out = res.get("stdout", "")
    assert "<codebadger_result>" in out, out[-3000:]
    body = out.split("<codebadger_result>", 1)[1].split("</codebadger_result>", 1)[0]
    return _parse_json_result(body.encode().decode("unicode_escape").strip())


def _edges(resp):
    return {(e["from"], e["to"]): e for e in resp["edges"]}


def test_shared_callee_is_not_a_cycle(project):
    resp = _call_graph_response(_graph(project, "^root$"))
    edges = _edges(resp)
    assert set(edges) == {("root", "leaf"), ("root", "via"), ("via", "leaf")}
    assert not any(e["recursive"] for e in edges.values()) and resp["recursive_edges"] == 0
    # call site (in the caller) is reported apart from the definition
    assert [c["line"] for c in edges[("root", "leaf")]["call_sites"]] == [4]
    assert edges[("root", "leaf")]["definition"]["line"] == 1
    assert "CYCLE" not in resp["summary"] and "RECURSIVE" not in resp["summary"]


def test_self_and_mutual_recursion_are_recursive(project):
    edges = _edges(_call_graph_response(_graph(project, "countdown")))
    assert edges[("countdown", "countdown")]["recursive"]
    mutual = _edges(_call_graph_response(_graph(project, "^ping$")))
    assert mutual[("ping", "pong")]["recursive"] and mutual[("pong", "ping")]["recursive"]


def test_incoming_lists_both_direct_callers(project):
    resp = _call_graph_response(_graph(project, "^via$", direction="incoming", depth=1))
    assert set(_edges(resp)) == {("root", "via")}
    resp = _call_graph_response(_graph(project, "^root$", direction="incoming", depth=1))
    assert resp["edges"] == []


def test_fanout_cap_is_reported(project):
    resp = _call_graph_response(_graph(project, "wide", depth=1))
    assert resp["total"] == 50 and resp["truncated"]
    assert resp["capped_methods"] == [{"method_id": resp["root"]["node_id"], "name": "wide", "shown": 50, "total": 55}]
    assert "CAPPED: wide" in resp["summary"]


def test_ambiguous_and_missing_methods_are_statuses(project):
    amb = _graph(project, "^leaf$")
    assert amb["status"] == "ambiguous" and amb["count"] == 2
    assert _graph(project, "no_such_fn")["status"] == "not_found"


def test_address_taken_callee_lists_its_reference(project):
    resp = _call_graph_response(_graph(project, "callback", direction="incoming", depth=1))
    assert resp["edges"] == []
    sites = resp["address_taken_sites"]
    assert [(a["filename"].rsplit("/", 1)[-1], a["line"]) for a in sites] == [("graph.c", 68)], sites
