"""Chat routes router.

Routes:
    POST /api/chat/upload
    POST /api/chat/upload/{app_id}/{user_id}
    GET  /api/chats/{app_id}/{workflow_name}
    GET  /api/chats/exists/{app_id}/{workflow_name}/{chat_id}
    GET  /api/chats/meta/{app_id}/{workflow_name}/{chat_id}
"""
from __future__ import annotations

import os
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile

from logs.logging_config import get_workflow_logger
from mozaiksai.core.auth import UserPrincipal, require_user_scope
from mozaiksai.core.auth.dependencies import validate_path_id, validate_user_id_against_principal
from mozaiksai.core.chat_attachments.attachments import handle_chat_upload
from mozaiksai.core.multitenant import build_app_scope_filter
from mozaiksai.hosts import runtime as runtime_app

router = APIRouter(tags=["chat"])
logger = get_workflow_logger("chat_router")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _is_ask_carrier_session(session: dict[str, Any] | None) -> bool:
    if not isinstance(session, dict):
        return False
    return str(session.get("transport_purpose") or "").strip().lower() == "ask_carrier"


async def _handle_chat_upload(
    *,
    file: UploadFile,
    app_id: str,
    user_id: str,
    chat_id: str,
    intent: str,
    bundle_path: str | None,
) -> dict[str, Any]:
    if not app_id or not user_id or not chat_id:
        raise HTTPException(status_code=400, detail="app_id, user_id, and chat_id are required")
    validate_path_id(app_id, "app_id")
    validate_path_id(chat_id, "chat_id")

    allowed_raw = os.getenv("CHAT_ATTACHMENTS_ALLOWED_WORKFLOWS", "").strip()
    try:
        coll = await runtime_app._chat_coll()
        res = await handle_chat_upload(
            chat_coll=coll,
            file_obj=file,
            app_id=app_id,
            user_id=user_id,
            chat_id=chat_id,
            intent=intent,
            bundle_path=bundle_path,
            allowed_workflows_env=allowed_raw,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="Chat session not found") from exc
    except ValueError as exc:
        message = str(exc)
        if message.startswith("File too large"):
            raise HTTPException(status_code=413, detail=message) from exc
        raise HTTPException(status_code=400, detail=message) from exc
    except Exception as exc:
        logger.exception("UPLOAD_FAILED")
        raise HTTPException(status_code=500, detail="Upload failed") from exc

    try:
        if runtime_app.simple_transport:
            workflow_name = None
            try:
                doc = await coll.find_one(
                    {"_id": chat_id, "user_id": user_id, **build_app_scope_filter(app_id)},
                    {"workflow_name": 1},
                )
                if doc:
                    workflow_name = doc.get("workflow_name")
            except Exception:
                workflow_name = None

            await runtime_app.simple_transport.send_event_to_ui(
                {
                    "kind": "attachment_uploaded",
                    "chat_id": chat_id,
                    "app_id": app_id,
                    "user_id": user_id,
                    "workflow_name": workflow_name,
                    "attachment": res.attachment,
                },
                chat_id,
            )
    except Exception as exc:
        logger.debug("attachment_uploaded WS emit failed for chat %s: %s", chat_id, exc)

    return {
        "success": True,
        "chat_id": chat_id,
        "app_id": app_id,
        "user_id": user_id,
        "attachment": res.attachment,
    }


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.post("/api/chat/upload")
async def upload_chat_file(
    request: Request,
    file: UploadFile = File(...),
    appId: str | None = Form(None),
    userId: str = Form(...),
    chatId: str = Form(...),
    intent: str = Form("context"),
    bundle_path: str | None = Form(None),
    principal: UserPrincipal = Depends(require_user_scope),
):
    _ = request
    resolved_app_id = (appId or "").strip()
    if not resolved_app_id:
        raise HTTPException(status_code=400, detail="appId is required")
    user_id = validate_user_id_against_principal(principal, body_user_id=userId)
    return await _handle_chat_upload(
        file=file,
        app_id=resolved_app_id,
        user_id=user_id,
        chat_id=chatId,
        intent=intent,
        bundle_path=bundle_path,
    )


@router.post("/api/chat/upload/{app_id}/{user_id}")
async def upload_chat_file_scoped(
    app_id: str,
    user_id: str,
    file: UploadFile = File(...),
    chatId: str = Form(...),
    intent: str = Form("context"),
    bundle_path: str | None = Form(None),
    principal: UserPrincipal = Depends(require_user_scope),
):
    validate_path_id(app_id, "app_id")
    user_id = validate_user_id_against_principal(principal, path_user_id=user_id)
    return await _handle_chat_upload(
        file=file,
        app_id=app_id,
        user_id=user_id,
        chat_id=chatId,
        intent=intent,
        bundle_path=bundle_path,
    )


