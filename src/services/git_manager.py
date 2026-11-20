"""
Git repository manager for cloning and managing remote git repositories.

Besides the built-in github.com/gitlab.com https allowlist, an operator can
allowlist custom git servers (e.g. a self-hosted Forgejo in the LAN, cloned
over ssh) via the GIT_CLONE_EXTRA_HOSTS / GIT_CLONE_SSH_* environment
variables — see src/defaults.py.
"""

import asyncio
import logging
import os
import re
import shlex
import shutil
import tempfile
from typing import Dict, Optional
from urllib.parse import quote, urlparse

import git

from .. import defaults
from ..exceptions import GitOperationError, ValidationError
from ..utils.validators import validate_github_url

logger = logging.getLogger(__name__)


def _mask_token_in_text(text: str) -> str:
    """
    Mask authentication tokens in error messages or logs.

    Args:
        text: Text that may contain tokens in URLs

    Returns:
        Text with tokens masked
    """
    return re.sub(r"(https?://)[^@\s]+@", r"\1***@", text)


def _ls_remote(url: str, ref: str, env: Optional[Dict[str, str]] = None) -> Optional[str]:
    """First commit `git ls-remote` reports for ref, or None."""
    g = git.cmd.Git()
    with g.custom_environment(**(env or {})):
        out = g.ls_remote("--", url, ref)
    for line in out.splitlines():
        sha, _, name = line.partition("\t")
        if name == ref and re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", sha):
            return sha
    return None


def _ssh_clone_env() -> Dict[str, str]:
    """Environment overrides for an ssh:// clone of a custom git server.

    git invokes the command in GIT_SSH_COMMAND for every ssh remote. Default to
    the operator's full override (GIT_CLONE_SSH_COMMAND) or build one from
    GIT_CLONE_SSH_KEY_PATH. BatchMode keeps a missing key/passphrase from
    hanging the clone on an interactive prompt.

    Host key policy: with GIT_CLONE_SSH_KNOWN_HOSTS pointing at a known_hosts
    file the server key is *pinned* (StrictHostKeyChecking=yes). Without it we
    fall back to accept-new, which records the key on first contact — note that
    in the dockerized stack that record lives in the container's ~/.ssh and is
    lost on every container recreate, so it is trust-on-first-use each deploy.

    Only GIT_SSH_COMMAND is returned: GitPython layers these over os.environ
    for the child process, so there is no need (and no reason, given the
    secrets in this server's environment) to copy the whole environment.
    """
    ssh_cmd = os.getenv("GIT_CLONE_SSH_COMMAND", defaults.GIT_CLONE_SSH_COMMAND).strip()
    if not ssh_cmd:
        parts = ["ssh"]
        key_path = os.getenv(
            "GIT_CLONE_SSH_KEY_PATH", defaults.GIT_CLONE_SSH_KEY_PATH
        ).strip()
        if key_path:
            # git runs GIT_SSH_COMMAND through a shell, so paths need quoting.
            parts += ["-i", shlex.quote(key_path)]
        known_hosts = os.getenv(
            "GIT_CLONE_SSH_KNOWN_HOSTS", defaults.GIT_CLONE_SSH_KNOWN_HOSTS
        ).strip()
        if known_hosts:
            parts += [
                "-o",
                f"UserKnownHostsFile={shlex.quote(known_hosts)}",
                "-o",
                "StrictHostKeyChecking=yes",
            ]
        else:
            parts += ["-o", "StrictHostKeyChecking=accept-new"]
        parts += ["-o", "BatchMode=yes"]
        ssh_cmd = " ".join(parts)
    return {"GIT_SSH_COMMAND": ssh_cmd}


