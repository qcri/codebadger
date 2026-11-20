"""Runtime requirements must not float across a major version on rebuild."""

import pathlib
import re

RUNTIME = pathlib.Path("requirements.txt").read_text().split("# Development dependencies")[0]


def _requirements():
    for line in RUNTIME.splitlines():
        line = line.split("#", 1)[0].strip()
        if line:
            yield line


def test_every_runtime_requirement_is_bounded():
    unbounded = [r for r in _requirements() if "==" not in r and not re.search(r"<\s*\d", r)]
    assert unbounded == []


def test_fastmcp_stays_on_the_tested_minor():
    fastmcp = next(r for r in _requirements() if r.startswith("fastmcp"))
    assert "<4.3" in fastmcp
