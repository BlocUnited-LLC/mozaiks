from __future__ import annotations

"""Studio management host layered on top of mozaiksai.hosts.platform.

Studio is the local/private management and create control plane used by the
CLI and by the hosted Mozaiks product. It adds Studio shell routes and
workflow triggering on top of the headless platform host.
"""

import io
import os
import stat
import zipfile
from asyncio import CancelledError, to_thread
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from difflib import unified_diff
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Literal
from uuid import uuid4

from anyio import CancelScope
from fastapi import BackgroundTasks, Depends, HTTPException, Request
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, ConfigDict, Field, StrictBool, TypeAdapter, ValidationError

from factory_app.app.modules.app_registry.backend.service import AppRegistryService
from factory_app.workflows._shared.artifact_bundle import read_artifact_bundle
from factory_app.workflows._shared.platform.review_continuation import continue_inline_app_review
from logs.logging_config import get_workflow_logger
from mozaiksai.control_plane import (
    AcceptedStagedAppBundleBuildRecordError,
    RefinementRequest,
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
from mozaiksai.control_plane.implementations.coding_worker import resolve_coding_validation_strategy
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
from mozaiksai.core.artifacts.content_store import (
    ContentNotFoundError,
    read_verified_artifact_bundle,
)
from mozaiksai.core.auth import UserPrincipal, require_user_scope
from mozaiksai.core.auth.anonymous_access import ANONYMOUS_PROVENANCE, STUDIO_PUBLIC_MESSAGE
from mozaiksai.core.auth.dependencies import validate_path_id
from mozaiksai.core.dashboard import load_dashboard_manifest
from mozaiksai.core.data.persistence import ConnectorStore
from mozaiksai.core.metrics import (
    OwnerAnalyticsService,
    PeriodError,
    PeriodWindow,
    resolve_period,
)
from mozaiksai.core.metrics.funnels import FunnelDef
from mozaiksai.core.runtime.app.metrics_loader import (
    MetricsConfigLoadError,
    load_metrics_config,
)
from mozaiksai.core.runtime.app.paths import app_bundle_workspace_path
from mozaiksai.core.runtime.app.studio_summary import (
    build_app_overview_summary,
    build_apps_summary,
    build_build_section,
    build_integrations_summary,
    get_missing_studio_surfaces,
    load_build_state_from_db,
    save_build_state_to_db,
)
from mozaiksai.core.sandbox.preview_sessions import preview_sessions_lifespan
from mozaiksai.core.secrets.contract import is_secret_contract_path, validate_secret_contract_text
from mozaiksai.core.session.build_binding import (
    BuildIdentity,
    BuildTargetReference,
    RunBuildBinding,
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
from mozaiksai.core.session.trigger_routing import TriggerRoutingContribution
from mozaiksai.core.startup.validation import require_management_auth_posture
from mozaiksai.core.studio.scope import resolve_studio_scope
from mozaiksai.core.workflow.generator_support.connector_health import run_connector_health_check
from mozaiksai.core.workflow.generator_support.connector_service import (
    compute_connector_health,
    delete_connector,
    list_connectors,
    patch_connector,
    save_connector,
)
from mozaiksai.hosts import platform as platform_app
from mozaiksai.hosts.bootstrap import register_repo_host_bootstrap
from mozaiksai.hosts.platform import (
    build_shell_config,
    resolve_app_root,
)
from mozaiksai.hosts.routers.sandbox import create_sandbox_router
from mozaiksai.hosts.runtime import register_app_lifespan
from mozaiksai.hosts.source_path_policy import (
    authorize_http_workflow_source_paths,
    require_http_local_source_mode,
)

app = platform_app.app
register_repo_host_bootstrap(app, "studio")


def _register_management_auth_posture_check(target_app) -> None:
    """Refuse an auth posture Studio cannot serve before any other startup work.

    It wraps the whole composed lifespan, so Studio's own message (no
    ``AUTH_ANON_ACCESS=public`` among the choices) comes before the runtime's
    generic startup checks. Request-level refusal: :func:`require_studio_user`.
    """
    existing_lifespan = target_app.router.lifespan_context

    @asynccontextmanager
    async def _management_auth_posture_lifespan(app_instance):
        require_management_auth_posture()
        async with existing_lifespan(app_instance):
            yield

    target_app.router.lifespan_context = _management_auth_posture_lifespan


_register_management_auth_posture_check(app)
register_app_lifespan(app, preview_sessions_lifespan)
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
    if not is_secret_contract_path(relative_path) and any(term in lowered for term in _RESTORE_SECRET_PATH_TERMS):
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


def _decode_text_bundle_entries(zip_source: Path | bytes) -> tuple[dict[str, str], list[str]]:
    files: dict[str, str] = {}
    skipped: list[str] = []
    total_bytes = 0
    entries: list[tuple[str, bytes]] = []

    with zipfile.ZipFile(io.BytesIO(zip_source) if isinstance(zip_source, bytes) else zip_source, "r") as archive:
        for info in archive.infolist():
            safe_name = _normalize_bundle_entry_name(info.filename)
            if safe_name is None:
                skipped.append(f"{info.filename}: unsafe_path")
                continue
            if info.is_dir():
                continue
            if _zipinfo_is_symlink(info):
                skipped.append(f"{safe_name}: symlink")
                continue
            if any(name == safe_name for name, _ in entries):
                skipped.append(f"{safe_name}: duplicate_path")
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


async def _verified_app_bundle(version) -> bytes:  # noqa: ANN001
    try:
        return await read_verified_artifact_bundle(version)
    except (ValueError, ContentNotFoundError, OSError) as exc:
        raise HTTPException(
            status_code=409,
            detail=(
                "Artifact archive identity or content could not be verified. "
                "Save a canonical app bundle before using this version."
            ),
        ) from exc


async def _bundle_source_for_review(version) -> bytes | Path | None:  # noqa: ANN001
    if version.build_family == "app_bundle":
        return await _verified_app_bundle(version)
    return _artifact_bundle_path_from_version(version)


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
) -> None:
    if version.validation_status == ArtifactValidationStatus.FAILED:
        raise HTTPException(
            status_code=409,
            detail=f"Artifact cannot be {action}; validation_status='failed'.",
        )
    if version.validation_status == ArtifactValidationStatus.PASSED:
        if action in {"promoted", "restored", "exported"} and version.app_validation_status != "passed":
            raise HTTPException(
                status_code=409,
                detail="This candidate needs passed whole-app build validation before export or activation.",
            )
        return
    raise HTTPException(
        status_code=409,
        detail=(
            f"Artifact cannot be {action}; validation_status='passed' is required."
        ),
    )


def _resolve_bundle_restore_target(version) -> Path:  # noqa: ANN001
    if version.build_family != "app_bundle":
        raise HTTPException(status_code=400, detail=f"Unsupported artifact kind for restore: {version.build_family}")
    target_id = TypeAdapter(BuildIdentity).validate_python(version.app_id)
    version_id = TypeAdapter(BuildIdentity).validate_python(version.id)
    workspace_root = Path(os.getenv("MOZAIKS_WORKSPACES_PATH", ".local/workspaces")).resolve()
    target = (workspace_root / target_id / version_id).resolve()
    active_root = resolve_app_root().resolve()
    if (
        not target.is_relative_to(workspace_root)
        or target.is_relative_to(active_root)
        or active_root.is_relative_to(target)
    ):
        raise HTTPException(status_code=409, detail="Build promotion cannot modify the active host workspace")
    return target


def _restore_bundle_to_target(*, bundle_bytes: bytes, target_dir: Path, workspace_layout: bool = False) -> dict[str, list[str]]:
    restored: list[str] = []
    skipped: list[str] = []
    target_dir.mkdir(parents=True, exist_ok=True)
    target_root = target_dir.resolve()

    with zipfile.ZipFile(io.BytesIO(bundle_bytes), "r") as archive:
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
            if is_secret_contract_path(safe_name):
                try:
                    validate_secret_contract_text(archive.read(info.filename))
                except ValueError as exc:
                    raise HTTPException(status_code=400, detail="Artifact contains an invalid names-only secret contract") from exc
            if contains_symlink_component(target_root, safe_name):
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
            if workspace_layout:
                safe_name = app_bundle_workspace_path(safe_name)
            if contains_symlink_component(target_root, safe_name):
                raise HTTPException(status_code=400, detail="Restore target contains a symlinked component")
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


async def _verified_review_snapshots(*, app_id: str, version, artifact_store) -> dict[str, bytes]:  # noqa: ANN001
    """Verify app archives before changing review state; reuse these bytes in the response."""
    snapshots = {}
    if version.build_family == "app_bundle":
        snapshots[version.id] = await _verified_app_bundle(version)
    if version.parent_build_record_id:
        parent = await artifact_store.get_build_record(
            app_id=app_id, build_record_id=version.parent_build_record_id,
        )
        if parent is not None and parent.build_family == "app_bundle":
            snapshots[parent.id] = await _verified_app_bundle(parent)
    return snapshots


async def _build_artifact_review_payload(
    *,
    app_id: str,
    version,
    artifact_store,
    verified_bundle_snapshots: dict[str, bytes] | None = None,
) -> dict[str, Any]:  # noqa: ANN001
    # Review mutations reuse the selected and parent records' verified snapshots.
    snapshots = verified_bundle_snapshots or {}
    current_zip: bytes | Path | None = snapshots.get(version.id)
    if current_zip is None:
        current_zip = await _bundle_source_for_review(version)
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
        parent_zip: bytes | Path | None = None
        if parent_version is not None:
            parent_zip = snapshots.get(parent_version.id)
            if parent_zip is None:
                parent_zip = await _bundle_source_for_review(parent_version)
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
    if validation_result is None:
        validation_result = _version_metadata(version).get("validation_result")

    review_status = version.lifecycle_status.value
    if latest_session is not None:
        review_status = latest_session.status.value
    elif version.lifecycle_status == ArtifactLifecycleStatus.DRAFT:
        review_status = "validated" if version.validation_status == ArtifactValidationStatus.PASSED else "needs_revision"
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
        and version.app_validation_status == "passed"
        and current_zip is not None
    )
    validation_override_required = _validation_override_required(version)
    validation_blocker = None
    if version.validation_status == ArtifactValidationStatus.FAILED:
        validation_blocker = "Validation failed. Reject or revise this artifact before accepting it."
    elif validation_override_required:
        validation_blocker = "Required checks have not passed. This draft cannot be activated yet."
    elif version.lifecycle_status == ArtifactLifecycleStatus.CURRENT and version.app_validation_status != "passed":
        validation_blocker = "Whole-app build validation must pass for this candidate before activation."

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

