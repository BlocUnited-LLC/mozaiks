"""Offline canonical composition under aggregate CompilationPlan authority."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Literal, cast

from pydantic import Field, field_validator, model_validator

from mozaiksai.core.runtime.app.layout_registry import PathScope
from mozaiksai.core.semantics.compilation_plan import (
    CompilationPlan,
    FamilyInstancePlan,
    PlanDisposition,
    RegenerationClosure,
)
from mozaiksai.core.semantics.materialization import MaterializedBundle, MaterializedOutput
from mozaiksai.core.semantics.plan_authority import (
    PlanAuthorityProof,
    require_plan_authority_proof,
)
from mozaiksai.core.semantics.portable_path import validate_portable_path
from mozaiksai.core.semantics.refs import CompilationPlanRef, PlanUnitRef, SemanticsModel
from mozaiksai.core.semantics.resolver import SemanticReferenceResolver
from mozaiksai.core.workflow.assignment_artifacts import (
    AssignmentArtifactResult,
    validate_assignment_artifact_result,
)
from mozaiksai.core.workflow.plan_assignment_compiler import (
    CompiledAssignment,
    CompiledAssignmentSet,
)
from mozaiksai.core.workflow.structured_output_contracts import stable_digest

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_GLOBAL_PATH_SCOPES = frozenset(
    {
        PathScope.APP_BUNDLE_ROOT,
        PathScope.WORKSPACE_ROOT,
        PathScope.DEPLOYMENT_DERIVED,
        PathScope.GENERATED_STAGING,
    }
)


def _sha256(value: str, *, field_name: str) -> str:
    text = str(value or "").strip()
    if _SHA256.fullmatch(text) is None:
        raise ValueError(f"{field_name} must be a lowercase SHA-256 digest")
    return text


class CompositionOutcome(StrEnum):
    RENDERED = "rendered"
    AGENT_AUTHORED = "agent_authored"
    PRESERVED = "preserved"
    REUSED = "reused"
    REMOVED = "removed"
    INPUT_ONLY = "input_only"
    EXTERNAL_HANDOFF = "external_handoff"
    INAPPLICABLE = "inapplicable"


class ArtifactAddress(SemanticsModel):
    """One collision-domain-qualified physical artifact address."""

    path_scope: PathScope
    placeholder_values: tuple[tuple[str, str], ...] = Field(default_factory=tuple)
    path: str

    @field_validator("placeholder_values")
    @classmethod
    def _placeholders(
        cls, value: tuple[tuple[str, str], ...]
    ) -> tuple[tuple[str, str], ...]:
        ordered = tuple(sorted(value))
        keys = [key for key, _ in ordered]
        if len(keys) != len(set(keys)):
            raise ValueError("artifact address substitutions must have unique names")
        return ordered

    @field_validator("path")
    @classmethod
    def _path(cls, value: str) -> str:
        return cast(str, validate_portable_path(value).text)

    @model_validator(mode="after")
    def _scope_identity(self) -> ArtifactAddress:
        if self.path_scope in _GLOBAL_PATH_SCOPES and self.placeholder_values:
            raise ValueError("global artifact addresses cannot use instance placeholders")
        if self.path_scope not in _GLOBAL_PATH_SCOPES and not self.placeholder_values:
            raise ValueError("instance-relative artifact addresses require placeholders")
        return self


class AccountedArtifact(SemanticsModel):
    address: ArtifactAddress
    content_digest: str | None

    @field_validator("content_digest")
    @classmethod
    def _digest(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _sha256(value, field_name="content_digest")


class CompositionUnitEntry(SemanticsModel):
    plan_unit_ref: PlanUnitRef
    disposition: PlanDisposition
    outcome: CompositionOutcome
    artifacts: tuple[AccountedArtifact, ...] = Field(default_factory=tuple)
    source_digest: str | None

    @field_validator("source_digest")
    @classmethod
    def _source_digest(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _sha256(value, field_name="source_digest")

    @field_validator("artifacts")
    @classmethod
    def _artifacts(
        cls, value: tuple[AccountedArtifact, ...]
    ) -> tuple[AccountedArtifact, ...]:
        ordered = tuple(sorted(value, key=lambda item: _address_key(item.address)))
        _assert_unique_addresses([item.address for item in ordered])
        return ordered

    @model_validator(mode="after")
    def _outcome_matches_disposition(self) -> CompositionUnitEntry:
        allowed = {
            PlanDisposition.RENDER: {
                CompositionOutcome.RENDERED,
                CompositionOutcome.REUSED,
            },
            PlanDisposition.AGENT_AUTHOR: {
                CompositionOutcome.AGENT_AUTHORED,
                CompositionOutcome.REUSED,
            },
            PlanDisposition.REUSE_FROM_BASE: {CompositionOutcome.REUSED},
            PlanDisposition.PRESERVE_UNOWNED: {
                CompositionOutcome.PRESERVED,
                CompositionOutcome.REUSED,
            },
            PlanDisposition.INPUT_ONLY: {CompositionOutcome.INPUT_ONLY},
            PlanDisposition.EXTERNAL_HANDOFF: {
                CompositionOutcome.EXTERNAL_HANDOFF
            },
            PlanDisposition.INAPPLICABLE: {CompositionOutcome.INAPPLICABLE},
        }
        if self.outcome not in allowed[self.disposition]:
            raise ValueError(
                f"composition outcome {self.outcome.value!r} does not match "
                f"disposition {self.disposition.value!r}"
            )
        contentless = {
            CompositionOutcome.INPUT_ONLY,
            CompositionOutcome.EXTERNAL_HANDOFF,
            CompositionOutcome.INAPPLICABLE,
        }
        if self.outcome in contentless:
            if self.source_digest is not None or any(
                item.content_digest is not None for item in self.artifacts
            ):
                raise ValueError(f"{self.outcome.value} cannot carry content authority")
        elif self.source_digest is None or any(
            item.content_digest is None for item in self.artifacts
        ):
            raise ValueError(f"{self.outcome.value} requires content and source digests")
        return self


class RemovedBaseArtifact(SemanticsModel):
    outcome: Literal[CompositionOutcome.REMOVED] = CompositionOutcome.REMOVED
    base_plan_unit_ref: PlanUnitRef
    artifact: AccountedArtifact

    @model_validator(mode="after")
    def _has_content_identity(self) -> RemovedBaseArtifact:
        if self.artifact.content_digest is None:
            raise ValueError("removed base artifact requires its prior content digest")
        return self


class CompositionLedger(SemanticsModel):
    ledger_schema_version: Literal["mozaiks.composition_ledger.v1"] = (
        "mozaiks.composition_ledger.v1"
    )
    compilation_plan_ref: CompilationPlanRef
    base_revision_digest: str | None
    unit_entries: tuple[CompositionUnitEntry, ...]
    removed_base_artifacts: tuple[RemovedBaseArtifact, ...] = Field(default_factory=tuple)
    final_bundle_manifest: tuple[AccountedArtifact, ...]
    bundle_digest: str
    ledger_digest: str

    @field_validator("base_revision_digest")
    @classmethod
    def _base_digest(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _sha256(value, field_name="base_revision_digest")

    @field_validator("bundle_digest", "ledger_digest")
    @classmethod
    def _digests(cls, value: str, info: Any) -> str:
        return _sha256(value, field_name=str(info.field_name))

    @field_validator("unit_entries")
    @classmethod
    def _units(
        cls, value: tuple[CompositionUnitEntry, ...]
    ) -> tuple[CompositionUnitEntry, ...]:
        ordered = tuple(sorted(value, key=lambda item: item.plan_unit_ref.unit_id))
        unit_ids = [item.plan_unit_ref.unit_id for item in ordered]
        if len(unit_ids) != len(set(unit_ids)):
            raise ValueError("composition ledger contains duplicate plan units")
        return ordered

    @field_validator("removed_base_artifacts")
    @classmethod
    def _removed(
        cls, value: tuple[RemovedBaseArtifact, ...]
    ) -> tuple[RemovedBaseArtifact, ...]:
        ordered = tuple(
            sorted(value, key=lambda item: _address_key(item.artifact.address))
        )
        _assert_unique_addresses([item.artifact.address for item in ordered])
        return ordered

    @field_validator("final_bundle_manifest")
    @classmethod
    def _manifest(
        cls, value: tuple[AccountedArtifact, ...]
    ) -> tuple[AccountedArtifact, ...]:
        ordered = tuple(sorted(value, key=lambda item: _address_key(item.address)))
        _assert_unique_addresses([item.address for item in ordered])
        if any(item.content_digest is None for item in ordered):
            raise ValueError("final bundle manifest entries require content digests")
        return ordered

    @model_validator(mode="after")
    def _ledger_identity(self) -> CompositionLedger:
        if any(
            entry.plan_unit_ref.compilation_plan_ref != self.compilation_plan_ref
            for entry in self.unit_entries
        ):
            raise ValueError("unit entry belongs to another CompilationPlan")
        final_from_units = tuple(
            artifact
            for entry in self.unit_entries
            for artifact in entry.artifacts
            if artifact.content_digest is not None
        )
        if tuple(sorted(final_from_units, key=lambda item: _address_key(item.address))) != (
            self.final_bundle_manifest
        ):
            raise ValueError("final bundle manifest does not match unit accounting")
        final_addresses = {item.address for item in self.final_bundle_manifest}
        if any(
            item.artifact.address in final_addresses
            for item in self.removed_base_artifacts
        ):
            raise ValueError("removed base artifact leaked into final bundle")
        if self.removed_base_artifacts and self.base_revision_digest is None:
            raise ValueError("removed base artifacts require a base revision digest")
        bundle_payload = [item.model_dump(mode="json") for item in self.final_bundle_manifest]
        if self.bundle_digest != stable_digest(bundle_payload):
            raise ValueError("bundle_digest does not match final bundle manifest")
        payload = self.model_dump(mode="json", exclude={"ledger_digest"})
        if self.ledger_digest != stable_digest(payload):
            raise ValueError("ledger_digest does not match composition ledger")
        return self


@dataclass(frozen=True, slots=True)
class ComposedArtifactContent:
    unit_id: str
    address: ArtifactAddress
    content: bytes
    content_digest: str


@dataclass(frozen=True, slots=True)
class CanonicalComposedBundle:
    """Runtime bytes paired with a content-free canonical ledger."""

    plan_digest: str
    artifacts: tuple[ComposedArtifactContent, ...]
    ledger: CompositionLedger

    def files(
        self,
        *,
        path_scope: PathScope = PathScope.APP_BUNDLE_ROOT,
        placeholder_values: tuple[tuple[str, str], ...] = (),
    ) -> dict[str, bytes]:
        return {
            item.address.path: item.content
            for item in self.artifacts
            if item.address.path_scope is path_scope
            and item.address.placeholder_values == placeholder_values
        }


def _plan_ref(plan: CompilationPlan) -> CompilationPlanRef:
    return CompilationPlanRef(
        subject_id=plan.graph_id,
        subject_version=plan.graph_version,
        content_digest=plan.plan_digest,
        scope=plan.scope,
    )


def _unit_ref(plan: CompilationPlan, unit: FamilyInstancePlan) -> PlanUnitRef:
    return PlanUnitRef(
        compilation_plan_ref=_plan_ref(plan),
        unit_id=unit.unit_id,
        unit_digest=unit.unit_digest,
    )


def _address(unit: FamilyInstancePlan, path_scope: str, path: str) -> ArtifactAddress:
    parsed_scope = PathScope(path_scope)
    placeholders = () if parsed_scope in _GLOBAL_PATH_SCOPES else unit.placeholder_values
    return ArtifactAddress(
        path_scope=parsed_scope,
        placeholder_values=placeholders,
        path=path,
    )


def _address_key(address: ArtifactAddress) -> tuple[str, tuple[tuple[str, str], ...], str]:
    return (address.path_scope.value, address.placeholder_values, address.path)


def _assert_unique_addresses(addresses: list[ArtifactAddress]) -> None:
    keys = [_address_key(item) for item in addresses]
    if len(keys) != len(set(keys)):
        raise ValueError("duplicate physical artifact address")
    by_domain: dict[tuple[str, tuple[tuple[str, str], ...]], list[str]] = {}
    for address in addresses:
        domain = (address.path_scope.value, address.placeholder_values)
        by_domain.setdefault(domain, []).append(address.path)
    for paths in by_domain.values():
        by_lower: dict[str, str] = {}
        for path in paths:
            prior = by_lower.setdefault(path.casefold(), path)
            if prior != path:
                raise ValueError(f"case-fold composition collision: {prior!r} and {path!r}")
        ordered = sorted(paths)
        for index, parent in enumerate(ordered):
            for child in ordered[index + 1 :]:
                if child.startswith(f"{parent}/"):
                    raise ValueError(
                        f"parent/child composition collision: {parent!r} and {child!r}"
                    )


def _materialized_source_digest(output: MaterializedOutput) -> str:
    return stable_digest(
        {
            "unit_id": output.unit_id,
            "path_scope": output.path_scope,
            "path": output.path,
            "origin": output.origin,
            "content_digest": output.content_digest,
        }
    )


def _index_materialized(
    outputs: Iterable[MaterializedOutput],
) -> dict[str, list[MaterializedOutput]]:
    indexed: dict[str, list[MaterializedOutput]] = {}
    for output in outputs:
        if output.origin not in {"rendered", "preserved", "reused"}:
            raise ValueError(f"unknown materialized output origin {output.origin!r}")
        if hashlib.sha256(output.content).hexdigest() != output.content_digest:
            raise ValueError(f"materialized output {output.path!r} failed digest verification")
        indexed.setdefault(output.unit_id, []).append(output)
    return indexed


def _index_assignments(
    assignments: CompiledAssignmentSet,
) -> dict[str, CompiledAssignment]:
    verified = CompiledAssignmentSet.model_validate(assignments.model_dump(mode="json"))
    indexed: dict[str, CompiledAssignment] = {}
    for assignment in verified.ordered_assignments:
        unit_id = assignment.plan_unit_ref.unit_id
        if unit_id in indexed:
            raise ValueError(f"duplicate compiled assignment for unit {unit_id!r}")
        indexed[unit_id] = assignment
    return indexed


def _index_results(
    results: Iterable[AssignmentArtifactResult],
) -> dict[str, AssignmentArtifactResult]:
    indexed: dict[str, AssignmentArtifactResult] = {}
    for result in results:
        unit_id = result.plan_unit_ref.unit_id
        if unit_id in indexed:
            raise ValueError(f"duplicate assignment artifact result for unit {unit_id!r}")
        indexed[unit_id] = result
    return indexed


def compose_plan_artifacts(
    *,
    plan: CompilationPlan,
    resolver: SemanticReferenceResolver,
    assignments: CompiledAssignmentSet,
    assignment_results: Iterable[AssignmentArtifactResult],
    materialized_bundle: MaterializedBundle,
    base_revision_digest: str | None,
    plan_authority_proof: PlanAuthorityProof,
    base_plan: CompilationPlan | None = None,
    base_outputs: Iterable[MaterializedOutput] = (),
    regeneration_closure: RegenerationClosure | None = None,
) -> CanonicalComposedBundle:
    """Compose all plan units without execution, persistence, or publication.

    Plan authority is mandatory: a validator-issued
    :class:`PlanAuthorityProof` covering exactly this plan must be supplied,
    and it is verified before any assignment result, preserved byte,
    materialized output, or path is inspected. ``None`` and non-issued proof
    documents fail closed. This boundary cannot re-derive the plan itself —
    composed plans legitimately carry ``preserve_unowned`` units that
    greenfield derivation never selects; the brownfield base-input contract
    that would make them derivable is the explicitly identified future
    authority, and until it exists the validator (or a test fixture
    simulating that future authority through the private issuance seam) is
    the only proof source.
    """

    verified_plan = CompilationPlan.model_validate(plan.model_dump(mode="json"))
    require_plan_authority_proof(plan_authority_proof, verified_plan)
    if verified_plan.gaps:
        raise ValueError("blocking CompilationPlan gaps prevent canonical composition")
    if materialized_bundle.plan_digest != verified_plan.plan_digest:
        raise ValueError("materialized bundle belongs to another CompilationPlan")

    plan_ref = _plan_ref(verified_plan)
    assignment_by_unit = _index_assignments(assignments)
    result_by_unit = _index_results(assignment_results)
    if any(
        assignment.base_revision_digest != base_revision_digest
        for assignment in assignment_by_unit.values()
    ):
        raise ValueError("compiled assignment carries a stale base revision digest")
    current_materialized = _index_materialized(materialized_bundle.outputs)
    base_materialized = _index_materialized(base_outputs)

    reusable: set[str] = set()
    removed: set[str] = set()
    verified_base: CompilationPlan | None = None
    if regeneration_closure is None:
        if base_plan is not None or base_materialized or base_revision_digest is not None:
            raise ValueError("base inputs require a regeneration closure")
    else:
        if base_plan is None or base_revision_digest is None:
            raise ValueError("refinement composition requires base plan and revision digest")
        verified_base = CompilationPlan.model_validate(base_plan.model_dump(mode="json"))
        closure = RegenerationClosure.model_validate(
            regeneration_closure.model_dump(mode="json")
        )
        if closure.successor_plan_digest != verified_plan.plan_digest:
            raise ValueError("regeneration closure targets another successor plan")
        if closure.base_plan_digest != verified_base.plan_digest:
            raise ValueError("regeneration closure targets another base plan")
        reusable = set(closure.reusable)
        removed = set(closure.removed)
        successor_ids = {unit.unit_id for unit in verified_plan.units}
        if reusable - successor_ids:
            raise ValueError("regeneration closure names unknown reusable units")
        base_ids = {unit.unit_id for unit in verified_base.units}
        if removed != base_ids - successor_ids:
            raise ValueError("regeneration closure removed partition is incomplete")
        unexpected_base = set(base_materialized) - reusable - removed
        if unexpected_base:
            raise ValueError(
                f"unaccounted base artifact units: {sorted(unexpected_base)}"
            )

    entries: list[CompositionUnitEntry] = []
    contents: list[ComposedArtifactContent] = []
    used_assignments: set[str] = set()
    used_results: set[str] = set()
    used_materialized: set[str] = set()
    used_base: set[str] = set()

    for unit in verified_plan.units:
        unit_ref = _unit_ref(verified_plan, unit)
        resolver.resolve_plan_unit(unit_ref, requesting_scope=verified_plan.scope)
        expected = {
            _address(unit, output.path_scope, output.path) for output in unit.outputs
        }
        outcome: CompositionOutcome
        accounted: list[AccountedArtifact] = []
        source_digest: str | None = None

        if unit.unit_id in reusable or unit.disposition is PlanDisposition.REUSE_FROM_BASE:
            if verified_base is None:
                raise ValueError("reusable unit requires a canonical base plan")
            base_unit = verified_base.unit(unit.unit_id)
            resolver.resolve_plan_unit(
                _unit_ref(verified_base, base_unit),
                requesting_scope=verified_base.scope,
            )
            base_items = base_materialized.get(unit.unit_id, [])
            base_actual = {
                _address(unit, item.path_scope, item.path): item for item in base_items
            }
            if len(base_actual) != len(base_items):
                raise ValueError(f"duplicate base artifact for unit {unit.unit_id!r}")
            if set(base_actual) != expected:
                raise ValueError(
                    f"reusable unit {unit.unit_id!r} does not have exact base artifacts"
                )
            used_base.add(unit.unit_id)
            outcome = CompositionOutcome.REUSED
            source_digest = stable_digest(
                [
                    _materialized_source_digest(base_actual[address])
                    for address in sorted(base_actual, key=_address_key)
                ]
            )
            for address, item in base_actual.items():
                accounted.append(
                    AccountedArtifact(address=address, content_digest=item.content_digest)
                )
                contents.append(
                    ComposedArtifactContent(
                        unit_id=unit.unit_id,
                        address=address,
                        content=item.content,
                        content_digest=item.content_digest,
                    )
                )
        elif unit.disposition is PlanDisposition.AGENT_AUTHOR:
            assignment = assignment_by_unit.get(unit.unit_id)
            result = result_by_unit.get(unit.unit_id)
            if assignment is None or result is None:
                raise ValueError(f"agent-authored unit {unit.unit_id!r} lacks an artifact result")
            if assignment.plan_unit_ref.compilation_plan_ref != plan_ref:
                raise ValueError("compiled assignment belongs to another CompilationPlan")
            resolver.resolve_plan_unit(
                assignment.plan_unit_ref, requesting_scope=verified_plan.scope
            )
            verified_result = validate_assignment_artifact_result(
                assignment=assignment, result=result
            )
            outputs_by_path: dict[str, list[ArtifactAddress]] = {}
            for address in expected:
                outputs_by_path.setdefault(address.path, []).append(address)
            assignment_actual: dict[ArtifactAddress, Any] = {}
            for artifact in verified_result.artifacts:
                matches = outputs_by_path.get(artifact.path, [])
                if len(matches) != 1:
                    raise ValueError(
                        f"assignment artifact {artifact.path!r} does not resolve to exactly "
                        "one plan-owned physical address"
                    )
                assignment_actual[matches[0]] = artifact
            if set(assignment_actual) != expected:
                raise ValueError("assignment artifact result does not cover exact unit outputs")
            used_assignments.add(unit.unit_id)
            used_results.add(unit.unit_id)
            outcome = CompositionOutcome.AGENT_AUTHORED
            source_digest = verified_result.result_digest
            for address, artifact in assignment_actual.items():
                accounted.append(
                    AccountedArtifact(
                        address=address, content_digest=artifact.content_digest
                    )
                )
                contents.append(
                    ComposedArtifactContent(
                        unit_id=unit.unit_id,
                        address=address,
                        content=artifact.content.encode("utf-8"),
                        content_digest=artifact.content_digest,
                    )
                )
        elif unit.disposition in {
            PlanDisposition.RENDER,
            PlanDisposition.PRESERVE_UNOWNED,
        }:
            materialized = current_materialized.get(unit.unit_id, [])
            materialized_actual = {
                _address(unit, item.path_scope, item.path): item for item in materialized
            }
            if len(materialized_actual) != len(materialized):
                raise ValueError(
                    f"duplicate materialized artifact for unit {unit.unit_id!r}"
                )
            if set(materialized_actual) != expected:
                raise ValueError(
                    f"{unit.disposition.value} unit {unit.unit_id!r} lacks exact materialized outputs"
                )
            used_materialized.add(unit.unit_id)
            outcome = (
                CompositionOutcome.RENDERED
                if unit.disposition is PlanDisposition.RENDER
                else CompositionOutcome.PRESERVED
            )
            expected_origin = "rendered" if outcome is CompositionOutcome.RENDERED else "preserved"
            if any(
                item.origin != expected_origin for item in materialized_actual.values()
            ):
                raise ValueError(
                    f"{unit.unit_id!r} materialized origin does not match {outcome.value}"
                )
            source_digest = stable_digest(
                [
                    _materialized_source_digest(materialized_actual[address])
                    for address in sorted(materialized_actual, key=_address_key)
                ]
            )
            for address, item in materialized_actual.items():
                accounted.append(
                    AccountedArtifact(address=address, content_digest=item.content_digest)
                )
                contents.append(
                    ComposedArtifactContent(
                        unit_id=unit.unit_id,
                        address=address,
                        content=item.content,
                        content_digest=item.content_digest,
                    )
                )
        else:
            contentless_outcome = {
                PlanDisposition.INPUT_ONLY: CompositionOutcome.INPUT_ONLY,
                PlanDisposition.EXTERNAL_HANDOFF: CompositionOutcome.EXTERNAL_HANDOFF,
                PlanDisposition.INAPPLICABLE: CompositionOutcome.INAPPLICABLE,
            }.get(unit.disposition)
            if contentless_outcome is None:
                raise ValueError(
                    f"unit {unit.unit_id!r} has unsupported disposition "
                    f"{unit.disposition.value!r}"
                )
            outcome = contentless_outcome
            accounted.extend(
                AccountedArtifact(address=address, content_digest=None)
                for address in expected
            )

        entries.append(
            CompositionUnitEntry(
                plan_unit_ref=unit_ref,
                disposition=unit.disposition,
                outcome=outcome,
                artifacts=tuple(accounted),
                source_digest=source_digest,
            )
        )

    if set(assignment_by_unit) != used_assignments:
        raise ValueError(
            f"extra compiled assignments: {sorted(set(assignment_by_unit) - used_assignments)}"
        )
    if set(result_by_unit) != used_results:
        raise ValueError(
            f"extra assignment artifact results: {sorted(set(result_by_unit) - used_results)}"
        )
    if set(current_materialized) != used_materialized:
        raise ValueError(
            f"extra materialized units: {sorted(set(current_materialized) - used_materialized)}"
        )

    removed_entries: list[RemovedBaseArtifact] = []
    if verified_base is not None:
        base_by_id = {unit.unit_id: unit for unit in verified_base.units}
        for unit_id in sorted(removed):
            unit = base_by_id[unit_id]
            resolver.resolve_plan_unit(
                _unit_ref(verified_base, unit),
                requesting_scope=verified_base.scope,
            )
            items = base_materialized.get(unit_id, [])
            expected = {
                _address(unit, output.path_scope, output.path) for output in unit.outputs
            }
            removed_actual = {
                _address(unit, item.path_scope, item.path): item for item in items
            }
            if len(removed_actual) != len(items):
                raise ValueError(f"duplicate removed base artifact for unit {unit_id!r}")
            if expected and set(removed_actual) != expected:
                raise ValueError(
                    f"removed base unit {unit_id!r} lacks exact prior artifacts"
                )
            used_base.add(unit_id)
            for address, item in removed_actual.items():
                removed_entries.append(
                    RemovedBaseArtifact(
                        base_plan_unit_ref=_unit_ref(verified_base, unit),
                        artifact=AccountedArtifact(
                            address=address, content_digest=item.content_digest
                        ),
                    )
                )
    if set(base_materialized) != used_base:
        raise ValueError(
            f"unaccounted base artifact units: {sorted(set(base_materialized) - used_base)}"
        )

    contents.sort(key=lambda item: _address_key(item.address))
    _assert_unique_addresses([item.address for item in contents])
    manifest = tuple(
        AccountedArtifact(address=item.address, content_digest=item.content_digest)
        for item in contents
    )
    entries.sort(key=lambda item: item.plan_unit_ref.unit_id)
    removed_entries.sort(key=lambda item: _address_key(item.artifact.address))
    bundle_digest = stable_digest([item.model_dump(mode="json") for item in manifest])
    ledger_payload: dict[str, Any] = {
        "ledger_schema_version": "mozaiks.composition_ledger.v1",
        "compilation_plan_ref": plan_ref,
        "base_revision_digest": base_revision_digest,
        "unit_entries": tuple(entries),
        "removed_base_artifacts": tuple(removed_entries),
        "final_bundle_manifest": manifest,
        "bundle_digest": bundle_digest,
    }
    canonical_ledger = CompositionLedger.model_construct(
        **ledger_payload, ledger_digest="0" * 64
    )
    ledger = CompositionLedger(
        **ledger_payload,
        ledger_digest=stable_digest(
            canonical_ledger.model_dump(mode="json", exclude={"ledger_digest"})
        ),
    )
    return CanonicalComposedBundle(
        plan_digest=verified_plan.plan_digest,
        artifacts=tuple(contents),
        ledger=ledger,
    )


__all__ = [
    "AccountedArtifact",
    "ArtifactAddress",
    "CanonicalComposedBundle",
    "ComposedArtifactContent",
    "CompositionLedger",
    "CompositionOutcome",
    "CompositionUnitEntry",
    "RemovedBaseArtifact",
    "compose_plan_artifacts",
]
