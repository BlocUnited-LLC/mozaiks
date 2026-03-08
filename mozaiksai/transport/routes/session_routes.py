"""Session listing routes (active/paused workflow sessions per user)."""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request

from mozaiksai.runtime.auth import UserPrincipal, require_user_scope
from mozaiksai.runtime.auth.dependencies import (
    validate_user_id_against_principal as _validate_user_id_against_principal,
)
from mozaiksai.runtime.multitenant import build_app_scope_filter
from logs.logging_config import get_workflow_logger

wf_logger = get_workflow_logger("session_routes")

router = APIRouter(tags=["sessions"])


@router.get("/api/sessions/list/{app_id}/{user_id}")
async def list_user_sessions(
    request: Request,
    app_id: str,
    user_id: str,
    principal: UserPrincipal = Depends(require_user_scope),
):
    """List all active/paused workflow sessions for a user.

    Used by frontend to render session tabs. Returns sessions across all
    workflows so UI can show which ones are IN_PROGRESS.
    """
    user_id = _validate_user_id_against_principal(principal, path_user_id=user_id)

    try:
        from mozaiksai.runtime.data.models import WorkflowStatus

        persistence_manager = request.app.state.persistence_manager
        coll = await persistence_manager._coll()

        sessions = (
            await coll.find(
                {
                    "user_id": user_id,
                    "status": int(WorkflowStatus.IN_PROGRESS),
                    **build_app_scope_filter(app_id),
                }
            )
            .sort("last_updated_at", -1)
            .to_list(length=100)
        )

        result = []
        for session in sessions:
            result.append(
                {
                    "chat_id": session["_id"],
                    "workflow_name": session.get("workflow_name"),
                    "created_at": (
                        session.get("created_at").isoformat()
                        if session.get("created_at")
                        else None
                    ),
                    "last_updated_at": (
                        session.get("last_updated_at").isoformat()
                        if session.get("last_updated_at")
                        else None
                    ),
                    "last_artifact": session.get("last_artifact"),
                }
            )

        wf_logger.debug(
            f"[LIST_SESSIONS] Found {len(result)} IN_PROGRESS sessions for user {user_id}"
        )

        return {"sessions": result, "count": len(result)}
    except Exception as e:
        wf_logger.error(f"[LIST_SESSIONS] Failed to list sessions: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to list sessions: {e}")


@router.get("/api/sessions/recent/{app_id}/{user_id}")
async def get_most_recent_workflow_session(
    request: Request,
    app_id: str,
    user_id: str,
    principal: UserPrincipal = Depends(require_user_scope),
):
    """Return the most recently updated IN_PROGRESS workflow session for a user.

    Used when toggling from general mode back to workflow mode.
    """
    user_id = _validate_user_id_against_principal(principal, path_user_id=user_id)

    try:
        from mozaiksai.runtime.data.models import WorkflowStatus

        persistence_manager = request.app.state.persistence_manager
        coll = await persistence_manager._coll()

        sessions = (
            await coll.find(
                {
                    "user_id": user_id,
                    "status": int(WorkflowStatus.IN_PROGRESS),
                    **build_app_scope_filter(app_id),
                }
            )
            .sort("last_updated_at", -1)
            .to_list(length=100)
        )

        if not sessions:
            wf_logger.debug(
                f"[RECENT_SESSION] No IN_PROGRESS workflows for user {user_id}"
            )
            return {"found": False, "chat_id": None, "workflow_name": None}

        recent = sessions[0]
        wf_logger.debug(
            f"[RECENT_SESSION] Returning most recent workflow "
            f"{recent.get('workflow_name')} chat_id={recent['_id']} for user {user_id}"
        )

        return {
            "found": True,
            "chat_id": recent["_id"],
            "workflow_name": recent.get("workflow_name"),
            "created_at": (
                recent.get("created_at").isoformat()
                if recent.get("created_at")
                else None
            ),
            "last_updated_at": (
                recent.get("last_updated_at").isoformat()
                if recent.get("last_updated_at")
                else None
            ),
            "last_artifact": recent.get("last_artifact"),
        }
    except Exception as e:
        wf_logger.error(f"[RECENT_SESSION] Failed to fetch most recent session: {e}")
        raise HTTPException(
            status_code=500, detail=f"Failed to fetch most recent session: {e}"
        )
