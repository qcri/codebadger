"""The sample client follows the real status lifecycle."""

import importlib.util
import pathlib
from types import SimpleNamespace

import pytest

_spec = importlib.util.spec_from_file_location(
    "sample_client", pathlib.Path("examples/sample_client.py")
)
sample_client = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sample_client)


class FakeClient:
    def __init__(self, statuses):
        self.statuses = list(statuses)
        self.calls = []

    async def call_tool(self, tool, arguments):
        self.calls.append(tool)
        return SimpleNamespace(structured_content=self.statuses.pop(0))


async def test_waits_through_generating_and_loading_until_ready():
    client = FakeClient([{"status": "generating"}, {"status": "loading"}, {"status": "ready"}])

    status = await sample_client.wait_until_ready(client, "h", attempts=5, delay=0)

    assert status["status"] == "ready"
    assert client.calls == ["get_cpg_status"] * 3


async def test_ready_does_not_require_an_exists_flag():
    """The status reply has no `exists` field; ready alone must be enough."""
    client = FakeClient([{"status": "ready"}])
    assert (await sample_client.wait_until_ready(client, "h", attempts=1, delay=0))["status"] == "ready"


async def test_failed_build_stops_polling_with_its_error_code():
    client = FakeClient([{"status": "failed", "error_code": "OOM", "error": "out of memory"}, {"status": "ready"}])

    with pytest.raises(sample_client.CPGNotReady, match="OOM"):
        await sample_client.wait_until_ready(client, "h", attempts=5, delay=0)
    assert len(client.calls) == 1


async def test_times_out_when_never_ready():
    client = FakeClient([{"status": "generating"}] * 3)
    with pytest.raises(sample_client.CPGNotReady, match="after 3 attempts"):
        await sample_client.wait_until_ready(client, "h", attempts=3, delay=0)


def test_sample_no_longer_calls_retired_tools():
    source = pathlib.Path("examples/sample_client.py").read_text()
    assert "get_codebase_summary" not in source
    assert '"exists"' not in source
    assert ".ping()" not in source  # sessionless MCP 2026 servers do not answer it
