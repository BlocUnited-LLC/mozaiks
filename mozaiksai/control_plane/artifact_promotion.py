from __future__ import annotations

import hashlib
import logging
import mimetypes
import os
import re
import zipfile

logger = logging.getLogger(__name__)
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from mozaiksai.control_plane.dry_run import RefinementExecutionPlan
from mozaiksai.control_plane.promotion import RefinementPromotionResult
from mozaiksai.control_plane.promotion_policy import PromotionPolicyDecision
from mozaiksai.control_plane.review import (
    RefinementReviewRecord,
    load_refinement_review_record,
    redact_review_notes,
)
from mozaiksai.control_plane.scoped_execution import (
    EXECUTION_RESULT_FILENAME,
    ScopedRefinementResult,
)
from mozaiksai.control_plane.staging import WORKSPACE_DIRNAME, RefinementStagingResult
from mozaiksai.control_plane.validation_evidence import (
    ValidationEvidence,
    normalize_validation_evidence,
)
from mozaiksai.core.artifacts import (
    ArtifactContentStore,
    ArtifactFileManifestEntry,
    ArtifactLifecycleStatus,
    ArtifactStore,
    ArtifactValidationStatus,
    ArtifactVersionDoc,
    get_artifact_content_store,
    get_artifact_store,
)


class DraftAppBundleArtifactVersionResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: str
    app_id: str
    artifact_version_id: str
    artifact_kind: str
    artifact_key: str
    parent_version_id: str | None = None
    lifecycle_status: ArtifactLifecycleStatus
    validation_status: ArtifactValidationStatus
    artifact_path: str
    content_ref: str | None = None
    content_backend: str | None = None
    bundle_sha256: str
    bundle_size_bytes: int
    files_manifest: list[ArtifactFileManifestEntry] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    artifact_version: ArtifactVersionDoc


class DraftAppBundleArtifactVersionError(ValueError):
    """Raised when a staged refinement cannot be converted into a draft app bundle artifact version."""


class AcceptedStagedAppBundleArtifactVersionError(ValueError):
    """Raised when a staged refinement draft app bundle cannot be accepted."""


_SECRET_PATH_TERMS = (
    ".env",
    "secret",
    "secrets",
    "vault",
    "credential",
    "credentials",
    "private_key",
    "private-key",
    "id_rsa",
    "id_dsa",
    ".pem",
    ".key",
)

_GLOB_CHARS = ("*", "?", "[")


def _repo_root() -> Path:
    here = Path(__file__).resolve()
    for parent in [here] + list(here.parents):
        if (parent / "mozaiksai").is_dir():
            return parent
    return here.parents[-1]


def _resolve_generated_artifacts_root(base: Path | str | None = None) -> Path:
    if base is not None:
        candidate = Path(base)
    else:
        raw = os.getenv("MOZAIKS_GENERATED_ARTIFACTS_PATH", "generated").strip()
        candidate = Path(raw)
        if not candidate.is_absolute():
            candidate = _repo_root() / candidate
    if not candidate.is_absolute():
        candidate = (_repo_root() / candidate).resolve()
    return candidate.resolve()


def _safe_path_segment(value: Any, *, fallback: str) -> str:
    text = str(value or "").strip()
    if not text:
        text = fallback
    text = re.sub(r"[^A-Za-z0-9_.-]+", "-", text).strip(".-")
    return text or fallback


def _is_relative_to(child: Path, parent: Path) -> bool:
    try:
        child.relative_to(parent)
        return True
    except ValueError:
        return False


def _resolve_inside(parent: Path, child: Path) -> Path:
    parent_resolved = parent.resolve()
    child_resolved = child.resolve()
    if not _is_relative_to(child_resolved, parent_resolved):
        raise DraftAppBundleArtifactVersionError(f"Refusing to write outside allowed workspace: {child}")
    return child_resolved


def _contains_symlink_component(root: Path, relative_path: str) -> bool:
    if root.is_symlink():
        return True
    current = root
    for part in PurePosixPath(relative_path).parts:
        current = current / part
        if current.exists() and current.is_symlink():
            return True
    return False