from factory_app.workflows._shared.platform.ask_context import studio_ask_context
from factory_app.workflows._shared.platform.build_target import bind_factory_session
from mozaiksai.core.runtime.composition.platform_hooks import get_platform_hooks
from mozaiksai.core.utils.path_containment import contains_symlink_component
from mozaiksai.core.utils.sequences import dedupe_strings


def register_studio_platform_hooks(registry: Any | None = None) -> None:
    """Install Studio's platform extension hooks.

    Called once at import time. Exposed as a named function so the wiring is
    assertable without depending on import side effects surviving a registry
    reset.
    """
    (registry or get_platform_hooks()).register_bundle(
        {"chat_session_fields": bind_factory_session, "ask_context": studio_ask_context},
        source="mozaiks.studio",
        prepend=True,
    )


register_studio_platform_hooks()


async def require_studio_user(
    principal: UserPrincipal = Depends(require_user_scope),
) -> UserPrincipal:
    """The caller of a Studio management route, never an anonymous visitor.

    Studio manages workspaces, builds and connectors for signed-in callers.
    Its startup refuses AUTH_ANON_ACCESS=public; this refuses a visitor
    principal on every request too, so a Studio composed without its
    lifespan cannot serve visitors either.
    """
    _refuse_anonymous_visitor(principal)
    return principal


def _refuse_anonymous_visitor(principal: UserPrincipal) -> None:
    if principal.auth_provenance == ANONYMOUS_PROVENANCE:
        raise HTTPException(status_code=403, detail=STUDIO_PUBLIC_MESSAGE)


def _resolve_studio_preview_scope(principal: UserPrincipal) -> tuple[str, str]:
    """Studio scope for the preview sandbox routes and websocket.

    Studio composes them from the shared router factory, whose routes take
    their principal from require_user_scope (or the websocket), so the
    visitor refusal of require_studio_user happens here.
    """
    _refuse_anonymous_visitor(principal)
    return _resolve_studio_scope(principal)


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
        default_app_id=platform_app._resolve_default_app_id(),
    )
    return scope.app_id, scope.user_id


async def _resolve_owned_app_scope(
    principal: UserPrincipal, *, app_id: str | None = None,
) -> tuple[str, str]:
    """Resolve an explicitly selected Studio app through its registry owner."""
    resolved_app_id, user_id = _resolve_studio_scope(principal, app_id=app_id)
    if str(app_id or "").strip() or str(principal.app_id or "").strip():
        await _get_owned_app_record(resolved_app_id, user_id)
    return resolved_app_id, user_id


@app.get("/api/shell-config")
async def get_studio_shell_config(request: Request):
    return await build_shell_config(surface="studio", client_scope=request.scope)


@app.get("/api/studio/dashboard")
async def get_studio_dashboard_config(
    scope: Literal["workspace", "app"] | None = None,
    app_id: str | None = None,
    principal: UserPrincipal = Depends(require_studio_user),
):
    resolved_app_id, _ = await _resolve_owned_app_scope(principal, app_id=app_id)
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
    build_registry_id: str | None = None,
    principal: UserPrincipal = Depends(require_studio_user),
):
    if build_registry_id is not None:
        resolved_app_id, user_id = await _resolve_studio_artifact_scope(
            principal, build_registry_id=build_registry_id, app_id=app_id,
        )
    else:
        resolved_app_id, user_id = _resolve_studio_scope(principal, app_id=app_id)
    app_root = resolve_app_root()
    missing_surfaces = get_missing_studio_surfaces(app_root)
    if missing_surfaces:
        raise HTTPException(
            status_code=500,
            detail=f"App overview is missing required surfaces: {', '.join(missing_surfaces)}",
        )

    try:
        record = (await _get_app_registry_service().get_app_record(app_id=resolved_app_id, owner_user_id=user_id)).get("app")
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
    principal: UserPrincipal = Depends(require_studio_user),
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
    model_config = {"extra": "forbid"}

    name: str | None = Field(default=None, max_length=120)
    description: str | None = Field(default=None, max_length=1000)


@app.post("/api/studio/apps")
async def create_workspace_app(
    body: CreateWorkspaceAppRequest,
    principal: UserPrincipal = Depends(require_studio_user),
):
    host_app_id, user_id = _resolve_studio_scope(principal)
    return await _get_app_registry_service().create_app_record(
        owner_user_id=user_id, name=body.name, description=body.description,
        chat_app_id=host_app_id,
    )


@app.delete("/api/studio/apps/{build_registry_id}")
async def delete_workspace_app(
    build_registry_id: str,
    principal: UserPrincipal = Depends(require_studio_user),
):
    _, user_id = _resolve_studio_scope(principal)
    try:
        result = await _get_app_registry_service().delete_app(build_registry_id=build_registry_id, owner_user_id=user_id)
        if not result.get("success"):
            raise HTTPException(status_code=404, detail="App registry record not found")
        return result
    except HTTPException:
        raise
    except ValueError as exc:
        logger.warning("delete_app validation error: %s", exc)
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Failed to delete app record")
        raise HTTPException(status_code=500, detail="Failed to delete app record") from exc


_owner_analytics_service: OwnerAnalyticsService | None = None


def _get_owner_analytics_service() -> OwnerAnalyticsService:
    global _owner_analytics_service
    if _owner_analytics_service is None:
        _owner_analytics_service = OwnerAnalyticsService()
    return _owner_analytics_service


def _analytics_period(period: str) -> PeriodWindow:
    try:
        return resolve_period(period)
    except PeriodError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _analytics_app_row(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "app_id": record.get("app_id"),
        "name": record.get("name") or record.get("app_id"),
        "lifecycle_state": record.get("lifecycle_state"),
    }


async def _get_owned_app_record(app_id: str, user_id: str) -> dict[str, Any]:
    """Resolve one app record through the ownership boundary or 404."""

    try:
        result = await _get_app_registry_service().get_app_record(
            app_id=app_id, owner_user_id=user_id
        )
        record = result.get("app")
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Studio app record lookup failed")
        raise HTTPException(status_code=500, detail="Failed to resolve app record") from exc
    if not isinstance(record, dict):
        raise HTTPException(status_code=404, detail=f"App record not found: {app_id}")
    return record


def _analytics_funnel_for_record(record: dict[str, Any]) -> FunnelDef | None:
    """Load the app-declared funnel from the app's bundle, when present.

    Registered apps carry their bundle path; the Studio host's own app falls
    back to the resolved app root. A declared-but-invalid config is treated
    as no funnel here (the bundle's own load path is where it fails closed).
    """

    roots: list[Path] = []
    bundle_path = record.get("bundle_path")
    if bundle_path:
        roots.append(Path(str(bundle_path)))
    elif record.get("app_id") == platform_app._resolve_default_app_id():
        roots.append(resolve_app_root())
    for root in roots:
        if not root.exists():
            continue
        try:
            config = load_metrics_config(root)
        except MetricsConfigLoadError:
            logger.warning(
                "ANALYTICS_METRICS_CONFIG_INVALID app_id=%s root=%s",
                record.get("app_id"),
                root,
                exc_info=True,
            )
            return None
        if config is not None:
            return config.default_funnel
    return None


@app.get("/api/studio/analytics/portfolio")
async def get_studio_analytics_portfolio(
    period: str = "30d",
    principal: UserPrincipal = Depends(require_studio_user),
):
    """World View analytics across every app the caller owns."""

    _, user_id = _resolve_studio_scope(principal)
    window = _analytics_period(period)
    try:
        records = (await _get_app_registry_service().list_apps(owner_user_id=user_id)).get(
            "apps"
        ) or []
    except Exception as exc:
        logger.exception("Analytics portfolio app listing failed")
        raise HTTPException(status_code=500, detail="Failed to list apps for analytics") from exc

    rows = [
        _analytics_app_row(record)
        for record in records
        if isinstance(record, dict) and record.get("app_id")
    ]
    try:
        return await _get_owner_analytics_service().portfolio(rows, window)
    except Exception as exc:
        logger.exception("Analytics portfolio assembly failed")
        raise HTTPException(status_code=500, detail="Failed to build portfolio analytics") from exc


