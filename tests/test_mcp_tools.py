"""
Tests for MCP tools
"""

import asyncio
import json
import os
import tempfile
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastmcp.exceptions import ToolError

from src.models import CodebaseInfo, Config, CPGLoadResult, QueryResult
from src.services.codebase_tracker import CodebaseTracker
from src.services.git_manager import GitManager
from src.tools.mcp_tools import register_tools


from fastmcp import FastMCP, Client



def _call_err(client, *args, **kwargs):
    """Call a tool expecting failure: return the error result instead of raising."""
    return client.call_tool(*args, raise_on_error=False, **kwargs)


def _error_reply(result):
    """Parse a coded MCP error 'CODE: message Hint: ...' into a dict."""
    assert result.is_error
    code, _, rest = result.content[0].text.partition(": ")
    message = rest.rsplit(" Hint: ", 1)[0]
    return {"success": False, "error_code": code, "error": message}


def _catalog_page_stub(rows):
    """Stand-in for CodebaseTracker.list_codebase_page over an in-memory catalog.

    Filtering and paging happen in Postgres in production (covered by
    test_postgres_db_manager); this mirrors that contract for tool-level tests.
    """
    from src.tools.core_tools import _codebase_project

    def page(*, offset, limit, language=None, project=None):
        matched = [
            cb for cb in rows
            if (not language or (cb.language or "").lower() == language.lower())
            and (not project or _codebase_project(cb).lower() == project.lower())
        ]
        return matched[offset:offset + limit], len(matched)

    return MagicMock(side_effect=page)

@pytest.fixture
def mock_services():
    """Create mock services for testing"""
    # Mock git manager
    git_manager = MagicMock(spec=GitManager)

    # Mock codebase tracker
    codebase_tracker = MagicMock(spec=CodebaseTracker)
    codebase_tracker.save_codebase.return_value = CodebaseInfo(
        codebase_hash="553642871dd4251d",
        source_type="github",
        source_path="https://github.com/test/repo",
        language="c",
        cpg_path="/tmp/test.cpg"
    )
    codebase_tracker.get_codebase.return_value = CodebaseInfo(
        codebase_hash="553642871dd4251d",
        source_type="github",
        source_path="https://github.com/test/repo",
        language="c",
        cpg_path="/tmp/test.cpg"
    )

    # Mock query executor
    query_executor = MagicMock()
    query_executor.execute_query.return_value = QueryResult(
        success=True,
        data=[{"_1": "main", "_2": "function", "_3": "void main()", "_4": "main.c", "_5": 1}],
        row_count=1
    )

    # Mock config
    config = Config()

    # Mock code browsing service
    code_browsing_service = MagicMock()
    code_browsing_service.list_methods.return_value = {"success": True, "methods": []}
    code_browsing_service.run_query.return_value = {"success": True, "data": [], "row_count": 0}

    # Mock joern server manager
    joern_server_manager = MagicMock()
    joern_server_manager.get_server_port.return_value = 8080
    joern_server_manager.get_or_create_client.return_value.execute_query.return_value = {
        "success": False,
    }

    return {
        "git_manager": git_manager,
        "codebase_tracker": codebase_tracker,
        "query_executor": query_executor,
        "config": config,
        "code_browsing_service": code_browsing_service,
        "joern_server_manager": joern_server_manager,
    }


@pytest.fixture
def temp_workspace():
    """Create a temporary workspace directory"""
    with tempfile.TemporaryDirectory() as temp_dir:
        # Create playground structure
        playground = os.path.join(temp_dir, "playground")
        os.makedirs(os.path.join(playground, "cpgs", "test1234567890123456"))
        os.makedirs(os.path.join(playground, "codebases", "test1234567890123456"))

        # Create a fake CPG file
        cpg_path = os.path.join(playground, "cpgs", "test1234567890123456", "cpg.bin")
        with open(cpg_path, "w") as f:
            f.write("fake cpg")

        yield temp_dir


