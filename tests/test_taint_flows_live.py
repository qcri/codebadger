"""Live-Joern semantics for find_taint_flows when no direct flow exists.

Opt-in like tests/test_detectors_live.py:

  CODEBADGER_LIVE_JOERN=1 python -m pytest tests/test_taint_flows_live.py -v
"""

import uuid

import pytest

from src.tools._common import parse_json_result
from src.tools.queries import QueryLoader
from tests.test_detectors_live import _dexec, _q, pytestmark, server  # noqa: F401 (fixture)

FLOWS_C = r"""struct dict { char **val; int n; };
char *read_input(void);
void escape_value(char *out, const char *in);
void dict_set(struct dict *d, int i) {
    d->val[i] = read_input();
}
void dict_dump(struct dict *d, char *out, int j) {
    escape_value(out, d->val[j]);
}
void encode(char *buf) {
    char *v = read_input();
    int e = 0;
    while (*v) {
        buf[e++] = 'x';
        v++;
    }
}
struct cfg { char *name; };
void cfg_set(struct cfg *c) {
    c->name = read_input();
}
void cfg_encode(struct cfg *c, char *buf) {
    int e = 0;
    while (c->name[e]) {
        buf[e] = 'x';
        e++;
    }
}
"""
# Lines: 5 d->val[i] = read_input() | 8 escape_value(out, d->val[j])
#        11 v = read_input() | 13 while (*v) | 14 buf[e++] = 'x'
#        20 c->name = read_input() | 24 while (c->name[e]) | 25 buf[e] = 'x'


@pytest.fixture(scope="module")
def project(server):
    proj = "flows_" + uuid.uuid4().hex[:8]
    _dexec(f"rm -rf /tmp/qh_test/{proj} && mkdir -p /tmp/qh_test/{proj}")
    _dexec(f"cat > /tmp/qh_test/{proj}/flows.c", FLOWS_C)
    imp = _q(f'importCode(inputPath="/tmp/qh_test/{proj}", projectName="{proj}")', timeout=300)
    assert imp.get("success"), imp.get("stderr", "")[:500]
    return proj


def _flows(project: str, source_line: int, sink_line: int, heap_bridge: bool = True) -> dict:
    QueryLoader.clear_cache()
    query = QueryLoader.load(
        "taint_flows", source_file="flows.c", source_line=source_line, sink_file="flows.c",
        sink_line=sink_line, source_node_id=-1, sink_node_id=-1, max_results=10,
        heap_bridge=heap_bridge,
    )
    res = _q(f'workspace.setActiveProject("{project}")\n' + query)
    assert res.get("success"), res.get("stderr", "")[:800]
    assert "<codebadger_result>" in res["stdout"], res["stdout"][-3000:]
    body = res["stdout"].split("<codebadger_result>", 1)[1].split("</codebadger_result>", 1)[0]
    return parse_json_result(body.strip())


def test_a_heap_field_bridges_two_api_calls(project):
    result = _flows(project, 5, 8)
    bridged = [(b["field"], b["store"]["line"], b["load"]["line"]) for b in result["heap_bridged_flows"]]
    assert result["flows"] == 0
    assert bridged == [("val", 5, 8)]


def test_the_heap_bridge_can_be_disabled(project):
    assert _flows(project, 5, 8, heap_bridge=False)["heap_bridged_flows"] == []


def test_a_loop_bound_by_the_source_is_control_flow(project):
    result = _flows(project, 11, 14)
    controls = [(c["condition"]["line"], c["via"]) for c in result["control_flows"]]
    assert result["flows"] == 0
    assert controls == [(13, "data")]


def test_a_loop_bound_loaded_from_the_heap_is_control_flow(project):
    result = _flows(project, 20, 25)
    controls = [(c["condition"]["line"], c["via"], c.get("field")) for c in result["control_flows"]]
    assert controls == [(24, "heap", "name")]