@app.get("/api/studio/analytics/apps/{app_id}")
async def get_studio_analytics_app(
    app_id: str,
    period: str = "30d",
    principal: UserPrincipal = Depends(require_studio_user),
):
    """App View analytics for one owned app, including movement and funnel."""

    _, user_id = _resolve_studio_scope(principal)
    window = _analytics_period(period)
    record = await _get_owned_app_record(app_id, user_id)
    funnel = _analytics_funnel_for_record(record)
    try:
        return await _get_owner_analytics_service().app_analytics(
            _analytics_app_row(record), window, funnel=funnel
        )
    except Exception as exc:
        logger.exception("Analytics app assembly failed")
        raise HTTPException(status_code=500, detail="Failed to build app analytics") from exc


@app.get("/api/studio/analytics/apps/{app_id}/metrics/{metric_id}")
async def get_studio_analytics_metric_detail(
    app_id: str,
    metric_id: str,
    period: str = "30d",
    principal: UserPrincipal = Depends(require_studio_user),
):
    """Drill-down detail for one metric on one owned app."""

    _, user_id = _resolve_studio_scope(principal)
    window = _analytics_period(period)
    record = await _get_owned_app_record(app_id, user_id)
    try:
        peers = (await _get_app_registry_service().list_apps(owner_user_id=user_id)).get(
            "apps"
        ) or []
    except Exception:
        peers = []
    peer_rows = [
        _analytics_app_row(peer)
        for peer in peers
        if isinstance(peer, dict) and peer.get("app_id")
    ]
    try:
        return await _get_owner_analytics_service().metric_detail(
            metric_id,
            window,
            app=_analytics_app_row(record),
            peer_apps=peer_rows,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"Unknown metric: {metric_id}") from exc
    except Exception as exc:
        logger.exception("Analytics metric detail assembly failed")
        raise HTTPException(status_code=500, detail="Failed to build metric detail") from exc


@app.get("/api/studio/integrations")
async def get_app_integrations(
    app_id: str | None = None,
    principal: UserPrincipal = Depends(require_studio_user),
):
    app_id, _ = await _resolve_owned_app_scope(principal, app_id=app_id)
    return await build_integrations_summary(app_id=app_id)


@app.get("/api/studio/integrations/connectors")
async def get_integration_connectors(
    app_id: str | None = None,
    principal: UserPrincipal = Depends(require_studio_user),
):
    app_id, _ = await _resolve_owned_app_scope(principal, app_id=app_id)
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
    principal: UserPrincipal = Depends(require_studio_user),
):
    validate_path_id(app_id, "app_id")
    resolved_app_id, _ = await _resolve_owned_app_scope(principal, app_id=app_id)
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
    principal: UserPrincipal = Depends(require_studio_user),
):
    validate_path_id(app_id, "app_id")
    resolved_app_id, user_id = await _resolve_owned_app_scope(principal, app_id=app_id)
    _authorize_http_source_import(body, principal=principal)
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
    principal: UserPrincipal = Depends(require_studio_user),
):
    validate_path_id(app_id, "app_id")
    resolved_app_id, user_id = await _resolve_owned_app_scope(principal, app_id=app_id)
    _authorize_http_source_import(body, principal=principal)
    return await _start_studio_app_intelligence_index_job(
        app_id=resolved_app_id,
        user_id=user_id,
        body=body,
        background_tasks=background_tasks,
    )


@app.get("/api/studio/apps/{app_id}/context/app-intelligence/index/latest")
async def get_latest_studio_app_intelligence_index_job(
    app_id: str,
    principal: UserPrincipal = Depends(require_studio_user),
):
    validate_path_id(app_id, "app_id")
    resolved_app_id, _ = await _resolve_owned_app_scope(principal, app_id=app_id)
    job = await get_latest_app_intelligence_index_job(app_id=resolved_app_id)
    return _redact_secret_fields({"app_id": resolved_app_id, "index_job": public_app_intelligence_index_job(job)})


@app.get("/api/studio/apps/{app_id}/context/app-intelligence/index/{job_id}")
async def get_studio_app_intelligence_index_job(
    app_id: str,
    job_id: str,
    principal: UserPrincipal = Depends(require_studio_user),
):
    validate_path_id(app_id, "app_id")
    validate_path_id(job_id, "job_id")
    resolved_app_id, _ = await _resolve_owned_app_scope(principal, app_id=app_id)
    job = await get_app_intelligence_index_job(app_id=resolved_app_id, job_id=job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="App Intelligence index job not found.")
    return _redact_secret_fields({"app_id": resolved_app_id, "index_job": public_app_intelligence_index_job(job)})


@app.post("/api/studio/apps/{app_id}/context/validation/run")
async def run_studio_app_source_validation(
    app_id: str,
    body: AppSourceValidationRequest,
    principal: UserPrincipal = Depends(require_studio_user),
):
    validate_path_id(app_id, "app_id")
    if not body.confirm_execution:
        raise HTTPException(status_code=400, detail="confirm_execution=true is required to run app validation.")
    resolved_app_id, _ = await _resolve_owned_app_scope(principal, app_id=app_id)
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


def _authorize_http_source_import(
    body: AppIntelligenceIndexRequest, *, principal: UserPrincipal,
) -> None:
    """Keep server filesystem paths out of authenticated Studio requests."""
    if body.source_kind == "local_workspace":
        require_http_local_source_mode(principal)


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
                "warnings": dedupe_strings([*job.warnings, *import_result.warnings]),
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
        "warnings": dedupe_strings([*list(result.warnings or []), *list(graph_health.get("warnings") or [])]),
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
            "warnings": dedupe_strings([*list(summary.warnings), *list(graph_status.get("warnings") or [])]),
        }
    return job_readiness or {"status": "missing", "warnings": list(summary.warnings)}




@app.post("/api/studio/apps/{app_id}/context/refresh-plan")
async def create_studio_app_context_refresh_plan(
    app_id: str,
    body: AppContextRefreshPlanRequest,
    principal: UserPrincipal = Depends(require_studio_user),
):
    validate_path_id(app_id, "app_id")
    resolved_app_id, user_id = await _resolve_owned_app_scope(principal, app_id=app_id)
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
    principal: UserPrincipal = Depends(require_studio_user),
):
    validate_path_id(app_id, "app_id")
    if not body.confirm_launch:
        raise HTTPException(status_code=400, detail="confirm_launch=true is required to launch context refresh.")
    resolved_app_id, user_id = await _resolve_owned_app_scope(principal, app_id=app_id)
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
    principal: UserPrincipal = Depends(require_studio_user),
):
    validate_path_id(app_id, "app_id")
    resolved_app_id, _ = await _resolve_owned_app_scope(principal, app_id=app_id)
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
    principal: UserPrincipal = Depends(require_studio_user),
):
    validate_path_id(app_id, "app_id")
    resolved_app_id, _ = await _resolve_owned_app_scope(principal, app_id=app_id)
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
    principal: UserPrincipal = Depends(require_studio_user),
):
    app_id, user_id = await _resolve_owned_app_scope(principal, app_id=app_id)
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
    principal: UserPrincipal = Depends(require_studio_user),
):
    validate_path_id(service, "service")
    app_id, user_id = await _resolve_owned_app_scope(principal, app_id=app_id)
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
    principal: UserPrincipal = Depends(require_studio_user),
):
    validate_path_id(service, "service")
    app_id, _ = await _resolve_owned_app_scope(principal, app_id=app_id)
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
    principal: UserPrincipal = Depends(require_studio_user),
):
    validate_path_id(service, "service")
    app_id, _ = await _resolve_owned_app_scope(principal, app_id=app_id)
    result = await delete_connector(scope=ConnectorStore.SCOPE_APP, scope_id=app_id, service=service)
    if not result.get("deleted"):
        raise HTTPException(status_code=404, detail=f"Connector not found: {service}")
    return {
        "app_id": app_id,
        **result,
    }


