"""Offline immutable artifact-revision and publication contracts for ADR 0007 Slice 5C."""

from __future__ import annotations

from collections.abc import Sequence
from enum import StrEnum
from typing import Any, Literal, cast

from pydantic import Field, field_validator, model_validator

from mozaiksai.core.runtime.app.layout_registry import ValidatorIdentifier
from mozaiksai.core.semantics.canonical import canonical_digest
from mozaiksai.core.semantics.compilation_plan import CompilationPlan, PlanDisposition
from mozaiksai.core.semantics.composition_ledger import (
    CompositionLedger,
    CompositionOutcome,
)
from mozaiksai.core.semantics.plan_authority import (
    CompilationPlanAuthorityInputs,
    PlanAuthorityError,
    PlanAuthorityMismatch,
    validate_compilation_plan_against_authority,
)
from mozaiksai.core.semantics.refs import (
    ArtifactRevisionRef,
    CompilationPlanAuthorityRef,
    CompilationPlanRef,
    ExecutionAccessScopeRef,
    ImplementationBindingRef,
    SemanticGraphRef,
    SemanticsModel,
    _validate_digest,
    _validate_identifier,
)
from mozaiksai.core.workflow.assignment_artifacts import (
    AssignmentArtifactResult,
    ValidatorReceipt,
)