class TestMCPTools:
    """Test MCP tools functionality"""

    def test_code_browsing_service_escapes_list_methods_query(self, mock_services):
        """Structured code-browsing queries should escape Scala string literals."""
        from src.services.code_browsing_service import CodeBrowsingService

        mock_services["query_executor"].execute_query.return_value = QueryResult(
            success=True,
            data=[],
            row_count=0,
        )
        service = CodeBrowsingService(
            codebase_tracker=mock_services["codebase_tracker"],
            query_executor=mock_services["query_executor"],
        )

        service.list_methods(
            "553642871dd4251d",
            name_pattern='main"; cpg.call.l //',
        )

        rendered_query = mock_services["query_executor"].execute_query.call_args.kwargs["query"]
        assert 'cpg.method.isExternal(false).name("main\\"; cpg.call.l //")' in rendered_query

    @pytest.mark.asyncio
    async def test_generate_cpg_github_success(self, mock_services, temp_workspace):
        """Test successful CPG generation from GitHub"""
        # Import core_tools to register the tools
        from src.tools.core_tools import register_core_tools
        
        with patch("src.tools.core_tools.os.path.abspath", return_value=temp_workspace), \
             patch("src.tools.core_tools.os.path.dirname", return_value=temp_workspace), \
             patch("src.tools.core_tools.os.path.join", side_effect=os.path.join), \
             patch("src.tools.core_tools.os.makedirs"), \
             patch("src.tools.core_tools.shutil.copytree"), \
             patch("src.tools.core_tools.shutil.copy2"):

            mcp = FastMCP("TestServer")
            register_core_tools(mcp, mock_services)

            # Mock the git clone
            mock_services["git_manager"].clone_repository.return_value = None

            # Call the tool using Client
            async with Client(mcp) as client:
                result = await client.call_tool(
                    "generate_cpg",
                    {
                        "source_type": "github",
                        "source_path": "https://github.com/test/repo",
                        "language": "c"
                    }
                )

                # extract data from CallToolResult
                data = result.content[0].text
                import json
                result_dict = json.loads(data)

                # Now it returns "generating" status immediately
                assert "codebase_hash" in result_dict
                assert result_dict["status"] == "generating"
                assert result_dict["source_type"] == "github"

    @pytest.mark.asyncio
    async def test_generate_cpg_cached(self, mock_services, temp_workspace):
        """Test CPG generation when CPG already exists"""
        from src.tools.core_tools import register_core_tools
        
        # Set up existing codebase in tracker
        mock_services["codebase_tracker"].get_codebase.return_value = CodebaseInfo(
            codebase_hash="553642871dd4251d",
            source_type="github",
            source_path="https://github.com/test/repo",
            language="c",
            cpg_path=os.path.join(temp_workspace, "playground/cpgs/test/cpg.bin"),
            joern_port=2000,
            metadata={"status": "ready"}
        )
        
        with patch("src.tools.core_tools.os.path.abspath", return_value=temp_workspace), \
             patch("src.tools.core_tools.os.path.dirname", return_value=temp_workspace), \
             patch("src.tools.core_tools.os.path.join", side_effect=os.path.join), \
             patch("src.tools.core_tools.os.path.exists", return_value=True):

            mcp = FastMCP("TestServer")
            register_core_tools(mcp, mock_services)

            # Call the tool using Client
            async with Client(mcp) as client:
                result = await client.call_tool(
                    "generate_cpg",
                    {
                        "source_type": "github",
                        "source_path": "https://github.com/test/repo",
                        "language": "c"
                    }
                )
                
                # import json
                # data = result.content[0].text
                # result_dict = json.loads(data)
                # The result object from FastMCP might be different if it handles JSON parsing automatically or wrapped
                # FastMCP Client.call_tool returns CallToolResult. 
                # Let's assume we need to parse content.
                
                import json
                result_dict = json.loads(result.content[0].text)

                assert result_dict["status"] == "ready"
                assert result_dict["cpg_path"] == "<redacted:host-path>"
                assert result_dict["joern_port"] == 2000

    async def test_generate_cpg_rebuilds_an_empty_cached_graph(self, mock_services, tmp_path):
        from src.tools import core_tools

        cached = {}
        mock_services["codebase_tracker"].get_codebase.side_effect = cached.get
        mock_services["cpg_queue"] = MagicMock(submit=AsyncMock(return_value="submitted"))
        mcp = FastMCP("TestServer")
        core_tools.register_core_tools(mcp, mock_services)
        request = {"source_type": "snippet", "language": "c", "code": "int main(void) { return 0; }"}
        with patch.object(core_tools, "__file__", str(tmp_path / "src/tools/core_tools.py")):
            async with Client(mcp) as client:
                first = json.loads((await client.call_tool("generate_cpg", request)).content[0].text)
                codebase_hash = first["codebase_hash"]
                cpg_path = tmp_path / "playground/cpgs" / codebase_hash / "cpg.bin"
                cpg_path.write_bytes(b"empty")
                cached[codebase_hash] = CodebaseInfo(
                    codebase_hash=codebase_hash, source_type="snippet", source_path="snippet",
                    language="c", cpg_path=str(cpg_path),
                    metadata={**core_tools._CPG_LOAD_FAILURE, "user_method_count": 0},
                )

                retry = json.loads((await client.call_tool("generate_cpg", request)).content[0].text)

        assert retry["status"] == "generating"
        assert not cpg_path.exists()
        assert mock_services["cpg_queue"].submit.await_count == 2

    @pytest.mark.parametrize(("source_type", "first", "second"), [
        ("github", {"include_globs": ["alpha/**"]}, {"include_globs": ["beta/**"]}),
        ("github", {}, {"auto_system_headers": True}),
        ("github", {"compile_commands": "build/debug.json"}, {"compile_commands": "build/release.json"}),
        ("github", {"include_paths": ["first", "second"]}, {"include_paths": ["second", "first"]}),
        ("github", {"defines": ["FOO=a,BAR=2"]}, {"defines": ["FOO=a", "BAR=2"]}),
        ("snippet", {"filename": "first.c"}, {"filename": "second.c"}),
        ("snippet", {}, {"defines": ["FOO=1"]}),
    ])
    async def test_generate_cpg_reuses_only_matching_build_options(
        self, mock_services, tmp_path, source_type, first, second
    ):
        from src.tools import core_tools

        cached = {}
        mock_services["codebase_tracker"].get_codebase.side_effect = cached.get
        mock_services["joern_server_manager"].is_server_running.return_value = True
        mock_services["cpg_queue"] = MagicMock(submit=AsyncMock(return_value="submitted"))
        mcp = FastMCP("TestServer")
        core_tools.register_core_tools(mcp, mock_services)
        base = {"source_type": source_type, "language": "c"}
        if source_type == "snippet":
            base["code"] = "int main(void) { return 0; }"
        else:
            base["source_path"] = "https://github.com/test/repo"
        with patch.object(core_tools, "__file__", str(tmp_path / "src/tools/core_tools.py")):
            async with Client(mcp) as client:
                original = await client.call_tool("generate_cpg", {**base, **first})
                response = json.loads(original.content[0].text)
                assert response["status"] == "generating"

                original_hash = response["codebase_hash"]
                cpg_path = tmp_path / "playground/cpgs" / original_hash / "cpg.bin"
                cpg_path.write_bytes(b"fixture")
                cached[original_hash] = CodebaseInfo(
                    codebase_hash=original_hash,
                    source_type=source_type,
                    source_path="example",
                    language="c",
                    cpg_path=str(cpg_path),
                    joern_port=2000,
                    metadata={"status": "ready"},
                )

                changed = await client.call_tool("generate_cpg", {**base, **second})
                response = json.loads(changed.content[0].text)
                assert response["status"] == "generating"
                assert response["codebase_hash"] != original_hash

                repeat = await client.call_tool("generate_cpg", {**base, **first})
                response = json.loads(repeat.content[0].text)
                assert response["status"] == "ready"
                assert response["codebase_hash"] == original_hash

        assert mock_services["cpg_queue"].submit.await_count == 2

    @pytest.mark.asyncio
    async def test_generate_cpg_uses_submitted_build_settings(self, mock_services, tmp_path):
        (tmp_path / "src").mkdir()
        (tmp_path / "src/compile_commands.json").write_text('[{"file": "a.c"}]')
        cmd = await self._run_async_build_capture_cmd(
            mock_services, tmp_path, "c",
            build_settings={"exclusion_patterns": ["skip/.*"], "autodetect_compile_db": False},
        )
        assert cmd[cmd.index("--exclude-regex") + 1] == "^(?:(?:(skip/.*)))$"
        assert "--compilation-database" not in cmd

    @pytest.mark.asyncio
    async def test_generate_cpg_cached_loading_does_not_schedule_duplicate_restart(self, mock_services, temp_workspace):
        """A cached codebase already loading should not enqueue a second restart."""
        from src.tools.core_tools import register_core_tools

        codebase_hash = "553642871dd4251d"
        mock_services["codebase_tracker"].get_codebase.return_value = CodebaseInfo(
            codebase_hash=codebase_hash,
            source_type="local",
            source_path="/Users/private/test-repo",
            language="c",
            cpg_path=os.path.join(temp_workspace, "playground/cpgs/test/cpg.bin"),
            joern_port=2000,
            metadata={
                "status": "loading",
                "container_cpg_path": f"/playground/cpgs/{codebase_hash}/cpg.bin",
            },
        )
        mock_services["joern_server_manager"].is_server_running.return_value = False
        pending_restart = asyncio.get_running_loop().create_future()
        mock_services["restart_tasks"] = {codebase_hash: pending_restart}

        with patch("src.tools.core_tools.os.path.abspath", return_value=temp_workspace), \
             patch("src.tools.core_tools.os.path.dirname", return_value=temp_workspace), \
             patch("src.tools.core_tools.os.path.join", side_effect=os.path.join), \
             patch("src.tools.core_tools.os.path.exists", return_value=True), \
               patch("src.tools.core_tools.get_cpg_cache_key", return_value=codebase_hash), \
               patch("src.tools.core_tools._schedule_restart_server_task") as schedule_restart:

            mcp = FastMCP("TestServer")
            register_core_tools(mcp, mock_services)

            async with Client(mcp) as client:
                result = await client.call_tool(
                    "generate_cpg",
                    {
                        "source_type": "local",
                        "source_path": "/Users/private/test-repo",
                        "language": "c"
                    }
                )

                import json
                result_dict = json.loads(result.content[0].text)

                assert result_dict["status"] == "loading"
                assert "already in progress" in result_dict["message"]
                schedule_restart.assert_not_called()

            pending_restart.cancel()

    @pytest.mark.asyncio
    async def test_generate_cpg_local_copy_error_redacts_host_path(self, mock_services, tmp_path):
        """Local copy failures should not echo host paths back to the client."""
        from src.tools.core_tools import register_core_tools

        source_dir = tmp_path / "private-repo"
        source_dir.mkdir()
        mock_services["codebase_tracker"].get_codebase.return_value = None

        with patch("src.tools.core_tools.resolve_host_path", return_value=str(source_dir)), \
             patch("src.tools.core_tools._get_git_commit_hash", return_value=None), \
             patch("src.tools.core_tools.os.path.abspath", return_value=str(tmp_path)), \
             patch("src.tools.core_tools.os.path.dirname", return_value=str(tmp_path)), \
             patch("src.tools.core_tools.os.path.join", side_effect=os.path.join), \
             patch("src.tools.core_tools.os.makedirs"), \
             patch("src.tools.core_tools.os.listdir", side_effect=OSError(f"permission denied: {source_dir}")):

            mcp = FastMCP("TestServer")
            register_core_tools(mcp, mock_services)

            async with Client(mcp) as client:
                result = await _call_err(client,
                    "generate_cpg",
                    {
                        "source_type": "local",
                        "source_path": str(source_dir),
                        "language": "c"
                    }
                )

                import json
                result_dict = _error_reply(result)

                assert result_dict["success"] is False
                assert result_dict["error_code"] == "VALIDATION_ERROR"
                assert result_dict["error"] == "Failed to copy local source directory"
                assert str(source_dir) not in result_dict["error"]

    @pytest.mark.asyncio
    async def test_generate_cpg_rejects_playground_self_inclusion(self, mock_services):
        """A local source that contains the playground must be refused, not copied.

        Regression for the outage where a cell passed CodeBadger's own dir (which
        contains playground/) as source: the recursive copy + on-loop size walk hung
        the entire MCP. The guard must reject it before any copy.
        """
        import src.tools.core_tools as core_tools
        from src.tools.core_tools import register_core_tools
        import json

        mock_services["codebase_tracker"].get_codebase.return_value = None
        # The real playground dir the guard derives from __file__, and its parent
        # (an ancestor) — analyzing the parent would recursively include playground.
        playground = os.path.abspath(
            os.path.join(os.path.dirname(core_tools.__file__), "..", "..", "playground")
        )
        ancestor = os.path.dirname(playground)

        with patch("src.tools.core_tools.resolve_host_path", return_value=ancestor), \
             patch("src.tools.core_tools._get_git_commit_hash", return_value=None), \
             patch("src.tools.core_tools._scan_repo", return_value=(0, 0)), \
             patch("src.tools.core_tools._copy_local_source_tree") as copy_mock:

            mcp = FastMCP("TestServer")
            register_core_tools(mcp, mock_services)

            async with Client(mcp) as client:
                result = await _call_err(client,
                    "generate_cpg",
                    {"source_type": "local", "source_path": ancestor, "language": "c"},
                )
                result_dict = _error_reply(result)

            assert result_dict["success"] is False
            assert result_dict["error_code"] == "VALIDATION_ERROR"
            assert "playground" in result_dict["error"].lower()
            # The bomb must never be staged.
            copy_mock.assert_not_called()

    @pytest.mark.asyncio
    async def test_generate_cpg_snippet_stages_pasted_code(self, mock_services, tmp_path):
        """A pasted snippet should be written into codebases/<hash> and queued."""
        from unittest.mock import AsyncMock
        from src.tools.core_tools import register_core_tools

        codebase_hash = "abc1234567890123"
        code = "int main() { int a[2]; a[5] = 1; return 0; }"
        mock_services["codebase_tracker"].get_codebase.return_value = None
        mock_services["cpg_queue"] = MagicMock()
        mock_services["cpg_queue"].submit = AsyncMock(return_value="queued")

        with patch("src.tools.core_tools.os.path.abspath", return_value=str(tmp_path)), \
             patch("src.tools.core_tools.os.path.dirname", return_value=str(tmp_path)), \
             patch("src.tools.core_tools.os.path.join", side_effect=os.path.join), \
             patch("src.tools.core_tools.get_cpg_cache_key", return_value=codebase_hash):

            mcp = FastMCP("TestServer")
            register_core_tools(mcp, mock_services)

            async with Client(mcp) as client:
                result = await client.call_tool(
                    "generate_cpg",
                    {
                        "source_type": "snippet",
                        "source_path": "",
                        "language": "c",
                        "code": code,
                    },
                )

            import json
            result_dict = json.loads(result.content[0].text)

            assert result_dict["status"] == "generating"
            mock_services["cpg_queue"].submit.assert_awaited_once()

            # The snippet was staged with the language-derived filename.
            snippet_file = tmp_path / "codebases" / codebase_hash / "snippet.c"
            assert snippet_file.read_text() == code

            # Persisted as a snippet source.
            save_kwargs = mock_services["codebase_tracker"].save_codebase.call_args.kwargs
            assert save_kwargs["source_type"] == "snippet"

    @pytest.mark.asyncio
    async def test_submission_estimate_does_not_block_other_requests(self, mock_services, tmp_path):
        """A slow estimate scan after queue submission must not stall an unrelated MCP call."""
        import threading
        from src.tools.core_tools import register_core_tools

        codebase_hash = "abc1234567890123"
        mock_services["cpg_queue"] = MagicMock()
        mock_services["cpg_queue"].submit = AsyncMock(return_value="queued")
        scan_started = threading.Event()
        release_scan = threading.Event()
        released_explicitly = []

        def slow_scan(_path):
            scan_started.set()
            released_explicitly.append(release_scan.wait(timeout=5))
            return (0, 0)

        with patch("src.tools.core_tools.os.path.abspath", return_value=str(tmp_path)), \
             patch("src.tools.core_tools.os.path.dirname", return_value=str(tmp_path)), \
             patch("src.tools.core_tools.os.path.join", side_effect=os.path.join), \
             patch("src.tools.core_tools.get_cpg_cache_key", return_value=codebase_hash), \
             patch("src.tools.core_tools._scan_repo", side_effect=slow_scan):
            mcp = FastMCP("TestServer")
            register_core_tools(mcp, mock_services)
            async with Client(mcp) as client:
                mock_services["codebase_tracker"].get_codebase.return_value = None
                generate = asyncio.create_task(client.call_tool(
                    "generate_cpg",
                    {"source_type": "snippet", "source_path": "", "language": "c",
                     "code": "int main() { return 0; }"},
                ))
                while not scan_started.is_set():
                    await asyncio.sleep(0.01)
                status = await asyncio.wait_for(
                    client.call_tool("get_cpg_status", {"codebase_hash": "553642871dd4251d"}),
                    timeout=2,
                )
                release_scan.set()
                result = json.loads((await generate).content[0].text)

        assert released_explicitly == [True]
        assert status.structured_content["codebase_hash"] == "553642871dd4251d"
        assert result["status"] == "generating"
        assert "estimated_time" in result

    @pytest.mark.asyncio
    async def test_cached_restart_estimate_reuses_guard_measurement(self, mock_services, temp_workspace):
        """The restart estimate reuses the size the large-project guard already measured."""
        from src.tools.core_tools import register_core_tools

        codebase_hash = "553642871dd4251d"
        mock_services["codebase_tracker"].get_codebase.return_value = CodebaseInfo(
            codebase_hash=codebase_hash,
            source_type="local",
            source_path="/Users/private/test-repo",
            language="c",
            cpg_path=os.path.join(temp_workspace, "playground/cpgs/test/cpg.bin"),
            metadata={"status": "sleeping"},
        )
        mock_services["joern_server_manager"].is_server_running.return_value = False

        with patch("src.tools.core_tools.os.path.abspath", return_value=temp_workspace), \
             patch("src.tools.core_tools.os.path.dirname", return_value=temp_workspace), \
             patch("src.tools.core_tools.os.path.join", side_effect=os.path.join), \
             patch("src.tools.core_tools.os.path.exists", return_value=True), \
             patch("src.tools.core_tools.resolve_host_path", return_value=temp_workspace), \
             patch("src.tools.core_tools.get_cpg_cache_key", return_value=codebase_hash), \
             patch("src.tools.core_tools._schedule_restart_server_task", return_value=True), \
             patch("src.tools.core_tools._scan_repo", return_value=(120, 1000)) as scan:
            mcp = FastMCP("TestServer")
            register_core_tools(mcp, mock_services)
            async with Client(mcp) as client:
                result = await client.call_tool(
                    "generate_cpg",
                    {"source_type": "local", "source_path": "/Users/private/test-repo", "language": "c"},
                )
        result_dict = json.loads(result.content[0].text)

        assert result_dict["status"] == "loading"
        assert result_dict["estimated_time"].startswith("~1-3 minutes")
        assert scan.call_count == 1

    @pytest.mark.asyncio
    async def test_queue_full_marks_catalog_failed_and_retry_submits_once(
        self, mock_services, tmp_path
    ):
        from unittest.mock import AsyncMock
        from src.tools.core_tools import register_core_tools

        codebase_hash = "abc1234567890123"
        mock_services["codebase_tracker"].get_codebase.return_value = None
        queue = MagicMock()
        queue.submit = AsyncMock(side_effect=["queue_full", "submitted"])
        mock_services["cpg_queue"] = queue
        with patch("src.tools.core_tools.os.path.abspath", return_value=str(tmp_path)), \
             patch("src.tools.core_tools.os.path.dirname", return_value=str(tmp_path)), \
             patch("src.tools.core_tools.os.path.join", side_effect=os.path.join), \
             patch("src.tools.core_tools.get_cpg_cache_key", return_value=codebase_hash):
            mcp = FastMCP("TestServer")
            register_core_tools(mcp, mock_services)
            async with Client(mcp) as client:
                rejected = await client.call_tool("generate_cpg", {
                    "source_type": "snippet", "source_path": "", "language": "c",
                    "code": "int main() { return 0; }",
                })
                retried = await client.call_tool("generate_cpg", {
                    "source_type": "snippet", "source_path": "", "language": "c",
                    "code": "int main() { return 0; }",
                })

        rejected_data = json.loads(rejected.content[0].text)
        retried_data = json.loads(retried.content[0].text)
        assert rejected_data["status"] == "queue_full"
        assert rejected_data["success"] is False
        assert retried_data["status"] == "generating"
        assert queue.submit.await_count == 2
        failed_update = next(
            call.kwargs["metadata"]
            for call in mock_services["codebase_tracker"].update_codebase.call_args_list
            if call.kwargs.get("metadata", {}).get("error_code") == "QUEUE_FULL"
        )
        assert failed_update["status"] == "failed"
        assert failed_update["phase"] == "failed"

    async def test_github_retry_reclones_after_failed_clone(
        self, mock_services, tmp_path
    ):
        from src.exceptions import GitOperationError
        from src.tools.core_tools import register_core_tools

        codebase_hash = "abc1234567890123"
        mock_services["codebase_tracker"].get_codebase.return_value = None
        queue = MagicMock()
        queue.submit = AsyncMock(return_value="submitted")
        mock_services["cpg_queue"] = queue

        async def clone(repo_url, target_path, branch, token, commit=None):
            os.makedirs(target_path, exist_ok=True)
            if clone_mock.await_count == 1:
                raise GitOperationError("network down")
            os.makedirs(os.path.join(target_path, "source"))

        clone_mock = AsyncMock(side_effect=clone)
        mock_services["git_manager"].clone_repository = clone_mock
        request = {
            "source_type": "github",
            "source_path": "https://github.com/test/repo",
            "language": "c",
        }
        with patch("src.tools.core_tools.os.path.abspath", return_value=str(tmp_path)), \
             patch("src.tools.core_tools.os.path.dirname", return_value=str(tmp_path)), \
             patch("src.tools.core_tools.os.path.join", side_effect=os.path.join), \
             patch("src.tools.core_tools.get_cpg_cache_key", return_value=codebase_hash):
            mcp = FastMCP("TestServer")
            register_core_tools(mcp, mock_services)
            async with Client(mcp) as client:
                await client.call_tool("generate_cpg", request, raise_on_error=False)
                retried = await client.call_tool("generate_cpg", request)

        assert clone_mock.await_count == 2
        assert json.loads(retried.content[0].text)["status"] == "generating"

    @pytest.mark.asyncio
    async def test_generate_cpg_snippet_requires_code(self, mock_services, tmp_path):
        """source_type='snippet' without code returns a validation error, not a crash."""
        from src.tools.core_tools import register_core_tools

        mock_services["codebase_tracker"].get_codebase.return_value = None

        mcp = FastMCP("TestServer")
        register_core_tools(mcp, mock_services)

        async with Client(mcp) as client:
            result = await _call_err(client,
                "generate_cpg",
                {"source_type": "snippet", "source_path": "", "language": "c"},
            )

        import json
        result_dict = _error_reply(result)
        assert result_dict["success"] is False
        assert result_dict["error_code"] == "VALIDATION_ERROR"
        # Helpful, actionable message telling the LLM how to supply the code.
        err = result_dict["error"]
        assert "no snippet code" in err.lower() and "<code language=" in err

    @pytest.mark.asyncio
    async def test_generate_cpg_local_disabled_in_chat_deploy(self, mock_services, tmp_path):
        """When chat_deploy is on, source_type='local' is refused with guidance."""
        from src.tools.core_tools import register_core_tools

        mock_services["config"].server.chat_deploy = True
        mock_services["codebase_tracker"].get_codebase.return_value = None

        mcp = FastMCP("TestServer")
        register_core_tools(mcp, mock_services)

        async with Client(mcp) as client:
            result = await _call_err(client,
                "generate_cpg",
                {"source_type": "local", "source_path": str(tmp_path), "language": "c"},
            )

        import json
        result_dict = _error_reply(result)
        assert result_dict["success"] is False
        assert result_dict["error_code"] == "VALIDATION_ERROR"
        err = result_dict["error"]
        assert "disabled" in err.lower()
        # Steer the LLM to the allowed alternatives.
        assert "github" in err.lower() and "snippet" in err.lower()

    @pytest.mark.asyncio
    async def test_get_cpg_status_exists(self, mock_services):
        """Test getting CPG status when CPG exists"""
        from src.tools.core_tools import register_core_tools
        
        # Set up existing codebase with metadata
        mock_services["codebase_tracker"].get_codebase.return_value = CodebaseInfo(
            codebase_hash="553642871dd4251d",
            source_type="local",
            source_path="/Users/private/test-repo",
            language="c",
            cpg_path="/tmp/test.cpg",
            joern_port=2000,
            metadata={
                "status": "ready",
                "container_codebase_path": "/playground/codebases/553642871dd4251d",
                "container_cpg_path": "/playground/cpgs/553642871dd4251d/cpg.bin"
            }
        )
        
        mcp = FastMCP("TestServer")
        register_core_tools(mcp, mock_services)

        with patch("os.path.exists", return_value=True):
            async with Client(mcp) as client:
                result = await client.call_tool("get_cpg_status", {"codebase_hash": "553642871dd4251d"})
                
                import json
                result_dict = json.loads(result.content[0].text)

                assert result_dict["codebase_hash"] == "553642871dd4251d"
                assert result_dict["status"] == "ready"
                # Non-sensitive label derived from basename, not the redacted path.
                assert result_dict["codebase_label"] == "test-repo@55364287"
                assert result_dict["cpg_path"] == "<redacted:host-path>"
                assert result_dict["source_path"] == "<redacted:local-source>"
                assert result_dict["container_codebase_path"] == "<redacted:container-path>"
                assert result_dict["container_cpg_path"] == "<redacted:container-path>"
                assert result_dict["elapsed_seconds"] == 0.0
                assert result_dict["deadline_seconds"] == 0.0

    @pytest.mark.asyncio
    async def test_get_cpg_status_uses_recorded_terminal_duration(self, mock_services):
        from src.tools.core_tools import register_core_tools

        mock_services["codebase_tracker"].get_codebase.return_value.metadata = {
            "status": "failed",
            "generation_elapsed_seconds": 12.5,
            "error_code": "BUILD_ERROR",
            "error": "frontend failed",
        }
        mcp = FastMCP("TestServer")
        register_core_tools(mcp, mock_services)

        async with Client(mcp) as client:
            result = await client.call_tool(
                "get_cpg_status", {"codebase_hash": "553642871dd4251d"}
            )
            import json
            response = json.loads(result.content[0].text)

        assert response["status"] == "failed"
        assert response["elapsed_seconds"] == 12.5
        assert response["deadline_seconds"] == 0.0
        assert response["error_code"] == "BUILD_ERROR"

    @pytest.mark.asyncio
    async def test_get_cpg_status_loading_does_not_reuse_generation_timestamp(self, mock_services):
        from datetime import datetime, timedelta, timezone
        from src.tools.core_tools import register_core_tools

        mock_services["codebase_tracker"].get_codebase.return_value.metadata = {
            "status": "loading",
            "generation_started_at": (
                datetime.now(timezone.utc) - timedelta(days=2)
            ).isoformat(),
        }
        mcp = FastMCP("TestServer")
        register_core_tools(mcp, mock_services)

        async with Client(mcp) as client:
            result = await client.call_tool(
                "get_cpg_status", {"codebase_hash": "553642871dd4251d"}
            )
            import json
            response = json.loads(result.content[0].text)

        assert response["status"] == "failed"
        assert response["elapsed_seconds"] == 0.0
        assert response["deadline_seconds"] == 0.0

    @pytest.mark.asyncio
    async def test_get_cpg_status_reports_progress_fields(self, mock_services):
        """get_cpg_status surfaces phase/elapsed/deadline and queue_position while building."""
        from datetime import datetime, timedelta, timezone
        from src.tools.core_tools import register_core_tools

        started = (datetime.now(timezone.utc) - timedelta(seconds=30)).isoformat()
        deadline = (datetime.now(timezone.utc) + timedelta(seconds=600)).isoformat()
        mock_services["codebase_tracker"].get_codebase.return_value = CodebaseInfo(
            codebase_hash="553642871dd4251d",
            source_type="github",
            source_path="https://github.com/test/repo",
            language="c",
            cpg_path=None,
            joern_port=None,
            metadata={
                "status": "generating",
                "phase": "frontend",
                "generation_started_at": started,
                "generation_deadline": deadline,
            },
        )
        cpg_queue = MagicMock()
        cpg_queue.is_in_flight.return_value = True
        cpg_queue.queue_position.return_value = 3
        mock_services["cpg_queue"] = cpg_queue

        mcp = FastMCP("TestServer")
        register_core_tools(mcp, mock_services)

        async with Client(mcp) as client:
            result = await client.call_tool("get_cpg_status", {"codebase_hash": "553642871dd4251d"})
            import json
            result_dict = json.loads(result.content[0].text)

            assert result_dict["status"] == "generating"
            # queue_position present => reported as queued
            assert result_dict["queue_position"] == 3
            assert result_dict["phase"] == "queued"
            assert result_dict["elapsed_seconds"] >= 30
            assert 0 < result_dict["deadline_seconds"] <= 600

    @pytest.mark.asyncio
    async def test_get_cpg_status_generating_past_deadline_no_worker_reconciles_failed(self, mock_services):
        """A 'generating' build past its deadline with no live worker is reconciled to FAILED."""
        from datetime import datetime, timedelta, timezone
        from src.tools.core_tools import register_core_tools

        past = (datetime.now(timezone.utc) - timedelta(seconds=60)).isoformat()
        mock_services["codebase_tracker"].get_codebase.return_value = CodebaseInfo(
            codebase_hash="553642871dd4251d",
            source_type="github",
            source_path="https://github.com/test/repo",
            language="c",
            cpg_path=None,
            joern_port=None,
            metadata={
                "status": "generating",
                "generation_started_at": past,
                "generation_deadline": past,  # already elapsed
            },
        )
        # No live build job for this hash.
        cpg_queue = MagicMock()
        cpg_queue.is_in_flight.return_value = False
        mock_services["cpg_queue"] = cpg_queue

        mcp = FastMCP("TestServer")
        register_core_tools(mcp, mock_services)

        async with Client(mcp) as client:
            result = await client.call_tool("get_cpg_status", {"codebase_hash": "553642871dd4251d"})
            import json
            result_dict = json.loads(result.content[0].text)

            assert result_dict["status"] == "failed"
            assert result_dict["error_code"] == "GENERATION_TIMEOUT"
            mock_services["codebase_tracker"].update_codebase.assert_called()

    @pytest.mark.asyncio
    async def test_get_cpg_status_generating_with_live_worker_stays_generating(self, mock_services):
        """A 'generating' build past its deadline but with a live worker is NOT condemned."""
        from datetime import datetime, timedelta, timezone
        from src.tools.core_tools import register_core_tools

        past = (datetime.now(timezone.utc) - timedelta(seconds=60)).isoformat()
        mock_services["codebase_tracker"].get_codebase.return_value = CodebaseInfo(
            codebase_hash="553642871dd4251d",
            source_type="github",
            source_path="https://github.com/test/repo",
            language="c",
            cpg_path=None,
            joern_port=None,
            metadata={
                "status": "generating",
                "generation_started_at": past,
                "generation_deadline": past,
            },
        )
        # A worker is still actively building/queued.
        cpg_queue = MagicMock()
        cpg_queue.is_in_flight.return_value = True
        mock_services["cpg_queue"] = cpg_queue

        mcp = FastMCP("TestServer")
        register_core_tools(mcp, mock_services)

        async with Client(mcp) as client:
            result = await client.call_tool("get_cpg_status", {"codebase_hash": "553642871dd4251d"})
            import json
            result_dict = json.loads(result.content[0].text)

            assert result_dict["status"] == "generating"

    async def _run_async_build_capture_cmd(self, mock_services, tmp_path, language, **kwargs):
        """Drive _generate_cpg_async far enough to capture the frontend cmd, then
        bail (exit_code != 0) so no real load happens. Returns the cmd list."""
        from unittest.mock import patch
        import src.tools.core_tools as core_tools

        mock_services["config"].cpg.languages_with_exclusions = []
        codebase_dir = tmp_path / "src"
        codebase_dir.mkdir(exist_ok=True)
        (codebase_dir / "a.txt").write_text("x")

        container = MagicMock()
        container.exec_run.return_value = MagicMock(exit_code=1, output=b"stop here")
        docker_client = MagicMock()
        docker_client.containers.get.return_value = container
        mock_services["joern_server_manager"].container_name = "codebadger-joern-server"

        with patch.object(core_tools.docker, "from_env", return_value=docker_client):
            await core_tools._generate_cpg_async(
                codebase_hash="553642871dd4251d",
                codebase_dir=str(codebase_dir),
                cpg_path=str(tmp_path / "cpg.bin"),
                language=language,
                container_cpg_path="/playground/cpgs/553642871dd4251d/cpg.bin",
                services=mock_services,
                **kwargs,
            )
        # exec_run is called as exec_run(cmd=cmd, ...)
        assert container.exec_run.called
        return container.exec_run.call_args.kwargs["cmd"]

    @pytest.mark.asyncio
    async def test_include_globs_adds_scope_exclude_regex_for_any_language(self, mock_services, tmp_path):
        """include_globs scoping works for non-C languages via --exclude-regex."""
        cmd = await self._run_async_build_capture_cmd(
            mock_services, tmp_path, "python", include_globs=["pkg/**"]
        )
        assert "--exclude-regex" in cmd
        rx = cmd[cmd.index("--exclude-regex") + 1]
        import re
        # out-of-scope python source excluded; in-scope kept; headers concept n/a
        assert re.match(rx + r"\Z", "other/mod.py")
        assert not re.match(rx + r"\Z", "pkg/mod.py")

    @pytest.mark.asyncio
    async def test_compile_commands_passed_and_rebased_for_c(self, mock_services, tmp_path):
        """compile_commands is rebased and passed as --compilation-database for C."""
        import json
        codebase_dir = tmp_path / "src"
        codebase_dir.mkdir(exist_ok=True)
        (codebase_dir / "compile_commands.json").write_text(json.dumps([
            {"directory": "/orig/root", "file": "/orig/root/a.c", "command": "cc -c a.c"},
        ]))
        cmd = await self._run_async_build_capture_cmd(
            mock_services, tmp_path, "c",
            compile_commands="compile_commands.json",
            source_root_host="/orig/root",
        )
        assert "--compilation-database" in cmd
        db_arg = cmd[cmd.index("--compilation-database") + 1]
        assert db_arg == "/playground/codebases/553642871dd4251d/compile_commands.container.json"
        # The rebased file was written under the codebase dir with container paths.
        written = json.loads((codebase_dir / "compile_commands.container.json").read_text())
        assert written[0]["file"] == "/playground/codebases/553642871dd4251d/a.c"

    @pytest.mark.parametrize(
        ("db_file", "applied"),
        [("compile_commands.json", True), ("missing_commands.json", False),
         ("foreign_commands.json", False)],
    )
    @pytest.mark.asyncio
    async def test_status_shows_whether_the_compilation_database_was_applied(
        self, mock_services, tmp_path, db_file, applied
    ):
        """Applied and missing databases are distinguishable over MCP after the source is gone."""
        import src.tools.core_tools as core_tools
        from src.tools.core_tools import register_core_tools

        key = "553642871dd4251d"
        codebase_dir = tmp_path / "src"
        codebase_dir.mkdir()
        (codebase_dir / "a.c").write_text("int main(void){return 0;}\n")
        (codebase_dir / "compile_commands.json").write_text(json.dumps([
            {"directory": "/orig/root", "file": "/orig/root/a.c", "command": "cc -c a.c"},
        ]))
        # A database written on another machine whose paths can't be rebased here.
        (codebase_dir / "foreign_commands.json").write_text(json.dumps([
            {"directory": "/build/root", "file": "/build/root/a.c", "command": "cc -c a.c"},
        ]))
        cpg_path = tmp_path / "cpg.bin"
        cpg_path.write_bytes(b"fixture")

        catalog = {key: CodebaseInfo(
            codebase_hash=key, source_type="local", source_path="/orig/root", language="c",
            cpg_path=None, metadata={"status": "generating"},
        )}

        def update_codebase(codebase_hash, cpg_path=None, joern_port=None, metadata=None):
            info = catalog[codebase_hash]
            info.metadata = {**info.metadata, **(metadata or {})}
            info.cpg_path = cpg_path or info.cpg_path
            info.joern_port = joern_port

        tracker = mock_services["codebase_tracker"]
        tracker.get_codebase.side_effect = catalog.get
        tracker.update_codebase.side_effect = update_codebase
        mock_services["config"].cpg.languages_with_exclusions = []
        mock_services["config"].cpg.ephemeral_source = True
        manager = mock_services["joern_server_manager"]
        manager.reload_with_retry.return_value = CPGLoadResult("ok", port=14000, user_method_count=1)
        manager.get_server_port.return_value = 14000
        manager.is_server_running.return_value = True
        mock_services["config"].cpg.persist_dataflow_overlay = False  # covered in test_overlay
        docker_client = MagicMock()
        docker_client.containers.get.return_value.exec_run.return_value = MagicMock(exit_code=0)

        with patch.object(core_tools.docker, "from_env", return_value=docker_client):
            built = await core_tools._generate_cpg_async(
                key, str(codebase_dir), str(cpg_path), "c", f"/playground/cpgs/{key}/cpg.bin",
                mock_services, compile_commands=db_file, source_root_host="/orig/root",
            )
        assert built is True
        assert not codebase_dir.exists()  # the source snapshot was reclaimed

        mcp = FastMCP("TestServer")
        register_core_tools(mcp, mock_services)
        async with Client(mcp) as client:
            status = (await client.call_tool("get_cpg_status", {"codebase_hash": key})).structured_content

        database = status["coverage"]["compilation_database"]
        assert database["applied"] is applied and database["source"] == "caller"
        frontend_cmd = docker_client.containers.get.return_value.exec_run.call_args_list[0].kwargs["cmd"]
        assert ("--compilation-database" in frontend_cmd) is applied
        if applied:
            assert (database["entries"], database["rebased"], database["reachable"]) == (1, 2, 1)
            assert status["coverage"]["warnings"] == []
        elif db_file == "foreign_commands.json":
            assert (database["entries"], database["reachable"]) == (1, 0)
            assert "skipped" in status["coverage"]["warnings"][0]
            assert "<path>/root" in status["coverage"]["warnings"][0]  # names the root, redacted
        else:
            assert "could not be applied" in status["coverage"]["warnings"][0]
        assert status["user_method_count"] == 1

    @pytest.mark.asyncio
    async def test_compile_commands_autodetected_for_c(self, mock_services, tmp_path):
        """A compile_commands.json shipped in the source is used automatically."""
        import json
        codebase_dir = tmp_path / "src"
        codebase_dir.mkdir(exist_ok=True)
        (codebase_dir / "compile_commands.json").write_text(json.dumps([
            {"directory": "/orig", "file": "/orig/a.c", "command": "cc -c a.c"},
        ]))
        # No compile_commands argument passed -> should be auto-detected.
        cmd = await self._run_async_build_capture_cmd(
            mock_services, tmp_path, "c", source_root_host="/orig"
        )
        assert "--compilation-database" in cmd

    @pytest.mark.asyncio
    async def test_compile_commands_autodetect_disabled(self, mock_services, tmp_path):
        """Auto-detect can be turned off via config."""
        import json
        mock_services["config"].cpg.autodetect_compile_db = False
        codebase_dir = tmp_path / "src"
        codebase_dir.mkdir(exist_ok=True)
        (codebase_dir / "compile_commands.json").write_text(json.dumps([{"file": "/o/a.c"}]))
        cmd = await self._run_async_build_capture_cmd(mock_services, tmp_path, "c")
        assert "--compilation-database" not in cmd

    @pytest.mark.asyncio
    async def test_compile_commands_ignored_for_python(self, mock_services, tmp_path):
        """Non-C frontends don't get --compilation-database (graceful)."""
        import json
        codebase_dir = tmp_path / "src"
        codebase_dir.mkdir(exist_ok=True)
        (codebase_dir / "compile_commands.json").write_text(json.dumps([{"file": "a.py"}]))
        cmd = await self._run_async_build_capture_cmd(
            mock_services, tmp_path, "python", compile_commands="compile_commands.json"
        )
        assert "--compilation-database" not in cmd

    @pytest.mark.asyncio
    async def test_auto_system_headers_flag_c_only(self, mock_services, tmp_path):
        """--with-include-auto-discovery is added for C when requested, never for python."""
        c_cmd = await self._run_async_build_capture_cmd(
            mock_services, tmp_path, "c", auto_system_headers=True
        )
        assert "--with-include-auto-discovery" in c_cmd

        py_cmd = await self._run_async_build_capture_cmd(
            mock_services, tmp_path, "python", auto_system_headers=True
        )
        assert "--with-include-auto-discovery" not in py_cmd

    @pytest.mark.asyncio
    async def test_defines_gated_by_frontend_capability(self, mock_services, tmp_path):
        """--define is passed for C, but NOT for python (frontend lacks it)."""
        c_cmd = await self._run_async_build_capture_cmd(
            mock_services, tmp_path, "c", defines=["FOO=1"]
        )
        assert "--define" in c_cmd and "FOO=1" in c_cmd

        py_cmd = await self._run_async_build_capture_cmd(
            mock_services, tmp_path, "python", defines=["FOO=1"]
        )
        assert "--define" not in py_cmd

    @pytest.mark.asyncio
    async def test_bare_define_is_passed_as_one(self, mock_services, tmp_path):
        cmd = await self._run_async_build_capture_cmd(
            mock_services, tmp_path, "c", defines=["FOO", "BAR=2"]
        )
        assert "FOO=1" in cmd and "BAR=2" in cmd

    @pytest.mark.asyncio
    async def test_harvested_defines_yield_to_caller_defines(self, mock_services, tmp_path):
        (tmp_path / "src").mkdir()
        (tmp_path / "src/config.h.in").write_text("#undef HAVE_A\n#undef HAVE_B\n")
        (tmp_path / "src/a.c").write_text("#if HAVE_A && HAVE_B\n#endif\n")
        cmd = await self._run_async_build_capture_cmd(
            mock_services, tmp_path, "c", defines=["HAVE_B=0"]
        )
        defines = [cmd[i + 1] for i, arg in enumerate(cmd) if arg == "--define"]
        assert defines == ["HAVE_A=1", "HAVE_B=0"]

    @pytest.mark.asyncio
    async def test_stale_build_attempt_keeps_published_cpg(self, mock_services, tmp_path):
        from src.tools import core_tools

        mock_services["config"].cpg.languages_with_exclusions = []
        cpg_path = tmp_path / "cpg.bin"
        cpg_path.write_bytes(b"current")
        attempt_path = tmp_path / "cpg.bin.1-1.bin"

        def build(**kwargs):
            cmd = kwargs["cmd"]
            assert cmd[cmd.index("-o") + 1].endswith(attempt_path.name)
            attempt_path.write_bytes(b"stale")
            return MagicMock(exit_code=0)

        async def renew_claim(publish=None):
            return publish is None

        docker_client = MagicMock()
        docker_client.containers.get.return_value.exec_run.side_effect = build
        with patch.object(core_tools.docker, "from_env", return_value=docker_client):
            await core_tools._generate_cpg_async(
                codebase_hash="553642871dd4251d",
                codebase_dir=str(tmp_path),
                cpg_path=str(cpg_path),
                language="python",
                container_cpg_path="/playground/cpgs/553642871dd4251d/cpg.bin",
                services=mock_services,
                build_attempt="1-1",
                renew_claim=renew_claim,
            )
        assert cpg_path.read_bytes() == b"current"
        assert not attempt_path.exists()
        mock_services["joern_server_manager"].reload_with_retry.assert_not_called()

    @pytest.mark.asyncio
    @pytest.mark.parametrize("load_failure", ["no-port", "exception", "no-manager"])
    async def test_generate_cpg_reports_load_failure_and_retries_saved_graph(
        self, mock_services, tmp_path, load_failure
    ):
        from src.tools import core_tools

        codebase_hash = "553642871dd4251d"
        source_dir = tmp_path / "src"
        source_dir.mkdir()
        source_file = source_dir / "snippet.c"
        code = "int main(void) { return 0; }"
        source_file.write_text(code)
        cpg_path = tmp_path / "cpg.bin"
        cpg_path.write_bytes(b"fixture")
        container_path = f"/playground/cpgs/{codebase_hash}/cpg.bin"
        info = CodebaseInfo(
            codebase_hash=codebase_hash, source_type="snippet",
            source_path="snippet.c", language="c",
        )
        tracker = mock_services["codebase_tracker"]
        tracker.get_codebase.return_value = info

        def update_codebase(codebase_hash, metadata, **fields):
            info.metadata.update(metadata)
            for key, value in fields.items():
                setattr(info, key, value)

        tracker.update_codebase.side_effect = update_codebase
        manager = mock_services["joern_server_manager"]
        manager.reload_with_retry.return_value = CPGLoadResult("error")
        if load_failure == "exception":
            manager.reload_with_retry.side_effect = RuntimeError("/private/worker startup failed")
        elif load_failure == "no-manager":
            mock_services["joern_server_manager"] = None

        mock_services["config"].cpg.persist_dataflow_overlay = False  # covered in test_overlay
        docker_client = MagicMock()
        frontend = docker_client.containers.get.return_value.exec_run
        frontend.return_value = MagicMock(exit_code=0)
        mcp = FastMCP("TestServer")
        core_tools.register_core_tools(mcp, mock_services)
        with patch.object(core_tools.docker, "from_env", return_value=docker_client), \
             patch.object(core_tools, "get_cpg_cache_key", return_value=codebase_hash):
            built = await core_tools._generate_cpg_async(
                codebase_hash, str(source_dir), str(cpg_path), "c", container_path, mock_services,
            )
            assert not built
            assert cpg_path.exists() and source_file.exists()
            mock_services["code_browsing_service"].warm_up_cache.assert_not_called()

            async with Client(mcp) as client:
                status = await client.call_tool("get_cpg_status", {"codebase_hash": codebase_hash})
                failed = json.loads(status.content[0].text)
                assert (failed["status"], failed["joern_port"]) == ("failed", None)
                assert failed["error_code"] == "CPG_LOAD_FAILED"
                assert "generate_cpg" in failed["error"]
                assert "/private/" not in failed["error"]

                mock_services["joern_server_manager"] = manager
                manager.reload_with_retry.side_effect = None
                manager.reload_with_retry.return_value = CPGLoadResult("ok", port=14000, user_method_count=1)
                retry = await client.call_tool("generate_cpg", {
                    "source_type": "snippet", "language": "c", "code": code,
                })
                assert json.loads(retry.content[0].text)["status"] == "loading"
                task = core_tools._get_active_restart_task(mock_services, codebase_hash)
                if task:
                    await task
                status = await client.call_tool("get_cpg_status", {"codebase_hash": codebase_hash})
                ready = json.loads(status.content[0].text)
                assert (ready["status"], ready["joern_port"]) == ("ready", 14000)
                assert "error_code" not in ready
                assert ready["user_method_count"] == 1

        frontend.assert_called_once()
        manager.reload_with_retry.assert_called_with(codebase_hash, container_path)

    @pytest.mark.asyncio
    async def test_concurrent_builds_publish_their_own_method_counts(self, mock_services, tmp_path):
        from src.tools import core_tools

        counts = {"553642871dd4251d": 7, "abcdef0123456789": 23}
        first, second = counts
        manager = mock_services["joern_server_manager"]
        manager.reload_with_retry.side_effect = lambda key, path: CPGLoadResult(
            "ok", port=14000 + counts[key], user_method_count=counts[key],
        )
        docker_client = MagicMock()
        docker_client.containers.get.return_value.exec_run.return_value = MagicMock(exit_code=0)
        first_loaded = asyncio.Event()
        release_first = asyncio.Event()

        async def renew_first(publish=None):
            if publish:
                publish()
            elif manager.reload_with_retry.called:
                first_loaded.set()
                await release_first.wait()
            return True

        async def build(key, renew_claim=None):
            source = tmp_path / key
            source.mkdir()
            cpg_path = tmp_path / f"{key}.bin"
            cpg_path.write_bytes(b"fixture")
            await core_tools._generate_cpg_async(
                key, str(source), str(cpg_path), "python",
                f"/playground/cpgs/{key}/cpg.bin", mock_services, renew_claim=renew_claim,
            )

        with patch.object(core_tools.docker, "from_env", return_value=docker_client):
            pending = asyncio.create_task(build(first, renew_first))
            try:
                await asyncio.wait_for(first_loaded.wait(), 5)
                await build(second)
            finally:
                release_first.set()
                await pending

        assert {
            call.kwargs["codebase_hash"]: call.kwargs["metadata"]["user_method_count"]
            for call in mock_services["codebase_tracker"].update_codebase.call_args_list
            if call.kwargs.get("metadata", {}).get("status") == "ready"
        } == counts

    @pytest.mark.asyncio
    async def test_ready_build_records_provenance_before_reclaiming_source(self, mock_services, tmp_path):
        """The commit, Joern/frontend version, and build options outlive the checkout."""
        import subprocess
        from src.tools import core_tools

        key = "553642871dd4251d"
        checkout = tmp_path / key / "source"
        checkout.mkdir(parents=True)
        (checkout / "main.c").write_text("int main(void){return 0;}\n")
        git = ["git", "-C", str(checkout), "-c", "user.name=t", "-c", "user.email=t@example.com"]
        subprocess.run(["git", "init", "-q", str(checkout)], check=True)
        subprocess.run([*git, "add", "."], check=True)
        subprocess.run([*git, "commit", "-qm", "fixture"], check=True)
        commit = subprocess.run([*git, "rev-parse", "HEAD"], check=True, capture_output=True, text=True).stdout.strip()

        manager = mock_services["joern_server_manager"]
        manager.reload_with_retry.return_value = CPGLoadResult("ok", port=14000, user_method_count=3)
        manager.container_name = "codebadger-joern-server"
        manager.docker_client.containers.get.return_value.attrs = {
            "Config": {"Env": ["PATH=/usr/bin", "JOERN_VERSION=4.0.687"]},
        }
        core_tools._JOERN_VERSION_CACHE.clear()
        docker_client = MagicMock()
        docker_client.containers.get.return_value.exec_run.return_value = MagicMock(exit_code=0)
        cpg_path = tmp_path / "cpg.bin"
        cpg_path.write_bytes(b"fixture")

        with patch.object(core_tools.docker, "from_env", return_value=docker_client), \
             patch.object(core_tools, "_reclaim_source_snapshot") as reclaim:
            built = await core_tools._generate_cpg_async(
                key, str(tmp_path / key), str(cpg_path), "c", f"/playground/cpgs/{key}/cpg.bin",
                mock_services, defines=["FOO=1"], include_globs=["src/**"],
            )

        assert built is True
        ready = next(
            c.kwargs["metadata"] for c in mock_services["codebase_tracker"].update_codebase.call_args_list
            if c.kwargs.get("metadata", {}).get("status") == "ready"
        )
        provenance = ready["provenance"]
        assert provenance["known"] is True and provenance["commit"] == commit
        assert (provenance["joern_version"], provenance["frontend"]) == ("4.0.687", "c2cpg.sh")
        assert provenance["build_options"]["defines"] == ["FOO=1"]
        assert provenance["build_options"]["include_globs"] == ["src/**"]
        reclaim.assert_called_once()  # the checkout goes; provenance stays


    def test_status_provenance_summarizes_long_pattern_lists(self):
        from src.tools.core_tools import _public_provenance

        patterns = [f"p{i}" for i in range(100)]
        stored = {"known": True, "build_options": {"defines": ["A"], "build_settings": {
            "exclusion_patterns": patterns, "autodetect_compile_db": True}}}
        public = _public_provenance(stored)

        summary = public["build_options"]["build_settings"]["exclusion_patterns"]
        assert summary["count"] == 100 and len(summary["sha256"]) == 16
        assert public["build_options"]["defines"] == ["A"]
        assert stored["build_options"]["build_settings"]["exclusion_patterns"] == patterns  # untouched

    def test_checkout_commit_ignores_an_enclosing_repository(self, tmp_path):
        import subprocess
        from src.tools.core_tools import _checkout_commit

        subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
        (tmp_path / "x").write_text("x")
        subprocess.run(["git", "-C", str(tmp_path), "-c", "user.name=t", "-c", "user.email=t@e",
                        "commit", "-qm", "outer", "--allow-empty"], check=True)
        nested = tmp_path / "codebases" / "h" / "source"
        nested.mkdir(parents=True)

        assert _checkout_commit(str(tmp_path / "codebases" / "h")) is None

    @pytest.mark.asyncio
    async def test_status_reports_unknown_provenance_for_older_graphs(self, mock_services):
        from src.tools.core_tools import register_core_tools

        mock_services["codebase_tracker"].get_codebase.return_value = CodebaseInfo(
            codebase_hash="553642871dd4251d", source_type="github",
            source_path="https://github.com/test/repo", language="c", cpg_path="/tmp/test.cpg",
            metadata={"status": "sleeping"},
        )
        mock_services["joern_server_manager"].get_server_port.return_value = None
        mcp = FastMCP("TestServer")
        register_core_tools(mcp, mock_services)
        with patch("src.tools.core_tools._schedule_restart_server_task", return_value=False):
            async with Client(mcp) as client:
                status = (await client.call_tool(
                    "get_cpg_status", {"codebase_hash": "553642871dd4251d"}
                )).structured_content

        assert status["provenance"]["known"] is False
        assert "regenerate" in status["provenance"]["reason"]

    @pytest.mark.asyncio
    async def test_generate_cpg_async_fails_fast_when_cpg_exceeds_load_ceiling(self, mock_services, tmp_path):
        """A built CPG larger than max_load_mb is failed fast with CPG_TOO_LARGE guidance,
        instead of being handed to the loader to stall and emit an opaque error."""
        from unittest.mock import patch
        import src.tools.core_tools as core_tools

        # Tiny ceiling so a small fake CPG trips the guard.
        mock_services["config"].cpg.max_load_mb = 1  # 1 MB
        mock_services["config"].cpg.languages_with_exclusions = []  # avoid None-membership check

        codebase_dir = tmp_path / "src"
        codebase_dir.mkdir()
        (codebase_dir / "a.c").write_text("int main(void){return 0;}\n")
        cpg_path = tmp_path / "cpg.bin"
        cpg_path.write_text("x")  # real size is irrelevant; we patch getsize

        # Container whose frontend "succeeds" (exit 0); load must never be reached.
        container = MagicMock()
        container.exec_run.return_value = MagicMock(exit_code=0, output=b"ok")
        docker_client = MagicMock()
        docker_client.containers.get.return_value = container

        jsm = mock_services["joern_server_manager"]
        jsm.container_name = "codebadger-joern-server"

        real_getsize = os.path.getsize

        def fake_getsize(p):
            if str(p) == str(cpg_path):
                return 50 * 1024 * 1024  # 50 MB > 1 MB ceiling
            return real_getsize(p)

        with patch.object(core_tools.docker, "from_env", return_value=docker_client), \
             patch("os.path.getsize", side_effect=fake_getsize):
            await core_tools._generate_cpg_async(
                codebase_hash="553642871dd4251d",
                codebase_dir=str(codebase_dir),
                cpg_path=str(cpg_path),
                language="c",
                container_cpg_path="/playground/cpgs/553642871dd4251d/cpg.bin",
                services=mock_services,
            )

        # The CPG should never have been handed to the loader.
        jsm.reload_with_retry.assert_not_called()
        # And the codebase must be marked FAILED with the actionable code.
        calls = mock_services["codebase_tracker"].update_codebase.call_args_list
        meta = next(
            c.kwargs["metadata"] for c in reversed(calls)
            if c.kwargs.get("metadata", {}).get("error_code") == "CPG_TOO_LARGE"
        )
        assert meta["status"] in ("failed", "FAILED")
        assert "load ceiling" in meta["error"]

    @pytest.mark.asyncio
    async def test_generate_cpg_async_returns_failure_for_repository_size_rejection(
        self, mock_services, tmp_path, monkeypatch
    ):
        import src.tools.core_tools as core_tools

        mock_services["config"].cpg.max_repo_size_mb = 1
        monkeypatch.setattr(core_tools, "_calculate_repo_size_mb", lambda _path: 2)
        result = await core_tools._generate_cpg_async(
            codebase_hash="553642871dd4251d",
            codebase_dir=str(tmp_path),
            cpg_path=str(tmp_path / "cpg.bin"),
            language="c",
            container_cpg_path="/playground/cpgs/553642871dd4251d/cpg.bin",
            services=mock_services,
        )

        assert result is False
        failure = mock_services["codebase_tracker"].update_codebase.call_args.kwargs[
            "metadata"
        ]
        assert failure["status"] == "failed"
        assert "exceeds maximum allowed" in failure["error"]

    @pytest.mark.asyncio
    async def test_generate_cpg_async_returns_failure_for_frontend_error(
        self, mock_services, tmp_path
    ):
        from unittest.mock import patch
        import src.tools.core_tools as core_tools

        mock_services["config"].cpg.languages_with_exclusions = []
        source = tmp_path / "src"
        source.mkdir()
        (source / "main.c").write_text("int main(void) { return 0; }")
        container = MagicMock()
        container.exec_run.return_value = MagicMock(exit_code=1, output=b"compiler failed")
        docker_client = MagicMock()
        docker_client.containers.get.return_value = container
        with patch.object(core_tools.docker, "from_env", return_value=docker_client):
            result = await core_tools._generate_cpg_async(
                codebase_hash="553642871dd4251d",
                codebase_dir=str(source),
                cpg_path=str(tmp_path / "cpg.bin"),
                language="c",
                container_cpg_path="/playground/cpgs/553642871dd4251d/cpg.bin",
                services=mock_services,
            )

        assert result is False
        failure = mock_services["codebase_tracker"].update_codebase.call_args.kwargs[
            "metadata"
        ]
        assert failure["status"] == "failed"
        assert failure["error_code"]
        assert "compiler failed" in failure["error"]

    def test_gc_cold_cpgs_evicts_cold_servers_but_keeps_disk(self, mock_services):
        """Default GC frees allocations of cold running servers (delete_files=False)
        and never deletes the cpg.bin."""
        from datetime import datetime, timedelta, timezone
        from unittest.mock import patch
        import src.tools.core_tools as core_tools

        cold = (datetime.now(timezone.utc) - timedelta(seconds=100000)).isoformat()
        warm = datetime.now(timezone.utc).isoformat()

        def cb(h, last, status="ready"):
            return CodebaseInfo(
                codebase_hash=h, source_type="github",
                source_path="https://github.com/test/repo", language="c",
                cpg_path="/tmp/x", last_accessed=datetime.fromisoformat(last),
                metadata={"status": status},
            )

        mock_services["codebase_tracker"].list_codebases_full.return_value = [
            cb("a" * 16, cold), cb("b" * 16, warm),
        ]
        mgr = mock_services["joern_server_manager"]
        mgr.get_server_port.return_value = 2001       # all "have a port"
        mgr.is_server_running.return_value = True     # all "running"
        mock_services["cpg_queue"] = MagicMock(is_in_flight=MagicMock(return_value=False))

        teardown_calls = []
        with patch.object(core_tools, "_teardown_codebase",
                          side_effect=lambda s, h, delete_files=True: teardown_calls.append((h, delete_files)) or 0):
            summary = core_tools.gc_cold_cpgs(mock_services, max_age_seconds=3600, max_count=50, delete_cold=False)

        # Only the cold codebase is evicted; eviction keeps the binary (delete_files=False).
        assert teardown_calls == [("a" * 16, False)]
        assert summary["evicted"] == ["repo@aaaaaaaa"]
        assert summary["deleted"] == []

    def test_gc_cold_cpgs_opt_in_deletes_cold_binaries(self, mock_services):
        """With delete_cold=True, cold sleeping (not-running) CPG binaries are deleted."""
        from datetime import datetime, timedelta, timezone
        from unittest.mock import patch
        import src.tools.core_tools as core_tools

        cold = (datetime.now(timezone.utc) - timedelta(seconds=100000)).isoformat()
        info = CodebaseInfo(
            codebase_hash="c" * 16, source_type="github",
            source_path="https://github.com/test/repo", language="c", cpg_path="/tmp/x",
            last_accessed=datetime.fromisoformat(cold), metadata={"status": "sleeping"},
        )
        mock_services["codebase_tracker"].list_codebases_full.return_value = [info]
        mgr = mock_services["joern_server_manager"]
        mgr.get_server_port.return_value = None       # not running
        mgr.is_server_running.return_value = False
        mock_services["cpg_queue"] = MagicMock(is_in_flight=MagicMock(return_value=False))

        teardown_calls = []
        with patch.object(core_tools, "_teardown_codebase",
                          side_effect=lambda s, h, delete_files=True: teardown_calls.append((h, delete_files)) or 1024), \
             patch.object(core_tools, "_cpgs_disk_usage", return_value=(1, 1.0)):
            summary = core_tools.gc_cold_cpgs(mock_services, max_age_seconds=3600, max_count=50, delete_cold=True)

        assert teardown_calls == [("c" * 16, True)]
        assert summary["deleted"] == ["repo@cccccccc"]

    @pytest.mark.asyncio
    async def test_get_backend_status_reports_capacity_and_load(self, mock_services):
        """get_backend_status surfaces queue depth, server load, memory and CPG list."""
        from datetime import datetime, timezone
        from src.tools.core_tools import register_core_tools

        cpg_queue = MagicMock()
        cpg_queue.depth = 2
        cpg_queue.maxsize = 0
        cpg_queue.in_flight = 3
        mock_services["cpg_queue"] = cpg_queue

        mgr = mock_services["joern_server_manager"]
        mgr.get_running_servers.return_value = {"a" * 16: 2001, "b" * 16: 2002}
        mgr._max_active = 3
        mgr.get_memory_stats.return_value = {"budget_mb": 24000, "reserved_mb": 8000, "free_mb": 16000}

        mock_services["codebase_tracker"].list_codebase_page = _catalog_page_stub([
            CodebaseInfo(
                codebase_hash="553642871dd4251d",
                source_type="github",
                source_path="https://github.com/test/repo.git",
                language="c",
                cpg_path="/tmp/test.cpg",
                last_accessed=datetime.now(timezone.utc),
                metadata={"status": "ready", "phase": "ready", "repository": "https://github.com/test/repo.git"},
            )
        ])

        mcp = FastMCP("TestServer")
        register_core_tools(mcp, mock_services)

        async with Client(mcp) as client:
            result = await client.call_tool("get_backend_status", {})
            import json
            r = json.loads(result.content[0].text)

            assert r["recommended_max_concurrent_builds"] == r["build_workers"]
            assert r["queue_depth"] == 2
            assert r["in_flight"] == 3
            assert r["active_servers"] == 2
            assert r["max_active_servers"] == 3
            assert r["memory"]["budget_mb"] == 24000
            assert r["total"] == 1
            assert r["cpgs"][0]["codebase_label"] == "repo@55364287"
            assert r["cpgs"][0]["status"] == "ready"
            assert r["page"] == 1
            assert r["page_size"] == 50
            assert r["total_pages"] == 1
            assert r["truncated"] is False

    @pytest.mark.asyncio
    async def test_core_status_tools_expose_titles_and_safe_annotations(self, mock_services):
        from src.tools.core_tools import register_core_tools
        from src.tools.code_browsing_tools import register_code_browsing_tools
        from src.tools.taint_analysis_tools import register_taint_analysis_tools
        from src.tools.custom_tools import register_custom_tools

        mcp = FastMCP("TestServer")
        register_core_tools(mcp, mock_services)
        register_code_browsing_tools(mcp, mock_services)
        register_taint_analysis_tools(mcp, mock_services)
        register_custom_tools(mcp, mock_services)

        async with Client(mcp) as client:
            tools = await client.list_tools()

            assert tools, "No MCP tools registered"
            for tool in tools:
                assert tool.title, f"Missing title for {tool.name}"
                assert tool.description, f"Missing description for {tool.name}"
                assert tool.annotations is not None, f"Missing annotations for {tool.name}"
                assert tool.output_schema is not None, f"Missing output schema for {tool.name}"

        by_name = {tool.name: tool for tool in tools}
        assert by_name["get_cpg_status"].title == "Get CPG Status"
        assert by_name["get_backend_status"].title == "Get Backend Status"
        assert by_name["get_cpg_status"].annotations.read_only_hint is True
        assert by_name["get_backend_status"].annotations.destructive_hint is False
        assert by_name["remove_cpg"].annotations.destructive_hint is True
        assert by_name["generate_cpg"].title == "Generate Code Property Graph"
        assert by_name["generate_cpg"].annotations.read_only_hint is False
        assert by_name["get_cpg_status"].output_schema["type"] == "object"
        assert by_name["get_cpg_status"].output_schema["additionalProperties"] is True
        assert by_name["get_backend_status"].output_schema["type"] == "object"

        for name in ("list_methods", "list_calls", "get_call_graph", "get_type_definition", "list_parameters", "run_cpgql_query", "get_cfg", "find_bounds_checks", "get_cpgql_syntax_help"):
            assert by_name[name].title
            assert by_name[name].annotations.read_only_hint is True
        for name in (
            "find_taint_sources", "find_taint_sinks", "find_taint_flows",
            "get_program_slice", "get_variable_flow", "find_use_after_free",
            "find_double_free", "find_null_pointer_deref", "find_integer_overflow",
            "find_format_string_vulns", "find_heap_overflow", "find_stack_overflow",
            "find_toctou", "find_uninitialized_reads",
        ):
            assert by_name[name].title
            assert by_name[name].annotations.read_only_hint is True
        methods_schema = by_name["list_methods"].output_schema
        assert methods_schema["required"] == ["success"]
        assert methods_schema["properties"]["truncated"]["type"] == "boolean"
        for name in ("list_methods", "list_calls", "find_taint_sources", "find_taint_sinks"):
            props = by_name[name].output_schema["properties"]
            assert "total" in props and "returned" in props and "truncated" in props
        calls_schema = by_name["list_calls"].output_schema
        assert calls_schema["properties"]["calls"]["items"]["properties"]["callee"]["type"] == "string"
        assert by_name["get_type_definition"].output_schema["properties"]["types"]["type"] == "array"
        assert by_name["list_parameters"].output_schema["properties"]["methods"]["type"] == "array"
        assert by_name["run_cpgql_query"].output_schema["properties"]["data"] == {}
        assert by_name["find_taint_sources"].output_schema["properties"]["sources"]["type"] == "array"
        assert by_name["find_taint_sinks"].output_schema["properties"]["sinks"]["type"] == "array"
        assert by_name["find_taint_flows"].title == "Find Taint Flows"
        assert by_name["get_program_slice"].title == "Get Program Slice"
        assert by_name["get_variable_flow"].title == "Get Variable Flow"
        assert by_name["find_use_after_free"].title == "Find Use-After-Free Issues"
        assert by_name["find_double_free"].title == "Find Double-Free Issues"
        assert by_name["find_null_pointer_deref"].title == "Find Null Pointer Dereferences"
        assert by_name["find_integer_overflow"].title == "Find Integer Overflows"
        assert by_name["find_format_string_vulns"].title == "Find Format String Vulnerabilities"
        assert by_name["find_heap_overflow"].title == "Find Heap Overflows"
        assert by_name["find_stack_overflow"].title == "Find Stack Overflows"
        assert by_name["find_toctou"].title == "Find TOCTOU Issues"
        assert by_name["find_uninitialized_reads"].title == "Find Uninitialized Reads"
        assert by_name["find_command_injection_sinks"].output_schema["properties"]["summary"]["type"] == "string"
        assert by_name["get_call_graph"].output_schema["properties"]["summary"]["type"] == "string"
        assert by_name["get_call_graph"].input_schema["properties"]["detail"]["default"] == "compact"
        assert by_name["find_taint_flows"].output_schema["properties"]["summary"]["type"] == "string"
        assert by_name["find_taint_flows"].input_schema["properties"]["detail"]["default"] == "compact"
        assert by_name["get_cfg"].output_schema["properties"]["summary"]["type"] == "string"
        assert by_name["find_bounds_checks"].output_schema["properties"]["summary"]["type"] == "string"
        assert by_name["get_program_slice"].output_schema["properties"]["summary"]["type"] == "string"
        assert by_name["get_program_slice"].input_schema["properties"]["detail"]["default"] == "compact"
        assert by_name["get_variable_flow"].output_schema["properties"]["summary"]["type"] == "string"
        assert by_name["get_variable_flow"].input_schema["properties"]["detail"]["default"] == "compact"
        assert by_name["find_use_after_free"].output_schema["properties"]["summary"]["type"] == "string"
        assert by_name["find_double_free"].output_schema["properties"]["summary"]["type"] == "string"
        assert by_name["find_null_pointer_deref"].output_schema["properties"]["summary"]["type"] == "string"
        assert by_name["find_integer_overflow"].output_schema["properties"]["summary"]["type"] == "string"
        assert by_name["find_format_string_vulns"].output_schema["properties"]["summary"]["type"] == "string"
        assert by_name["find_heap_overflow"].output_schema["properties"]["summary"]["type"] == "string"
        assert by_name["find_stack_overflow"].output_schema["properties"]["summary"]["type"] == "string"
        assert by_name["find_toctou"].output_schema["properties"]["summary"]["type"] == "string"
        assert by_name["find_uninitialized_reads"].output_schema["properties"]["summary"]["type"] == "string"
        assert by_name["get_cpgql_syntax_help"].output_schema["required"] == ["success"]

    @pytest.mark.asyncio
    async def test_get_backend_status_rows_resume_same_named_projects(self, mock_services):
        """A fresh client can pick either same-named project and call the next tool with its row."""
        from datetime import datetime, timezone
        from src.tools.core_tools import register_core_tools

        now = datetime.now(timezone.utc)
        catalog = {
            cb.codebase_hash: cb
            for cb in [
                CodebaseInfo(
                    codebase_hash="1111aaaa00000001", source_type="local",
                    source_path="/src/a/libpng", language="c", cpg_path="/tmp/a.cpg",
                    last_accessed=now, metadata={"status": "ready"},
                ),
                CodebaseInfo(
                    codebase_hash="1111aaaa00000002", source_type="local",
                    source_path="/src/b/libpng", language="c", cpg_path="/tmp/b.cpg",
                    last_accessed=now, metadata={"status": "sleeping"},
                ),
                CodebaseInfo(
                    codebase_hash="2222bbbb00000003", source_type="github",
                    source_path="https://github.com/x/libpng", language="java",
                    cpg_path="/tmp/c.cpg", last_accessed=now,
                    metadata={"status": "ready", "repository": "https://github.com/x/libpng.git"},
                ),
                CodebaseInfo(
                    codebase_hash="3333cccc00000004", source_type="local",
                    source_path="/src/zlib", language="c", cpg_path="/tmp/d.cpg",
                    last_accessed=now, metadata={"status": "ready"},
                ),
            ]
        }
        tracker = mock_services["codebase_tracker"]
        tracker.list_codebase_page = _catalog_page_stub(list(catalog.values()))
        tracker.get_codebase.side_effect = catalog.get

        mcp = FastMCP("TestServer")
        register_core_tools(mcp, mock_services)

        async with Client(mcp) as client:
            listing = (await client.call_tool(
                "get_backend_status", {"project": "LibPNG", "language": "c"}
            )).structured_content
            by_language = (await client.call_tool(
                "get_backend_status", {"language": "java"}
            )).structured_content
            statuses = [
                (await client.call_tool(
                    "get_cpg_status", {"codebase_hash": row["codebase_hash"]}
                )).structured_content
                for row in listing["cpgs"]
            ]

        rows = listing["cpgs"]
        assert listing["total"] == 2
        assert listing["truncated"] is False
        assert {row["codebase_hash"] for row in rows} == {"1111aaaa00000001", "1111aaaa00000002"}
        # The short labels collide; the full hash is what tells the rows apart.
        assert {row["codebase_label"] for row in rows} == {"libpng@1111aaaa"}
        assert {row["project"] for row in rows} == {"libpng"}
        assert [s["codebase_hash"] for s in statuses] == [row["codebase_hash"] for row in rows]
        assert [row["codebase_hash"] for row in by_language["cpgs"]] == ["2222bbbb00000003"]
        assert by_language["total"] == 1

    @pytest.mark.asyncio
    async def test_get_backend_status_pages_large_cpg_catalog(self, mock_services):
        from datetime import datetime, timezone
        from src.tools.core_tools import register_core_tools

        now = datetime.now(timezone.utc)
        page_stub = _catalog_page_stub([
            CodebaseInfo(
                codebase_hash=f"{index:016x}", source_type="local",
                source_path="/tmp/repo", language="c", cpg_path="/tmp/test.cpg",
                last_accessed=now, metadata={"status": "ready"},
            )
            for index in range(55)
        ])
        mock_services["codebase_tracker"].list_codebase_page = page_stub

        mcp = FastMCP("TestServer")
        register_core_tools(mcp, mock_services)

        async with Client(mcp) as client:
            result = await client.call_tool(
                "get_backend_status", {"page": 2, "page_size": 20}
            )
            import json
            response = json.loads(result.content[0].text)

        assert response["total"] == 55
        assert len(response["cpgs"]) == 20
        assert response["page"] == 2
        assert response["page_size"] == 20
        assert response["returned"] == 20
        assert response["total_pages"] == 3
        assert response["truncated"] is True
        # Only the requested page is asked for; the full catalog is never listed.
        page_stub.assert_called_once_with(offset=20, limit=20, language=None, project=None)
        mock_services["codebase_tracker"].list_codebases_full.assert_not_called()

    @pytest.mark.asyncio
    async def test_get_cpg_status_not_found(self, mock_services):
        """Test getting CPG status when CPG doesn't exist (valid-format but unknown hash)"""
        from src.tools.core_tools import register_core_tools

        mock_services["codebase_tracker"].get_codebase.return_value = None
        unknown_hash = "0123456789abcdef"

        mcp = FastMCP("TestServer")
        register_core_tools(mcp, mock_services)

        async with Client(mcp) as client:
            result = await client.call_tool("get_cpg_status", {"codebase_hash": unknown_hash})
            import json
            result_dict = json.loads(result.content[0].text)

            assert result_dict["codebase_hash"] == unknown_hash
            assert result_dict["status"] == "not_found"

    @pytest.mark.asyncio
    async def test_get_cpg_status_rejects_malformed_hash(self, mock_services):
        """A malformed codebase_hash is rejected by validation, not treated as not_found."""
        from src.tools.core_tools import register_core_tools

        mcp = FastMCP("TestServer")
        register_core_tools(mcp, mock_services)

        async with Client(mcp) as client:
            result = await _call_err(client, "get_cpg_status", {"codebase_hash": "nonexistent"})
            import json
            result_dict = _error_reply(result)

            assert result_dict["success"] is False
            assert result_dict["error_code"] == "VALIDATION_ERROR"
            assert "16-character hex" in result_dict["error"]

    @pytest.mark.asyncio
    async def test_remove_cpg_rejects_malformed_hash(self, mock_services):
        """remove_cpg validates the hash before any DB/filesystem action."""
        from src.tools.core_tools import register_core_tools

        mcp = FastMCP("TestServer")
        register_core_tools(mcp, mock_services)

        async with Client(mcp) as client:
            result = await _call_err(client,
                "remove_cpg", {"codebase_hash": "../../etc", "delete_files": True}
            )
            import json
            result_dict = _error_reply(result)

            assert result_dict["success"] is False
            assert result_dict["error_code"] == "VALIDATION_ERROR"
            assert "16-character hex" in result_dict["error"]
            mock_services["codebase_tracker"].get_codebase.assert_not_called()

    @pytest.mark.asyncio
    async def test_list_methods_success(self, mock_services):
        """Test listing methods successfully"""
        from src.tools.code_browsing_tools import register_code_browsing_tools
        
        mcp = FastMCP("TestServer")
        register_code_browsing_tools(mcp, mock_services)

        async with Client(mcp) as client:
            result = await client.call_tool("list_methods", {"codebase_hash": "553642871dd4251d"})
            import json
            result_dict = json.loads(result.content[0].text)

            assert result_dict["success"] is True
            assert "methods" in result_dict
            assert isinstance(result_dict["methods"], list)

    @pytest.mark.asyncio
    async def test_run_cpgql_query_success(self, mock_services):
        """Test running CPGQL query successfully"""
        from src.tools.code_browsing_tools import register_code_browsing_tools
        
        mcp = FastMCP("TestServer")
        register_code_browsing_tools(mcp, mock_services)

        # Patch the query_executor to return a structured QueryResult
        from src.models import QueryResult
        mock_services["query_executor"].execute_query.return_value = QueryResult(
            success=True,
            data=["result"],
            row_count=1,
        )

        async with Client(mcp) as client:
            result = await client.call_tool("run_cpgql_query", {"codebase_hash": "553642871dd4251d", "query": "cpg.method"})
            import json
            result_dict = json.loads(result.content[0].text)

            assert result_dict["success"] is True
            assert result_dict["data"] == ["result"]

    @pytest.mark.asyncio
    async def test_run_cpgql_query_invalid(self, mock_services):
        """Test running invalid CPGQL query"""
        from src.tools.code_browsing_tools import register_code_browsing_tools
        
        mcp = FastMCP("TestServer")
        register_code_browsing_tools(mcp, mock_services)

        from src.models import QueryResult
        mock_services["query_executor"].execute_query.return_value = QueryResult(
            success=False,
            error="Invalid query syntax",
            data=[],
            row_count=0,
        )

        async with Client(mcp) as client:
            result = await client.call_tool(
                "run_cpgql_query",
                {"codebase_hash": "553642871dd4251d", "query": "invalid query"},
                raise_on_error=False,
            )

            assert result.is_error
            assert result.content[0].text.startswith("QUERY_ERROR: Invalid query syntax")

    @pytest.mark.asyncio
    async def test_run_cpgql_query_blocks_dangerous_query(self, mock_services):
        """The security blocklist is enforced on the raw-query path, before execution."""
        from src.tools.code_browsing_tools import register_code_browsing_tools

        mcp = FastMCP("TestServer")
        register_code_browsing_tools(mcp, mock_services)

        async with Client(mcp) as client:
            result = await client.call_tool(
                "run_cpgql_query",
                {
                    "codebase_hash": "553642871dd4251d",
                    "query": 'scala.io.Source.fromFile("/etc/passwd").mkString',
                },
                raise_on_error=False,
            )

            assert result.is_error
            text = result.content[0].text
            assert text.startswith("VALIDATION_ERROR: ")
            assert "dangerous operation" in text
            # Blocked before ever reaching the executor.
            mock_services["query_executor"].execute_query.assert_not_called()

    @pytest.mark.asyncio
    async def test_run_cpgql_query_surfaces_truncation(self, mock_services):
        """A truncated QueryResult is flagged to the client."""
        from src.tools.code_browsing_tools import register_code_browsing_tools
        from src.models import QueryResult

        mcp = FastMCP("TestServer")
        register_code_browsing_tools(mcp, mock_services)
        mock_services["query_executor"].execute_query.return_value = QueryResult(
            success=True, data=["x"], row_count=1, truncated=True
        )

        async with Client(mcp) as client:
            result = await client.call_tool(
                "run_cpgql_query",
                {"codebase_hash": "553642871dd4251d", "query": "cpg.method.name.l"},
            )
            import json
            result_dict = json.loads(result.content[0].text)

            assert result_dict["success"] is True
            assert result_dict["truncated"] is True
            assert "truncation_note" in result_dict









    @pytest.mark.asyncio
    async def test_get_cfg_success(self, mock_services):
        """get_cfg returns structured nodes/edges, totals and a text summary"""
        import json
        from src.tools.code_browsing_tools import register_code_browsing_tools

        payload = [{
            "status": "ok", "total_nodes": 3, "total_edges": 3,
            "method": {"node_id": "1000", "name": "test_func", "full_name": "test_func", "filename": "t.c", "line": 1},
            "nodes": [
                {"node_id": "1000", "kind": "METHOD", "code": "int test_func(int x)", "line": 1, "out_degree": 1},
                {"node_id": "1001", "kind": "CALL", "code": "x > 0", "line": 2, "out_degree": 2},
            ],
            "edges": [{"from": "1000", "to": "1001", "branch": False}],
        }]
        mock_services["query_executor"].execute_query.return_value = QueryResult(
            success=True,
            data=[json.dumps(payload)],
            row_count=1
        )

        mcp = FastMCP("TestServer")
        register_code_browsing_tools(mcp, mock_services)

        async with Client(mcp) as client:
            result = await client.call_tool("get_cfg", {
                "codebase_hash": "553642871dd4251d",
                "method_name": "test_func"
            })

        data = result.structured_content
        assert data["method"]["name"] == "test_func"
        assert (data["total"], data["returned"], data["truncated"]) == (3, 2, True)
        assert (data["total_edges"], data["returned_edges"]) == (3, 1)
        assert "[1001] CALL line 2: x > 0" in data["summary"]
        assert "TRUNCATED: showing 2 of 3 nodes" in data["summary"]

    @pytest.mark.asyncio
    async def test_get_type_definition_success(self, mock_services):
        """Types carry member totals, truncation, aliases and empty declarations."""
        import json
        from src.tools.code_browsing_tools import register_code_browsing_tools

        payload = [{"total": 3, "types": [
            {"name": "Wide", "fullName": "Wide", "filename": "t.c", "lineNumber": 2, "aliasOf": "",
             "forward_declaration": False, "member_total": 21,
             "members": [{"name": f"m{i:02d}", "type": "int"} for i in range(1, 21)]},
            {"name": "Empty", "fullName": "Empty", "filename": "t.c", "lineNumber": 25, "aliasOf": "",
             "forward_declaration": False, "member_total": 0, "members": []},
        ]}]
        mock_services["query_executor"].execute_query.return_value = QueryResult(
            success=True, data=[json.dumps(payload)], row_count=1)

        mcp = FastMCP("TestServer")
        register_code_browsing_tools(mcp, mock_services)

        async with Client(mcp) as client:
            result = await client.call_tool("get_type_definition", {
                "codebase_hash": "553642871dd4251d", "type_name": ".*", "limit": 2, "member_limit": 20,
            })
        data = result.structured_content
        assert (data["total"], data["returned"], data["truncated"]) == (3, 2, True)
        wide, empty = data["types"]
        assert (wide["member_total"], len(wide["members"]), wide["members_truncated"]) == (21, 20, True)
        assert wide["aliasOf"] is None
        assert (empty["member_total"], empty["members_truncated"], empty["forward_declaration"]) == (0, False, False)
        query = mock_services["query_executor"].execute_query.call_args.kwargs["query"]
        assert "members.take(20)" in query and ".take(2)" in query

    @pytest.mark.asyncio
    async def test_warns_when_guard_on_and_over_threshold(self, mock_services, tmp_path):
        from src.tools.core_tools import register_core_tools
        mock_services["codebase_tracker"].get_codebase.return_value = None
        src = tmp_path / "src"; src.mkdir()
        # default config: guard on, thresholds 2000 MB / 2M LOC -> 3M LOC trips it
        with patch("src.tools.core_tools.resolve_host_path", return_value=str(src)), \
             patch("src.tools.core_tools._scan_repo", return_value=(3000, 3_000_000)):
            mcp = FastMCP("t"); register_core_tools(mcp, mock_services)
            async with Client(mcp) as client:
                result = await client.call_tool(
                    "generate_cpg",
                    {"source_type": "local", "source_path": str(src), "language": "c"},
                )
            import json; d = json.loads(result.content[0].text)
            assert d["status"] == "large_project_warning"
            assert d["lines_of_code"] == 3_000_000

    @pytest.mark.asyncio
    async def test_guard_off_skips_warning_and_builds(self, mock_services, tmp_path):
        from unittest.mock import AsyncMock
        from src.tools.core_tools import register_core_tools
        mock_services["codebase_tracker"].get_codebase.return_value = None
        mock_services["config"].cpg.large_project_guard = False   # batch driver
        mock_services["cpg_queue"] = MagicMock()
        mock_services["cpg_queue"].submit = AsyncMock(return_value="queued")
        src = tmp_path / "src"; src.mkdir()
        with patch("src.tools.core_tools.resolve_host_path", return_value=str(src)), \
             patch("src.tools.core_tools._get_git_commit_hash", return_value=None), \
             patch("src.tools.core_tools._copy_local_source_tree"), \
             patch("src.tools.core_tools.os.path.abspath", return_value=str(tmp_path)), \
             patch("src.tools.core_tools.os.path.dirname", return_value=str(tmp_path)), \
             patch("src.tools.core_tools.os.path.join", side_effect=os.path.join):
            mcp = FastMCP("t"); register_core_tools(mcp, mock_services)
            async with Client(mcp) as client:
                result = await client.call_tool(
                    "generate_cpg",
                    {"source_type": "local", "source_path": str(src), "language": "c"},
                )
            import json; d = json.loads(result.content[0].text)
            assert d["status"] == "generating"          # guard off -> built, not declined


