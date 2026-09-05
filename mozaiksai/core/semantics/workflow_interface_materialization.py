"""Pure deterministic rendering of compiler-owned workflow module interfaces.

Closed inputs are projected from a canonical plan unit by the materialization
owner. This renderer adds only the ``mozaiks.module_interface.v2`` serialization
shape. It reads no semantic graph, payload objects, filesystem, or runtime
state. Row identity establishes the exact output shape; aggregate plan
membership remains the canonical plan authority's responsibility.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field, field_validator, model_validator

from mozaiksai.core.runtime.app.layout_registry import build_app_layout_registry
from mozaiksai.core.semantics.compilation_plan import (
    FamilyInstancePlan,
    PlanDisposition,
    PlanEdgeSource,
    PlanSource,
    PlanSourceScope,
    PlanTaxonomySource,
    canonical_instance_identity_value,
    canonical_instance_unit_id,
    snapshot_layout_registry,
)
from mozaiksai.core.semantics.decl_bytes import yaml_decl_bytes
from mozaiksai.core.semantics.refs import SemanticsModel, validate_node_id_grammar
from mozaiksai.core.taxonomy import SemanticCategory, validate_identifier_grammar

WORKFLOW_MODULE_INTERFACE = "workflow_module_interface"
WORKFLOW_INTERFACE_RENDERER_IMPLEMENTATION_ID = "deterministic_workflow_interface_renderer"
WORKFLOW_INTERFACE_RENDERER_IMPLEMENTATION_VERSION = "1"
MODULE_INTERFACE_SCHEMA_VERSION: Literal["mozaiks.module_interface.v2"] = (
    "mozaiks.module_interface.v2"
)


class WorkflowInterfaceMaterializationError(ValueError):
    """The unit or input violates the canonical interface renderer contract."""


class _ActionBinding(SemanticsModel):
    module_id: str
    action_node_id: str

    @field_validator("module_id")
    @classmethod
    def _module_id(cls, value: str) -> str:
        return canonical_instance_identity_value(value)

    @field_validator("action_node_id")
    @classmethod
    def _action_node_id(cls, value: str) -> str:
        return validate_node_id_grammar(value)


class RenderInputConsumesActionBinding(_ActionBinding):
    role: Literal["consumes_action"] = "consumes_action"


class RenderInputCommitsResultBinding(_ActionBinding):
    role: Literal["commits_result_through_action"] = "commits_result_through_action"
    workflow_result_id: str = Field(pattern=r"^[a-z][a-z0-9_]*$")


class RenderInputTriggeredByEventBinding(SemanticsModel):
    role: Literal["triggered_by_event"] = "triggered_by_event"
    event_type: str

    @field_validator("event_type")
    @classmethod
    def _event_type(cls, value: str) -> str:
        return validate_identifier_grammar(SemanticCategory.EVENT, value)


RenderInputWorkflowBinding = Annotated[
    RenderInputConsumesActionBinding
    | RenderInputCommitsResultBinding
    | RenderInputTriggeredByEventBinding,
    Field(discriminator="role"),
]


class RenderInputWorkflowResult(SemanticsModel):
    result_id: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    description: str | None = None


class RenderInputWorkflowCapability(SemanticsModel):
    capability_id: str
    description: str | None = None
    results: tuple[RenderInputWorkflowResult, ...] = ()
    bindings: tuple[RenderInputWorkflowBinding, ...] = ()

    @field_validator("capability_id")
    @classmethod
    def _capability_id(cls, value: str) -> str:
        return validate_identifier_grammar(SemanticCategory.CAPABILITY, value)

    @field_validator("results")
    @classmethod
    def _results(
        cls, value: tuple[RenderInputWorkflowResult, ...]
    ) -> tuple[RenderInputWorkflowResult, ...]:
        ordered = tuple(sorted(value, key=lambda item: item.result_id))
        if len({item.result_id for item in ordered}) != len(ordered):
            raise ValueError("duplicate result identity in one capability")
        return ordered

    @field_validator("bindings")
    @classmethod
    def _bindings(
        cls, value: tuple[RenderInputWorkflowBinding, ...]
    ) -> tuple[RenderInputWorkflowBinding, ...]:
        ordered = tuple(sorted(value, key=_binding_identity))
        if len({_binding_identity(item) for item in ordered}) != len(ordered):
            raise ValueError("duplicate capability binding identity")
        return ordered

    @model_validator(mode="after")
    def _committed_results_owned(self) -> RenderInputWorkflowCapability:
        result_ids = {item.result_id for item in self.results}
        for binding in self.bindings:
            if (
                isinstance(binding, RenderInputCommitsResultBinding)
                and binding.workflow_result_id not in result_ids
            ):
                raise ValueError("commit binding names a result outside its capability")
        return self


def _binding_identity(binding: RenderInputWorkflowBinding) -> tuple[str, ...]:
    if isinstance(binding, RenderInputTriggeredByEventBinding):
        return (binding.role, binding.event_type)
    return (
        binding.role,
        binding.module_id,
        binding.action_node_id,
        binding.workflow_result_id
        if isinstance(binding, RenderInputCommitsResultBinding)
        else "",
    )


class WorkflowInterfaceRenderInput(SemanticsModel):
    """Only interface facts and their exact three plan-pinned source sets."""

    workflow_id: str
    sources: tuple[PlanSource, ...] = Field(min_length=1)
    edge_sources: tuple[PlanEdgeSource, ...] = ()
    taxonomy_sources: tuple[PlanTaxonomySource, ...] = ()
    capabilities: tuple[RenderInputWorkflowCapability, ...] = ()

    @field_validator("workflow_id")
    @classmethod
    def _workflow_id(cls, value: str) -> str:
        return canonical_instance_identity_value(value)

    @field_validator("sources")
    @classmethod
    def _sources(cls, value: tuple[PlanSource, ...]) -> tuple[PlanSource, ...]:
        ordered = tuple(sorted(value, key=lambda item: item.node_id))
        if len({item.node_id for item in ordered}) != len(ordered):
            raise ValueError("duplicate payload source identity")
        return ordered

    @field_validator("edge_sources")
    @classmethod
    def _edges(cls, value: tuple[PlanEdgeSource, ...]) -> tuple[PlanEdgeSource, ...]:
        ordered = tuple(sorted(value, key=lambda item: item.edge_identity))
        if len({item.edge_identity for item in ordered}) != len(ordered):
            raise ValueError("duplicate edge source identity")
        return ordered

    @field_validator("taxonomy_sources")
    @classmethod
    def _taxonomy(
        cls, value: tuple[PlanTaxonomySource, ...]
    ) -> tuple[PlanTaxonomySource, ...]:
        ordered = tuple(sorted(value, key=lambda item: (item.node_id, item.identifier)))
        if any(item.category != SemanticCategory.EVENT.value for item in ordered):
            raise ValueError("interface taxonomy sources must identify events only")
        if len({item.node_id for item in ordered}) != len(ordered):
            raise ValueError("duplicate event taxonomy source identity")
        return ordered

    @field_validator("capabilities")
    @classmethod
    def _capabilities(
        cls, value: tuple[RenderInputWorkflowCapability, ...]
    ) -> tuple[RenderInputWorkflowCapability, ...]:
        ordered = tuple(sorted(value, key=lambda item: item.capability_id))
        if len({item.capability_id for item in ordered}) != len(ordered):
            raise ValueError("duplicate workflow capability identity")
        return ordered

    @model_validator(mode="after")
    def _exact_trigger_identities(self) -> WorkflowInterfaceRenderInput:
        triggers = {
            binding.event_type
            for capability in self.capabilities
            for binding in capability.bindings
            if isinstance(binding, RenderInputTriggeredByEventBinding)
        }
        if triggers != {item.identifier for item in self.taxonomy_sources}:
            raise ValueError("trigger identities must equal the pinned event taxonomy set")
        return self


def _binding_document(binding: RenderInputWorkflowBinding) -> dict[str, object]:
    if isinstance(binding, RenderInputTriggeredByEventBinding):
        return {"role": binding.role, "event_type": binding.event_type}
    result: dict[str, object] = {
        "role": binding.role,
        "module_id": binding.module_id,
        "action_node_id": binding.action_node_id,
    }
    if isinstance(binding, RenderInputCommitsResultBinding):
        result["workflow_result_id"] = binding.workflow_result_id
    return result


def render_workflow_module_interface_unit(
    *, unit: FamilyInstancePlan, render_input: WorkflowInterfaceRenderInput
) -> bytes:
    """Render exactly one canonical interface unit shape, in either live row.

    Resolve the row from the pinned digest first. Caller scope and path never
    select an output profile. A complete alternate canonical twin is a valid
    shape here; authorization to include it in a plan is checked upstream.
    """
    try:
        unit = FamilyInstancePlan.model_validate(unit.model_dump(mode="json"))
        render_input = WorkflowInterfaceRenderInput.model_validate(
            render_input.model_dump(mode="json")
        )
    except ValueError as exc:
        raise WorkflowInterfaceMaterializationError(
            "interface unit or render input failed cold validation"
        ) from exc

    if (
        unit.family_kind != WORKFLOW_MODULE_INTERFACE
        or unit.disposition is not PlanDisposition.RENDER
        or unit.source_scope is not PlanSourceScope.DECLARED
        or unit.materializer != "workflow_interface_executor"
    ):
        raise WorkflowInterfaceMaterializationError("unit is not an active interface render unit")
    rows = {
        row.row_digest: row
        for row in snapshot_layout_registry(build_app_layout_registry(())).rows
        if row.kind == WORKFLOW_MODULE_INTERFACE
    }
    row = rows.get(unit.family_identity_digest)
    if row is None:
        raise WorkflowInterfaceMaterializationError("family digest names no canonical interface row")
    if (
        unit.materializer != row.materializer
        or unit.disposition.value != row.disposition.value
        or unit.validator != row.validator
    ):
        raise WorkflowInterfaceMaterializationError("unit does not match its row's renderer contract")
    if len(unit.placeholder_values) != 1 or unit.placeholder_values[0][0] != "workflow_id":
        raise WorkflowInterfaceMaterializationError("unit requires exactly the workflow_id placeholder")
    workflow_id = canonical_instance_identity_value(unit.placeholder_values[0][1])
    if unit.unit_id != canonical_instance_unit_id(row.kind, workflow_id, row.row_digest):
        raise WorkflowInterfaceMaterializationError("unit id does not match its canonical row and instance")
    if render_input.workflow_id != workflow_id:
        raise WorkflowInterfaceMaterializationError("render input workflow_id differs from unit identity")
    expected_path = row.path_template.replace("{workflow_id}", workflow_id)
    if (
        len(unit.outputs) != 1
        or unit.outputs[0].path_scope != row.path_scope
        or unit.outputs[0].path != expected_path
    ):
        raise WorkflowInterfaceMaterializationError("output differs from the exact canonical row expansion")
    if (
        render_input.sources != unit.sources
        or render_input.edge_sources != unit.edge_sources
        or render_input.taxonomy_sources != unit.taxonomy_sources
    ):
        raise WorkflowInterfaceMaterializationError("render input does not match the exact pinned source sets")

    capabilities: list[dict[str, object]] = []
    for capability in render_input.capabilities:
        document: dict[str, object] = {"capability_id": capability.capability_id}
        if capability.description is not None:
            document["description"] = capability.description
        document["results"] = [
            result.model_dump(mode="json", exclude_none=True) for result in capability.results
        ]
        document["bindings"] = [_binding_document(binding) for binding in capability.bindings]
        capabilities.append(document)
    return yaml_decl_bytes(
        {
            "schema_version": MODULE_INTERFACE_SCHEMA_VERSION,
            "workflow_id": workflow_id,
            "capabilities": capabilities,
        }
    )


__all__ = [
    "MODULE_INTERFACE_SCHEMA_VERSION",
    "WORKFLOW_INTERFACE_RENDERER_IMPLEMENTATION_ID",
    "WORKFLOW_INTERFACE_RENDERER_IMPLEMENTATION_VERSION",
    "WORKFLOW_MODULE_INTERFACE",
    "RenderInputCommitsResultBinding",
    "RenderInputConsumesActionBinding",
    "RenderInputTriggeredByEventBinding",
    "RenderInputWorkflowBinding",
    "RenderInputWorkflowCapability",
    "RenderInputWorkflowResult",
    "WorkflowInterfaceMaterializationError",
    "WorkflowInterfaceRenderInput",
    "render_workflow_module_interface_unit",
]
