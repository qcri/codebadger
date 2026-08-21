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
