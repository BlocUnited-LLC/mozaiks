from __future__ import annotations

"""Studio management host layered on top of mozaiksai.hosts.platform.

Studio is the local/private management and create control plane used by the
CLI and by the hosted Mozaiks product. It adds Studio shell routes and
workflow triggering on top of the headless platform host.
"""

import stat
import zipfile
from asyncio import to_thread
from datetime import UTC, datetime
from difflib import unified_diff
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Literal
from uuid import uuid4

from fastapi import BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel, Field, ValidationError

from factory_app.app.modules.app_registry.backend.service import AppRegistryService
from mozaiksai.hosts.bootstrap import configure_repo_host_defaults

configure_repo_host_defaults("studio")

from logs.logging_config import get_workflow_logger
from mozaiksai.control_plane import (
    AcceptedStagedAppBundleBuildRecordError,
    SourceImportRequest,
    accept_staged_refinement_build_record,
    build_refinement_review_package,
    get_orchestration_control_harness,
    index_workspace_app_intelligence,
    resolve_source_import,
    run_current_app_source_validation,
    source_import_scan_policy,
)
from mozaiksai.control_plane.app_context import (
    get_current_app_context_graph,
    get_current_app_context_summary,
)
from mozaiksai.control_plane.app_context_override import (
    AppContextPolicyOverrideDecision,
    apply_app_context_policy_override,
    create_app_context_policy_override,
)
from mozaiksai.control_plane.app_context_policy import (
    AppContextPolicyDecision,
    AppContextPolicyResult,
)
from mozaiksai.control_plane.app_context_refresh import (
    build_context_refresh_plan,
    build_context_refresh_request,
)
from mozaiksai.control_plane.app_context_refresh_execution import (
    ContextRefreshLaunchResult,
    complete_context_refresh,
    launch_context_refresh_plan,
)
from mozaiksai.control_plane.app_intelligence_jobs import (
    AppIntelligenceIndexJob,
    advance_app_intelligence_index_job,
    complete_app_intelligence_index_job,
    create_app_intelligence_index_job,
    fail_app_intelligence_index_job,
    get_app_intelligence_index_job,
    get_latest_app_intelligence_index_job,
    public_app_intelligence_index_job,
    save_app_intelligence_index_job,
)
from mozaiksai.control_plane.dry_run import RefinementDryRunPlan, RefinementExecutionPlan
from mozaiksai.control_plane.review import load_refinement_review_record
from mozaiksai.core.app_context.models import SourceRef
from mozaiksai.core.app_context.refresh import ContextRefreshPlan, ContextRefreshScope
from mozaiksai.core.artifacts import (
    ArtifactLifecycleStatus,
    ArtifactValidationStatus,
    ChangeClassification,
    RefinementSessionStatus,
    get_artifact_store,
)
from mozaiksai.core.auth import UserPrincipal, require_user_scope
from mozaiksai.core.auth.dependencies import validate_path_id
from mozaiksai.core.dashboard import load_dashboard_manifest
from mozaiksai.core.data.persistence import ConnectorStore
from mozaiksai.core.runtime.app.studio_summary import (
    build_app_overview_summary,
    build_apps_summary,
    build_build_section,
    build_integrations_summary,
    get_missing_studio_surfaces,
    load_build_state_from_db,
    save_build_state_to_db,
)
from mozaiksai.core.session.launcher import launch_prepared_workflow, prepare_routed_workflow_launch
from mozaiksai.core.session.model import (
    PendingDecisionAction,
    PendingHarnessDecision,
    RoutingDecision,
    SessionLifecycle,
    TriggerInput,
)
from mozaiksai.core.session.router import configure_session_router, get_session_router
from mozaiksai.core.studio.scope import resolve_studio_scope
from mozaiksai.core.workflow.generator_support.connector_health import run_connector_health_check
from mozaiksai.core.workflow.generator_support.connector_service import (
    compute_connector_health,
    delete_connector,
    list_connectors,
    patch_connector,
    save_connector,
)
from mozaiksai.core.workflow.paths import candidate_app_workflows_roots
from mozaiksai.hosts import platform as platform_app
from mozaiksai.hosts.platform import (
    build_shell_config,
    resolve_app_root,
)

app = platform_app.app
logger = get_workflow_logger("studio_app")

