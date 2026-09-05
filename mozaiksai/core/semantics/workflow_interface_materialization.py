"""Deterministic workflow-interface family rendering.

One accepted renderer authority — ``deterministic_workflow_interface_renderer@1``
— produces canonical bytes for the two workflow-interface families whose
complete input authority exists on accepted semantic payloads plus pinned
node-level canonical event identities:

- ``workflow_module_interface`` -> ``module_interface.yaml`` (one per workflow)
- ``app_workflow_registry``     -> ``workflows/workflow_registry.json``

``module_interface.yaml`` is an exact projection of one workflow's
module-capability closure (workflow capabilities, EVERY workflow result each
capability canonically owns — committed or advisory — their typed bindings to
declared module actions, and canonical trigger events). It is a validation
and diff surface for authored workflow bundles —
never runtime input and never independent authority. The registry lists every
workflow's capability surface and event triggers so a future platform
consumer can stop re-parsing orchestrator files; it deliberately carries no
journey, sequencing, entrypoint, or AI-launch facts (those semantics do not
exist yet) and is NOT the factory ``extension_registry.json``.

Action identity is the semantic node id: the module and the action are graph
nodes and ``ModuleActionRef`` pins their node ids rather than inventing a
parallel id scheme, so the projection renders ``module_id`` (a typed module
fact) plus ``action_node_id`` — lossless under the sole-declarer closure.
Canonical event identity lives on the EVENT node's taxonomy reference, so it
reaches the renderer only through the unit's pinned ``taxonomy_sources``.

Each family consumes its own closed, frozen, family-local render input
projected by the central offline materialization owner from exactly that
unit's plan-pinned sources. Dependency direction stays one-way: this module
imports no semantic payload classes, no graph model, and no binding
machinery — only the canonical layout-row contract (the same
``RegistryFamilyRow`` snapshot machinery the planner uses) so the renderer
can bind every supplied unit to the exact canonical row identity it claims.
The renderer is offline substrate: no filesystem, no clocks, no
environment, no AG2, no AppBuildPlan, no production callers.
"""

from __future__ import annotations

from collections.abc import Mapping
from functools import lru_cache
from typing import Literal

from pydantic import BaseModel, ConfigDict, model_validator

from mozaiksai.core.runtime.app.layout_registry import build_app_layout_registry
from mozaiksai.core.semantics.compilation_plan import (
    FamilyInstancePlan,
    PlanDisposition,
    RegistryFamilyRow,
    canonical_instance_identity_value,
    canonical_instance_unit_id,
    canonical_single_unit_id,
    snapshot_layout_registry,
)
from mozaiksai.core.semantics.decl_bytes import json_decl_bytes, yaml_decl_bytes

WORKFLOW_INTERFACE_RENDERER_IMPLEMENTATION_ID = "deterministic_workflow_interface_renderer"
WORKFLOW_INTERFACE_RENDERER_IMPLEMENTATION_VERSION = "1"

WORKFLOW_INTERFACE_RENDER_INPUT_VERSION: Literal[
    "mozaiks.workflow_interface_render_input.v1"
] = "mozaiks.workflow_interface_render_input.v1"

MODULE_INTERFACE_SCHEMA_VERSION = "mozaiks.module_interface.v2"
APP_WORKFLOW_REGISTRY_SCHEMA_VERSION = "mozaiks.app_workflow_registry.v1"

#: The closed family set this renderer implementation may produce.
WORKFLOW_INTERFACE_FAMILIES: frozenset[str] = frozenset(
    {
        "workflow_module_interface",
        "app_workflow_registry",
    }
)

@lru_cache(maxsize=1)
def _canonical_rows_by_digest() -> Mapping[str, RegistryFamilyRow]:
    """The exact canonical layout rows this renderer version owns.

    Resolution direction is the invariant: a supplied unit's
    ``family_identity_digest`` names — or fails to name — one of these
    content-addressed canonical rows, and every other unit fact (family
    kind, unit id, placeholder set, output scope, output path) must then
    equal what THAT row canonically derives. The renderer never selects a
    profile from caller-supplied ``path_scope`` or ``path``.

    Pure construction from the one canonical core registry definition — the
    same ``RegistryFamilyRow`` machinery the planner snapshots — so the
    compiler and renderer cannot carry independently authored layout facts
    that drift. No filesystem, environment, runtime app layout, or dynamic
    row discovery is consulted; a mutated or extended row simply has a
    digest this renderer does not know and is rejected.
    """
    snapshot = snapshot_layout_registry(build_app_layout_registry(()))
    return {
        row.row_digest: row
        for row in snapshot.rows
        if row.kind in WORKFLOW_INTERFACE_FAMILIES
    }


