"""Live-Joern check of list_methods' cyclomaticComplexity (audit case `metric`).

McCabe over the CFG: the ternary has two paths and the three-arm switch three.
Opt-in like tests/test_detectors_live.py:

  CODEBADGER_LIVE_JOERN=1 python -m pytest tests/test_method_metrics_live.py -v
"""

import re
import uuid

import pytest

from tests.test_detectors_live import _dexec, _q, pytestmark, server  # noqa: F401 (fixture)

SRC = r"""unsigned countdown(unsigned n) {
    return n ? countdown(n - 1) : 0;
}
int select_value(const int *items, unsigned i, unsigned size) {
    if (i < size) return items[i];
    return 0;
}
int color(int n) {
    switch (n) {
        case 0: return 7;
        case 1: return 8;
        default: return 9;
    }
}
int straight(int a) { return a + 1; }
int guard(int i, int size) {
    if (i < 0 || i >= size) return 0;
    while (i > 0) i--;
    return i;
}
"""
EXPECTED = {"countdown": 2, "select_value": 2, "color": 3, "straight": 1, "guard": 4}


@pytest.fixture(scope="module")
def project(server):
    proj = "metric_" + uuid.uuid4().hex[:8]
    _dexec(f"rm -rf /tmp/qh_test/{proj} && mkdir -p /tmp/qh_test/{proj}")
    _dexec(f"cat > /tmp/qh_test/{proj}/control.c", SRC)
    imp = _q(f'importCode(inputPath="/tmp/qh_test/{proj}", projectName="{proj}")', timeout=300)
    assert imp.get("success"), imp.get("stderr", "")[:500]
    return proj


def test_complexity_is_mccabe_over_the_cfg(project):
    # The same expression list_methods projects (code_browsing_service.list_methods).
    expr = "m.cfgNode.l.map(_._cfgOut.size).filter(_ > 1).map(_ - 1).sum + 1"
    res = _q(f'workspace.setActiveProject("{project}")\n'
             f'cpg.method.isExternal(false).name("{"|".join(EXPECTED)}")'
             f'.map(m => m.name + "=" + ({expr})).l.mkString("<R>", ",", "</R>")')
    assert res.get("success"), res.get("stderr", "")[:800]
    body = re.search(r"<R>(.*?)</R>", res.get("stdout", "")).group(1)
    got = {k: int(v) for k, v in (p.split("=") for p in body.split(","))}
    assert got == EXPECTED