class ArtifactRevisionValidationEvidence(SemanticsModel):
    """Immutable proof that one exact composed bundle passed its required validators."""

    evidence_schema_version: Literal["mozaiks.artifact_revision_validation_evidence.v1"] = (
        "mozaiks.artifact_revision_validation_evidence.v1"
    )
    scope: ExecutionAccessScopeRef
    app_id: str
    compilation_plan_ref: CompilationPlanRef
    composition_ledger_digest: str
    bundle_digest: str
    assignment_result_digests: tuple[str, ...]
    bundle_validator_receipts: tuple[ValidatorReceipt, ...]
    evidence_digest: str

    @field_validator("app_id")
    @classmethod
    def _app_id(cls, value: str) -> str:
        return _validate_identifier(value, field_name="app_id")

    @field_validator("composition_ledger_digest", "bundle_digest", "evidence_digest")
    @classmethod
    def _digest(cls, value: str, info: Any) -> str:
        return _validate_digest(value, field_name=str(info.field_name))

    @field_validator("assignment_result_digests")
    @classmethod
    def _assignment_results(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        parsed = tuple(
            sorted(_validate_digest(item, field_name="assignment_result_digest") for item in value)
        )
        if len(parsed) != len(set(parsed)):
            raise ValueError("assignment_result_digests must be unique")
        return parsed

    @field_validator("bundle_validator_receipts")
    @classmethod
    def _receipts(cls, value: tuple[ValidatorReceipt, ...]) -> tuple[ValidatorReceipt, ...]:
        ordered = tuple(sorted(value, key=lambda item: item.validator.value))
        validators = [item.validator for item in ordered]
        if ValidatorIdentifier.NONE in validators:
            raise ValueError("NONE is not an executable validator")
        if len(validators) != len(set(validators)):
            raise ValueError("bundle validator receipts must be unique")
        if any(not item.passed for item in ordered):
            raise ValueError("artifact revision evidence requires passing validators")
        return ordered

    @model_validator(mode="after")
    def _identity(self) -> ArtifactRevisionValidationEvidence:
        if self.compilation_plan_ref.scope != self.scope:
            raise ValueError("CompilationPlan scope does not match validation evidence")
        if any(
            receipt.subject_digest != self.bundle_digest
            for receipt in self.bundle_validator_receipts
        ):
            raise ValueError("bundle validator receipt does not bind the exact bundle digest")
        expected = canonical_digest(self.model_dump(mode="json", exclude={"evidence_digest"}))
        if self.evidence_digest != expected:
            raise ValueError("evidence_digest does not match validation evidence")
        return self


class ArtifactRevision(SemanticsModel):
    """One immutable application revision; publication state lives elsewhere."""

    revision_schema_version: Literal["mozaiks.artifact_revision.v1"] = (
        "mozaiks.artifact_revision.v1"
    )
    scope: ExecutionAccessScopeRef
    app_id: str
    parent_revision_ref: ArtifactRevisionRef | None
    semantic_graph_ref: SemanticGraphRef
    implementation_binding_ref: ImplementationBindingRef
    compilation_plan_ref: CompilationPlanRef
    #: Content-addressed reference to the exact immutable
    #: CompilationPlanAuthorityInputs document the plan was derived from.
    #: Part of immutable revision identity: two revisions with the same plan
    #: self-digest but different derivation authority are distinguishable.
    compilation_plan_authority_ref: CompilationPlanAuthorityRef
    composition_ledger_digest: str
    bundle_digest: str
    validation_evidence_digest: str
    revision_digest: str

    @field_validator("app_id")
    @classmethod
    def _app_id(cls, value: str) -> str:
        return _validate_identifier(value, field_name="app_id")

    @field_validator(
        "composition_ledger_digest",
        "bundle_digest",
        "validation_evidence_digest",
        "revision_digest",
    )
    @classmethod
    def _digest(cls, value: str, info: Any) -> str:
        return _validate_digest(value, field_name=str(info.field_name))

    @model_validator(mode="after")
    def _identity(self) -> ArtifactRevision:
        refs = (
            self.semantic_graph_ref,
            self.implementation_binding_ref,
            self.compilation_plan_ref,
            self.compilation_plan_authority_ref,
        )
        if any(ref.scope != self.scope for ref in refs):
            raise ValueError("artifact revision references must share its execution scope")
        if self.parent_revision_ref is not None and (
            self.parent_revision_ref.scope != self.scope
            or self.parent_revision_ref.app_id != self.app_id
        ):
            raise ValueError("parent revision must belong to the same scope and app")
        expected = canonical_digest(self.model_dump(mode="json", exclude={"revision_digest"}))
        if self.revision_digest != expected:
            raise ValueError("revision_digest does not match artifact revision")
        return self

    @property
    def ref(self) -> ArtifactRevisionRef:
        return ArtifactRevisionRef(
            scope=self.scope,
            app_id=self.app_id,
            revision_digest=self.revision_digest,
        )


class ApplicationPublication(SemanticsModel):
    """The one mutable CURRENT selection for an application scope."""

    publication_schema_version: Literal["mozaiks.application_publication.v1"] = (
        "mozaiks.application_publication.v1"
    )
    scope: ExecutionAccessScopeRef
    app_id: str
    current_revision_ref: ArtifactRevisionRef | None
    generation: int = Field(ge=0, strict=True)

    @field_validator("app_id")
    @classmethod
    def _app_id(cls, value: str) -> str:
        return _validate_identifier(value, field_name="app_id")

    @model_validator(mode="after")
    def _scope(self) -> ApplicationPublication:
        if self.current_revision_ref is not None and (
            self.current_revision_ref.scope != self.scope
            or self.current_revision_ref.app_id != self.app_id
        ):
            raise ValueError("current revision must belong to the publication scope and app")
        return self


class PublicationOutcome(StrEnum):
    PROMOTED = "promoted"
    ALREADY_CURRENT = "already_current"


class PublicationResult(SemanticsModel):
    outcome: PublicationOutcome
    publication: ApplicationPublication


def _canonical_plan_for_evidence(
    plan: CompilationPlan,
    authority_inputs: CompilationPlanAuthorityInputs,
) -> CompilationPlan:
    """Evidence obligations exist only for canonically rederived plans.

    A candidate plan proves body integrity through its self-digest and
    nothing more; without rederivation a forged plan whose validators were
    rewritten to NONE would bless empty evidence. The canonical rederived
    plan is the only plan evidence obligations may be derived from.
    """

    if authority_inputs is None:
        raise PlanAuthorityError(
            PlanAuthorityMismatch.REQUIRED_AUTHORITY_MISSING,
            "validation evidence requires the plan's canonical authority inputs",
            plan_digest=plan.plan_digest,
        )
    return validate_compilation_plan_against_authority(plan, authority_inputs)


def build_artifact_revision_validation_evidence(
    *,
    scope: ExecutionAccessScopeRef,
    app_id: str,
    plan: CompilationPlan,
    authority_inputs: CompilationPlanAuthorityInputs,
    ledger: CompositionLedger,
    assignment_results: Sequence[AssignmentArtifactResult],
    bundle_validator_receipts: Sequence[ValidatorReceipt],
) -> ArtifactRevisionValidationEvidence:
    """Construct evidence only after its complete plan/ledger/result closure agrees."""

    canonical_plan = _canonical_plan_for_evidence(plan, authority_inputs)
    result_digests = _canonical_evidence_obligations(
        scope=scope,
        app_id=app_id,
        canonical_plan=canonical_plan,
        ledger=ledger,
        assignment_results=assignment_results,
        bundle_validator_receipts=bundle_validator_receipts,
    )
    payload: dict[str, Any] = {
        "evidence_schema_version": ("mozaiks.artifact_revision_validation_evidence.v1"),
        "scope": scope,
        "app_id": app_id,
        "compilation_plan_ref": ledger.compilation_plan_ref,
        "composition_ledger_digest": ledger.ledger_digest,
        "bundle_digest": ledger.bundle_digest,
        "assignment_result_digests": result_digests,
        "bundle_validator_receipts": tuple(bundle_validator_receipts),
    }
    return ArtifactRevisionValidationEvidence(
        **payload,
        evidence_digest=canonical_digest(
            ArtifactRevisionValidationEvidence.model_construct(
                **payload, evidence_digest="0" * 64
            ).model_dump(mode="json", exclude={"evidence_digest"})
        ),
    )


def validate_artifact_revision_validation_evidence(
    *,
    evidence: ArtifactRevisionValidationEvidence,
    plan: CompilationPlan,
    authority_inputs: CompilationPlanAuthorityInputs,
    ledger: CompositionLedger,
    assignment_results: Sequence[AssignmentArtifactResult],
) -> ArtifactRevisionValidationEvidence:
    canonical_plan = _canonical_plan_for_evidence(plan, authority_inputs)
    verified = ArtifactRevisionValidationEvidence.model_validate(evidence.model_dump(mode="json"))
    expected_result_digests = _canonical_evidence_obligations(
        scope=verified.scope,
        app_id=verified.app_id,
        canonical_plan=canonical_plan,
        ledger=ledger,
        assignment_results=assignment_results,
        bundle_validator_receipts=verified.bundle_validator_receipts,
    )
    if verified.assignment_result_digests != expected_result_digests:
        raise ValueError(
            "validation evidence assignment_result_digests do not match "
            "AGENT_AUTHOR ledger sources"
        )
    return cast(ArtifactRevisionValidationEvidence, verified)


def _canonical_evidence_obligations(
    *,
    scope: ExecutionAccessScopeRef,
    app_id: str,
    canonical_plan: CompilationPlan,
    ledger: CompositionLedger,
    assignment_results: Sequence[AssignmentArtifactResult],
    bundle_validator_receipts: Sequence[ValidatorReceipt],
) -> tuple[str, ...]:
    """Derive obligations from a plan that is already the canonical rederivation.

    Only ``_canonical_plan_for_evidence`` output may reach this helper; it is
    not a validation boundary and must never be handed a caller-supplied
    candidate plan.
    """

    del app_id  # structurally validated by the evidence/revision models
    verified_ledger = CompositionLedger.model_validate(ledger.model_dump(mode="json"))
    if canonical_plan.scope != scope or verified_ledger.compilation_plan_ref.scope != scope:
        raise ValueError("validation evidence closure crosses execution scope")
    expected_plan_ref = CompilationPlanRef(
        subject_id=canonical_plan.graph_id,
        subject_version=canonical_plan.graph_version,
        content_digest=canonical_plan.plan_digest,
        scope=canonical_plan.scope,
    )
    if verified_ledger.compilation_plan_ref != expected_plan_ref:
        raise ValueError("validation evidence ledger belongs to another CompilationPlan")
    canonical_units = {unit.unit_id: unit for unit in canonical_plan.units}
    entries_by_id = {
        entry.plan_unit_ref.unit_id: entry for entry in verified_ledger.unit_entries
    }
    if set(entries_by_id) != set(canonical_units):
        raise ValueError("ledger unit entries do not cover the canonical plan units")
    for unit_id, entry in entries_by_id.items():
        unit = canonical_units[unit_id]
        if entry.plan_unit_ref.unit_digest != unit.unit_digest:
            raise ValueError("ledger unit entry does not match the canonical unit identity")
        if entry.disposition is not unit.disposition:
            raise ValueError("ledger unit entry disposition does not match the canonical plan")

    expected_result_digests = tuple(
        sorted(
            entry.source_digest
            for entry in verified_ledger.unit_entries
            if entry.disposition is PlanDisposition.AGENT_AUTHOR
            and entry.outcome is CompositionOutcome.AGENT_AUTHORED
            and entry.source_digest is not None
        )
    )
    verified_results = tuple(
        AssignmentArtifactResult.model_validate(item.model_dump(mode="json"))
        for item in assignment_results
    )
    actual_result_digests = tuple(sorted(item.result_digest for item in verified_results))
    if len(actual_result_digests) != len(set(actual_result_digests)):
        raise ValueError("assignment results must be unique")
    if actual_result_digests != expected_result_digests:
        raise ValueError("assignment results do not exactly match AGENT_AUTHOR ledger sources")

    result_by_digest = {item.result_digest: item for item in verified_results}
    for entry in verified_ledger.unit_entries:
        if entry.source_digest not in result_by_digest:
            continue
        if result_by_digest[entry.source_digest].plan_unit_ref != entry.plan_unit_ref:
            raise ValueError("assignment result belongs to another plan unit")

    required_validators = tuple(
        sorted(
            {
                unit.validator
                for unit in canonical_plan.units
                if unit.validator is not ValidatorIdentifier.NONE
            },
            key=lambda item: item.value,
        )
    )
    receipts = tuple(sorted(bundle_validator_receipts, key=lambda item: item.validator.value))
    if tuple(item.validator for item in receipts) != required_validators:
        raise ValueError("bundle validator set does not match CompilationPlan requirements")
    if any(
        item.subject_digest != verified_ledger.bundle_digest or not item.passed for item in receipts
    ):
        raise ValueError("bundle validator receipt does not validate the exact bundle")
    return actual_result_digests


def build_artifact_revision(
    *,
    scope: ExecutionAccessScopeRef,
    app_id: str,
    parent_revision_ref: ArtifactRevisionRef | None,
    semantic_graph_ref: SemanticGraphRef,
    implementation_binding_ref: ImplementationBindingRef,
    compilation_plan_ref: CompilationPlanRef,
    compilation_plan_authority_ref: CompilationPlanAuthorityRef,
    composition_ledger_digest: str,
    bundle_digest: str,
    validation_evidence_digest: str,
) -> ArtifactRevision:
    verified_scope = ExecutionAccessScopeRef.model_validate(scope)
    verified_parent = (
        None
        if parent_revision_ref is None
        else ArtifactRevisionRef.model_validate(parent_revision_ref)
    )
    verified_graph_ref = SemanticGraphRef.model_validate(semantic_graph_ref)
    verified_binding_ref = ImplementationBindingRef.model_validate(implementation_binding_ref)
    verified_plan_ref = CompilationPlanRef.model_validate(compilation_plan_ref)
    verified_authority_ref = CompilationPlanAuthorityRef.model_validate(
        compilation_plan_authority_ref
    )
    if verified_authority_ref.scope != verified_scope:
        raise ValueError("plan-authority reference crosses execution scope")
    payload: dict[str, Any] = {
        "revision_schema_version": "mozaiks.artifact_revision.v1",
        "scope": verified_scope,
        "app_id": app_id,
        "parent_revision_ref": verified_parent,
        "semantic_graph_ref": verified_graph_ref,
        "implementation_binding_ref": verified_binding_ref,
        "compilation_plan_ref": verified_plan_ref,
        "compilation_plan_authority_ref": verified_authority_ref,
        "composition_ledger_digest": composition_ledger_digest,
        "bundle_digest": bundle_digest,
        "validation_evidence_digest": validation_evidence_digest,
    }
    raw = ArtifactRevision.model_construct(**payload, revision_digest="0" * 64)
    return ArtifactRevision(
        **payload,
        revision_digest=canonical_digest(raw.model_dump(mode="json", exclude={"revision_digest"})),
    )


__all__ = [
    "ApplicationPublication",
    "ArtifactRevision",
    "ArtifactRevisionValidationEvidence",
    "PublicationOutcome",
    "PublicationResult",
    "build_artifact_revision",
    "build_artifact_revision_validation_evidence",
    "validate_artifact_revision_validation_evidence",
]
