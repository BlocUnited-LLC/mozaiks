from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator


def _utc_now() -> datetime:
    return datetime.now(UTC)


class BuildRecordStatus(StrEnum):
    DRAFT = "draft"
    CURRENT = "current"
    STALE = "stale"
    SUPERSEDED = "superseded"
    ARCHIVED = "archived"
    DELETED = "deleted"


class BuildRecordValidationStatus(StrEnum):
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


class BuildRecordFileEntry(BaseModel):
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


# Module-internal alias
BuildRecordCommitMetadata = ArtifactCommitMetadata


class RefinementRequestPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_kind: str = "refinement"
    declared_change_class: ChangeClassification | None = None
    build_family: str
    build_key: str | None = None
    build_record_id: str | None = None
    raw_user_request: str = ""
    source_surface: str | None = None
    app_id: str | None = None
    requested_workflow_id: str | None = None
    extra: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def _remap_legacy_fields(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        remapped = dict(data)
        if "artifact_kind" in remapped and "build_family" not in remapped:
            remapped["build_family"] = remapped.pop("artifact_kind")
        else:
            remapped.pop("artifact_kind", None)
        if "artifact_key" in remapped and "build_key" not in remapped:
            remapped["build_key"] = remapped.pop("artifact_key")
        else:
            remapped.pop("artifact_key", None)
        if "artifact_version_id" in remapped and "build_record_id" not in remapped:
            remapped["build_record_id"] = remapped.pop("artifact_version_id")
        else:
            remapped.pop("artifact_version_id", None)
        return remapped

    # Prior-api attribute aliases
    @property
    def artifact_kind(self) -> str:
        return self.build_family

    @property
    def artifact_key(self) -> str | None:
        return self.build_key

    @property
    def artifact_version_id(self) -> str | None:
        return self.build_record_id


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


class BuildRecord(BaseModel):
    """Canonical build record model (versioned artifact document)."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    id: str = Field(alias="_id")
    app_id: str
    build_family: str
    build_key: str
    version_number: int = Field(ge=1)
    parent_build_record_id: str | None = None
    lineage_root_id: str
    source_workflow: str | None = None
    source_chat_id: str | None = None
    canonical_inputs_version: dict[str, str] = Field(default_factory=dict)
    lifecycle_status: BuildRecordStatus = BuildRecordStatus.DRAFT
    validation_status: BuildRecordValidationStatus = BuildRecordValidationStatus.PENDING
    invalidated_by_build_record_id: str | None = None
    invalidation_reason: str | None = None
    stale_at: datetime | None = None
    files_manifest: list[BuildRecordFileEntry] = Field(default_factory=list)
    commit_metadata: ArtifactCommitMetadata = Field(default_factory=ArtifactCommitMetadata)
    created_at: datetime = Field(default_factory=_utc_now)
    updated_at: datetime = Field(default_factory=_utc_now)

    @model_validator(mode="before")
    @classmethod
    def _remap_legacy_fields(cls, data: Any) -> Any:
        """Remap old ArtifactVersionDoc field names from stored MongoDB docs."""
        if not isinstance(data, dict):
            return data
        remapped = dict(data)
        # artifact_kind → build_family
        if "artifact_kind" in remapped and "build_family" not in remapped:
            remapped["build_family"] = remapped.pop("artifact_kind")
        else:
            remapped.pop("artifact_kind", None)
        # artifact_key → build_key
        if "artifact_key" in remapped and "build_key" not in remapped:
            remapped["build_key"] = remapped.pop("artifact_key")
        else:
            remapped.pop("artifact_key", None)
        # parent_version_id → parent_build_record_id
        if "parent_version_id" in remapped and "parent_build_record_id" not in remapped:
            remapped["parent_build_record_id"] = remapped.pop("parent_version_id")
        else:
            remapped.pop("parent_version_id", None)
        # invalidated_by_version_id → invalidated_by_build_record_id
        if "invalidated_by_version_id" in remapped and "invalidated_by_build_record_id" not in remapped:
            remapped["invalidated_by_build_record_id"] = remapped.pop("invalidated_by_version_id")
        else:
            remapped.pop("invalidated_by_version_id", None)
        return remapped

    # Prior-api attribute aliases (for callers that haven't been updated yet)
    @property
    def artifact_kind(self) -> str:
        return self.build_family

    @property
    def artifact_key(self) -> str:
        return self.build_key

    @property
    def parent_version_id(self) -> str | None:
        return self.parent_build_record_id

    @property
    def invalidated_by_version_id(self) -> str | None:
        return self.invalidated_by_build_record_id


class ChangeRequestDoc(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    id: str = Field(alias="_id")
    app_id: str
    build_family: str
    build_key: str
    build_record_id: str | None = None
    raw_user_request: str = ""
    classification: ChangeClassification
    refinement_request: RefinementRequestPayload
    change_intent: ChangeIntentDoc
    impact_set: ImpactSetDoc
    router_decision: dict[str, Any] = Field(default_factory=dict)
    created_by_user_id: str | None = None
    created_at: datetime = Field(default_factory=_utc_now)

    @model_validator(mode="before")
    @classmethod
    def _remap_legacy_fields(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        remapped = dict(data)
        if "artifact_kind" in remapped and "build_family" not in remapped:
            remapped["build_family"] = remapped.pop("artifact_kind")
        else:
            remapped.pop("artifact_kind", None)
        if "artifact_key" in remapped and "build_key" not in remapped:
            remapped["build_key"] = remapped.pop("artifact_key")
        else:
            remapped.pop("artifact_key", None)
        if "artifact_version_id" in remapped and "build_record_id" not in remapped:
            remapped["build_record_id"] = remapped.pop("artifact_version_id")
        else:
            remapped.pop("artifact_version_id", None)
        return remapped

    # Prior-api attribute aliases
    @property
    def artifact_kind(self) -> str:
        return self.build_family

    @property
    def artifact_key(self) -> str:
        return self.build_key

    @property
    def artifact_version_id(self) -> str | None:
        return self.build_record_id


class RefinementSessionDoc(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    id: str = Field(alias="_id")
    app_id: str
    build_record_id: str
    result_build_record_id: str | None = None
    change_request_id: str
    provider: str = "e2b"
    sandbox_id: str | None = None
    status: RefinementSessionStatus = RefinementSessionStatus.PENDING
    preview_url: str | None = None

    @model_validator(mode="before")
    @classmethod
    def _remap_legacy_fields(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        remapped = dict(data)
        if "artifact_version_id" in remapped and "build_record_id" not in remapped:
            remapped["build_record_id"] = remapped.pop("artifact_version_id")
        else:
            remapped.pop("artifact_version_id", None)
        if "result_artifact_version_id" in remapped and "result_build_record_id" not in remapped:
            remapped["result_build_record_id"] = remapped.pop("result_artifact_version_id")
        else:
            remapped.pop("result_artifact_version_id", None)
        return remapped

    metadata: dict[str, Any] = Field(default_factory=dict)
    started_at: datetime = Field(default_factory=_utc_now)
    ended_at: datetime | None = None

    # Prior-api attribute aliases
    @property
    def artifact_version_id(self) -> str:
        return self.build_record_id

    @property
    def result_artifact_version_id(self) -> str | None:
        return self.result_build_record_id


# Prior-api class aliases — kept so callers that import the old names still work
# during the migration period. Remove once all callers are updated.
ArtifactLifecycleStatus = BuildRecordStatus
ArtifactValidationStatus = BuildRecordValidationStatus
ArtifactFileManifestEntry = BuildRecordFileEntry
ArtifactVersionDoc = BuildRecord