_BUNDLE_MAX_TEXT_FILES = 200
_BUNDLE_MAX_TOTAL_BYTES = 2_000_000
_BUNDLE_MAX_SINGLE_FILE_BYTES = 250_000
_DIFF_PREVIEW_MAX_LINES = 120
_DIFF_PREVIEW_MAX_CHARS = 12_000
_RESTORE_SECRET_PATH_TERMS = (
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
_RESTORE_SKIP_FILENAMES = {
    "refinement_plan.json",
    "affected_paths.json",
    "refinement_review.json",
    "execution_result.json",
}


def _get_app_registry_service() -> AppRegistryService:
    return AppRegistryService()


def _normalize_bundle_entry_name(name: str) -> str | None:
    normalized = str(name or "").replace("\\", "/").strip("/")
    if not normalized or normalized.endswith("/"):
        return None
    parts = [part for part in normalized.split("/") if part]
    if any(part == ".." for part in parts):
        return None
    return "/".join(parts)


def _restore_entry_name(name: str) -> tuple[str | None, str | None]:
    raw = str(name or "").strip()
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
    if any(term in lowered for term in _RESTORE_SECRET_PATH_TERMS):
        return relative_path, "Secret-sensitive paths are not restored."
    basename = PurePosixPath(relative_path).name.lower()
    if (
        basename in _RESTORE_SKIP_FILENAMES
        or lowered.startswith("backups/")
        or "/backups/" in lowered
        or any(segment == "backups" for segment in PurePosixPath(relative_path).parts)
    ):
        return relative_path, "Staging metadata and backups are not restored."
    return relative_path, None


def _zipinfo_is_symlink(info: zipfile.ZipInfo) -> bool:
    mode = (info.external_attr >> 16) & 0o170000
    return mode == stat.S_IFLNK


def _contains_symlink_component(root: Path, relative_path: str) -> bool:
    if root.is_symlink():
        return True
    current = root
    for part in PurePosixPath(relative_path).parts:
        current = current / part
        if current.exists() and current.is_symlink():
            return True
    return False


def _strip_shared_root_prefix(entries: list[tuple[str, bytes]]) -> list[tuple[str, bytes]]:
    if not entries:
        return entries
    split_paths = [path.split("/") for path, _ in entries]
    if not split_paths or any(len(parts) < 2 for parts in split_paths):
        return entries
    first_segment = split_paths[0][0]
    if not all(parts[0] == first_segment for parts in split_paths):
        return entries
    return [("/".join(parts[1:]), content) for parts, (_, content) in zip(split_paths, entries, strict=False)]


def _strip_app_bundle_root_prefix(paths: list[str]) -> dict[str, str]:
    if not paths:
        return {}
    split_paths = [path.split("/") for path in paths]
    if not split_paths or any(len(parts) < 2 for parts in split_paths):
        return {path: path for path in paths}
    first_segment = split_paths[0][0]
    if not first_segment or not all(parts[0] == first_segment for parts in split_paths):
        return {path: path for path in paths}

    stripped = ["/".join(parts[1:]) for parts in split_paths]
    if "app.json" not in stripped:
        return {path: path for path in paths}
    return {path: stripped_path for path, stripped_path in zip(paths, stripped, strict=False)}


def _decode_text_bundle_entries(zip_path: Path) -> tuple[dict[str, str], list[str]]:
    files: dict[str, str] = {}
    skipped: list[str] = []
    total_bytes = 0
    entries: list[tuple[str, bytes]] = []

    with zipfile.ZipFile(zip_path, "r") as archive:
        for info in archive.infolist():
            safe_name = _normalize_bundle_entry_name(info.filename)
            if safe_name is None:
                continue
            if info.is_dir():
                continue
            if len(entries) >= _BUNDLE_MAX_TEXT_FILES:
                skipped.append(f"{safe_name}: file_limit")
                continue
            if info.file_size > _BUNDLE_MAX_SINGLE_FILE_BYTES:
                skipped.append(f"{safe_name}: file_too_large")
                continue
            raw = archive.read(info.filename)
            if total_bytes + len(raw) > _BUNDLE_MAX_TOTAL_BYTES:
                skipped.append(f"{safe_name}: total_size_limit")
                continue
            if b"\x00" in raw:
                skipped.append(f"{safe_name}: binary")
                continue
            try:
                raw.decode("utf-8")
            except UnicodeDecodeError:
                skipped.append(f"{safe_name}: non_utf8")
                continue
            total_bytes += len(raw)
            entries.append((safe_name, raw))

    for relative_path, raw in _strip_shared_root_prefix(entries):
        files[relative_path] = raw.decode("utf-8")
    return files, skipped


def _artifact_bundle_path_from_version(version) -> Path | None:  # noqa: ANN001
    artifact_path = (version.commit_metadata.metadata or {}).get("artifact_path")
    if not artifact_path:
        return None
    path = Path(str(artifact_path))
    return path if path.exists() else None


def _version_metadata(version) -> dict[str, Any]:
    commit_metadata = getattr(version, "commit_metadata", None)
    metadata = getattr(commit_metadata, "metadata", None)
    if isinstance(metadata, dict):
        return dict(metadata)
    if isinstance(commit_metadata, dict):
        payload = commit_metadata.get("metadata")
        if isinstance(payload, dict):
            return dict(payload)
    return {}


def _refinement_metadata_from_version(version) -> dict[str, Any] | None:  # noqa: ANN001
    metadata = _version_metadata(version)
    refinement = metadata.get("refinement")
    if isinstance(refinement, dict):
        return refinement
    return None


def _load_refinement_review_record_for_version(version):  # noqa: ANN001
    refinement = _refinement_metadata_from_version(version)
    if refinement is None:
        return None
    staging_area = str(refinement.get("staging_area") or "").strip()
    if not staging_area:
        return None
    try:
        return load_refinement_review_record(staging_area)
    except FileNotFoundError:
        return None


def _refinement_acceptance_ready(version) -> bool:  # noqa: ANN001
    refinement = _refinement_metadata_from_version(version)
    if refinement is None:
        return True
    review_record = _load_refinement_review_record_for_version(version)
    review_snapshot = refinement.get("review")
    if not isinstance(review_snapshot, dict):
        return False
    return bool(
        review_record is not None
        and review_record.status == "promotion_ready"
        and review_record.promotion_allowed is True
        and str(review_snapshot.get("status") or "").strip() == "promotion_ready"
        and review_snapshot.get("promotion_allowed") is True
    )


def _validation_override_required(version) -> bool:  # noqa: ANN001
    return version.validation_status in {ArtifactValidationStatus.PENDING, ArtifactValidationStatus.SKIPPED}


def _enforce_artifact_validation_gate(
    version,  # noqa: ANN001
    *,
    action: str,
    allow_validation_override: bool = False,
) -> None:
    if version.validation_status == ArtifactValidationStatus.FAILED:
        raise HTTPException(
            status_code=409,
            detail=f"Artifact cannot be {action}; validation_status='failed'.",
        )
    if version.validation_status == ArtifactValidationStatus.PASSED:
        return
    if allow_validation_override and _validation_override_required(version):
        return
    raise HTTPException(
        status_code=409,
        detail=(
            f"Artifact cannot be {action}; validation_status='passed' is required "
            "unless an explicit validation override is supplied."
        ),
    )


def _resolve_bundle_restore_target(version) -> Path:  # noqa: ANN001
    app_root = resolve_app_root()
    if version.build_family != "app_bundle":
        raise HTTPException(status_code=400, detail=f"Unsupported artifact kind for restore: {version.build_family}")
    app_root.mkdir(parents=True, exist_ok=True)
    return app_root


def _restore_bundle_to_target(*, zip_path: Path, target_dir: Path) -> dict[str, list[str]]:
    restored: list[str] = []
    skipped: list[str] = []
    target_dir.mkdir(parents=True, exist_ok=True)
    target_root = target_dir.resolve()

    with zipfile.ZipFile(zip_path, "r") as archive:
        planned: list[tuple[zipfile.ZipInfo, str]] = []
        for info in archive.infolist():
            if info.is_dir():
                continue
            if _zipinfo_is_symlink(info):
                skipped.append(info.filename)
                continue
            safe_name, reason = _restore_entry_name(info.filename)
            if safe_name is None:
                raise HTTPException(status_code=400, detail=f"Unsafe artifact archive entry: {info.filename} ({reason})")
            if reason is not None:
                skipped.append(safe_name)
                continue
            if _contains_symlink_component(target_root, safe_name):
                raise HTTPException(
                    status_code=400,
                    detail=f"Restore target contains a symlinked component for archive entry: {safe_name}",
                )
            planned.append((info, safe_name))

        if not planned:
            raise HTTPException(status_code=400, detail="Artifact bundle did not contain any restorable files.")

        restore_names = _strip_app_bundle_root_prefix([safe_name for _, safe_name in planned])
        for info, safe_name in planned:
            safe_name = restore_names.get(safe_name, safe_name)
            destination = (target_root / safe_name).resolve()
            if not destination.is_relative_to(target_root):
                raise HTTPException(
                    status_code=400,
                    detail=f"Archive entry escapes restore target: {safe_name}",
                )
            if destination.exists() and destination.is_symlink():
                raise HTTPException(
                    status_code=400,
                    detail=f"Refusing to write through a symlinked target path: {safe_name}",
                )
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(archive.read(info.filename))
            restored.append(safe_name)

    return {"restored": sorted(restored), "skipped": sorted(skipped)}


def _build_diff_preview(*, path: str, before: str | None, after: str | None) -> str:
    before_lines = [] if before is None else before.splitlines(keepends=True)
    after_lines = [] if after is None else after.splitlines(keepends=True)
    diff_lines = list(
        unified_diff(
            before_lines,
            after_lines,
            fromfile=f"a/{path}",
            tofile=f"b/{path}",
            lineterm="",
        )
    )
    if len(diff_lines) > _DIFF_PREVIEW_MAX_LINES:
        diff_lines = diff_lines[:_DIFF_PREVIEW_MAX_LINES] + ["...diff truncated..."]
    preview = "\n".join(diff_lines)
    if len(preview) > _DIFF_PREVIEW_MAX_CHARS:
        preview = preview[:_DIFF_PREVIEW_MAX_CHARS] + "\n...diff truncated..."
    return preview


def _build_bundle_diff_summary(
    *,
    current_files: dict[str, str],
    parent_files: dict[str, str],
) -> list[dict[str, Any]]:
    changed: list[dict[str, Any]] = []
    for path in sorted(set(current_files) | set(parent_files)):
        before = parent_files.get(path)
        after = current_files.get(path)
        if before == after:
            continue
        if before is None:
            change_type = "added"
        elif after is None:
            change_type = "removed"
        else:
            change_type = "modified"
        changed.append(
            {
                "path": path,
                "change_type": change_type,
                "diff_preview": _build_diff_preview(path=path, before=before, after=after),
                "before_size": len(before or ""),
                "after_size": len(after or ""),
            }
        )
    return changed


async def _build_artifact_review_payload(
    *,
    app_id: str,
    version,
    artifact_store,
) -> dict[str, Any]:  # noqa: ANN001
    current_zip = _artifact_bundle_path_from_version(version)
    current_files: dict[str, str] = {}
    current_skipped: list[str] = []
    if current_zip is not None:
        current_files, current_skipped = _decode_text_bundle_entries(current_zip)

    parent_version = None
    parent_files: dict[str, str] = {}
    parent_skipped: list[str] = []
    if version.parent_build_record_id:
        parent_version = await artifact_store.get_build_record(
            app_id=app_id,
            build_record_id=version.parent_build_record_id,
        )
        parent_zip = _artifact_bundle_path_from_version(parent_version) if parent_version is not None else None
        if parent_zip is not None:
            parent_files, parent_skipped = _decode_text_bundle_entries(parent_zip)

    sessions = await artifact_store.list_refinement_sessions(
        app_id=app_id,
        result_build_record_id=version.id,
        limit=5,
    )
    latest_session = sessions[0] if sessions else None
    change_request = None
    if latest_session is not None:
        change_request = await artifact_store.get_change_request(
            app_id=app_id,
            change_request_id=latest_session.change_request_id,
        )
    if change_request is None and version.parent_build_record_id:
        requests = await artifact_store.list_change_requests(
            app_id=app_id,
            build_record_id=version.parent_build_record_id,
            limit=1,
        )
        change_request = requests[0] if requests else None

    refinement_metadata = _refinement_metadata_from_version(version)
    changed_files = _build_bundle_diff_summary(current_files=current_files, parent_files=parent_files)
    selected_paths = []
    validation_result = None
    coding_summary = None
    if latest_session is not None and isinstance(latest_session.metadata, dict):
        worker_meta = latest_session.metadata.get("coding_worker") or {}
        if isinstance(worker_meta, dict):
            selected_paths = list(worker_meta.get("metadata", {}).get("selected_file_paths") or [])
            validation_result = worker_meta.get("validation_result")
            coding_summary = (worker_meta.get("plan") or {}).get("summary")
    if not selected_paths:
        selected_paths = list((version.commit_metadata.metadata or {}).get("applied_paths") or [])

    review_status = version.lifecycle_status.value
    if latest_session is not None:
        review_status = latest_session.status.value
    elif version.lifecycle_status == ArtifactLifecycleStatus.DRAFT:
        review_status = "validated"
    elif version.lifecycle_status == ArtifactLifecycleStatus.ARCHIVED:
        review_status = "rejected"

    can_accept = (
        version.lifecycle_status == ArtifactLifecycleStatus.DRAFT
        and version.validation_status == ArtifactValidationStatus.PASSED
        and _refinement_acceptance_ready(version)
    )
    can_reject = version.lifecycle_status == ArtifactLifecycleStatus.DRAFT
    can_promote = (
        version.lifecycle_status == ArtifactLifecycleStatus.CURRENT
        and version.validation_status == ArtifactValidationStatus.PASSED
        and current_zip is not None
    )
    validation_override_required = _validation_override_required(version)
    validation_blocker = None
    if version.validation_status == ArtifactValidationStatus.FAILED:
        validation_blocker = "Validation failed. Reject or revise this artifact before accepting it."
    elif validation_override_required:
        validation_blocker = "Validation has not passed. Run validation or use an explicit operator override."

    review_package = build_refinement_review_package(
        app_id=app_id,
        artifact_version_id=version.id,
        artifact_kind=version.build_family,
        artifact_key=version.build_key,
        parent_version_id=version.parent_build_record_id,
        lifecycle_status=version.lifecycle_status.value,
        validation_status=version.validation_status.value,
        review_status=review_status,
        changed_files=changed_files,
        selected_paths=selected_paths,
        current_skipped_files=current_skipped,
        parent_skipped_files=parent_skipped,
        refinement_metadata=refinement_metadata,
        change_request=change_request,
        latest_session=latest_session,
        coding_summary=coding_summary,
        validation_result=validation_result,
        validation_override_required=validation_override_required,
        validation_blocker=validation_blocker,
        can_accept=can_accept,
        can_reject=can_reject,
        can_promote=can_promote,
    )

    return {
        "artifact_version": version.model_dump(by_alias=False, mode="python"),
        "parent_artifact_version": parent_version.model_dump(by_alias=False, mode="python") if parent_version else None,
        "change_request": change_request.model_dump(by_alias=False, mode="python") if change_request else None,
        "refinement_session": latest_session.model_dump(by_alias=False, mode="python") if latest_session else None,
        "review": review_package.model_dump(mode="python"),
    }

configure_session_router(
    trigger_route_resolver=get_orchestration_control_harness(),
)

def _resolve_studio_scope(
    principal: UserPrincipal,
    *,
    app_id: str | None = None,
    user_id: str | None = None,
) -> tuple[str, str]:
    """Resolve app/user scope for Studio endpoints in both auth modes."""
    scope = resolve_studio_scope(
        principal,
        app_id=app_id,
        user_id=user_id,
        default_user_id=platform_app._DEFAULT_PROFILE_USER_ID,
    )
    return scope.app_id, scope.user_id


@app.get("/api/shell-config")
async def get_studio_shell_config():
    return await build_shell_config(surface="studio")


@app.get("/api/studio/dashboard")
async def get_studio_dashboard_config(
    scope: Literal["workspace", "app"] | None = None,
    app_id: str | None = None,
    principal: UserPrincipal = Depends(require_user_scope),
):
    resolved_app_id, _ = _resolve_studio_scope(principal, app_id=app_id)
    manifest = load_dashboard_manifest(resolve_app_root())
    payload = manifest.model_dump(mode="json")
    payload["resolved"] = {
        "scope": scope,
        "app_id": resolved_app_id if scope == "app" or app_id else None,
    }
    if scope:
        payload["surface"] = manifest.surface_for_scope(scope).model_dump(mode="json")
    return payload


@app.get("/api/studio/overview")
async def get_app_overview(
    app_id: str | None = None,
    principal: UserPrincipal = Depends(require_user_scope),
):
    resolved_app_id, _ = _resolve_studio_scope(principal, app_id=app_id)
    app_root = resolve_app_root()
    missing_surfaces = get_missing_studio_surfaces(app_root)
    if missing_surfaces:
        raise HTTPException(
            status_code=500,
            detail=f"App overview is missing required surfaces: {', '.join(missing_surfaces)}",
        )

    try:
        record = (await _get_app_registry_service().get_app_record(app_id=resolved_app_id)).get("app")
        if not isinstance(record, dict):
            raise HTTPException(status_code=404, detail=f"App record not found: {resolved_app_id}")
        return build_app_overview_summary(
            app_root,
            surface="shell-overview",
            local_only=True,
            app_record=record,
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail="Failed to build app overview summary") from exc


@app.get("/api/studio/apps")
async def get_workspace_apps(
    principal: UserPrincipal = Depends(require_user_scope),
):
    _, user_id = _resolve_studio_scope(principal)
    app_root = resolve_app_root()
    missing_surfaces = get_missing_studio_surfaces(app_root)
    if missing_surfaces:
        raise HTTPException(
            status_code=500,
            detail=f"Apps surface is missing required surfaces: {', '.join(missing_surfaces)}",
        )

    try:
        apps = (await _get_app_registry_service().list_apps(owner_user_id=user_id)).get("apps") or []
        return build_apps_summary(
            app_root,
            surface="shell-apps",
            local_only=True,
            app_records=apps,
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail="Failed to build apps summary") from exc


class CreateWorkspaceAppRequest(BaseModel):
    name: str | None = Field(default=None, max_length=120)
    description: str | None = Field(default=None, max_length=1000)
    app_id: str | None = Field(default=None, max_length=160)
    chat_app_id: str | None = Field(default=None, max_length=160)
    status: str = Field(default="draft", max_length=40)
    active_chat_id: str | None = Field(default=None, max_length=160)
    active_workflow_id: str | None = Field(default=None, max_length=160)
    name_source: str | None = Field(default=None, max_length=80)
    build_context_profile: dict[str, Any] | None = None
    current_build_run: dict[str, Any] | None = None


class UpdateWorkspaceAppStatusRequest(BaseModel):
    status: str = Field(min_length=1, max_length=40)
    bundle_path: str | None = Field(default=None, max_length=1000)
    artifact_version_id: str | None = Field(default=None, max_length=160)
    workflow_sequence: str | None = Field(default=None, max_length=160)
    active_chat_id: str | None = Field(default=None, max_length=160)
    active_workflow_id: str | None = Field(default=None, max_length=160)
    current_build_run: dict[str, Any] | None = None


@app.post("/api/studio/apps")
async def create_workspace_app(
    body: CreateWorkspaceAppRequest,
    principal: UserPrincipal = Depends(require_user_scope),
):
    _, user_id = _resolve_studio_scope(principal)
    try:
        return await _get_app_registry_service().create_app_record(
            owner_user_id=user_id,
            name=body.name,
            description=body.description,
            status=body.status,
            app_id=body.app_id,
            chat_app_id=body.chat_app_id,
            active_chat_id=body.active_chat_id,
            active_workflow_id=body.active_workflow_id,
            name_source=body.name_source,
            build_context_profile=body.build_context_profile,
            current_build_run=body.current_build_run,
        )
    except ValueError as exc:
        logger.warning("create_app_record validation error: %s", exc)
        raise HTTPException(status_code=400, detail="Invalid app parameters.") from exc
    except Exception as exc:
        logger.exception("Failed to create app record")
        raise HTTPException(status_code=500, detail="Failed to create app record") from exc


@app.put("/api/studio/apps/{build_registry_id}/status")
async def update_workspace_app_status(
    build_registry_id: str,
    body: UpdateWorkspaceAppStatusRequest,
    principal: UserPrincipal = Depends(require_user_scope),
):
    _, _user_id = _resolve_studio_scope(principal)
    try:
        return await _get_app_registry_service().update_build_status(
            build_registry_id=build_registry_id,
            status=body.status,
            bundle_path=body.bundle_path,
            artifact_version_id=body.artifact_version_id,
            workflow_sequence=body.workflow_sequence,
            active_chat_id=body.active_chat_id,
            active_workflow_id=body.active_workflow_id,
            current_build_run=body.current_build_run,
        )
    except ValueError as exc:
        logger.warning("update_app_status validation error: %s", exc)
        raise HTTPException(status_code=400, detail="Invalid app status parameters.") from exc
    except Exception as exc:
        logger.exception("Failed to update app status")
        raise HTTPException(status_code=500, detail="Failed to update app status") from exc


@app.delete("/api/studio/apps/{build_registry_id}")
async def delete_workspace_app(
    build_registry_id: str,
    principal: UserPrincipal = Depends(require_user_scope),
):
    _, _user_id = _resolve_studio_scope(principal)
    try:
        return await _get_app_registry_service().delete_app(build_registry_id=build_registry_id)
    except ValueError as exc:
        logger.warning("delete_app validation error: %s", exc)
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Failed to delete app record")
        raise HTTPException(status_code=500, detail="Failed to delete app record") from exc


@app.get("/api/studio/integrations")
async def get_app_integrations(
    app_id: str | None = None,
    principal: UserPrincipal = Depends(require_user_scope),
):
    app_id, _ = _resolve_studio_scope(principal, app_id=app_id)
    return await build_integrations_summary(app_id=app_id)


@app.get("/api/studio/integrations/connectors")
async def get_integration_connectors(
    app_id: str | None = None,
    principal: UserPrincipal = Depends(require_user_scope),
):
    app_id, _ = _resolve_studio_scope(principal, app_id=app_id)
    connectors = await list_connectors(scope=ConnectorStore.SCOPE_APP, scope_id=app_id)
    return {
        "app_id": app_id,
        "connectors": [_redact_connector_record(connector) for connector in connectors],
    }


class IntegrationConnectorPatchRequest(BaseModel):
    display_name: str | None = None
    notes: str | None = None
    status: Literal["metadata_only", "active", "expiring", "expired", "revoked"] | None = None
    expires_at: str | None = None
    public_config: dict[str, Any] | None = None
    required_fields: list[dict[str, Any]] | None = None
    secret_value: str | None = None
    ttl_days: int | None = Field(default=30, ge=1, le=3650)


class IntegrationConnectorCreateRequest(IntegrationConnectorPatchRequest):
    service: str = Field(..., description="Connector service identifier, such as email_provider or analytics_provider")


IntegrationConnectorPatchRequest.model_rebuild()
IntegrationConnectorCreateRequest.model_rebuild()


class AppContextRefreshPlanRequest(BaseModel):
    reason: str = Field(..., min_length=1)
    refresh_scope: ContextRefreshScope = ContextRefreshScope.DISCOVERY_INDEXING
    source_refs: list[SourceRef] | None = None
    current_context_version_id: str | None = None
    requested_by: str | None = None


class AppContextRefreshLaunchRequest(BaseModel):
    plan: ContextRefreshPlan
    confirm_launch: bool = False
    reason: str | None = None
    refresh_scope: ContextRefreshScope = ContextRefreshScope.DISCOVERY_INDEXING
    request_id: str | None = None


class AppContextRefreshCompleteRequest(BaseModel):
    plan: ContextRefreshPlan
    previous_context_version_id: str | None = None
    launch_result: ContextRefreshLaunchResult | None = None
    workflow_context_variables: dict[str, Any] = Field(default_factory=dict)


class AppIntelligenceIndexRequest(BaseModel):
    source_kind: Literal["local_workspace", "git_repository"] = "local_workspace"
    workspace_root: str | None = Field(default=None, min_length=1)
    repo_url: str | None = Field(default=None, min_length=1)
    branch: str | None = Field(default=None, max_length=240)
    monorepo_path: str | None = Field(default=None, max_length=1000)
    auth_connector_id: str | None = Field(default=None, max_length=160)
    ignored_paths: list[str] = Field(default_factory=list)
    workspace_key: str = "app_intelligence_workspace"
    make_current: bool = True
    scan_policy: dict[str, Any] | None = None


class AppSourceValidationRequest(BaseModel):
    allowed_kinds: list[Literal["install", "lint", "test", "build", "typecheck"]] = Field(default_factory=list)
    include_install: bool = False
    max_commands: int = Field(default=4, ge=1, le=12)
    timeout_seconds: int = Field(default=120, ge=5, le=900)
    confirm_execution: bool = False


class AppContextPolicyOverrideRequest(BaseModel):
    request_id: str = Field(..., min_length=1)
    context_version_id: str | None = None
    original_policy_decision: AppContextPolicyDecision
    override_decision: AppContextPolicyOverrideDecision
    reason: str = Field(..., min_length=1)
    reviewer: str = Field(..., min_length=1)
    applies_to_paths: list[str] = Field(default_factory=list)
    change_class: str | None = None
    refinement_lane: str | None = None
    policy_result: AppContextPolicyResult | None = None
    apply_to_plan: bool = False
    plan: dict[str, Any] | None = None


_SECRET_RESPONSE_KEYS = {
    "secret_value",
    "secret",
    "secret_hash",
    "secret_salt",
    "api_key",
    "apikey",
    "token",
    "access_token",
    "refresh_token",
    "auth_token",
    "bearer_token",
    "client_secret",
    "client_secret_hash",
    "password",
    "private_key",
    "private_cert",
}


def _redact_secret_fields(value: Any) -> Any:
    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        for key, item in value.items():
            if str(key).strip().lower() in _SECRET_RESPONSE_KEYS:
                continue
            redacted[key] = _redact_secret_fields(item)
        return redacted
    if isinstance(value, list):
        return [_redact_secret_fields(item) for item in value]
    return value


def _redact_connector_record(record: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(record, dict):
        return None
    enriched = dict(record)
    if not isinstance(enriched.get("health"), dict):
        enriched["health"] = compute_connector_health(enriched, checked_by="manual")
    return _redact_secret_fields(enriched)


def _redact_secret_result(result: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(result, dict):
        return None
    return _redact_secret_fields(result)


def _context_refresh_policy(reason: str, warnings: list[str] | None = None) -> AppContextPolicyResult:
    return AppContextPolicyResult(
        decision=AppContextPolicyDecision.BLOCK_REQUIRES_CONTEXT_REFRESH,
        allowed=False,
        blocking=True,
        risk_level="high",
        reasons=[reason],
        warnings=list(warnings or []),
        requires_context_refresh=True,
    )


def _plan_from_operator_payload(payload: dict[str, Any]) -> RefinementExecutionPlan | RefinementDryRunPlan:
    try:
        return RefinementExecutionPlan.model_validate(payload)
    except ValidationError:
        return RefinementDryRunPlan.model_validate(payload)


@app.get("/api/studio/apps/{app_id}/context")
async def get_studio_app_context_status(
    app_id: str,
    principal: UserPrincipal = Depends(require_user_scope),
):
    validate_path_id(app_id, "app_id")
    resolved_app_id, _ = _resolve_studio_scope(principal, app_id=app_id)
    summary = await get_current_app_context_summary(
        app_id=resolved_app_id,
        artifact_store=get_artifact_store(),
    )
    graph_lookup = await get_current_app_context_graph(
        app_id=resolved_app_id,
        app_context_summary=summary,
        artifact_store=get_artifact_store(),
    )
    graph = graph_lookup.graph
    graph_status = {
        "available": graph is not None,
        "graph_id": graph.graph_id if graph is not None else None,
        "stale_status": graph.stale_status.value if graph is not None else None,
        "graph_hash": graph.graph_hash if graph is not None else None,
        "indexed_at": graph.indexed_at.isoformat() if graph is not None else None,
        "node_count": len(graph.nodes) if graph is not None else 0,
        "edge_count": len(graph.edges) if graph is not None else 0,
        "source_ref_count": len(graph.source_refs) if graph is not None else 0,
        "warnings": list(graph_lookup.warnings),
    }
    latest_job = await get_latest_app_intelligence_index_job(app_id=resolved_app_id)
    context_readiness = _studio_context_readiness(
        summary=summary,
        graph_status=graph_status,
        latest_job=latest_job,
    )
    return _redact_secret_fields(
        {
            "app_id": resolved_app_id,
            "app_context_summary": summary.model_dump(mode="json"),
            "context_graph_status": graph_status,
            "context_readiness": context_readiness,
            "index_job": public_app_intelligence_index_job(latest_job),
            "stale_status": summary.stale_status,
            "warnings": [*list(summary.warnings), *list(graph_lookup.warnings)],
            "artifact_refs": [ref.model_dump(mode="json") for ref in summary.artifact_refs],
            "ownership": summary.ownership.model_dump(mode="json"),
        }
    )


@app.post("/api/studio/apps/{app_id}/context/app-intelligence/index", status_code=202)
async def index_studio_app_intelligence_context(
    app_id: str,
    body: AppIntelligenceIndexRequest,
    background_tasks: BackgroundTasks,
    principal: UserPrincipal = Depends(require_user_scope),
):
    validate_path_id(app_id, "app_id")
    resolved_app_id, user_id = _resolve_studio_scope(principal, app_id=app_id)
    return await _start_studio_app_intelligence_index_job(
        app_id=resolved_app_id,
        user_id=user_id,
        body=body,
        background_tasks=background_tasks,
    )


@app.post("/api/studio/apps/{app_id}/context/source-import", status_code=202)
async def import_studio_app_source_context(
    app_id: str,
    body: AppIntelligenceIndexRequest,
    background_tasks: BackgroundTasks,
    principal: UserPrincipal = Depends(require_user_scope),
):
    validate_path_id(app_id, "app_id")
    resolved_app_id, user_id = _resolve_studio_scope(principal, app_id=app_id)
    return await _start_studio_app_intelligence_index_job(
        app_id=resolved_app_id,
        user_id=user_id,
        body=body,
        background_tasks=background_tasks,
    )


@app.get("/api/studio/apps/{app_id}/context/app-intelligence/index/latest")
async def get_latest_studio_app_intelligence_index_job(
    app_id: str,
    principal: UserPrincipal = Depends(require_user_scope),
):
    validate_path_id(app_id, "app_id")
    resolved_app_id, _ = _resolve_studio_scope(principal, app_id=app_id)
    job = await get_latest_app_intelligence_index_job(app_id=resolved_app_id)
    return _redact_secret_fields({"app_id": resolved_app_id, "index_job": public_app_intelligence_index_job(job)})


@app.get("/api/studio/apps/{app_id}/context/app-intelligence/index/{job_id}")
async def get_studio_app_intelligence_index_job(
    app_id: str,
    job_id: str,
    principal: UserPrincipal = Depends(require_user_scope),
):
    validate_path_id(app_id, "app_id")
    validate_path_id(job_id, "job_id")
    resolved_app_id, _ = _resolve_studio_scope(principal, app_id=app_id)
    job = await get_app_intelligence_index_job(app_id=resolved_app_id, job_id=job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="App Intelligence index job not found.")
    return _redact_secret_fields({"app_id": resolved_app_id, "index_job": public_app_intelligence_index_job(job)})


@app.post("/api/studio/apps/{app_id}/context/validation/run")
async def run_studio_app_source_validation(
    app_id: str,
    body: AppSourceValidationRequest,
    principal: UserPrincipal = Depends(require_user_scope),
):
    validate_path_id(app_id, "app_id")
    if not body.confirm_execution:
        raise HTTPException(status_code=400, detail="confirm_execution=true is required to run app validation.")
    resolved_app_id, _ = _resolve_studio_scope(principal, app_id=app_id)
    try:
        result = await run_current_app_source_validation(
            app_id=resolved_app_id,
            artifact_store=get_artifact_store(),
            allowed_kinds=body.allowed_kinds,
            include_install=body.include_install,
            max_commands=body.max_commands,
            timeout_seconds=body.timeout_seconds,
            confirm_execution=body.confirm_execution,
        )
    except ValueError as exc:
        logger.warning("run_app_source_validation validation error app=%s: %s", app_id, exc)
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _redact_secret_fields(
        {
            "app_id": resolved_app_id,
            "validation": result.model_dump(mode="json"),
        }
    )


async def _start_studio_app_intelligence_index_job(
    *,
    app_id: str,
    user_id: str | None,
    body: AppIntelligenceIndexRequest,
    background_tasks: BackgroundTasks,
) -> dict[str, Any]:
    try:
        _validate_app_intelligence_index_request(body)
        job = create_app_intelligence_index_job(
            app_id=app_id,
            requested_by=user_id,
            source_kind=body.source_kind,
            workspace_root=body.workspace_root,
            repo_url=body.repo_url,
            branch=body.branch,
            monorepo_path=body.monorepo_path,
            auth_connector_id=body.auth_connector_id,
            ignored_paths=body.ignored_paths,
            workspace_key=body.workspace_key,
            make_current=body.make_current,
            scan_policy=body.scan_policy,
        )
        job = await save_app_intelligence_index_job(job)
    except ValueError as exc:
        logger.warning("create_app_intelligence_index_job validation error app=%s: %s", app_id, exc)
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    background_tasks.add_task(_run_studio_app_intelligence_index_job, app_id, job.job_id)
    return _redact_secret_fields(
        {
            "app_id": app_id,
            "accepted": True,
            "index_job": public_app_intelligence_index_job(job),
        }
    )


def _validate_app_intelligence_index_request(body: AppIntelligenceIndexRequest) -> None:
    if body.source_kind == "local_workspace" and not body.workspace_root:
        raise ValueError("workspace_root is required for local workspace indexing")
    if body.source_kind == "git_repository" and not body.repo_url:
        raise ValueError("repo_url is required for repository import")


async def _run_studio_app_intelligence_index_job(app_id: str, job_id: str) -> None:
    job = await get_app_intelligence_index_job(app_id=app_id, job_id=job_id)
    if job is None:
        return
    try:
        if job.source_kind == "git_repository":
            job = advance_app_intelligence_index_job(job, phase_id="repo_clone")
            job = await save_app_intelligence_index_job(job)

        import_result = await to_thread(
            resolve_source_import,
            app_id=job.app_id,
            request=_source_import_request_from_job(job),
        )
        job = job.model_copy(
            update={
                "workspace_root": import_result.selected_root,
                "import_result": import_result.model_dump(mode="python"),
                "warnings": _dedupe_strings([*job.warnings, *import_result.warnings]),
            }
        )
        job = await save_app_intelligence_index_job(job)

        async def progress_callback(phase_id: str, details: dict[str, Any]) -> None:
            nonlocal job
            job = advance_app_intelligence_index_job(
                job,
                phase_id=phase_id,
                message=str((details or {}).get("message") or ""),
                details=details,
            )
            job = await save_app_intelligence_index_job(job)

        result = await index_workspace_app_intelligence(
            app_id=job.app_id,
            workspace_root=import_result.selected_root,
            artifact_store=get_artifact_store(),
            workspace_key=job.workspace_key,
            source_workflow="studio_app_intelligence_index",
            source_chat_id=job.requested_by,
            scan_policy=source_import_scan_policy(job.scan_policy, ignored_paths=import_result.ignored_paths),
            make_current=job.make_current,
            progress_callback=progress_callback,
        )
        job = complete_app_intelligence_index_job(
            job,
            app_intelligence=_app_intelligence_result_payload(result),
            context_readiness=_index_result_readiness(result),
            warnings=result.warnings,
        )
        await save_app_intelligence_index_job(job)
    except Exception as exc:
        logger.exception("Studio App Intelligence index job failed job_id=%s", job_id)
        failed = fail_app_intelligence_index_job(job, error=str(exc), warnings=job.warnings)
        await save_app_intelligence_index_job(failed)


def _source_import_request_from_job(job: AppIntelligenceIndexJob) -> SourceImportRequest:
    return SourceImportRequest(
        source_kind=job.source_kind,
        workspace_root=job.workspace_root,
        repo_url=job.repo_url,
        branch=job.branch,
        monorepo_path=job.monorepo_path,
        auth_connector_id=job.auth_connector_id,
        ignored_paths=list(job.ignored_paths),
    )


def _app_intelligence_result_payload(result: Any) -> dict[str, Any]:
    return {
        "app_bundle_artifact_version_id": result.app_bundle_artifact_version_id,
        "source_context_artifact_version_id": result.source_context_artifact_version_id,
        "app_intelligence_artifact_version_id": result.app_intelligence_artifact_version_id,
        "app_context_version_id": result.app_context_version_id,
        "app_context_artifact_version_id": result.app_context_artifact_version_id,
        "graph_artifact_version_id": result.graph_artifact_version_id,
        "artifact_path": result.artifact_path,
        "indexed_file_count": result.indexed_file_count,
        "scan_health": result.scan_health,
        "health_report": result.health_report,
        "framework_detection": result.framework_detection,
        "warnings": result.warnings,
    }


def _index_result_readiness(result: Any) -> dict[str, Any]:
    frameworks = dict(result.framework_detection or {})
    graph_health = dict(result.health_report or {})
    status = "ready" if result.indexed_file_count > 0 and not graph_health.get("blockers") else "degraded"
    return {
        "status": status,
        "indexed_file_count": result.indexed_file_count,
        "primary_framework_id": frameworks.get("primary_framework_id"),
        "primary_framework_label": frameworks.get("primary_framework_label"),
        "framework_count": len(frameworks.get("frameworks") or []),
        "validation_command_count": len(frameworks.get("validation_commands") or []),
        "warnings": _dedupe_strings([*list(result.warnings or []), *list(graph_health.get("warnings") or [])]),
    }


def _job_readiness(job: AppIntelligenceIndexJob | None) -> dict[str, Any] | None:
    if job is None:
        return None
    if job.context_readiness:
        return dict(job.context_readiness)
    if job.status in {"queued", "running"}:
        return {"status": job.status, "progress_percent": job.progress_percent, "current_phase": job.current_phase}
    if job.status == "failed":
        return {"status": "failed", "error": job.error, "warnings": list(job.warnings)}
    return {"status": job.status, "progress_percent": job.progress_percent}


def _studio_context_readiness(*, summary: Any, graph_status: dict[str, Any], latest_job: AppIntelligenceIndexJob | None) -> dict[str, Any]:
    job_readiness = _job_readiness(latest_job)
    if job_readiness and latest_job and latest_job.status in {"queued", "running", "failed"}:
        return job_readiness
    if graph_status.get("available"):
        stale_status = getattr(summary.stale_status, "value", summary.stale_status)
        return {
            "status": "ready" if stale_status == "current" else "stale",
            "graph_id": graph_status.get("graph_id"),
            "indexed_at": graph_status.get("indexed_at"),
            "node_count": graph_status.get("node_count"),
            "edge_count": graph_status.get("edge_count"),
            "warnings": _dedupe_strings([*list(summary.warnings), *list(graph_status.get("warnings") or [])]),
        }
    return job_readiness or {"status": "missing", "warnings": list(summary.warnings)}


def _dedupe_strings(values: list[Any]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if text and text not in seen:
            seen.add(text)
            out.append(text)
    return out


@app.post("/api/studio/apps/{app_id}/context/refresh-plan")
async def create_studio_app_context_refresh_plan(
    app_id: str,
    body: AppContextRefreshPlanRequest,
    principal: UserPrincipal = Depends(require_user_scope),
):
    validate_path_id(app_id, "app_id")
    resolved_app_id, user_id = _resolve_studio_scope(principal, app_id=app_id)
    summary = await get_current_app_context_summary(
        app_id=resolved_app_id,
        artifact_store=get_artifact_store(),
    )
    if body.current_context_version_id is not None:
        summary = summary.model_copy(update={"context_version_id": body.current_context_version_id})
    try:
        request = build_context_refresh_request(
            app_id=resolved_app_id,
            app_context_summary=summary,
            policy_result=_context_refresh_policy(body.reason, summary.warnings),
            reason=body.reason,
            source_refs=body.source_refs,
            requested_by=body.requested_by or user_id,
            refresh_scope=body.refresh_scope,
        )
        plan = build_context_refresh_plan(
            policy_result=_context_refresh_policy(body.reason, summary.warnings),
            app_context_summary=summary,
            request=request,
        )
    except ValueError as exc:
        logger.warning("build_context_refresh_plan validation error app=%s: %s", app_id, exc)
        raise HTTPException(status_code=400, detail="Invalid context refresh parameters.") from exc
    return _redact_secret_fields(
        {
            "app_id": resolved_app_id,
            "context_refresh_request": request.model_dump(mode="json"),
            "context_refresh_plan": plan.model_dump(mode="json"),
            "launched": False,
        }
    )


@app.post("/api/studio/apps/{app_id}/context/refresh-launch")
async def launch_studio_app_context_refresh(
    app_id: str,
    body: AppContextRefreshLaunchRequest,
    principal: UserPrincipal = Depends(require_user_scope),
):
    validate_path_id(app_id, "app_id")
    if not body.confirm_launch:
        raise HTTPException(status_code=400, detail="confirm_launch=true is required to launch context refresh.")
    resolved_app_id, user_id = _resolve_studio_scope(principal, app_id=app_id)
    try:
        result = await launch_context_refresh_plan(
            body.plan,
            app_id=resolved_app_id,
            user_id=user_id,
            refresh_scope=body.refresh_scope,
            reason=body.reason,
            request_id=body.request_id,
            session_router=get_session_router(),
        )
    except ValueError as exc:
        logger.warning("launch_context_refresh_plan validation error app=%s: %s", app_id, exc)
        raise HTTPException(status_code=400, detail="Invalid context refresh launch parameters.") from exc
    return _redact_secret_fields(
        {
            "app_id": resolved_app_id,
            "context_refresh_launch": result.model_dump(mode="json"),
        }
    )


@app.post("/api/studio/apps/{app_id}/context/refresh-complete")
async def complete_studio_app_context_refresh(
    app_id: str,
    body: AppContextRefreshCompleteRequest,
    principal: UserPrincipal = Depends(require_user_scope),
):
    validate_path_id(app_id, "app_id")
    resolved_app_id, _ = _resolve_studio_scope(principal, app_id=app_id)
    try:
        result = await complete_context_refresh(
            body.plan,
            app_id=resolved_app_id,
            artifact_store=get_artifact_store(),
            previous_context_version_id=body.previous_context_version_id,
            launch_result=body.launch_result,
            workflow_context_variables=dict(body.workflow_context_variables or {}),
        )
    except ValueError as exc:
        logger.warning("complete_context_refresh validation error app=%s: %s", app_id, exc)
        raise HTTPException(status_code=400, detail="Invalid context refresh completion parameters.") from exc
    return _redact_secret_fields(
        {
            "app_id": resolved_app_id,
            "context_refresh_result": result.model_dump(mode="json"),
        }
    )


@app.post("/api/studio/apps/{app_id}/context/override")
async def create_studio_app_context_policy_override(
    app_id: str,
    body: AppContextPolicyOverrideRequest,
    principal: UserPrincipal = Depends(require_user_scope),
):
    validate_path_id(app_id, "app_id")
    resolved_app_id, _ = _resolve_studio_scope(principal, app_id=app_id)
    policy_result = body.policy_result or AppContextPolicyResult(
        decision=body.original_policy_decision,
        allowed=False,
        blocking=True,
    )
    try:
        override = create_app_context_policy_override(
            policy_result=policy_result,
            app_id=resolved_app_id,
            request_id=body.request_id,
            context_version_id=body.context_version_id,
            override_decision=body.override_decision,
            reason=body.reason,
            reviewer=body.reviewer,
            applies_to_paths=body.applies_to_paths,
            applies_to_change_class=body.change_class,
            applies_to_refinement_lane=body.refinement_lane,
        )
        applied_plan = None
        if body.apply_to_plan:
            if body.plan is None:
                raise ValueError("plan is required when apply_to_plan=true")
            applied_plan = apply_app_context_policy_override(
                _plan_from_operator_payload(body.plan),
                override,
            )
    except (ValidationError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="Invalid policy override request") from exc

    return _redact_secret_fields(
        {
            "app_id": resolved_app_id,
            "app_context_policy_override": override.model_dump(mode="json"),
            "applied_plan": applied_plan.model_dump(mode="json") if applied_plan is not None else None,
            "mutation_allowed": False,
        }
    )


@app.post("/api/studio/integrations/connectors")
async def create_or_update_integration_connector(
    body: IntegrationConnectorCreateRequest,
    app_id: str | None = None,
    principal: UserPrincipal = Depends(require_user_scope),
):
    app_id, user_id = _resolve_studio_scope(principal, app_id=app_id)
    record = None
    secret_result: dict[str, Any] | None = None
    if body.secret_value:
        secret_result = await save_connector(
            scope=ConnectorStore.SCOPE_APP,
            scope_id=app_id,
            user_id=user_id,
            service=body.service,
            secret_value=body.secret_value,
            display_name=body.display_name,
            ttl_days=body.ttl_days or 30,
            public_config=body.public_config,
            required_fields=body.required_fields,
        )
        record = secret_result.get("connector")
    if (
        body.display_name is not None
        or body.notes is not None
        or body.status is not None
        or body.expires_at is not None
        or body.public_config is not None
    ):
        record = await patch_connector(
            scope=ConnectorStore.SCOPE_APP,
            scope_id=app_id,
            service=body.service,
            user_id=user_id,
            display_name=body.display_name,
            notes=body.notes,
            status=body.status or ("active" if (secret_result or {}).get("success") else "metadata_only"),
            expires_at=body.expires_at,
            public_config=body.public_config,
            required_fields=body.required_fields,
        )
    if not record:
        store = ConnectorStore()
        record = await store.upsert(
            scope=ConnectorStore.SCOPE_APP,
            scope_id=app_id,
            service=body.service,
            display_name=body.display_name,
            user_id=user_id,
            status=body.status or "metadata_only",
            secret_storage="unmanaged",
            secret_available=False,
            notes=body.notes,
            expires_at=body.expires_at,
            public_config=body.public_config,
            required_fields=body.required_fields,
            status_reason="Created manually from the integrations surface.",
        )
    return {
        "app_id": app_id,
        "connector": _redact_connector_record(record),
        "secret_result": _redact_secret_result(secret_result),
    }


@app.patch("/api/studio/integrations/connectors/{service}")
async def patch_integration_connector(
    service: str,
    body: IntegrationConnectorPatchRequest,
    app_id: str | None = None,
    principal: UserPrincipal = Depends(require_user_scope),
):
    validate_path_id(service, "service")
    app_id, user_id = _resolve_studio_scope(principal, app_id=app_id)
    store = ConnectorStore()
    existing = await store.get(scope=ConnectorStore.SCOPE_APP, scope_id=app_id, service=service)
    if not existing:
        raise HTTPException(status_code=404, detail=f"Connector not found: {service}")

    secret_result: dict[str, Any] | None = None
    if body.secret_value:
        secret_result = await save_connector(
            scope=ConnectorStore.SCOPE_APP,
            scope_id=app_id,
            user_id=user_id,
            service=service,
            secret_value=body.secret_value,
            display_name=body.display_name,
            ttl_days=body.ttl_days or 30,
            public_config=body.public_config,
            required_fields=body.required_fields,
        )
    record = await patch_connector(
        scope=ConnectorStore.SCOPE_APP,
        scope_id=app_id,
        service=service,
        user_id=user_id,
        display_name=body.display_name,
        notes=body.notes,
        status=body.status,
        expires_at=body.expires_at,
        public_config=body.public_config,
        required_fields=body.required_fields,
    )
    return {
        "app_id": app_id,
        "connector": _redact_connector_record(record),
        "secret_result": _redact_secret_result(secret_result),
    }


@app.post("/api/studio/integrations/connectors/{service}/health-check")
async def check_integration_connector_health(
    service: str,
    app_id: str | None = None,
    principal: UserPrincipal = Depends(require_user_scope),
):
    validate_path_id(service, "service")
    app_id, _ = _resolve_studio_scope(principal, app_id=app_id)
    result = await run_connector_health_check(app_id=app_id, service=service, checked_by="manual")
    return {
        "app_id": app_id,
        "service": service,
        "health": _redact_secret_fields(
            {
                "status": result.get("status"),
                "last_checked_at": result.get("last_checked_at"),
                "message": result.get("message"),
                "missing_fields": result.get("missing_fields") or [],
                "checked_by": result.get("checked_by") or "manual",
                "safe_details": result.get("health_details") or {},
                "error_code": result.get("error_code"),
                "health_check_supported": bool(result.get("health_check_supported")),
                "frontend_safe": True,
            }
        ),
    }


@app.delete("/api/studio/integrations/connectors/{service}")
async def remove_integration_connector(
    service: str,
    app_id: str | None = None,
    principal: UserPrincipal = Depends(require_user_scope),
):
    validate_path_id(service, "service")
    app_id, _ = _resolve_studio_scope(principal, app_id=app_id)
    result = await delete_connector(scope=ConnectorStore.SCOPE_APP, scope_id=app_id, service=service)
    if not result.get("deleted"):
        raise HTTPException(status_code=404, detail=f"Connector not found: {service}")
    return {
        "app_id": app_id,
        **result,
    }


@app.get("/api/studio/build/history")
async def get_build_history(
    principal: UserPrincipal = Depends(require_user_scope),
    app_id: str | None = None,
    build_family: str | None = None,
    build_key: str | None = None,
    build_record_id: str | None = None,
    limit: int = 25,
):
    """Return recent artifact versions and change requests for the current workspace."""
    app_id, _ = _resolve_studio_scope(principal)
    artifact_store = get_artifact_store()
    versions = await artifact_store.list_build_records(
        app_id=app_id,
        build_family=build_family,
        build_key=build_key,
        limit=min(limit, 100),
    )
    change_requests = await artifact_store.list_change_requests(
        app_id=app_id,
        build_record_id=build_record_id,
        limit=min(limit, 100),
    )
    return {
        "app_id": app_id,
        "artifact_versions": [v.model_dump(by_alias=False, mode="python") for v in versions],
        "change_requests": [cr.model_dump(by_alias=False, mode="python") for cr in change_requests],
    }


@app.get("/api/studio/build/artifacts/{artifact_version_id}/bundle")
async def get_build_artifact_bundle(
    artifact_version_id: str,
    app_id: str | None = None,
    principal: UserPrincipal = Depends(require_user_scope),
):
    validate_path_id(artifact_version_id, "artifact_version_id")
    app_id, _ = _resolve_studio_scope(principal, app_id=app_id)
    artifact_store = get_artifact_store()
    version = await artifact_store.get_build_record(
        app_id=app_id,
        build_record_id=artifact_version_id,
    )
    if not version:
        raise HTTPException(status_code=404, detail=f"Artifact version not found: {artifact_version_id}")
    if version.build_family not in {"app_bundle", "workflow_bundle"}:
        raise HTTPException(status_code=400, detail=f"Unsupported artifact kind for bundle workbench: {version.build_family}")

    zip_path = _artifact_bundle_path_from_version(version)
    if zip_path is None:
        raise HTTPException(
            status_code=400,
            detail="This artifact version does not have a bundle path that the build surface can inspect.",
        )

    try:
        generated_files, skipped_files = _decode_text_bundle_entries(zip_path)
    except Exception as exc:
        raise HTTPException(status_code=500, detail="Failed to load artifact bundle") from exc

    review_payload = await _build_artifact_review_payload(
        app_id=app_id,
        version=version,
        artifact_store=artifact_store,
    )

    return {
        "app_id": app_id,
        "artifact_version_id": version.id,
        "build_family": version.build_family,
        "build_key": version.build_key,
        "source_workflow": version.source_workflow,
        "bundle_path": str(zip_path),
        "generated_files": generated_files,
        "skipped_files": skipped_files,
        "workbench": {
            "title": f"Artifact Workbench · {version.build_key} v{version.version_number}",
            "description": "Inspect a persisted artifact bundle and launch scoped coding refinement from explicit file scope.",
            "artifact_version_id": version.id,
            "build_family": version.build_family,
            "build_key": version.build_key,
            "generated_files": generated_files,
        },
        "review": review_payload["review"],
        "refinement_session": review_payload["refinement_session"],
        "change_request": review_payload["change_request"],
    }


@app.get("/api/studio/build/artifacts/{artifact_version_id}/review")
async def get_build_artifact_review(
    artifact_version_id: str,
    app_id: str | None = None,
    principal: UserPrincipal = Depends(require_user_scope),
):
    validate_path_id(artifact_version_id, "artifact_version_id")
    app_id, _ = _resolve_studio_scope(principal, app_id=app_id)
    artifact_store = get_artifact_store()
    version = await artifact_store.get_build_record(
        app_id=app_id,
        build_record_id=artifact_version_id,
    )
    if not version:
        raise HTTPException(status_code=404, detail=f"Artifact version not found: {artifact_version_id}")
    payload = await _build_artifact_review_payload(
        app_id=app_id,
        version=version,
        artifact_store=artifact_store,
    )
    return {"app_id": app_id, **payload}


class BuildArtifactAcceptanceRequest(BaseModel):
    allow_validation_override: bool = Field(
        default=False,
        description="Allow accepting skipped or pending validation with an explicit operator override.",
    )
    notes: str | None = Field(default=None, max_length=2000)


@app.post("/api/studio/build/artifacts/{artifact_version_id}/accept")
async def accept_build_artifact_version(
    artifact_version_id: str,
    body: BuildArtifactAcceptanceRequest | None = None,
    app_id: str | None = None,
    principal: UserPrincipal = Depends(require_user_scope),
):
    validate_path_id(artifact_version_id, "artifact_version_id")
    app_id, _ = _resolve_studio_scope(principal, app_id=app_id)
    artifact_store = get_artifact_store()
    version = await artifact_store.get_build_record(app_id=app_id, build_record_id=artifact_version_id)
    if not version:
        raise HTTPException(status_code=404, detail=f"Artifact version not found: {artifact_version_id}")
    if version.lifecycle_status == ArtifactLifecycleStatus.ARCHIVED:
        raise HTTPException(status_code=409, detail="Rejected artifact versions cannot be accepted.")
    if version.lifecycle_status != ArtifactLifecycleStatus.DRAFT:
        raise HTTPException(status_code=409, detail="Only draft artifact versions can be accepted.")
    allow_validation_override = bool(body.allow_validation_override) if body is not None else False
    _enforce_artifact_validation_gate(
        version,
        action="accepted",
        allow_validation_override=allow_validation_override,
    )

    refinement_metadata = _refinement_metadata_from_version(version)
    refinement_review_record = _load_refinement_review_record_for_version(version)
    if refinement_metadata is not None and refinement_review_record is None:
        raise HTTPException(
            status_code=409,
            detail="Staged refinement drafts require a promotion_ready review record before acceptance.",
        )
    if refinement_review_record is not None:
        try:
            accepted_result = await accept_staged_refinement_build_record(
                app_id=app_id,
                draft_build_record_id=artifact_version_id,
                review_record=refinement_review_record,
                request_id=refinement_review_record.request_id,
                record_store=artifact_store,
                accepted_by=principal.user_id,
                notes=body.notes if body is not None else None,
                allow_validation_override=allow_validation_override,
            )
        except (AcceptedStagedAppBundleBuildRecordError, ValueError) as exc:
            logger.warning("accept_staged_refinement conflict app=%s version=%s: %s", app_id, artifact_version_id, exc)
            raise HTTPException(status_code=409, detail="Conflict accepting artifact version.") from exc
        accepted = accepted_result.build_record
    else:
        accepted = await artifact_store.accept_build_record(app_id=app_id, build_record_id=artifact_version_id)
    if accepted is None:
        raise HTTPException(status_code=404, detail=f"Artifact version not found: {artifact_version_id}")

    for session in await artifact_store.list_refinement_sessions(
        app_id=app_id,
        result_build_record_id=artifact_version_id,
        limit=20,
    ):
        await artifact_store.update_refinement_session(
            app_id=app_id,
            session_id=session.id,
            status=RefinementSessionStatus.ACCEPTED,
            ended_at=datetime.now(UTC),
        )

    payload = await _build_artifact_review_payload(
        app_id=app_id,
        version=accepted,
        artifact_store=artifact_store,
    )
    return {"accepted": True, "app_id": app_id, **payload}


@app.post("/api/studio/build/artifacts/{artifact_version_id}/reject")
async def reject_build_artifact_version(
    artifact_version_id: str,
    app_id: str | None = None,
    principal: UserPrincipal = Depends(require_user_scope),
):
    validate_path_id(artifact_version_id, "artifact_version_id")
    app_id, _ = _resolve_studio_scope(principal, app_id=app_id)
    artifact_store = get_artifact_store()
    version = await artifact_store.get_build_record(app_id=app_id, build_record_id=artifact_version_id)
    if not version:
        raise HTTPException(status_code=404, detail=f"Artifact version not found: {artifact_version_id}")
    if version.lifecycle_status != ArtifactLifecycleStatus.DRAFT:
        raise HTTPException(status_code=409, detail="Only draft artifact versions can be rejected.")

    rejected = await artifact_store.reject_artifact_version(
        app_id=app_id,
        artifact_version_id=artifact_version_id,
        reason="Rejected in build review.",
    )
    if not rejected:
        raise HTTPException(status_code=500, detail="Artifact version could not be rejected.")

    for session in await artifact_store.list_refinement_sessions(
        app_id=app_id,
        result_build_record_id=artifact_version_id,
        limit=20,
    ):
        await artifact_store.update_refinement_session(
            app_id=app_id,
            session_id=session.id,
            status=RefinementSessionStatus.REJECTED,
            ended_at=datetime.now(UTC),
        )

    refreshed = await artifact_store.get_build_record(app_id=app_id, build_record_id=artifact_version_id)
    payload = await _build_artifact_review_payload(
        app_id=app_id,
        version=refreshed,
        artifact_store=artifact_store,
    )
    return {"rejected": True, "app_id": app_id, **payload}


class BuildArtifactPromotionRequest(BaseModel):
    build_registry_id: str | None = Field(
        default=None,
        description="Optional app_registry record id to mark active after the artifact is restored.",
    )
    allow_validation_override: bool = Field(
        default=False,
        description="Allow promoting skipped or pending validation with an explicit operator override.",
    )


@app.post("/api/studio/build/artifacts/{artifact_version_id}/promote")
async def promote_build_artifact_version(
    artifact_version_id: str,
    body: BuildArtifactPromotionRequest | None = None,
    app_id: str | None = None,
    principal: UserPrincipal = Depends(require_user_scope),
):
    validate_path_id(artifact_version_id, "artifact_version_id")
    app_id, user_id = _resolve_studio_scope(principal, app_id=app_id)
    artifact_store = get_artifact_store()
    version = await artifact_store.get_build_record(app_id=app_id, build_record_id=artifact_version_id)
    if not version:
        raise HTTPException(status_code=404, detail=f"Artifact version not found: {artifact_version_id}")
    if version.lifecycle_status != ArtifactLifecycleStatus.CURRENT:
        raise HTTPException(status_code=409, detail="Only accepted current artifact versions can be promoted.")
    if version.build_family != "app_bundle":
        raise HTTPException(status_code=400, detail=f"Unsupported artifact kind for restore: {version.build_family}")
    allow_validation_override = bool(body.allow_validation_override) if body is not None else False
    _enforce_artifact_validation_gate(
        version,
        action="promoted",
        allow_validation_override=allow_validation_override,
    )

    zip_path = _artifact_bundle_path_from_version(version)
    if zip_path is None:
        raise HTTPException(
            status_code=400,
            detail="This artifact version has no restorable file path. Only versions generated after artifact persistence was added can be promoted.",
        )
    if not version.files_manifest:
        raise HTTPException(
            status_code=400,
            detail="Artifact versions must include a file manifest before promotion.",
        )
    metadata = _version_metadata(version)
    promotion_build_registry_id = (
        str((body.build_registry_id if body is not None else None) or metadata.get("build_registry_id") or "").strip()
        or None
    )
    app_registry_result = None
    app_registry_service = None
    if promotion_build_registry_id:
        app_registry_service = _get_app_registry_service()
        registry_record = (
            await app_registry_service.get_app_record(build_registry_id=promotion_build_registry_id)
        ).get("app")
        if not registry_record:
            raise HTTPException(
                status_code=404,
                detail=f"App registry record not found: {promotion_build_registry_id}",
            )
        if str(registry_record.get("app_id") or "").strip() != app_id:
            raise HTTPException(
                status_code=409,
                detail="App registry record does not match the artifact app id.",
            )
        if str(registry_record.get("lifecycle_state") or "").strip() != "review":
            raise HTTPException(
                status_code=409,
                detail="Only app registry records in review can be promoted.",
            )
    refinement_metadata = _refinement_metadata_from_version(version)

    target_dir = _resolve_bundle_restore_target(version)
    try:
        restore_summary = _restore_bundle_to_target(zip_path=zip_path, target_dir=target_dir)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail="Failed to promote artifact") from exc

    for session in await artifact_store.list_refinement_sessions(
        app_id=app_id,
        result_build_record_id=artifact_version_id,
        limit=20,
    ):
        await artifact_store.update_refinement_session(
            app_id=app_id,
            session_id=session.id,
            status=RefinementSessionStatus.PROMOTED,
            ended_at=datetime.now(UTC),
        )
    if promotion_build_registry_id and app_registry_service is not None:
        try:
            app_registry_result = await app_registry_service.promote_build(
                build_registry_id=promotion_build_registry_id,
                promoted_by=user_id,
            )
        except ValueError as exc:
            logger.warning("promote_build conflict app=%s build=%s: %s", app_id, promotion_build_registry_id, exc)
            raise HTTPException(status_code=409, detail="Conflict promoting build.") from exc

    payload = await _build_artifact_review_payload(
        app_id=app_id,
        version=version,
        artifact_store=artifact_store,
    )
    logger.info(
        "Promoted app_id=%s artifact_version_id=%s (%s/%s) restored=%s skipped=%s refinement_request_id=%s",
        app_id,
        artifact_version_id,
        version.build_family,
        version.build_key,
        len(restore_summary["restored"]),
        len(restore_summary["skipped"]),
        str((refinement_metadata or {}).get("request_id") or "").strip() or None,
    )
    return {
        "promoted": True,
        "app_id": app_id,
        "artifact_version_id": artifact_version_id,
        "build_family": version.build_family,
        "build_key": version.build_key,
        "target_path": str(target_dir),
        "restart_required": True,
        "restored_files": restore_summary["restored"],
        "skipped_files": restore_summary["skipped"],
        "app_registry": app_registry_result,
        **payload,
    }


class BuildRevertRequest(BaseModel):
    artifact_version_id: str = Field(..., description="Artifact version to restore as the active app state")


@app.post("/api/studio/build/revert")
async def revert_to_artifact_version(
    body: BuildRevertRequest,
    app_id: str | None = None,
    principal: UserPrincipal = Depends(require_user_scope),
):
    """Restore a previously generated artifact version as the active app state.

    Extracts the stored bundle zip to the appropriate directory under the active
    app root. Returns restart_required=True because the platform host reads from
    disk at startup.
    """
    from pathlib import Path as _Path

    app_id, _ = _resolve_studio_scope(principal, app_id=app_id)
    artifact_store = get_artifact_store()

    version = await artifact_store.get_build_record(
        app_id=app_id,
        build_record_id=body.artifact_version_id,
    )
    if not version:
        raise HTTPException(status_code=404, detail=f"Artifact version not found: {body.artifact_version_id}")

    artifact_path = (version.commit_metadata.metadata or {}).get("artifact_path")
    if not artifact_path:
        raise HTTPException(
            status_code=400,
            detail="This artifact version has no restorable file path. Only versions generated after revert support was added can be restored.",
        )

    zip_path = _Path(artifact_path)
    if not zip_path.exists():
        raise HTTPException(
            status_code=400,
            detail=f"Artifact file no longer exists on disk: {artifact_path}",
        )

    app_root = resolve_app_root()

    if version.build_family == "workflow_bundle":
        target_dir = candidate_app_workflows_roots(app_root)[0]
        target_dir.mkdir(parents=True, exist_ok=True)
    elif version.build_family == "app_bundle":
        target_dir = app_root
    else:
        raise HTTPException(status_code=400, detail=f"Cannot revert artifact kind: {version.build_family}")

    try:
        _restore_bundle_to_target(zip_path=zip_path, target_dir=target_dir)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail="Failed to extract artifact") from exc

    logger.info(
        "Reverted app_id=%s to artifact_version_id=%s (%s/%s)",
        app_id,
        body.artifact_version_id,
        version.build_family,
        version.build_key,
    )

    return {
        "reverted": True,
        "artifact_version_id": body.artifact_version_id,
        "build_family": version.build_family,
        "build_key": version.build_key,
        "target_path": str(target_dir),
        "restart_required": True,
    }


@app.get("/api/studio/build")
async def get_build_surface(
    app_id: str | None = None,
    principal: UserPrincipal = Depends(require_user_scope),
):
    app_id, _ = _resolve_studio_scope(principal, app_id=app_id)
    app_root = resolve_app_root()
    missing_surfaces = get_missing_studio_surfaces(app_root)
    if missing_surfaces:
        raise HTTPException(
            status_code=500,
            detail=f"Build surface is missing required surfaces: {', '.join(missing_surfaces)}",
        )

    try:
        record = (await _get_app_registry_service().get_app_record(app_id=app_id)).get("app")
        if not isinstance(record, dict):
            raise HTTPException(status_code=404, detail=f"App record not found: {app_id}")
        build_state = await load_build_state_from_db(app_id)
        home_summary = build_app_overview_summary(
            app_root,
            surface="shell-build",
            local_only=True,
            app_record=record,
        )
        home_summary["studio"] = {**home_summary["studio"], "surface": "shell-build", "route": f"/apps/{app_id}/build"}
        return {
            **home_summary,
            "build": build_build_section(home_summary, build_state),
        }
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail="Failed to build build summary") from exc


class BuildSaveRequest(BaseModel):
    request_text: str = Field(..., description="Persisted build request text")
    request_kind: Literal["greenfield_app", "brownfield_app", "refinement"] | None = Field(
        None,
        description="High-level request kind for the current build draft",
    )
    change_class: Literal["patch", "design", "feature", "core"] | None = Field(
        None,
        description="Refinement change class when the current draft is a refinement request",
    )


@app.put("/api/studio/build")
async def save_build_surface(
    request: BuildSaveRequest,
    app_id: str | None = None,
    principal: UserPrincipal = Depends(require_user_scope),
):
    app_id, user_id = _resolve_studio_scope(principal, app_id=app_id)
    if request.change_class and request.request_kind != "refinement":
        raise HTTPException(status_code=400, detail="change_class is only valid when request_kind is 'refinement'")

    app_root = resolve_app_root()
    missing_surfaces = get_missing_studio_surfaces(app_root)
    if missing_surfaces:
        raise HTTPException(
            status_code=500,
            detail=f"Build surface is missing required surfaces: {', '.join(missing_surfaces)}",
        )

    try:
        record_result = await _get_app_registry_service().ensure_status_for_app(
            app_id=app_id,
            owner_user_id=user_id,
            status="draft",
            default_name=app_id,
        )
        record = record_result.get("app")
        build_state = await save_build_state_to_db(
            app_id,
            request_text=request.request_text,
            request_kind=request.request_kind,
            change_class=request.change_class,
        )
        home_summary = build_app_overview_summary(
            app_root,
            surface="shell-build",
            local_only=True,
            app_record=record if isinstance(record, dict) else None,
        )
        home_summary["studio"] = {**home_summary["studio"], "surface": "shell-build", "route": f"/apps/{app_id}/build"}
        return {
            **home_summary,
            "build": build_build_section(home_summary, build_state),
        }
    except HTTPException:
        raise
    except ValueError as exc:
        logger.warning("build state validation error app=%s: %s", app_id, exc)
        raise HTTPException(status_code=400, detail="Invalid build state parameters.") from exc
    except Exception as exc:
        logger.exception("Failed to persist build state")
        raise HTTPException(status_code=500, detail="Failed to persist build state") from exc


class WorkflowTriggerRequest(BaseModel):
    workflow_id: str | None = None
    trigger_source: str = "chat"
    journey_id: str | None = None
    context_variables: dict[str, Any] = Field(default_factory=dict)
    trigger_payload: dict[str, Any] = Field(default_factory=dict)
    action_id: str | None = None
    artifact_key: str | None = None
    app_id: str | None = None
    user_id: str | None = None


@app.post("/api/workflows/trigger")
async def trigger_workflow(
    body: WorkflowTriggerRequest,
    principal: UserPrincipal = Depends(require_user_scope),
):
    app_id, user_id = _resolve_studio_scope(principal, app_id=body.app_id, user_id=body.user_id)
    trigger_payload = dict(body.trigger_payload or {})
    orchestration_control = get_orchestration_control_harness()
    refinement_request = None
    refinement_decision = None
    harness_decision = None
    resolved_change_class = None
    resolved_artifact_kind = None
    resolved_artifact_version_id = None
    coding_request = None
    coding_result = None
    coding_session = None
    contract_surface_plan = None
    surface_result = None
    persisted_change_request_id = str(trigger_payload.get("change_request_id") or "").strip() or None
    persisted_revision_id = str(trigger_payload.get("revision_id") or "").strip() or None

    if body.trigger_source == "refinement":
        maybe_action = trigger_payload.get("harness_action")
        if isinstance(maybe_action, dict) and (
            not str(trigger_payload.get("change_request_id") or "").strip()
            or not str(trigger_payload.get("revision_id") or "").strip()
        ):
            try:
                session_snapshot = await get_session_router().get_session_snapshot(
                    app_id=app_id,
                    user_id=user_id,
                )
            except Exception as session_err:
                logger.warning("Failed to load prelaunch revision session state: %s", session_err)
                session_snapshot = None
            if isinstance(session_snapshot, dict):
                if not str(trigger_payload.get("change_request_id") or "").strip():
                    active_change_request_id = str(
                        session_snapshot.get("active_change_request_id") or ""
                    ).strip()
                    if active_change_request_id:
                        trigger_payload["change_request_id"] = active_change_request_id
                if not str(trigger_payload.get("revision_id") or "").strip():
                    active_revision_id = str(
                        session_snapshot.get("active_revision_id") or ""
                    ).strip()
                    if active_revision_id:
                        trigger_payload["revision_id"] = active_revision_id
        try:
            refinement_request = orchestration_control.request_from_payload(
                payload=trigger_payload,
                app_id=app_id,
                user_id=user_id,
                requested_workflow_id=body.workflow_id,
                default_source_surface=(
                    str((body.context_variables or {}).get("screen") or "").strip() or None
                ),
            )
        except ValidationError as exc:
            raise HTTPException(status_code=400, detail="Invalid refinement_request") from exc

        if refinement_request is None:
            raise HTTPException(
                status_code=400,
                detail="refinement triggers require trigger_payload.refinement_request.",
            )
        persisted_revision_id = (
            str(refinement_request.extra.get("revision_id") or "").strip() or persisted_revision_id
        )
        persisted_change_request_id = (
            str(refinement_request.extra.get("change_request_id") or "").strip()
            or persisted_change_request_id
        )

        try:
            refinement_decision = await orchestration_control.route_refinement_request(refinement_request)
        except Exception as exc:
            logger.error("refinement_classification_failed: %s", exc, exc_info=True)
            raise HTTPException(status_code=503, detail="Refinement classification unavailable") from exc
        harness_decision = orchestration_control.build_harness_decision(refinement_decision)
        if (
            orchestration_control.contract_surface_enabled()
            and refinement_decision.change_intent.change_class.value in {"feature", "design"}
            and refinement_request.build_family in {"app_bundle", "workflow_bundle"}
        ):
            try:
                contract_surface_plan, harness_decision = (
                    await orchestration_control.prepare_contract_surface_request(
                        refinement_request=refinement_request,
                        routing_decision=refinement_decision,
                    )
                )
                if contract_surface_plan is not None:
                    surface_result = await orchestration_control.execute_surface_plan(
                        plan=contract_surface_plan,
                        refinement_request=refinement_request,
                        routing_decision=refinement_decision,
                    )
            except Exception as exc:
                logger.warning("contract_surface_planner_failed, falling back to workflow: %s", exc)
                contract_surface_plan = None
                surface_result = None
                harness_decision = orchestration_control.build_harness_decision(refinement_decision)
        resolved_change_class = refinement_decision.change_intent.change_class.value
        resolved_artifact_kind = refinement_request.build_family
        resolved_artifact_version_id = refinement_request.build_record_id
        if orchestration_control.coding_enabled() and isinstance(trigger_payload.get("coding_request"), dict):
            coding_request = orchestration_control.build_coding_request(
                refinement_request=refinement_request,
                routing_decision=refinement_decision,
                payload=trigger_payload.get("coding_request"),
            )
            if coding_request is not None:
                try:
                    coding_request, coding_decision = await orchestration_control.prepare_coding_request(coding_request)
                    harness_decision = coding_decision
                    if coding_request is not None:
                        coding_result = await orchestration_control.execute_coding_request(coding_request)
                        harness_decision = orchestration_control.build_coding_result_decision(coding_request)
                except Exception as exc:
                    logger.error("coding_worker_failed: %s", exc, exc_info=True)
                    raise HTTPException(status_code=503, detail="Coding worker unavailable") from exc
        trigger_payload = {
            "refinement_request": refinement_request.model_dump(mode="python"),
        }
        if persisted_change_request_id is not None:
            trigger_payload["change_request_id"] = persisted_change_request_id
        if persisted_revision_id is not None:
            trigger_payload["revision_id"] = persisted_revision_id

    artifact_store = get_artifact_store() if refinement_request is not None and refinement_decision is not None else None

    if (
        persisted_change_request_id is None
        and refinement_request is not None
        and refinement_decision is not None
        and artifact_store is not None
    ):
        try:
            persisted_change_request = await artifact_store.create_change_request(
                app_id=app_id,
                build_family=refinement_request.build_family,
                build_key=body.artifact_key or refinement_request.normalized_build_key(),
                build_record_id=refinement_request.build_record_id,
                raw_user_request=refinement_request.raw_user_request,
                classification=ChangeClassification(refinement_decision.change_intent.change_class.value),
                refinement_request=refinement_request.model_dump(mode="python"),
                change_intent=refinement_decision.change_intent.model_dump(mode="python"),
                impact_set=refinement_decision.impact_set.model_dump(mode="python"),
                router_decision={
                    "workflow_id": refinement_decision.workflow_id,
                    "workflow_sequence": refinement_decision.workflow_sequence,
                    "requested_workflow_id": body.workflow_id,
                    "explanation": refinement_decision.explanation,
                    "is_full_restart": refinement_decision.is_full_restart,
                    "rerouted_by_dependency": False,
                    "execution_mode": (
                        "coding_worker"
                        if coding_result is not None and coding_result.eligible
                        else "harness_decision"
                        if harness_decision is not None
                        and (
                            harness_decision.requires_confirmation
                            or harness_decision.decision_type in {"clarify_scope", "fallback_workflow"}
                        )
                        else "workflow"
                    ),
                    "harness_decision": (
                        harness_decision.model_dump(mode="python") if harness_decision is not None else None
                    ),
                },
                created_by_user_id=user_id,
            )
            persisted_change_request_id = persisted_change_request.id
        except Exception as persist_err:
            logger.warning("Failed to persist ChangeRequest: %s", persist_err)
    if (
        persisted_change_request_id is not None
        and refinement_request is not None
        and refinement_decision is not None
    ):
        try:
            await orchestration_control.persist_revision_invalidation(
                refinement_request=refinement_request,
                routing_decision=refinement_decision,
                change_request_id=persisted_change_request_id,
                artifact_store=artifact_store,
            )
        except Exception as invalidation_err:
            logger.warning("Failed to persist artifact invalidation: %s", invalidation_err)
    if persisted_change_request_id is not None:
        trigger_payload["change_request_id"] = persisted_change_request_id

    confirmed_action = None
    if refinement_request is not None:
        maybe_action = refinement_request.extra.get("harness_action")
        if isinstance(maybe_action, dict):
            confirmed_action = str(maybe_action.get("action_id") or "").strip() or None

    should_return_harness_decision = (
        harness_decision is not None
        and coding_result is None
        and (
            (
                harness_decision.decision_type == "clarify_scope"
                and confirmed_action not in {"apply_proposed_scope"}
            )
            or (
                harness_decision.decision_type == "fallback_workflow"
                and confirmed_action not in {"run_recommended_workflow", "confirm_recommended_workflow"}
            )
            or (
                harness_decision.requires_confirmation
                and confirmed_action not in {"confirm_recommended_workflow", "run_recommended_workflow"}
            )
        )
    )

    if should_return_harness_decision:
        try:
            pending_workflow_id = harness_decision.recommended_workflow_id or refinement_decision.workflow_id
            pending_journey_id = (
                refinement_decision.workflow_sequence if refinement_decision is not None else body.journey_id
            )
            pending_revision_id = (
                persisted_revision_id
                or str((refinement_decision.context_seed or {}).get("revision_id") or "").strip()
                or uuid4().hex
            )
            trigger_payload["revision_id"] = pending_revision_id
            pending_decision = RoutingDecision(
                workflow_id=pending_workflow_id,
                requested_workflow_id=body.workflow_id or pending_workflow_id,
                journey_id=pending_journey_id,
                context_seed=dict(refinement_decision.context_seed or {}),
                explanation=refinement_decision.explanation if refinement_decision is not None else "",
                is_full_restart=bool(refinement_decision.is_full_restart),
                rerouted_by_dependency=False,
                lifecycle_state=(
                    SessionLifecycle.STALE
                    if refinement_decision is not None and refinement_decision.is_full_restart
                    else SessionLifecycle.ACTIVE
                ),
            )
            if persisted_change_request_id:
                pending_decision.context_seed["change_request_id"] = persisted_change_request_id
            pending_decision.context_seed["revision_id"] = pending_revision_id
            pending_harness_decision = PendingHarnessDecision(
                decision_id=(
                    persisted_change_request_id
                    or pending_revision_id
                    or uuid4().hex
                ),
                decision_type=harness_decision.decision_type,
                message=harness_decision.message,
                rationale=harness_decision.rationale,
                confidence=float(harness_decision.confidence),
                recommended_workflow_id=harness_decision.recommended_workflow_id,
                selected_paths=list(harness_decision.selected_paths or []),
                clarification_question=harness_decision.clarification_question,
                change_request_id=persisted_change_request_id,
                revision_id=pending_revision_id,
                requires_confirmation=bool(harness_decision.requires_confirmation),
                trigger_source=body.trigger_source,
                requested_workflow_id=body.workflow_id,
                journey_id=pending_journey_id,
                context_variables=dict(body.context_variables or {}),
                trigger_payload=dict(trigger_payload or {}),
                actions=[
                    PendingDecisionAction(
                        action_id=action.action_id,
                        label=action.label,
                        action_type=action.action_type,
                        workflow_id=action.workflow_id,
                        metadata=dict(action.metadata or {}),
                    )
                    for action in harness_decision.actions
                ],
                metadata=dict(harness_decision.metadata or {}),
            )
            pending_snapshot = await get_session_router().persist_revision_intent(
                trigger=TriggerInput(
                    app_id=app_id,
                    user_id=user_id,
                    trigger_source=body.trigger_source,
                    workflow_id=body.workflow_id,
                    journey_id=pending_journey_id,
                    context_variables=body.context_variables or {},
                    trigger_payload=trigger_payload,
                ),
                decision=pending_decision,
                pending_harness_decision=pending_harness_decision,
            )
            pending_revision_id = str(pending_snapshot.get("active_revision_id") or "").strip()
            if pending_revision_id:
                persisted_revision_id = pending_revision_id
                trigger_payload["revision_id"] = pending_revision_id
        except Exception as session_err:
            logger.warning("Failed to persist prelaunch revision intent: %s", session_err)
        return {
            "execution_mode": "harness_decision",
            "chat_id": None,
            "workflow_id": harness_decision.recommended_workflow_id or refinement_decision.workflow_id,
            "requested_workflow_id": body.workflow_id or (harness_decision.recommended_workflow_id if harness_decision else None),
            "websocket_url": None,
            "trigger_source": body.trigger_source,
            "routing_explanation": refinement_decision.explanation if refinement_decision is not None else "",
            "rerouted_by_dependency": False,
            "harness_decision": harness_decision.model_dump(mode="python"),
        }

    if coding_result is not None and coding_result.eligible:
        if artifact_store is not None and persisted_change_request_id is not None and refinement_request.build_record_id:
            try:
                session_status = {
                    "validated": RefinementSessionStatus.VALIDATED,
                    "failed": RefinementSessionStatus.FAILED,
                }.get(coding_result.status, RefinementSessionStatus.PENDING)
                coding_session = await artifact_store.create_refinement_session(
                    app_id=app_id,
                    build_record_id=refinement_request.build_record_id,
                    change_request_id=persisted_change_request_id,
                    result_build_record_id=((coding_result.metadata or {}).get("build_record_id") or (coding_result.metadata or {}).get("artifact_version_id")),
                    provider="control_plane_coding",
                    status=session_status,
                    preview_url=((coding_result.validation_result or {}).get("preview_url")),
                    metadata={
                        "coding_worker": coding_result.model_dump(mode="python"),
                        "workflow_id": refinement_decision.workflow_id,
                    },
                )
            except Exception as persist_err:
                logger.warning("Failed to persist RefinementSession: %s", persist_err)

        return {
            "execution_mode": "coding_worker",
            "chat_id": None,
            "workflow_id": refinement_decision.workflow_id,
            "requested_workflow_id": body.workflow_id or refinement_decision.workflow_id,
            "websocket_url": None,
            "trigger_source": body.trigger_source,
            "routing_explanation": refinement_decision.explanation,
            "rerouted_by_dependency": False,
            "refinement_session_id": coding_session.id if coding_session is not None else None,
            "harness_decision": harness_decision.model_dump(mode="python") if harness_decision is not None else None,
            "coding_worker": coding_result.model_dump(mode="python"),
        }

    if surface_result is not None:
        surface_session = None
        if (
            artifact_store is not None
            and persisted_change_request_id is not None
            and refinement_request.build_record_id
        ):
            try:
                surface_session_status = {
                    "success": RefinementSessionStatus.VALIDATED,
                    "partial": RefinementSessionStatus.PENDING,
                    "failed": RefinementSessionStatus.FAILED,
                }.get(surface_result.status, RefinementSessionStatus.PENDING)
                surface_session = await artifact_store.create_refinement_session(
                    app_id=app_id,
                    build_record_id=refinement_request.build_record_id,
                    change_request_id=persisted_change_request_id,
                    provider="contract_surface_regeneration",
                    status=surface_session_status,
                    metadata={
                        "surface_result": surface_result.model_dump(mode="python"),
                        "workflow_id": refinement_decision.workflow_id,
                    },
                )
            except Exception as persist_err:
                logger.warning("Failed to persist surface RefinementSession: %s", persist_err)
        return {
            "execution_mode": "surface_regeneration",
            "chat_id": None,
            "workflow_id": refinement_decision.workflow_id,
            "requested_workflow_id": body.workflow_id or refinement_decision.workflow_id,
            "websocket_url": None,
            "trigger_source": body.trigger_source,
            "routing_explanation": refinement_decision.explanation,
            "rerouted_by_dependency": False,
            "refinement_session_id": surface_session.id if surface_session is not None else None,
            "harness_decision": harness_decision.model_dump(mode="python") if harness_decision is not None else None,
            "surface_result": surface_result.model_dump(mode="python"),
        }

    try:
        launch = await prepare_routed_workflow_launch(
            workflow_id=body.workflow_id,
            app_id=app_id,
            user_id=user_id,
            trigger_source=body.trigger_source,
            journey_id=(refinement_decision.workflow_sequence if refinement_decision is not None else body.journey_id),
            context_variables=body.context_variables or {},
            trigger_payload=trigger_payload,
            extra_trigger_meta={
                "action_id": body.action_id,
                "change_class": resolved_change_class,
                "build_record_id": resolved_artifact_version_id,
                "build_family": resolved_artifact_kind,
                "workflow_sequence": refinement_decision.workflow_sequence if refinement_decision is not None else None,
            },
        )
    except ValueError as route_err:
        raise HTTPException(status_code=400, detail=str(route_err)) from route_err
    except Exception as route_err:
        logger.error("SessionRouter routing failed: %s", route_err, exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to route workflow trigger") from route_err

    resolved_workflow_id = launch.workflow_id
    routing_decision = launch.routing_decision

    if persisted_change_request_id is not None and artifact_store is not None:
        try:
            await artifact_store.update_change_request_router_decision(
                app_id=app_id,
                change_request_id=persisted_change_request_id,
                router_decision={
                    "workflow_id": resolved_workflow_id,
                    "workflow_sequence": refinement_decision.workflow_sequence if refinement_decision is not None else None,
                    "requested_workflow_id": routing_decision.requested_workflow_id,
                    "explanation": routing_decision.explanation,
                    "is_full_restart": routing_decision.is_full_restart,
                    "rerouted_by_dependency": routing_decision.rerouted_by_dependency,
                    "execution_mode": "workflow",
                    "harness_decision": (
                        harness_decision.model_dump(mode="python") if harness_decision is not None else None
                    ),
                },
            )
        except Exception as persist_err:
            logger.warning("Failed to update ChangeRequest routing decision: %s", persist_err)

    workflow_launch = await launch_prepared_workflow(launch)

    return {
        "execution_mode": "workflow",
        "chat_id": workflow_launch.chat_id,
        "workflow_id": workflow_launch.workflow_id,
        "requested_workflow_id": workflow_launch.requested_workflow_id,
        "journey_id": workflow_launch.journey_id,
        "websocket_url": workflow_launch.websocket_url,
        "trigger_source": workflow_launch.trigger_source,
        "routing_explanation": workflow_launch.routing_explanation,
        "rerouted_by_dependency": workflow_launch.rerouted_by_dependency,
        "harness_decision": harness_decision.model_dump(mode="python") if harness_decision is not None else None,
    }


app.router.routes[:] = sorted(
    app.router.routes,
    key=lambda route: (
        0
        if getattr(route, "path", None) == "/api/shell-config"
        and getattr(getattr(route, "endpoint", None), "__module__", "") == __name__
        else 1
    ),
)
