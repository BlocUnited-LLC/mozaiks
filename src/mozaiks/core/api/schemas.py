from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from mozaiks.contracts import EventEnvelope


class WorkflowSummary(BaseModel):
    name: str
    version: str
    description: str
    tags: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class RunCreateRequest(BaseModel):
    workflow_name: str
    workflow_version: str
    input: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)
    app_id: str | None = None
    chat_id: str | None = None


class RunResumeRequest(BaseModel):
    checkpoint_key: str = "latest"
    metadata: dict[str, Any] = Field(default_factory=dict)
    app_id: str | None = None
    chat_id: str | None = None


class UIToolSubmissionRequest(BaseModel):
    submission_id: str = Field(..., min_length=1)
    payload: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)
    checkpoint_key: str = "latest"
    app_id: str | None = None
    chat_id: str | None = None


class PreviewRunCreateRequest(BaseModel):
    workflow_name: str = "preview.inline"
    workflow_version: str = "0.0.0"
    payload: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)
    app_id: str | None = None
    chat_id: str | None = None


class RunView(BaseModel):
    run_id: str
    created_at: datetime
    status: str
    workflow_name: str
    workflow_version: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class RunCreateResponse(RunView):
    result: dict[str, Any] | None = None


class RunResumeResponse(RunView):
    resumed_from: str
    checkpoint_restored: bool
    result: dict[str, Any] | None = None


class UIToolSubmissionResponse(RunView):
    ui_tool_id: str
    submission_id: str
    resumed_from: str
    outcome_event_type: str
    completion_state: str
    idempotent_replay: bool = False
    artifact_linkage: dict[str, Any] | None = None
    result: dict[str, Any] | None = None


class RunEventItem(BaseModel):
    seq: int
    event: EventEnvelope


class RunEventsPage(BaseModel):
    run_id: str
    events: list[RunEventItem] = Field(default_factory=list)
    next_after_seq: int | None = None


class PreviewRunCreateResponse(BaseModel):
    run_id: str
    preview_url: str
    status: str


class PreviewSecretUpsertRequest(BaseModel):
    key: str = Field(..., min_length=1)
    value: str


class PreviewRequiredSecretsResponse(BaseModel):
    run_id: str
    scope: str
    required_keys: list[str] = Field(default_factory=list)


class ArtifactView(BaseModel):
    artifact_id: str
    run_id: str
    seq: int | None = None
    artifact_type: str
    uri: str
    checksum: str
    version: str
    media_type: str | None = None
    content_base64: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime

    @classmethod
    def from_record(cls, record: Any) -> "ArtifactView":
        return cls(
            artifact_id=record.artifact_id,
            run_id=record.run_id,
            seq=record.seq,
            artifact_type=record.artifact_type,
            uri=record.uri,
            checksum=record.checksum,
            version=record.version,
            media_type=record.media_type,
            content_base64=record.content_base64,
            metadata=dict(record.metadata),
            created_at=record.created_at,
        )
