"""Infra-free unit tests for DurableCPGQueue's worker loop (poll backoff).

These use a fake in-memory store, so they exercise the backoff/reset logic with
no Postgres — complementing the Postgres-gated tests in test_durable_queue.py.
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import src.tools.core_tools as core_tools


class _FakeStore:
    """Minimal job-store stand-in: claim_next_job returns queued results in order."""

    def __init__(self, claim_results):
        self._claims = list(claim_results)
        self.completed = []
        self.failed = []

    def claim_next_job(self, job_type, lease_seconds):
        return self._claims.pop(0) if self._claims else None

    def complete_job(self, job_id, attempt):
        self.completed.append(job_id)
        return True

    def fail_job(self, job_id, attempt, error):
        self.failed.append((job_id, error))
        return True

    def renew_job_lease(self, job_id, attempt, lease_seconds, publish=None):
        if publish:
            publish()
        return True


def _patch_sleep(monkeypatch, stop_after):
    """Record asyncio.sleep delays; abort the worker loop after ``stop_after``."""
    delays = []
    real_sleep = asyncio.sleep

    async def fake_sleep(delay):
        delays.append(delay)
        if len(delays) >= stop_after:
            raise asyncio.CancelledError
        await real_sleep(0)

    monkeypatch.setattr(core_tools.asyncio, "sleep", fake_sleep)
    return delays


@pytest.mark.asyncio
async def test_idle_poll_backs_off_exponentially_capped(monkeypatch):
    delays = _patch_sleep(monkeypatch, stop_after=5)
    store = _FakeStore([])  # queue always empty -> always idle
    q = core_tools.DurableCPGQueue(
        store, {}, workers=0, poll_interval=0.1, max_poll_interval=0.8
    )
    with pytest.raises(asyncio.CancelledError):
        await q._worker()
    # base, 2x, 4x, capped, capped
    assert delays == pytest.approx([0.1, 0.2, 0.4, 0.8, 0.8])


@pytest.mark.asyncio
async def test_backoff_resets_after_a_job_is_claimed(monkeypatch):
    async def fake_generate(**kwargs):
        return True

    monkeypatch.setattr(core_tools, "_generate_cpg_async", fake_generate)
    delays = _patch_sleep(monkeypatch, stop_after=4)
    # idle, idle, JOB, idle, idle
    store = _FakeStore(
        [None, None, {"id": 1, "payload": {}, "codebase_hash": "h", "attempts": 1}, None, None]
    )
    q = core_tools.DurableCPGQueue(
        store, {}, workers=0, poll_interval=0.1, max_poll_interval=0.8
    )
    with pytest.raises(asyncio.CancelledError):
        await q._worker()
    # 0.1, 0.2 (backing off) -> job claimed (reset) -> 0.1, 0.2 again
    assert delays == pytest.approx([0.1, 0.2, 0.1, 0.2])
    assert store.completed == [1]


@pytest.mark.asyncio
@pytest.mark.parametrize("error_code,error", [
    ("REPO_TOO_LARGE", "Repository exceeds maximum allowed size"),
    ("FRONTEND_FAILED", "Joern frontend exited with code 1"),
])
async def test_terminal_build_failure_fails_durable_job_with_catalog_reason(
    monkeypatch, error_code, error
):
    tracker = MagicMock()
    tracker.get_codebase.return_value = SimpleNamespace(
        metadata={"status": "failed", "error_code": error_code, "error": error}
    )

    async def fake_generate(**kwargs):
        kwargs["services"]["codebase_tracker"].update_codebase(
            codebase_hash=kwargs["codebase_hash"],
            metadata={"status": "failed", "error_code": error_code, "error": error},
        )
        return False

    monkeypatch.setattr(core_tools, "_generate_cpg_async", fake_generate)
    store = _FakeStore([])
    queue = core_tools.DurableCPGQueue(store, {"codebase_tracker": tracker}, workers=0)

    await queue._run_claimed_job(
        7, 1, {"codebase_hash": "hash", "services": queue.services}
    )

    assert store.completed == []
    assert store.failed == [(7, f"{error_code}: {error}")]


@pytest.mark.asyncio
async def test_successful_build_completes_durable_job(monkeypatch):
    async def fake_generate(**kwargs):
        return True

    monkeypatch.setattr(core_tools, "_generate_cpg_async", fake_generate)
    store = _FakeStore([])
    queue = core_tools.DurableCPGQueue(store, {}, workers=0)

    await queue._run_claimed_job(8, 1, {"codebase_hash": "hash"})

    assert store.completed == [8]
    assert store.failed == []


def test_max_poll_interval_never_below_base():
    store = _FakeStore([])
    q = core_tools.DurableCPGQueue(
        store, {}, workers=0, poll_interval=2.0, max_poll_interval=0.5
    )
    # A misconfigured max below the base must not shrink the base interval.
    assert q._max_poll_interval == 2.0
