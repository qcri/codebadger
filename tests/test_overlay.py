"""Build-time persistence of Joern's dataflow overlay in the production builder."""

from unittest.mock import MagicMock, patch

import pytest

import src.tools.core_tools as core_tools
from src.models import CodebaseInfo, Config, CPGLoadResult

KEY = "553642871dd4251d"


def _services(overlay=True, max_load_mb=2048):
    config = Config()
    config.cpg.languages_with_exclusions = []
    config.cpg.persist_dataflow_overlay = overlay
    config.cpg.max_load_mb = max_load_mb
    catalog = {KEY: CodebaseInfo(codebase_hash=KEY, source_type="local", source_path="/x",
                                 language="c", metadata={"status": "generating"})}

    def update(codebase_hash, cpg_path=None, joern_port=None, metadata=None):
        catalog[codebase_hash].metadata = {**catalog[codebase_hash].metadata, **(metadata or {})}

    tracker = MagicMock(get_codebase=MagicMock(side_effect=catalog.get),
                        update_codebase=MagicMock(side_effect=update))
    manager = MagicMock()
    manager.reload_with_retry.return_value = CPGLoadResult("ok", port=14000, user_method_count=1)
    return {"codebase_tracker": tracker, "config": config, "joern_server_manager": manager}, catalog


async def _build(tmp_path, services, overlay_exit=0, overlay_output=b"OVERLAY_OK bytes=9", base_bytes=8):
    src = tmp_path / "src"
    src.mkdir()
    (src / "a.c").write_text("int main(void){return 0;}\n")
    cpg = tmp_path / "cpg.bin"
    calls = []

    def exec_run(cmd, **kwargs):
        calls.append((cmd, kwargs))
        if cmd[0] == "bash":  # the overlay pass
            return MagicMock(exit_code=overlay_exit, output=overlay_output)
        cpg.write_bytes(b"x" * base_bytes)  # the frontend writes the base graph
        return MagicMock(exit_code=0, output=b"")

    docker_client = MagicMock()
    docker_client.containers.get.return_value.exec_run.side_effect = exec_run
    with patch.object(core_tools.docker, "from_env", return_value=docker_client), \
         patch.object(core_tools, "_reclaim_source_snapshot"):
        built = await core_tools._generate_cpg_async(
            KEY, str(src), str(cpg), "c", f"/playground/cpgs/{KEY}/cpg.bin", services,
        )
    return built, calls


async def test_overlay_runs_after_the_frontend_in_the_build_container(tmp_path):
    services, catalog = _services()
    built, calls = await _build(tmp_path, services)

    assert built is True
    assert [c[0][0] for c in calls] == ["/opt/joern/joern-cli/c2cpg.sh", "bash"]
    script = calls[1][0][2]
    assert f'importCpg("%s", "ovl")' in script and f"/playground/cpgs/{KEY}/cpg.bin" in script
    assert calls[1][1]["environment"] == calls[0][1]["environment"]  # same build heap
    coverage = catalog[KEY].metadata["coverage"]
    assert coverage["dataflow_overlay"]["persisted"] is True
    assert coverage["warnings"] == []


@pytest.mark.parametrize("exit_code, output", [(1, b"java.lang.OutOfMemoryError"), (0, b"OVERLAY_NO_OUTPUT")])
async def test_a_failed_overlay_keeps_the_base_graph(tmp_path, exit_code, output):
    services, catalog = _services()
    built, _ = await _build(tmp_path, services, overlay_exit=exit_code, overlay_output=output)

    assert built is True  # still built and loaded; loads recompute the overlay
    coverage = catalog[KEY].metadata["coverage"]
    assert coverage["dataflow_overlay"]["persisted"] is False
    assert "dataflow overlay not persisted" in coverage["warnings"][0]


async def test_overlay_can_be_disabled(tmp_path):
    services, catalog = _services(overlay=False)
    built, calls = await _build(tmp_path, services)

    assert built is True and [c[0][0] for c in calls] == ["/opt/joern/joern-cli/c2cpg.sh"]
    assert catalog[KEY].metadata["coverage"]["dataflow_overlay"] == {"persisted": False, "seconds": 0}


async def test_load_ceiling_uses_the_pre_overlay_size_and_skips_the_pass(tmp_path):
    services, catalog = _services(max_load_mb=1)
    built, calls = await _build(tmp_path, services, base_bytes=2 * 1024 * 1024)

    assert built is False and len(calls) == 1  # no overlay for a graph that can't load
    assert catalog[KEY].metadata["error_code"] == "CPG_TOO_LARGE"
    assert catalog[KEY].metadata["cpg_size_mb"] == 2.0
