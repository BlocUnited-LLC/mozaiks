"""Chat session management routes (start, list, exists, meta, stubs)."""
from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, UTC
from typing import Any, Dict, Optional
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Request

from mozaiksai.runtime.auth import UserPrincipal, require_user_scope
from mozaiksai.runtime.auth.dependencies import (
    validate_path_app_id,
    validate_user_id_against_principal as _validate_user_id_against_principal,
)
from mozaiksai.runtime.multitenant import build_app_scope_filter
from mozaiksai.runtime.observability.performance_manager import get_performance_manager
from mozaiksai.runtime.extensions.platform_hooks import get_platform_hooks
from logs.logging_config import get_workflow_logger

logger = logging.getLogger(__name__)
wf_logger = get_workflow_logger("chat_routes")

router = APIRouter(tags=["chats"])


# ---------------------------------------------------------------------------
# Start chat
# ---------------------------------------------------------------------------

@router.post("/api/chats/{app_id}/{workflow_name}/start")
async def start_chat(
    app_id: str,
    workflow_name: str,
    request: Request,
    principal: UserPrincipal = Depends(require_user_scope),
):
    """Start a new chat session for a workflow.

    Idempotency / duplicate suppression strategy:
      - If an in-progress chat for (app_id, user_id, workflow_name) was created
        within the last N seconds (default 15) AND client did not set force_new=true,
        we *reuse* that chat_id instead of creating a new one.
      - Optional client-supplied ``client_request_id`` can further collapse rapid
        replays (e.g. browser double-submit).
    """
    validate_path_app_id(principal, app_id)

    persistence_manager = request.app.state.persistence_manager

    IDEMPOTENCY_WINDOW_SEC = int(os.getenv("CHAT_START_IDEMPOTENCY_SEC", "15"))
    now = datetime.now(UTC)
    reuse_cutoff = now - timedelta(seconds=IDEMPOTENCY_WINDOW_SEC)

    try:
        data = await request.json()
        body_user_id = data.get("user_id")
        client_request_id = data.get("client_request_id")
        force_new = str(data.get("force_new", "false")).lower() in ("1", "true", "yes")

        user_id = _validate_user_id_against_principal(principal, body_user_id=body_user_id)

        # Platform hook: prerequisite gate check
        ok, prereq_error = await get_platform_hooks().call_chat_prereqs(
            app_id=app_id,
            user_id=user_id,
            workflow_name=workflow_name,
            persistence=persistence_manager,
        )
        if not ok:
            raise HTTPException(status_code=409, detail=prereq_error)

        coll = await persistence_manager._coll()

        # Reuse recent in-progress session if present
        reused_doc = None
        if not force_new:
            base_query = {
                "user_id": user_id,
                "workflow_name": workflow_name,
                "status": 0,
                "created_at": {"$gte": reuse_cutoff},
                **build_app_scope_filter(app_id),
            }
            if client_request_id:
                reused_doc = await coll.find_one(
                    {**base_query, "client_request_id": client_request_id},
                    projection={"chat_id": 1, "created_at": 1},
                )
            if not reused_doc:
                reused_doc = await coll.find_one(
                    base_query, projection={"chat_id": 1, "created_at": 1}
                )

        if reused_doc:
            chat_id = reused_doc["chat_id"]
            wf_logger.info(
                "CHAT_SESSION_REUSED: Existing recent chat reused",
                app_id=app_id,
                workflow_name=workflow_name,
                user_id=user_id,
                chat_id=chat_id,
                reuse_window_sec=IDEMPOTENCY_WINDOW_SEC,
            )
            try:
                cache_seed = await persistence_manager.get_or_assign_cache_seed(chat_id, app_id)
            except Exception as se:
                cache_seed = None
                logger.debug(f"cache_seed assignment failed (reused chat {chat_id}): {se}")

            return {
                "success": True,
                "chat_id": chat_id,
                "workflow_name": workflow_name,
                "app_id": app_id,
                "user_id": user_id,
                "websocket_url": f"/ws/{workflow_name}/{app_id}/{chat_id}/{user_id}",
                "message": "Existing recent chat reused.",
                "reused": True,
                "cache_seed": cache_seed,
            }

        # Generate new chat
        chat_id = str(uuid4())

        try:
            extra_fields: Dict[str, Any] = {}
            if client_request_id:
                extra_fields["client_request_id"] = client_request_id

            try:
                platform_fields = await get_platform_hooks().call_chat_session_fields(
                    app_id=app_id,
                    user_id=user_id,
                    workflow_name=workflow_name,
                    chat_id=chat_id,
                )
                if platform_fields:
                    extra_fields.update(platform_fields)
            except Exception as _pf_err:
                logger.debug(f"platform session fields skipped: {_pf_err}")

            await persistence_manager.create_chat_session(
                chat_id=chat_id,
                app_id=app_id,
                workflow_name=workflow_name,
                user_id=user_id,
                extra_fields=extra_fields or None,
            )
        except Exception as ce:
            logger.debug(f"chat_session pre-create skipped {chat_id}: {ce}")

        try:
            perf_mgr = await get_performance_manager()
            await perf_mgr.record_workflow_start(chat_id, app_id, workflow_name, user_id)
        except Exception as perf_e:
            logger.debug(f"perf_start skipped {chat_id}: {perf_e}")

        wf_logger.info(
            "CHAT_SESSION_STARTED: New chat session initiated",
            app_id=app_id,
            workflow_name=workflow_name,
            user_id=user_id,
            chat_id=chat_id,
            idempotency_window_sec=IDEMPOTENCY_WINDOW_SEC,
        )

        try:
            cache_seed = await persistence_manager.get_or_assign_cache_seed(chat_id, app_id)
        except Exception as se:
            cache_seed = None
            logger.debug(f"cache_seed assignment failed (new chat {chat_id}): {se}")

        return {
            "success": True,
            "chat_id": chat_id,
            "workflow_name": workflow_name,
            "app_id": app_id,
            "user_id": user_id,
            "websocket_url": f"/ws/{workflow_name}/{app_id}/{chat_id}/{user_id}",
            "message": "Chat session initialized; connect to websocket to start.",
            "reused": False,
            "cache_seed": cache_seed,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to start chat session: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to start chat: {e}")


# ---------------------------------------------------------------------------
# List / exists / meta
# ---------------------------------------------------------------------------

@router.get("/api/chats/{app_id}/{workflow_name}")
async def list_chats(
    request: Request,
    app_id: str,
    workflow_name: str,
    principal: UserPrincipal = Depends(require_user_scope),
):
    """List recent chat IDs for a given app and workflow."""
    try:
        persistence_manager = request.app.state.persistence_manager
        coll = await persistence_manager._coll()
        query: Dict[str, Any] = {
            "workflow_name": workflow_name,
            **build_app_scope_filter(app_id),
        }
        if principal.user_id != "anonymous":
            query["user_id"] = principal.user_id
        cursor = coll.find(query).sort("created_at", -1)
        docs = await cursor.to_list(length=20)
        chat_ids = [doc.get("_id") for doc in docs]
        return {"chat_ids": chat_ids}
    except Exception as e:
        logger.error(f"Failed to list chats for app {app_id}, workflow {workflow_name}: {e}")
        raise HTTPException(status_code=500, detail="Failed to list chats")


@router.get("/api/chats/exists/{app_id}/{workflow_name}/{chat_id}")
async def chat_exists(
    request: Request,
    app_id: str,
    workflow_name: str,
    chat_id: str,
    principal: UserPrincipal = Depends(require_user_scope),
):
    """Lightweight existence check for a chat session."""
    try:
        persistence_manager = request.app.state.persistence_manager
        coll = await persistence_manager._coll()
        query: Dict[str, Any] = {
            "_id": chat_id,
            "workflow_name": workflow_name,
            **build_app_scope_filter(app_id),
        }
        if principal.user_id != "anonymous":
            query["user_id"] = principal.user_id
        doc = await coll.find_one(query, {"_id": 1})
        return {"exists": doc is not None}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to check chat existence: {e}")


@router.get("/api/chats/meta/{app_id}/{workflow_name}/{chat_id}")
async def chat_meta(
    request: Request,
    app_id: str,
    workflow_name: str,
    chat_id: str,
    principal: UserPrincipal = Depends(require_user_scope),
):
    """Return lightweight chat metadata (cache_seed, last_artifact, artifact_instance).

    Allows a second user/browser to restore artifact UI state even if local storage
    is empty. Does not return full transcript.
    """
    try:
        persistence_manager = request.app.state.persistence_manager

        has_children = False
        try:
            from mozaiksai.kernel.pack.config import workflow_has_journeys
            has_children = workflow_has_journeys(workflow_name)
        except Exception:
            has_children = False

        coll = await persistence_manager._coll()
        projection = {
            "cache_seed": 1,
            "last_artifact": 1,
            "status": 1,
            "last_sequence": 1,
            "_id": 1,
            "workflow_name": 1,
        }
        query: Dict[str, Any] = {
            "_id": chat_id,
            "workflow_name": workflow_name,
            **build_app_scope_filter(app_id),
        }
        if principal.user_id != "anonymous":
            query["user_id"] = principal.user_id
        doc = await coll.find_one(query, projection)
        if not doc:
            return {"exists": False}

        # Fetch artifact instance from WorkflowSessions (multi-workflow navigation)
        artifact_instance_id = None
        artifact_state = None
        try:
            from mozaiksai.runtime.sessions import session_manager

            workflow_session = await session_manager.get_workflow_session(chat_id, app_id)
            if workflow_session and workflow_session.get("artifact_instance_id"):
                artifact_instance_id = workflow_session["artifact_instance_id"]
                artifact_doc = await session_manager.get_artifact_instance(
                    artifact_instance_id, app_id
                )
                if artifact_doc:
                    artifact_state = artifact_doc.get("state")
                    wf_logger.debug(
                        f"[CHAT_META] Retrieved artifact instance {artifact_instance_id} "
                        f"for chat {chat_id}"
                    )
        except Exception as artifact_err:
            wf_logger.warning(
                f"[CHAT_META] Failed to retrieve artifact instance for chat {chat_id}: "
                f"{artifact_err}"
            )

        return {
            "exists": True,
            "chat_id": chat_id,
            "workflow_name": workflow_name,
            "has_children": has_children,
            "cache_seed": doc.get("cache_seed"),
            "status": doc.get("status"),
            "last_sequence": doc.get("last_sequence"),
            "last_artifact": doc.get("last_artifact"),
            "artifact_instance_id": artifact_instance_id,
            "artifact_state": artifact_state,
            "app_id": app_id,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to load chat meta: {e}")


# ---------------------------------------------------------------------------
# General-chat stubs (platform feature; graceful degradation for OSS frontend)
# ---------------------------------------------------------------------------

@router.get("/api/general_chats/list/{app_id}/{user_id}")
async def list_general_chats_stub(
    app_id: str,
    user_id: str,
    limit: int = 50,
):
    """Stub: Ask-mode general chat sessions are a platform feature. Returns empty list."""
    return {"sessions": []}


@router.get("/api/general_chats/transcript/{app_id}/{general_chat_id}")
async def general_chat_transcript_stub(
    app_id: str,
    general_chat_id: str,
):
    """Stub: Ask-mode transcripts are a platform feature."""
    raise HTTPException(
        status_code=404,
        detail="General chat not available in open-source runtime",
    )
