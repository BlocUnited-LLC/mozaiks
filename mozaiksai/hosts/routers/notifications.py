"""Notifications router — platform_notifications collection CRUD.

Routes:
    GET    /api/notifications/count
    GET    /api/notifications
    POST   /api/notifications/{notification_id}/read
    POST   /api/notifications/mark-all-read
    DELETE /api/notifications
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from logs.logging_config import get_workflow_logger
from mozaiksai.core.auth import UserPrincipal, require_user_scope
from mozaiksai.core.auth.dependencies import validate_path_id

router = APIRouter(tags=["notifications"])
logger = get_workflow_logger("platform_app")

# Fields excluded from notification list responses.
# source_event may contain provider IDs (e.g. payment_provider_payment_intent_id).
_NOTIFICATION_SAFE_PROJECTION: dict[str, int] = {
    "_id": 0,
    "source_event": 0,
    "tenant_id": 0,
    "actor": 0,
    "audience": 0,
}


def _notification_query_for_principal(principal: UserPrincipal) -> dict[str, Any]:
    query: dict[str, Any] = {"status": "unread"}
    if principal.app_id:
        query["app_id"] = principal.app_id
    query["$or"] = _notification_visibility_filter(principal)
    return query


def _notification_visibility_filter(principal: UserPrincipal) -> list[dict[str, Any]]:
    """Return the $or visibility filter for platform_notifications queries."""
    visibility: list[dict[str, Any]] = [
        {"actor.id": principal.user_id},
        {"audience.user_ids": principal.user_id},
    ]
    roles = [role for role in principal.roles if role]
    if roles:
        visibility.append({"audience.roles": {"$in": roles}})
    visibility.append({"audience.roles": {"$exists": False}})
    return visibility


@router.get("/api/notifications/count")
async def notifications_count_fallback(
    principal: UserPrincipal = Depends(require_user_scope),
):
    try:
        from mozaiksai.core.core_config import get_mongo_client

        collection = get_mongo_client()["mozaiks"]["platform_notifications"]
        unread_count = await collection.count_documents(_notification_query_for_principal(principal))
        return {"count": int(unread_count), "unread_count": int(unread_count)}
    except Exception as exc:
        logger.debug("NOTIFICATION_COUNT_SKIPPED: %s", exc)
        return {"count": 0, "unread_count": 0}


@router.get("/api/notifications")
async def list_notifications(
    status: str = "all",
    limit: int = 50,
    app_id: str | None = None,
    principal: UserPrincipal = Depends(require_user_scope),
):
    """
    List platform notifications visible to the authenticated principal.

    Returns notifications from the platform_notifications collection filtered by
    audience roles and principal app_id scope.

    Safe fields only — source_event (which may contain provider IDs) is excluded.

    Query params:
        status: "all" | "unread" | "read"  (default: "all")
        limit:  1–200  (default: 50)
        app_id: explicit app scope override for Studio use
    """
    bounded_limit = max(1, min(int(limit), 200))
    query: dict[str, Any] = {}
    if status in ("unread", "read"):
        query["status"] = status

    effective_app_id = app_id or (principal.app_id if principal.app_id else None)
    if effective_app_id:
        query["app_id"] = effective_app_id

    query["$or"] = _notification_visibility_filter(principal)

    try:
        from mozaiksai.core.core_config import get_mongo_client

        collection = get_mongo_client()["mozaiks"]["platform_notifications"]
        cursor = (
            collection.find(query, _NOTIFICATION_SAFE_PROJECTION)
            .sort("created_at", -1)
            .limit(bounded_limit)
        )
        notifications = await cursor.to_list(length=bounded_limit)
        unread_count = sum(1 for n in notifications if n.get("status") == "unread")
        return {
            "notifications": notifications,
            "count": len(notifications),
            "unread_count": unread_count,
        }
    except Exception as exc:
        logger.debug("NOTIFICATION_LIST_SKIPPED: %s", exc)
        return {"notifications": [], "count": 0, "unread_count": 0}


@router.post("/api/notifications/{notification_id}/read")
async def mark_notification_read(
    notification_id: str,
    principal: UserPrincipal = Depends(require_user_scope),
):
    """Mark a single notification as read. Only updates records visible to the principal."""
    validate_path_id(notification_id, "notification_id")
    try:
        from mozaiksai.core.core_config import get_mongo_client

        collection = get_mongo_client()["mozaiks"]["platform_notifications"]
        match_query: dict[str, Any] = {
            "notification_id": notification_id,
            "$or": _notification_visibility_filter(principal),
        }
        result = await collection.update_one(match_query, {"$set": {"status": "read"}})
        return {"success": result.modified_count > 0, "notification_id": notification_id}
    except Exception as exc:
        logger.debug("NOTIFICATION_MARK_READ_SKIPPED: %s", exc)
        return {"success": False, "notification_id": notification_id}


@router.post("/api/notifications/mark-all-read")
async def mark_all_notifications_read(
    app_id: str | None = None,
    principal: UserPrincipal = Depends(require_user_scope),
):
    """Mark all visible unread notifications as read for the authenticated principal."""
    try:
        from mozaiksai.core.core_config import get_mongo_client

        collection = get_mongo_client()["mozaiks"]["platform_notifications"]
        query: dict[str, Any] = {"status": "unread"}
        effective_app_id = app_id or (principal.app_id if principal.app_id else None)
        if effective_app_id:
            query["app_id"] = effective_app_id
        query["$or"] = _notification_visibility_filter(principal)
        result = await collection.update_many(query, {"$set": {"status": "read"}})
        return {"success": True, "marked_count": result.modified_count}
    except Exception as exc:
        logger.debug("NOTIFICATION_MARK_ALL_READ_SKIPPED: %s", exc)
        return {"success": False, "marked_count": 0}


@router.delete("/api/notifications")
async def clear_all_notifications(
    app_id: str | None = None,
    principal: UserPrincipal = Depends(require_user_scope),
):
    """Hard-delete all notifications visible to the authenticated principal."""
    try:
        from mozaiksai.core.core_config import get_mongo_client

        collection = get_mongo_client()["mozaiks"]["platform_notifications"]
        query: dict[str, Any] = {}
        effective_app_id = app_id or (principal.app_id if principal.app_id else None)
        if effective_app_id:
            query["app_id"] = effective_app_id
        query["$or"] = _notification_visibility_filter(principal)
        result = await collection.delete_many(query)
        return {"success": True, "cleared_count": result.deleted_count}
    except Exception as exc:
        logger.debug("NOTIFICATION_CLEAR_SKIPPED: %s", exc)
        return {"success": False, "cleared_count": 0}
