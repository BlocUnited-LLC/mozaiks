"""Retired factory ownership stays closed around one explicit compiler lane."""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
COMPILER_METADATA_REFERENCES = frozenset({
    "mozaiksai/core/runtime/app/layout_registry.py",
    "mozaiksai/core/semantics/compilation_plan.py",
    "mozaiksai/core/semantics/materialization.py",
})
COMPILER_BYTE_WRITER = "mozaiksai/core/semantics/workflow_interface_materialization.py"
ACTIVE_TREES = ("factory_app", "mozaiksai", "mozaiks_cli", "scripts", "examples")
SOURCE_SUFFIXES = {".py", ".yaml", ".yml", ".json", ".md", ".txt", ".js", ".ts", ".jsx", ".tsx", ".ps1", ".sh", ".j2"}
INTERFACE_REFERENCE = re.compile(r"module_interface|module[ ._-]+interface[ ._-]+v1", re.IGNORECASE)
RETIRED_REFERENCE = re.compile(
    r"mozaiks\.module_interface\.v1|module[ ._-]+interface[ ._-]+v1"
    r"|generate_module_interface_files|_MODULE_INTERFACE_TEMPLATE",
    re.IGNORECASE,
)


def test_active_sources_have_only_the_canonical_compiler_interface_lane() -> None:
    # Metadata/projection references are named individually; only the separate
    # compiler renderer owns bytes. Factory prompts, templates, filenames and
    # writers remain forbidden, with no directory exemptions. Retired v1 forms
    # remain forbidden even inside the explicitly allowed compiler files.
    allowed_references = COMPILER_METADATA_REFERENCES | {COMPILER_BYTE_WRITER}
    referenced_paths = set()
    schema_owners = set()
    violations = []
    for tree in ACTIVE_TREES:
        for path in (ROOT / tree).rglob("*"):
            if not path.is_file() or path.suffix not in SOURCE_SUFFIXES:
                continue
            relative = path.relative_to(ROOT).as_posix()
            if INTERFACE_REFERENCE.search(path.name):
                referenced_paths.add(relative)
            if RETIRED_REFERENCE.search(path.name):
                violations.append(relative)
            for line_number, line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1):
                if INTERFACE_REFERENCE.search(line):
                    referenced_paths.add(relative)
                if "mozaiks.module_interface.v2" in line:
                    schema_owners.add(relative)
                if RETIRED_REFERENCE.search(line):
                    violations.append(f"{relative}:{line_number}")
    assert not violations, "Retired module-interface authoring references: " + ", ".join(violations)
    assert referenced_paths == allowed_references
    assert schema_owners == {COMPILER_BYTE_WRITER}


def test_compiler_interface_has_exactly_one_renderer_implementation() -> None:
    from mozaiksai.core.semantics import materialization, workflow_interface_materialization

    renderer = workflow_interface_materialization.render_workflow_module_interface_unit
    assert materialization.render_workflow_module_interface_unit is renderer
    definitions = []
    for relative in COMPILER_METADATA_REFERENCES | {COMPILER_BYTE_WRITER}:
        tree = ast.parse((ROOT / relative).read_text(encoding="utf-8-sig"))
        for node in ast.walk(tree):
            if (
                isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                and node.name.startswith("render_")
                and "module_interface" in node.name
            ):
                definitions.append((relative, node.name))
    assert definitions == [(COMPILER_BYTE_WRITER, "render_workflow_module_interface_unit")]


@pytest.mark.parametrize("reference", [
    "workflows/{workflow_name}/module_interface.yaml",
    "mozaiks.module_interface.v1",
    "generate_module_interface_files",
    "_MODULE_INTERFACE_TEMPLATE",
])
def test_hygiene_detects_each_retired_authoring_form(reference: str) -> None:
    assert INTERFACE_REFERENCE.search(reference)


@pytest.mark.parametrize("reference", [
    "mozaiks.module_interface.v1",
    "module interface v1",
    "generate_module_interface_files",
    "_MODULE_INTERFACE_TEMPLATE",
])
def test_retired_authority_is_forbidden_even_in_compiler_files(reference: str) -> None:
    assert RETIRED_REFERENCE.search(reference)
    assert not RETIRED_REFERENCE.search("mozaiks.module_interface.v2")
