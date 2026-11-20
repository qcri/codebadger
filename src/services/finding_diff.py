"""Compare one detector's findings between two builds (e.g. two commits).

Detector reports list issues as "--- Issue N ---" blocks. A finding's identity is
the block's site description (the lines before its first blank line) with issue
numbers, line numbers, and confidence removed, so an unchanged finding keeps its
identity when code above it shifts. A report cut short by its limit is partial:
findings it might have omitted are never reported as fixed.
"""

import hashlib
import re
from typing import Dict, List

_ISSUE = re.compile(r"^--- Issue \d+ ---$", re.MULTILINE)
_TRUNCATED = re.compile(r"\(Showing \d+ of \d+ issues")
_LINE_NO = re.compile(r"(?<=[\w)\]])(:\d+)(?=[\s\]),:]|$)|\b[Ll]ine:? ?\d+\b")


def parse_findings(report: str) -> Dict[str, object]:
    """Split a detector report into findings with stable fingerprints."""
    starts = [m.start() for m in _ISSUE.finditer(report or "")]
    findings = []
    for i, start in enumerate(starts):
        end = starts[i + 1] if i + 1 < len(starts) else len(report)
        block = report[start:end]
        # The final block runs into the report footer (truncation note, totals).
        block = re.split(r"\n\((?:Showing \d+)|\nTotal: ", block)[0].rstrip()
        lines = block.splitlines()[1:]
        confidence = next((l.split(":", 1)[1].strip() for l in lines if l.startswith("Confidence:")), None)
        site = []
        for line in lines:
            if not line.strip():
                if site:
                    break
                continue
            if not line.startswith("Confidence:"):
                site.append(_LINE_NO.sub("", line).strip())
        identity = "\n".join(site)
        findings.append({
            "fingerprint": hashlib.sha256(identity.encode()).hexdigest()[:16],
            "title": site[0] if site else "",
            "confidence": confidence,
            "location": next((l.strip() for l in lines if l.strip().startswith("Location:")), None),
            "text": block[:1500],
        })
    return {"findings": findings, "complete": not _TRUNCATED.search(report or "")}


def diff_findings(base: Dict[str, object], head: Dict[str, object]) -> List[dict]:
    """Classify findings as new, fixed, unchanged, or unconfirmed (partial reports)."""
    base_by = {}
    for f in base["findings"]:
        base_by.setdefault(f["fingerprint"], []).append(f)
    out = []
    for f in head["findings"]:
        matches = base_by.get(f["fingerprint"])
        if matches:
            old = matches.pop(0)
            out.append({**f, "status": "unchanged", "base_confidence": old["confidence"]})
        else:
            # Only a complete base report proves the finding is new.
            out.append({**f, "status": "new" if base["complete"] else "unconfirmed"})
    for leftovers in base_by.values():
        for f in leftovers:
            # Only a complete head report proves the finding is gone.
            out.append({**f, "status": "fixed" if head["complete"] else "unconfirmed",
                        "base_confidence": f["confidence"], "confidence": None})
    order = {"new": 0, "fixed": 1, "unconfirmed": 2, "unchanged": 3}
    out.sort(key=lambda f: (order[f["status"]], f["title"]))
    return out
