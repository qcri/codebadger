"""Method-level diff between two CPGs."""

from unittest.mock import MagicMock

import pytest
from fastmcp import Client, FastMCP

from src.models import CodebaseInfo, QueryResult
from src.services import cpg_diff
from src.services.code_browsing_service import CodeBrowsingService
from src.services.cpg_diff import comparability_warnings, diff_methods, fetch_fingerprints


def m(name, fp, line=1, filename="a.c", signature="void()"):
    return {"filename": filename, "name": name, "signature": signature, "full_name": name,
            "line": line, "line_end": line + 3, "fingerprint": fp}


def kinds(result):
    return {(c["change"], c["filename"], c["name"]) for c in result["changes"]}


def test_moved_methods_are_unchanged_and_edits_are_modified():
    base = [m("keep", 1, line=10), m("edit", 2, line=20), m("gone", 3)]
    head = [m("keep", 1, line=55), m("edit", 9, line=60), m("new", 4)]
    result = diff_methods(base, head)

    assert kinds(result) == {("modified", "a.c", "edit"), ("removed", "a.c", "gone"), ("added", "a.c", "new")}
    assert result["summary"] == {"added": 1, "removed": 1, "modified": 1, "unchanged": 1}
    edit = next(c for c in result["changes"] if c["name"] == "edit")
    assert (edit["base_line"], edit["head_line"], edit["signature_changed"]) == (20, 60, False)


def test_a_signature_change_is_one_modified_method():
    """Mirrors kilo 69c3ce6 -> 323d93b: editorDelChar() became editorDelChar(void)."""
    result = diff_methods([m("editorDelChar", 1, signature="void()")],
                          [m("editorDelChar", 2, signature="void(void)")])
    assert [(c["change"], c["signature_changed"], c["base_signature"], c["signature"])
            for c in result["changes"]] == [("modified", True, "void()", "void(void)")]


def test_overloads_and_same_named_statics_are_kept_apart():
    base = [m("f", 1, signature="int(int)"), m("f", 2, signature="int(double)"),
            m("init", 5, filename="a.c"), m("init", 6, filename="b.c")]
    head = [m("f", 1, signature="int(int)"), m("f", 7, signature="int(double)"),
            m("init", 5, filename="a.c"), m("init", 8, filename="b.c")]
    result = diff_methods(base, head)

    assert kinds(result) == {("modified", "a.c", "f"), ("modified", "b.c", "init")}
    assert next(c for c in result["changes"] if c["name"] == "f")["signature"] == "int(double)"


def test_ambiguous_leftovers_are_added_and_removed_not_guessed():
    base = [m("f", 1, signature="int(int)"), m("f", 2, signature="int(char)")]
    head = [m("f", 3, signature="long(int)"), m("f", 4, signature="long(char)")]
    assert diff_methods(base, head)["summary"] == {"added": 2, "removed": 2, "modified": 0, "unchanged": 0}


def test_duplicates_pair_in_line_order():
    base = [m("cb", 1, line=10), m("cb", 2, line=50)]
    head = [m("cb", 1, line=12), m("cb", 3, line=52)]
    result = diff_methods(base, head)
    assert [(c["change"], c["base_line"], c["head_line"]) for c in result["changes"]] == [("modified", 50, 52)]


def test_warnings_flag_differences_that_are_not_source_changes():
    known = {"provenance": {"known": True, "build_options": {"defines": []}, "joern_version": "4.0.687"}}
    other = {"provenance": {"known": True, "build_options": {"defines": ["X"]}, "joern_version": "4.0.700"}}
    assert comparability_warnings(known, known, "c", "c") == []
    warnings = comparability_warnings(known, other, "c", "cpp")
    assert len(warnings) == 3 and "build options differ" in warnings[1]
    assert "provenance unknown" in comparability_warnings({}, known, "c", "c")[0]


def _rows(n, start=0):
    return [{"_1": "a.c", "_2": f"f{i}", "_3": "void()", "_4": f"f{i}", "_5": i, "_6": i + 1, "_7": i}
            for i in range(start, start + n)]


def test_fingerprints_are_fetched_in_pages_and_each_page_is_cached(monkeypatch):
    monkeypatch.setattr(cpg_diff, "FINGERPRINT_PAGE", 3)
    executor = MagicMock()
    executor.execute_query.side_effect = [
        QueryResult(success=True, data=_rows(3)), QueryResult(success=True, data=_rows(2, 3)),
    ]
    db = MagicMock()
    db.get_cached_tool_output.return_value = None
    service = CodeBrowsingService(MagicMock(), executor, db_manager=db)

    fetched = fetch_fingerprints(service, "h" * 16, "/cpg")

    assert [r["name"] for r in fetched["methods"]] == ["f0", "f1", "f2", "f3", "f4"]
    queries = [c.kwargs["query"] for c in executor.execute_query.call_args_list]
    assert ".drop(0).take(3)" in queries[0] and ".drop(3).take(3)" in queries[1]
    assert [c.args[2]["offset"] for c in db.cache_tool_output.call_args_list] == [0, 3]


