"""Live-Joern semantics for find_bounds_checks (audit bounds21/guard/bounds8).

Guards are found inside compound conditions with their branch polarity: an early
return of `i < 0 || i >= size` establishes 0 <= i < size on the fall-through.
Opt-in like tests/test_detectors_live.py:

  CODEBADGER_LIVE_JOERN=1 python -m pytest tests/test_bounds_checks_live.py -v
"""

import uuid

import pytest

from src.tools.code_browsing_tools import _bounds_response, _parse_json_result
from src.tools.queries import QueryLoader
from tests.test_detectors_live import _dexec, _q, pytestmark, server  # noqa: F401 (fixture)

SRC = r"""#include <string.h>
int pick(const int *items, int i, int size) {
    if (i < 0 || i >= size) return 0;
    return items[i];
}
int select_value(const int *items, unsigned i, unsigned size) {
    if (i < size) return items[i];
    return 0;
}
int upper_only(const int *items, int i, int size) {
    if (i >= size) return 0;
    return items[i];
}
int unchecked(const int *items, int i) {
    return items[i];
}
int sum(const int *items, int n) {
    int t = 0;
    for (int k = 0; k < n; k++) t += items[k];
    return t;
}
int at(const int *items, int i) { return items[i]; }
int caller(const int *items, int j) {
    if (j >= 0 && j < 8) return at(items, j);
    return 0;
}
void copy(char *dst, const char *src, unsigned len) {
    if (len > 64) return;
    memcpy(dst, src, len);
}
int nested(const int *items, int i, int size) {
    if (i >= 0) {
        if (i < size) {
            return items[i];
        }
    }
    return -1;
}
"""
# lines: 4 pick | 7 select_value | 12 upper_only | 15 unchecked | 19 sum loop
#        22 at | 24 caller guard | 29 memcpy | 34 nested


@pytest.fixture(scope="module")
def project(server):
    proj = "bounds_" + uuid.uuid4().hex[:8]
    _dexec(f"rm -rf /tmp/qh_test/{proj} && mkdir -p /tmp/qh_test/{proj}")
    _dexec(f"cat > /tmp/qh_test/{proj}/b.c", SRC)
    imp = _q(f'importCode(inputPath="/tmp/qh_test/{proj}", projectName="{proj}")', timeout=300)
    assert imp.get("success"), imp.get("stderr", "")[:500]
    return proj


def _check(project, line):
    QueryLoader.clear_cache()
    query = QueryLoader.load("bounds_checks", filename="b.c", line_num=line)
    res = _q(f'workspace.setActiveProject("{project}")\n' + query)
    assert res.get("success"), res.get("stderr", "")[:800]
    out = res.get("stdout", "")
    assert "<codebadger_result>" in out, out[-3000:]
    body = out.split("<codebadger_result>", 1)[1].split("</codebadger_result>", 1)[0]
    data = _parse_json_result(body.encode().decode("unicode_escape").strip())
    return data if data.get("status") == "unsupported" else _bounds_response(data, f"b.c:{line}")


def test_compound_early_return_establishes_both_bounds(project):
    r = _check(project, 4)
    assert r["status"] == "guarded"
    (g,) = r["guards"]
    assert (g["condition"], g["branch"], g["kind"]) == ("i < 0 || i >= size", "false", "early_return")
    assert g["establishes"] == ["i >= 0", "i < size"]


def test_unsigned_index_needs_only_the_upper_bound(project):
    r = _check(project, 7)
    assert r["status"] == "guarded" and r["target"]["variable_unsigned"]
    assert r["guards"][0]["branch"] == "true" and r["guards"][0]["establishes"] == ["i < size"]


def test_one_sided_and_missing_checks(project):
    assert _check(project, 12)["status"] == "partially_guarded"
    r = _check(project, 15)
    assert r["status"] == "no_guard_found" and r["guards"] == []


def test_loop_condition_guards_its_body(project):
    r = _check(project, 19)
    assert r["guards"][0]["kind"] == "loop" and "k < n" in r["guards"][0]["establishes"]


def test_parameter_checked_in_the_caller(project):
    r = _check(project, 22)
    assert r["status"] == "guarded" and r["guards"] == []
    (g,) = r["caller_guards"]
    assert g["caller"] == "caller" and g["establishes"] == ["j >= 0", "j < 8"]


def test_sized_call_needs_only_an_upper_bound(project):
    r = _check(project, 29)
    assert r["status"] == "guarded" and r["target"]["function"] == "memcpy"
    assert r["guards"][0]["establishes"] == ["len <= 64"]


def test_nested_ifs_combine(project):
    r = _check(project, 34)
    assert r["status"] == "guarded"
    assert sorted(e for g in r["guards"] for e in g["establishes"]) == ["i < size", "i >= 0"]


def test_unsupported_line_is_a_status(project):
    assert _check(project, 2)["status"] == "unsupported"
