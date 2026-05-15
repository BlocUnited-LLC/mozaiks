from __future__ import annotations

"""Mozaiks hosted product host layered on top of mozaiksai.hosts.studio.

This is the hosted Mozaiks product composition. It currently reuses the local
Studio management and create surface and is the place for product-only
additions such as collaboration, marketplace, billing, and hosted workspace
behavior.
"""

from typing import Optional

from fastapi import Depends

from mozaiksai.hosts.bootstrap import configure_repo_host_defaults

configure_repo_host_defaults("mozaiks")

from mozaiksai.hosts import studio as studio_app
from logs.logging_config import get_workflow_logger
from mozaiksai.core.auth import UserPrincipal, require_user_scope
from mozaiksai.core.session.router import get_session_router


app = studio_app.app
logger = get_workflow_logger("mozaiks_app")


@app.get("/api/sessions/active")
async def get_active_session(
    app_id: Optional[str] = None,
    principal: UserPrincipal = Depends(require_user_scope),
):
    """Return the current active chat session for the calling user.

    Used by the app console to resume an in-progress workflow session instead
    of always launching a new one. Returns ``active: true`` with ``chat_id``
    and ``workflow_id`` when a resumable session exists; ``active: false``
    otherwise.
    """
    resolved_app_id, user_id = studio_app._resolve_studio_scope(principal, app_id=app_id)
    try:
        snapshot = await get_session_router().get_session_snapshot(
            app_id=resolved_app_id,
            user_id=user_id,
        )
    except ValueError:
        return {"active": False, "chat_id": None, "workflow_id": None, "lifecycle_state": None}
    except Exception as exc:
        logger.warning("get_active_session: session snapshot failed: %s", exc)
        return {"active": False, "chat_id": None, "workflow_id": None, "lifecycle_state": None}

    chat_id = snapshot.get("current_chat_id")
    workflow_id = snapshot.get("current_workflow_id")
    lifecycle_state = str(snapshot.get("lifecycle_state") or "")

    resumable = {"active", "awaiting_decision", "awaiting_transition"}
    if chat_id and lifecycle_state in resumable:
        return {
            "active": True,
            "chat_id": chat_id,
            "workflow_id": workflow_id,
            "lifecycle_state": lifecycle_state,
        }
    return {"active": False, "chat_id": None, "workflow_id": None, "lifecycle_state": lifecycle_state}


__all__ = ["app"]