def _canonical_unit_identity(
    unit: FamilyInstancePlan, row: RegistryFamilyRow
) -> tuple[str, str, str | None]:
    """Derive (expected_unit_id, expected_path, workflow_id) from the row.

    Uses the planner's own derivation rules — the shared unit-id helpers and
    the row's own path-template expansion — never a second path convention.
    Placeholder exactness is the instance-identity contract: the interface
    rows require EXACTLY ``(("workflow_id", <id>),)`` (the workflow-relative
    twin carries the same scope-implied identity even though its template
    has no placeholder), and the registry row requires EXACTLY ``()``.
    Surplus, missing, or substituted identity axes reject.
    """
    if row.kind == "workflow_module_interface":
        values = unit.placeholder_values
        if len(values) != 1 or values[0][0] != "workflow_id":
            raise WorkflowInterfaceMaterializationError(
                f"unit {unit.unit_id!r} must carry exactly the canonical "
                f"workflow instance identity; got {values!r}"
            )
        try:
            workflow_id = canonical_instance_identity_value(values[0][1])
        except ValueError as exc:
            raise WorkflowInterfaceMaterializationError(
                f"unit {unit.unit_id!r} workflow instance identity is not "
                f"canonical: {exc}"
            ) from exc
        expected_unit_id = canonical_instance_unit_id(
            row.kind, workflow_id, row.row_digest
        )
        expected_path = row.path_template.replace("{workflow_id}", workflow_id)
    else:
        if unit.placeholder_values != ():
            raise WorkflowInterfaceMaterializationError(
                f"unit {unit.unit_id!r} must carry no instance identity; "
                f"got {unit.placeholder_values!r}"
            )
        workflow_id = None
        expected_unit_id = canonical_single_unit_id(row.kind, row.row_digest)
        expected_path = row.path_template
    if "{" in expected_path or "}" in expected_path:
        raise WorkflowInterfaceMaterializationError(
            f"canonical row {row.kind!r} template did not expand completely"
        )
    return expected_unit_id, expected_path, workflow_id

_BINDING_ROLES: frozenset[str] = frozenset(
    {
        "consumes_action",
        "commits_result_through_action",
        "triggered_by_event",
    }
)


class WorkflowInterfaceMaterializationError(ValueError):
    """The inputs violate the workflow-interface family contract."""


class _ClosedRenderInputModel(BaseModel):
    """Frozen, unknown-field-rejecting base for every render-input component."""

    model_config = ConfigDict(frozen=True, extra="forbid")


class RenderInputSource(_ClosedRenderInputModel):
    """One pinned semantic source: exact node identity and payload digest."""

    node_id: str
    payload_digest: str


class RenderInputTaxonomySource(_ClosedRenderInputModel):
    """One pinned node-level canonical taxonomy identity."""

    node_id: str
    category: str
    identifier: str


class RenderInputCapabilityBinding(_ClosedRenderInputModel):
    """The projection facts of one typed capability binding."""

    role: str
    module_id: str | None = None
    action_node_id: str | None = None
    event_type: str | None = None
    workflow_result_id: str | None = None

    @model_validator(mode="after")
    def _role_shape(self) -> RenderInputCapabilityBinding:
        if self.role not in _BINDING_ROLES:
            raise ValueError(f"unknown binding role {self.role!r}")
        action_role = self.role in {"consumes_action", "commits_result_through_action"}
        if action_role != (self.module_id is not None and self.action_node_id is not None):
            raise ValueError(
                f"binding role {self.role!r} requires exactly its action identity"
            )
        if (self.role == "triggered_by_event") != (self.event_type is not None):
            raise ValueError(
                f"binding role {self.role!r} requires exactly its event identity"
            )
        if (self.role == "commits_result_through_action") != (
            self.workflow_result_id is not None
        ):
            raise ValueError(
                f"binding role {self.role!r} requires exactly its result identity"
            )
        return self

    @property
    def sort_key(self) -> tuple[str, str, str, str, str]:
        return (
            self.role,
            self.module_id or "",
            self.action_node_id or "",
            self.event_type or "",
            self.workflow_result_id or "",
        )


class RenderInputCapabilityResult(_ClosedRenderInputModel):
    """One capability-owned workflow result: semantic identity only.

    A result belongs to the interface because its capability declares it —
    whether or not any commit binding delivers it. Advisory results are valid
    application semantics; no provider, model, structured-output class,
    prompt, runtime, route, or handler identity ever enters this shape.
    """

    result_id: str
    description: str | None


