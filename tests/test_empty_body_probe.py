"""Tests for the dropped-body diagnostic run after a CPG loads."""

import pytest

from src.tools import core_tools

REPL_ECHO = (
    '\x1b[33mval\x1b[0m \x1b[36mres3\x1b[0m: \x1b[32mString\x1b[0m = '
    '"[{\\"_1\\":true,\\"_2\\":2,\\"_3\\":[\\"f@a.c:1\\",\\"g@a.c:9\\"]},'
    '{\\"_1\\":false,\\"_2\\":8,\\"_3\\":[]}]"\n'
)


class FakeClient:
    def __init__(self, result: dict[str, object]) -> None:
        self.result = result

    def execute_query(self, query: str, timeout: int) -> dict[str, object]:
        return self.result


def test_counts_empty_bodies_from_repl_echo() -> None:
    client = FakeClient({"success": True, "stdout": REPL_ECHO})

    empty = core_tools._count_empty_bodies(client, "h")

    assert empty == {"count": 2, "of": 10, "sample": ["f@a.c:1", "g@a.c:9"]}


@pytest.mark.parametrize(
    "result",
    [{"success": False, "stdout": REPL_ECHO}, {"success": True, "stdout": "error: oops"}],
)
def test_unreadable_probe_returns_nothing(result: dict[str, object]) -> None:
    assert core_tools._count_empty_bodies(FakeClient(result), "h") == {}
