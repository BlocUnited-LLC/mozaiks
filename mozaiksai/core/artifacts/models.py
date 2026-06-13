from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


def _utc_now() -> datetime:
    return datetime.now(UTC)


class ArtifactLifecycleStatus(StrEnum):
    DRAFT = "draft"
    CURRENT = "current"
    STALE = "stale"
    SUPERSEDED = "superseded"
    ARCHIVED = "archived"
    DELETED = "deleted"


class ArtifactValidationStatus(StrEnum):
    PENDING = "pending"
    PASSED = "passed"
    FAILED = "failed"
    SKIPPED = "skipped"


class ChangeClassification(StrEnum):
    PATCH = "patch"
    DESIGN = "design"
    FEATURE = "feature"
    CORE = "core"


class RefinementSessionStatus(StrEnum):
    PENDING = "pending"
    PROVISIONING = "provisioning"
    RUNNING = "running"
    VALIDATED = "validated"
    COMMITTED = "committed"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    PROMOTED = "promoted"
    FAILED = "failed"
    TERMINATED = "terminated"


class ArtifactFileManifestEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str
    sha256: str | None = None
    size_bytes: int | None = Field(default=None, ge=0)
    content_type: str | None = None


class ArtifactCommitMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message: str | None = None
    author_user_id: str | None = None
    source_workflow: str | None = None
    source_chat_id: str | None = None
    trace_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class RefinementRequestPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_kind: str = "refinement"
    declared_change_class: ChangeClassification | None = None
    artifact_kind: str
    artifact_key: str | None = None
    artifact_version_id: str | None = None
    raw_user_request: str = ""
    source_surface: str | None = None
    app_id: str | None = None
    requested_workflow_id: str | None = None
    extra: dict[str, Any] = Field(default_factory=dict)


class ChangeIntentDoc(BaseModel):
    model_config = ConfigDict(extra="forbid")

    change_class: ChangeClassification
    source: str = "declared"
    signals: list[str] = Field(default_factory=list)
    rationale: str
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    requires_concept_revision: bool = False
    touches_app_bundle: bool = False
    touches_workflow_bundle: bool = False
    touches_design_docs: bool = False
    touches_concept: bool = False


class ImpactSetDoc(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workflow_sequence: str | None = None
    affected_workflows: list[str] = Field(default_factory=list)
    affected_bundle_paths: list[str] = Field(default_factory=list)
    affected_declarative_families: list[str] = Field(default_factory=list)
    requires_replanning: bool = False
    requires_rebuild: bool = True
    restart_from: str | None = None
    scope_summary: str = ""


class ArtifactVersionDoc(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    id: str = Field(alias="_id")
    app_id: str
    artifact_kind: str
    artifact_key: str
    version_number: int = Field(ge=1)
    parent_version_id: str | None = None
    lineage_root_id: str
    source_workflow: str | None = None
    source_chat_id: str | None = None
    canonical_inputs_version: dict[str, str] = Field(default_factory=dict)
    lifecycle_status: ArtifactLifecycleStatus = ArtifactLifecycleStatus.DRAFT
    validation_status: ArtifactValidationStatus = ArtifactValidationStatus.PENDING
    invalidated_by_version_id: str | None = None
    invalidation_reason: str | None = None
    stale_at: datetime | None = None
    files_manifest: list[ArtifactFileManifestEntry] = Field(default_factory=list)
    commit_metadata: ArtifactCommitMetadata = Field(default_factory=ArtifactCommitMetadata)
    created_at: datetime = Field(default_factory=_utc_now)
    updated_at: datetime = Field(default_factory=_utc_now)


class ChangeRequestDoc(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    id: str = Field(alias="_id")
    app_id: str
    artifact_kind: str
    artifact_key: str
    artifact_version_id: str | None = None
    raw_user_request: str = ""
    classification: ChangeClassification
    refinement_request: RefinementRequestPayload
    change_intent: ChangeIntentDoc
    impact_set: ImpactSetDoc
    router_decision: dict[str, Any] = Field(default_factory=dict)
    created_by_user_id: str | None = None
    created_at: datetime = Field(default_factory=_utc_now)


class RefinementSessionDoc(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    id: str = Field(alias="_id")
    app_id: str
    artifact_version_id: str
    result_artifact_version_id: str | None = None
    change_request_id: str
    provider: str = "e2b"
    sandbox_id: str | None = None
    status: RefinementSessionStatus = RefinementSessionStatus.PENDING
    preview_url: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    started_at: datetime = Field(default_factory=_utc_now)
    ended_at: datetime | None = None