class RenderInputCapability(_ClosedRenderInputModel):
    """One workflow capability with its complete result and binding projection."""

    capability_id: str
    description: str | None
    results: tuple[RenderInputCapabilityResult, ...] = ()
    bindings: tuple[RenderInputCapabilityBinding, ...]

    @model_validator(mode="after")
    def _canonical_members(self) -> RenderInputCapability:
        results = tuple(sorted(self.results, key=lambda r: r.result_id))
        result_ids = [r.result_id for r in results]
        if len(result_ids) != len(set(result_ids)):
            raise ValueError(
                f"capability {self.capability_id!r} declares duplicate result ids"
            )
        object.__setattr__(self, "results", results)
        ordered = tuple(sorted(self.bindings, key=lambda b: b.sort_key))
        keys = [b.sort_key for b in ordered]
        if len(keys) != len(set(keys)):
            raise ValueError(
                f"capability {self.capability_id!r} declares duplicate bindings"
            )
        object.__setattr__(self, "bindings", ordered)
        return self


class RenderInputRegistryCapability(_ClosedRenderInputModel):
    """The registry projection of one capability: identity and triggers."""

    capability_id: str
    event_triggers: tuple[str, ...]

    @model_validator(mode="after")
    def _canonical_triggers(self) -> RenderInputRegistryCapability:
        ordered = tuple(sorted(set(self.event_triggers)))
        object.__setattr__(self, "event_triggers", ordered)
        return self


class RenderInputRegistryWorkflow(_ClosedRenderInputModel):
    """The registry projection of one workflow."""

    workflow_id: str
    startup_mode: str | None
    capabilities: tuple[RenderInputRegistryCapability, ...]

    @model_validator(mode="after")
    def _canonical_capabilities(self) -> RenderInputRegistryWorkflow:
        ordered = tuple(sorted(self.capabilities, key=lambda c: c.capability_id))
        capability_ids = [c.capability_id for c in ordered]
        if len(capability_ids) != len(set(capability_ids)):
            raise ValueError(
                f"workflow {self.workflow_id!r} declares duplicate capability ids"
            )
        object.__setattr__(self, "capabilities", ordered)
        return self


class _FamilyRenderInputBase(_ClosedRenderInputModel):
    """Shared identity of every family-local render input.

    Each input carries only the facts its one family consumes plus the exact
    pinned source payload digests and node-level taxonomy identities that
    produced them, normalized to one canonical order.
    """

    render_input_schema_version: Literal["mozaiks.workflow_interface_render_input.v1"]
    sources: tuple[RenderInputSource, ...]
    taxonomy_sources: tuple[RenderInputTaxonomySource, ...] = ()

    @model_validator(mode="after")
    def _canonical_sources(self) -> _FamilyRenderInputBase:
        sources = tuple(sorted(self.sources, key=lambda s: s.node_id))
        source_ids = [s.node_id for s in sources]
        if len(set(source_ids)) != len(source_ids):
            raise ValueError("render input declares duplicate source node ids")
        if not sources:
            raise ValueError("render input pins no semantic sources")
        object.__setattr__(self, "sources", sources)
        taxonomy = tuple(
            sorted(
                self.taxonomy_sources,
                key=lambda t: (t.node_id, t.category, t.identifier),
            )
        )
        taxonomy_keys = [(t.node_id, t.category) for t in taxonomy]
        if len(taxonomy_keys) != len(set(taxonomy_keys)):
            raise ValueError("render input declares duplicate taxonomy identities")
        object.__setattr__(self, "taxonomy_sources", taxonomy)
        return self


class ModuleInterfaceRenderInput(_FamilyRenderInputBase):
    """Facts consumed by one workflow's ``module_interface.yaml`` only."""

    family: Literal["workflow_module_interface"] = "workflow_module_interface"
    workflow_id: str
    capabilities: tuple[RenderInputCapability, ...]

    @model_validator(mode="after")
    def _canonical_capabilities(self) -> ModuleInterfaceRenderInput:
        ordered = tuple(sorted(self.capabilities, key=lambda c: c.capability_id))
        capability_ids = [c.capability_id for c in ordered]
        if len(capability_ids) != len(set(capability_ids)):
            raise ValueError("render input declares duplicate capability ids")
        object.__setattr__(self, "capabilities", ordered)
        return self


