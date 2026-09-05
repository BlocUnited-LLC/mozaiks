"""Retirement guard: a future compiler owner must deliberately change this test."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
COMPILER_OWNER_ALLOWLIST: frozenset[str] = frozenset()
ACTIVE_TREES = ("factory_app", "mozaiksai", "mozaiks_cli", "scripts", "examples")
SOURCE_SUFFIXES = {".py", ".yaml", ".yml", ".json", ".md", ".txt", ".js", ".ts", ".jsx", ".tsx", ".ps1", ".sh", ".j2"}
RETIRED_REFERENCE = re.compile(r"module_interface|module[ ._-]+interface[ ._-]+v1", re.IGNORECASE)


def test_active_sources_have_zero_module_interface_authors() -> None:
    # Deliberately stricter than detecting writes: prompts, templates, filenames,
    # schema markers and serializers all count. Historical docs live outside
    # these active trees. No future producer is grandfathered in.
    assert COMPILER_OWNER_ALLOWLIST == frozenset()
    violations = []
    for tree in ACTIVE_TREES:
        for path in (ROOT / tree).rglob("*"):
            if not path.is_file() or path.suffix not in SOURCE_SUFFIXES:
                continue
            relative = path.relative_to(ROOT).as_posix()
            if relative in COMPILER_OWNER_ALLOWLIST:
                continue
            if RETIRED_REFERENCE.search(path.name):
                violations.append(relative)
            for line_number, line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1):
                if RETIRED_REFERENCE.search(line):
                    violations.append(f"{relative}:{line_number}")
    assert not violations, "Active module-interface authoring references: " + ", ".join(violations)


@pytest.mark.parametrize("reference", [
    "workflows/{workflow_name}/module_interface.yaml",
    "mozaiks.module_interface.v1",
    "generate_module_interface_files",
    "_MODULE_INTERFACE_TEMPLATE",
])
def test_hygiene_detects_each_retired_authoring_form(reference: str) -> None:
    assert RETIRED_REFERENCE.search(reference)