class GitManager:
    """Handles remote git repository operations"""

    def __init__(self, workspace_root: str):
        self.workspace_root = workspace_root
        self.repos_dir = os.path.join(workspace_root, "repos")
        os.makedirs(self.repos_dir, exist_ok=True)

    @staticmethod
    def _remote_access(repo_url: str, token: Optional[str]):
        """Validated (auth_url, env, injected_credential) for talking to a remote."""
        # Validate URL (scheme/host allowlist, no embedded credentials, …)
        validate_github_url(repo_url)
        parsed = urlparse(repo_url)
        auth_url = repo_url
        clone_env: Optional[Dict[str, str]] = None
        injected_credential = False
        if parsed.scheme in ("http", "https"):
            # Built-in github.com/gitlab.com hosts only (the validator
            # rejects every other host): the per-call token rides in the
            # URL username and is stripped from .git/config afterwards.
            if token:
                auth_url = (
                    f"{parsed.scheme}://{quote(token, safe='')}"
                    f"@{parsed.netloc}{parsed.path}"
                )
                injected_credential = True
        elif parsed.scheme == "ssh":
            # ssh:// (custom GIT_CLONE_EXTRA_HOSTS server): auth comes from
            # the key/agent via GIT_SSH_COMMAND, not from the URL.
            clone_env = _ssh_clone_env()
        return auth_url, clone_env, injected_credential

    async def resolve_remote_ref(
        self, repo_url: str, branch: Optional[str] = None, token: Optional[str] = None,
    ) -> str:
        """Current commit of `branch` (default: the remote HEAD) via git ls-remote."""
        try:
            auth_url, env, _ = self._remote_access(repo_url, token)
            ref = f"refs/heads/{branch}" if branch else "HEAD"
            loop = asyncio.get_event_loop()
            commit = await loop.run_in_executor(None, _ls_remote, auth_url, ref, env)
        except ValidationError:
            raise
        except Exception as e:
            raise GitOperationError(f"Failed to resolve remote ref: {_mask_token_in_text(str(e))}")
        if not commit:
            raise ValidationError(
                f"Branch {branch!r} not found on the remote" if branch
                else "The remote has no HEAD commit"
            )
        return commit

    async def clone_repository(
        self,
        repo_url: str,
        target_path: str,
        branch: Optional[str] = None,
        token: Optional[str] = None,
        commit: Optional[str] = None,
    ) -> str:
        """Clone a repo (https for github/gitlab; ssh for custom hosts).

        With `commit`, fetch exactly that commit (shallow) and check it out detached.
        """
        try:
            auth_url, clone_env, injected_credential = self._remote_access(repo_url, token)

            # Create target directory
            os.makedirs(target_path, exist_ok=True)
            source_path = os.path.join(target_path, "source")
            # Publish the checkout only after a complete clone so a retry never
            # mistakes a partial one for usable source.
            staging_path = tempfile.mkdtemp(prefix=".clone-", dir=target_path)

            # Clone in a thread pool (git operations are blocking)
            loop = asyncio.get_event_loop()
            try:
                await loop.run_in_executor(
                    None, self._do_clone, auth_url, staging_path, branch, clone_env, commit
                )

                # Remove the embedded credential from .git/config so the token
                # is not stored on disk in plaintext.
                if injected_credential:
                    await loop.run_in_executor(
                        None, self._strip_remote_credential, staging_path, repo_url
                    )
                os.replace(staging_path, source_path)
            finally:
                shutil.rmtree(staging_path, ignore_errors=True)

            logger.info(f"Cloned repository {repo_url} to {source_path}")
            return source_path

        except ValidationError:
            raise
        except Exception as e:
            # Mask tokens in error messages before logging
            safe_error = _mask_token_in_text(str(e))
            logger.error(f"Failed to clone repository: {safe_error}")
            raise GitOperationError(f"Failed to clone repository: {safe_error}")

    def _do_clone(
        self,
        url: str,
        target: str,
        branch: Optional[str],
        env: Optional[Dict[str, str]] = None,
        commit: Optional[str] = None,
    ):
        """Blocking clone operation"""
        try:
            if commit:
                # Fetch by URL, not via a named remote, so no credential-bearing
                # origin is written to .git/config.
                repo = git.Repo.init(target)
                with repo.git.custom_environment(**(env or {})):
                    repo.git.fetch("--depth", "1", "--", url, commit)
                repo.git.checkout("--detach", "FETCH_HEAD")
            elif branch:
                git.Repo.clone_from(url, target, branch=branch, depth=1, env=env)
            else:
                git.Repo.clone_from(url, target, depth=1, env=env)
        except Exception as e:
            # Mask tokens in error messages
            safe_error = _mask_token_in_text(str(e))
            raise GitOperationError(f"Git clone failed: {safe_error}")

    def _strip_remote_credential(self, repo_path: str, clean_url: str) -> None:
        """Rewrite the 'origin' remote URL to the credential-free form.

        git clone stores the full auth URL (including embedded token) in
        .git/config.  Overwriting it with the public URL prevents the token
        from persisting on disk after the clone completes.
        """
        try:
            repo = git.Repo(repo_path)
            if "origin" in [r.name for r in repo.remotes]:
                repo.remotes["origin"].set_url(clean_url)
        except Exception as e:
            logger.warning(f"Could not strip credential from remote URL: {e}")

    def validate_repository(self, repo_url: str) -> bool:
        """Validate that repository exists and is accessible"""
        try:
            validate_github_url(repo_url)
            # Could add additional checks here (API call to check if repo exists)
            return True
        except Exception as e:
            logger.error(f"Repository validation failed: {e}")
            return False

    def get_repository_info(self, repo_url: str) -> Dict:
        """Get repository information"""
        try:
            validate_github_url(repo_url)
            parsed = urlparse(repo_url)
            parts = parsed.path.strip("/").split("/")

            return {
                "owner": parts[0] if len(parts) > 0 else "",
                "repo": parts[1] if len(parts) > 1 else "",
                "url": repo_url,
            }
        except Exception as e:
            logger.error(f"Failed to get repository info: {e}")
            raise GitOperationError(f"Failed to parse repository URL: {str(e)}")

    def parse_github_url(self, url: str) -> Dict:
        """Parse GitHub URL into components"""
        try:
            validate_github_url(url)
            parsed = urlparse(url)
            parts = parsed.path.strip("/").split("/")

            # Remove .git suffix if present
            repo = parts[1].replace(".git", "") if len(parts) > 1 else ""

            return {
                "owner": parts[0] if len(parts) > 0 else "",
                "repo": repo,
                "host": parsed.netloc,
                "scheme": parsed.scheme,
            }
        except Exception as e:
            logger.error(f"Failed to parse GitHub URL: {e}")
            raise GitOperationError(f"Invalid GitHub URL: {str(e)}")

    def cleanup_repository(self, target_path: str):
        """Clean up cloned repository"""
        try:
            if os.path.exists(target_path):
                shutil.rmtree(target_path)
                logger.info(f"Cleaned up repository at {target_path}")
        except Exception as e:
            logger.error(f"Failed to cleanup repository: {e}")