class WorkflowRegistryRenderInput(_FamilyRenderInputBase):
    """Facts consumed by ``workflows/workflow_registry.json`` only."""

    family: Literal["app_workflow_registry"] = "app_workflow_registry"
    workflows: tuple[RenderInputRegistryWorkflow, ...]

    @model_validator(mode="after")
    def _canonical_workflows(self) -> WorkflowRegistryRenderInput:
        ordered = tuple(sorted(self.workflows, key=lambda w: w.workflow_id))
        workflow_ids = [w.workflow_id for w in ordered]
        if len(workflow_ids) != len(set(workflow_ids)):
            raise ValueError("render input declares duplicate workflow ids")
        object.__setattr__(self, "workflows", ordered)
        return self


WorkflowInterfaceRenderInput = ModuleInterfaceRenderInput | WorkflowRegistryRenderInput


def _verify_unit_binding(
    unit: FamilyInstancePlan, render_input: WorkflowInterfaceRenderInput
) -> None:
    """The render input must bind exactly the unit's pinned identity sets.

    After canonical normalization, both the ``(node_id, payload_digest)``
    source tuples and the ``(node_id, category, identifier)`` taxonomy
    tuples must equal the plan unit's pinned sets exactly — a missing,
    extra, duplicate, stale, or substituted identity fails closed.
    """
    expected_sources = tuple(
        sorted((s.node_id, s.payload_digest) for s in unit.sources)
    )
    if len({node_id for node_id, _ in expected_sources}) != len(expected_sources):
        raise WorkflowInterfaceMaterializationError(
            f"unit {unit.unit_id!r} pins duplicate source node ids"
        )
    actual_sources = tuple((s.node_id, s.payload_digest) for s in render_input.sources)
    if actual_sources != expected_sources:
        raise WorkflowInterfaceMaterializationError(
            f"unit {unit.unit_id!r} render input does not bind exactly the "
            "unit's pinned source set: "
            f"expected {[n for n, _ in expected_sources]!r}, "
            f"got {[n for n, _ in actual_sources]!r}"
        )
    expected_taxonomy = tuple(
        sorted((t.node_id, t.category, t.identifier) for t in unit.taxonomy_sources)
    )
    actual_taxonomy = tuple(
        (t.node_id, t.category, t.identifier) for t in render_input.taxonomy_sources
    )
    if actual_taxonomy != expected_taxonomy:
        raise WorkflowInterfaceMaterializationError(
            f"unit {unit.unit_id!r} render input does not bind exactly the "
            "unit's pinned taxonomy identities: "
            f"expected {expected_taxonomy!r}, got {actual_taxonomy!r}"
        )


def _binding_document(binding: RenderInputCapabilityBinding) -> dict[str, object]:
    document: dict[str, object] = {"role": binding.role}
    if binding.module_id is not None:
        document["module_id"] = binding.module_id
    if binding.action_node_id is not None:
        document["action_node_id"] = binding.action_node_id
    if binding.event_type is not None:
        document["event_type"] = binding.event_type
    if binding.workflow_result_id is not None:
        document["workflow_result_id"] = binding.workflow_result_id
    return document


def _module_interface_document(
    render_input: ModuleInterfaceRenderInput,
) -> dict[str, object]:
    return {
        "schema_version": MODULE_INTERFACE_SCHEMA_VERSION,
        "workflow_id": render_input.workflow_id,
        "capabilities": [
            {
                "capability_id": capability.capability_id,
                **(
                    {"description": capability.description}
                    if capability.description is not None
                    else {}
                ),
                "results": [
                    {
                        "result_id": result.result_id,
                        **(
                            {"description": result.description}
                            if result.description is not None
                            else {}
                        ),
                    }
                    for result in capability.results
                ],
                "bindings": [
                    _binding_document(binding) for binding in capability.bindings
                ],
            }
            for capability in render_input.capabilities
        ],
    }


def _workflow_registry_document(
    render_input: WorkflowRegistryRenderInput,
) -> dict[str, object]:
    return {
        "schema_version": APP_WORKFLOW_REGISTRY_SCHEMA_VERSION,
        "workflows": [
            {
                "workflow_id": workflow.workflow_id,
                "startup_mode": workflow.startup_mode,
                "capabilities": [
                    {
                        "capability_id": capability.capability_id,
                        "event_triggers": list(capability.event_triggers),
                    }
                    for capability in workflow.capabilities
                ],
            }
            for workflow in render_input.workflows
        ],
    }