def _normalize_relative_path(path: str) -> tuple[str | None, str | None]:
    raw = str(path or "").strip()
    if not raw:
        return None, "Empty path."
    if "\x00" in raw:
        return None, "Path contains a null byte."
    if PureWindowsPath(raw).is_absolute() or PureWindowsPath(raw).drive:
        return None, "Absolute or drive-qualified paths are not allowed."
    if raw.startswith(("/", "\\", "//", "\\\\")) or PurePosixPath(raw).is_absolute():
        return None, "Absolute paths are not allowed."

    normalized = raw.replace("\\", "/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    parts = [part for part in PurePosixPath(normalized).parts if part not in ("", ".")]
    if not parts or any(part == ".." for part in parts):
        return None, "Path traversal is not allowed."

    relative_path = "/".join(parts)
    lowered = relative_path.lower()
    if any(term in lowered for term in _SECRET_PATH_TERMS):
        return relative_path, "Secret-sensitive paths are not bundled."
    if any(char in relative_path for char in _GLOB_CHARS):
        return relative_path, "Glob paths are not bundled."
    return relative_path, None


def _validation_status_from_evidence(evidence: ValidationEvidence) -> ArtifactValidationStatus:
    if list(evidence.failed or []):
        return ArtifactValidationStatus.FAILED
    if list(evidence.completed or []):
        return ArtifactValidationStatus.PASSED
    return ArtifactValidationStatus.PENDING


def _normalized_policy_decisions(
    promotion_result: RefinementPromotionResult | None = None,
    policy_decisions: Sequence[PromotionPolicyDecision | Mapping[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    raw_decisions: Sequence[PromotionPolicyDecision | Mapping[str, Any]] = (
        list(promotion_result.policy_decisions)
        if promotion_result is not None
        else list(policy_decisions or [])
    )
    normalized: list[dict[str, Any]] = []
    for decision in raw_decisions:
        if isinstance(decision, PromotionPolicyDecision):
            normalized.append(decision.model_dump(mode="json"))
        elif isinstance(decision, Mapping):
            normalized.append(dict(decision))
    return normalized


def _commit_metadata_document(commit_metadata: Any) -> dict[str, Any]:
    if hasattr(commit_metadata, "model_dump"):
        payload = commit_metadata.model_dump(mode="python")  # type: ignore[call-arg]
        if isinstance(payload, dict):
            return dict(payload)
        return {}
    if isinstance(commit_metadata, Mapping):
        return dict(commit_metadata)
    return {}


def _build_review_snapshot(review_record: RefinementReviewRecord) -> dict[str, Any]:
    return {
        "status": review_record.status,
        "reviewer": review_record.reviewer,
        "reviewed_at": review_record.reviewed_at,
        "decision": review_record.decision,
        "notes": review_record.notes,
        "promotion_allowed": review_record.promotion_allowed,
        "source_bundle_path": review_record.source_bundle_path,
        "staging_area": review_record.staging_area,
        "affected_bundle_paths": list(review_record.affected_bundle_paths or []),
    }


def _build_scoped_execution_snapshot(scoped_result: ScopedRefinementResult) -> dict[str, Any]:
    return {
        "execution_mode": scoped_result.execution_mode,
        "mutation_scope": scoped_result.mutation_scope,
        "source_mutated": scoped_result.source_mutated,
        "mutation_allowed": scoped_result.mutation_allowed,
        "result_path": scoped_result.result_path,
        "changed_files": [item.model_dump(mode="json") for item in scoped_result.changed_files],
    }


def _load_execution_result(
    *,
    staging_area: Path,
    scoped_result: ScopedRefinementResult,
) -> ScopedRefinementResult:
    result_path = _resolve_inside(staging_area, Path(scoped_result.result_path))
    if result_path.name != EXECUTION_RESULT_FILENAME:
        raise DraftAppBundleArtifactVersionError("Scoped result path must point to execution_result.json.")
    if not result_path.exists():
        raise DraftAppBundleArtifactVersionError(f"Execution result not found: {result_path}")
    disk_result = ScopedRefinementResult.model_validate_json(result_path.read_text(encoding="utf-8"))
    if disk_result.model_dump(mode="json") != scoped_result.model_dump(mode="json"):
        raise DraftAppBundleArtifactVersionError("Execution result on disk does not match the provided scoped result.")
    return disk_result


def _load_review_record(
    *,
    staging_area: Path,
    review_record: RefinementReviewRecord,
) -> RefinementReviewRecord:
    disk_record = load_refinement_review_record(staging_area)
    if disk_record.model_dump(mode="json") != review_record.model_dump(mode="json"):
        raise DraftAppBundleArtifactVersionError("Review record on disk does not match the provided review record.")
    return disk_record


async def _resolve_source_artifact_version(
    *,
    artifact_store: ArtifactStore,
    app_id: str,
    source_artifact_version_id: str | None,
    artifact_key: str,
) -> ArtifactVersionDoc | None:
    resolved_source_id = str(source_artifact_version_id or "").strip()
    if resolved_source_id:
        source_version = await artifact_store.get_artifact_version(app_id=app_id, artifact_version_id=resolved_source_id)
        return source_version
    versions = await artifact_store.list_artifact_versions(
        app_id=app_id,
        artifact_kind="app_bundle",
        artifact_key=artifact_key,
        lifecycle_status=ArtifactLifecycleStatus.CURRENT,
        limit=1,
    )
    return versions[0] if versions else None


def _workspace_entries(workspace_root: Path) -> list[tuple[str, Path]]:
    entries: list[tuple[str, Path]] = []
    for root, dirs, files in os.walk(workspace_root, followlinks=False):
        root_path = Path(root)
        dirs[:] = [name for name in dirs if not (root_path / name).is_symlink()]
        for filename in files:
            file_path = root_path / filename
            if file_path.is_symlink():
                continue
            try:
                relative_path = file_path.resolve().relative_to(workspace_root.resolve()).as_posix()
            except Exception:
                continue
            entries.append((relative_path, file_path))
    entries.sort(key=lambda item: item[0])
    return entries


def _bundle_workspace(
    *,
    workspace_root: Path,
    bundle_path: Path,
) -> tuple[list[ArtifactFileManifestEntry], str, int]:
    bundle_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_entries: list[ArtifactFileManifestEntry] = []
    with zipfile.ZipFile(bundle_path, "w", zipfile.ZIP_DEFLATED) as zipf:
        for relative_path, file_path in _workspace_entries(workspace_root):
            normalized_path, skip_reason = _normalize_relative_path(relative_path)
            if normalized_path is None or skip_reason is not None:
                continue
            if file_path.is_symlink() or _contains_symlink_component(workspace_root, normalized_path):
                continue
            if not file_path.exists() or not file_path.is_file():
                continue
            raw = file_path.read_bytes()
            zipf.writestr(normalized_path, raw)
            manifest_entries.append(
                ArtifactFileManifestEntry(
                    path=normalized_path,
                    sha256=hashlib.sha256(raw).hexdigest(),
                    size_bytes=len(raw),
                    content_type=mimetypes.guess_type(normalized_path)[0],
                )
            )

    if not manifest_entries:
        raise DraftAppBundleArtifactVersionError("Staged workspace has no promotable files.")

    zip_bytes = bundle_path.read_bytes()
    return manifest_entries, hashlib.sha256(zip_bytes).hexdigest(), len(zip_bytes)


def _build_refinement_metadata(
    *,
    plan: RefinementExecutionPlan,
    staging_result: RefinementStagingResult,
    review_record: RefinementReviewRecord,
    scoped_result: ScopedRefinementResult,
    validation_evidence: ValidationEvidence,
    policy_decisions: list[dict[str, Any]],
    source_artifact_version_id: str | None,
    canonical_inputs_version: Mapping[str, str],
    bundle_path: Path,
    workspace_dir: Path,
    bundle_sha256: str,
    bundle_size_bytes: int,
    content_ref: str | None,
    content_backend: str | None,
    app_id: str,
    files_manifest: list[ArtifactFileManifestEntry],
) -> dict[str, Any]:
    return {
        "artifact_path": bundle_path.as_posix(),
        "workspace_dir": workspace_dir.as_posix(),
        "bundle_mode": "staged_refinement_workspace_snapshot",
        "bundle_sha256": bundle_sha256,
        "bundle_size_bytes": bundle_size_bytes,
        "content_ref": content_ref,
        "content_backend": content_backend,
        "refinement": {
            "request_id": plan.request_id,
            "app_id": app_id,
            "change_class": plan.change_class,
            "refinement_lane": plan.refinement_lane,
            "workflow_sequence": plan.workflow_sequence,
            "target_workflow": plan.target_workflow,
            "affected_declarative_families": list(plan.affected_declarative_families or []),
            "affected_bundle_paths": list(plan.affected_bundle_paths or []),
            "staging_area": staging_result.staging_area,
            "source_bundle_path": staging_result.source_bundle_path,
            "source_artifact_version_id": source_artifact_version_id,
            "canonical_inputs_version": dict(canonical_inputs_version or {}),
            "validation_evidence": validation_evidence.model_dump(mode="json"),
            "review": _build_review_snapshot(review_record),
            "scoped_execution": _build_scoped_execution_snapshot(scoped_result),
            "promotion_policy": {"policy_decisions": policy_decisions},
            "bundle": {
                "entry_count": len(files_manifest),
                "manifest_paths": [entry.path for entry in files_manifest],
            },
        },
    }


def _commit_metadata_payload(commit_metadata: Any) -> dict[str, Any]:
    payload = _commit_metadata_document(commit_metadata)
    metadata = payload.get("metadata")
    if isinstance(metadata, dict):
        return dict(metadata)
    return {}


def _build_acceptance_commit_metadata(
    *,
    commit_metadata: Any,
    accepted_by: str | None,
    accepted_at: str,
    request_id: str,
    review_status: str,
    notes: str | None = None,
) -> dict[str, Any]:
    payload = _commit_metadata_document(commit_metadata)
    metadata = dict(payload.get("metadata") or {})
    acceptance: dict[str, Any] = {
        "accepted_by": accepted_by,
        "accepted_at": accepted_at,
        "refinement_review_status": review_status,
        "request_id": request_id,
    }
    redacted_notes = redact_review_notes(notes)
    if redacted_notes is not None:
        acceptance["notes"] = redacted_notes
    metadata["acceptance"] = acceptance
    payload["metadata"] = metadata
    return payload


async def create_draft_app_bundle_from_staged_refinement(
    plan: RefinementExecutionPlan,
    staging_result: RefinementStagingResult,
    scoped_result: ScopedRefinementResult,
    review_record: RefinementReviewRecord,
    validation_evidence: ValidationEvidence | dict[str, Any] | list[str] | None,
    *,
    artifact_store: ArtifactStore | None = None,
    content_store: ArtifactContentStore | None = None,
    source_artifact_version_id: str | None = None,
    canonical_inputs_version: Mapping[str, str] | None = None,
    app_id: str | None = None,
    artifact_key: str = "app_bundle",
    generated_artifacts_root: Path | str | None = None,
    promotion_result: RefinementPromotionResult | None = None,
    policy_decisions: Sequence[PromotionPolicyDecision | Mapping[str, Any]] | None = None,
) -> DraftAppBundleArtifactVersionResult:
    if str(plan.artifact_kind or "").strip() != "app_bundle":
        raise DraftAppBundleArtifactVersionError("Draft app bundle creation only supports artifact_kind='app_bundle'.")
    if plan.execution_mode != "staged":
        raise DraftAppBundleArtifactVersionError("Draft app bundle creation requires a staged execution plan.")
    if plan.mutation_allowed is not False or staging_result.mutation_allowed is not False:
        raise DraftAppBundleArtifactVersionError("Draft app bundle creation requires mutation_allowed=false on plan and staging result.")
    if scoped_result.mutation_allowed is not False or scoped_result.source_mutated is not False:
        raise DraftAppBundleArtifactVersionError("Draft app bundle creation requires a non-mutating scoped execution result.")
    if scoped_result.execution_mode != "scoped_staging" or scoped_result.mutation_scope != "staging_only":
        raise DraftAppBundleArtifactVersionError("Draft app bundle creation requires a scoped staging execution result.")

    if plan.request_id != staging_result.request_id or plan.request_id != review_record.request_id or plan.request_id != scoped_result.request_id:
        raise DraftAppBundleArtifactVersionError("Plan, staging, review, and scoped execution request_id values must match.")

    staging_area = Path(staging_result.staging_area)
    if not staging_area.exists() or not staging_area.is_dir():
        raise DraftAppBundleArtifactVersionError(f"Staging area does not exist: {staging_area}")
    if staging_area.is_symlink():
        raise DraftAppBundleArtifactVersionError("Staging area cannot be a symlink.")
    staging_area = staging_area.resolve()

    workspace_area = _resolve_inside(staging_area, staging_area / WORKSPACE_DIRNAME)
    if not workspace_area.exists() or not workspace_area.is_dir():
        raise DraftAppBundleArtifactVersionError(f"Staging workspace does not exist: {workspace_area}")
    if workspace_area.is_symlink():
        raise DraftAppBundleArtifactVersionError("Staging workspace cannot be a symlink.")

    loaded_review = _load_review_record(staging_area=staging_area, review_record=review_record)
    loaded_execution = _load_execution_result(staging_area=staging_area, scoped_result=scoped_result)

    resolved_source_bundle_path = str(staging_result.source_bundle_path or "").strip() or None
    if resolved_source_bundle_path is not None:
        source_root = Path(resolved_source_bundle_path)
        if not source_root.exists() or not source_root.is_dir():
            raise DraftAppBundleArtifactVersionError(f"Source bundle path does not exist or is not a directory: {source_root}")
        if source_root.is_symlink():
            raise DraftAppBundleArtifactVersionError("Source bundle path cannot be a symlink.")

    resolved_app_id = str(app_id or plan.app_id or "").strip()
    if not resolved_app_id:
        raise DraftAppBundleArtifactVersionError("app_id is required to create a draft artifact version.")

    artifact_store = artifact_store or get_artifact_store()
    source_version = await _resolve_source_artifact_version(
        artifact_store=artifact_store,
        app_id=resolved_app_id,
        source_artifact_version_id=source_artifact_version_id,
        artifact_key=artifact_key,
    )
    if source_artifact_version_id and source_version is None:
        raise DraftAppBundleArtifactVersionError(
            f"Source artifact version not found: {source_artifact_version_id}"
        )
    if source_version is not None and source_version.artifact_kind != "app_bundle":
        raise DraftAppBundleArtifactVersionError(
            f"Source artifact version must be app_bundle; received {source_version.artifact_kind!r}."
        )

    resolved_source_artifact_version_id = (
        str(source_artifact_version_id or "").strip()
        or (source_version.id if source_version is not None else None)
    )
    resolved_canonical_inputs_version = dict(canonical_inputs_version or {})
    if source_version is not None:
        base_inputs = dict(source_version.canonical_inputs_version or {})
        base_inputs.update(resolved_canonical_inputs_version)
        resolved_canonical_inputs_version = base_inputs

    bundle_root = _resolve_generated_artifacts_root(generated_artifacts_root)
    safe_app_id = _safe_path_segment(resolved_app_id, fallback="local-app")
    safe_request_id = _safe_path_segment(plan.request_id, fallback="refinement")
    artifact_dir = bundle_root / "apps" / safe_app_id / "refinements" / safe_request_id / "app"
    artifact_dir.mkdir(parents=True, exist_ok=True)
    artifact_path = artifact_dir / "artifact.zip"

    files_manifest, bundle_sha256, bundle_size_bytes = _bundle_workspace(
        workspace_root=workspace_area,
        bundle_path=artifact_path,
    )

    validation = normalize_validation_evidence(validation_evidence)
    validation_status = _validation_status_from_evidence(validation)
    policy = _normalized_policy_decisions(promotion_result=promotion_result, policy_decisions=policy_decisions)
    source_workflow = str(plan.target_workflow or plan.workflow_sequence or "refinement_control_plane").strip()

    metadata = _build_refinement_metadata(
        plan=plan,
        staging_result=staging_result,
        review_record=loaded_review,
        scoped_result=loaded_execution,
        validation_evidence=validation,
        policy_decisions=policy,
        source_artifact_version_id=resolved_source_artifact_version_id,
        canonical_inputs_version=resolved_canonical_inputs_version,
        bundle_path=artifact_path,
        workspace_dir=workspace_area,
        bundle_sha256=bundle_sha256,
        bundle_size_bytes=bundle_size_bytes,
        content_ref=None,
        content_backend=None,
        app_id=resolved_app_id,
        files_manifest=files_manifest,
    )

    artifact_version = await artifact_store.create_artifact_version(
        app_id=resolved_app_id,
        artifact_kind="app_bundle",
        artifact_key=artifact_key,
        parent_version_id=resolved_source_artifact_version_id,
        canonical_inputs_version=resolved_canonical_inputs_version,
        files_manifest=[entry.model_dump(mode="python") for entry in files_manifest],
        source_workflow=source_workflow or None,
        source_chat_id=None,
        lifecycle_status=ArtifactLifecycleStatus.DRAFT,
        validation_status=validation_status,
        commit_metadata={
            "message": f"{source_workflow or 'refinement'}: app_bundle draft from staged refinement",
            "source_workflow": source_workflow or None,
            "metadata": metadata,
        },
    )

    content_store = content_store or get_artifact_content_store()
    content_ref = None
    content_backend = content_store.backend_name
    if content_backend != "local":
        try:
            content_ref = await content_store.put_bundle(
                artifact_path.read_bytes(),
                app_id=resolved_app_id,
                artifact_version_id=artifact_version.id,
            )
        except Exception as exc:
            logger.warning(
                "CONTENT_STORE_PUT_BUNDLE_FAILED app=%s artifact=%s backend=%s: %s",
                resolved_app_id,
                artifact_version.id,
                content_backend,
                exc,
                exc_info=True,
            )
            content_ref = None
            content_backend = None  # type: ignore[assignment]
        else:
            refreshed_metadata = _build_refinement_metadata(
                plan=plan,
                staging_result=staging_result,
                review_record=loaded_review,
                scoped_result=loaded_execution,
                validation_evidence=validation,
                policy_decisions=policy,
                source_artifact_version_id=resolved_source_artifact_version_id,
                canonical_inputs_version=resolved_canonical_inputs_version,
                bundle_path=artifact_path,
                workspace_dir=workspace_area,
                bundle_sha256=bundle_sha256,
                bundle_size_bytes=bundle_size_bytes,
                content_ref=content_ref,
                content_backend=content_store.backend_name,
                app_id=resolved_app_id,
                files_manifest=files_manifest,
            )
            await artifact_store.set_validation_status(
                app_id=resolved_app_id,
                artifact_version_id=artifact_version.id,
                validation_status=validation_status,
                commit_metadata={
                    "message": f"{source_workflow or 'refinement'}: app_bundle draft from staged refinement",
                    "source_workflow": source_workflow or None,
                    "metadata": refreshed_metadata,
                },
            )
            refreshed = await artifact_store.get_artifact_version(
                app_id=resolved_app_id,
                artifact_version_id=artifact_version.id,
            )
            if refreshed is not None:
                artifact_version = refreshed

    return DraftAppBundleArtifactVersionResult(
        request_id=plan.request_id,
        app_id=resolved_app_id,
        artifact_version_id=artifact_version.id,
        artifact_kind=artifact_version.artifact_kind,
        artifact_key=artifact_version.artifact_key,
        parent_version_id=artifact_version.parent_version_id,
        lifecycle_status=artifact_version.lifecycle_status,
        validation_status=artifact_version.validation_status,
        artifact_path=artifact_path.as_posix(),
        content_ref=content_ref,
        content_backend=content_backend if content_ref is not None else None,
        bundle_sha256=bundle_sha256,
        bundle_size_bytes=bundle_size_bytes,
        files_manifest=list(files_manifest),
        metadata=_commit_metadata_payload(artifact_version.commit_metadata),
        artifact_version=artifact_version,
    )


class AcceptedStagedAppBundleArtifactVersionResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: str
    app_id: str
    artifact_version_id: str
    artifact_kind: str
    artifact_key: str
    parent_version_id: str | None = None
    lifecycle_status: ArtifactLifecycleStatus
    validation_status: ArtifactValidationStatus
    accepted_by: str | None = None
    accepted_at: str
    refinement_review_status: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    artifact_version: ArtifactVersionDoc


async def accept_staged_refinement_artifact_version(
    *,
    app_id: str,
    draft_artifact_version_id: str,
    review_record: RefinementReviewRecord,
    request_id: str,
    artifact_store: ArtifactStore | None = None,
    accepted_by: str | None = None,
    notes: str | None = None,
) -> AcceptedStagedAppBundleArtifactVersionResult:
    resolved_app_id = str(app_id or "").strip()
    resolved_request_id = str(request_id or "").strip()
    if not resolved_app_id:
        raise AcceptedStagedAppBundleArtifactVersionError(
            "app_id is required to accept a staged refinement artifact version."
        )
    if not resolved_request_id:
        raise AcceptedStagedAppBundleArtifactVersionError(
            "request_id is required to accept a staged refinement artifact version."
        )

    artifact_store = artifact_store or get_artifact_store()
    artifact_version = await artifact_store.get_artifact_version(
        app_id=resolved_app_id,
        artifact_version_id=str(draft_artifact_version_id or "").strip(),
    )
    if artifact_version is None:
        raise AcceptedStagedAppBundleArtifactVersionError(
            f"Draft artifact version not found: {draft_artifact_version_id}"
        )
    if artifact_version.artifact_kind != "app_bundle":
        raise AcceptedStagedAppBundleArtifactVersionError(
            f"Staged refinement acceptance only supports artifact_kind='app_bundle'; received {artifact_version.artifact_kind!r}."
        )
    if artifact_version.lifecycle_status != ArtifactLifecycleStatus.DRAFT:
        raise AcceptedStagedAppBundleArtifactVersionError(
            f"Staged refinement acceptance requires a DRAFT artifact version; received {artifact_version.lifecycle_status.value!r}."
        )

    metadata_payload = _commit_metadata_payload(artifact_version.commit_metadata)
    refinement_metadata = metadata_payload.get("refinement")
    if not isinstance(refinement_metadata, dict):
        raise AcceptedStagedAppBundleArtifactVersionError(
            "Draft app bundle artifact version is missing staged refinement metadata."
        )

    refinement_request_id = str(refinement_metadata.get("request_id") or "").strip()
    if refinement_request_id != resolved_request_id:
        raise AcceptedStagedAppBundleArtifactVersionError(
            "request_id mismatch between the staged refinement metadata and the acceptance request."
        )

    review_snapshot = refinement_metadata.get("review")
    if not isinstance(review_snapshot, dict):
        raise AcceptedStagedAppBundleArtifactVersionError(
            "Draft app bundle artifact version is missing staged refinement review metadata."
        )

    staging_area = str(review_record.staging_area or "").strip()
    if not staging_area:
        raise AcceptedStagedAppBundleArtifactVersionError(
            "Staged refinement review record is missing a staging_area."
        )
    loaded_review = _load_review_record(staging_area=Path(staging_area), review_record=review_record)
    if loaded_review.request_id != resolved_request_id:
        raise AcceptedStagedAppBundleArtifactVersionError(
            "request_id mismatch between the provided review record and the acceptance request."
        )
    if loaded_review.status != "promotion_ready":
        raise AcceptedStagedAppBundleArtifactVersionError(
            "Staged refinement can only be accepted when the review status is promotion_ready."
        )
    if loaded_review.promotion_allowed is not True:
        raise AcceptedStagedAppBundleArtifactVersionError(
            "Staged refinement can only be accepted when promotion_allowed is true."
        )

    if str(review_snapshot.get("status") or "").strip() != "promotion_ready":
        raise AcceptedStagedAppBundleArtifactVersionError(
            "Draft artifact metadata does not reflect promotion_ready review status."
        )
    if review_snapshot.get("promotion_allowed") is not True:
        raise AcceptedStagedAppBundleArtifactVersionError(
            "Draft artifact metadata does not reflect promotion_allowed=true."
        )

    accepted_by_resolved = str(accepted_by or loaded_review.reviewer or review_record.reviewer or "").strip() or None
    accepted_at = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    commit_metadata_update = _build_acceptance_commit_metadata(
        commit_metadata=artifact_version.commit_metadata,
        accepted_by=accepted_by_resolved,
        accepted_at=accepted_at,
        request_id=resolved_request_id,
        review_status=loaded_review.status,
        notes=notes,
    )

    accepted_version = await artifact_store.accept_artifact_version(
        app_id=resolved_app_id,
        artifact_version_id=artifact_version.id,
        commit_metadata=commit_metadata_update,
    )
    if accepted_version is None:
        raise AcceptedStagedAppBundleArtifactVersionError(
            f"Draft artifact version could not be accepted: {draft_artifact_version_id}"
        )

    return AcceptedStagedAppBundleArtifactVersionResult(
        request_id=resolved_request_id,
        app_id=resolved_app_id,
        artifact_version_id=accepted_version.id,
        artifact_kind=accepted_version.artifact_kind,
        artifact_key=accepted_version.artifact_key,
        parent_version_id=accepted_version.parent_version_id,
        lifecycle_status=accepted_version.lifecycle_status,
        validation_status=accepted_version.validation_status,
        accepted_by=accepted_by_resolved,
        accepted_at=accepted_at,
        refinement_review_status=loaded_review.status,
        metadata=_commit_metadata_payload(accepted_version.commit_metadata),
        artifact_version=accepted_version,
    )


__all__ = [
    "AcceptedStagedAppBundleArtifactVersionError",
    "AcceptedStagedAppBundleArtifactVersionResult",
    "DraftAppBundleArtifactVersionError",
    "DraftAppBundleArtifactVersionResult",
    "accept_staged_refinement_artifact_version",
    "create_draft_app_bundle_from_staged_refinement",
]
