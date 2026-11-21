"""Live-Joern semantics for get_variable_flow (program-analysis audit cases).

Covers declaration-based alias resolution (`&x_count` is not an alias of `x`),
reaching definitions backward, the lifetime of one assigned value forward, and an
unknown variable. Opt-in like tests/test_detectors_live.py:

  CODEBADGER_LIVE_JOERN=1 python -m pytest tests/test_variable_flow_live.py -v
"""

import uuid

import pytest

from src.tools._common import parse_json_result
from src.tools.queries import QueryLoader

LAST_EVIDENCE: dict = {}
from tests.test_detectors_live import _dexec, _q, pytestmark, server  # noqa: F401 (fixture)

AUDIT_C = r"""int read_value(void);
void show_value(int value);
int report(int flag) {
    int x = read_value();
    if (flag) { show_value(0); }
    show_value(x);
    x = 7;
    show_value(x);
    return x;
}
"""
# 4 x = read_value() | 6 show_value(x) | 7 x = 7 | 8 show_value(x) | 9 return x

CONTROL_C = r"""void emit(int value);
int scratch(void) {
    int x = 2, x_count = 4;
    int *copy = &x_count;
    emit(*copy);
    emit(x);
    return x;
}
int main(void) { scratch(); return 0; }
"""


@pytest.fixture(scope="module")
def project(server):
    proj = "vflow_" + uuid.uuid4().hex[:8]
    _dexec(f"rm -rf /tmp/qh_test/{proj} && mkdir -p /tmp/qh_test/{proj}")
    _dexec(f"cat > /tmp/qh_test/{proj}/audit.c", AUDIT_C)
    _dexec(f"cat > /tmp/qh_test/{proj}/control.c", CONTROL_C)
    imp = _q(f'importCode(inputPath="/tmp/qh_test/{proj}", projectName="{proj}")', timeout=300)
    assert imp.get("success"), imp.get("stderr", "")[:500]
    return proj


def _flow(project, filename, line, variable, direction):
    QueryLoader.clear_cache()
    query = QueryLoader.load("variable_flow", filename=filename, line_num=line,
                             variable=variable, direction=direction, max_results=50)
    res = _q(f'workspace.setActiveProject("{project}")\n' + query)
    assert res.get("success"), res.get("stderr", "")[:800]
    out = res.get("stdout", "")
    assert "<codebadger_result>" in out, out[-3000:]
    body = out.split("<codebadger_result>", 1)[1].split("</codebadger_result>", 1)[0].strip()
    if not body.startswith("["):  # plain-text failure: undo REPL string escapes
        return body.encode().decode("unicode_escape").strip()
    global LAST_EVIDENCE
    LAST_EVIDENCE = parse_json_result(body)
    return LAST_EVIDENCE["summary"].strip()


def _rows(text, filename):
    return [l.strip() for l in text.splitlines() if l.strip().startswith(f"[{filename}:")]


def test_address_of_a_similar_name_is_not_an_alias(project):
    text = _flow(project, "control.c", 6, "x", "backward")
    assert "Aliases" not in text and "copy" not in text and "x_count" not in text
    assert any("emit(x)" in r for r in _rows(text, "control.c"))


def test_a_read_passed_to_a_call_is_not_a_redefinition(project):
    # Joern treats emit(x)'s argument as a possible definition; the value of x at
    # the return is still the one from `x = 2`.
    back = _rows(_flow(project, "control.c", 7, "x", "backward"), "control.c")
    assert any(r.startswith("[control.c:3] x = 2 (assignment) [reaches line 7]") for r in back)
    assert not any("emit(x)" in r and "[reaches" in r for r in back)
    fwd = _rows(_flow(project, "control.c", 3, "x", "forward"), "control.c")
    assert any(r.startswith("[control.c:7] return x") and "[value from line 3]" in r for r in fwd)


def test_backward_marks_only_the_reaching_definition(project):
    rows = _rows(_flow(project, "audit.c", 8, "x", "backward"), "audit.c")
    assert any(r.startswith("[audit.c:7] x = 7 (assignment) [reaches line 8]") for r in rows)
    read = [r for r in rows if "read_value" in r]
    assert read and "[reaches" not in read[0]


def test_forward_separates_the_value_from_the_name(project):
    rows = _rows(_flow(project, "audit.c", 4, "x", "forward"), "audit.c")
    assert any(r.startswith("[audit.c:6] show_value(x)") and "[value from line 4]" in r for r in rows)
    assert any(r.startswith("[audit.c:8] show_value(x)") and "[name only: a later value]" in r for r in rows)
    assert any(r.startswith("[audit.c:9] return x") for r in rows)


def test_unknown_variable_is_distinguished(project):
    text = _flow(project, "audit.c", 6, "nope", "backward")
    assert text.startswith("VARIABLE_NOT_FOUND:")
    assert "x" in text.split("Variables in report:", 1)[1]


def test_line_outside_any_method_is_anchor_not_found(project):
    assert _flow(project, "audit.c", 80, "x", "backward").startswith("ANCHOR_NOT_FOUND:")


def test_flow_rows_carry_kind_and_note(project):
    _flow(project, "audit.c", 4, "x", "forward")
    ev = LAST_EVIDENCE
    assert ev["variable"] == "x" and ev["method"]["name"] == "report" and ev["aliases"] == []
    rows = {(r["line"], r["kind"]): r["note"] for r in ev["rows"]}
    assert rows[(6, "use_in_call")] == "value from line 4"
    assert rows[(8, "use_in_call")] == "name only: a later value"
    assert ev["total"] == ev["returned"] == len(ev["rows"]) and ev["truncated"] is False
