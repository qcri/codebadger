"""Comparing one detector's findings between two builds."""

from unittest.mock import MagicMock

from fastmcp import Client, FastMCP

from src.models import CodebaseInfo, Config, QueryResult
from src.services.finding_diff import diff_findings, parse_findings


def issue(n, ptr, file, line, method, confidence="MEDIUM", callers="main"):
    return (f"--- Issue {n} ---\nConfidence: {confidence}\nFree Site: free({ptr})\n"
            f"  Location: {file}:{line} in {method}()\n  Freed Pointer: {ptr}\n\n"
            f"Post-Free Usage(s):\n  [{file}:{line + 2}] use({ptr})\n\n"
            f"Context:\n  File: {file}\n  Called By: {callers}\n\n")


def report(*issues, showing=None):
    body = "Use-After-Free Analysis (Deep Interprocedural)\n" + "=" * 60 + "\n\n" + "".join(issues)
    if showing:
        body += f"(Showing {showing[0]} of {showing[1]} issues. Increase limit to see more.)\n\n"
    return body + f"Total: {len(issues)} potential UAF issue(s) found\n"


def test_identity_survives_line_shifts_confidence_and_context_changes():
    base = parse_findings(report(issue(1, "buf", "a.c", 10, "parse")))
    head = parse_findings(report(issue(1, "buf", "a.c", 42, "parse", confidence="HIGH", callers="main, run")))

    assert base["findings"][0]["fingerprint"] == head["findings"][0]["fingerprint"]
    [f] = diff_findings(base, head)
    assert (f["status"], f["base_confidence"], f["confidence"]) == ("unchanged", "MEDIUM", "HIGH")
    assert f["location"] == "Location: a.c:42 in parse()"


def test_new_and_fixed_findings():
    base = parse_findings(report(issue(1, "buf", "a.c", 10, "parse"), issue(2, "node", "b.c", 5, "drop")))
    head = parse_findings(report(issue(1, "buf", "a.c", 12, "parse"), issue(2, "ctx", "c.c", 7, "init")))
    by_status = {f["status"]: f["title"] for f in diff_findings(base, head)}

    assert by_status == {"new": "Free Site: free(ctx)", "fixed": "Free Site: free(node)",
                         "unchanged": "Free Site: free(buf)"}


def test_a_truncated_report_never_marks_findings_fixed_or_new():
    base = parse_findings(report(issue(1, "buf", "a.c", 10, "parse"), issue(2, "node", "b.c", 5, "drop")))
    head = parse_findings(report(issue(1, "buf", "a.c", 10, "parse"), showing=(1, 2)))
    assert head["complete"] is False
    statuses = sorted(f["status"] for f in diff_findings(base, head))
    assert statuses == ["unchanged", "unconfirmed"]

    statuses = sorted(f["status"] for f in diff_findings(head, base))
    assert statuses == ["unchanged", "unconfirmed"]  # truncated base: can't call node new either


def test_reports_without_issues_parse_as_complete_and_empty():
    parsed = parse_findings("Use-After-Free Analysis\n\nNo potential Use-After-Free issues detected.\n")
    assert parsed == {"findings": [], "complete": True}


async def test_diff_findings_tool_runs_the_detector_on_both_cpgs():
    base_hash, head_hash = "a" * 16, "b" * 16
    reports = {
        base_hash: report(issue(1, "buf", "a.c", 10, "parse"), issue(2, "node", "b.c", 5, "drop")),
        head_hash: report(issue(1, "buf", "a.c", 30, "parse"), issue(2, "ctx", "c.c", 7, "init")),
    }
    tracker = MagicMock()
    tracker.get_codebase.side_effect = lambda h: CodebaseInfo(
        codebase_hash=h, source_type="local", source_path="/x", language="c", cpg_path=f"/cpg/{h}")
    executor = MagicMock()
    executor.execute_query.side_effect = lambda codebase_hash, cpg_path, query, timeout: QueryResult(
        success=True, data=[f"<codebadger_result>\n{reports[codebase_hash]}</codebadger_result>"])
    from src.tools.argument_errors import ArgumentValidationMiddleware
    from src.tools.taint_analysis_tools import register_taint_analysis_tools

    mcp = FastMCP("TestServer")
    mcp.add_middleware(ArgumentValidationMiddleware())
    register_taint_analysis_tools(mcp, {"codebase_tracker": tracker, "query_executor": executor,
                                        "config": Config()})
    async with Client(mcp) as client:
        result = (await client.call_tool("diff_findings", {
            "base_codebase_hash": base_hash, "head_codebase_hash": head_hash,
            "detector": "use_after_free"})).structured_content
        only_new = (await client.call_tool("diff_findings", {
            "base_codebase_hash": base_hash, "head_codebase_hash": head_hash,
            "detector": "use_after_free", "status": "new"})).structured_content
        same = await client.call_tool("diff_findings", {
            "base_codebase_hash": base_hash, "head_codebase_hash": base_hash,
            "detector": "use_after_free"}, raise_on_error=False)

    assert result["summary"] == {"new": 1, "fixed": 1, "unchanged": 1, "unconfirmed": 0}
    assert (result["base"]["complete"], result["head"]["findings"]) == (True, 2)
    assert [f["title"] for f in only_new["findings"]] == ["Free Site: free(ctx)"] and only_new["total"] == 1
    assert same.is_error and same.content[0].text.startswith("VALIDATION_ERROR: base and head are the same CPG")


def _timeout_server(reports):
    """Taint tools whose executor times out for any hash missing from `reports`."""
    tracker = MagicMock()
    tracker.get_codebase.side_effect = lambda h: CodebaseInfo(
        codebase_hash=h, source_type="local", source_path="/x", language="c", cpg_path=f"/cpg/{h}")
    executor = MagicMock()

    def run(codebase_hash, cpg_path, query, timeout, **kwargs):
        if codebase_hash in reports:
            return QueryResult(success=True, data=[f"<codebadger_result>\n{reports[codebase_hash]}</codebadger_result>"])
        return QueryResult(success=False, error="Query timed out after 300s", error_code="TIMEOUT")

    executor.execute_query.side_effect = run
    from src.tools.taint_analysis_tools import register_taint_analysis_tools

    mcp = FastMCP("TestServer")
    register_taint_analysis_tools(mcp, {"codebase_tracker": tracker, "query_executor": executor,
                                        "config": Config()})
    return mcp


import pytest


@pytest.mark.parametrize("timed_out", ["head", "base"])
async def test_a_timed_out_side_fails_the_diff_instead_of_reporting_fixed_or_new(timed_out):
    """Roadmap regression: one base finding + a timed-out head used to report one fixed finding."""
    base_hash, head_hash = "a" * 16, "b" * 16
    ok = base_hash if timed_out == "head" else head_hash
    mcp = _timeout_server({ok: report(issue(1, "buf", "a.c", 10, "parse"))})
    async with Client(mcp) as client:
        result = await client.call_tool("diff_findings", {
            "base_codebase_hash": base_hash, "head_codebase_hash": head_hash,
            "detector": "use_after_free"}, raise_on_error=False)

    assert result.is_error
    assert result.content[0].text.startswith("TIMEOUT: Query timed out after 300s")


async def test_a_timed_out_slice_is_an_error_not_a_successful_analysis():
    mcp = _timeout_server({})
    async with Client(mcp) as client:
        result = await client.call_tool("get_program_slice", {
            "codebase_hash": "c" * 16, "location": "a.c:10"}, raise_on_error=False)

    assert result.is_error
    assert result.content[0].text.startswith("TIMEOUT: ")
