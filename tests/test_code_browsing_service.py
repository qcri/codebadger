import pytest
from unittest.mock import MagicMock

from src.models import CodebaseInfo, QueryResult
from src.services.code_browsing_service import CodeBrowsingService

CODEBASE_HASH = "553642871dd4251d"


def _service(query_data, db_manager=None):
    tracker = MagicMock()
    tracker.get_codebase.return_value = CodebaseInfo(
        codebase_hash=CODEBASE_HASH,
        source_type="local",
        source_path="/workspace/example",
        language="c",
        cpg_path="/workspace/example.cpg",
    )
    executor = MagicMock()
    executor.execute_query.return_value = QueryResult(
        success=True,
        data=query_data,
        row_count=len(query_data),
    )
    return CodeBrowsingService(tracker, executor, db_manager), executor


def _method_row(index):
    return {
        "_1": f"method_{index}",
        "_2": index,
        "_3": f"method_{index}:void()",
        "_4": "void()",
        "_5": "example.c",
        "_6": index + 1,
        "_7": index + 3,
        "_8": 2,
        "_9": False,
    }


def _call_row(index):
    return {
        "_1": f"caller_{index}",
        "_2": f"callee_{index}",
        "_3": f"callee_{index}()",
        "_4": "example.c",
        "_5": index + 1,
    }


def test_list_calls_reports_exact_total_beyond_the_limit():
    service, executor = _service([])
    executor.execute_query.side_effect = [
        QueryResult(success=True, data='val res8: String = "42"', row_count=1),
        QueryResult(success=True, data=[_call_row(i) for i in range(10)], row_count=10),
    ]

    result = service.list_calls(CODEBASE_HASH, limit=10, page=3, page_size=4)

    assert result["total"] == 42
    assert result["returned"] == 2
    assert result["truncated"] is True
    assert result["total_pages"] == 3
    assert [call["callee"] for call in result["calls"]] == [
        "callee_8",
        "callee_9",
    ]

    count_call, calls_call = executor.execute_query.call_args_list
    assert count_call.kwargs["query"].endswith(".dedup.size")
    # Dedup call nodes before projecting, so identical same-line calls stay distinct.
    assert ".dedup.map(c => (" in calls_call.kwargs["query"]
    assert calls_call.kwargs["query"].endswith(" c.id, c.columnNumber.getOrElse(-1), c.method.id)).take(10).l")


def test_list_calls_reports_untruncated_results():
    service, executor = _service([])
    executor.execute_query.side_effect = [
        QueryResult(success=True, data=3, row_count=1),
        QueryResult(success=True, data=[_call_row(i) for i in range(3)], row_count=3),
    ]

    result = service.list_calls(CODEBASE_HASH, limit=10)

    assert result["total"] == 3
    assert result["returned"] == 3
    assert result["truncated"] is False
    assert result["total_pages"] == 1


def test_list_calls_versions_its_cache_key():
    db_manager = MagicMock()
    db_manager.get_cached_tool_output.return_value = None
    service, executor = _service([], db_manager=db_manager)
    executor.execute_query.side_effect = [
        QueryResult(success=True, data=0, row_count=1),
        QueryResult(success=True, data=[], row_count=0),
    ]

    service.list_calls(CODEBASE_HASH)

    expected_params = {
        "caller_pattern": None,
        "callee_pattern": None,
        "limit": 1000,
        "result_schema_version": 3,
    }
    db_manager.get_cached_tool_output.assert_called_once_with(
        "list_calls", CODEBASE_HASH, expected_params, cache_ttl=300
    )


def test_list_methods_reports_exact_total_beyond_the_limit():
    service, executor = _service([])
    executor.execute_query.side_effect = [
        QueryResult(success=True, data=25, row_count=1),
        QueryResult(success=True, data=[_method_row(i) for i in range(10)], row_count=10),
    ]

    result = service.list_methods(CODEBASE_HASH, limit=10, page=2, page_size=4)

    assert result["total"] == 25
    assert result["returned"] == 4
    assert result["truncated"] is True
    assert result["total_pages"] == 3
    assert [method["name"] for method in result["methods"]] == [
        "method_4",
        "method_5",
        "method_6",
        "method_7",
    ]

    count_call, methods_call = executor.execute_query.call_args_list
    assert count_call.kwargs["query"].endswith(".dedup.size")
    assert methods_call.kwargs["query"].endswith(".take(10).l")


