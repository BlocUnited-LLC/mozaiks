from __future__ import annotations

"""Studio management host layered on top of mozaiksai.hosts.platform.

Studio is the local/private management and create control plane used by the
CLI and by the hosted Mozaiks product. It adds Studio shell routes and
workflow triggering on top of the headless platform host.
"""

import zipfile
from datetime import UTC, datetime
from difflib import unified_diff
from pathlib import Path
from typing import Any, Dict, Literal, Optional

from fastapi import Depends, HTTPException
from pydantic import BaseModel, Field, ValidationError

from mozaiksai.hosts.bootstrap import configure_repo_host_defaults

configure_repo_host_defaults("studio")

from mozaiksai.hosts import platform as platform_app
from mozaiksai.control_plane import get_orchestration_control_harness
from logs.logging_config import get_workflow_logger
from mozaiksai.core.auth import UserPrincipal, require_any_auth, require_user_scope
from mozaiksai.core.runtime.app.studio_home import (
    build_create_section,
    build_studio_adapters_summary,
    build_studio_apps_summary,
    build_studio_home_summary,
    build_studio_create_summary,
    get_missing_studio_surfaces,
    load_studio_create_state_from_db,
    save_studio_create_state_to_db,
)
from mozaiksai.core.workflow.generator_support.connector_service import (
    delete_connector,
    list_connectors,
    store_connector,
    update_connector_metadata,
)
from mozaiksai.core.session.launcher import launch_prepared_workflow, prepare_routed_workflow_launch
from mozaiksai.core.session.router import configure_session_router
from mozaiksai.hosts.platform import (
    build_shell_config,
    resolve_app_root,
    resolve_scope_from_principal,
)
from mozaiksai.core.artifacts import (
    ArtifactLifecycleStatus,
    ArtifactValidationStatus,
    ChangeClassification,
    RefinementSessionStatus,
    get_artifact_store,
)


app = platform_app.app
logger = get_workflow_logger("studio_app")

_BUNDLE_MAX_TEXT_FILES = 200
_BUNDLE_MAX_TOTAL_BYTES = 2_000_000
_BUNDLE_MAX_SINGLE_FILE_BYTES = 250_000
_DIFF_PREVIEW_MAX_LINES = 120
_DIFF_PREVIEW_MAX_CHARS = 12_000


def _normalize_bundle_entry_name(name: str) -> Optional[str]:
    normalized = str(name or "").replace("\\", "/").strip("/")
    if not normalized or normalized.endswith("/"):
        return None
    parts = [part for part in normalized.split("/") if part]
    if any(part == ".." for part in parts):
        return None
    return "/".join(parts)


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


def _resolve_bundle_restore_target(version) -> Path:  # noqa: ANN001
    app_root = resolve_app_root()
    if version.artifact_kind == "workflow_bundle":
        target_dir = app_root / "workflows"
        target_dir.mkdir(parents=True, exist_ok=True)
        return target_dir
    if version.artifact_kind == "app_bundle":
        return app_root
    raise HTTPException(status_code=400, detail=f"Unsupported artifact kind for restore: {version.artifact_kind}")


def _restore_bundle_to_target(*, zip_path: Path, target_dir: Path) -> None:
    with zipfile.ZipFile(zip_path, "r") as archive:
        archive.extractall(target_dir)


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
async def get_studio_shell_config():
    return await build_shell_config(surface="studio")


@app.get("/api/studio/home")
async def get_studio_home(
    principal: UserPrincipal = Depends(require_any_auth),
):
    _ = principal
    app_root = resolve_app_root()
    missing_surfaces = get_missing_studio_surfaces(app_root)
    if missing_surfaces:
        raise HTTPException(
            status_code=500,
            detail=f"Studio Home is missing required surfaces: {', '.join(missing_surfaces)}",
        )

    try:
        return build_studio_home_summary(app_root, surface="shell-home", local_only=True)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to build Studio Home summary: {exc}") from exc


@app.get("/api/studio/apps")
async def get_studio_apps(
    principal: UserPrincipal = Depends(require_any_auth),
):
    _ = principal
    app_root = resolve_app_root()
    missing_surfaces = get_missing_studio_surfaces(app_root)
    if missing_surfaces:
        raise HTTPException(
            status_code=500,
            detail=f"Studio Hub is missing required surfaces: {', '.join(missing_surfaces)}",
        )

    try:
        return build_studio_apps_summary(app_root, surface="shell-hub", local_only=True)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to build Studio Hub summary: {exc}") from exc


