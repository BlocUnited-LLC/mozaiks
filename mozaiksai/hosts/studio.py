from __future__ import annotations

"""Studio management host layered on top of mozaiksai.hosts.platform.

Studio is the local/private management and create control plane used by the
CLI and by the hosted Mozaiks product. It adds Studio shell routes and
workflow triggering on top of the headless platform host.
"""

import stat
import zipfile
from datetime import UTC, datetime
from difflib import unified_diff
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Dict, List, Literal, Optional
from uuid import uuid4

from fastapi import Depends, HTTPException
from pydantic import BaseModel, Field, ValidationError

from factory_app.app.modules.app_registry.backend.service import AppRegistryService
from mozaiksai.hosts.bootstrap import configure_repo_host_defaults

configure_repo_host_defaults("studio")

from logs.logging_config import get_workflow_logger
from mozaiksai.control_plane import (
    AcceptedStagedAppBundleArtifactVersionError,
    accept_staged_refinement_artifact_version,
    get_orchestration_control_harness,
)
from mozaiksai.control_plane.app_context import get_current_app_context_summary
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
from mozaiksai.control_plane.dry_run import RefinementDryRunPlan, RefinementExecutionPlan
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
from mozaiksai.control_plane.review import load_refinement_review_record
from mozaiksai.core.runtime.app.console_summary import (
    build_app_overview_summary,
    build_apps_summary,
    build_build_section,
    build_integrations_summary,
    get_missing_console_surfaces,
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
from mozaiksai.core.workflow.generator_support.connector_health import run_connector_health_check
from mozaiksai.core.workflow.generator_support.connector_service import (
    compute_connector_health,
    delete_connector,
    list_connectors,
    store_connector,
    update_connector_metadata,
)
from mozaiksai.core.workflow.paths import candidate_app_workflows_roots
from mozaiksai.hosts import platform as platform_app
from mozaiksai.hosts.platform import (
    build_shell_config,
    resolve_app_root,
    resolve_scope_from_principal,
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


def _normalize_bundle_entry_name(name: str) -> Optional[str]:
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
    return [("/".join(parts[1:]), content) for parts, (_, content) in zip(split_paths, entries)]


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


def _artifact_bundle_path_from_version(version) -> Optional[Path]:  # noqa: ANN001
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


def _refinement_metadata_from_version(version) -> Optional[dict[str, Any]]:  # noqa: ANN001
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


def _resolve_bundle_restore_target(version) -> Path:  # noqa: ANN001
    app_root = resolve_app_root()
    if version.artifact_kind != "app_bundle":
        raise HTTPException(status_code=400, detail=f"Unsupported artifact kind for restore: {version.artifact_kind}")
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

        for info, safe_name in planned:
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


def _build_diff_preview(*, path: str, before: Optional[str], after: Optional[str]) -> str:
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
    if version.parent_version_id:
        parent_version = await artifact_store.get_artifact_version(
            app_id=app_id,
            artifact_version_id=version.parent_version_id,
        )
        parent_zip = _artifact_bundle_path_from_version(parent_version) if parent_version is not None else None
        if parent_zip is not None:
            parent_files, parent_skipped = _decode_text_bundle_entries(parent_zip)

    sessions = await artifact_store.list_refinement_sessions(
        app_id=app_id,
        result_artifact_version_id=version.id,
        limit=5,
    )
    latest_session = sessions[0] if sessions else None
    change_request = None
    if latest_session is not None:
        change_request = await artifact_store.get_change_request(
            app_id=app_id,
            change_request_id=latest_session.change_request_id,
        )
    if change_request is None and version.parent_version_id:
        requests = await artifact_store.list_change_requests(
            app_id=app_id,
            artifact_version_id=version.parent_version_id,
            limit=1,
        )
        change_request = requests[0] if requests else None

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
        and version.validation_status in {ArtifactValidationStatus.PASSED, ArtifactValidationStatus.SKIPPED}
        and _refinement_acceptance_ready(version)
    )
    can_reject = version.lifecycle_status == ArtifactLifecycleStatus.DRAFT
    can_promote = (
        version.lifecycle_status == ArtifactLifecycleStatus.CURRENT
        and version.validation_status in {ArtifactValidationStatus.PASSED, ArtifactValidationStatus.SKIPPED}
        and current_zip is not None
    )

    return {
        "artifact_version": version.model_dump(by_alias=False, mode="python"),
        "parent_artifact_version": parent_version.model_dump(by_alias=False, mode="python") if parent_version else None,
        "change_request": change_request.model_dump(by_alias=False, mode="python") if change_request else None,
        "refinement_session": latest_session.model_dump(by_alias=False, mode="python") if latest_session else None,
        "review": {
            "artifact_version_id": version.id,
            "parent_version_id": version.parent_version_id,
            "review_status": review_status,
            "lifecycle_status": version.lifecycle_status.value,
            "validation_status": version.validation_status.value,
            "changed_file_count": len(changed_files),
            "changed_files": changed_files,
            "selected_paths": selected_paths,
            "coding_summary": coding_summary,
            "validation_result": validation_result,
            "current_skipped_files": current_skipped,
            "parent_skipped_files": parent_skipped,
            "can_accept": can_accept,
            "can_reject": can_reject,
            "can_promote": can_promote,
        },
    }

configure_session_router(
    trigger_route_resolver=get_orchestration_control_harness(),
)

def _resolve_studio_scope(
    principal: UserPrincipal,
    *,
    app_id: Optional[str] = None,
    user_id: Optional[str] = None,
) -> tuple[str, str]:
    """Resolve app/user scope for Studio endpoints in both auth modes."""
    return resolve_scope_from_principal(
        principal,
        app_id=app_id,
        user_id=user_id,
        default_user_id=platform_app._DEFAULT_PROFILE_USER_ID,
    )


@app.get("/api/shell-config")
async def get_console_shell_config():
    return await build_shell_config(surface="studio")


@app.get("/api/studio/overview")
async def get_app_overview(
    app_id: Optional[str] = None,
    principal: UserPrincipal = Depends(require_user_scope),
):
    resolved_app_id, _ = _resolve_studio_scope(principal, app_id=app_id)
    app_root = resolve_app_root()
    missing_surfaces = get_missing_console_surfaces(app_root)
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
        raise HTTPException(status_code=500, detail=f"Failed to build app overview summary: {exc}") from exc


@app.get("/api/studio/apps")
async def get_workspace_apps(
    principal: UserPrincipal = Depends(require_user_scope),
):
    _, user_id = _resolve_studio_scope(principal)
    app_root = resolve_app_root()
    missing_surfaces = get_missing_console_surfaces(app_root)
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
        raise HTTPException(status_code=500, detail=f"Failed to build apps summary: {exc}") from exc


class CreateWorkspaceAppRequest(BaseModel):
    name: str = Field(default="New App", min_length=1, max_length=120)
    description: Optional[str] = Field(default=None, max_length=1000)
    app_id: Optional[str] = Field(default=None, max_length=160)


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
            status="draft",
            app_id=body.app_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to create app record: {exc}") from exc


@app.get("/api/studio/integrations")
async def get_app_integrations(
    app_id: Optional[str] = None,
    principal: UserPrincipal = Depends(require_user_scope),
):
    app_id, _ = _resolve_studio_scope(principal, app_id=app_id)
    return await build_integrations_summary(app_id=app_id)


@app.get("/api/studio/integrations/connectors")
async def get_integration_connectors(
    app_id: Optional[str] = None,
    principal: UserPrincipal = Depends(require_user_scope),
):
    app_id, _ = _resolve_studio_scope(principal, app_id=app_id)
    connectors = await list_connectors(app_id)
    return {
        "app_id": app_id,
        "connectors": [_redact_connector_record(connector) for connector in connectors],
    }


class IntegrationConnectorPatchRequest(BaseModel):
    display_name: Optional[str] = None
    notes: Optional[str] = None
    status: Optional[Literal["metadata_only", "active", "expiring", "expired", "revoked"]] = None
    expires_at: Optional[str] = None
    public_config: Optional[Dict[str, Any]] = None
    required_fields: Optional[List[Dict[str, Any]]] = None
    secret_value: Optional[str] = None
    ttl_days: Optional[int] = Field(default=30, ge=1, le=3650)


class IntegrationConnectorCreateRequest(IntegrationConnectorPatchRequest):
    service: str = Field(..., description="Connector service identifier, such as email_provider or analytics_provider")


IntegrationConnectorPatchRequest.model_rebuild()
IntegrationConnectorCreateRequest.model_rebuild()


class AppContextRefreshPlanRequest(BaseModel):
    reason: str = Field(..., min_length=1)
    refresh_scope: ContextRefreshScope = ContextRefreshScope.DISCOVERY_INDEXING
    source_refs: list[SourceRef] | None = None
    current_context_version_id: Optional[str] = None
    requested_by: Optional[str] = None


class AppContextRefreshLaunchRequest(BaseModel):
    plan: ContextRefreshPlan
    confirm_launch: bool = False
    reason: Optional[str] = None
    refresh_scope: ContextRefreshScope = ContextRefreshScope.DISCOVERY_INDEXING
    request_id: Optional[str] = None


class AppContextRefreshCompleteRequest(BaseModel):
    plan: ContextRefreshPlan
    previous_context_version_id: Optional[str] = None
    launch_result: Optional[ContextRefreshLaunchResult] = None
    workflow_context_variables: Dict[str, Any] = Field(default_factory=dict)


class AppContextPolicyOverrideRequest(BaseModel):
    request_id: str = Field(..., min_length=1)
    context_version_id: Optional[str] = None
    original_policy_decision: AppContextPolicyDecision
    override_decision: AppContextPolicyOverrideDecision
    reason: str = Field(..., min_length=1)
    reviewer: str = Field(..., min_length=1)
    applies_to_paths: list[str] = Field(default_factory=list)
    change_class: Optional[str] = None
    refinement_lane: Optional[str] = None
    policy_result: Optional[AppContextPolicyResult] = None
    apply_to_plan: bool = False
    plan: Optional[Dict[str, Any]] = None


_SECRET_RESPONSE_KEYS = {"secret_value", "secret", "api_key", "apikey", "token", "password"}


def _redact_secret_fields(value: Any) -> Any:
    if isinstance(value, dict):
        redacted: Dict[str, Any] = {}
        for key, item in value.items():
            if str(key).strip().lower() in _SECRET_RESPONSE_KEYS:
                continue
            redacted[key] = _redact_secret_fields(item)
        return redacted
    if isinstance(value, list):
        return [_redact_secret_fields(item) for item in value]
    return value


def _redact_connector_record(record: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not isinstance(record, dict):
        return None
    enriched = dict(record)
    if not isinstance(enriched.get("health"), dict):
        enriched["health"] = compute_connector_health(enriched, checked_by="manual")
    return _redact_secret_fields(enriched)


def _redact_secret_result(result: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
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


def _plan_from_operator_payload(payload: Dict[str, Any]) -> RefinementExecutionPlan | RefinementDryRunPlan:
    try:
        return RefinementExecutionPlan.model_validate(payload)
    except ValidationError:
        return RefinementDryRunPlan.model_validate(payload)


@app.get("/api/studio/apps/{app_id}/context")
async def get_studio_app_context_status(
    app_id: str,
    principal: UserPrincipal = Depends(require_user_scope),
):
    resolved_app_id, _ = _resolve_studio_scope(principal, app_id=app_id)
    summary = await get_current_app_context_summary(
        app_id=resolved_app_id,
        artifact_store=get_artifact_store(),
    )
    return _redact_secret_fields(
        {
            "app_id": resolved_app_id,
            "app_context_summary": summary.model_dump(mode="json"),
            "stale_status": summary.stale_status,
            "warnings": list(summary.warnings),
            "artifact_refs": [ref.model_dump(mode="json") for ref in summary.artifact_refs],
            "ownership": summary.ownership.model_dump(mode="json"),
        }
    )


@app.post("/api/studio/apps/{app_id}/context/refresh-plan")
async def create_studio_app_context_refresh_plan(
    app_id: str,
    body: AppContextRefreshPlanRequest,
    principal: UserPrincipal = Depends(require_user_scope),
):
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
        raise HTTPException(status_code=400, detail=str(exc)) from exc
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
        raise HTTPException(status_code=400, detail=str(exc)) from exc
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
        raise HTTPException(status_code=400, detail=str(exc)) from exc
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
        raise HTTPException(status_code=400, detail=str(exc)) from exc

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
    app_id: Optional[str] = None,
    principal: UserPrincipal = Depends(require_user_scope),
):
    app_id, user_id = _resolve_studio_scope(principal, app_id=app_id)
    record = None
    secret_result: Optional[Dict[str, Any]] = None
    if body.secret_value:
        secret_result = await store_connector(
            app_id=app_id,
            user_id=user_id,
            service=body.service,
            secret_value=body.secret_value,
            display_name=body.display_name,
            ttl_days=body.ttl_days or 30,
            public_config=body.public_config,
            required_fields=body.required_fields,
        )
        record = secret_result.get("record")
    if (
        body.display_name is not None
        or body.notes is not None
        or body.status is not None
        or body.expires_at is not None
        or body.public_config is not None
    ):
        record = await update_connector_metadata(
            app_id=app_id,
            service=body.service,
            user_id=user_id,
            display_name=body.display_name,
            notes=body.notes,
            status=body.status or (secret_result or {}).get("connector_status") or "metadata_only",
            expires_at=body.expires_at,
            public_config=body.public_config,
            required_fields=body.required_fields,
        )
    if not record:
        from mozaiksai.core.data.persistence import AppConnectorStore

        store = AppConnectorStore()
        record = await store.upsert_connector(
            app_id=app_id,
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
    app_id: Optional[str] = None,
    principal: UserPrincipal = Depends(require_user_scope),
):
    from mozaiksai.core.data.persistence import AppConnectorStore

    app_id, user_id = _resolve_studio_scope(principal, app_id=app_id)
    store = AppConnectorStore()
    existing = await store.get_connector(app_id=app_id, service=service)
    if not existing:
        raise HTTPException(status_code=404, detail=f"Connector not found: {service}")

    secret_result: Optional[Dict[str, Any]] = None
    if body.secret_value:
        secret_result = await store_connector(
            app_id=app_id,
            user_id=user_id,
            service=service,
            secret_value=body.secret_value,
            display_name=body.display_name,
            ttl_days=body.ttl_days or 30,
            public_config=body.public_config,
            required_fields=body.required_fields,
        )
    record = await update_connector_metadata(
        app_id=app_id,
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
    app_id: Optional[str] = None,
    principal: UserPrincipal = Depends(require_user_scope),
):
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
    app_id: Optional[str] = None,
    principal: UserPrincipal = Depends(require_user_scope),
):
    app_id, _ = _resolve_studio_scope(principal, app_id=app_id)
    result = await delete_connector(app_id=app_id, service=service)
    if not result.get("deleted"):
        raise HTTPException(status_code=404, detail=f"Connector not found: {service}")
    return {
        "app_id": app_id,
        **result,
    }


@app.get("/api/studio/build/history")
async def get_build_history(
    principal: UserPrincipal = Depends(require_user_scope),
    app_id: Optional[str] = None,
    artifact_kind: Optional[str] = None,
    artifact_key: Optional[str] = None,
    artifact_version_id: Optional[str] = None,
    limit: int = 25,
):
    """Return recent artifact versions and change requests for the current workspace."""
    app_id, _ = _resolve_studio_scope(principal)
    artifact_store = get_artifact_store()
    versions = await artifact_store.list_artifact_versions(
        app_id=app_id,
        artifact_kind=artifact_kind,
        artifact_key=artifact_key,
        limit=min(limit, 100),
    )
    change_requests = await artifact_store.list_change_requests(
        app_id=app_id,
        artifact_version_id=artifact_version_id,
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
    app_id: Optional[str] = None,
    principal: UserPrincipal = Depends(require_user_scope),
):
    app_id, _ = _resolve_studio_scope(principal, app_id=app_id)
    artifact_store = get_artifact_store()
    version = await artifact_store.get_artifact_version(
        app_id=app_id,
        artifact_version_id=artifact_version_id,
    )
    if not version:
        raise HTTPException(status_code=404, detail=f"Artifact version not found: {artifact_version_id}")
    if version.artifact_kind not in {"app_bundle", "workflow_bundle"}:
        raise HTTPException(status_code=400, detail=f"Unsupported artifact kind for bundle workbench: {version.artifact_kind}")

    zip_path = _artifact_bundle_path_from_version(version)
    if zip_path is None:
        raise HTTPException(
            status_code=400,
            detail="This artifact version does not have a bundle path that the build surface can inspect.",
        )

    try:
        generated_files, skipped_files = _decode_text_bundle_entries(zip_path)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to load artifact bundle: {exc}") from exc

    review_payload = await _build_artifact_review_payload(
        app_id=app_id,
        version=version,
        artifact_store=artifact_store,
    )

    return {
        "app_id": app_id,
        "artifact_version_id": version.id,
        "artifact_kind": version.artifact_kind,
        "artifact_key": version.artifact_key,
        "source_workflow": version.source_workflow,
        "bundle_path": str(zip_path),
        "generated_files": generated_files,
        "skipped_files": skipped_files,
        "workbench": {
            "title": f"Artifact Workbench · {version.artifact_key} v{version.version_number}",
            "description": "Inspect a persisted artifact bundle and launch scoped coding refinement from explicit file scope.",
            "artifact_version_id": version.id,
            "artifact_kind": version.artifact_kind,
            "artifact_key": version.artifact_key,
            "generated_files": generated_files,
        },
        "review": review_payload["review"],
        "refinement_session": review_payload["refinement_session"],
        "change_request": review_payload["change_request"],
    }


@app.get("/api/studio/build/artifacts/{artifact_version_id}/review")
async def get_build_artifact_review(
    artifact_version_id: str,
    app_id: Optional[str] = None,
    principal: UserPrincipal = Depends(require_user_scope),
):
    app_id, _ = _resolve_studio_scope(principal, app_id=app_id)
    artifact_store = get_artifact_store()
    version = await artifact_store.get_artifact_version(
        app_id=app_id,
        artifact_version_id=artifact_version_id,
    )
    if not version:
        raise HTTPException(status_code=404, detail=f"Artifact version not found: {artifact_version_id}")
    payload = await _build_artifact_review_payload(
        app_id=app_id,
        version=version,
        artifact_store=artifact_store,
    )
    return {"app_id": app_id, **payload}


@app.post("/api/studio/build/artifacts/{artifact_version_id}/accept")
async def accept_build_artifact_version(
    artifact_version_id: str,
    app_id: Optional[str] = None,
    principal: UserPrincipal = Depends(require_user_scope),
):
    app_id, _ = _resolve_studio_scope(principal, app_id=app_id)
    artifact_store = get_artifact_store()
    version = await artifact_store.get_artifact_version(app_id=app_id, artifact_version_id=artifact_version_id)
    if not version:
        raise HTTPException(status_code=404, detail=f"Artifact version not found: {artifact_version_id}")
    if version.lifecycle_status == ArtifactLifecycleStatus.ARCHIVED:
        raise HTTPException(status_code=409, detail="Rejected artifact versions cannot be accepted.")
    if version.lifecycle_status != ArtifactLifecycleStatus.DRAFT:
        raise HTTPException(status_code=409, detail="Only draft artifact versions can be accepted.")
    if version.validation_status not in {ArtifactValidationStatus.PASSED, ArtifactValidationStatus.SKIPPED}:
        raise HTTPException(status_code=409, detail="Only validated artifact versions can be accepted.")

    refinement_metadata = _refinement_metadata_from_version(version)
    refinement_review_record = _load_refinement_review_record_for_version(version)
    if refinement_metadata is not None and refinement_review_record is None:
        raise HTTPException(
            status_code=409,
            detail="Staged refinement drafts require a promotion_ready review record before acceptance.",
        )
    if refinement_review_record is not None:
        try:
            accepted_result = await accept_staged_refinement_artifact_version(
                app_id=app_id,
                draft_artifact_version_id=artifact_version_id,
                review_record=refinement_review_record,
                request_id=refinement_review_record.request_id,
                artifact_store=artifact_store,
                accepted_by=principal.user_id,
            )
        except (AcceptedStagedAppBundleArtifactVersionError, ValueError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        accepted = accepted_result.artifact_version
    else:
        accepted = await artifact_store.accept_artifact_version(app_id=app_id, artifact_version_id=artifact_version_id)
    if accepted is None:
        raise HTTPException(status_code=404, detail=f"Artifact version not found: {artifact_version_id}")

    for session in await artifact_store.list_refinement_sessions(
        app_id=app_id,
        result_artifact_version_id=artifact_version_id,
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
    app_id: Optional[str] = None,
    principal: UserPrincipal = Depends(require_user_scope),
):
    app_id, _ = _resolve_studio_scope(principal, app_id=app_id)
    artifact_store = get_artifact_store()
    version = await artifact_store.get_artifact_version(app_id=app_id, artifact_version_id=artifact_version_id)
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
        result_artifact_version_id=artifact_version_id,
        limit=20,
    ):
        await artifact_store.update_refinement_session(
            app_id=app_id,
            session_id=session.id,
            status=RefinementSessionStatus.REJECTED,
            ended_at=datetime.now(UTC),
        )

    refreshed = await artifact_store.get_artifact_version(app_id=app_id, artifact_version_id=artifact_version_id)
    payload = await _build_artifact_review_payload(
        app_id=app_id,
        version=refreshed,
        artifact_store=artifact_store,
    )
    return {"rejected": True, "app_id": app_id, **payload}


@app.post("/api/studio/build/artifacts/{artifact_version_id}/promote")
async def promote_build_artifact_version(
    artifact_version_id: str,
    app_id: Optional[str] = None,
    principal: UserPrincipal = Depends(require_user_scope),
):
    app_id, _ = _resolve_studio_scope(principal, app_id=app_id)
    artifact_store = get_artifact_store()
    version = await artifact_store.get_artifact_version(app_id=app_id, artifact_version_id=artifact_version_id)
    if not version:
        raise HTTPException(status_code=404, detail=f"Artifact version not found: {artifact_version_id}")
    if version.lifecycle_status != ArtifactLifecycleStatus.CURRENT:
        raise HTTPException(status_code=409, detail="Only accepted current artifact versions can be promoted.")
    if version.artifact_kind != "app_bundle":
        raise HTTPException(status_code=400, detail=f"Unsupported artifact kind for restore: {version.artifact_kind}")
    if version.validation_status not in {ArtifactValidationStatus.PASSED, ArtifactValidationStatus.SKIPPED}:
        raise HTTPException(status_code=409, detail="Only validated artifact versions can be promoted.")

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
    refinement_metadata = _refinement_metadata_from_version(version)

    target_dir = _resolve_bundle_restore_target(version)
    try:
        restore_summary = _restore_bundle_to_target(zip_path=zip_path, target_dir=target_dir)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to promote artifact: {exc}") from exc

    for session in await artifact_store.list_refinement_sessions(
        app_id=app_id,
        result_artifact_version_id=artifact_version_id,
        limit=20,
    ):
        await artifact_store.update_refinement_session(
            app_id=app_id,
            session_id=session.id,
            status=RefinementSessionStatus.PROMOTED,
            ended_at=datetime.now(UTC),
        )

    payload = await _build_artifact_review_payload(
        app_id=app_id,
        version=version,
        artifact_store=artifact_store,
    )
    logger.info(
        "Promoted app_id=%s artifact_version_id=%s (%s/%s) restored=%s skipped=%s refinement_request_id=%s",
        app_id,
        artifact_version_id,
        version.artifact_kind,
        version.artifact_key,
        len(restore_summary["restored"]),
        len(restore_summary["skipped"]),
        str((refinement_metadata or {}).get("request_id") or "").strip() or None,
    )
    return {
        "promoted": True,
        "app_id": app_id,
        "artifact_version_id": artifact_version_id,
        "artifact_kind": version.artifact_kind,
        "artifact_key": version.artifact_key,
        "target_path": str(target_dir),
        "restart_required": True,
        "restored_files": restore_summary["restored"],
        "skipped_files": restore_summary["skipped"],
        **payload,
    }


class BuildRevertRequest(BaseModel):
    artifact_version_id: str = Field(..., description="Artifact version to restore as the active app state")


@app.post("/api/studio/build/revert")
async def revert_to_artifact_version(
    body: BuildRevertRequest,
    app_id: Optional[str] = None,
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

    version = await artifact_store.get_artifact_version(
        app_id=app_id,
        artifact_version_id=body.artifact_version_id,
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

    if version.artifact_kind == "workflow_bundle":
        target_dir = candidate_app_workflows_roots(app_root)[0]
        target_dir.mkdir(parents=True, exist_ok=True)
    elif version.artifact_kind == "app_bundle":
        target_dir = app_root
    else:
        raise HTTPException(status_code=400, detail=f"Cannot revert artifact kind: {version.artifact_kind}")

    try:
        _restore_bundle_to_target(zip_path=zip_path, target_dir=target_dir)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to extract artifact: {exc}") from exc

    logger.info(
        "Reverted app_id=%s to artifact_version_id=%s (%s/%s)",
        app_id,
        body.artifact_version_id,
        version.artifact_kind,
        version.artifact_key,
    )

    return {
        "reverted": True,
        "artifact_version_id": body.artifact_version_id,
        "artifact_kind": version.artifact_kind,
        "artifact_key": version.artifact_key,
        "target_path": str(target_dir),
        "restart_required": True,
    }


@app.get("/api/studio/build")
async def get_build_surface(
    app_id: Optional[str] = None,
    principal: UserPrincipal = Depends(require_user_scope),
):
    app_id, _ = _resolve_studio_scope(principal, app_id=app_id)
    app_root = resolve_app_root()
    missing_surfaces = get_missing_console_surfaces(app_root)
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
        home_summary["console"] = {**home_summary["console"], "surface": "shell-build", "route": f"/apps/{app_id}/build"}
        return {
            **home_summary,
            "build": build_build_section(home_summary, build_state),
        }
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to build build summary: {exc}") from exc


class BuildSaveRequest(BaseModel):
    request_text: str = Field(..., description="Persisted build request text")
    request_kind: Optional[Literal["greenfield_app", "brownfield_app", "refinement"]] = Field(
        None,
        description="High-level request kind for the current build draft",
    )
    change_class: Optional[Literal["patch", "design", "feature", "core"]] = Field(
        None,
        description="Refinement change class when the current draft is a refinement request",
    )


@app.put("/api/studio/build")
async def save_build_surface(
    request: BuildSaveRequest,
    app_id: Optional[str] = None,
    principal: UserPrincipal = Depends(require_user_scope),
):
    app_id, user_id = _resolve_studio_scope(principal, app_id=app_id)
    if request.change_class and request.request_kind != "refinement":
        raise HTTPException(status_code=400, detail="change_class is only valid when request_kind is 'refinement'")

    app_root = resolve_app_root()
    missing_surfaces = get_missing_console_surfaces(app_root)
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
        home_summary["console"] = {**home_summary["console"], "surface": "shell-build", "route": f"/apps/{app_id}/build"}
        return {
            **home_summary,
            "build": build_build_section(home_summary, build_state),
        }
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to persist build state: {exc}") from exc


class WorkflowTriggerRequest(BaseModel):
    workflow_id: Optional[str] = None
    trigger_source: str = "chat"
    journey_id: Optional[str] = None
    context_variables: Dict[str, Any] = Field(default_factory=dict)
    trigger_payload: Dict[str, Any] = Field(default_factory=dict)
    action_id: Optional[str] = None
    artifact_key: Optional[str] = None
    app_id: Optional[str] = None
    user_id: Optional[str] = None


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
            raise HTTPException(status_code=400, detail=f"Invalid refinement_request: {exc}") from exc

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
            raise HTTPException(status_code=503, detail=f"Refinement classification unavailable: {exc}") from exc
        harness_decision = orchestration_control.build_harness_decision(refinement_decision)
        resolved_change_class = refinement_decision.change_intent.change_class.value
        resolved_artifact_kind = refinement_request.artifact_kind
        resolved_artifact_version_id = refinement_request.artifact_version_id
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
                    raise HTTPException(status_code=503, detail=f"Coding worker unavailable: {exc}") from exc
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
                artifact_kind=refinement_request.artifact_kind,
                artifact_key=body.artifact_key or refinement_request.normalized_artifact_key(),
                artifact_version_id=refinement_request.artifact_version_id,
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
        if artifact_store is not None and persisted_change_request_id is not None and refinement_request.artifact_version_id:
            try:
                session_status = {
                    "validated": RefinementSessionStatus.VALIDATED,
                    "failed": RefinementSessionStatus.FAILED,
                }.get(coding_result.status, RefinementSessionStatus.PENDING)
                coding_session = await artifact_store.create_refinement_session(
                    app_id=app_id,
                    artifact_version_id=refinement_request.artifact_version_id,
                    change_request_id=persisted_change_request_id,
                    result_artifact_version_id=((coding_result.metadata or {}).get("artifact_version_id")),
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
                "artifact_version_id": resolved_artifact_version_id,
                "artifact_kind": resolved_artifact_kind,
                "workflow_sequence": refinement_decision.workflow_sequence if refinement_decision is not None else None,
            },
        )
    except ValueError as route_err:
        raise HTTPException(status_code=400, detail=str(route_err)) from route_err
    except Exception as route_err:
        logger.error("SessionRouter routing failed: %s", route_err, exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to route workflow trigger: {route_err}") from route_err

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
