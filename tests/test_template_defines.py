"""Tests for feature macros harvested from config-header templates."""

import pathlib

from src.tools import core_tools


def _write(root: pathlib.Path, relative_path: str, text: str) -> None:
    path = root / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def test_harvests_tested_feature_macros_and_skips_platform(tmp_path: pathlib.Path) -> None:
    _write(tmp_path, "lib/foo_config.h.cmake",
           "#cmakedefine FOO_DTD\n#cmakedefine01 FOO_NS\n#cmakedefine _WIN32_WINNT\n"
           "#define FOO_CTX @FOO_CTX@\n#cmakedefine UNUSED_FEATURE\n#undef HAVE_ZLIB\n")
    _write(tmp_path, "lib/parse.c",
           "#ifdef FOO_DTD\n#endif\n#if FOO_NS && defined(HAVE_ZLIB)\n#endif\n"
           "#if FOO_CTX > 0\n#endif\n#ifdef _WIN32_WINNT\n#endif\n")

    harvested = core_tools._harvest_template_defines(str(tmp_path))

    assert harvested == ["FOO_CTX", "FOO_DTD", "FOO_NS", "HAVE_ZLIB"]


def test_skips_template_when_generated_header_exists(tmp_path: pathlib.Path) -> None:
    _write(tmp_path, "config.h.in", "#undef HAVE_FOO\n")
    _write(tmp_path, "config.h", "#define HAVE_FOO 1\n")
    _write(tmp_path, "a.c", "#ifdef HAVE_FOO\n#endif\n")

    assert core_tools._harvest_template_defines(str(tmp_path)) == []


def test_returns_nothing_without_templates(tmp_path: pathlib.Path) -> None:
    _write(tmp_path, "a.c", "#ifdef HAVE_FOO\n#endif\n")

    assert core_tools._harvest_template_defines(str(tmp_path)) == []
