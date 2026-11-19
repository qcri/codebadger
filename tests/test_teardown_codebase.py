from unittest.mock import MagicMock
import json

import pytest
from fastmcp import Client, FastMCP

from src.models import CodebaseInfo
import src.tools.core_tools as core_tools


HASH = "abcdef0123456789"


def _playground(tmp_path, monkeypatch):
    root = tmp_path / "playground"
    for sub, name in (("cpgs", "cpg.bin"), ("codebases", "source.c")):
        directory = root / sub / HASH
        directory.mkdir(parents=True)
        (directory / name).write_bytes(b"artifact")
    monkeypatch.setattr(core_tools, "_playground_path", lambda: str(root))
    return root


def test_teardown_reports_remaining_files_and_keeps_database_row(tmp_path, monkeypatch):
    root = _playground(tmp_path, monkeypatch)
    db = MagicMock()
    rmtree = core_tools.shutil.rmtree

    def fail_cpg(path):
        if path.endswith("/cpgs/" + HASH):
            raise OSError("busy")
        rmtree(path)

    monkeypatch.setattr(core_tools.shutil, "rmtree", fail_cpg)
    with pytest.raises(core_tools._TeardownError) as error:
        core_tools._teardown_codebase({"db_manager": db}, HASH)

    assert error.value.remaining_artifacts == ["cpgs"]
    assert error.value.freed_bytes == len(b"artifact")
    assert (root / "cpgs" / HASH).exists()
    assert not (root / "codebases" / HASH).exists()
    db.delete_codebase.assert_not_called()


def test_teardown_reports_database_failure_and_allows_retry(tmp_path, monkeypatch):
    root = _playground(tmp_path, monkeypatch)
    db = MagicMock()
    db.delete_codebase.side_effect = [False, True]
    services = {"db_manager": db}

    with pytest.raises(core_tools._TeardownError) as error:
        core_tools._teardown_codebase(services, HASH)

    assert error.value.remaining_artifacts == ["database_record"]
    assert error.value.freed_bytes == 2 * len(b"artifact")
    assert not (root / "cpgs" / HASH).exists()
    assert not (root / "codebases" / HASH).exists()

    assert core_tools._teardown_codebase(services, HASH) == 0
    assert db.delete_codebase.call_count == 2


@pytest.mark.asyncio
async def test_remove_cpg_returns_partial_result(monkeypatch):
    tracker = MagicMock()
    tracker.get_codebase.return_value = CodebaseInfo(
        codebase_hash=HASH, source_type="local", source_path="/src", language="c",
        cpg_path="/tmp/cpg.bin",
    )
    monkeypatch.setattr(
        core_tools, "_teardown_codebase",
        MagicMock(side_effect=core_tools._TeardownError(1024, ["cpgs"])),
    )
    mcp = FastMCP("TestServer")
    core_tools.register_core_tools(mcp, {"codebase_tracker": tracker})

    async with Client(mcp) as client:
        result = await client.call_tool(
            "remove_cpg", {"codebase_hash": HASH, "delete_files": True}
        )
    response = json.loads(result.content[0].text)

    assert response["success"] is False
    assert response["status"] == "partial"
    assert response["freed_mb"] == 0.0
    assert response["remaining_artifacts"] == ["cpgs"]
    assert "Retry remove_cpg" in response["error"]
