"""Tests for QueryExecutor._normalize_query — the run_cpgql_query char-explosion fix.

The bug: a trailing `.toJsonPretty` appended to a `++` chain bound only to the LAST operand,
so its String concatenated into the list as List[String | Char] and printed as raw characters.
The fix parenthesises the base query so the serializer applies to the whole expression, while
keeping the JSON output path (the <codebadger_result> marker is reserved for self-emitting
template blocks)."""

from unittest.mock import MagicMock

from src.services.query_executor import QueryExecutor
from src.defaults import MAX_RESULT_ROWS


def _qe():
    return QueryExecutor(joern_server_manager=MagicMock(), config={}, coordinator=MagicMock())


def test_plus_chain_is_parenthesised_as_a_whole():
    """The regression: the entire `++` chain must be wrapped before .take/.toJsonPretty,
    so the serializer can't bind to just the last operand."""
    qe = _qe()
    out = qe._normalize_query("cpg.x.l ++ cpg.y.name.l", None)
    # trailing .l stripped, whole expression parenthesised, then capped + serialized
    assert out.startswith("({\ncpg.x.l ++ cpg.y.name\n}: Any) match {"), out
    assert ".take(" in out and ".toJsonPretty" in out
    # the bug signature must NOT appear: a serializer bound to the chain's last operand
    assert "name.toJsonPretty" not in out.replace(" ", "")


def test_simple_query_parenthesised_and_serialized():
    qe = _qe()
    out = qe._normalize_query("cpg.method.name(\"f\").code.l", None)
    assert out.startswith("({\ncpg.method.name(\"f\").code\n}: Any) match {")
    assert "__cbIt.iterator.take(" in out and ".toList.toJsonPretty" in out


def test_scalar_results_are_decided_at_run_time():
    """`.size` and `.id.head` are single values: the cap applies only to a collection,
    and a scalar comes back in its own envelope instead of `(x).take(n)` (audit raw_first)."""
    qe = _qe()
    for query in ("cpg.method.size", 'cpg.method.name("leaf").id.head'):
        out = qe._normalize_query(query, None)
        assert out.startswith(f"({{\n{query}\n}}: Any) match {{"), out
        assert "case __cbIt: IterableOnce[?] =>" in out
        assert 'case __cbValue => "<codebadger_scalar>' in out
        assert f"({query}).take(" not in out


def test_scalar_envelope_parses_to_a_typed_value():
    """Audit raw_count: a count is the number 2, not REPL text."""
    qe = _qe()
    repl = 'val res39: String = """<codebadger_scalar>\n[2]\n</codebadger_scalar>"""'
    assert qe._parse_output(repl) == 2
    assert qe._parse_output('val r: String = """<codebadger_scalar>\n[107374182400]\n</codebadger_scalar>"""') == 107374182400
    assert qe._parse_output('val r: String = """<codebadger_scalar>\n["leaf"]\n</codebadger_scalar>"""') == "leaf"


def test_unbounded_query_capped():
    qe = _qe()
    out = qe._normalize_query("cpg.method.l", None)
    assert f".take({MAX_RESULT_ROWS + 1})" in out


def test_huge_explicit_limit_clamped():
    qe = _qe()
    out = qe._normalize_query("cpg.method", 1_000_000_000)
    assert f".take({MAX_RESULT_ROWS + 1})" in out


def test_marker_block_passthrough_unchanged():
    """Self-emitting analysis templates (own <codebadger_result> envelope) are left as-is."""
    qe = _qe()
    block = '{ val x = cpg.method.l; "<codebadger_result>\\n" + x.mkString + "\\n</codebadger_result>" }'
    assert qe._normalize_query(block, None) == block


def test_block_with_tojson_tail_passthrough():
    qe = _qe()
    block = "{ cpg.method.l.toJsonPretty }"
    assert qe._normalize_query(block, None) == block


def test_trailing_tojsonpretty_is_stripped_then_reapplied_once():
    qe = _qe()
    out = qe._normalize_query("cpg.method.name(\"f\").toJsonPretty", None)
    # exactly one .toJsonPretty, applied to the parenthesised base
    assert out.count(".toJsonPretty") == 1
    assert out.startswith("({\ncpg.method.name(\"f\")\n}: Any) match {")


def test_single_line_repl_json_is_decoded():
    """An empty or short result prints as `val r: String = "[]"`, not triple-quoted."""
    qe = _qe()
    assert qe._parse_output('val res8: String = "[]"') == []
    assert qe._parse_output('val res9: String = "[\\"a\\"]"') == ["a"]


def test_multi_statement_query_is_wrapped_in_a_block() -> None:
    out = _qe()._normalize_query("val ms = cpg.method.l; ms.size", None)
    assert out.startswith("({\nval ms = cpg.method.l; ms.size\n}: Any) match {"), out
