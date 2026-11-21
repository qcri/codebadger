"""Method-level diff between two CPGs (e.g. two commits or two builds of a project).

Each internal method gets a fingerprint over its signature and the code of its own
CFG nodes. Line numbers are not part of it, so a method that only moved is
unchanged. A file's <global> pseudo-method holds a METHOD_REF per function defined
in the file; those are left out of its fingerprint, so adding, removing, or editing
a function does not mark <global> modified.
"""

import re
from collections import defaultdict
from typing import Any, Dict, List, Optional, Tuple

from ..tools.queries import QueryLoader

FINGERPRINT_PAGE = 1500  # rows per query; keeps each cached page under the cache cap
MAX_METHODS = 200_000


def fetch_fingerprints(service, codebase_hash: str, cpg_path: str) -> Dict[str, Any]:
    """All internal-method fingerprints of one CPG, fetched in cached pages.

    Returns {"success": True, "methods": [...]} or a {"success": False, ...} failure.
    """
    methods: List[dict] = []
    offset = 0
    while True:
        def run(offset=offset):
            result = service.query_executor.execute_query(
                codebase_hash=codebase_hash, cpg_path=cpg_path,
                query=QueryLoader.load("method_fingerprints", offset=offset, limit=FINGERPRINT_PAGE),
                timeout=180, limit=FINGERPRINT_PAGE,
            )
            if not result.success:
                return {"success": False, "error": result.error,
                        "error_code": result.error_code or "QUERY_ERROR"}
            rows = [
                {"filename": r.get("_1"), "name": r.get("_2"), "signature": r.get("_3"),
                 "full_name": r.get("_4"), "line": r.get("_5"), "line_end": r.get("_6"),
                 "fingerprint": r.get("_7"), "method_id": r.get("_8")}
                for r in (result.data or []) if isinstance(r, dict)
            ]
            return {"success": True, "rows": rows}

        page = service._get_cached_or_execute(
            "method_fingerprints", codebase_hash, {"offset": offset, "v": 3}, run,
        )
        if not page.get("success"):
            return page
        methods.extend(page["rows"])
        if len(page["rows"]) < FINGERPRINT_PAGE:
            return {"success": True, "methods": methods}
        offset += FINGERPRINT_PAGE
        if offset >= MAX_METHODS:
            return {"success": False, "error_code": "QUERY_ERROR",
                    "error": f"CPG {codebase_hash} has more than {MAX_METHODS} methods; "
                             "narrow the builds (include_globs) before diffing"}


def _qualified(m: dict) -> str:
    """Owner-qualified identity; drops c2cpg's build-order <duplicate>N suffix."""
    return re.sub(r"<duplicate>\d+", "", m.get("full_name") or m["name"])


def _pair_by_owner(base: List[dict], head: List[dict]):
    """Pair methods whose owner-qualified name and signature are unique on both sides."""
    def unique(methods):
        keys = defaultdict(list)
        for m in methods:
            keys[(_qualified(m), m["signature"])].append(m)
        return {k: v[0] for k, v in keys.items() if len(v) == 1}

    b, h = unique(base), unique(head)
    matched = [k for k in b if k in h]
    pairs = [(b[k], h[k]) for k in matched]
    used_b = {id(b[k]) for k in matched}
    used_h = {id(h[k]) for k in matched}
    return (pairs, [m for m in base if id(m) not in used_b], [m for m in head if id(m) not in used_h])


def _pair(base: List[dict], head: List[dict]) -> Tuple[List[Tuple[dict, dict]], List[dict], List[dict]]:
    """Pair methods sharing a file and name: by owner-qualified identity first, then
    by signature and line order for what remains."""
    owner_pairs, base, head = _pair_by_owner(base, head)
    pairs, base_left, head_left = list(owner_pairs), [], []
    by_sig_head = defaultdict(list)
    for m in head:
        by_sig_head[m["signature"]].append(m)
    for sig_list in by_sig_head.values():
        sig_list.sort(key=lambda m: m["line"])
    for m in sorted(base, key=lambda m: m["line"]):
        candidates = by_sig_head.get(m["signature"])
        if candidates:
            pairs.append((m, candidates.pop(0)))
        else:
            base_left.append(m)
    head_left = [m for lst in by_sig_head.values() for m in lst]
    # A single unmatched method on each side is the same method with a new signature.
    if len(base_left) == 1 and len(head_left) == 1:
        pairs.append((base_left[0], head_left[0]))
        base_left, head_left = [], []
    return pairs, base_left, head_left


def diff_methods(base: List[dict], head: List[dict]) -> Dict[str, Any]:
    """Classify every method as added, removed, modified, or unchanged."""
    groups = defaultdict(lambda: ([], []))
    for m in base:
        groups[(m["filename"], m["name"])][0].append(m)
    for m in head:
        groups[(m["filename"], m["name"])][1].append(m)

    changes, unchanged = [], 0
    for (filename, name), (b, h) in groups.items():
        pairs, removed, added = _pair(b, h)
        for old, new in pairs:
            if old["fingerprint"] == new["fingerprint"] and old["signature"] == new["signature"]:
                unchanged += 1
                continue
            changes.append({
                "change": "modified", "filename": filename, "name": name,
                "signature": new["signature"], "base_signature": old["signature"],
                "signature_changed": old["signature"] != new["signature"],
                "base_line": old["line"], "head_line": new["line"],
                "base_full_name": old.get("full_name"), "head_full_name": new.get("full_name"),
                "base_method_id": old.get("method_id"), "head_method_id": new.get("method_id"),
            })
        for old in removed:
            changes.append({"change": "removed", "filename": filename, "name": name,
                            "signature": old["signature"], "base_line": old["line"],
                            "base_full_name": old.get("full_name"), "base_method_id": old.get("method_id")})
        for new in added:
            changes.append({"change": "added", "filename": filename, "name": name,
                            "signature": new["signature"], "head_line": new["line"],
                            "head_full_name": new.get("full_name"), "head_method_id": new.get("method_id")})
    changes.sort(key=lambda c: (c["filename"] or "", c["name"] or "", c["change"]))
    summary = {kind: sum(1 for c in changes if c["change"] == kind)
               for kind in ("added", "removed", "modified")}
    summary["unchanged"] = unchanged
    return {"changes": changes, "summary": summary}


def comparability_warnings(base_meta: dict, head_meta: dict, base_lang: str, head_lang: str) -> List[str]:
    """Reasons the two graphs may differ for reasons other than source changes."""
    warnings = []
    if (base_lang or "").lower() != (head_lang or "").lower():
        warnings.append(f"languages differ ({base_lang} vs {head_lang})")
    base_p, head_p = base_meta.get("provenance") or {}, head_meta.get("provenance") or {}
    if not base_p.get("known") or not head_p.get("known"):
        warnings.append("provenance unknown for at least one CPG; build options may differ")
    else:
        if base_p.get("build_options") != head_p.get("build_options"):
            warnings.append("build options differ; some changes may come from the build, not the source")
        if base_p.get("joern_version") != head_p.get("joern_version"):
            warnings.append(
                f"Joern versions differ ({base_p.get('joern_version')} vs {head_p.get('joern_version')})"
            )
    return warnings
