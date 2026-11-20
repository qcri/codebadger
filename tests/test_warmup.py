"""Cache warm-up: sequential execution + off-critical-path scheduling.

warm_up_cache must run its default queries sequentially (they serialize on the
per-CPG query lock anyway) and keep going when one fails; _schedule_warmup must
fire-and-forget so it never blocks the build worker that calls it.
"""

import asyncio

import pytest

import src.tools.core_tools as core_tools
from src.services.code_browsing_service import CodeBrowsingService


def _make_service(calls, failing=()):
    """A CodeBrowsingService whose candidate warm-up queries just record their order."""
    svc = CodeBrowsingService.__new__(CodeBrowsingService)  # bypass __init__ deps

    def make(name):
        def _q(codebase_hash):
            calls.append(name)
            if name in failing:
                raise RuntimeError(f"{name} boom")
        return _q

    for name in ("list_methods", "list_calls", "list_parameters", "find_literals"):
        setattr(svc, name, make(name))
    return svc


def test_warm_up_cache_runs_only_queries_a_client_can_hit():
    calls = []
    _make_service(calls).warm_up_cache("h1")
    assert calls == ["list_methods", "list_calls"]


async def test_warm_up_cache_keys_match_default_tool_calls():
    """The warm-up must populate the cache entries that default MCP calls look up."""
    import json
    from unittest.mock import MagicMock

    from fastmcp import Client, FastMCP

    from src.models import QueryResult
    from src.tools.code_browsing_tools import register_code_browsing_tools

    def key(call):
        tool_name, codebase_hash, params = call.args[:3]
        return tool_name, codebase_hash, json.dumps(params, sort_keys=True)

    executor = MagicMock()
    executor.execute_query.return_value = QueryResult(success=True, data=[], row_count=0)
    db = MagicMock()
    db.get_cached_tool_output.return_value = None
    tracker = MagicMock()
    svc = CodeBrowsingService(tracker, executor, db_manager=db)

    svc.warm_up_cache("553642871dd4251d")
    warmed = {key(c) for c in db.cache_tool_output.call_args_list}
    db.reset_mock()
    db.get_cached_tool_output.return_value = None

    mcp = FastMCP("TestServer")
    register_code_browsing_tools(mcp, {
        "code_browsing_service": svc, "codebase_tracker": tracker, "query_executor": executor,
    })
    async with Client(mcp) as client:
        for tool in ("list_methods", "list_calls"):
            await client.call_tool(tool, {"codebase_hash": "553642871dd4251d"})
    looked_up = {key(c) for c in db.get_cached_tool_output.call_args_list}

    assert len(warmed) == 2
    assert looked_up == warmed


def test_warm_up_cache_continues_after_a_failing_query():
    calls = []
    # A failure in the middle must not abort the remaining warm-up queries.
    _make_service(calls, failing={"list_methods"}).warm_up_cache("h1")
    assert calls == ["list_methods", "list_calls"]


@pytest.mark.asyncio
async def test_schedule_warmup_is_fire_and_forget():
    ran = []

    class FakeSvc:
        def warm_up_cache(self, codebase_hash):
            ran.append(codebase_hash)

    services = {"code_browsing_service": FakeSvc()}
    assert core_tools._schedule_warmup(services, "h1") is None  # returns immediately

    for _ in range(100):
        if ran and not core_tools._warmup_tasks:
            break
        await asyncio.sleep(0.01)
    assert ran == ["h1"]
    assert core_tools._warmup_tasks == set()  # done-callback drained the registry


@pytest.mark.asyncio
async def test_schedule_warmup_swallows_warmup_errors():
    class FakeSvc:
        def warm_up_cache(self, codebase_hash):
            raise RuntimeError("boom")

    services = {"code_browsing_service": FakeSvc()}
    core_tools._schedule_warmup(services, "h1")  # must not raise

    for _ in range(100):
        if not core_tools._warmup_tasks:
            break
        await asyncio.sleep(0.01)
    assert core_tools._warmup_tasks == set()


def test_schedule_warmup_noop_without_service():
    core_tools._schedule_warmup({}, "h1")  # no code_browsing_service -> nothing scheduled
    assert core_tools._warmup_tasks == set()


def test_schedule_warmup_noop_without_running_loop():
    class FakeSvc:
        def warm_up_cache(self, codebase_hash):
            raise AssertionError("warm-up should not run without a loop")

    # Called from a sync context (no running loop) -> skipped, no error.
    core_tools._schedule_warmup({"code_browsing_service": FakeSvc()}, "h1")