def test_list_methods_reports_untruncated_results():
    service, executor = _service([])
    executor.execute_query.side_effect = [
        QueryResult(success=True, data="3", row_count=1),
        QueryResult(success=True, data=[_method_row(i) for i in range(3)], row_count=3),
    ]

    result = service.list_methods(CODEBASE_HASH, limit=10)

    assert result["total"] == 3
    assert result["returned"] == 3
    assert result["truncated"] is False
    assert result["total_pages"] == 1


def test_list_methods_decodes_joern_repl_scalar_count():
    service, executor = _service([])
    executor.execute_query.side_effect = [
        QueryResult(success=True, data='val res7: String = "3369"', row_count=1),
        QueryResult(success=True, data=[_method_row(0)], row_count=1),
    ]

    result = service.list_methods(CODEBASE_HASH, limit=1)

    assert result["total"] == 3369
    assert result["truncated"] is True


def test_list_methods_versions_its_cache_key():
    db_manager = MagicMock()
    db_manager.get_cached_tool_output.return_value = None
    service, executor = _service([], db_manager=db_manager)
    executor.execute_query.side_effect = [
        QueryResult(success=True, data=0, row_count=1),
        QueryResult(success=True, data=[], row_count=0),
    ]

    service.list_methods(CODEBASE_HASH)

    expected_params = {
        "name_pattern": None,
        "file_pattern": None,
        "callee_pattern": None,
        "include_external": False,
        "limit": 1000,
        "result_schema_version": 3,
    }
    db_manager.get_cached_tool_output.assert_called_once_with(
        "list_methods", CODEBASE_HASH, expected_params, cache_ttl=300
    )


def test_list_parameters_decodes_current_joern_method_keyed_json():
    service, _ = _service(
        [
            {
                "xmlReadMemory": [
                    {"_1": "buffer", "_2": "const char *", "_3": 1},
                    {"size": ["int", 2]},
                    ["URL", "const char *", 3],
                ]
            }
        ]
    )

    result = service.list_parameters(CODEBASE_HASH, "xmlReadMemory")

    assert result == {
        "success": True,
        "methods": [
            {
                "method": "xmlReadMemory",
                "parameters": [
                    {"name": "buffer", "type": "const char *", "index": 1},
                    {"name": "size", "type": "int", "index": 2},
                    {"name": "URL", "type": "const char *", "index": 3},
                ],
            }
        ],
        "total": 1,
        "returned": 1,
        "truncated": False,
    }


def test_list_parameters_keeps_legacy_tuple_json_compatibility():
    service, _ = _service(
        [
            {
                "_1": "parse",
                "_2": [
                    {"_1": "input", "_2": "char *", "_3": 1},
                    {"name": "length", "typeFullName": "size_t", "index": 2},
                ],
            }
        ]
    )

    result = service.list_parameters(CODEBASE_HASH, "parse")

    assert result["methods"] == [
        {
            "method": "parse",
            "parameters": [
                {"name": "input", "type": "char *", "index": 1},
                {"name": "length", "type": "size_t", "index": 2},
            ],
        }
    ]


def test_list_parameters_versions_its_cache_key():
    db_manager = MagicMock()
    db_manager.get_cached_tool_output.return_value = None
    service, _ = _service([], db_manager=db_manager)

    service.list_parameters(CODEBASE_HASH, "parse")

    expected_params = {"method_name": "parse", "method_id": -1, "limit": 1000, "result_schema_version": 3}
    db_manager.get_cached_tool_output.assert_called_once_with(
        "list_parameters", CODEBASE_HASH, expected_params, cache_ttl=300
    )
    db_manager.cache_tool_output.assert_called_once()


@pytest.mark.parametrize(("page", "returned", "truncated"), [(1, 2, True), (2, 2, True), (3, 1, False)])
def test_truncated_means_more_results_beyond_this_page(page, returned, truncated):
    """Within the limit, earlier pages are truncated; only the last page is complete."""
    service, executor = _service([])
    executor.execute_query.side_effect = [
        QueryResult(success=True, data=5, row_count=1),
        QueryResult(success=True, data=[_call_row(i) for i in range(5)], row_count=5),
    ]

    result = service.list_calls(CODEBASE_HASH, limit=10, page=page, page_size=2)

    assert (result["total"], result["returned"], result["truncated"]) == (5, returned, truncated)
    assert result["total_pages"] == 3
    assert not {"available", "result_cap", "has_more"} & set(result)


