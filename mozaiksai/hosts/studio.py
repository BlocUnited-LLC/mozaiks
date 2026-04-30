from __future__ import annotations

"""Studio management host layered on top of mozaiksai.hosts.platform.

Studio is the local/private management and create control plane used by the
CLI and by the hosted Mozaiks product. It adds Studio shell routes and
workflow triggering on top of the headless platform host.
"""

from typing import Any, Dict, Literal, Optional

from fastapi import Depends, HTTPException
from pydantic import BaseModel, Field

from mozaiksai.hosts.bootstrap import configure_repo_host_defaults

configure_repo_host_defaults("studio")

from mozaiksai.hosts import platform as platform_app
from factory_app.app.modules.factory_control_plane.backend.refinement_router import (
    get_refinement_trigger_route_resolver,
)
from logs.logging_config import get_workflow_logger
from mozaiksai.core.auth import UserPrincipal, require_any_auth, require_user_scope
from mozaiksai.core.runtime.app.studio_home import (
    build_create_section,
    build_studio_adapters_summary,
    build_studio_home_summary,
    build_studio_create_summary,
    get_missing_studio_surfaces,
    load_studio_create_state_from_db,
    save_studio_create_state_to_db,
)
from mozaiksai.core.session.launcher import launch_prepared_workflow, prepare_routed_workflow_launch
from mozaiksai.core.session.router import configure_session_router
from mozaiksai.hosts.platform import (
    build_shell_config,
    resolve_platform_path,
    resolve_scope_from_principal,
)
from mozaiksai.core.artifacts import ChangeClassification, get_artifact_store


app = platform_app.app
logger = get_workflow_logger("studio_app")

configure_session_router(
    trigger_route_resolver=get_refinement_trigger_route_resolver(),
)


def _build_trigger_payload(
    *,
    change_class: Optional[str] = None,
    artifact_kind: Optional[str] = None,
    artifact_version_id: Optional[str] = None,
    raw_user_request: Optional[str] = None,
) -> Dict[str, Any]:
    payload: Dict[str, Any] = {}
    if change_class is not None:
        payload["change_class"] = change_class
    if artifact_kind is not None:
        payload["artifact_kind"] = artifact_kind
    if artifact_version_id is not None:
        payload["artifact_version_id"] = artifact_version_id
    if raw_user_request is not None:
        payload["raw_user_request"] = raw_user_request
    return payload


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
    return await build_shell_config(include_studio=True)


@app.get("/api/studio/home")
async def get_studio_home(
    principal: UserPrincipal = Depends(require_any_auth),
):
    _ = principal
    platform_root = resolve_platform_path()
    missing_surfaces = get_missing_studio_surfaces(platform_root)
    if missing_surfaces:
        raise HTTPException(
            status_code=500,
            detail=f"Studio Home is missing required surfaces: {', '.join(missing_surfaces)}",
        )

    try:
        return build_studio_home_summary(platform_root, surface="shell-home", local_only=True)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to build Studio Home summary: {exc}") from exc


@app.get("/api/studio/adapters")
async def get_studio_adapters(
    principal: UserPrincipal = Depends(require_any_auth),
):
    _ = principal
    return await build_studio_adapters_summary()


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

    platform_root = resolve_platform_path()

    if version.artifact_kind == "workflow_bundle":
        target_dir = platform_root / "workflows"
        target_dir.mkdir(parents=True, exist_ok=True)
    elif version.artifact_kind == "app_bundle":
        target_dir = platform_root
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
    platform_root = resolve_platform_path()
    missing_surfaces = get_missing_studio_surfaces(platform_root)
    if missing_surfaces:
        raise HTTPException(
            status_code=500,
            detail=f"Studio Create is missing required surfaces: {', '.join(missing_surfaces)}",
        )

    try:
        create_state = await load_studio_create_state_from_db(app_id)
        home_summary = build_studio_home_summary(platform_root, surface="shell-create", local_only=True)
        home_summary["studio"] = {**home_summary["studio"], "surface": "shell-create", "route": "/studio/create"}
        return {
            **home_summary,
            "create": build_create_section(home_summary, create_state),
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to build Studio Create summary: {exc}") from exc


class StudioCreateSaveRequest(BaseModel):
    request_text: str = Field(..., description="Persisted Studio create request text")
    request_kind: Optional[Literal["new_app", "existing_app", "refinement"]] = Field(
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

    platform_root = resolve_platform_path()
    missing_surfaces = get_missing_studio_surfaces(platform_root)
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
        home_summary = build_studio_home_summary(platform_root, surface="shell-create", local_only=True)
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
    resolved_change_class = str(trigger_payload.get("change_class") or "").strip() or None
    resolved_artifact_kind = str(trigger_payload.get("artifact_kind") or "").strip() or None
    resolved_artifact_version_id = str(trigger_payload.get("artifact_version_id") or "").strip() or None
    resolved_raw_user_request = str(trigger_payload.get("raw_user_request") or "").strip() or None

    valid_change_classes = {"patch", "design", "feature", "core"}
    if resolved_change_class and resolved_change_class not in valid_change_classes:
        raise HTTPException(status_code=400, detail=f"Invalid change_class. Must be one of: {valid_change_classes}")

    try:
        launch = await prepare_routed_workflow_launch(
            workflow_id=body.workflow_id,
            app_id=app_id,
            user_id=user_id,
            trigger_source=body.trigger_source,
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

    if body.trigger_source == "refinement" and resolved_change_class:
        try:
            artifact_store = get_artifact_store()
            await artifact_store.create_change_request(
                app_id=app_id,
                artifact_kind=resolved_artifact_kind or "app_bundle",
                artifact_key=body.artifact_key or resolved_artifact_kind or "app_bundle",
                artifact_version_id=resolved_artifact_version_id or "",
                raw_user_request=resolved_raw_user_request or "",
                classification=ChangeClassification(resolved_change_class),
                router_decision={
                    "workflow_id": resolved_workflow_id,
                    "explanation": routing_decision.explanation,
                    "is_full_restart": routing_decision.is_full_restart,
                    "rerouted_by_dependency": routing_decision.rerouted_by_dependency,
                },
                created_by_user_id=user_id,
            )
        except Exception as persist_err:
            logger.warning("Failed to persist ChangeRequest: %s", persist_err)

    workflow_launch = await launch_prepared_workflow(launch)

    return {
        "chat_id": workflow_launch.chat_id,
        "workflow_id": workflow_launch.workflow_id,
        "requested_workflow_id": workflow_launch.requested_workflow_id,
        "websocket_url": workflow_launch.websocket_url,
        "trigger_source": workflow_launch.trigger_source,
        "routing_explanation": workflow_launch.routing_explanation,
        "rerouted_by_dependency": workflow_launch.rerouted_by_dependency,
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