def render_workflow_interface_unit(
    unit: FamilyInstancePlan,
    render_input: WorkflowInterfaceRenderInput,
) -> bytes:
    """Render one workflow-interface family unit to canonical bytes.

    The unit must be an active RENDER unit of this renderer's closed family
    set, its output template must be one this implementation declares, and
    the render input must bind exactly the unit's pinned source and taxonomy
    identity sets.
    """
    if (
        unit.disposition is not PlanDisposition.RENDER
        or unit.family_kind not in WORKFLOW_INTERFACE_FAMILIES
    ):
        raise WorkflowInterfaceMaterializationError(
            f"unit {unit.unit_id!r} ({unit.family_kind!r}) is not an active "
            "workflow-interface render unit"
        )
    if unit.family_kind != render_input.family:
        raise WorkflowInterfaceMaterializationError(
            f"unit {unit.unit_id!r} family does not match render input "
            f"family {render_input.family!r}"
        )
    if len(unit.outputs) != 1:
        raise WorkflowInterfaceMaterializationError(
            f"unit {unit.unit_id!r} must own exactly one output path"
        )
    target = unit.outputs[0]
    # Complete canonical layout-row/instance identity binding. Direction is
    # the invariant: family_identity_digest resolves the exact canonical row
    # this renderer owns, that row derives the exact instance placeholder
    # set, unit id, path scope, and expanded path, and every supplied unit
    # fact must equal that canonical result — the renderer never chooses a
    # profile from caller-supplied scope or path. This is the direct-renderer
    # responsibility ("is this unit exactly one canonical unit shape this
    # renderer owns?"); whether that canonical unit belongs to the current
    # CompilationPlan is #475/#477 plan authority, deliberately not
    # re-implemented here.
    row = _canonical_rows_by_digest().get(unit.family_identity_digest)
    if row is None:
        raise WorkflowInterfaceMaterializationError(
            f"unit {unit.unit_id!r} family identity digest does not name a "
            "canonical layout row this renderer owns"
        )
    if unit.family_kind != row.kind:
        raise WorkflowInterfaceMaterializationError(
            f"unit {unit.unit_id!r} family {unit.family_kind!r} does not match "
            f"its canonical layout row {row.kind!r}"
        )
    expected_unit_id, expected_path, unit_workflow_id = _canonical_unit_identity(
        unit, row
    )
    if unit.unit_id != expected_unit_id:
        raise WorkflowInterfaceMaterializationError(
            f"unit id {unit.unit_id!r} does not equal the exact canonical unit "
            f"identity {expected_unit_id!r} of its layout row"
        )
    if isinstance(render_input, ModuleInterfaceRenderInput):
        if render_input.workflow_id != unit_workflow_id:
            raise WorkflowInterfaceMaterializationError(
                f"unit {unit.unit_id!r} render input names workflow "
                f"{render_input.workflow_id!r} but the unit's exact workflow "
                f"identity is {unit_workflow_id!r}"
            )
    if target.path_scope != row.path_scope or target.path != expected_path:
        raise WorkflowInterfaceMaterializationError(
            f"unit {unit.unit_id!r} output ({target.path_scope!r}, "
            f"{target.path!r}) does not equal the exact canonical path "
            f"({row.path_scope!r}, {expected_path!r}) of its layout row"
        )
    _verify_unit_binding(unit, render_input)
    if isinstance(render_input, ModuleInterfaceRenderInput):
        return yaml_decl_bytes(_module_interface_document(render_input))
    return json_decl_bytes(_workflow_registry_document(render_input))


__all__ = [
    "APP_WORKFLOW_REGISTRY_SCHEMA_VERSION",
    "MODULE_INTERFACE_SCHEMA_VERSION",
    "ModuleInterfaceRenderInput",
    "RenderInputCapability",
    "RenderInputCapabilityBinding",
    "RenderInputCapabilityResult",
    "RenderInputRegistryCapability",
    "RenderInputRegistryWorkflow",
    "RenderInputSource",
    "RenderInputTaxonomySource",
    "WORKFLOW_INTERFACE_FAMILIES",
    "WORKFLOW_INTERFACE_RENDERER_IMPLEMENTATION_ID",
    "WORKFLOW_INTERFACE_RENDERER_IMPLEMENTATION_VERSION",
    "WORKFLOW_INTERFACE_RENDER_INPUT_VERSION",
    "WorkflowInterfaceMaterializationError",
    "WorkflowInterfaceRenderInput",
    "WorkflowRegistryRenderInput",
    "render_workflow_interface_unit",
]