@app.get("/api/studio/adapters")
async def get_studio_adapters(
    principal: UserPrincipal = Depends(require_user_scope),
):
    app_id, _ = _resolve_studio_scope(principal)
    return await build_studio_adapters_summary(app_id=app_id)


@app.get("/api/studio/adapters/connectors")
async def get_studio_connectors(
    principal: UserPrincipal = Depends(require_user_scope),
):
    app_id, _ = _resolve_studio_scope(principal)
    connectors = await list_connectors(app_id)
    return {
        "app_id": app_id,
        "connectors": connectors,
    }


class StudioConnectorPatchRequest(BaseModel):
    display_name: Optional[str] = None
    notes: Optional[str] = None
    status: Optional[Literal["metadata_only", "active", "expiring", "expired", "revoked"]] = None
    expires_at: Optional[str] = None
    secret_value: Optional[str] = None
    ttl_days: Optional[int] = Field(default=30, ge=1, le=3650)


class StudioConnectorCreateRequest(StudioConnectorPatchRequest):
    service: str = Field(..., description="Connector service identifier, such as openai or stripe")


@app.post("/api/studio/adapters/connectors")
async def create_or_update_studio_connector(
    body: StudioConnectorCreateRequest,
    principal: UserPrincipal = Depends(require_user_scope),
):
    app_id, user_id = _resolve_studio_scope(principal)
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
        )
        record = secret_result.get("record")
    if body.display_name is not None or body.notes is not None or body.status is not None or body.expires_at is not None:
        record = await update_connector_metadata(
            app_id=app_id,
            service=body.service,
            user_id=user_id,
            display_name=body.display_name,
            notes=body.notes,
            status=body.status or (secret_result or {}).get("connector_status") or "metadata_only",
            expires_at=body.expires_at,
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
            status_reason="Created manually from the Studio adapters surface.",
        )
    return {
        "app_id": app_id,
        "connector": record,
        "secret_result": secret_result,
    }


@app.patch("/api/studio/adapters/connectors/{service}")
async def patch_studio_connector(
    service: str,
    body: StudioConnectorPatchRequest,
    principal: UserPrincipal = Depends(require_user_scope),
):
    from mozaiksai.core.data.persistence import AppConnectorStore

    app_id, user_id = _resolve_studio_scope(principal)
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
        )
    record = await update_connector_metadata(
        app_id=app_id,
        service=service,
        user_id=user_id,
        display_name=body.display_name,
        notes=body.notes,
        status=body.status,
        expires_at=body.expires_at,
    )
    return {
        "app_id": app_id,
        "connector": record,
        "secret_result": secret_result,
    }


@app.delete("/api/studio/adapters/connectors/{service}")
async def remove_studio_connector(
    service: str,
    principal: UserPrincipal = Depends(require_user_scope),
):
    app_id, _ = _resolve_studio_scope(principal)
    result = await delete_connector(app_id=app_id, service=service)
    if not result.get("deleted"):
        raise HTTPException(status_code=404, detail=f"Connector not found: {service}")
    return {
        "app_id": app_id,
        **result,
    }


