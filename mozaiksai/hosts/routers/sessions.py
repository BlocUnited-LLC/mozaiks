"""Sessions and general-chats router.

Routes:
    GET    /api/sessions/list/{app_id}/{user_id}
    DELETE /api/sessions/{app_id}/{user_id}
    DELETE /api/general_chats/{app_id}/{user_id}
    DELETE /api/general_chats/{app_id}/{user_id}/{general_chat_id}
    GET    /api/general_chats/list/{app_id}/{user_id}
    GET    /api/general_chats/transcript/{app_id}/{general_chat_id}
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from logs.logging_config import get_workflow_logger
from mozaiksai.core.auth import UserPrincipal, require_user_scope
from mozaiksai.core.auth.dependencies import validate_path_id, validate_user_id_against_principal
from mozaiksai.core.multitenant import build_app_scope_filter
from mozaiksai.core.runtime.composition.platform_hooks import get_platform_hooks
from mozaiksai.hosts import runtime as runtime_app

router = APIRouter(tags=["sessions"])
logger = get_workflow_logger("sessions_router")

_NON_RUNNABLE_WORKFLOW_IDS: frozenset[str] = frozenset({"extended_orchestration"})

persistence_manager = runtime_app.persistence_manager


# ---------------------------------------------------------------------------
# Helpers (local to sessions/general_chats routes)
# ---------------------------------------------------------------------------

def _get_ordered_workflow_names() -> list[str]:
    from mozaiksai.core.workflow.workflow_manager import workflow_manager

    return get_platform_hooks().call_workflow_ordering(sorted(workflow_manager.get_all_workflow_names()))


def _is_runnable_workflow_name(workflow_name: str | None, ordered_names: list[str] | None = None) -> bool:
    name = str(workflow_name or "").strip()
    if not name:
        return False
    if name in _NON_RUNNABLE_WORKFLOW_IDS:
        return False
    names = ordered_names if ordered_names is not None else _get_ordered_workflow_names()
    return any(name.lower() == loaded.lower() for loaded in names)


def _is_ask_carrier_session(session: dict[str, Any] | None) -> bool:
    if not isinstance(session, dict):
        return False
    return str(session.get("transport_purpose") or "").strip().lower() == "ask_carrier"


def _json_timestamp(value: Any) -> Any:
    return value.isoformat() if hasattr(value, "isoformat") else value


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.get("/api/sessions/list/{app_id}/{user_id}")
async def list_user_sessions(
    app_id: str,
    user_id: str,
    principal: UserPrincipal = Depends(require_user_scope),
):
    validate_path_id(app_id, "app_id")
    user_id = validate_user_id_against_principal(principal, path_user_id=user_id)
    try:
        from mozaiksai.core.data.models import WorkflowStatus
        from mozaiksai.core.data.persistence.persistence_manager import extract_last_artifact

        coll = await runtime_app._chat_coll()
        sessions = await coll.find({
            "user_id": user_id,
            "status": int(WorkflowStatus.IN_PROGRESS),
            **build_app_scope_filter(app_id),
        }).sort("last_updated_at", -1).to_list(length=100)
        runnable_names = _get_ordered_workflow_names()
        sessions = [
            session
            for session in sessions
            if _is_runnable_workflow_name(session.get("workflow_name"), runnable_names)
            and not _is_ask_carrier_session(session)
        ]

        result = []
        for session in sessions:
            result.append({
                "chat_id": session["_id"],
                "workflow_name": session.get("workflow_name"),
                "created_at": session.get("created_at").isoformat() if session.get("created_at") else None,
                "last_updated_at": session.get("last_updated_at").isoformat() if session.get("last_updated_at") else None,
                "last_artifact": extract_last_artifact(session),
            })
        return {"sessions": result, "count": len(result)}
    except Exception as exc:
        logger.error("[LIST_SESSIONS] Failed to list sessions: %s", exc)
        raise HTTPException(status_code=500, detail="Failed to list sessions") from exc


@router.delete("/api/sessions/{app_id}/{user_id}")
async def delete_user_sessions(
    app_id: str,
    user_id: str,
    status: str = "in_progress",
    workflow_name: str | None = None,
    principal: UserPrincipal = Depends(require_user_scope),
):
    validate_path_id(app_id, "app_id")
    user_id = validate_user_id_against_principal(principal, path_user_id=user_id)
    try:
        from mozaiksai.core.data.models import WorkflowStatus

        normalized_status = str(status or "in_progress").strip().lower()
        query: dict[str, Any] = {"user_id": user_id, **build_app_scope_filter(app_id)}
        if workflow_name:
            query["workflow_name"] = str(workflow_name).strip()
        if normalized_status in {"in_progress", "active", "open"}:
            query["status"] = int(WorkflowStatus.IN_PROGRESS)
        elif normalized_status in {"completed", "done", "closed"}:
            query["status"] = int(WorkflowStatus.COMPLETED)
        elif normalized_status in {"all", "any", "*"}:
            pass
        else:
            raise HTTPException(status_code=400, detail="status must be one of: in_progress, completed, all")

        result = await (await runtime_app._chat_coll()).delete_many(query)
        return {
            "success": True,
            "app_id": app_id,
            "user_id": user_id,
            "status": normalized_status,
            "workflow_name": workflow_name,
            "deleted_count": int(result.deleted_count or 0),
        }
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail="Failed to delete sessions") from exc


@router.delete("/api/general_chats/{app_id}/{user_id}")
async def delete_general_chats(
    app_id: str,
    user_id: str,
    status: str = "all",
    principal: UserPrincipal = Depends(require_user_scope),
):
    validate_path_id(app_id, "app_id")
    user_id = validate_user_id_against_principal(principal, path_user_id=user_id)
    try:
        from mozaiksai.core.data.models import WorkflowStatus

        normalized_status = str(status or "all").strip().lower()
        query: dict[str, Any] = {"user_id": user_id, **build_app_scope_filter(app_id)}
        if normalized_status in {"in_progress", "active", "open"}:
            query["status"] = int(WorkflowStatus.IN_PROGRESS)
        elif normalized_status in {"completed", "done", "closed"}:
            query["status"] = int(WorkflowStatus.COMPLETED)
        elif normalized_status in {"all", "any", "*"}:
            pass
        else:
            raise HTTPException(status_code=400, detail="status must be one of: in_progress, completed, all")

        general_coll = await persistence_manager._general_coll()
        result = await general_coll.delete_many(query)
        deleted_count = int(result.deleted_count or 0)

        if normalized_status in {"all", "any", "*"}:
            try:
                counter_coll = await persistence_manager._general_counter_coll()
                await counter_coll.delete_many({"user_id": user_id, **build_app_scope_filter(app_id)})
            except Exception as counter_err:
                logger.debug("[DELETE_GENERAL_CHATS] Counter reset skipped: %s", counter_err)

        return {
            "success": True,
            "app_id": app_id,
            "user_id": user_id,
            "status": normalized_status,
            "deleted_count": deleted_count,
        }
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail="Failed to delete general chats") from exc


@router.delete("/api/general_chats/{app_id}/{user_id}/{general_chat_id}")
async def delete_general_chat(
    app_id: str,
    user_id: str,
    general_chat_id: str,
    principal: UserPrincipal = Depends(require_user_scope),
):
    validate_path_id(app_id, "app_id")
    user_id = validate_user_id_against_principal(principal, path_user_id=user_id)
    validate_path_id(general_chat_id, "general_chat_id")
    try:
        deleted = await persistence_manager.delete_general_chat(
            general_chat_id=general_chat_id,
            app_id=app_id,
            user_id=user_id,
        )
        return {
            "success": True,
            "deleted": bool(deleted),
            "app_id": app_id,
            "user_id": user_id,
            "general_chat_id": general_chat_id,
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail="Failed to delete general chat") from exc


@router.get("/api/general_chats/list/{app_id}/{user_id}")
async def list_general_chats_fallback(
    app_id: str,
    user_id: str,
    limit: int = 50,
    principal: UserPrincipal = Depends(require_user_scope),
):
    validate_path_id(app_id, "app_id")
    user_id = validate_user_id_against_principal(principal, path_user_id=user_id)
    bounded_limit = max(1, min(int(limit or 50), 200))
    try:
        sessions = await persistence_manager.list_general_chats(
            app_id=app_id,
            user_id=user_id,
            limit=bounded_limit,
        )
        normalized_sessions = []
        for session in sessions:
            item = dict(session)
            item["created_at"] = _json_timestamp(item.get("created_at"))
            item["last_updated_at"] = _json_timestamp(item.get("last_updated_at"))
            normalized_sessions.append(item)
        return {
            "app_id": app_id,
            "user_id": user_id,
            "limit": bounded_limit,
            "sessions": normalized_sessions,
            "count": len(normalized_sessions),
            "source": "persistence",
        }
    except Exception as exc:
        logger.debug("[GENERAL_CHATS_LIST] persistence fallback failed: %s", exc)
    return {
        "app_id": app_id,
        "user_id": user_id,
        "limit": bounded_limit,
        "sessions": [],
        "count": 0,
        "source": "fallback",
    }


@router.get("/api/general_chats/transcript/{app_id}/{general_chat_id}")
async def general_chat_transcript_fallback(
    app_id: str,
    general_chat_id: str,
    after_sequence: int = -1,
    limit: int = 200,
    principal: UserPrincipal = Depends(require_user_scope),
):
    validate_path_id(app_id, "app_id")
    validate_path_id(general_chat_id, "general_chat_id")
    bounded_limit = max(1, min(int(limit or 200), 2000))
    try:
        transcript = await persistence_manager.fetch_general_chat_transcript(
            general_chat_id=general_chat_id,
            app_id=app_id,
            after_sequence=int(after_sequence or -1),
            limit=bounded_limit,
        )
        if transcript:
            owner = str(transcript.get("user_id") or "")
            if principal.user_id != "anonymous" and owner and owner != principal.user_id:
                raise HTTPException(status_code=403, detail="Forbidden")
            payload = dict(transcript)
            payload["created_at"] = _json_timestamp(payload.get("created_at"))
            payload["last_updated_at"] = _json_timestamp(payload.get("last_updated_at"))
            messages = []
            for message in payload.get("messages") or []:
                item = dict(message)
                item["timestamp"] = _json_timestamp(item.get("timestamp"))
                messages.append(item)
            payload["messages"] = messages
            payload["found"] = True
            payload["source"] = "persistence"
            return payload
    except HTTPException:
        raise
    except Exception as exc:
        logger.debug("[GENERAL_CHAT_TRANSCRIPT] persistence fallback failed: %s", exc)
    return {
        "app_id": app_id,
        "chat_id": general_chat_id,
        "label": general_chat_id,
        "messages": [],
        "last_sequence": max(-1, int(after_sequence or -1)),
        "limit": bounded_limit,
        "found": False,
        "source": "fallback",
    }


__all__ = ["router"]
