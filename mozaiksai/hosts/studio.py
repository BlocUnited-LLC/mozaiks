from __future__ import annotations

"""Studio management host layered on top of mozaiksai.hosts.platform.

Studio is the local/private management and create control plane used by the
CLI and by the hosted Mozaiks product. It adds Studio shell routes and
workflow triggering on top of the headless platform host.
"""

from typing import Any, Dict, Literal, Optional

from fastapi import Depends, HTTPException
from pydantic import BaseModel, Field, ValidationError

from mozaiksai.hosts.bootstrap import configure_repo_host_defaults

configure_repo_host_defaults("studio")

from mozaiksai.hosts import platform as platform_app
from factory_app.control_plane.orchestration_control import (
    get_orchestration_control_harness,
)
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
from mozaiksai.core.artifacts import ChangeClassification, get_artifact_store


app = platform_app.app
logger = get_workflow_logger("studio_app")

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
    resolved_change_class = None
    resolved_artifact_kind = None
    resolved_artifact_version_id = None

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
        resolved_change_class = refinement_decision.change_intent.change_class.value
        resolved_artifact_kind = refinement_request.artifact_kind.value
        resolved_artifact_version_id = refinement_request.artifact_version_id
        trigger_payload = {
            "refinement_request": refinement_request.model_dump(mode="python"),
        }

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

    if refinement_request is not None and refinement_decision is not None:
        try:
            artifact_store = get_artifact_store()
            await artifact_store.create_change_request(
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
                    "workflow_id": resolved_workflow_id,
                    "requested_workflow_id": routing_decision.requested_workflow_id,
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
