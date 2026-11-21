"""Live-Joern check of find_literals (roadmap acceptance: "[retry]" in two files).

Runs the exact queries CodeBrowsingService.find_literals sends. Opt-in like
tests/test_detectors_live.py:

  CODEBADGER_LIVE_JOERN=1 python -m pytest tests/test_find_literals_live.py -v
"""

import uuid
from unittest.mock import MagicMock

import pytest

from src.models import CodebaseInfo
from src.services.code_browsing_service import CodeBrowsingService
from tests.test_detectors_live import _dexec, _q, pytestmark, server  # noqa: F401 (fixture)
from tests.test_list_calls_live import _joern

NET = 'void log_msg(const char *m);\nvoid reconnect(void) { log_msg("[retry] giving up"); log_msg("retry later"); }\n'
DISK = 'void log_msg(const char *m);\nint flush(int n) { if (n > 4096) log_msg("[retry] disk busy"); return 4096; }\n'


@pytest.fixture(scope="module")
def service(server):
    proj = "lits_" + uuid.uuid4().hex[:8]
    _dexec(f"rm -rf /tmp/qh_test/{proj} && mkdir -p /tmp/qh_test/{proj}")
    _dexec(f"cat > /tmp/qh_test/{proj}/net.c", NET)
    _dexec(f"cat > /tmp/qh_test/{proj}/disk.c", DISK)
    imp = _q(f'importCode(inputPath="/tmp/qh_test/{proj}", projectName="{proj}")', timeout=300)
    assert imp.get("success"), imp.get("stderr", "")[:500]
    tracker = MagicMock()
    tracker.get_codebase.return_value = CodebaseInfo(
        codebase_hash="553642871dd4251d", source_type="local", source_path="/x", language="c", cpg_path="/x.cpg")
    return CodeBrowsingService(tracker, _joern(proj))


def test_brackets_are_literal_and_each_location_is_distinct(service):
    r = service.find_literals("553642871dd4251d", text="[retry]")
    assert (r["total"], r["returned"], r["truncated"]) == (2, 2, False)
    assert sorted(l["filename"].rsplit("/", 1)[-1] for l in r["literals"]) == ["disk.c", "net.c"]
    assert {l["method"] for l in r["literals"]} == {"reconnect", "flush"}
    assert len({l["node_id"] for l in r["literals"]}) == 2 and all(l["method_id"] for l in r["literals"])
    # "retry later" has no brackets and is not matched
    assert not any("later" in l["value"] for l in r["literals"])


def test_exact_and_regex_modes(service):
    exact = service.find_literals("553642871dd4251d", text="retry later", match="exact")
    assert [l["value"] for l in exact["literals"]] == ['"retry later"']
    numbers = service.find_literals("553642871dd4251d", text="4096", match="exact")
    assert numbers["total"] == 2 and {l["value"] for l in numbers["literals"]} == {"4096"}
    regex = service.find_literals("553642871dd4251d", text=r'"\[retry\] d.*', match="regex")
    assert [l["method"] for l in regex["literals"]] == ["flush"]


def test_limit_reports_the_rest(service):
    r = service.find_literals("553642871dd4251d", text="retry", limit=1)
    assert (r["total"], r["returned"], r["truncated"]) == (3, 1, True) and "raise limit" in r["hint"]
