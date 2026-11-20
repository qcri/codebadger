"""Static guards for Scala 3 syntax pitfalls in query templates."""

import pathlib
import re

import pytest

TEMPLATES = sorted(pathlib.Path("src/tools/queries").glob("*.scala"))


@pytest.mark.parametrize("template", TEMPLATES, ids=lambda p: p.name)
def test_no_function_type_wrapped_onto_continuation_line(template):
    """Scala 3 fails with E040 when a parameter's `A =>` type continues on the next line."""
    wrapped = [
        number
        for number, line in enumerate(template.read_text().splitlines(), 1)
        if re.search(r"^\s*\w+\s*:\s*[\w.\[\], ]+=>\s*$", line)
    ]
    assert wrapped == []
