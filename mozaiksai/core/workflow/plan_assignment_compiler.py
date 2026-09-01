"""Offline CompilationPlan-to-assignment contract compiler.

This deterministic substrate does not enqueue work, select agents, call AG2,
touch persistence, or participate in production AppBuildPlan flows.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from mozaiksai.core.runtime.app.layout_registry import ValidatorIdentifier
from mozaiksai.core.semantics.compilation_plan import CompilationPlan, PlanDisposition
from mozaiksai.core.semantics.refs import PlanUnitRef, SemanticPayloadRef
from mozaiksai.core.semantics.resolver import SemanticReferenceResolver

from .assignment_kinds import AssignmentKind
from .structured_output_contracts import (
    StructuredOutputContractRef,
    resolve_structured_output_contract_ref,
    stable_digest,
)

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
DependencyContextRef = Annotated[
    SemanticPayloadRef | PlanUnitRef, Field(union_mode="left_to_right")
]


class _FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ApprovedAssignmentSpec(_FrozenModel):
    """Closed approval for exactly one executable CompilationPlan unit."""

    plan_unit_ref: PlanUnitRef
    assignment_kind: AssignmentKind
    dependency_context_refs: tuple[DependencyContextRef, ...]
    required_structured_output_ref: StructuredOutputContractRef
    required_validators: tuple[ValidatorIdentifier, ...] = Field(min_length=1)
    assignment_retry_limit: int = Field(default=0, ge=0, le=5, strict=True)
    base_revision_digest: str | None

    @field_validator("base_revision_digest")
    @classmethod
    def _base_revision(cls, value: str | None) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        if _SHA256.fullmatch(text) is None:
            raise ValueError("base_revision_digest must be a lowercase SHA-256 digest")
        return text

    @field_validator("dependency_context_refs")
    @classmethod
    def _refs(
        cls, value: tuple[DependencyContextRef, ...]
    ) -> tuple[DependencyContextRef, ...]:
        keyed = sorted(value, key=lambda ref: stable_digest(ref.model_dump(mode="json")))
        digests = [stable_digest(ref.model_dump(mode="json")) for ref in keyed]
        if len(digests) != len(set(digests)):
            raise ValueError("dependency_context_refs must be unique")
        return tuple(keyed)

    @field_validator("required_validators")
    @classmethod
    def _validators(
        cls, value: tuple[ValidatorIdentifier, ...]
    ) -> tuple[ValidatorIdentifier, ...]:
        parsed = tuple(ValidatorIdentifier(item) for item in value)
        if ValidatorIdentifier.NONE in parsed or len(parsed) != len(set(parsed)):
            raise ValueError("required_validators must be unique non-NONE identifiers")
        return tuple(sorted(parsed, key=lambda item: item.value))


class ApprovedPlan(_FrozenModel):
    assignments: tuple[ApprovedAssignmentSpec, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _one_spec_per_unit(self) -> ApprovedPlan:
        refs = [stable_digest(item.plan_unit_ref.model_dump(mode="json")) for item in self.assignments]
        if len(refs) != len(set(refs)):
            raise ValueError("approved plan contains duplicate plan-unit refs")
        plan_refs = {item.plan_unit_ref.compilation_plan_ref for item in self.assignments}
        if len(plan_refs) != 1:
            raise ValueError("approved assignments must belong to exactly one compilation plan")
        return self


class CompiledAssignment(_FrozenModel):
    assignment_id: str
    plan_unit_ref: PlanUnitRef
    assignment_kind: AssignmentKind
    owned_paths: tuple[str, ...] = Field(min_length=1)
    depends_on_unit_refs: tuple[PlanUnitRef, ...]
    dependency_context_refs: tuple[DependencyContextRef, ...]
    required_structured_output_ref: StructuredOutputContractRef
    required_validators: tuple[ValidatorIdentifier, ...]
    assignment_retry_limit: int
    base_revision_digest: str | None
    assignment_digest: str

    @model_validator(mode="after")
    def _digest(self) -> CompiledAssignment:
        payload = self.model_dump(
            mode="json", exclude={"assignment_id", "assignment_digest"}
        )
        expected = stable_digest(payload)
        if self.assignment_digest != expected:
            raise ValueError("assignment_digest does not match assignment content")
        if self.assignment_id != f"wa_{expected[:24]}":
            raise ValueError("assignment_id does not match canonical assignment identity")
        return self


class CompiledAssignmentSet(_FrozenModel):
    ordered_assignments: tuple[CompiledAssignment, ...]
    assignment_set_digest: str

    @model_validator(mode="after")
    def _digest(self) -> CompiledAssignmentSet:
        expected = stable_digest([item.assignment_digest for item in self.ordered_assignments])
        if self.assignment_set_digest != expected:
            raise ValueError("assignment_set_digest does not match compiled assignments")
        return self


def compile_approved_plan(
    plan: ApprovedPlan,
    *,
    resolver: SemanticReferenceResolver,
    structured_output_configs: Mapping[str, Any],
) -> CompiledAssignmentSet:
    """Cold-resolve and compile approved units without execution side effects."""

    compiled: list[CompiledAssignment] = []
    plan_ref = plan.assignments[0].plan_unit_ref.compilation_plan_ref
    scope = plan_ref.scope
    canonical_plan = resolver.resolve(plan_ref, requesting_scope=scope)
    if not isinstance(canonical_plan, CompilationPlan):
        raise ValueError("compilation plan ref did not resolve to a canonical plan")
    canonical_plan = CompilationPlan.model_validate(
        canonical_plan.model_dump(mode="json")
    )
    plan_order = {unit.unit_id: index for index, unit in enumerate(canonical_plan.units)}
    for spec in plan.assignments:
        unit = resolver.resolve_plan_unit(spec.plan_unit_ref, requesting_scope=scope)
        if unit.disposition is not PlanDisposition.AGENT_AUTHOR:
            raise ValueError("only agent_author plan units may become assignments")
        if spec.assignment_kind is not unit.assignment_kind:
            raise ValueError("assignment kind does not match plan-unit authority")
        if spec.required_structured_output_ref != unit.required_structured_output_ref:
            raise ValueError("structured-output ref does not match plan-unit authority")
        if spec.required_validators != (unit.validator,):
            raise ValueError("validators do not match plan-unit authority")

        semantic_refs = tuple(
            ref for ref in spec.dependency_context_refs if isinstance(ref, SemanticPayloadRef)
        )
        unit_refs = tuple(
            ref for ref in spec.dependency_context_refs if isinstance(ref, PlanUnitRef)
        )
        if {(ref.node_id, ref.content_digest) for ref in semantic_refs} != {
            (source.node_id, source.payload_digest) for source in unit.sources
        }:
            raise ValueError("semantic dependency refs must exactly match the source footprint")
        for semantic_ref in semantic_refs:
            if semantic_ref.scope != scope:
                raise ValueError("semantic dependency ref has foreign scope")
            resolver.resolve_semantic_payload(semantic_ref, requesting_scope=scope)

        if {ref.unit_id for ref in unit_refs} != set(unit.depends_on_units):
            raise ValueError("plan-unit dependency refs must exactly match unit dependencies")
        for dependency_ref in unit_refs:
            if dependency_ref.compilation_plan_ref != plan_ref:
                raise ValueError("dependency plan-unit ref belongs to a foreign plan")
            resolver.resolve_plan_unit(dependency_ref, requesting_scope=scope)

        resolve_structured_output_contract_ref(
            spec.required_structured_output_ref, configs=structured_output_configs
        )

        depends_on_refs = tuple(sorted(unit_refs, key=lambda ref: ref.unit_id))
        payload: dict[str, Any] = {
            "plan_unit_ref": spec.plan_unit_ref.model_dump(mode="json"),
            "assignment_kind": spec.assignment_kind.value,
            "owned_paths": [output.path for output in unit.outputs],
            "depends_on_unit_refs": [ref.model_dump(mode="json") for ref in depends_on_refs],
            "dependency_context_refs": [
                ref.model_dump(mode="json") for ref in spec.dependency_context_refs
            ],
            "required_structured_output_ref": spec.required_structured_output_ref.model_dump(
                mode="json"
            ),
            "required_validators": [item.value for item in spec.required_validators],
            "assignment_retry_limit": spec.assignment_retry_limit,
            "base_revision_digest": spec.base_revision_digest,
        }
        digest = stable_digest(payload)
        compiled.append(
            CompiledAssignment(
                assignment_id=f"wa_{digest[:24]}", assignment_digest=digest, **payload
            )
        )

    ordered = tuple(
        sorted(compiled, key=lambda item: plan_order[item.plan_unit_ref.unit_id])
    )
    return CompiledAssignmentSet(
        ordered_assignments=ordered,
        assignment_set_digest=stable_digest([item.assignment_digest for item in ordered]),
    )


__all__ = [
    "ApprovedAssignmentSpec",
    "ApprovedPlan",
    "CompiledAssignment",
    "CompiledAssignmentSet",
    "DependencyContextRef",
    "compile_approved_plan",
]
