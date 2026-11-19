from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from src.models import QueryResult
from src.services.code_browsing_service import CodeBrowsingService
from src.tools._common import run_query
from src.tools.taint_analysis_tools import _cached_taint_query


def _services(enabled=True, ttl=300):
    db = MagicMock()
    executor = MagicMock()
    executor.execute_query.return_value = QueryResult(success=True, data="result")
    return {
        "config": SimpleNamespace(query=SimpleNamespace(cache_enabled=enabled, cache_ttl=ttl)),
        "db_manager": db,
        "query_executor": executor,
    }


@pytest.mark.parametrize("runner", [
    lambda s: run_query(
        s, "hash", "/tmp/cpg", "query", tool_name="custom", cache_params={}
    ),
    lambda s: _cached_taint_query(
        s, "taint", "hash", {}, lambda: "result"
    ),
])
def test_tool_cache_disabled_skips_reads_and_writes(runner):
    services = _services(enabled=False)

    assert runner(services) == "result"

    services["db_manager"].get_cached_tool_output.assert_not_called()
    services["db_manager"].cache_tool_output.assert_not_called()


def test_custom_and_taint_queries_pass_configured_ttl():
    runners = (
        ("custom", lambda s: run_query(
            s, "hash", "/tmp/cpg", "query", tool_name="custom", cache_params={}
        )),
        ("taint", lambda s: _cached_taint_query(
            s, "taint", "hash", {}, lambda: "result"
        )),
    )
    for name, runner in runners:
        services = _services(ttl=47)
        services["db_manager"].get_cached_tool_output.return_value = "cached"

        assert runner(services) == "cached"
        services["db_manager"].get_cached_tool_output.assert_called_once_with(
            name, "hash", {}, cache_ttl=47
        )


@pytest.mark.parametrize("enabled", [False, True])
def test_browsing_cache_policy_and_failed_queries(enabled):
    db = MagicMock()
    service = CodeBrowsingService(
        MagicMock(), MagicMock(), db, cache_enabled=enabled, cache_ttl=47
    )
    db.get_cached_tool_output.return_value = None
    query = MagicMock(return_value={"success": True, "rows": []})

    assert service._get_cached_or_execute("browse", "hash", {}, query) == {
        "success": True, "rows": []
    }
    if enabled:
        db.get_cached_tool_output.assert_called_once_with(
            "browse", "hash", {}, cache_ttl=47
        )
        db.cache_tool_output.assert_called_once()
    else:
        db.get_cached_tool_output.assert_not_called()
        db.cache_tool_output.assert_not_called()

    db.reset_mock()
    query.side_effect = RuntimeError("query failed")
    with pytest.raises(RuntimeError, match="query failed"):
        service._get_cached_or_execute("browse", "hash", {}, query)
    db.cache_tool_output.assert_not_called()