@app.get("/api/studio/history")
async def get_studio_history(
    principal: UserPrincipal = Depends(require_user_scope),
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


@app.get("/api/studio/artifacts/{artifact_version_id}/bundle")
async def get_studio_artifact_bundle(
    artifact_version_id: str,
    principal: UserPrincipal = Depends(require_user_scope),
):
    app_id, _ = _resolve_studio_scope(principal)
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
            detail="This artifact version does not have a bundle path that Studio can inspect.",
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


@app.get("/api/studio/artifacts/{artifact_version_id}/review")
async def get_studio_artifact_review(
    artifact_version_id: str,
    principal: UserPrincipal = Depends(require_user_scope),
):
    app_id, _ = _resolve_studio_scope(principal)
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


@app.post("/api/studio/artifacts/{artifact_version_id}/accept")
async def accept_studio_artifact_version(
    artifact_version_id: str,
    principal: UserPrincipal = Depends(require_user_scope),
):
    app_id, _ = _resolve_studio_scope(principal)
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


@app.post("/api/studio/artifacts/{artifact_version_id}/reject")
async def reject_studio_artifact_version(
    artifact_version_id: str,
    principal: UserPrincipal = Depends(require_user_scope),
):
    app_id, _ = _resolve_studio_scope(principal)
    artifact_store = get_artifact_store()
    version = await artifact_store.get_artifact_version(app_id=app_id, artifact_version_id=artifact_version_id)
    if not version:
        raise HTTPException(status_code=404, detail=f"Artifact version not found: {artifact_version_id}")
    if version.lifecycle_status != ArtifactLifecycleStatus.DRAFT:
        raise HTTPException(status_code=409, detail="Only draft artifact versions can be rejected.")

    rejected = await artifact_store.reject_artifact_version(
        app_id=app_id,
        artifact_version_id=artifact_version_id,
        reason="Rejected in Studio review.",
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


@app.post("/api/studio/artifacts/{artifact_version_id}/promote")
async def promote_studio_artifact_version(
    artifact_version_id: str,
    principal: UserPrincipal = Depends(require_user_scope),
):
    app_id, _ = _resolve_studio_scope(principal)
    artifact_store = get_artifact_store()
    version = await artifact_store.get_artifact_version(app_id=app_id, artifact_version_id=artifact_version_id)
    if not version:
        raise HTTPException(status_code=404, detail=f"Artifact version not found: {artifact_version_id}")
    if version.lifecycle_status != ArtifactLifecycleStatus.CURRENT:
        raise HTTPException(status_code=409, detail="Only accepted current artifact versions can be promoted.")
    if version.validation_status not in {ArtifactValidationStatus.PASSED, ArtifactValidationStatus.SKIPPED}:
        raise HTTPException(status_code=409, detail="Only validated artifact versions can be promoted.")

    zip_path = _artifact_bundle_path_from_version(version)
    if zip_path is None:
        raise HTTPException(
            status_code=400,
            detail="This artifact version has no restorable file path. Only versions generated after artifact persistence was added can be promoted.",
        )

    target_dir = _resolve_bundle_restore_target(version)
    try:
        _restore_bundle_to_target(zip_path=zip_path, target_dir=target_dir)
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
        "Promoted app_id=%s artifact_version_id=%s (%s/%s)",
        app_id,
        artifact_version_id,
        version.artifact_kind,
        version.artifact_key,
    )
    return {
        "promoted": True,
        "app_id": app_id,
        "artifact_version_id": artifact_version_id,
        "artifact_kind": version.artifact_kind,
        "artifact_key": version.artifact_key,
        "target_path": str(target_dir),
        "restart_required": True,
        **payload,
    }


class StudioRevertRequest(BaseModel):
    artifact_version_id: str = Field(..., description="Artifact version to restore as the active app state")


@app.post("/api/studio/revert")
async def revert_to_artifact_version(
    body: StudioRevertRequest,
    principal: UserPrincipal = Depends(require_user_scope),
):
    """Restore a previously generated artifact version as the active app state.

    Extracts the stored bundle zip to the appropriate directory under the active
    app root. Returns restart_required=True because the platform host reads from
    disk at startup.
    """
    import zipfile as _zipfile
    from pathlib import Path as _Path

    app_id, _ = _resolve_studio_scope(principal)
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
        target_dir = app_root / "workflows"
        target_dir.mkdir(parents=True, exist_ok=True)
    elif version.artifact_kind == "app_bundle":
        target_dir = app_root
    else:
        raise HTTPException(status_code=400, detail=f"Cannot revert artifact kind: {version.artifact_kind}")

    try:
        with _zipfile.ZipFile(zip_path, "r") as zf:
            zf.extractall(target_dir)
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


@app.get("/api/studio/create")
async def get_studio_create(
    principal: UserPrincipal = Depends(require_user_scope),
):
    app_id, _ = _resolve_studio_scope(principal)
    app_root = resolve_app_root()
    missing_surfaces = get_missing_studio_surfaces(app_root)
    if missing_surfaces:
        raise HTTPException(
            status_code=500,
            detail=f"Studio Create is missing required surfaces: {', '.join(missing_surfaces)}",
        )

    try:
        create_state = await load_studio_create_state_from_db(app_id)
        home_summary = build_studio_home_summary(app_root, surface="shell-create", local_only=True)
        home_summary["studio"] = {**home_summary["studio"], "surface": "shell-create", "route": "/studio/create"}
        return {
            **home_summary,
            "create": build_create_section(home_summary, create_state),
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to build Studio Create summary: {exc}") from exc


class StudioCreateSaveRequest(BaseModel):
    request_text: str = Field(..., description="Persisted Studio create request text")
    request_kind: Optional[Literal["greenfield_app", "brownfield_app", "refinement"]] = Field(
        None,
        description="High-level request kind for the current create draft",
    )
    change_class: Optional[Literal["patch", "design", "feature", "core"]] = Field(
        None,
        description="Refinement change class when the current draft is a refinement request",
    )


@app.put("/api/studio/create")
async def save_studio_create(
    request: StudioCreateSaveRequest,
    principal: UserPrincipal = Depends(require_user_scope),
):
    app_id, _ = _resolve_studio_scope(principal)
    if request.change_class and request.request_kind != "refinement":
        raise HTTPException(status_code=400, detail="change_class is only valid when request_kind is 'refinement'")

    app_root = resolve_app_root()
    missing_surfaces = get_missing_studio_surfaces(app_root)
    if missing_surfaces:
        raise HTTPException(
            status_code=500,
            detail=f"Studio Create is missing required surfaces: {', '.join(missing_surfaces)}",
        )

    try:
        create_state = await save_studio_create_state_to_db(
            app_id,
            request_text=request.request_text,
            request_kind=request.request_kind,
            change_class=request.change_class,
        )
        home_summary = build_studio_home_summary(app_root, surface="shell-create", local_only=True)
        home_summary["studio"] = {**home_summary["studio"], "surface": "shell-create", "route": "/studio/create"}
        return {
            **home_summary,
            "create": build_create_section(home_summary, create_state),
        }
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to persist Studio Create state: {exc}") from exc


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

    if body.trigger_source == "refinement":
        try:
            refinement_request = orchestration_control.request_from_payload(
                payload=trigger_payload,
                app_id=app_id,
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

        try:
            refinement_decision = await orchestration_control.route_refinement_request(refinement_request)
        except Exception as exc:
            raise HTTPException(status_code=503, detail=f"Refinement classification unavailable: {exc}") from exc
        harness_decision = orchestration_control.build_harness_decision(refinement_decision)
        resolved_change_class = refinement_decision.change_intent.change_class.value
        resolved_artifact_kind = refinement_request.artifact_kind.value
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

    artifact_store = get_artifact_store() if refinement_request is not None and refinement_decision is not None else None
    persisted_change_request = None

    if refinement_request is not None and refinement_decision is not None and artifact_store is not None:
        try:
            persisted_change_request = await artifact_store.create_change_request(
                app_id=app_id,
                artifact_kind=refinement_request.artifact_kind.value,
                artifact_key=body.artifact_key or refinement_request.normalized_artifact_key(),
                artifact_version_id=refinement_request.artifact_version_id,
                raw_user_request=refinement_request.raw_user_request,
                classification=ChangeClassification(refinement_decision.change_intent.change_class.value),
                refinement_request=refinement_request.model_dump(mode="python"),
                change_intent=refinement_decision.change_intent.model_dump(mode="python"),
                impact_set=refinement_decision.impact_set.model_dump(mode="python"),
                router_decision={
                    "workflow_id": refinement_decision.workflow_id,
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
        except Exception as persist_err:
            logger.warning("Failed to persist ChangeRequest: %s", persist_err)

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
        if artifact_store is not None and persisted_change_request is not None and refinement_request.artifact_version_id:
            try:
                session_status = {
                    "validated": RefinementSessionStatus.VALIDATED,
                    "failed": RefinementSessionStatus.FAILED,
                }.get(coding_result.status, RefinementSessionStatus.PENDING)
                coding_session = await artifact_store.create_refinement_session(
                    app_id=app_id,
                    artifact_version_id=refinement_request.artifact_version_id,
                    change_request_id=persisted_change_request.id,
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
            journey_id=body.journey_id,
            context_variables=body.context_variables or {},
            trigger_payload=trigger_payload,
            extra_trigger_meta={
                "action_id": body.action_id,
                "change_class": resolved_change_class,
                "artifact_version_id": resolved_artifact_version_id,
                "artifact_kind": resolved_artifact_kind,
            },
        )
    except ValueError as route_err:
        raise HTTPException(status_code=400, detail=str(route_err)) from route_err
    except Exception as route_err:
        logger.error("SessionRouter routing failed: %s", route_err, exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to route workflow trigger: {route_err}") from route_err

    resolved_workflow_id = launch.workflow_id
    routing_decision = launch.routing_decision

    if persisted_change_request is not None and artifact_store is not None:
        try:
            await artifact_store.update_change_request_router_decision(
                app_id=app_id,
                change_request_id=persisted_change_request.id,
                router_decision={
                    "workflow_id": resolved_workflow_id,
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