async def _resolve_studio_artifact_scope(
    principal: UserPrincipal, *, build_registry_id: str | None, app_id: str | None = None,
) -> tuple[str, str]:
    host_app_id, user_id = _resolve_studio_scope(principal, app_id=app_id)
    try:
        reference = BuildTargetReference(build_registry_id=build_registry_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid build target") from exc
    if reference.build_registry_id is None:
        raise HTTPException(status_code=400, detail="Select a registered build target")
    result = await _get_app_registry_service().get_app_record(
        build_registry_id=reference.build_registry_id, owner_user_id=user_id,
    )
    record = result.get("app")
    if not record or record.get("chat_app_id") != host_app_id:
        raise HTTPException(status_code=404, detail="Build target not found")
    return record["app_id"], user_id


@app.get("/api/studio/build/artifacts/{artifact_version_id}/download")
async def download_build_artifact(
    artifact_version_id: str,
    build_registry_id: str,
    app_id: str | None = None,
    principal: UserPrincipal = Depends(require_studio_user),
):
    validate_path_id(artifact_version_id, "artifact_version_id")
    target_app_id, _ = await _resolve_studio_artifact_scope(
        principal, build_registry_id=build_registry_id, app_id=app_id,
    )
    version = await get_artifact_store().get_build_record(
        app_id=target_app_id, build_record_id=artifact_version_id,
    )
    if version is None:
        raise HTTPException(status_code=404, detail="Artifact not found")
    try:
        binding = RunBuildBinding.model_validate({
            key: _version_metadata(version).get(key) for key in RunBuildBinding.model_fields
        })
    except ValueError as exc:
        raise HTTPException(status_code=409, detail="Artifact has no verified build identity") from exc
    if binding.build_registry_id != build_registry_id or binding.target_app_id != target_app_id:
        raise HTTPException(status_code=409, detail="Artifact identity does not match its build target")
    if version.build_family == "app_bundle":
        _enforce_artifact_validation_gate(version, action="exported")
        return Response(
            await _verified_app_bundle(version), media_type="application/zip",
            headers={"Content-Disposition": f'attachment; filename="{target_app_id}-{artifact_version_id}.zip"'},
        )
    zip_path = _artifact_bundle_path_from_version(version)
    if zip_path is None or not zip_path.is_file():
        raise HTTPException(status_code=404, detail="Artifact archive is unavailable")
    return FileResponse(zip_path, media_type="application/zip", filename=f"{target_app_id}-{artifact_version_id}.zip")


@app.get("/api/studio/build/history")
async def get_build_history(
    principal: UserPrincipal = Depends(require_studio_user),
    app_id: str | None = None,
    build_registry_id: str | None = None,
    build_family: str | None = None,
    build_key: str | None = None,
    build_record_id: str | None = None,
    limit: int = 25,
):
    """Return recent artifact versions and change requests for the current workspace."""
    app_id, _ = await _resolve_studio_artifact_scope(principal, app_id=app_id, build_registry_id=build_registry_id)
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
    build_registry_id: str | None = None,
    principal: UserPrincipal = Depends(require_studio_user),
):
    validate_path_id(artifact_version_id, "artifact_version_id")
    host_app_id, _ = _resolve_studio_scope(principal, app_id=app_id)
    app_id, owner_user_id = await _resolve_studio_artifact_scope(principal, app_id=host_app_id, build_registry_id=build_registry_id)
    artifact_store = get_artifact_store()
    version = await artifact_store.get_build_record(
        app_id=app_id,
        build_record_id=artifact_version_id,
    )
    if not version:
        raise HTTPException(status_code=404, detail=f"Artifact version not found: {artifact_version_id}")
    if version.build_family not in {"app_bundle", "workflow_bundle"}:
        raise HTTPException(status_code=400, detail=f"Unsupported artifact kind for bundle workbench: {version.build_family}")

    zip_source = await _bundle_source_for_review(version)
    if zip_source is None:
        raise HTTPException(
            status_code=400,
            detail="This artifact version does not have a bundle path that the build surface can inspect.",
        )

    try:
        generated_files, skipped_files = _decode_text_bundle_entries(zip_source)
    except Exception as exc:
        raise HTTPException(status_code=500, detail="Failed to load artifact bundle") from exc

    review_payload = await _build_artifact_review_payload(
        app_id=app_id,
        version=version,
        artifact_store=artifact_store,
    )
    app_record = (await _get_app_registry_service().get_app_record(
        app_id=app_id, owner_user_id=owner_user_id,
    )).get("app") or {}
    workbench_title = str(app_record.get("name") or "").strip() or (
        "Saved app" if version.build_family == "app_bundle" else "Saved workflow"
    )

    return {
        "app_id": app_id,
        "artifact_version_id": version.id,
        "build_family": version.build_family,
        "build_key": version.build_key,
        "source_workflow": version.source_workflow,
        "bundle_path": _version_metadata(version).get("artifact_path"),
        "generated_files": generated_files,
        "skipped_files": skipped_files,
        "workbench_ui": {"component": "AppWorkbench", "workflow_name": "AppGenerator"},
        "workbench": {
            "app_id": host_app_id,
            "target_app_id": app_id,
            "build_registry_id": build_registry_id,
            "title": workbench_title,
            "artifact_version_id": version.id,
            "build_family": version.build_family,
            "build_key": version.build_key,
            "generated_files": generated_files,
            "validation_result": _version_metadata(version).get("validation_result") or _version_metadata(version).get("app_validation_result"),
            "app_validation_status": version.app_validation_status or "pending",
            "app_validation_strategy_used": version.app_validation_strategy,
            "integration_test_result": _version_metadata(version).get("app_bundle_acceptance"),
        },
        "review": review_payload["review"],
        "refinement_session": review_payload["refinement_session"],
        "change_request": review_payload["change_request"],
    }


@app.get("/api/studio/build/artifacts/{artifact_version_id}/review")
async def get_build_artifact_review(
    artifact_version_id: str,
    app_id: str | None = None,
    build_registry_id: str | None = None,
    principal: UserPrincipal = Depends(require_studio_user),
):
    validate_path_id(artifact_version_id, "artifact_version_id")
    app_id, _ = await _resolve_studio_artifact_scope(principal, app_id=app_id, build_registry_id=build_registry_id)
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
    model_config = ConfigDict(extra="forbid")
    notes: str | None = Field(default=None, max_length=2000)


@app.post("/api/studio/build/artifacts/{artifact_version_id}/accept")
async def accept_build_artifact_version(
    artifact_version_id: str,
    body: BuildArtifactAcceptanceRequest | None = None,
    app_id: str | None = None,
    build_registry_id: str | None = None,
    principal: UserPrincipal = Depends(require_studio_user),
):
    validate_path_id(artifact_version_id, "artifact_version_id")
    app_id, _ = await _resolve_studio_artifact_scope(principal, app_id=app_id, build_registry_id=build_registry_id)
    artifact_store = get_artifact_store()
    version = await artifact_store.get_build_record(app_id=app_id, build_record_id=artifact_version_id)
    if not version:
        raise HTTPException(status_code=404, detail=f"Artifact version not found: {artifact_version_id}")
    if version.lifecycle_status == ArtifactLifecycleStatus.ARCHIVED:
        raise HTTPException(status_code=409, detail="Rejected artifact versions cannot be accepted.")
    if version.lifecycle_status != ArtifactLifecycleStatus.DRAFT:
        raise HTTPException(status_code=409, detail="Only draft artifact versions can be accepted.")
    _enforce_artifact_validation_gate(
        version,
        action="accepted",
    )
    verified_bundle_snapshots = await _verified_review_snapshots(
        app_id=app_id, version=version, artifact_store=artifact_store,
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
        verified_bundle_snapshots=verified_bundle_snapshots,
    )
    return {"accepted": True, "app_id": app_id, **payload}


@app.post("/api/studio/build/artifacts/{artifact_version_id}/reject")
async def reject_build_artifact_version(
    artifact_version_id: str,
    app_id: str | None = None,
    build_registry_id: str | None = None,
    principal: UserPrincipal = Depends(require_studio_user),
):
    validate_path_id(artifact_version_id, "artifact_version_id")
    app_id, _ = await _resolve_studio_artifact_scope(principal, app_id=app_id, build_registry_id=build_registry_id)
    artifact_store = get_artifact_store()
    version = await artifact_store.get_build_record(app_id=app_id, build_record_id=artifact_version_id)
    if not version:
        raise HTTPException(status_code=404, detail=f"Artifact version not found: {artifact_version_id}")
    if version.lifecycle_status != ArtifactLifecycleStatus.DRAFT:
        raise HTTPException(status_code=409, detail="Only draft artifact versions can be rejected.")
    verified_bundle_snapshots = await _verified_review_snapshots(
        app_id=app_id, version=version, artifact_store=artifact_store,
    )

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
        verified_bundle_snapshots=verified_bundle_snapshots,
    )
    return {"rejected": True, "app_id": app_id, **payload}


class BuildArtifactPromotionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


