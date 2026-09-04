from __future__ import annotations

import re
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
    # Sandbox build-validation outcome, first-class and queryable (the full
    # trimmed result stays in commit_metadata.metadata["app_validation_result"]).
    app_validation_status: str | None = None
    app_validation_strategy: str | None = None
    sandbox_session_id: str | None = None
    sandbox_provider: str | None = None
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
        # parent_version_id -> parent_build_record_id
        if "parent_version_id" in remapped and "parent_build_record_id" not in remapped:
            remapped["parent_build_record_id"] = remapped.pop("parent_version_id")
        else:
            remapped.pop("parent_version_id", None)
        # invalidated_by_version_id -> invalidated_by_build_record_id
        if "invalidated_by_version_id" in remapped and "invalidated_by_build_record_id" not in remapped:
            remapped["invalidated_by_build_record_id"] = remapped.pop("invalidated_by_version_id")
        else:
            remapped.pop("invalidated_by_version_id", None)
        return remapped

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


# --------------------------------------------------------------------------
# Canonical app-bundle manifest entry identity
# --------------------------------------------------------------------------

# The canonical bundle archive entry is identified by its closed writer
# contract, never by digest equality: the app_bundle manifest writer stamps
# exactly one entry with this content type (the generated-file mapper can
# never produce it), at exactly the canonical archive path
# "{bundle_name}/{bundle_name}.zip" with bundle_name persisted in
# commit_metadata.metadata["bundle_name"].
CANONICAL_BUNDLE_CONTENT_TYPE = "application/zip"

CANONICAL_APP_BUNDLE_FAMILY = "app_bundle"
CANONICAL_APP_BUNDLE_KEY = "app_bundle"

_SHA256_HEX_RE = re.compile(r"^[0-9a-f]{64}$")

# The closed bundle-name grammar shared by the writer (name derivation) and
# the resolver (identity verification): ASCII letters, digits, hyphen,
# underscore only. This structurally excludes path separators, dot segments,
# traversal, absolute/drive-qualified paths, and control characters.
_CANONICAL_BUNDLE_NAME_RE = re.compile(r"^[A-Za-z0-9_-]+$")


class CanonicalBundleEntryError(ValueError):
    """The record does not identify exactly one canonical bundle entry.

    Raised when the record is not the canonical app_bundle record, when its
    persisted bundle name violates the closed grammar, and for zero
    candidates, multiple candidates, a missing/malformed digest, or a path
    that is not exactly the canonical archive path. Consumers treat this as
    "no verifiable bundle" and fail closed — in particular, a digest may
    never be validated against "any manifest entry with this digest": only
    the uniquely identified canonical entry's digest is bundle authority.
    """


def validate_canonical_bundle_name(bundle_name: Any) -> str:
    """Validate a bundle name against the closed shared grammar.

    Returns the exact name, or raises :class:`CanonicalBundleEntryError`.
    Never normalizes: an invalid name fails closed, it is not repaired.
    """
    name = str(bundle_name or "")
    if not _CANONICAL_BUNDLE_NAME_RE.match(name):
        raise CanonicalBundleEntryError(
            f"bundle name {name!r} violates the canonical bundle-name grammar "
            "(ASCII letters, digits, hyphen, underscore only)"
        )
    return name


def canonical_bundle_archive_path(bundle_name: str) -> str:
    """The one canonical archive path formula: ``{bundle_name}/{bundle_name}.zip``.

    Both the manifest writer and the resolver derive the archive identity
    from this helper, so writer and resolver cannot drift independently.
    """
    name = validate_canonical_bundle_name(bundle_name)
    return f"{name}/{name}.zip"


def resolve_canonical_bundle_entry(record: BuildRecord) -> BuildRecordFileEntry:
    """Resolve the single canonical bundle archive entry of an app_bundle record.

    Exact contract (closed writer identity — no heuristics, no basename
    inference, no suffix search, no digest membership search, no first-ZIP
    wins, no path normalization):

    1. the record itself is the canonical application-bundle record:
       ``build_family == build_key == "app_bundle"`` — another family/key is
       never accepted merely because its manifest looks bundle-shaped;
    2. the persisted ``commit_metadata.metadata["bundle_name"]`` exists and
       satisfies the closed shared bundle-name grammar;
    3. the manifest contains exactly one ``application/zip`` candidate;
    4. that candidate's path equals exactly
       ``{bundle_name}/{bundle_name}.zip``;
    5. the candidate's ``sha256`` is a lowercase 64-hex digest.

    Anything else raises :class:`CanonicalBundleEntryError` and can never
    become bundle-digest authority.
    """
    if (
        record.build_family != CANONICAL_APP_BUNDLE_FAMILY
        or record.build_key != CANONICAL_APP_BUNDLE_KEY
    ):
        raise CanonicalBundleEntryError(
            f"record {record.id!r} family/key "
            f"{record.build_family!r}/{record.build_key!r} is not the "
            "canonical app_bundle record"
        )

    bundle_name = str(record.commit_metadata.metadata.get("bundle_name") or "")
    if not bundle_name.strip():
        raise CanonicalBundleEntryError(
            f"record {record.id!r} carries no persisted bundle_name identity"
        )
    expected_path = canonical_bundle_archive_path(bundle_name)

    candidates = [
        entry
        for entry in record.files_manifest
        if entry.content_type == CANONICAL_BUNDLE_CONTENT_TYPE
    ]
    if not candidates:
        raise CanonicalBundleEntryError(
            f"record {record.id!r} manifest has no canonical bundle entry "
            f"(content_type={CANONICAL_BUNDLE_CONTENT_TYPE!r})"
        )
    if len(candidates) > 1:
        raise CanonicalBundleEntryError(
            f"record {record.id!r} manifest has {len(candidates)} canonical "
            "bundle entries; exactly one is required"
        )
    entry = candidates[0]

    if str(entry.path or "") != expected_path:
        raise CanonicalBundleEntryError(
            f"record {record.id!r} canonical bundle entry path {entry.path!r} "
            f"is not the exact canonical archive path {expected_path!r}"
        )

    digest = str(entry.sha256 or "").strip()
    if not _SHA256_HEX_RE.match(digest):
        raise CanonicalBundleEntryError(
            f"record {record.id!r} canonical bundle entry {entry.path!r} has "
            "no valid lowercase sha256 digest"
        )
    return entry


# Prior-api class aliases -- kept so callers that import the old names still work
# during the migration period. Remove once all callers are updated.
ArtifactLifecycleStatus = BuildRecordStatus
ArtifactValidationStatus = BuildRecordValidationStatus
ArtifactFileManifestEntry = BuildRecordFileEntry
ArtifactVersionDoc = BuildRecord
