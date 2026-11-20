"""Default exclusion rules: whole directories and test-file conventions, never word prefixes.

Each path is checked under both frontend regimes: full match against the relative
path (JVM frontends) and unanchored search against the absolute path (astgen).
"""

import re
from unittest.mock import MagicMock

import pytest

from src import defaults
from src.models import Config
from src.tools.core_tools import _count_excluded_files, _exclusion_patterns

ROOT = "/playground/codebases/0123456789abcdef/"

KEEP = {  # product code the old prefix rules dropped (found on the 30-repo corpus)
    "c": ["src/util/alloc.c", "src/testhelp.h", "src/toolx/tool_time.c", "src/misc/ngx_mod.c",
          "src/http/modules/perl/ngx_http_perl_module.c", "utils/corrupt_rdb.c", "src/builtin/x.c",
          "src/units.c", "src/configure_args.c", "src/logic/eval.c", "source/src/jv.c"],
    "cpp": ["util/arena.cc", "helpers/memenv/memenv.cc", "src/perf_event.cc"],
    "java": ["src/main/java/org/apache/commons/lang3/builder/Builder.java",
             "src/main/java/org/springframework/samples/petclinic/model/Person.java",
             "gson/src/main/java/com/google/gson/internal/bind/util/ISO8601Utils.java",
             "src/main/java/com/acme/test/Fixtures.java"],
    "kotlin": ["okhttp/src/commonJvmAndroid/kotlin/okhttp3/internal/cache/CacheInterceptor.kt",
               "okcurl/src/main/kotlin/okhttp3/curl/logging/LoggingUtil.kt"],
    "javascript": ["lib/helpers/AxiosTransformStream.js", "scripts/axios-build-instance.js", "lib/router.js"],
    "python": ["django/core/cache/__init__.py", "django/db/migrations/operations/special.py",
               "django/contrib/gis/utils/__init__.py", "src/testing_helpers.py"],
    "go": ["util/annotations/annotations.go", "tsdb/chunks/samples.go", "cmd/promtool/unittest.go",
           "internal/tools/tools.go", "go/ast/ast.go"],
    "php": ["src/Illuminate/Foundation/Testing/Attributes/Seed.php", "Debug/CliRequest.php"],
    "ruby": ["guides/bug_report_templates/action_controller.rb", "lib/sinatra/base.rb"],
    "csharp": ["Src/Newtonsoft.Json/JsonConvert.cs", "src/AutoMapper/Mapper.cs"],
    "swift": ["Source/Core/Session.swift"],
    "rust": ["crates/core/main.rs", "src/lib.rs"],
}

EXCLUDE = {
    "c": ["tests/unit.c", "test/foo.c", "fuzz/f.c", "fuzzers/x.c", "deps/jemalloc/src/a.c",
          "third_party/zlib/z.c", "build/gen.c", "examples/demo.c", "docs/x.c", "src/parser_test.c",
          ".github/x.c", "source/tests/a.c"],
    "cpp": ["test/fmt_test.cc", "src/db_test.cc", "util/arena_unittest.cc", "benchmarks/b.cc",
            "cmake-build-debug/x.cc"],
    "java": ["src/test/java/com/acme/AppTest.java", "src/it/java/Smoke.java", "src/main/java/a/FooTest.java",
             "examples/src/main/java/Ex.java", "build/generated/X.java"],
    "kotlin": ["okhttp/src/jvmTest/kotlin/A.kt", "okhttp/src/androidHostTest/kotlin/B.kt",
               "src/main/kotlin/x/FooTest.kt"],
    "javascript": ["test/app.js", "__tests__/a.test.js", "src/a.spec.ts", "node_modules/x/index.js",
                   "dist/axios.min.js", "src/c.test.tsx", "examples/e.js"],
    "python": ["tests/test_api.py", "src/pkg/test_util.py", "src/pkg/util_test.py", "conftest.py",
               "docs/conf.py", ".venv/lib/x.py", "venv/lib/y.py", "build/lib/z.py"],
    "go": ["handler_test.go", "testdata/x.go", "vendor/github.com/x/y.go", "examples/main.go"],
    "php": ["tests/Unit/FooTest.php", "Tests/Descriptor/AbstractDescriptorTestCase.php",
            "src/FooTest.php", "vendor/symfony/x.php"],
    "ruby": ["spec/models/user_spec.rb", "test/models/user_test.rb", "lib/a_spec.rb", "features/step.rb"],
    "csharp": ["Src/Newtonsoft.Json.Tests/JsonConvertTests.cs", "src/AutoMapper.UnitTests/A.cs",
               "src/Foo/BarTests.cs", "bin/Debug/x.cs", "obj/Release/y.cs"],
    "swift": ["Tests/SessionTests.swift", "Source/FooTests.swift"],
    "rust": ["tests/integration.rs", "benches/bench.rs", "examples/demo.rs", "target/debug/build/x.rs"],
}


JVM_ONLY = {"java", "kotlin"}  # javasrc2cpg/kotlin2cpg full-match; the source-root guard relies on it


def _excluded(language, path):
    patterns = [re.compile(p) for p in defaults.exclusion_patterns_for(language)]
    full = any(p.fullmatch(path) for p in patterns)                 # JVM frontends
    if language not in JVM_ONLY:
        search = any(p.search(ROOT + path) for p in patterns)      # astgen frontends
        assert full == search, f"{language}: regimes disagree on {path}"
    return full


@pytest.mark.parametrize("language,path", [(l, p) for l, ps in KEEP.items() for p in ps])
def test_product_code_is_kept(language, path):
    assert not _excluded(language, path)


@pytest.mark.parametrize("language,path", [(l, p) for l, ps in EXCLUDE.items() for p in ps])
def test_tests_vendored_docs_and_build_output_are_excluded(language, path):
    assert _excluded(language, path)


def test_every_frontend_with_source_paths_has_rules():
    assert set(defaults.LANGUAGES_WITH_EXCLUSIONS) == set(defaults.SUPPORTED_LANGUAGES) - {"ghidra", "jimple"}
    assert all(defaults.exclusion_patterns_for(lang) for lang in defaults.LANGUAGES_WITH_EXCLUSIONS)


def test_operator_list_overrides_every_language_and_can_disable():
    cpg = Config().cpg
    assert _exclusion_patterns(cpg, "go") == defaults.exclusion_patterns_for("go")
    cpg.exclusion_patterns = ["(?:^|.*/)legacy/.*"]
    assert _exclusion_patterns(cpg, "go") == ["(?:^|.*/)legacy/.*"]
    cpg.languages_with_exclusions = []
    assert _exclusion_patterns(cpg, "go") == []
    assert _exclusion_patterns(Config().cpg, "ghidra") == []


def test_coverage_counts_excluded_source_files(tmp_path):
    for rel in ("src/util/a.c", "src/b.c", "tests/t.c", "deps/lua/l.c", "README.md", ".git/x.c"):
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text("x")
    rx = "|".join(f"({p})" for p in defaults.exclusion_patterns_for("c"))

    assert _count_excluded_files(str(tmp_path), rx, "c") == {
        "count": 2, "of": 4, "sample": ["deps/lua/l.c", "tests/t.c"]}
