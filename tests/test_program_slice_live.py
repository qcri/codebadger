"""Live-Joern semantics for get_program_slice (program-analysis audit cases).

Builds the audit's fixture C and runs the real program_slice template. Covers
reaching definitions across an unconditional overwrite, control dependence for a
branch that rejoins versus an early return that really guards, and an
authoritative call name. Opt-in like tests/test_detectors_live.py:

  CODEBADGER_LIVE_JOERN=1 python -m pytest tests/test_program_slice_live.py -v
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
int pick(const int *items, int i, int size) {
    if (i < 0 || i >= size) return 0;
    return items[i];
}
void fill(char *buf, int n) {
    int i = n * 2;
    buf[i] = 0;
}
void count(const char *s) {
    int e = 0;
    while (s[e]) e++;
    show_value(e);
}
struct box { int size; };
void set_size(struct box *b, int v) {
    b->size = v;
}
void use_size(struct box *b) {
    show_value(b->size);
}
void log_value(int x) {
    printf("%d", x);
    show_value(x);
}
"""
# Lines: 4 x = read_value() | 5 if (flag) show_value(0) | 6 show_value(x) | 7 x = 7
#        8 show_value(x) | 12 early-return guard | 13 return items[i]
#        16 i = n * 2 | 17 buf[i] = 0 | 21 e++ | 22 show_value(e)
#        26 b->size = v | 29 show_value(b->size) | 32 printf | 33 show_value(x)


@pytest.fixture(scope="module")
def project(server):
    proj = "slice_" + uuid.uuid4().hex[:8]
    _dexec(f"rm -rf /tmp/qh_test/{proj} && mkdir -p /tmp/qh_test/{proj}")
    _dexec(f"cat > /tmp/qh_test/{proj}/audit.c", AUDIT_C)
    imp = _q(f'importCode(inputPath="/tmp/qh_test/{proj}", projectName="{proj}")', timeout=300)
    assert imp.get("success"), imp.get("stderr", "")[:500]
    return proj


def _slice(project, line, call="", direction="backward"):
    QueryLoader.clear_cache()
    query = QueryLoader.load(
        "program_slice", filename="audit.c", line_num=line, use_node_id="false", node_id="",
        call_name=call, max_depth=3, include_backward=str(direction == "backward").lower(),
        include_forward=str(direction == "forward").lower(), include_control_flow="true",
        direction=direction,
    )
    res = _q(f'workspace.setActiveProject("{project}")\n' + query)
    assert res.get("success"), res.get("stderr", "")[:800]
    out = res.get("stdout", "")
    body = out.split("<codebadger_result>", 1)[1].split("</codebadger_result>", 1)[0].strip()
    if not body.startswith("["):  # plain-text failure: undo REPL string escapes
        return body.encode().decode("unicode_escape").strip()
    global LAST_EVIDENCE
    LAST_EVIDENCE = parse_json_result(body)
    return LAST_EVIDENCE["summary"].strip()


def _deps(text):
    return [l.strip() for l in text.splitlines() if l.strip().startswith("[audit.c:")]


def test_backward_slice_ignores_a_rejoining_branch(project):
    text = _slice(project, 6, "show_value")
    assert _deps(text)[0].startswith("[audit.c:4] x: x = read_value()")
    assert "(none: the anchor runs on every path" in text  # line 5's branch rejoins


def test_backward_slice_stops_at_an_unconditional_overwrite(project):
    deps = _deps(_slice(project, 8, "show_value"))
    assert any(d.startswith("[audit.c:7] x: x = 7") for d in deps)
    assert not any("read_value" in d for d in deps)


def test_forward_slice_stops_at_an_unconditional_overwrite(project):
    deps = _deps(_slice(project, 4, "read_value", direction="forward"))
    assert any(d.startswith("[audit.c:6] usage (x): show_value(x)") for d in deps)
    assert not any(d.startswith("[audit.c:8]") for d in deps)


