"""Live-Joern semantics for find_references (roadmap: `x` in control.c excludes x_count and *copy).

Opt-in like tests/test_detectors_live.py:

  CODEBADGER_LIVE_JOERN=1 python -m pytest tests/test_find_references_live.py -v
"""

import uuid

import pytest

from src.tools._common import parse_json_result
from src.tools.queries import QueryLoader
from tests.test_detectors_live import _dexec, _q, pytestmark, server  # noqa: F401 (fixture)

SRC = """int counter;
void emit(int value);
int scratch(void) {
    int x = 2, x_count = 4;
    int *copy = &x_count;
    emit(*copy);
    x += 1;
    emit(x);
    return x;
}
int other(int x) { int *p = &x; counter++; return x; }
void reset(void) { counter = 0; }
"""


@pytest.fixture(scope="module")
def project(server):
    proj = "refs_" + uuid.uuid4().hex[:8]
    _dexec(f"rm -rf /tmp/qh_test/{proj} && mkdir -p /tmp/qh_test/{proj}")
    _dexec(f"cat > /tmp/qh_test/{proj}/control.c", SRC)
    imp = _q(f'importCode(inputPath="/tmp/qh_test/{proj}", projectName="{proj}")', timeout=300)
    assert imp.get("success"), imp.get("stderr", "")[:500]
    return proj


def _refs(project, line, variable):
    QueryLoader.clear_cache()
    query = QueryLoader.load("find_references", filename="control.c", line_num=line, variable=variable,
                             max_results=200)
    res = _q(f'workspace.setActiveProject("{project}")\n' + query)
    assert res.get("success"), res.get("stderr", "")[:800]
    out = res.get("stdout", "")
    assert "<codebadger_result>" in out, out[-3000:]
    return parse_json_result(out.split("<codebadger_result>", 1)[1].split("</codebadger_result>", 1)[0].strip())


def test_local_x_excludes_similar_names_and_other_functions(project):
    r = _refs(project, 8, "x")
    assert r["declaration"]["scope"] == "local" and r["declaration"]["method"] == "scratch"
    rows = [(x["line"], x["access"]) for x in r["references"]]
    assert rows == [(4, "write"), (7, "read_write"), (8, "read"), (9, "read")]
    assert not any("x_count" in x["code"] or "copy" in x["code"] for x in r["references"])
    assert r["counts"] == {"write": 1, "read_write": 1, "read": 2}


def test_parameter_and_address_taken(project):
    r = _refs(project, 11, "x")
    assert r["declaration"]["scope"] == "parameter" and r["declaration"]["method"] == "other"
    assert sorted(x["access"] for x in r["references"]) == ["address_taken", "read"]


def test_global_and_unknown(project):
    g = _refs(project, 11, "counter")
    assert g["declaration"]["scope"] == "global"
    assert [(x["method"], x["access"]) for x in g["references"]] == [("other", "read_write"), ("reset", "write")]
    assert g["declaration"]["line"] == 1
    from_reset = _refs(project, 12, "counter")  # same global, resolved from another function
    assert {x["node_id"] for x in from_reset["references"]} == {x["node_id"] for x in g["references"]}
    missing = _refs(project, 8, "nope")
    assert missing["status"] == "not_found" and "x_count" in missing["variables"]
