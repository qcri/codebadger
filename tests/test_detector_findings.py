"""Detector replies carry their issues as structured findings alongside the summary."""

from src.services.finding_diff import parse_findings
from src.tools.taint_analysis_tools import _detector_response

REPORT = """Use-After-Free Analysis
============================================================
(Showing 2 of 5 issues. Increase limit to see more.)

--- Issue 1 ---
Free Site: free(buf)
  Location: src/a.c:10 in process()
Post-Free Usage(s):
  [src/a.c:12] use(buf)
Confidence: HIGH

--- Issue 2 ---
Free Site: free(p)
  Location: src/b.c:40 in other()
Post-Free Usage(s):
  [src/b.c:44] p->x
Confidence: MEDIUM

Total: 2 issues
"""


def test_findings_mirror_the_issue_blocks():
    r = _detector_response(REPORT)
    assert r["summary"] == REPORT
    assert (r["total"], r["returned"], r["truncated"]) == (5, 2, True)
    first, second = r["findings"]
    assert first["title"] == "Free Site: free(buf)" and first["confidence"] == "HIGH"
    assert first["sites"] == [{"filename": "src/a.c", "line": 10}, {"filename": "src/a.c", "line": 12}]
    assert second["sites"][-1] == {"filename": "src/b.c", "line": 44}
    # the same fingerprints diff_findings uses to match across builds
    assert [f["fingerprint"] for f in r["findings"]] == [f["fingerprint"] for f in parse_findings(REPORT)["findings"]]


def test_a_clean_report_has_no_findings():
    r = _detector_response("Use-After-Free Analysis\n====\nNo issues found.\n")
    assert (r["findings"], r["total"], r["returned"], r["truncated"]) == ([], 0, 0, False)
