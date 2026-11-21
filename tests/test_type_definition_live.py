"""Live-Joern semantics for get_type_definition (audit typePair/typeWide/typeImaginary).

Member totals beyond the member cap, empty and forward-only declarations, typedef
aliases, and the per-function/per-file type declarations that are not user
types. Opt-in like tests/test_detectors_live.py:

  CODEBADGER_LIVE_JOERN=1 python -m pytest tests/test_type_definition_live.py -v
"""

import uuid

import pytest

from src.tools.code_browsing_tools import _parse_json_result
from src.tools.queries import QueryLoader
from tests.test_detectors_live import _dexec, _q, pytestmark, server  # noqa: F401 (fixture)

FIELDS = "\n".join(f"    int m{i:02d};" for i in range(1, 22))
SRC = f"""struct Pair {{ int a; int b; }};
struct Wide {{
{FIELDS}
}};
struct Empty {{}};
struct Opaque;
struct Pair;
typedef struct Pair Pair_t;
int use(struct Opaque *o, Pair_t *p, struct Wide *w) {{ return p->a + w->m21; }}
"""


@pytest.fixture(scope="module")
def project(server):
    proj = "types_" + uuid.uuid4().hex[:8]
    _dexec(f"rm -rf /tmp/qh_test/{proj} && mkdir -p /tmp/qh_test/{proj}")
    _dexec(f"cat > /tmp/qh_test/{proj}/t.c", SRC)
    imp = _q(f'importCode(inputPath="/tmp/qh_test/{proj}", projectName="{proj}")', timeout=300)
    assert imp.get("success"), imp.get("stderr", "")[:500]
    return proj


def _types(project, pattern, limit=50, member_limit=20):
    QueryLoader.clear_cache()
    query = QueryLoader.load("type_definition", type_name=pattern, limit=limit, member_limit=member_limit)
    res = _q(f'workspace.setActiveProject("{project}")\n' + query)
    assert res.get("success"), res.get("stderr", "")[:800]
    out = res.get("stdout", "")
    assert "<codebadger_result>" in out, out[-3000:]
    body = out.split("<codebadger_result>", 1)[1].split("</codebadger_result>", 1)[0]
    return _parse_json_result(body.encode().decode("unicode_escape").strip())


def test_wide_struct_reports_the_member_beyond_the_cap(project):
    (wide,) = _types(project, "Wide")["types"]
    assert wide["member_total"] == 21 and len(wide["members"]) == 20
    assert wide["members"][0]["name"] == "m01" and wide["members"][-1]["name"] == "m20"
    (full,) = _types(project, "Wide", member_limit=100)["types"]
    assert full["members"][-1] == {"name": "m21", "type": "int"}


def test_definition_wins_over_a_forward_declaration(project):
    (pair,) = _types(project, "Pair")["types"]
    assert [m["name"] for m in pair["members"]] == ["a", "b"] and not pair["forward_declaration"]


def test_empty_and_forward_declarations_are_listed_and_distinguished(project):
    (empty,) = _types(project, "Empty")["types"]
    (opaque,) = _types(project, "Opaque")["types"]
    assert (empty["member_total"], empty["forward_declaration"]) == (0, False)
    assert (opaque["member_total"], opaque["forward_declaration"]) == (0, True)
    assert _types(project, "Imaginary") == {"total": 0, "types": []}


def test_function_and_file_types_are_not_user_types(project):
    names = {t["name"] for t in _types(project, ".*")["types"]}
    assert names == {"Pair", "Wide", "Empty", "Opaque", "Pair_t"}
    (alias,) = _types(project, "Pair_t")["types"]
    assert alias["aliasOf"] == "Pair"
