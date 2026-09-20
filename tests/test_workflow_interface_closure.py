"""Closed renderer domains and the single offline interface projection boundary."""

from __future__ import annotations

import ast
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Annotated, Any

from pydantic import BaseModel

from mozaiksai.core.semantics.materialization import (
    _is_closed_annotation,
    _walk_model_closure,
    project_workflow_interface_render_input,
)
from mozaiksai.core.semantics.payloads import SemanticPayloadBase
from mozaiksai.core.semantics.refs import SemanticsModel
from mozaiksai.core.semantics.workflow_interface_materialization import (
    WorkflowInterfaceRenderInput,
    render_workflow_module_interface_unit,
)
from tests.test_workflow_interface_rematerialization import _state, _unit

ROOT = Path(__file__).resolve().parents[1]
RENDERER = "mozaiksai.core.semantics.workflow_interface_materialization"


def test_interface_input_recursive_closure_includes_discriminated_binding_models():
    violations = []
    visited = set()
    _walk_model_closure(WorkflowInterfaceRenderInput, "interface", violations, visited)
    assert violations == []
    assert {
        "RenderInputConsumesActionBinding",
        "RenderInputCommitsResultBinding",
        "RenderInputTriggeredByEventBinding",
        "PlanSource",
        "PlanEdgeSource",
        "PlanTaxonomySource",
    } <= {model.__name__ for model in visited}
    assert all(model.model_config.get("frozen") for model in visited)


def test_annotated_metadata_cannot_hide_any_or_an_open_nested_model():
    assert not _is_closed_annotation(Annotated[Any, "metadata"])
    assert not _is_closed_annotation(tuple[Annotated[dict[str, Any], "metadata"], ...])

    class OpenChild(BaseModel):
        value: dict[str, Any]

    class ClosedParent(SemanticsModel):
        children: tuple[Annotated[OpenChild, "metadata"], ...]

    ClosedParent.model_rebuild()
    violations = []
    visited = set()
    _walk_model_closure(ClosedParent, "parent", violations, visited)
    assert OpenChild in visited
    assert any("does not forbid unknown fields" in item for item in violations)
    assert any("open annotation" in item for item in violations)


def test_pure_interface_renderer_imports_no_ambient_state_or_semantic_authoring_models():
    source = ROOT / "mozaiksai/core/semantics/workflow_interface_materialization.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    for forbidden in (
        "os", "time", "datetime", "random", "uuid", "pathlib", "subprocess",
        "socket", "requests", "httpx", "factory_app",
        "mozaiksai.core.semantics.graph", "mozaiksai.core.semantics.payloads",
    ):
        assert not any(
            name == forbidden or name.startswith(forbidden + ".") for name in imported
        ), forbidden


def test_only_the_offline_materialization_owner_imports_the_interface_renderer():
    importers = set()
    for package in ("mozaiksai", "factory_app", "mozaiks_cli"):
        for path in (ROOT / package).rglob("*.py"):
            source = path.read_text(encoding="utf-8")
            if "workflow_interface_materialization" not in source:
                continue
            for node in ast.walk(ast.parse(source)):
                if isinstance(node, ast.Import):
                    imports_renderer = any(alias.name == RENDERER for alias in node.names)
                elif isinstance(node, ast.ImportFrom):
                    imports_renderer = node.module in {RENDERER, "workflow_interface_materialization"} or (
                        node.module in {"mozaiksai.core.semantics", None}
                        and any(alias.name == "workflow_interface_materialization" for alias in node.names)
                    )
                else:
                    continue
                if imports_renderer:
                    importers.add(path.relative_to(ROOT).as_posix())
    assert importers == {"mozaiksai/core/semantics/materialization.py"}


def test_projection_reads_only_exact_pinned_payload_keys_without_ambient_iteration():
    state = _state()
    unit = _unit(state)
    payloads = {payload.node_id: payload for payload in state["payloads"]}
    pinned = {source.node_id for source in unit.sources}
    reads = []

    class PinnedLookupOnly(Mapping[str, SemanticPayloadBase]):
        def __getitem__(self, key: str) -> SemanticPayloadBase:
            assert key in pinned, f"projection attempted an unpinned read: {key}"
            reads.append(key)
            return payloads[key]

        def __iter__(self) -> Iterator[str]:
            raise AssertionError("projection must not enumerate ambient payloads")

        def __len__(self) -> int:
            raise AssertionError("projection must not inspect ambient payload count")

    projected = project_workflow_interface_render_input(unit=unit, payload_by_node=PinnedLookupOnly())
    assert reads == [source.node_id for source in unit.sources]
    expected = project_workflow_interface_render_input(unit=unit, payload_by_node=payloads)
    assert projected == expected
    assert render_workflow_module_interface_unit(
        unit=unit, render_input=projected
    ) == render_workflow_module_interface_unit(unit=unit, render_input=expected)