@app.post("/api/studio/build/artifacts/{artifact_version_id}/promote")
async def promote_build_artifact_version(
    artifact_version_id: str,
    background_tasks: BackgroundTasks,
    body: BuildArtifactPromotionRequest | None = None,
    app_id: str | None = None,
    build_registry_id: str | None = None,
    principal: UserPrincipal = Depends(require_studio_user),
):
    validate_path_id(artifact_version_id, "artifact_version_id")
    app_id, user_id = await _resolve_studio_artifact_scope(principal, app_id=app_id, build_registry_id=build_registry_id)
    artifact_store = get_artifact_store()
    version = await artifact_store.get_build_record(app_id=app_id, build_record_id=artifact_version_id)
    if not version:
        raise HTTPException(status_code=404, detail=f"Artifact version not found: {artifact_version_id}")
    if version.lifecycle_status != ArtifactLifecycleStatus.CURRENT:
        raise HTTPException(status_code=409, detail="Only accepted current artifact versions can be promoted.")
    if version.build_family != "app_bundle":
        raise HTTPException(status_code=400, detail=f"Unsupported artifact kind for restore: {version.build_family}")
    _enforce_artifact_validation_gate(
        version,
        action="promoted",
    )

    metadata = _version_metadata(version)
    promotion_build_registry_id = build_registry_id
    if metadata.get("build_registry_id") != promotion_build_registry_id:
        raise HTTPException(status_code=409, detail="Artifact does not belong to the selected build")
    app_registry_result = None
    app_registry_service = None
    if promotion_build_registry_id:
        app_registry_service = _get_app_registry_service()
        registry_record = (
            await app_registry_service.get_app_record(build_registry_id=promotion_build_registry_id, owner_user_id=user_id)
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
        current_run = registry_record.get("current_build_run") or {}
        if (
            current_run.get("artifact_version_id") != artifact_version_id
            or current_run.get("build_id") != metadata.get("build_id")
        ):
            raise HTTPException(status_code=409, detail="Selected artifact is not the current build under review")
    refinement_metadata = _refinement_metadata_from_version(version)

    verified_bundle_snapshots = await _verified_review_snapshots(
        app_id=app_id, version=version, artifact_store=artifact_store,
    )
    bundle_bytes = verified_bundle_snapshots[version.id]
    target_dir = _resolve_bundle_restore_target(version)
    try:
        restore_summary = _restore_bundle_to_target(bundle_bytes=bundle_bytes, target_dir=target_dir, workspace_layout=True)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail="Failed to promote artifact") from exc

    if promotion_build_registry_id and app_registry_service is not None:
        try:
            app_registry_result = await app_registry_service.promote_build(
                build_registry_id=promotion_build_registry_id,
                promoted_by=user_id,
                bundle_path=str(target_dir / "app"),
                expected_build_id=metadata["build_id"],
                expected_artifact_version_id=artifact_version_id,
            )
        except ValueError as exc:
            logger.warning("promote_build conflict app=%s build=%s: %s", app_id, promotion_build_registry_id, exc)
            raise HTTPException(status_code=409, detail="Conflict promoting build.") from exc
        if not app_registry_result["success"]:
            raise HTTPException(status_code=409, detail="Build changed during promotion. Refresh before retrying.")

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

    # Index the selected workspace, never the host that performed the promotion.
    app_intelligence_refresh: dict[str, Any] | None = None
    try:
        app_intelligence_refresh = await _start_studio_app_intelligence_index_job(
            app_id=app_id,
            user_id=user_id,
            body=AppIntelligenceIndexRequest(workspace_root=str(target_dir)),
            background_tasks=background_tasks,
        )
    except Exception as exc:
        logger.warning("POST_PROMOTE_APP_INTELLIGENCE_REFRESH_FAILED app=%s: %s", app_id, exc)
        app_intelligence_refresh = {"status": "failed", "error": str(exc)}

    payload = await _build_artifact_review_payload(
        app_id=app_id,
        version=version,
        artifact_store=artifact_store,
        verified_bundle_snapshots=verified_bundle_snapshots,
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
        "app_intelligence_refresh": app_intelligence_refresh,
        **payload,
    }


class BuildRestoreRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    artifact_version_id: str = Field(..., description="Accepted artifact version to materialize in a separate workspace")


@app.post("/api/studio/build/restore")
async def restore_artifact_version(
    body: BuildRestoreRequest,
    app_id: str | None = None,
    build_registry_id: str | None = None,
    principal: UserPrincipal = Depends(require_studio_user),
):
    """Materialize a selected app version outside the running host workspace."""
    app_id, _ = await _resolve_studio_artifact_scope(principal, app_id=app_id, build_registry_id=build_registry_id)
    validate_path_id(body.artifact_version_id, "artifact_version_id")
    artifact_store = get_artifact_store()

    version = await artifact_store.get_build_record(
        app_id=app_id,
        build_record_id=body.artifact_version_id,
    )
    if not version:
        raise HTTPException(status_code=404, detail=f"Artifact version not found: {body.artifact_version_id}")

    if _version_metadata(version).get("build_registry_id") != build_registry_id:
        raise HTTPException(status_code=409, detail="Artifact does not belong to the selected build")
    if version.lifecycle_status not in {ArtifactLifecycleStatus.CURRENT, ArtifactLifecycleStatus.SUPERSEDED}:
        raise HTTPException(status_code=409, detail="Only previously accepted artifacts can be restored")
    _enforce_artifact_validation_gate(version, action="restored")

    bundle_bytes = await _verified_app_bundle(version)
    target_dir = _resolve_bundle_restore_target(version)

    try:
        _restore_bundle_to_target(bundle_bytes=bundle_bytes, target_dir=target_dir, workspace_layout=True)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail="Failed to extract artifact") from exc

    logger.info(
        "Restored app_id=%s artifact_version_id=%s to a separate workspace (%s/%s)",
        app_id,
        body.artifact_version_id,
        version.build_family,
        version.build_key,
    )

    return {
        "restored": True,
        "artifact_version_id": body.artifact_version_id,
        "build_family": version.build_family,
        "build_key": version.build_key,
        "target_path": str(target_dir),
        "active_version_changed": False,
    }


@app.get("/api/studio/build")
async def get_build_surface(
    app_id: str | None = None,
    build_registry_id: str | None = None,
    principal: UserPrincipal = Depends(require_studio_user),
):
    if build_registry_id is not None:
        app_id, user_id = await _resolve_studio_artifact_scope(
            principal, build_registry_id=build_registry_id, app_id=app_id,
        )
    else:
        app_id, user_id = _resolve_studio_scope(principal, app_id=app_id)
    app_root = resolve_app_root()
    missing_surfaces = get_missing_studio_surfaces(app_root)
    if missing_surfaces:
        raise HTTPException(
            status_code=500,
            detail=f"Build surface is missing required surfaces: {', '.join(missing_surfaces)}",
        )

    try:
        record = (await _get_app_registry_service().get_app_record(app_id=app_id, owner_user_id=user_id)).get("app")
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
    principal: UserPrincipal = Depends(require_studio_user),
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
    build_registry_id: BuildIdentity | None = None
    source_chat_id: BuildIdentity | None = None
    retry_failed: StrictBool = False


