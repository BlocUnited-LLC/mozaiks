"""File upload routes for chat attachments."""
from __future__ import annotations

import logging
import os
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile

from mozaiksai.runtime.auth import UserPrincipal, require_user_scope
from mozaiksai.runtime.auth.dependencies import (
    validate_user_id_against_principal as _validate_user_id_against_principal,
)
from mozaiksai.runtime.multitenant import build_app_scope_filter
from mozaiksai.runtime.artifacts.attachments import handle_chat_upload

logger = logging.getLogger(__name__)

router = APIRouter(tags=["uploads"])


@router.post("/api/chat/upload")
async def upload_chat_file(
    request: Request,
    file: UploadFile = File(...),
    appId: Optional[str] = Form(None),
    userId: str = Form(...),
    chatId: str = Form(...),
    intent: str = Form("context"),
    bundle_path: Optional[str] = Form(None),
    principal: UserPrincipal = Depends(require_user_scope),
):
    """Upload a file associated with a specific chat session.

    The uploaded file is stored on disk and a metadata record is appended to the
    ChatSessions document under the ``attachments`` array.

    Clients may set ``intent`` to ``context`` (default) or ``bundle``/``deliverable``
    to include the file in AgentGenerator's generated download bundle.
    """
    resolved_app_id = (appId or "").strip()
    if not resolved_app_id:
        raise HTTPException(status_code=400, detail="appId is required")

    user_id = _validate_user_id_against_principal(principal, body_user_id=userId)

    persistence_manager = request.app.state.persistence_manager
    simple_transport = request.app.state.simple_transport

    return await _handle_chat_upload(
        persistence_manager=persistence_manager,
        simple_transport=simple_transport,
        file=file,
        app_id=resolved_app_id,
        user_id=user_id,
        chat_id=chatId,
        intent=intent,
        bundle_path=bundle_path,
    )


@router.post("/api/chat/upload/{app_id}/{user_id}")
async def upload_chat_file_scoped(
    request: Request,
    app_id: str,
    user_id: str,
    file: UploadFile = File(...),
    chatId: str = Form(...),
    intent: str = Form("context"),
    bundle_path: Optional[str] = Form(None),
    principal: UserPrincipal = Depends(require_user_scope),
):
    """Back-compat upload endpoint used by older ChatUI adapters."""
    user_id = _validate_user_id_against_principal(principal, path_user_id=user_id)

    persistence_manager = request.app.state.persistence_manager
    simple_transport = request.app.state.simple_transport

    return await _handle_chat_upload(
        persistence_manager=persistence_manager,
        simple_transport=simple_transport,
        file=file,
        app_id=app_id,
        user_id=user_id,
        chat_id=chatId,
        intent=intent,
        bundle_path=bundle_path,
    )


# ---------------------------------------------------------------------------
# Internal helper
# ---------------------------------------------------------------------------

async def _handle_chat_upload(
    *,
    persistence_manager,
    simple_transport,
    file: UploadFile,
    app_id: str,
    user_id: str,
    chat_id: str,
    intent: str,
    bundle_path: Optional[str],
) -> Dict[str, Any]:
    if not app_id or not user_id or not chat_id:
        raise HTTPException(status_code=400, detail="app_id, user_id, and chat_id are required")

    allowed_raw = os.getenv("CHAT_ATTACHMENTS_ALLOWED_WORKFLOWS", "").strip()
    try:
        coll = await persistence_manager._coll()
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
    except LookupError:
        raise HTTPException(status_code=404, detail="Chat session not found")
    except ValueError as ve:
        msg = str(ve)
        if msg.startswith("File too large"):
            raise HTTPException(status_code=413, detail=msg)
        raise HTTPException(status_code=400, detail=msg)
    except Exception as exc:
        logger.exception("UPLOAD_FAILED")
        raise HTTPException(status_code=500, detail="Upload failed") from exc

    # Emit a websocket event so the UI can render an attachment indicator
    try:
        if simple_transport:
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

            await simple_transport.send_event_to_ui(
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
    except Exception as e:
        logger.debug(f"attachment_uploaded WS emit failed for chat {chat_id}: {e}")

    return {
        "success": True,
        "chat_id": chat_id,
        "app_id": app_id,
        "user_id": user_id,
        "attachment": res.attachment,
    }
