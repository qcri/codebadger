#!/usr/bin/env python3
"""Check commit subjects and PR titles against `type(component): 6-9 words`.

Usage:
  check_commit_msg.py --title "fix(cpg): ..."      check one subject or PR title
  check_commit_msg.py --file .git/COMMIT_EDITMSG   check a commit message file (hook)
  check_commit_msg.py --range origin/main..HEAD    check every commit in a range
"""
import argparse
import re
import subprocess
import sys

TYPES = ("feat", "fix", "perf", "refactor", "test", "docs", "build", "ci", "chore", "revert")
COMPONENTS = (
    "cpg", "query", "taint", "slice", "tools", "detectors", "cache", "db",
    "workers", "server", "config", "docker", "deps", "ci", "docs", "tests",
    "release", "repo",
)
MIN_WORDS, MAX_WORDS = 6, 9
SUBJECT = re.compile(r"^(?P<type>[a-z]+)\((?P<component>[a-z-]+)\)!?: (?P<description>\S.*)$")
AI_TRAILER = re.compile(r"^(co-authored-by:.*(claude|anthropic)|.*generated with \[?claude)", re.I | re.M)


def subject_errors(subject: str) -> list[str]:
    match = SUBJECT.match(subject)
    if not match:
        return ["expected `type(component): description`"]
    errors = []
    if match["type"] not in TYPES:
        errors.append(f"type `{match['type']}` is not one of: {', '.join(TYPES)}")
    if match["component"] not in COMPONENTS:
        errors.append(f"component `{match['component']}` is not one of: {', '.join(COMPONENTS)}")
    description = match["description"]
    words = len(description.split())
    if not MIN_WORDS <= words <= MAX_WORDS:
        errors.append(f"description has {words} words, expected {MIN_WORDS}-{MAX_WORDS}")
    if description[0].isupper() and not description.split()[0].isupper():
        errors.append("description must start lowercase")
    if description.endswith("."):
        errors.append("description must not end with a period")
    return errors


def message_errors(message: str) -> list[str]:
    lines = [l for l in message.splitlines() if not l.startswith("#")]
    subject = lines[0].strip() if lines else ""
    if subject.startswith(("Merge ", "fixup! ", "squash! ")):
        return []
    errors = subject_errors(subject)
    if AI_TRAILER.search("\n".join(lines[1:])):
        errors.append("remove AI co-author or generated-with trailers")
    return errors


def report(label: str, errors: list[str]) -> bool:
    for error in errors:
        print(f"{label}: {error}", file=sys.stderr)
    return not errors


def main() -> int:
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--title")
    group.add_argument("--file")
    group.add_argument("--range")
    args = parser.parse_args()

    if args.title is not None:
        ok = report(repr(args.title), subject_errors(args.title.strip()))
    elif args.file:
        with open(args.file, encoding="utf-8") as handle:
            ok = report("commit message", message_errors(handle.read()))
    else:
        out = subprocess.run(
            ["git", "log", "--no-merges", "--format=%h%x00%B%x01", args.range],
            check=True, capture_output=True, text=True,
        ).stdout
        ok = True
        for entry in filter(None, (e.strip("\n") for e in out.split("\x01"))):
            sha, message = entry.split("\x00", 1)
            ok = report(sha, message_errors(message)) and ok

    if not ok:
        print("See docs/contributing.md#commits-and-pull-requests", file=sys.stderr)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
