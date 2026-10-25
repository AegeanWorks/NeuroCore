"""Packaging contract: pyproject.toml is the source of truth for every requirements file."""

import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RUNTIME_EXTRAS = ("browser", "documents", "messaging")


def _name(requirement: str) -> str:
    return re.split(r"[<>=!~\[; ]", requirement.strip(), maxsplit=1)[0].lower()


def _requirement_names(path: Path) -> set:
    names = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if line and not line.startswith("-"):
            names.add(_name(line))
    return names


def _project():
    return tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]


def test_requirements_txt_is_runtime_plus_optional_capabilities():
    project = _project()
    expected = {_name(dep) for dep in project["dependencies"]}
    for extra in RUNTIME_EXTRAS:
        expected |= {_name(dep) for dep in project["optional-dependencies"][extra]}

    assert _requirement_names(ROOT / "requirements.txt") == expected


def test_requirements_dev_adds_exactly_the_dev_group():
    dev = {_name(dep) for dep in _project()["optional-dependencies"]["dev"]}
    text = (ROOT / "requirements-dev.txt").read_text(encoding="utf-8")

    assert "-r requirements.txt" in text
    assert _requirement_names(ROOT / "requirements-dev.txt") == dev


def test_built_in_route_dependencies_are_core_dependencies():
    core = {_name(dep) for dep in _project()["dependencies"]}

    assert {"scipy"} <= core
