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


def test_list_methods_reports_exact_total_and_explicit_result_cap():
    service, executor = _service([])
    executor.execute_query.side_effect = [
        QueryResult(success=True, data=25, row_count=1),
        QueryResult(success=True, data=[_method_row(i) for i in range(10)], row_count=10),
    ]

    result = service.list_methods(CODEBASE_HASH, limit=10, page=2, page_size=4)

    assert result["total"] == 25
    assert result["available"] == 10
    assert result["returned"] == 4
    assert result["result_cap"] == 10
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
    assert result["available"] == 3
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
        "result_schema_version": 2,
    }
    db_manager.get_cached_tool_output.assert_called_once_with(
        "list_methods", CODEBASE_HASH, expected_params
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

    expected_params = {"method_name": "parse", "result_schema_version": 2}
    db_manager.get_cached_tool_output.assert_called_once_with(
        "list_parameters", CODEBASE_HASH, expected_params
    )
    db_manager.cache_tool_output.assert_called_once()