def _methods_service(total, rows):
    service, executor = _service([])
    executor.execute_query.side_effect = [
        QueryResult(success=True, data=total, row_count=1),
        QueryResult(success=True, data=[_method_row(i) for i in range(rows)], row_count=rows),
    ]
    return service


def test_list_methods_hint_says_to_raise_limit_past_the_kept_window():
    # Audit case methods_cap: 3 matches, limit=1, page 2 of size 1.
    result = _methods_service(3, 1).list_methods(CODEBASE_HASH, limit=1, page=2, page_size=1)
    assert (result["total"], result["returned"], result["truncated"], result["total_pages"]) == (3, 0, True, 1)
    assert "page 2 is past the last page (1)" in result["hint"]
    assert "limit=1 kept 1 of 3 matches. Raise limit (up to 3)" in result["hint"]


def test_list_methods_hint_points_to_the_next_page_first():
    result = _methods_service(3, 3).list_methods(CODEBASE_HASH, limit=10, page=1, page_size=1)
    assert result["truncated"] is True
    assert result["hint"].startswith("More matches on later pages: request page=2 (pages 1-3)")


def test_list_methods_has_no_hint_when_nothing_is_hidden():
    result = _methods_service(3, 3).list_methods(CODEBASE_HASH, limit=10)
    assert result["truncated"] is False and "hint" not in result


def test_list_methods_complexity_counts_cfg_branches():
    service = _methods_service(1, 1)
    service.list_methods(CODEBASE_HASH, limit=10)
    query = service.query_executor.execute_query.call_args_list[1].kwargs["query"]
    assert "m.cfgNode.l.map(_._cfgOut.size).filter(_ > 1).map(_ - 1).sum + 1" in query
    assert "controlStructure.size" not in query


def test_list_calls_keeps_same_line_calls_apart_by_node_and_column():
    # Audit case calls_twice: `show_value(9); show_value(9);` on one line.
    row = lambda node, col: {"_1": "twice", "_2": "show_value", "_3": "show_value(9)", "_4": "a.c",
                             "_5": 4, "_6": node, "_7": col, "_8": 107374182400}
    service, executor = _service([])
    executor.execute_query.side_effect = [
        QueryResult(success=True, data=2, row_count=1),
        QueryResult(success=True, data=[row(30064771081, 25), row(30064771082, 40)], row_count=2),
    ]
    calls = service.list_calls(CODEBASE_HASH, caller_pattern="twice")["calls"]
    assert [(c["node_id"], c["columnNumber"], c["caller_id"]) for c in calls] == [
        ("30064771081", 25, "107374182400"), ("30064771082", 40, "107374182400")]


def _py_size(node, owner, param):
    return {"_1": "size", "_2": node, "_3": f"{owner}.size", "_4": owner, "_5": "model.py", "_6": 2,
            "_7": [{"_1": "self", "_2": "ANY", "_3": 0}, {"_1": param, "_2": "ANY", "_3": 1}]}


def test_list_parameters_attributes_same_named_methods_to_their_owner():
    # Audit case py_params: A.size(self, width) and B.size(self, height).
    service, _ = _service([_py_size(107374182402, "model.py:<module>.A", "width"),
                           _py_size(107374182407, "model.py:<module>.B", "height")])
    methods = service.list_parameters(CODEBASE_HASH, "size")["methods"]
    assert [(m["node_id"], m["owner"], m["parameters"][1]["name"]) for m in methods] == [
        ("107374182402", "model.py:<module>.A", "width"), ("107374182407", "model.py:<module>.B", "height")]


def test_list_parameters_selects_by_method_id():
    service, executor = _service([_py_size(107374182407, "model.py:<module>.B", "height")])
    result = service.list_parameters(CODEBASE_HASH, "size", method_id=107374182407)
    query = executor.execute_query.call_args.kwargs["query"]
    assert ".filter(_.id == 107374182407L)" in query and '.name("size")' not in query
    assert result["methods"][0]["owner"] == "model.py:<module>.B"


def test_list_parameters_reports_a_capped_list():
    service, executor = _service([_py_size(i, "m.C", "x") for i in range(3)])
    result = service.list_parameters(CODEBASE_HASH, "size", limit=2)
    assert executor.execute_query.call_args.kwargs["query"].endswith(".take(3).l")
    assert (result["returned"], result["truncated"]) == (2, True) and "method_id" in result["hint"]
