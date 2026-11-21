"""Live-Joern semantics for resolve_location (roadmap: pick either call on audit.c:23).

Opt-in like tests/test_detectors_live.py:

  CODEBADGER_LIVE_JOERN=1 python -m pytest tests/test_resolve_location_live.py -v
"""

import uuid

import pytest

from src.tools._common import parse_json_result
from src.tools.queries import QueryLoader
from tests.test_detectors_live import _dexec, _q, pytestmark, server  # noqa: F401 (fixture)

SRC = """int read_value(void);
void show_value(int value);
void twice(void) { show_value(9); show_value(9); }
int report(int flag) {
    int x = read_value();

    return x;
}
"""
# line 3: show_value(9) at columns 20 and 35 | line 5: x = read_value() | line 6: blank


@pytest.fixture(scope="module")
def project(server):
    proj = "loc_" + uuid.uuid4().hex[:8]
    _dexec(f"rm -rf /tmp/qh_test/{proj} && mkdir -p /tmp/qh_test/{proj}")
    _dexec(f"cat > /tmp/qh_test/{proj}/audit.c", SRC)
    imp = _q(f'importCode(inputPath="/tmp/qh_test/{proj}", projectName="{proj}")', timeout=300)
    assert imp.get("success"), imp.get("stderr", "")[:500]
    return proj


def _resolve(project, line, column=-1, include_all=False, filename="audit.c"):
    QueryLoader.clear_cache()
    query = QueryLoader.load("resolve_location", filename=filename, line_num=line, column=column,
                             include_all=include_all, max_results=50)
    res = _q(f'workspace.setActiveProject("{project}")\n' + query)
    assert res.get("success"), res.get("stderr", "")[:800]
    out = res.get("stdout", "")
    assert "<codebadger_result>" in out, out[-3000:]
    return parse_json_result(out.split("<codebadger_result>", 1)[1].split("</codebadger_result>", 1)[0].strip())


def test_identical_calls_on_one_line_are_separate_candidates(project):
    r = _resolve(project, 3)
    calls = [(c["code"], c["column"], c["method"]) for c in r["candidates"]]
    assert calls == [("show_value(9)", 20, "twice"), ("show_value(9)", 35, "twice")]
    assert len({c["node_id"] for c in r["candidates"]}) == 2


def test_column_selects_one_call(project):
    for column, expected in [(20, 20), (30, 20), (35, 35), (44, 35)]:
        (first, *_) = _resolve(project, 3, column)["candidates"]
        assert (first["code"], first["column"]) == ("show_value(9)", expected)


def test_real_calls_come_before_operators(project):
    r = _resolve(project, 5)
    assert [(c["name"], c["operator"]) for c in r["candidates"]] == [
        ("read_value", False), ("<operator>.assignment", True)]


def test_include_all_adds_expressions(project):
    kinds = {c["kind"] for c in _resolve(project, 7, include_all=True)["candidates"]}
    assert "RETURN" in kinds and "IDENTIFIER" in kinds
    assert _resolve(project, 7)["candidates"] == []


def test_blank_line_and_unknown_file(project):
    blank = _resolve(project, 6)
    assert blank["files"] and blank["total"] == 0
    assert _resolve(project, 3, filename="nope.c")["files"] == []
