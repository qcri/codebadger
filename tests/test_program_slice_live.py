"""Live-Joern semantics for get_program_slice (program-analysis audit cases).

Builds the audit's fixture C and runs the real program_slice template. Covers
reaching definitions across an unconditional overwrite, control dependence for a
branch that rejoins versus an early return that really guards, and an
authoritative call name. Opt-in like tests/test_detectors_live.py:

  CODEBADGER_LIVE_JOERN=1 python -m pytest tests/test_program_slice_live.py -v
"""

import uuid

import pytest

from src.tools.queries import QueryLoader
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
"""
# Lines: 4 x = read_value() | 5 if (flag) show_value(0) | 6 show_value(x) | 7 x = 7
#        8 show_value(x) | 12 early-return guard | 13 return items[i]


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
    return out.split("<codebadger_result>", 1)[1].split("</codebadger_result>", 1)[0] \
              .encode().decode("unicode_escape").strip()


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
