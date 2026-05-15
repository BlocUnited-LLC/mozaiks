from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field


def _utc_now() -> datetime:
    return datetime.now(UTC)


class ArtifactLifecycleStatus(str, Enum):
    DRAFT = "draft"
    CURRENT = "current"
    STALE = "stale"
    SUPERSEDED = "superseded"
    ARCHIVED = "archived"
    DELETED = "deleted"


class ArtifactValidationStatus(str, Enum):
    PENDING = "pending"
    PASSED = "passed"
    FAILED = "failed"
    SKIPPED = "skipped"


class ChangeClassification(str, Enum):
    PATCH = "patch"
    DESIGN = "design"
    FEATURE = "feature"
    CORE = "core"


class RefinementSessionStatus(str, Enum):
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
    sha256: Optional[str] = None
    size_bytes: Optional[int] = Field(default=None, ge=0)
    content_type: Optional[str] = None


class ArtifactCommitMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message: Optional[str] = None
    author_user_id: Optional[str] = None
    source_workflow: Optional[str] = None
    source_chat_id: Optional[str] = None
    trace_id: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class RefinementRequestPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_kind: str = "refinement"
    declared_change_class: Optional[ChangeClassification] = None
    artifact_kind: str
    artifact_key: Optional[str] = None
    artifact_version_id: Optional[str] = None
    raw_user_request: str = ""
    source_surface: Optional[str] = None
    app_id: Optional[str] = None
    requested_workflow_id: Optional[str] = None
    extra: Dict[str, Any] = Field(default_factory=dict)


class ChangeIntentDoc(BaseModel):
    model_config = ConfigDict(extra="forbid")

    change_class: ChangeClassification
    source: str = "declared"
    signals: List[str] = Field(default_factory=list)
    rationale: str
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    requires_concept_revision: bool = False
    touches_app_bundle: bool = False
    touches_workflow_bundle: bool = False
    touches_design_docs: bool = False
    touches_concept: bool = False


class ImpactSetDoc(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workflow_sequence: Optional[str] = None
    affected_workflows: List[str] = Field(default_factory=list)
    affected_bundle_paths: List[str] = Field(default_factory=list)
    affected_declarative_families: List[str] = Field(default_factory=list)
    requires_replanning: bool = False
    requires_rebuild: bool = True
    restart_from: Optional[str] = None
    scope_summary: str = ""


class ArtifactVersionDoc(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    id: str = Field(alias="_id")
    app_id: str
    artifact_kind: str
    artifact_key: str
    version_number: int = Field(ge=1)
    parent_version_id: Optional[str] = None
    lineage_root_id: str
    source_workflow: Optional[str] = None
    source_chat_id: Optional[str] = None
    canonical_inputs_version: Dict[str, str] = Field(default_factory=dict)
    lifecycle_status: ArtifactLifecycleStatus = ArtifactLifecycleStatus.DRAFT
    validation_status: ArtifactValidationStatus = ArtifactValidationStatus.PENDING
    invalidated_by_version_id: Optional[str] = None
    invalidation_reason: Optional[str] = None
    stale_at: Optional[datetime] = None
    files_manifest: List[ArtifactFileManifestEntry] = Field(default_factory=list)
    commit_metadata: ArtifactCommitMetadata = Field(default_factory=ArtifactCommitMetadata)
    created_at: datetime = Field(default_factory=_utc_now)
    updated_at: datetime = Field(default_factory=_utc_now)


class ChangeRequestDoc(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    id: str = Field(alias="_id")
    app_id: str
    artifact_kind: str
    artifact_key: str
    artifact_version_id: Optional[str] = None
    raw_user_request: str = ""
    classification: ChangeClassification
    refinement_request: RefinementRequestPayload
    change_intent: ChangeIntentDoc
    impact_set: ImpactSetDoc
    router_decision: Dict[str, Any] = Field(default_factory=dict)
    created_by_user_id: Optional[str] = None
    created_at: datetime = Field(default_factory=_utc_now)


class RefinementSessionDoc(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    id: str = Field(alias="_id")
    app_id: str
    artifact_version_id: str
    result_artifact_version_id: Optional[str] = None
    change_request_id: str
    provider: str = "e2b"
    sandbox_id: Optional[str] = None
    status: RefinementSessionStatus = RefinementSessionStatus.PENDING
    preview_url: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)
    started_at: datetime = Field(default_factory=_utc_now)
    ended_at: Optional[datetime] = None
