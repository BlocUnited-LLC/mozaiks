from __future__ import annotations

"""Local Studio composition host layered on top of platform_app.py.

Studio is the local/private builder interface used by the CLI and by the
hosted Mozaiks product. It adds builder-facing shell routes and workflow
triggering on top of the headless platform host.
"""

from datetime import UTC, datetime
from typing import Any, Dict, Literal, Optional

from fastapi import Depends, HTTPException
from pydantic import BaseModel, Field

import platform_app
from logs.logging_config import get_workflow_logger
from mozaiksai.core.auth import UserPrincipal, require_any_auth, require_user_scope
from mozaiksai.core.runtime.app.studio_home import (
    build_studio_adapters_summary,
    build_studio_build_summary,
    build_studio_home_summary,
    get_missing_studio_surfaces,
    save_studio_build_request,
)
from platform_app import (
    build_shell_config,
    create_routed_chat_session,
    resolve_platform_path,
    resolve_scope_from_principal,
    validate_context_for_workflow,
)
from runtime_app import persistence_manager


app = platform_app.app
logger = get_workflow_logger("studio_app")


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


@app.get("/api/studio/build")
async def get_studio_build(
    principal: UserPrincipal = Depends(require_any_auth),
):
    _ = principal
    platform_root = resolve_platform_path()
    missing_surfaces = get_missing_studio_surfaces(platform_root)
    if missing_surfaces:
        raise HTTPException(
            status_code=500,
            detail=f"Studio Build is missing required surfaces: {', '.join(missing_surfaces)}",
        )

    try:
        return build_studio_build_summary(platform_root, surface="shell-build", local_only=True)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to build Studio Build summary: {exc}") from exc


class StudioBuildSaveRequest(BaseModel):
    request_text: str = Field(..., description="Persisted Studio Build request text")
    request_kind: Optional[Literal["new_app", "existing_app", "refinement"]] = Field(
        None,
        description="High-level request kind for the current build draft",
    )
    change_class: Optional[Literal["patch", "design", "feature", "core"]] = Field(
        None,
        description="Refinement change class when the current draft is a refinement request",
    )


@app.put("/api/studio/build")
async def save_studio_build(
    request: StudioBuildSaveRequest,
    principal: UserPrincipal = Depends(require_any_auth),
):
    _ = principal
    if request.change_class and request.request_kind != "refinement":
        raise HTTPException(status_code=400, detail="change_class is only valid when request_kind is 'refinement'")

    platform_root = resolve_platform_path()
    missing_surfaces = get_missing_studio_surfaces(platform_root)
    if missing_surfaces:
        raise HTTPException(
            status_code=500,
            detail=f"Studio Build is missing required surfaces: {', '.join(missing_surfaces)}",
        )

    try:
        save_studio_build_request(
            platform_root,
            request_text=request.request_text,
            request_kind=request.request_kind,
            change_class=request.change_class,
        )
        return build_studio_build_summary(platform_root, surface="shell-build", local_only=True)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to persist Studio Build summary: {exc}") from exc


class WorkflowTriggerRequest(BaseModel):
    workflow_id: Optional[str] = None
    trigger_source: str = "chat"
    context_variables: Dict[str, Any] = Field(default_factory=dict)
    action_id: Optional[str] = None
    change_class: Optional[str] = None
    artifact_version_id: Optional[str] = None
    artifact_kind: Optional[str] = None
    raw_user_request: Optional[str] = None
    app_id: Optional[str] = None
    user_id: Optional[str] = None


@app.post("/api/workflows/trigger")
async def trigger_workflow(
    body: WorkflowTriggerRequest,
    principal: UserPrincipal = Depends(require_user_scope),
):
    app_id, user_id = resolve_scope_from_principal(principal, app_id=body.app_id, user_id=body.user_id)

    valid_change_classes = {"patch", "design", "feature", "core"}
    if body.change_class and body.change_class not in valid_change_classes:
        raise HTTPException(status_code=400, detail=f"Invalid change_class. Must be one of: {valid_change_classes}")

    try:
        from mozaiksai.core.session import TriggerInput, get_session_router

        session_router = get_session_router()
        routing_decision = await session_router.route_trigger(
            TriggerInput(
                app_id=app_id,
                user_id=user_id,
                trigger_source=body.trigger_source,
                workflow_id=body.workflow_id,
                change_class=body.change_class,
                artifact_kind=body.artifact_kind,
                artifact_version_id=body.artifact_version_id,
                raw_user_request=body.raw_user_request,
                context_variables=body.context_variables or {},
            )
        )
    except ValueError as route_err:
        raise HTTPException(status_code=400, detail=str(route_err)) from route_err
    except Exception as route_err:
        logger.error("SessionRouter routing failed: %s", route_err, exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to route workflow trigger: {route_err}") from route_err

    resolved_workflow_id = routing_decision.workflow_id
    merged_context = {**dict(routing_decision.context_seed), **body.context_variables}
    validated_context = validate_context_for_workflow(resolved_workflow_id, merged_context)

    if body.trigger_source == "refinement" and body.change_class:
        try:
            collection = await persistence_manager._coll()
            await collection.insert_one(
                {
                    "kind": "change_request",
                    "app_id": app_id,
                    "user_id": user_id,
                    "artifact_kind": body.artifact_kind or "app_bundle",
                    "artifact_version_id": body.artifact_version_id,
                    "raw_user_request": body.raw_user_request,
                    "classification": body.change_class,
                    "router_decision": {
                        "workflow_id": resolved_workflow_id,
                        "explanation": routing_decision.explanation,
                        "is_full_restart": routing_decision.is_full_restart,
                        "rerouted_by_dependency": routing_decision.rerouted_by_dependency,
                    },
                    "created_at": datetime.now(UTC).isoformat(),
                }
            )
        except Exception as persist_err:
            logger.warning("Failed to persist ChangeRequest: %s", persist_err)

    trigger_meta: Dict[str, Any] = {
        "trigger_source": body.trigger_source,
        **({"action_id": body.action_id} if body.action_id else {}),
        **({"change_class": body.change_class} if body.change_class else {}),
        **({"artifact_version_id": body.artifact_version_id} if body.artifact_version_id else {}),
        **({"artifact_kind": body.artifact_kind} if body.artifact_kind else {}),
        "requested_workflow_id": routing_decision.requested_workflow_id,
        "resolved_workflow_id": resolved_workflow_id,
        "rerouted_by_dependency": bool(routing_decision.rerouted_by_dependency),
    }
    chat_id = await create_routed_chat_session(
        workflow_id=resolved_workflow_id,
        app_id=app_id,
        user_id=user_id,
        context_variables=validated_context,
        trigger_meta=trigger_meta,
        session_router=session_router,
    )

    return {
        "chat_id": chat_id,
        "workflow_id": resolved_workflow_id,
        "requested_workflow_id": routing_decision.requested_workflow_id,
        "websocket_url": f"/ws/{resolved_workflow_id}/{app_id}/{chat_id}/{user_id}",
        "trigger_source": body.trigger_source,
        "routing_explanation": routing_decision.explanation,
        "rerouted_by_dependency": bool(routing_decision.rerouted_by_dependency),
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
