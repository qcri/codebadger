"""Caller paths relative to the repository root also work for github clones,
which live in codebase_dir/source while the frontend parses codebase_dir."""

import re
import subprocess
from unittest.mock import MagicMock, patch

import pytest

import src.tools.core_tools as core_tools
from src.models import Config

KEY = "553642871dd4251d"


def _layout(tmp_path, github):
    root = tmp_path / "codebase"
    repo = root / "source" if github else root
    (repo / "src").mkdir(parents=True)
    (repo / "other").mkdir()
    (repo / "src" / "a.c").write_text("int a;\n")
    (repo / "other" / "b.c").write_text("int b;\n")
    (repo / "build").mkdir()
    (repo / "build" / "cc.json").write_text(
        '[{"file": "src/a.c", "command": "cc -c src/a.c"}]')
    if github:
        subprocess.run(["git", "init", "-q", str(repo)], check=True)
    return root


async def _cmd(root, **kwargs):
    config = Config()
    config.cpg.languages_with_exclusions = []
    container = MagicMock()
    container.exec_run.return_value = MagicMock(exit_code=1, output=b"stop after the frontend")
    docker_client = MagicMock()
    docker_client.containers.get.return_value = container
    services = {"codebase_tracker": MagicMock(), "config": config, "joern_server_manager": MagicMock()}
    with patch.object(core_tools.docker, "from_env", return_value=docker_client):
        await core_tools._generate_cpg_async(
            KEY, str(root), str(root.parent / "cpg.bin"), "c", f"/playground/cpgs/{KEY}/cpg.bin",
            services, **kwargs,
        )
    return container.exec_run.call_args_list[0].kwargs["cmd"]


@pytest.mark.parametrize("github, prefix", [(True, "source/"), (False, "")])
def test_repo_root_prefix(tmp_path, github, prefix):
    assert core_tools._repo_root_prefix(str(_layout(tmp_path, github))) == prefix


@pytest.mark.parametrize("github", [True, False])
async def test_include_globs_scope_the_repository_for_both_layouts(tmp_path, github):
    cmd = await _cmd(_layout(tmp_path, github), include_globs=["src/**"])
    rx = cmd[cmd.index("--exclude-regex") + 1]
    prefix = "source/" if github else ""

    assert re.fullmatch(rx, f"{prefix}src/a.c") is None       # in scope: kept
    assert re.fullmatch(rx, f"{prefix}other/b.c") is not None  # out of scope: excluded


async def test_relative_include_paths_and_databases_resolve_inside_the_clone(tmp_path):
    cmd = await _cmd(_layout(tmp_path, True), include_paths=["src"], compile_commands="build/cc.json")

    includes = [cmd[i + 1] for i, part in enumerate(cmd) if part == "--include"]
    assert f"/playground/codebases/{KEY}/source/src" in includes
    assert cmd[cmd.index("--compilation-database") + 1].startswith(f"/playground/codebases/{KEY}/")


async def test_github_builds_with_repo_paths_get_a_fresh_cache_key():
    from unittest.mock import AsyncMock

    from fastmcp import Client, FastMCP

    from src.tools.core_tools import register_core_tools

    keys = []
    real = core_tools.get_cpg_cache_key

    def spy(*args, **kwargs):
        keys.append(kwargs.get("extra", {}))
        return real(*args, **kwargs)

    services = {"codebase_tracker": MagicMock(get_codebase=MagicMock(return_value=None)),
                "config": Config(), "git_manager": MagicMock(), "joern_server_manager": MagicMock(),
                "cpg_queue": MagicMock(submit=AsyncMock(return_value="submitted"))}
    services["git_manager"].clone_repository = AsyncMock(side_effect=RuntimeError("stop before cloning"))
    mcp = FastMCP("TestServer")
    register_core_tools(mcp, services)
    with patch.object(core_tools, "get_cpg_cache_key", side_effect=spy):
        async with Client(mcp) as client:
            for extra in ({"include_globs": ["src/**"]}, {}):
                await client.call_tool("generate_cpg", {
                    "source_type": "github", "source_path": "https://github.com/x/y", "language": "c", **extra,
                }, raise_on_error=False)

    assert keys[0].get("repo_root_paths") == 1
    assert "repo_root_paths" not in keys[1]  # unscoped github builds keep their existing keys