def test_an_early_return_is_a_real_control_dependence(project):
    text = _slice(project, 13)
    controls = text.split("Control Dependencies", 1)[1]
    assert "[audit.c:12] i < 0 || i >= size" in controls
    assert "i < 0 || i >= size" not in "\n".join(_deps(text.split("Control Dependencies", 1)[0]))


def test_a_named_call_is_authoritative(project):
    assert _slice(project, 6, "missing_call").startswith("ANCHOR_NOT_FOUND: no call to 'missing_call' on line 6")


def test_slice_evidence_matches_the_summary(project):
    _slice(project, 8, "show_value")
    ev = LAST_EVIDENCE
    assert ev["anchor"]["code"] == "show_value(x)" and ev["anchor"]["line"] == 8 and not ev["anchor"]["moved"]
    assert [(d["line"], d["variable"], d["kind"]) for d in ev["backward"]] == [(7, "x", "definition")]
    assert ev["control"] == [] and ev["truncated"] is False
    _slice(project, 4, "read_value", direction="forward")
    fwd = LAST_EVIDENCE
    assert fwd["stored_in"] == ["x"]
    # the read value reaches show_value(x) on line 6 and its parameter (declared line 2), not line 8
    assert sorted((p["line"], p["kind"]) for p in fwd["forward"]) == [(2, "passed_to_func"), (6, "usage")]


def test_an_indexed_write_depends_on_its_index(project):
    deps = _deps(_slice(project, 17))
    assert any(d.startswith("[audit.c:16] i: i = n * 2") for d in deps)


def test_an_increment_is_a_definition(project):
    deps = _deps(_slice(project, 22, "show_value"))
    assert any(d.startswith("[audit.c:21] e: e++") for d in deps)


def test_a_literal_argument_is_not_a_dependency(project):
    deps = _deps(_slice(project, 33, "show_value"))
    assert any(d.startswith("[audit.c:32] x: may be modified by") for d in deps)
    assert not any(d.startswith('[audit.c:32] "%d"') for d in deps)


def test_a_field_read_reaches_its_store_in_another_function(project):
    _slice(project, 29, "show_value")
    stores = [(d["line"], d["variable"]) for d in LAST_EVIDENCE["backward"] if d["kind"] == "field_store"]
    assert stores == [(26, "size")]

WIDE_TERMS = 61
WIDE_C = (
    "void show_value(int value);\nvoid wide(int p) {\n"
    + "".join(f"    int v{i} = p;\n" for i in range(WIDE_TERMS))
    + "    int t = " + " + ".join(f"v{i}" for i in range(WIDE_TERMS)) + ";\n"
    + "    show_value(t);\n}\n"
)
WIDE_ANCHOR_LINE = WIDE_TERMS + 4


@pytest.fixture(scope="module")
def wide_project(server):
    proj = "slice_wide_" + uuid.uuid4().hex[:8]
    _dexec(f"rm -rf /tmp/qh_test/{proj} && mkdir -p /tmp/qh_test/{proj}")
    _dexec(f"cat > /tmp/qh_test/{proj}/audit.c", WIDE_C)
    _dexec(f"cat > /tmp/qh_test/{proj}/a.c", "void wide(int p);\nvoid caller(void) { wide(1); }\n")
    imp = _q(f'importCode(inputPath="/tmp/qh_test/{proj}", projectName="{proj}")', timeout=300)
    assert imp.get("success"), imp.get("stderr", "")[:500]
    return proj


def test_truncation_keeps_the_dependencies_nearest_the_anchor(wide_project):
    _slice(wide_project, WIDE_ANCHOR_LINE, "show_value")
    rows = LAST_EVIDENCE["backward"]
    assert LAST_EVIDENCE["truncated"] is True
    assert any(d["variable"] == "t" and d["kind"] == "definition" for d in rows)
    assert not any(d["filename"].endswith("a.c") for d in rows)
