"""Pinned commits are reproducible; refresh follows a moving branch to a new CPG."""

import json
import os
import subprocess
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastmcp import Client, FastMCP

from src.models import Config
from src.services.git_manager import GitManager, _ls_remote
from src.tools.core_tools import register_core_tools

URL = "https://github.com/test/repo"


def _git(repo, *args):
    return subprocess.run(
        ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@example.com", *args],
        check=True, capture_output=True, text=True,
    ).stdout.strip()


@pytest.fixture
def remote(tmp_path):
    repo = tmp_path / "remote"
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
    (repo / "a.c").write_text("int a;\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "one")
    return repo


def test_ls_remote_tracks_the_branch_tip(remote):
    url = f"file://{remote}"
    first = _git(remote, "rev-parse", "HEAD")
    assert _ls_remote(url, "refs/heads/main") == _ls_remote(url, "HEAD") == first

    (remote / "b.c").write_text("int b;\n")
    _git(remote, "add", ".")
    _git(remote, "commit", "-qm", "two")
    assert _ls_remote(url, "refs/heads/main") == _git(remote, "rev-parse", "HEAD") != first
    assert _ls_remote(url, "refs/heads/missing") is None


def test_pinned_clone_checks_out_exactly_that_commit(remote, tmp_path):
    first = _git(remote, "rev-parse", "HEAD")
    (remote / "b.c").write_text("int b;\n")
    _git(remote, "add", ".")
    _git(remote, "commit", "-qm", "two")  # the branch moves on

    target = tmp_path / "checkout"
    GitManager.__new__(GitManager)._do_clone(f"file://{remote}", str(target), None, None, commit=first)

    assert _git(target, "rev-parse", "HEAD") == first
    assert not (target / "b.c").exists()
    assert _git(target, "remote") == ""  # no origin: nothing credential-bearing persisted


async def _hashes(requests, tips):
    services = {name: MagicMock() for name in ("codebase_tracker", "joern_server_manager")}
    services["config"] = Config()
    services["codebase_tracker"].get_codebase.return_value = None
    services["cpg_queue"] = MagicMock(submit=AsyncMock(return_value="submitted"))
    git_manager = MagicMock()
    git_manager.resolve_remote_ref = AsyncMock(side_effect=tips)

    async def clone(repo_url, target_path, branch, token, commit=None):
        os.makedirs(os.path.join(target_path, "source"), exist_ok=True)

    git_manager.clone_repository = AsyncMock(side_effect=clone)
    services["git_manager"] = git_manager
    mcp = FastMCP("TestServer")
    register_core_tools(mcp, services)
    hashes = []
    async with Client(mcp) as client:
        for request in requests:
            reply = await client.call_tool("generate_cpg", {
                "source_type": "github", "source_path": URL, "language": "c", **request,
            })
            hashes.append(json.loads(reply.content[0].text)["codebase_hash"])
    return hashes, git_manager


async def test_refresh_after_the_branch_advances_builds_a_new_graph(tmp_path):
    with patch("src.tools.core_tools.os.path.abspath", return_value=str(tmp_path)), \
         patch("src.tools.core_tools.os.path.dirname", return_value=str(tmp_path)), \
         patch("src.tools.core_tools.os.path.join", side_effect=os.path.join):
        (plain, plain_again, at_tip1, at_tip1_again, at_tip2, pinned, pinned_again), git_manager = await _hashes(
            [{}, {}, {"refresh": True}, {"refresh": True}, {"refresh": True},
             {"commit": "a" * 40}, {"commit": "A" * 40}],
            ["1" * 40, "1" * 40, "2" * 40],
        )

    assert plain == plain_again                       # default: reuse by URL and branch
    assert at_tip1 == at_tip1_again != at_tip2        # refresh follows the moving branch
    assert pinned == pinned_again                     # pinned commits are reproducible
    assert len({plain, at_tip1, at_tip2, pinned}) == 4
    clone_commits = [c.kwargs["commit"] for c in git_manager.clone_repository.await_args_list]
    assert {"1" * 40, "2" * 40, "a" * 40} <= set(clone_commits)


@pytest.mark.parametrize(
    ("request_args", "message"),
    [
        ({"commit": "abc123"}, "full 40- or 64-character hex SHA"),
        ({"commit": "--upload-pack=x" + "0" * 26}, "full 40- or 64-character hex SHA"),
        ({"source_type": "local", "source_path": "/src", "refresh": True}, "only to source_type='github'"),
    ],
)
async def test_invalid_revision_requests_are_rejected(request_args, message):
    services = {name: MagicMock() for name in ("codebase_tracker", "git_manager", "joern_server_manager")}
    services["config"] = Config()
    mcp = FastMCP("TestServer")
    register_core_tools(mcp, services)
    async with Client(mcp) as client:
        result = await client.call_tool("generate_cpg", {
            "source_type": "github", "source_path": URL, "language": "c", **request_args,
        }, raise_on_error=False)

    assert result.is_error and result.content[0].text.startswith("VALIDATION_ERROR: ")
    assert message in result.content[0].text
    services["git_manager"].clone_repository.assert_not_called()