class TestCpgBuildFailureLabeling:
    """Fix #4: failed CPG builds carry a labeled cause (OOM/TIMEOUT/BUILD_ERROR)."""

    def test_classifier_oom_by_exit_137(self):
        from src.tools.core_tools import _classify_cpg_build_failure
        code, msg = _classify_cpg_build_failure(137, "killed", 28)
        assert code == "OOM" and "-Xmx28G" in msg

    def test_classifier_oom_by_marker(self):
        from src.tools.core_tools import _classify_cpg_build_failure
        code, _ = _classify_cpg_build_failure(1, "x java.lang.OutOfMemoryError: Java heap space", 28)
        assert code == "OOM"

    def test_classifier_generic_build_error(self):
        from src.tools.core_tools import _classify_cpg_build_failure
        code, _ = _classify_cpg_build_failure(2, "parse error", 28)
        assert code == "BUILD_ERROR"

    def test_classifier_bounds_output(self):
        from src.tools.core_tools import _classify_cpg_build_failure
        _, msg = _classify_cpg_build_failure(1, "X" * 9000, 28)
        assert len(msg) < 2300   # frontend dump tail-bounded

    @pytest.mark.asyncio
    async def test_get_cpg_status_surfaces_failure_cause(self, mock_services):
        from src.tools.core_tools import register_core_tools
        mock_services["codebase_tracker"].get_codebase.return_value = CodebaseInfo(
            codebase_hash="0123456789abcdef",
            source_type="local",
            source_path="/x",
            language="c",
            cpg_path=None,
            metadata={"status": "failed", "error_code": "OOM", "error": "ran out of memory"},
        )
        mcp = FastMCP("t"); register_core_tools(mcp, mock_services)
        async with Client(mcp) as client:
            result = await client.call_tool("get_cpg_status", {"codebase_hash": "0123456789abcdef"})
        import json; d = json.loads(result.content[0].text)
        assert d["status"] == "failed"
        assert d["error_code"] == "OOM"
        assert "memory" in d["error"]

    @pytest.mark.asyncio
    async def test_contract_representative_success_and_native_error(self, mock_services):
        from src.tools.core_tools import register_core_tools
        from src.tools.code_browsing_tools import register_code_browsing_tools

        mock_services["codebase_tracker"].get_codebase.return_value = CodebaseInfo(
            codebase_hash="0123456789abcdef", source_type="local", source_path="/x",
            language="c", cpg_path="/x/cpg.bin", metadata={"status": "ready"},
        )
        mcp = FastMCP("contract")
        register_core_tools(mcp, mock_services)
        register_code_browsing_tools(mcp, mock_services)
        async with Client(mcp) as client:
            success = await client.call_tool("get_cpg_status", {"codebase_hash": "0123456789abcdef"})
            assert success.content and success.content[0].type == "text"
            with pytest.raises(ToolError, match="VALIDATION_ERROR"):
                await client.call_tool("get_call_graph", {
                    "codebase_hash": "0123456789abcdef", "method_name": "main", "depth": 99,
                })