async def _failed_workflow_retry_contribution(
    body: WorkflowTriggerRequest, *, app_id: str, user_id: str,
) -> TriggerRoutingContribution:
    from mozaiksai.core.multitenant import build_app_scope_filter
    from mozaiksai.core.session.launcher import _PERSISTENCE_MANAGER, validate_context_for_workflow

    if body.trigger_source != "manual" or not body.source_chat_id or not body.workflow_id:
        raise ValueError("Failed-workflow retry requires a manual trigger and source workflow session")
    if (body.context_variables or body.trigger_payload or body.journey_id is not None
            or body.action_id is not None or body.artifact_key is not None):
        raise ValueError("Failed-workflow retry cannot override saved launch intent")
    source = await (await _PERSISTENCE_MANAGER._coll()).find_one(
        {"_id": body.source_chat_id, "user_id": user_id, "workflow_name": body.workflow_id,
         **build_app_scope_filter(app_id)},
        {"status": 1, "run_build_binding": 1, "trigger_meta": 1, "change_request_id": 1,
         "revision_id": 1, "coding_participation": 1},
    )
    if not source or source.get("status") != 2:
        raise ValueError("Source workflow session is not an owned failed run")
    binding = await _get_app_registry_service().resolve_build_binding(
        owner_user_id=user_id, app_id=app_id, chat_id=body.source_chat_id, workflow_name=body.workflow_id,
        build_registry_id=body.build_registry_id, source_chat_id=body.source_chat_id,
        persisted_binding=source.get("run_build_binding"),
    )
    trigger_meta = source.get("trigger_meta") or {}
    if binding.phase == "genesis":
        seed = {}
        if source.get("coding_participation") is not None:
            seed["coding_participation"] = TypeAdapter(Literal["guided", "autonomous"]).validate_python(
                source["coding_participation"],
            )
        return TriggerRoutingContribution(
            workflow_id=body.workflow_id, journey_id=trigger_meta.get("journey_id"),
            context_seed=validate_context_for_workflow(body.workflow_id, seed),
            explanation="Retry failed workflow", require_exact_route=True,
        )

    change_id = TypeAdapter(BuildIdentity).validate_python(source.get("change_request_id"))
    revision_id = TypeAdapter(BuildIdentity).validate_python(source.get("revision_id"))
    store = get_artifact_store()
    change = await store.get_change_request(app_id=binding.target_app_id, change_request_id=change_id)
    if (change is None or change.id != change_id or change.app_id != binding.target_app_id
            or change.created_by_user_id != user_id):
        raise ValueError("Saved refinement request is not available to this owner")
    request = RefinementRequest.model_validate(change.refinement_request.model_dump(mode="python"))
    journey_id = trigger_meta.get("workflow_sequence") or trigger_meta.get("journey_id")
    if not journey_id or journey_id != change.impact_set.workflow_sequence:
        raise ValueError("Saved refinement journey does not match the failed run")
    if (change.router_decision.get("is_full_restart")
            or journey_id in {"full_rebuild", "conceptual_replan"} or request.request_kind != "refinement"):
        raise ValueError("Failed-workflow retry does not support saved rebuild or restart intent")
    baseline_id = TypeAdapter(BuildIdentity).validate_python(request.build_record_id)
    if (
        not request.raw_user_request.strip() or request.raw_user_request != change.raw_user_request
        or request.app_id not in {None, app_id} or request.user_id not in {None, user_id}
        or request.target_app_id not in {None, binding.target_app_id}
        or request.build_record_id != change.build_record_id
        or request.build_record_id != trigger_meta.get("build_record_id")
        or request.build_family != change.build_family or request.normalized_build_key() != change.build_key
        or change.classification != change.change_intent.change_class
        or change.classification != trigger_meta.get("change_class")
    ):
        raise ValueError("Saved refinement request does not match the failed run")
    baseline = await store.get_build_record(app_id=binding.target_app_id, build_record_id=baseline_id)
    if (
        baseline is None or baseline.id != request.build_record_id or baseline.app_id != binding.target_app_id
        or baseline.build_family != request.build_family or baseline.build_key != request.normalized_build_key()
        or baseline.lifecycle_status in {ArtifactLifecycleStatus.ARCHIVED, ArtifactLifecycleStatus.DELETED}
        or baseline.commit_metadata.author_user_id != user_id
    ):
        raise ValueError("Selected retry baseline is unavailable or retired")
    metadata = _version_metadata(baseline)
    if metadata.get("build_registry_id") != binding.build_registry_id or metadata.get("target_app_id") != binding.target_app_id:
        raise ValueError("Selected retry baseline does not belong to the registered target")

    # Reuse the accepted request and routing facts, never the failed run's
    # generated output, validation, counters, or mutable workspace copy.
    extra = {"change_request_id": change_id, "revision_id": revision_id,
             "files_manifest": [entry.model_dump(mode="python") for entry in baseline.files_manifest]}
    if metadata.get("workspace_dir"):
        extra.update(lifecycle_state="review", bundle_path=metadata["workspace_dir"])
    request = request.model_copy(update={"app_id": app_id, "user_id": user_id, "target_app_id": binding.target_app_id, "extra": extra})
    seed = {
        "build_mode": "revision", "revision_scope": change.classification.value,
        "artifact_kind": request.build_family, "artifact_version_id": baseline.id,
        "refinement_request": request.raw_user_request, "refinement_request_meta": request.model_dump(mode="python"),
        "change_intent": change.change_intent.model_dump(mode="python"), "impact_set": change.impact_set.model_dump(mode="python"),
        "change_request_id": change_id, "revision_id": revision_id,
        "revision_origin_workflow": request.requested_workflow_id or body.workflow_id,
        "workflow_sequence": journey_id, "screen": request.source_surface,
    }
    if extra.get("lifecycle_state"):
        seed["lifecycle_state"] = extra["lifecycle_state"]
    return TriggerRoutingContribution(
        workflow_id=body.workflow_id, journey_id=journey_id,
        context_seed=validate_context_for_workflow(body.workflow_id, seed),
        explanation="Retry failed workflow with its saved refinement request and selected baseline",
        require_exact_route=True,
    )


async def _complete_inline_refinement(*, binding: RunBuildBinding, user_id: str, result: Any) -> None:
    artifact_id = (result.metadata or {}).get("build_record_id")
    if result.status == "validated" and not artifact_id:
        raise ValueError("Inline refinement has no saved output to review")
    version = None
    if artifact_id:
        version = await get_artifact_store().get_build_record(
            app_id=binding.target_app_id, build_record_id=artifact_id,
        )
        if version is None or any(
            _version_metadata(version).get(key) != value for key, value in binding.model_dump().items()
        ):
            raise ValueError("Inline refinement output does not belong to its registered run")
        if result.status == "validated" and (
            version.validation_status != ArtifactValidationStatus.PASSED
            or version.app_validation_status != "passed"
        ):
            raise ValueError("Inline refinement output has not passed validation")
    result_status = "review" if version is not None and result.status == "validated" else "needs_revision"
    saved = await _get_app_registry_service().update_build_status(
        owner_user_id=user_id, build_registry_id=binding.build_registry_id,
        expected_build_id=binding.build_id, status=result_status,
        artifact_version_id=version.id if version else None,
        bundle_path=_version_metadata(version).get("workspace_dir") if version else None,
    )
    if not saved["success"]:
        raise ValueError("Inline refinement was superseded before its output could be registered")


async def _fail_inline_refinement(*, binding: RunBuildBinding, user_id: str) -> None:
    try:
        with CancelScope(shield=True):
            await _get_app_registry_service().update_build_status(
                owner_user_id=user_id, build_registry_id=binding.build_registry_id,
                expected_build_id=binding.build_id, status="needs_revision",
            )
    except Exception:
        logger.exception("Could not record inline refinement failure for build=%s", binding.build_id)


def _confirmed_refinement_decision(
    snapshot: dict[str, Any] | None,
    request: RefinementRequest,
    *,
    action_id: str,
    change_request_id: str | None,
    revision_id: str | None,
    source_chat_id: str | None,
) -> dict[str, Any]:
    """Bind an approval to the existing target-scoped pending decision."""
    pending = (snapshot or {}).get("pending_harness_decision") or {}
    metadata = pending.get("metadata") or {}
    if "source_chat_id" in metadata and metadata["source_chat_id"] != source_chat_id:
        raise HTTPException(status_code=409, detail="The decision belongs to a different review conversation. Reopen its original review.")
    stored_request = (pending.get("trigger_payload") or {}).get("refinement_request")
    actions = pending.get("actions") or []
    if (
        not isinstance(stored_request, dict)
        or not change_request_id or pending.get("change_request_id") != change_request_id
        or not revision_id or pending.get("revision_id") != revision_id
        or not pending.get("decision_id")
        or not any(action.get("action_id") == action_id for action in actions)
    ):
        raise HTTPException(status_code=409, detail="The proposed scope is no longer current. Submit the change again.")
    try:
        previous = RefinementRequest.model_validate(stored_request)
    except ValidationError as exc:
        raise HTTPException(status_code=409, detail="The proposed scope is unavailable. Submit the change again.") from exc
    if any(
        getattr(previous, field) != getattr(request, field)
        for field in (
            "app_id", "target_app_id", "user_id", "request_kind", "declared_change_class",
            "build_family", "build_key", "build_record_id", "raw_user_request",
            "requested_workflow_id", "source_surface",
        )
    ):
        raise HTTPException(status_code=409, detail="The proposed scope belongs to a different request or artifact. Submit the change again.")
    return pending


