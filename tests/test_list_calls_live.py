"""Live-Joern check that list_calls keeps identical same-line calls (audit `calls_twice`).

Runs the exact query CodeBrowsingService.list_calls sends. Opt-in like
tests/test_detectors_live.py:

  CODEBADGER_LIVE_JOERN=1 python -m pytest tests/test_list_calls_live.py -v
"""

import json
import re
import uuid
from unittest.mock import MagicMock

import pytest

from src.models import CodebaseInfo, QueryResult
from src.services.code_browsing_service import CodeBrowsingService
from tests.test_detectors_live import _dexec, _q, pytestmark, server  # noqa: F401 (fixture)

SRC = r"""void show_value(int value);
void twice(void) { show_value(9); show_value(9); }
"""


@pytest.fixture(scope="module")
def project(server):
    proj = "calls_" + uuid.uuid4().hex[:8]
    _dexec(f"rm -rf /tmp/qh_test/{proj} && mkdir -p /tmp/qh_test/{proj}")
    _dexec(f"cat > /tmp/qh_test/{proj}/a.c", SRC)
    imp = _q(f'importCode(inputPath="/tmp/qh_test/{proj}", projectName="{proj}")', timeout=300)
    assert imp.get("success"), imp.get("stderr", "")[:500]
    return proj


def _joern(project):
    """An executor stand-in that runs each query on the live Joern server."""
    def run(query, **_):
        tail = ".size" if query.endswith(".size") else ".toJson"
        expr = query[:-len(".size")] + ".size.toString" if tail == ".size" else query + ".toJson"
        # A multi-line string prints raw (triple-quoted), so JSON escapes survive.
        res = _q(f'workspace.setActiveProject("{project}")\n"<R>\\n" + ({expr}) + "\\n</R>"')
        assert res.get("success"), res.get("stderr", "")[:800]
        body = re.search(r"<R>\s*(.*?)\s*</R>", res["stdout"], re.DOTALL).group(1)
        data = int(body) if tail == ".size" else json.loads(body)
        return QueryResult(success=True, data=data, row_count=1)
    executor = MagicMock()
    executor.execute_query.side_effect = run
    return executor


def test_identical_same_line_calls_are_two_rows(project):
    tracker = MagicMock()
    tracker.get_codebase.return_value = CodebaseInfo(
        codebase_hash="553642871dd4251d", source_type="local", source_path="/x", language="c", cpg_path="/x.cpg")
    service = CodeBrowsingService(tracker, _joern(project))
    result = service.list_calls("553642871dd4251d", caller_pattern="twice", callee_pattern="show_value")
    assert result["total"] == 2 and result["returned"] == 2
    ids = {c["node_id"] for c in result["calls"]}
    cols = sorted(c["columnNumber"] for c in result["calls"])
    assert len(ids) == 2 and cols[0] < cols[1]
    assert {c["lineNumber"] for c in result["calls"]} == {2}
    assert len({c["caller_id"] for c in result["calls"]}) == 1
