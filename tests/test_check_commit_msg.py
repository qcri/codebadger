import importlib.util
from pathlib import Path

import pytest

_spec = importlib.util.spec_from_file_location(
    "check_commit_msg", Path(__file__).resolve().parents[1] / "scripts" / "check_commit_msg.py"
)
check = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(check)


@pytest.mark.parametrize("subject", [
    "fix(cpg): stop excluding bin and target directories everywhere",
    "feat(taint)!: report sources that control the sink",
    "docs(docs): explain CPG cache keys in the configuration guide",
])
def test_valid_subjects(subject):
    assert check.subject_errors(subject) == []


@pytest.mark.parametrize("subject, error", [
    ("fix: stop excluding bin and target directories", "expected"),
    ("add(cpg): stop excluding bin and target directories", "type"),
    ("fix(core): stop excluding bin and target directories", "component"),
    ("fix(cpg): stop excluding bin dirs", "words"),
    ("fix(cpg): stop excluding bin and target dirs for every single language", "words"),
    ("fix(cpg): Stop excluding bin and target directories everywhere", "lowercase"),
    ("fix(cpg): stop excluding bin and target directories everywhere.", "period"),
])
def test_invalid_subjects(subject, error):
    assert any(error in e for e in check.subject_errors(subject))


def test_rejects_ai_trailer_and_skips_merges():
    message = (
        "fix(cpg): stop excluding bin and target directories everywhere\n\n"
        "Co-Authored-By: Claude Opus <noreply@anthropic.com>\n"
    )
    assert any("trailer" in e for e in check.message_errors(message))
    assert check.message_errors("Merge branch 'main' into fix/x\n") == []
