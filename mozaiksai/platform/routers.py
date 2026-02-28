from __future__ import annotations

"""Mozaiks Platform API Routes
================================
All routes in this module are Mozaiks-platform-specific features that are NOT
part of the open-source AG2 runtime core.  They are mounted into the FastAPI
app during ``on_startup`` by ``mozaiksai.platform.extensions``.

Routes provided:
    GET  /api/themes/{app_id}
    POST /api/realtime/oauth/completed
    GET  /api/apps/{app_id}/builds/{build_id}/export
    GET  /api/general_chats/list/{app_id}/{user_id}
    GET  /api/general_chats/transcript/{app_id}/{general_chat_id}
    GET  /api/workflows/{app_id}/available

State contract (set by shared_app.py before on_startup is called):
    app.state.persistence_manager   — AG2PersistenceManager instance
    app.state.simple_transport      — SimpleTransport instance (may be None)
    app.state.platform_theme_manager— ThemeManager instance (set by on_startup)
"""

import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, AliasChoices

from mozaiksai.core.auth import (
    UserPrincipal,
    ServicePrincipal,
    require_user_scope,
    require_internal,
    validate_user_id_against_principal,
)
from mozaiksai.core.multitenant import build_app_scope_filter, coalesce_app_id
from logs.logging_config import get_workflow_logger

logger = get_workflow_logger("mozaiks_platform.routers")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _iso(dt: Optional[datetime]) -> Optional[str]:
    if isinstance(dt, datetime):
        try:
            return dt.isoformat()
        except Exception:
            return str(dt)
    return dt  # type: ignore[return-value]


def _get_persistence(request: Request) -> Any:
    pm = getattr(request.app.state, "persistence_manager", None)
    if pm is None:
        raise HTTPException(status_code=503, detail="Persistence service unavailable")
    return pm


def _get_transport(request: Request) -> Any:
    return getattr(request.app.state, "simple_transport", None)


async def _chat_coll(request: Request) -> Any:
    pm = _get_persistence(request)
    return await pm._coll()


# ---------------------------------------------------------------------------
# OAuth Webhook model
# ---------------------------------------------------------------------------

class OAuthCompletedWebhookPayload(BaseModel):
    """Payload sent by MozaiksCore when OAuth completes for a platform integration."""

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    chat_session_id: str = Field(..., alias="chatSessionId", min_length=1)
    correlation_id: Optional[str] = Field(None, alias="correlationId")
    app_id: str = Field(
        ...,
        validation_alias=AliasChoices("appId", "app_id"),
        serialization_alias="appId",
        min_length=1,
    )
    user_id: str = Field(..., alias="userId", min_length=1)
    platform: str = Field(..., min_length=1)
    success: bool
    account_id: Optional[str] = Field(None, alias="accountId")
    account_name: Optional[str] = Field(None, alias="accountName")
    error: Optional[str] = None
    timestamp_utc: Optional[datetime] = Field(None, alias="timestampUtc")


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------

