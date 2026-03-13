from __future__ import annotations

from enum import Enum
from typing import Any, Dict, Mapping, Optional, Type

from pydantic import BaseModel, ConfigDict, Field, field_validator

from mozaiksai.core.orchestration.change_classifier import ChangeIntent
from mozaiksai.core.ports.orchestration import DomainEvent


def _trim_required(value: Any, *, field_name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{field_name} must be non-empty")
    return text


def _normalize_string_list(values: list[str]) -> list[str]:
    normalized: list[str] = []
    for item in values:
        text = str(item or "").strip()
        if text:
            normalized.append(text)
    return normalized


class ControlPlaneState(str, Enum):
    INTAKE = "intake"
    PLANNING = "planning"
    APPROVAL_PENDING = "approval_pending"
    PREREQUISITES_PENDING = "prerequisites_pending"
    EXECUTING = "executing"
    REVIEW = "review"
    REROUTING = "rerouting"
    COMPLETED = "completed"
    FAILED = "failed"


class ControlPlaneEventKind(str, Enum):
    CANONICAL_STATE_CREATED = "control.canonical_state_created"
    CANONICAL_STATE_REVISED = "control.canonical_state_revised"
    PLAN_CREATED = "control.plan_created"
    PLAN_APPROVED = "control.plan_approved"
    PLAN_REJECTED = "control.plan_rejected"
    PREREQUISITES_REQUIRED = "control.prerequisites_required"
    PREREQUISITES_SATISFIED = "control.prerequisites_satisfied"
    PREREQUISITES_BLOCKED = "control.prerequisites_blocked"
    EXECUTION_BATCH_STARTED = "control.execution_batch_started"
    EXECUTION_BATCH_COMPLETED = "control.execution_batch_completed"
    EXECUTION_BATCH_FAILED = "control.execution_batch_failed"
    EXECUTION_COMPLETED = "control.execution_completed"
    EXECUTION_FAILED = "control.execution_failed"
    ARTIFACT_READY = "control.artifact_ready"
    FEEDBACK_RECEIVED = "control.feedback_received"
    IMPACT_COMPUTED = "control.impact_computed"
    TRANSFER_REQUESTED = "control.transfer_requested"
    ITERATION_STARTED = "control.iteration_started"


def _coerce_control_event_kind(kind: "ControlPlaneEventKind | str") -> "ControlPlaneEventKind":
    if isinstance(kind, ControlPlaneEventKind):
        return kind
    return ControlPlaneEventKind(str(kind))


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class WorkflowScopedPayload(_StrictModel):
    app_id: str
    workflow_name: str

    @field_validator("app_id", "workflow_name")
    @classmethod
    def _validate_required_fields(cls, value: Any, info):  # type: ignore[no-untyped-def]
        return _trim_required(value, field_name=info.field_name)


class IterationScopedPayload(WorkflowScopedPayload):
    iteration_id: str

    @field_validator("iteration_id")
    @classmethod
    def _validate_iteration_id(cls, value: Any) -> str:
        return _trim_required(value, field_name="iteration_id")


class PlanScopedPayload(WorkflowScopedPayload):
    plan_id: str

    @field_validator("plan_id")
    @classmethod
    def _validate_plan_id(cls, value: Any) -> str:
        return _trim_required(value, field_name="plan_id")


class CanonicalStateCreatedPayload(WorkflowScopedPayload):
    canonical_version: int = Field(ge=1)
    canonical_id: Optional[str] = None
    summary: str = ""

    @field_validator("canonical_id")
    @classmethod
    def _validate_canonical_id(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        return _trim_required(value, field_name="canonical_id")


class CanonicalStateRevisedPayload(WorkflowScopedPayload):
    canonical_version: int = Field(ge=1)
    previous_canonical_version: int = Field(ge=1)
    iteration_id: Optional[str] = None
    rationale: str = ""

    @field_validator("iteration_id")
    @classmethod
    def _validate_optional_iteration_id(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        return _trim_required(value, field_name="iteration_id")


class PlanCreatedPayload(PlanScopedPayload):
    canonical_version: int = Field(ge=1)
    summary: str = ""
    initial_batch_ids: list[str] = Field(default_factory=list)

    @field_validator("initial_batch_ids")
    @classmethod
    def _validate_initial_batch_ids(cls, value: list[str]) -> list[str]:
        return _normalize_string_list(value)


class PlanApprovedPayload(PlanScopedPayload):
    canonical_version: int = Field(ge=1)
    approval_source: str = "user"

    @field_validator("approval_source")
    @classmethod
    def _validate_approval_source(cls, value: Any) -> str:
        return _trim_required(value, field_name="approval_source")


class PlanRejectedPayload(PlanScopedPayload):
    reason: str

    @field_validator("reason")
    @classmethod
    def _validate_reason(cls, value: Any) -> str:
        return _trim_required(value, field_name="reason")


class PrerequisiteClass(str, Enum):
    REQUIRED_NOW = "required_now"
    REQUIRED_LATER = "required_later"
    OPTIONAL = "optional"
    NOT_REQUIRED = "not_required"


class PrerequisiteRequirement(_StrictModel):
    requirement_id: str
    key: str
    label: str
    category: str
    requirement_class: PrerequisiteClass

    @field_validator("requirement_id", "key", "label", "category")
    @classmethod
    def _validate_fields(cls, value: Any, info):  # type: ignore[no-untyped-def]
        return _trim_required(value, field_name=info.field_name)


class PrerequisitesRequiredPayload(PlanScopedPayload):
    requirements: list[PrerequisiteRequirement]
    iteration_id: Optional[str] = None

    @field_validator("iteration_id")
    @classmethod
    def _validate_optional_iteration_id(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        return _trim_required(value, field_name="iteration_id")


class PrerequisitesSatisfiedPayload(PlanScopedPayload):
    satisfied_requirement_ids: list[str] = Field(default_factory=list)

    @field_validator("satisfied_requirement_ids")
    @classmethod
    def _validate_satisfied_requirement_ids(cls, value: list[str]) -> list[str]:
        return _normalize_string_list(value)


class PrerequisitesBlockedPayload(PlanScopedPayload):
    missing_requirement_ids: list[str] = Field(default_factory=list)
    reason: str = ""

    @field_validator("missing_requirement_ids")
    @classmethod
    def _validate_missing_requirement_ids(cls, value: list[str]) -> list[str]:
        return _normalize_string_list(value)


class ExecutionBatchStartedPayload(IterationScopedPayload):
    batch_index: int = Field(ge=1)
    batch_ids: list[str] = Field(default_factory=list)

    @field_validator("batch_ids")
    @classmethod
    def _validate_batch_ids(cls, value: list[str]) -> list[str]:
        return _normalize_string_list(value)


class ExecutionBatchCompletedPayload(ExecutionBatchStartedPayload):
    completed_batch_ids: list[str] = Field(default_factory=list)
    failed_batch_ids: list[str] = Field(default_factory=list)

    @field_validator("completed_batch_ids", "failed_batch_ids")
    @classmethod
    def _validate_result_ids(cls, value: list[str]) -> list[str]:
        return _normalize_string_list(value)


class ExecutionBatchFailedPayload(ExecutionBatchStartedPayload):
    failed_batch_ids: list[str]
    error: str = ""

    @field_validator("failed_batch_ids")
    @classmethod
    def _validate_failed_ids(cls, value: list[str]) -> list[str]:
        return _normalize_string_list(value)


class ExecutionCompletedPayload(IterationScopedPayload):
    total_batches: int = Field(ge=0)


class ExecutionFailedPayload(IterationScopedPayload):
    error: str

    @field_validator("error")
    @classmethod
    def _validate_error(cls, value: Any) -> str:
        return _trim_required(value, field_name="error")


class ArtifactReadyPayload(IterationScopedPayload):
    artifact_type: str
    artifact_ref: Optional[str] = None

    @field_validator("artifact_type")
    @classmethod
    def _validate_artifact_type(cls, value: Any) -> str:
        return _trim_required(value, field_name="artifact_type")

    @field_validator("artifact_ref")
    @classmethod
    def _validate_artifact_ref(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        text = str(value).strip()
        return text or None


class FeedbackReceivedPayload(IterationScopedPayload):
    feedback_text: str

    @field_validator("feedback_text")
    @classmethod
    def _validate_feedback_text(cls, value: Any) -> str:
        return _trim_required(value, field_name="feedback_text")


class ImpactComputedPayload(IterationScopedPayload):
    scope: str
    affected_targets: list[str] = Field(default_factory=list)
    read_refs: list[str] = Field(default_factory=list)
    write_refs: list[str] = Field(default_factory=list)

    @field_validator("scope")
    @classmethod
    def _validate_scope(cls, value: Any) -> str:
        return _trim_required(value, field_name="scope")

    @field_validator("affected_targets", "read_refs", "write_refs")
    @classmethod
    def _validate_lists(cls, value: list[str]) -> list[str]:
        return _normalize_string_list(value)


class TransferRequestedPayload(_StrictModel):
    app_id: str
    from_workflow: str
    target_workflow: str
    transfer_mode: str
    change_intent: ChangeIntent
    rationale: str = ""

    @field_validator("app_id", "from_workflow", "target_workflow", "transfer_mode")
    @classmethod
    def _validate_transfer_fields(cls, value: Any, info):  # type: ignore[no-untyped-def]
        return _trim_required(value, field_name=info.field_name)


class IterationStartedPayload(IterationScopedPayload):
    canonical_version: int = Field(ge=1)
    previous_iteration_id: Optional[str] = None

    @field_validator("previous_iteration_id")
    @classmethod
    def _validate_previous_iteration_id(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        return _trim_required(value, field_name="previous_iteration_id")


ControlPlanePayloadModel = (
    CanonicalStateCreatedPayload
    | CanonicalStateRevisedPayload
    | PlanCreatedPayload
    | PlanApprovedPayload
    | PlanRejectedPayload
    | PrerequisitesRequiredPayload
    | PrerequisitesSatisfiedPayload
    | PrerequisitesBlockedPayload
    | ExecutionBatchStartedPayload
    | ExecutionBatchCompletedPayload
    | ExecutionBatchFailedPayload
    | ExecutionCompletedPayload
    | ExecutionFailedPayload
    | ArtifactReadyPayload
    | FeedbackReceivedPayload
    | ImpactComputedPayload
    | TransferRequestedPayload
    | IterationStartedPayload
)


_PAYLOAD_MODEL_BY_KIND: Dict[ControlPlaneEventKind, Type[_StrictModel]] = {
    ControlPlaneEventKind.CANONICAL_STATE_CREATED: CanonicalStateCreatedPayload,
    ControlPlaneEventKind.CANONICAL_STATE_REVISED: CanonicalStateRevisedPayload,
    ControlPlaneEventKind.PLAN_CREATED: PlanCreatedPayload,
    ControlPlaneEventKind.PLAN_APPROVED: PlanApprovedPayload,
    ControlPlaneEventKind.PLAN_REJECTED: PlanRejectedPayload,
    ControlPlaneEventKind.PREREQUISITES_REQUIRED: PrerequisitesRequiredPayload,
    ControlPlaneEventKind.PREREQUISITES_SATISFIED: PrerequisitesSatisfiedPayload,
    ControlPlaneEventKind.PREREQUISITES_BLOCKED: PrerequisitesBlockedPayload,
    ControlPlaneEventKind.EXECUTION_BATCH_STARTED: ExecutionBatchStartedPayload,
    ControlPlaneEventKind.EXECUTION_BATCH_COMPLETED: ExecutionBatchCompletedPayload,
    ControlPlaneEventKind.EXECUTION_BATCH_FAILED: ExecutionBatchFailedPayload,
    ControlPlaneEventKind.EXECUTION_COMPLETED: ExecutionCompletedPayload,
    ControlPlaneEventKind.EXECUTION_FAILED: ExecutionFailedPayload,
    ControlPlaneEventKind.ARTIFACT_READY: ArtifactReadyPayload,
    ControlPlaneEventKind.FEEDBACK_RECEIVED: FeedbackReceivedPayload,
    ControlPlaneEventKind.IMPACT_COMPUTED: ImpactComputedPayload,
    ControlPlaneEventKind.TRANSFER_REQUESTED: TransferRequestedPayload,
    ControlPlaneEventKind.ITERATION_STARTED: IterationStartedPayload,
}


_STATE_BY_EVENT_KIND: Dict[ControlPlaneEventKind, ControlPlaneState] = {
    ControlPlaneEventKind.CANONICAL_STATE_CREATED: ControlPlaneState.PLANNING,
    ControlPlaneEventKind.CANONICAL_STATE_REVISED: ControlPlaneState.REROUTING,
    ControlPlaneEventKind.PLAN_CREATED: ControlPlaneState.APPROVAL_PENDING,
    ControlPlaneEventKind.PLAN_APPROVED: ControlPlaneState.APPROVAL_PENDING,
    ControlPlaneEventKind.PLAN_REJECTED: ControlPlaneState.APPROVAL_PENDING,
    ControlPlaneEventKind.PREREQUISITES_REQUIRED: ControlPlaneState.PREREQUISITES_PENDING,
    ControlPlaneEventKind.PREREQUISITES_SATISFIED: ControlPlaneState.EXECUTING,
    ControlPlaneEventKind.PREREQUISITES_BLOCKED: ControlPlaneState.PREREQUISITES_PENDING,
    ControlPlaneEventKind.EXECUTION_BATCH_STARTED: ControlPlaneState.EXECUTING,
    ControlPlaneEventKind.EXECUTION_BATCH_COMPLETED: ControlPlaneState.EXECUTING,
    ControlPlaneEventKind.EXECUTION_BATCH_FAILED: ControlPlaneState.EXECUTING,
    ControlPlaneEventKind.EXECUTION_COMPLETED: ControlPlaneState.COMPLETED,
    ControlPlaneEventKind.EXECUTION_FAILED: ControlPlaneState.FAILED,
    ControlPlaneEventKind.ARTIFACT_READY: ControlPlaneState.REVIEW,
    ControlPlaneEventKind.FEEDBACK_RECEIVED: ControlPlaneState.REVIEW,
    ControlPlaneEventKind.IMPACT_COMPUTED: ControlPlaneState.REVIEW,
    ControlPlaneEventKind.TRANSFER_REQUESTED: ControlPlaneState.REROUTING,
    ControlPlaneEventKind.ITERATION_STARTED: ControlPlaneState.REROUTING,
}


def validate_control_plane_payload(
    kind: ControlPlaneEventKind | str,
    payload: Mapping[str, Any] | BaseModel,
) -> ControlPlanePayloadModel:
    event_kind = _coerce_control_event_kind(kind)
    model = _PAYLOAD_MODEL_BY_KIND[event_kind]
    raw_payload = payload.model_dump(mode="json") if isinstance(payload, BaseModel) else dict(payload)
    return model.model_validate(raw_payload)


def build_control_plane_event(
    *,
    kind: ControlPlaneEventKind | str,
    payload: Mapping[str, Any] | BaseModel,
    chat_id: str,
    source: str = "control_plane",
) -> DomainEvent:
    validated_payload = validate_control_plane_payload(kind, payload)
    event_kind = _coerce_control_event_kind(kind)
    return DomainEvent(
        kind=event_kind.value,
        payload=validated_payload.model_dump(mode="json"),
        chat_id=_trim_required(chat_id, field_name="chat_id"),
        source=_trim_required(source, field_name="source"),
    )


def parse_control_plane_event(event: DomainEvent) -> ControlPlanePayloadModel:
    return validate_control_plane_payload(event.kind, event.payload)


def infer_control_plane_state(
    event_or_kind: DomainEvent | ControlPlaneEventKind | str,
) -> ControlPlaneState:
    if isinstance(event_or_kind, DomainEvent):
        event_kind = _coerce_control_event_kind(event_or_kind.kind)
    else:
        event_kind = _coerce_control_event_kind(event_or_kind)
    return _STATE_BY_EVENT_KIND[event_kind]


__all__ = [
    "ControlPlaneState",
    "ControlPlaneEventKind",
    "PrerequisiteClass",
    "PrerequisiteRequirement",
    "CanonicalStateCreatedPayload",
    "CanonicalStateRevisedPayload",
    "PlanCreatedPayload",
    "PlanApprovedPayload",
    "PlanRejectedPayload",
    "PrerequisitesRequiredPayload",
    "PrerequisitesSatisfiedPayload",
    "PrerequisitesBlockedPayload",
    "ExecutionBatchStartedPayload",
    "ExecutionBatchCompletedPayload",
    "ExecutionBatchFailedPayload",
    "ExecutionCompletedPayload",
    "ExecutionFailedPayload",
    "ArtifactReadyPayload",
    "FeedbackReceivedPayload",
    "ImpactComputedPayload",
    "TransferRequestedPayload",
    "IterationStartedPayload",
    "validate_control_plane_payload",
    "build_control_plane_event",
    "parse_control_plane_event",
    "infer_control_plane_state",
]
