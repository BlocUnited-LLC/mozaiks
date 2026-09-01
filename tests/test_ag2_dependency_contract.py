"""Keep Mozaiks' exact AG2 dependency declarations synchronized."""

from __future__ import annotations

import importlib.metadata
import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
AG2_REQUIREMENT = re.compile(r"^ag2(?:\[[^]]+\])?==(?P<version>[^;\s]+)$", re.IGNORECASE)


def _declared_ag2_versions() -> list[str]:
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    project = pyproject["project"]
    requirements = list(project["dependencies"])
    for extra_requirements in project["optional-dependencies"].values():
        requirements.extend(extra_requirements)

    ag2_requirements = [item for item in requirements if item.lower().startswith("ag2")]
    matches = [AG2_REQUIREMENT.fullmatch(item) for item in ag2_requirements]
    assert all(matches), f"every AG2 declaration must use an exact pin: {ag2_requirements}"
    return [match.group("version") for match in matches if match is not None]


def _requirements_txt_ag2_version() -> str:
    declarations = [
        line.strip()
        for line in (ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines()
        if line.strip().lower().startswith("ag2")
    ]
    assert len(declarations) == 1, f"expected one requirements.txt AG2 pin: {declarations}"
    match = AG2_REQUIREMENT.fullmatch(declarations[0])
    assert match is not None, "requirements.txt AG2 declaration must use an exact pin"
    return match.group("version")


def test_ag2_dependency_declarations_and_installed_runtime_match() -> None:
    versions = set(_declared_ag2_versions())
    assert len(versions) == 1, f"mixed AG2 versions declared: {sorted(versions)}"

    (declared_version,) = versions
    assert _requirements_txt_ag2_version() == declared_version
    assert importlib.metadata.version("ag2") == declared_version