@app.post("/api/workflows/trigger")
async def trigger_workflow(
    body: WorkflowTriggerRequest,
    principal: UserPrincipal = Depends(require_studio_user),
):
    app_id, user_id = _resolve_studio_scope(principal, app_id=body.app_id, user_id=body.user_id)
    authorize_http_workflow_source_paths(body.context_variables, principal=principal)
    retry_contribution = None
    if body.retry_failed:
        try:
            retry_contribution = await _failed_workflow_retry_contribution(body, app_id=app_id, user_id=user_id)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    build_registry_id = body.build_registry_id
    artifact_app_id = app_id
    session_router = get_session_router()
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
    inline_binding = None
    baseline_files = None
    persisted_change_request_id = str(trigger_payload.get("change_request_id") or "").strip() or None
    persisted_revision_id = str(trigger_payload.get("revision_id") or "").strip() or None
    action_payload = trigger_payload.get("harness_action")
    action_id = str(action_payload.get("action_id") or "").strip() if isinstance(action_payload, dict) else None
    workflow_continuation = action_id in {"run_recommended_workflow", "confirm_recommended_workflow"}

    if body.trigger_source == "refinement":
        from mozaiksai.core.runtime.composition.platform_hooks import get_platform_hooks
        from mozaiksai.core.session.launcher import _PERSISTENCE_MANAGER
        from mozaiksai.core.usage.context import AuxiliaryUsageContext

        protected_context = {
            "run_build_binding", "build_registry_id", "build_id", "target_app_id",
            "artifact_root", "bundle_path", "lifecycle_state", "artifact_version_id",
            "app_id", "user_id", "chat_id",
        }
        if protected_context.intersection(body.context_variables):
            raise HTTPException(status_code=400, detail="Refinement context cannot override saved build identity or paths")
        if body.source_chat_id:
            source = await _get_app_registry_service().repo.get_owned_chat_binding(
                app_id=app_id, owner_user_id=user_id, chat_id=body.source_chat_id,
            )
            if not source:
                raise HTTPException(status_code=404, detail="Source build session not found")
            binding = RunBuildBinding.model_validate(source)
            if build_registry_id and build_registry_id != binding.build_registry_id:
                raise HTTPException(status_code=400, detail="Source session does not match selected build")
            build_registry_id = binding.build_registry_id
        artifact_app_id, _ = await _resolve_studio_artifact_scope(
            principal, app_id=app_id, build_registry_id=build_registry_id,
        )
        session_router = session_router.for_target(artifact_app_id)
        allowed, reason = await get_platform_hooks().call_chat_prereqs(
            app_id, user_id, body.workflow_id or "", _PERSISTENCE_MANAGER,
        )
        if not allowed:
            raise HTTPException(status_code=403, detail=reason or "Workflow prerequisites not met")
        session_snapshot = None
        if action_id:
            try:
                session_snapshot = await session_router.get_session_snapshot(
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
        usage_context = AuxiliaryUsageContext(
            app_id=app_id, user_id=user_id,
            tenant_id=principal.tenant_id, workspace_id=principal.workspace_id,
        )
        try:
            refinement_request = orchestration_control.request_from_payload(
                usage_context=usage_context,
                payload=trigger_payload,
                app_id=app_id,
                target_app_id=artifact_app_id,
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
        source_version = await get_artifact_store().get_build_record(
            app_id=artifact_app_id, build_record_id=refinement_request.build_record_id,
        ) if refinement_request.build_record_id else None
        if source_version is None or source_version.build_family != refinement_request.build_family:
            raise HTTPException(status_code=404, detail="Selected refinement artifact not found")
        metadata = _version_metadata(source_version)
        # Filesystem roots and baseline contracts come from the saved version,
        # not from caller context or copied workbench metadata.
        safe_extra = {
            key: value for key, value in refinement_request.extra.items()
            if key in {"change_request_id", "revision_id"}
        }
        safe_extra["files_manifest"] = [entry.model_dump(mode="python") for entry in source_version.files_manifest]
        if metadata.get("workspace_dir"):
            safe_extra.update(lifecycle_state="review", bundle_path=metadata["workspace_dir"])
        refinement_request = refinement_request.model_copy(update={"extra": safe_extra})
        persisted_revision_id = (
            str(refinement_request.extra.get("revision_id") or "").strip() or persisted_revision_id
        )
        persisted_change_request_id = (
            str(refinement_request.extra.get("change_request_id") or "").strip()
            or persisted_change_request_id
        )
        if persisted_change_request_id:
            change = await get_artifact_store().get_change_request(
                app_id=artifact_app_id, change_request_id=persisted_change_request_id,
            )
            if change is None or change.created_by_user_id != user_id:
                raise HTTPException(status_code=404, detail="Refinement change request not found")
        if persisted_revision_id:
            snapshot = await session_router.get_session_snapshot(app_id=app_id, user_id=user_id)
            if not snapshot or snapshot.get("active_revision_id") != persisted_revision_id:
                raise HTTPException(status_code=409, detail="Refinement revision is no longer active")

        pending_decision = None
        if action_id:
            pending_decision = _confirmed_refinement_decision(
                session_snapshot, refinement_request, action_id=action_id,
                change_request_id=persisted_change_request_id, revision_id=persisted_revision_id,
                source_chat_id=body.source_chat_id,
            )
            if body.context_variables != (pending_decision.get("context_variables") or {}):
                raise HTTPException(status_code=409, detail="The decision belongs to a different request context. Submit the change again.")
        coding_payload = trigger_payload.get("coding_request")
        if workflow_continuation:
            # Explicit workflow continuation must not try inline generation first.
            coding_payload = None
            trigger_payload.pop("coding_request", None)
        if coding_payload is not None and not isinstance(coding_payload, dict):
            raise HTTPException(status_code=400, detail="Invalid coding_request")
        if action_id == "apply_proposed_scope":
            confirmed_paths = (pending_decision or {}).get("selected_paths")
            if not isinstance(confirmed_paths, list) or not confirmed_paths or any(
                not isinstance(path, str) or not path for path in confirmed_paths
            ):
                raise HTTPException(status_code=409, detail="The proposed scope has no available files. Submit the change again.")
            supplied_files = coding_payload.get("files") if isinstance(coding_payload, dict) else None
            if supplied_files is not None and (
                not isinstance(supplied_files, dict) or set(supplied_files) != set(confirmed_paths)
            ):
                raise HTTPException(status_code=409, detail="The submitted files do not match the proposed scope.")
            coding_payload = {**(coding_payload or {}), "files": dict.fromkeys(confirmed_paths, "")}
            trigger_payload["coding_request"] = coding_payload
        if isinstance(coding_payload, dict) and "files" in coding_payload and not isinstance(coding_payload["files"], dict):
            raise HTTPException(status_code=400, detail="Refinement file scope must be an object")
        explicit_paths = list((coding_payload or {}).get("files") or {})
        if explicit_paths:
            if not orchestration_control.coding_enabled():
                raise HTTPException(status_code=409, detail="Selected-file refinement is unavailable. The file scope has not been widened.")
            baseline_files, skipped = _decode_text_bundle_entries(await _verified_app_bundle(source_version))
            if skipped:
                raise HTTPException(status_code=409, detail="Inline refinement cannot preserve every file in this bundle")
            if any(path not in baseline_files for path in explicit_paths):
                raise HTTPException(status_code=400, detail="Refinement file scope is not in the selected artifact")
            coding_payload = {**coding_payload, "files": {path: baseline_files[path] for path in explicit_paths}}
            trigger_payload["coding_request"] = coding_payload
        if orchestration_control.coding_enabled() and isinstance(coding_payload, dict):
            try:
                resolve_coding_validation_strategy(coding_payload.get("validation_strategy"))
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
        if pending_decision is not None:
            try:
                await session_router.resolve_pending_harness_decision(
                    app_id=app_id, user_id=user_id,
                    decision_id=pending_decision["decision_id"], action_id=action_id,
                    expected_pending_decision=pending_decision,
                )
            except ValueError as exc:
                raise HTTPException(status_code=409, detail="This decision has already been handled or replaced. Submit the change again.") from exc
            refinement_request = refinement_request.model_copy(update={"extra": {
                **refinement_request.extra, "harness_action": {"action_id": action_id},
            }})
        try:
            refinement_decision = await orchestration_control.route_refinement_request(refinement_request)
        except Exception as exc:
            logger.error("refinement_classification_failed: %s", exc, exc_info=True)
            raise HTTPException(status_code=503, detail="Refinement classification unavailable") from exc
        if workflow_continuation and pending_decision is not None and (
            refinement_decision.workflow_id != pending_decision.get("recommended_workflow_id")
            or refinement_decision.workflow_sequence != pending_decision.get("journey_id")
        ):
            raise HTTPException(status_code=409, detail="The recommended workflow has changed. Submit the change again to review the new route.")
        harness_decision = orchestration_control.build_harness_decision(refinement_decision)
        selected_surface_request = (
            bool(explicit_paths)
            and not workflow_continuation
            and orchestration_control.contract_surface_enabled()
            and refinement_decision.change_intent.change_class.value in {"design", "feature"}
            and refinement_request.build_family == "app_bundle"
        )
        if explicit_paths and refinement_decision.change_intent.change_class.value != "patch" and not selected_surface_request:
            raise HTTPException(
                status_code=409,
                detail="This change needs a broader plan than the selected files allow. Narrow the request, or clear the file limit and submit it for a broader review. No files were changed.",
            )
        if (
            not workflow_continuation
            and orchestration_control.contract_surface_enabled()
            and refinement_decision.change_intent.change_class.value in {"feature", "design"}
            and refinement_request.build_family == "app_bundle"
        ):
            inline_binding = None
            try:
                if baseline_files is None:
                    baseline_files, skipped = _decode_text_bundle_entries(await _verified_app_bundle(source_version))
                    if skipped:
                        raise HTTPException(status_code=409, detail="Inline refinement cannot preserve every file in this bundle")
                contract_surface_plan, harness_decision = (
                    await orchestration_control.prepare_contract_surface_request(
                        refinement_request=refinement_request,
                        routing_decision=refinement_decision,
                        workspace_files=baseline_files,
                        allowed_paths=explicit_paths or None,
                    )
                )
                if contract_surface_plan is not None and contract_surface_plan.requires_schema_migration:
                    contract_surface_plan = None
                    harness_decision = orchestration_control.build_harness_decision(refinement_decision)
                if selected_surface_request and contract_surface_plan is None:
                    raise HTTPException(
                        status_code=409,
                        detail="This change could not be resolved within the selected files. Narrow the request, or clear the file limit to review a broader plan. No files were changed.",
                    )
                if contract_surface_plan is not None:
                    inline_binding = await _get_app_registry_service().begin_refinement_run(
                        owner_user_id=user_id, app_id=app_id, build_registry_id=build_registry_id,
                        workflow_name=refinement_decision.workflow_id,
                    )
                    refinement_request = refinement_request.model_copy(update={
                        "usage_context": usage_context.model_copy(update={"run_build_binding": inline_binding}),
                    })
                    surface_result = await orchestration_control.execute_surface_plan(
                        plan=contract_surface_plan,
                        refinement_request=refinement_request,
                        routing_decision=refinement_decision,
                        workspace_files=baseline_files,
                        allowed_paths=explicit_paths or None,
                    )
                    finalized = await orchestration_control.finalize_surface_output(
                        plan=contract_surface_plan, result=surface_result, refinement_request=refinement_request,
                        routing_decision=refinement_decision, workspace_files=baseline_files,
                        run_build_binding=inline_binding,
                    )
                    await _complete_inline_refinement(binding=inline_binding, user_id=user_id, result=finalized)
                    surface_result = surface_result.model_copy(update={
                        "status": {"validated": "success", "planned": "partial"}.get(finalized.status, "failed"),
                        "metadata": {**surface_result.metadata, **finalized.metadata, "validation_result": finalized.validation_result},
                    })
            except (HTTPException, CancelledError):
                if inline_binding is not None:
                    await _fail_inline_refinement(binding=inline_binding, user_id=user_id)
                raise
            except Exception as exc:
                if inline_binding is not None:
                    await _fail_inline_refinement(binding=inline_binding, user_id=user_id)
                    logger.exception("surface_regeneration_failed build=%s", inline_binding.build_id)
                    raise HTTPException(status_code=503, detail="Surface refinement unavailable") from exc
                if selected_surface_request:
                    raise HTTPException(
                        status_code=409,
                        detail="This change could not be verified within the selected files. No files were changed. Submit a narrower request or review a broader plan.",
                    ) from exc
                logger.warning("contract_surface_planner_failed, falling back to workflow: %s", exc)
                contract_surface_plan = None
                surface_result = None
                harness_decision = orchestration_control.build_harness_decision(refinement_decision)
        resolved_change_class = refinement_decision.change_intent.change_class.value
        resolved_artifact_kind = refinement_request.build_family
        resolved_artifact_version_id = refinement_request.build_record_id
        if surface_result is None and not workflow_continuation and orchestration_control.coding_enabled() and isinstance(trigger_payload.get("coding_request"), dict):
            coding_payload = dict(trigger_payload["coding_request"])
            coding_request = orchestration_control.build_coding_request(
                refinement_request=refinement_request,
                routing_decision=refinement_decision,
                payload=coding_payload,
            )
            if coding_request is not None:
                inline_binding = None
                try:
                    coding_request, coding_decision = await orchestration_control.prepare_coding_request(coding_request)
                    harness_decision = coding_decision
                    if coding_request is not None:
                        baseline_files, skipped = _decode_text_bundle_entries(await _verified_app_bundle(source_version))
                        if skipped:
                            raise HTTPException(status_code=409, detail="Inline refinement cannot preserve every file in this bundle")
                        inline_binding = await _get_app_registry_service().begin_refinement_run(
                            owner_user_id=user_id, app_id=app_id,
                            build_registry_id=build_registry_id,
                            workflow_name=refinement_decision.workflow_id,
                        )
                        coding_request = coding_request.model_copy(update={
                            "run_build_binding": inline_binding, "baseline_files": baseline_files,
                        })
                        coding_result = await orchestration_control.execute_coding_request(coding_request)
                        await _complete_inline_refinement(
                            binding=inline_binding, user_id=user_id, result=coding_result,
                        )
                        harness_decision = orchestration_control.build_coding_result_decision(coding_request, coding_result)
                except (HTTPException, CancelledError):
                    if inline_binding is not None:
                        await _fail_inline_refinement(binding=inline_binding, user_id=user_id)
                    raise
                except Exception as exc:
                    if inline_binding is not None:
                        await _fail_inline_refinement(binding=inline_binding, user_id=user_id)
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
                app_id=artifact_app_id,
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

    confirmed_action = action_id if refinement_request is not None else None

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
        decision_metadata = {
            **(harness_decision.metadata or {}), "build_registry_id": build_registry_id,
            "source_chat_id": body.source_chat_id,
        }
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
                metadata=decision_metadata,
            )
            pending_snapshot = await session_router.persist_revision_intent(
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
            "build_registry_id": build_registry_id,
            "change_request_id": persisted_change_request_id,
            "revision_id": persisted_revision_id,
            "chat_id": None,
            "workflow_id": harness_decision.recommended_workflow_id or refinement_decision.workflow_id,
            "requested_workflow_id": body.workflow_id,
            "websocket_url": None,
            "trigger_source": body.trigger_source,
            "routing_explanation": refinement_decision.explanation if refinement_decision is not None else "",
            "rerouted_by_dependency": False,
            "harness_decision": {**harness_decision.model_dump(mode="python"), "metadata": decision_metadata},
        }

    review_continuation = None
    review_continuation_error = None
    if inline_binding is not None and (coding_result is not None or surface_result is not None):
        try:
            review_continuation = await continue_inline_app_review(
                app_id=app_id, user_id=user_id, source_chat_id=body.source_chat_id,
                binding=inline_binding, baseline_id=refinement_request.build_record_id,
                result=coding_result if coding_result is not None else surface_result,
                registry=_get_app_registry_service(), artifact_store=artifact_store,
                persistence=_PERSISTENCE_MANAGER, session_router=session_router,
            )
        except Exception:
            logger.exception("inline_review_continuation_failed build=%s", inline_binding.build_id)
            review_continuation_error = (
                "The edit finished, but its review chat could not be reopened. "
                "Open the app's saved builds to review the result."
            )

    if coding_result is not None and coding_result.eligible:
        if artifact_store is not None and persisted_change_request_id is not None and refinement_request.build_record_id:
            try:
                session_status = {
                    "validated": RefinementSessionStatus.VALIDATED,
                    "failed": RefinementSessionStatus.FAILED,
                }.get(coding_result.status, RefinementSessionStatus.PENDING)
                coding_session = await artifact_store.create_refinement_session(
                    app_id=artifact_app_id,
                    build_record_id=refinement_request.build_record_id,
                    change_request_id=persisted_change_request_id,
                    result_build_record_id=coding_result.metadata.get("build_record_id"),
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
            "review_continuation": review_continuation,
            "review_continuation_error": review_continuation_error,
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
                    app_id=artifact_app_id,
                    build_record_id=refinement_request.build_record_id,
                    change_request_id=persisted_change_request_id,
                    provider="contract_surface_regeneration",
                    status=surface_session_status,
                    result_build_record_id=surface_result.metadata.get("build_record_id"),
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
            "review_continuation": review_continuation,
            "review_continuation_error": review_continuation_error,
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
            build_registry_id=build_registry_id,
            source_chat_id=body.source_chat_id,
            session_router=session_router,
            routing_contribution=(
                TriggerRoutingContribution(
                    workflow_id=refinement_decision.workflow_id,
                    journey_id=refinement_decision.workflow_sequence,
                    context_seed=refinement_decision.context_seed,
                    explanation=refinement_decision.explanation,
                    is_full_restart=refinement_decision.is_full_restart,
                    lifecycle_state=SessionLifecycle.STALE if refinement_decision.is_full_restart else SessionLifecycle.ACTIVE,
                ) if refinement_decision is not None else retry_contribution
            ),
            extra_trigger_meta={
                "action_id": body.action_id,
                "change_class": resolved_change_class or (retry_contribution.context_seed.get("revision_scope") if retry_contribution else None),
                "build_record_id": resolved_artifact_version_id or (retry_contribution.context_seed.get("artifact_version_id") if retry_contribution else None),
                "build_family": resolved_artifact_kind or (retry_contribution.context_seed.get("artifact_kind") if retry_contribution else None),
                "workflow_sequence": refinement_decision.workflow_sequence if refinement_decision is not None else (retry_contribution.journey_id if retry_contribution else None),
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
                app_id=artifact_app_id,
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


async def _resolve_preview_build(principal: UserPrincipal, build_registry_id: str) -> str:
    target_app_id, _ = await _resolve_studio_artifact_scope(principal, build_registry_id=build_registry_id)
    return target_app_id


async def _resolve_preview_artifact(
    principal: UserPrincipal, artifact_version_id: str, build_registry_id: str,
) -> tuple[str, dict[str, str | bytes]]:
    target_app_id = await _resolve_preview_build(principal, build_registry_id)
    version = await get_artifact_store().get_build_record(app_id=target_app_id, build_record_id=artifact_version_id)
    if version is None:
        raise HTTPException(status_code=404, detail="Artifact not found")
    if version.build_family != "app_bundle":
        raise HTTPException(status_code=422, detail="Preview requires an app bundle")
    try:
        binding = RunBuildBinding.model_validate({
            key: _version_metadata(version).get(key) for key in RunBuildBinding.model_fields
        })
    except ValueError as exc:
        raise HTTPException(status_code=409, detail="Artifact has no verified build identity") from exc
    if binding.build_registry_id != build_registry_id or binding.target_app_id != target_app_id:
        raise HTTPException(status_code=409, detail="Artifact identity does not match its build target")
    try:
        files, diagnostics = await read_artifact_bundle(version, include_binary=True)
    except (OSError, ValueError, zipfile.BadZipFile) as exc:
        raise HTTPException(status_code=422, detail="Artifact archive is unavailable or failed integrity validation") from exc
    if any(item.get("blocking") for item in diagnostics):
        raise HTTPException(status_code=422, detail="Artifact contains unsafe or oversized preview files")
    return target_app_id, files


app.include_router(create_sandbox_router(
    resolve_scope=_resolve_studio_preview_scope, resolve_build=_resolve_preview_build,
    resolve_artifact=_resolve_preview_artifact,
))


app.router.routes[:] = sorted(
    app.router.routes,
    key=lambda route: (
        0
        if getattr(route, "path", None) == "/api/shell-config"
        and getattr(getattr(route, "endpoint", None), "__module__", "") == __name__
        else 1
    ),
)
