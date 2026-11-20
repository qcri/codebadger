"""Runtime requirements must not float across a major version on rebuild."""

import pathlib
import re

DEV_TOOLS = {"pytest", "pytest-asyncio", "pytest-mock", "black", "isort", "flake8", "mypy", "autopep8"}


def _requirements(path="requirements.txt"):
    for line in pathlib.Path(path).read_text().splitlines():
        line = line.split("#", 1)[0].strip()
        if line:
            yield line


def _names(path):
    return {re.split(r"[\[<>=!~ ]", r, maxsplit=1)[0].lower() for r in _requirements(path)}


def test_every_runtime_requirement_is_bounded():
    unbounded = [r for r in _requirements() if "==" not in r and not re.search(r"<\s*\d", r)]
    assert unbounded == []


def test_fastmcp_stays_on_the_tested_minor():
    fastmcp = next(r for r in _requirements() if r.startswith("fastmcp"))
    assert "<4.3" in fastmcp


def test_directly_imported_packages_are_declared():
    """A package the code imports itself must not depend on another package's dependencies."""
    assert "starlette" in _names("requirements.txt")
    assert "jsonschema" in _names("requirements-test.txt")


def test_runtime_requirements_exclude_test_and_lint_tools():
    """requirements.txt is the package's runtime dependency list and the image's base layer."""
    assert _names("requirements.txt") & DEV_TOOLS == set()
    assert {"pytest", "pytest-asyncio", "pytest-mock"} <= _names("requirements-test.txt")
    assert {"black", "isort", "flake8", "mypy", "autopep8"} <= _names("requirements-dev.txt")


def test_test_and_dev_requirements_are_bounded():
    for path in ("requirements-test.txt", "requirements-dev.txt"):
        unbounded = [r for r in _requirements(path) if "==" not in r and not re.search(r"<\s*\d", r)]
        assert unbounded == [], path


def _lock():
    pins = {}
    for line in pathlib.Path("requirements.lock").read_text().splitlines():
        line = line.split("#", 1)[0].strip()
        if line:
            name, _, version = line.partition("==")
            assert version, f"lock entry is not an exact pin: {line}"
            pins[name.lower().replace("_", "-")] = version
    return pins


def test_lock_pins_every_declared_requirement_within_its_range():
    from packaging.requirements import Requirement

    pins = _lock()
    problems = []
    for path in ("requirements.txt", "requirements-test.txt"):
        for line in _requirements(path):
            req = Requirement(line)
            name = req.name.lower().replace("_", "-")
            if name not in pins:
                problems.append(f"{name} missing from requirements.lock")
            elif not req.specifier.contains(pins[name], prereleases=True):
                problems.append(f"{name}=={pins[name]} violates {req.specifier} ({path})")
    assert problems == []


def test_image_installs_under_the_lock():
    dockerfile = pathlib.Path("Dockerfile.mcp").read_text()
    assert "requirements.lock" in dockerfile
    assert dockerfile.count("-c requirements.lock") == 2  # runtime and test installs