def _server(fingerprints, metadata=None):
    infos = {
        h: CodebaseInfo(codebase_hash=h, source_type="github", source_path="https://github.com/x/kilo",
                        language="c", cpg_path=f"/cpg/{h}", metadata=(metadata or {}).get(h, {}))
        for h in fingerprints
    }
    tracker = MagicMock()
    tracker.get_codebase.side_effect = infos.get
    executor = MagicMock()

    def run(codebase_hash, cpg_path, query, timeout, limit):
        return QueryResult(success=True, data=fingerprints[codebase_hash] if ".drop(0)" in query else [])

    executor.execute_query.side_effect = run
    from src.tools.argument_errors import ArgumentValidationMiddleware
    from src.tools.code_browsing_tools import register_code_browsing_tools

    mcp = FastMCP("TestServer")
    mcp.add_middleware(ArgumentValidationMiddleware())
    register_code_browsing_tools(mcp, {
        "code_browsing_service": CodeBrowsingService(tracker, executor),
        "codebase_tracker": tracker, "query_executor": executor,
    })
    return mcp


BASE, HEAD = "a" * 16, "b" * 16


async def test_diff_tool_pages_filters_and_warns():
    base = [{"_1": "x.c", "_2": f"f{i}", "_3": "void()", "_4": "", "_5": i, "_6": i, "_7": i} for i in range(5)]
    head = [dict(r, _7=r["_7"] + 100) for r in base[:3]] + [
        {"_1": "y.c", "_2": "g", "_3": "void()", "_4": "", "_5": 1, "_6": 1, "_7": 7}]
    mcp = _server({BASE: base, HEAD: head})
    async with Client(mcp) as client:
        first = (await client.call_tool("diff_cpgs", {
            "base_codebase_hash": BASE, "head_codebase_hash": HEAD, "page_size": 2})).structured_content
        removed = (await client.call_tool("diff_cpgs", {
            "base_codebase_hash": BASE, "head_codebase_hash": HEAD, "change": "removed"})).structured_content
        in_y = (await client.call_tool("diff_cpgs", {
            "base_codebase_hash": BASE, "head_codebase_hash": HEAD, "file_pattern": r"^y\."})).structured_content
        same = await client.call_tool("diff_cpgs", {
            "base_codebase_hash": BASE, "head_codebase_hash": BASE}, raise_on_error=False)

    assert first["summary"] == {"added": 1, "removed": 2, "modified": 3, "unchanged": 0}
    assert (first["total"], first["returned"], first["total_pages"], first["truncated"]) == (6, 2, 3, True)
    assert {c["name"] for c in removed["changes"]} == {"f3", "f4"} and removed["total"] == 2
    assert [c["name"] for c in in_y["changes"]] == ["g"]
    assert "provenance unknown" in first["warnings"][0]
    assert same.is_error and same.content[0].text.startswith("VALIDATION_ERROR: base and head are the same CPG")


async def test_missing_cpg_is_a_validation_error():
    mcp = _server({BASE: []})
    async with Client(mcp) as client:
        result = await client.call_tool("diff_cpgs", {
            "base_codebase_hash": BASE, "head_codebase_hash": HEAD}, raise_on_error=False)
    assert result.is_error and result.content[0].text.startswith("VALIDATION_ERROR: CPG not found")


def test_global_fingerprint_ignores_function_definitions():
    from src.tools.queries import QueryLoader
    query = QueryLoader.load("method_fingerprints", offset=0, limit=10)
    assert 'm.name == "<global>" && n.isInstanceOf[io.shiftleft.codepropertygraph.generated.nodes.MethodRef]' in query


def q(name, owner, fp, line, signature="ANY", filename="shapes.py", method_id=None):
    row = m(name, fp, line=line, filename=filename, signature=signature)
    row.update(full_name=f"{filename}:<module>.{owner}.{name}" if owner else name, method_id=method_id)
    return row


def test_swapping_two_classes_does_not_modify_their_methods():
    """Roadmap case diff_py: A.size and B.size swap places with unchanged bodies."""
    base = [q("size", "A", 1, 3, method_id=11), q("size", "B", 2, 9, method_id=12)]
    head = [q("size", "B", 2, 3, method_id=21), q("size", "A", 1, 9, method_id=22)]
    result = diff_methods(base, head)
    assert result["changes"] == [] and result["summary"]["unchanged"] == 2


def test_owner_pairing_still_reports_a_real_edit_and_keeps_overloads_apart():
    base = [q("size", "A", 1, 3), q("size", "B", 2, 9),
            q("area", "B", 5, 12, signature="int(int)"), q("area", "B", 6, 15, signature="int(double)")]
    head = [q("size", "B", 2, 3, method_id=31), q("size", "A", 99, 9, method_id=32),   # A.size edited
            q("area", "B", 5, 12, signature="int(int)"), q("area", "B", 6, 15, signature="int(double)")]
    [change] = diff_methods(base, head)["changes"]

    assert (change["change"], change["head_full_name"], change["head_method_id"]) == (
        "modified", "shapes.py:<module>.A.size", 32)
    assert change["base_full_name"] == "shapes.py:<module>.A.size"


def test_c_duplicate_suffixes_do_not_break_owner_pairing():
    base = [q("init", None, 1, 2, filename="b.c"), q("init", None, 2, 9, filename="b.c")]
    base[0]["full_name"], base[1]["full_name"] = "init", "init<duplicate>0"
    head = [dict(r) for r in base]
    head[0]["full_name"], head[1]["full_name"] = "init<duplicate>3", "init<duplicate>4"
    assert diff_methods(base, head)["summary"]["unchanged"] == 2