@router.get("/api/chats/{app_id}/{workflow_name}")
async def list_chats(
    app_id: str,
    workflow_name: str,
    principal: UserPrincipal = Depends(require_user_scope),
):
    validate_path_id(app_id, "app_id")
    validate_path_id(workflow_name, "workflow_name")
    try:
        coll = await runtime_app._chat_coll()
        query: dict[str, Any] = {"workflow_name": workflow_name, **build_app_scope_filter(app_id)}
        if principal.user_id != "anonymous":
            query["user_id"] = principal.user_id
        docs = await coll.find(query).sort("created_at", -1).to_list(length=20)
        return {"chat_ids": [doc.get("_id") for doc in docs]}
    except Exception as exc:
        logger.error("Failed to list chats for app %s workflow %s: %s", app_id, workflow_name, exc)
        raise HTTPException(status_code=500, detail="Failed to list chats") from exc


@router.get("/api/chats/exists/{app_id}/{workflow_name}/{chat_id}")
async def chat_exists(
    app_id: str,
    workflow_name: str,
    chat_id: str,
    principal: UserPrincipal = Depends(require_user_scope),
):
    validate_path_id(app_id, "app_id")
    validate_path_id(workflow_name, "workflow_name")
    validate_path_id(chat_id, "chat_id")
    try:
        coll = await runtime_app._chat_coll()
        query: dict[str, Any] = {"_id": chat_id, "workflow_name": workflow_name, **build_app_scope_filter(app_id)}
        if principal.user_id != "anonymous":
            query["user_id"] = principal.user_id
        doc = await coll.find_one(query, {"_id": 1, "transport_purpose": 1})
        if doc and _is_ask_carrier_session(doc):
            return {"exists": False}
        return {"exists": doc is not None}
    except Exception as exc:
        raise HTTPException(status_code=500, detail="Failed to check chat existence") from exc


@router.get("/api/chats/meta/{app_id}/{workflow_name}/{chat_id}")
async def chat_meta(
    app_id: str,
    workflow_name: str,
    chat_id: str,
    principal: UserPrincipal = Depends(require_user_scope),
):
    validate_path_id(app_id, "app_id")
    validate_path_id(workflow_name, "workflow_name")
    validate_path_id(chat_id, "chat_id")
    try:
        from mozaiksai.core.data.persistence.persistence_manager import extract_last_artifact

        has_children = False

        coll = await runtime_app._chat_coll()
        projection = {"cache_seed": 1, "workflow_ui_state.last_artifact": 1, "status": 1, "_id": 1, "workflow_name": 1}
        query: dict[str, Any] = {"_id": chat_id, "workflow_name": workflow_name, **build_app_scope_filter(app_id)}
        if principal.user_id != "anonymous":
            query["user_id"] = principal.user_id
        doc = await coll.find_one(query, projection)
        if not doc:
            return {"exists": False}

        run_history = await runtime_app.persistence_manager.load_run_history(
            chat_id=chat_id,
            app_id=app_id,
        )
        run_history_count = len(run_history)

        artifact_instance_id = None
        artifact_state = None
        try:
            from mozaiksai.core.workflow import session_manager

            workflow_session = await session_manager.get_workflow_session(chat_id, app_id)
            if workflow_session and workflow_session.get("artifact_instance_id"):
                artifact_instance_id = workflow_session["artifact_instance_id"]
                artifact_doc = await session_manager.get_artifact_instance(artifact_instance_id, app_id)
                if artifact_doc:
                    artifact_state = artifact_doc.get("state")
        except Exception as artifact_err:
            logger.warning("[CHAT_META] Failed to retrieve artifact instance for chat %s: %s", chat_id, artifact_err)

        return {
            "exists": True,
            "chat_id": chat_id,
            "workflow_name": workflow_name,
            "has_children": has_children,
            "cache_seed": doc.get("cache_seed"),
            "status": doc.get("status"),
            "run_history_count": run_history_count,
            "last_artifact": extract_last_artifact(doc),
            "artifact_instance_id": artifact_instance_id,
            "artifact_state": artifact_state,
            "app_id": app_id,
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail="Failed to load chat meta") from exc


__all__ = ["router"]
