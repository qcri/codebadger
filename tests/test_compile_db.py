"""Unit tests for compile_commands.json rebasing."""

import json

from src.utils.compile_db import (
    _rebase_path,
    rebase_entries,
    prepare_container_compile_db,
    find_compile_db,
)


class TestFindCompileDb:
    def _db(self, p):
        p.write_text('[{"file":"a.c"}]')

    def test_finds_at_root(self, tmp_path):
        self._db(tmp_path / "compile_commands.json")
        assert find_compile_db(str(tmp_path)) == "compile_commands.json"

    def test_finds_in_build_dir(self, tmp_path):
        (tmp_path / "build").mkdir()
        self._db(tmp_path / "build" / "compile_commands.json")
        assert find_compile_db(str(tmp_path)) == "build/compile_commands.json"

    def test_none_when_absent(self, tmp_path):
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "a.c").write_text("int main(){}")
        assert find_compile_db(str(tmp_path)) is None

    def test_prefers_largest(self, tmp_path):
        (tmp_path / "build").mkdir()
        (tmp_path / "compile_commands.json").write_text('[{"file":"a.c"}]')
        # bigger DB in build/ should win (more coverage)
        (tmp_path / "build" / "compile_commands.json").write_text(
            '[{"file":"a.c"},{"file":"b.c"},{"file":"c.c"}]' + " " * 200
        )
        assert find_compile_db(str(tmp_path)) == "build/compile_commands.json"


class TestRebasePath:
    def test_rebases_under_host_root(self):
        assert _rebase_path("/home/u/proj/src/a.c", "/home/u/proj", "/playground/cb/h") \
            == "/playground/cb/h/src/a.c"

    def test_root_itself(self):
        assert _rebase_path("/home/u/proj", "/home/u/proj", "/pg/h") == "/pg/h"

    def test_relative_passthrough(self):
        assert _rebase_path("src/a.c", "/home/u/proj", "/pg/h") == "src/a.c"

    def test_outside_root_passthrough(self):
        assert _rebase_path("/usr/include/stdio.h", "/home/u/proj", "/pg/h") \
            == "/usr/include/stdio.h"

    def test_no_host_root(self):
        assert _rebase_path("/abs/x.c", "", "/pg/h") == "/abs/x.c"


class TestRebaseEntries:
    def test_rebases_directory_and_file(self):
        entries = [
            {"directory": "/home/u/proj/build", "file": "/home/u/proj/src/a.c",
             "command": "cc -c a.c"},
            {"directory": "/home/u/proj", "file": "/home/u/proj/b.c", "arguments": ["cc", "b.c"]},
        ]
        out, n = rebase_entries(entries, "/home/u/proj", "/pg/h")
        assert n == 4  # 2 dir + 2 file
        assert out[0]["file"] == "/pg/h/src/a.c"
        assert out[0]["directory"] == "/pg/h/build"
        assert out[1]["file"] == "/pg/h/b.c"
        # command/arguments preserved
        assert out[0]["command"] == "cc -c a.c"

    def test_none_host_root_passthrough(self):
        entries = [{"directory": "/x", "file": "/x/a.c"}]
        out, n = rebase_entries(entries, None, "/pg/h")
        assert n == 0
        assert out == entries


class TestPrepareContainerCompileDb:
    def test_roundtrip(self, tmp_path):
        db = tmp_path / "compile_commands.json"
        db.write_text(json.dumps([
            {"directory": "/src/proj", "file": "/src/proj/a.c", "command": "cc -c a.c"},
        ]))
        out = tmp_path / "compile_commands.container.json"
        result = prepare_container_compile_db(str(db), "/src/proj", "/pg/h", str(out))
        assert result is not None
        out_path, count, rebased, reachable = result
        assert count == 1 and rebased == 2 and reachable == 1
        written = json.loads(out.read_text())
        assert written[0]["file"] == "/pg/h/a.c"

    def test_missing_file_returns_none(self, tmp_path):
        assert prepare_container_compile_db(
            str(tmp_path / "nope.json"), "/x", "/pg/h", str(tmp_path / "o.json")
        ) is None

    def test_bad_json_returns_none(self, tmp_path):
        db = tmp_path / "compile_commands.json"
        db.write_text("{not json")
        assert prepare_container_compile_db(str(db), "/x", "/pg/h", str(tmp_path / "o.json")) is None

    def test_empty_array_returns_none(self, tmp_path):
        db = tmp_path / "compile_commands.json"
        db.write_text("[]")
        assert prepare_container_compile_db(str(db), "/x", "/pg/h", str(tmp_path / "o.json")) is None



class TestReachableEntries:
    def test_counts_files_inside_the_tree(self):
        from src.utils.compile_db import reachable_entries

        entries = [
            {"directory": "/pg/h", "file": "/pg/h/a.c"},          # absolute, inside
            {"directory": "/pg/h/src", "file": "b.c"},            # relative to an inside dir
            {"file": "c.c"},                                      # relative, no directory
            {"directory": "/build/root", "file": "/build/root/d.c"},  # another machine
            {"directory": "/build/root", "file": "e.c"},          # relative to a foreign dir
            {"directory": "/pg/h", "file": "../escape.c"},        # escapes the tree
        ]
        assert reachable_entries(entries, "/pg/h") == 3

    def test_foreign_database_has_no_reachable_entries(self, tmp_path):
        db = tmp_path / "compile_commands.json"
        db.write_text(json.dumps([{"directory": "/build/root", "file": "/build/root/main.c"}]))
        result = prepare_container_compile_db(str(db), "/host/src", "/pg/h", str(tmp_path / "o.json"))
        assert result[1:] == (1, 0, 0)