def get_platform_router() -> APIRouter:  # noqa: C901
    router = APIRouter()

    # -----------------------------------------------------------------------
    # Themes
    # -----------------------------------------------------------------------

    @router.get("/api/themes/{app_id}")
    async def get_app_theme(
        app_id: str,
        request: Request,
        principal: UserPrincipal = Depends(require_user_scope),
    ):
        theme_mgr = getattr(request.app.state, "platform_theme_manager", None)
        if theme_mgr is None:
            raise HTTPException(status_code=503, detail="Theme service unavailable")
        try:
            return await theme_mgr.get_theme(app_id)
        except Exception as exc:
            logger.exception("THEME_FETCH_FAILED")
            raise HTTPException(status_code=500, detail="Failed to load theme") from exc

    # -----------------------------------------------------------------------
    # OAuth completion webhook (internal — called by MozaiksCore)
    # -----------------------------------------------------------------------

    @router.post("/api/realtime/oauth/completed")
    async def oauth_completed_webhook(
        payload: OAuthCompletedWebhookPayload,
        request: Request,
        service: ServicePrincipal = Depends(require_internal),
    ):
        """Forward OAuth completion events to the live WebSocket chat session."""
        chat_id = payload.chat_session_id
        logger.info(
            "OAUTH_COMPLETED_WEBHOOK_RECEIVED",
            chat_id=chat_id,
            app_id=payload.app_id,
            user_id=payload.user_id,
            platform=payload.platform,
            success=payload.success,
            correlation_id=payload.correlation_id,
        )

        transport = _get_transport(request)
        if not transport:
            return JSONResponse(status_code=202, content={"accepted": True, "delivered": False})

        conn = transport.connections.get(chat_id)
        connected = bool(conn and conn.get("websocket"))

        delivered = False
        if connected:
            conn_app_id = conn.get("app_id")
            conn_user_id = conn.get("user_id")
            if (conn_app_id and str(conn_app_id) != str(payload.app_id)) or (
                conn_user_id and str(conn_user_id) != str(payload.user_id)
            ):
                logger.warning(
                    "OAUTH_COMPLETED_WEBHOOK_TENANT_MISMATCH",
                    chat_id=chat_id,
                    payload_app_id=payload.app_id,
                    connected_app_id=conn_app_id,
                )
            else:
                delivered = True

        event = {
            "kind": "oauth_completed",
            "chat_id": chat_id,
            "app_id": payload.app_id,
            "user_id": payload.user_id,
            "platform": payload.platform,
            "success": payload.success,
            "account_id": payload.account_id,
            "account_name": payload.account_name,
            "error": payload.error,
            "correlation_id": payload.correlation_id,
        }
        await transport.send_event_to_ui(event, chat_id)

        status_code = 200 if delivered else 202
        return JSONResponse(
            status_code=status_code,
            content={"accepted": True, "delivered": delivered},
        )

    # -----------------------------------------------------------------------
    # Build export download (internal — called by MozaiksCore)
    # -----------------------------------------------------------------------

    @router.get("/api/apps/{app_id}/builds/{build_id}/export")
    async def download_build_export(
        app_id: str,
        build_id: str,
        request: Request,
        service: ServicePrincipal = Depends(require_internal),
    ):
        """Download the build export bundle (zip) for an app build."""
        from fastapi.responses import FileResponse

        resolved_app_id = coalesce_app_id(app_id=app_id)
        if not resolved_app_id:
            raise HTTPException(status_code=400, detail="app_id is required")
        resolved_build_id = str(build_id or "").strip()
        if not resolved_build_id:
            raise HTTPException(status_code=400, detail="build_id is required")

        base_dir = Path(os.getenv("RUNTIME_GENERATED_APPS_DIR", "generated_apps")).resolve()
        build_dir = (base_dir / str(resolved_app_id) / resolved_build_id).resolve()
        if not str(build_dir).startswith(str(base_dir)):
            raise HTTPException(status_code=403, detail="Access denied")

        zip_path: Optional[Path] = None
        zip_name: Optional[str] = None

        # Prefer the persisted UI artifact payload for the exact bundle path/name.
        try:
            coll = await _chat_coll(request)
            doc = await coll.find_one(
                {"_id": resolved_build_id, **build_app_scope_filter(str(resolved_app_id))},
                {"last_artifact": 1},
            )
            last_artifact = doc.get("last_artifact") if isinstance(doc, dict) else None
            payload_data = last_artifact.get("payload") if isinstance(last_artifact, dict) else None
            if isinstance(payload_data, dict):
                files = payload_data.get("files") or payload_data.get("ui_files")
                if isinstance(files, list):
                    for f in files:
                        if not isinstance(f, dict):
                            continue
                        raw_path = f.get("path")
                        if not isinstance(raw_path, str) or not raw_path.strip():
                            continue
                        candidate = Path(raw_path).resolve()
                        if candidate.suffix.lower() != ".zip":
                            continue
                        if str(candidate).startswith(str(base_dir)):
                            zip_path = candidate
                            raw_name = f.get("name")
                            zip_name = (
                                str(raw_name).strip()
                                if isinstance(raw_name, str) and raw_name.strip()
                                else candidate.name
                            )
                            break
        except Exception as lookup_err:
            logger.debug(f"build export last_artifact lookup failed: {lookup_err}")

        # Fallback: scan the build directory.
        if zip_path is None:
            if not build_dir.exists() or not build_dir.is_dir():
                raise HTTPException(status_code=404, detail="Build export not found")
            try:
                candidates = sorted(
                    build_dir.glob("*.zip"),
                    key=lambda p: p.stat().st_mtime,
                    reverse=True,
                )
            except Exception:
                candidates = []
            if candidates:
                zip_path = candidates[0].resolve()
                zip_name = zip_path.name

        if zip_path is None or not zip_path.exists() or not zip_path.is_file():
            raise HTTPException(status_code=404, detail="Build export not found")

        return FileResponse(
            path=str(zip_path),
            filename=zip_name or zip_path.name,
            media_type="application/zip",
            headers={
                "Content-Disposition": f'attachment; filename="{zip_name or zip_path.name}"',
                "X-Content-Type-Options": "nosniff",
                "Cache-Control": "no-store",
            },
        )

    # -----------------------------------------------------------------------
    # General chats (Ask Mode — Mozaiks dual-mode UX)
    # -----------------------------------------------------------------------

    @router.get("/api/general_chats/list/{app_id}/{user_id}")
    async def list_general_chats(
        app_id: str,
        user_id: str,
        request: Request,
        limit: int = 50,
        principal: UserPrincipal = Depends(require_user_scope),
    ):
        """Return general (non-AG2) chat sessions for a user."""
        user_id = validate_user_id_against_principal(principal, path_user_id=user_id)
        pm = _get_persistence(request)
        try:
            sanitized_limit = max(1, min(int(limit or 1), 200))
            sessions = await pm.list_general_chats(
                app_id=app_id,
                user_id=user_id,
                limit=sanitized_limit,
            )
            normalized: List[Dict[str, Any]] = [
                {
                    "chat_id": sess.get("chat_id"),
                    "label": sess.get("label"),
                    "sequence": sess.get("sequence"),
                    "status": sess.get("status"),
                    "created_at": _iso(sess.get("created_at")),
                    "last_updated_at": _iso(sess.get("last_updated_at")),
                    "last_sequence": sess.get("last_sequence"),
                }
                for sess in sessions
            ]
            return {"sessions": normalized, "count": len(normalized)}
        except Exception as e:
            logger.error(f"[LIST_GENERAL_CHATS] Failed: {e}")
            raise HTTPException(status_code=500, detail=f"Failed to list general chats: {e}")

    @router.get("/api/general_chats/transcript/{app_id}/{general_chat_id}")
    async def general_chat_transcript(
        app_id: str,
        general_chat_id: str,
        request: Request,
        after_sequence: int = -1,
        limit: int = 200,
        principal: UserPrincipal = Depends(require_user_scope),
    ):
        """Return a general (non-AG2) chat transcript slice for the UI."""
        pm = _get_persistence(request)
        try:
            transcript = await pm.fetch_general_chat_transcript(
                general_chat_id=general_chat_id,
                app_id=app_id,
                after_sequence=after_sequence,
                limit=limit,
            )
            if not transcript:
                raise HTTPException(status_code=404, detail="General chat not found")

            if principal.user_id != "anonymous":
                owner = transcript.get("user_id")
                if owner and str(owner).strip() != str(principal.user_id).strip():
                    raise HTTPException(status_code=404, detail="General chat not found")

            def _serialize_message(msg: Dict[str, Any]) -> Dict[str, Any]:
                m = dict(msg)
                ts = m.get("timestamp")
                if isinstance(ts, datetime):
                    m["timestamp"] = ts.isoformat()
                return m

            return {
                "chat_id": transcript.get("chat_id"),
                "label": transcript.get("label"),
                "sequence": transcript.get("sequence"),
                "status": transcript.get("status"),
                "app_id": transcript.get("app_id") or app_id,
                "user_id": transcript.get("user_id"),
                "created_at": _iso(transcript.get("created_at")),
                "last_updated_at": _iso(transcript.get("last_updated_at")),
                "last_sequence": transcript.get("last_sequence"),
                "messages": [_serialize_message(m) for m in transcript.get("messages", [])],
            }
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"[GENERAL_CHAT_TRANSCRIPT] Failed: {e}")
            raise HTTPException(status_code=500, detail=f"Failed to load general chat transcript: {e}")

    # -----------------------------------------------------------------------
    # Available workflows (pack-gated availability)
    # -----------------------------------------------------------------------

    @router.get("/api/workflows/{app_id}/available")
    async def get_available_workflows(
        app_id: str,
        request: Request,
        user_id: Optional[str] = None,
        principal: UserPrincipal = Depends(require_user_scope),
    ):
        """Workflows with availability status based on pack prerequisite gates."""
        pm = _get_persistence(request)
        try:
            from mozaiksai.core.workflow.pack.gating import list_workflow_availability

            if principal.user_id == "anonymous":
                resolved_user_id = str(user_id or "").strip()
                if not resolved_user_id:
                    raise HTTPException(status_code=400, detail="user_id is required")
            else:
                resolved_user_id = principal.user_id
                if user_id and str(user_id).strip() != str(resolved_user_id).strip():
                    raise HTTPException(status_code=403, detail="user_id mismatch")

            workflows = await list_workflow_availability(
                app_id=app_id,
                user_id=resolved_user_id,
                persistence=pm,
            )
            logger.info(
                "AVAILABLE_WORKFLOWS_REQUESTED",
                app_id=app_id,
                user_id=resolved_user_id,
                workflow_count=len(workflows),
            )
            return {"workflows": workflows}
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Failed to get available workflows for app {app_id}: {e}")
            raise HTTPException(status_code=500, detail=f"Failed to retrieve available workflows: {e}")

    return router
