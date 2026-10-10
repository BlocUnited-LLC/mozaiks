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

from fastapi import APIRouter, Depends, HTTPException, Request

from logs.logging_config import get_workflow_logger
from mozaiksai.core.auth import UserPrincipal, require_user_scope
from mozaiksai.core.auth.dependencies import validate_path_id
from mozaiksai.core.runtime.composition.platform_hooks import (
    ModuleScopeResolutionError,
    get_platform_hooks,
)

router = APIRouter(tags=["notifications"])
logger = get_workflow_logger("platform_app")

# Fields excluded from notification list responses.
# source_event may contain provider IDs (e.g. payment_provider_payment_intent_id).
_NOTIFICATION_SAFE_PROJECTION: dict[str, int] = {
    "_id": 0,
    "source_event": 0,
    "tenant_id": 0,
    "workspace_id": 0,
    "actor": 0,
    "audience": 0,
}


_SUPPORT_NOTIFICATION_MATCHES: tuple[dict[str, Any], ...] = (
    {"module_id": "workspace_support"},
    {"rule_id": {"$regex": r"^workspace_support\."}},
    {"event_type": {"$regex": r"^domain\.workspace_support\."}},
    {"context.related_type": "workspace_support.request"},
    {"source_event.payload.related_type": "workspace_support.request"},
    {"source_event.related_type": "workspace_support.request"},
)


async def _verified_notification_scope(
    request: Request, principal: UserPrincipal, app_id: str,
) -> tuple[str | None, str | None]:
    """Read the host's membership assertion, never request-selected workspace metadata."""
    if not principal.is_authenticated or not principal.workspace_id:
        return None, None
    try:
        scope = await get_platform_hooks().call_module_scope(
            principal=principal,
            module_name="workspace_support",
            action_name="list_support_requests",
            requested_scope={
                "app_id": app_id,
                "user_id": principal.user_id,
                "tenant_id": None,
                "workspace_id": principal.workspace_id,
            },
            params={},
            request=request,
            default_permissions=list(principal.scopes),
            fail_closed=True,
        )
    except ModuleScopeResolutionError:
        logger.warning("NOTIFICATION_WORKSPACE_VERIFICATION_FAILED")
        return None, None
    verified_workspace_id = scope.get("_verified_workspace_id")
    if verified_workspace_id != principal.workspace_id:
        return None, None
    return scope.get("_verified_tenant_id"), verified_workspace_id


def _notification_workspace_filter(workspace_id: str | None) -> dict[str, Any]:
    # Historical support records have no owner. They cannot be safely assigned
    # to a workspace from their audience or app ID.
    app_wide = {"$nor": list(_SUPPORT_NOTIFICATION_MATCHES)}
    if not workspace_id:
        return app_wide
    return {"$or": [app_wide, {"$expr": {"$eq": ["$workspace_id", workspace_id]}}]}


def _notification_broad_audience_scope_filter(
    tenant_id: str | None, workspace_id: str | None,
) -> dict[str, Any]:
    # A token role or permission alone is not authority over another tenant's
    # alert. Older broad-audience alerts without an owner stay hidden.
    visible: list[dict[str, Any]] = [
        {"$and": [
            {"$or": [{"audience.roles": {"$exists": False}}, {"audience.roles": []}]},
            {"$or": [{"audience.permissions": {"$exists": False}}, {"audience.permissions": []}]},
        ]},
    ]
    if not workspace_id:
        return {"$or": visible}

    workspace_owner = {
        "$or": [
            {"workspace_id": {"$exists": False}},
            {"$expr": {"$eq": ["$workspace_id", workspace_id]}},
        ]
    }
    tenant_owner: dict[str, Any] = {"tenant_id": {"$exists": False}}
    if tenant_id:
        tenant_owner = {
            "$or": [
                tenant_owner,
                {"$expr": {"$eq": ["$tenant_id", tenant_id]}},
            ]
        }
    visible.append({
        "$and": [
            {"$or": [{"workspace_id": {"$exists": True}}, {"tenant_id": {"$exists": True}}]},
            workspace_owner,
            tenant_owner,
        ]
    })
    return {"$or": visible}


def _notification_scope_filters(
    tenant_id: str | None, workspace_id: str | None,
) -> list[dict[str, Any]]:
    return [
        _notification_workspace_filter(workspace_id),
        _notification_broad_audience_scope_filter(tenant_id, workspace_id),
    ]


def _notification_query_for_principal(
    principal: UserPrincipal, app_id: str,
    tenant_id: str | None, workspace_id: str | None,
) -> dict[str, Any]:
    query: dict[str, Any] = {"app_id": app_id, "status": "unread"}
    query["$or"] = _notification_visibility_filter(principal)
    query["$and"] = _notification_scope_filters(tenant_id, workspace_id)
    return query


def _notification_app_id(
    request: Request, principal: UserPrincipal, requested_app_id: str | None = None,
) -> str:
    loaded_app_id = getattr(request.app.state, "loaded_app_id", None)
    if not isinstance(loaded_app_id, str) or not loaded_app_id.strip():
        raise HTTPException(status_code=503, detail="Loaded app identity unavailable")
    if (principal.app_id and principal.app_id != loaded_app_id) or (
        requested_app_id and requested_app_id != loaded_app_id
    ):
        raise HTTPException(status_code=403, detail="App ID does not match the loaded app")
    return loaded_app_id


def _notification_visibility_filter(principal: UserPrincipal) -> list[dict[str, Any]]:
    """Return the $or visibility filter for platform_notifications queries."""
    visibility: list[dict[str, Any]] = [
        {"audience.user_ids": principal.user_id},
    ]
    # Token-wide roles and permissions do not prove a grant in this record's
    # workspace. Broad audiences need an exact-membership grant before reads.
    visibility.append(
        {
            "$and": [
                {"$or": [{"audience.user_ids": {"$exists": False}}, {"audience.user_ids": []}]},
                {"$or": [{"audience.roles": {"$exists": False}}, {"audience.roles": []}]},
                {"$or": [{"audience.permissions": {"$exists": False}}, {"audience.permissions": []}]},
            ]
        }
    )
    return visibility


@router.get("/api/notifications/count")
async def notifications_count_fallback(
    request: Request,
    principal: UserPrincipal = Depends(require_user_scope),
):
    app_id = _notification_app_id(request, principal)
    try:
        from mozaiksai.core.core_config import get_mongo_client

        tenant_id, workspace_id = await _verified_notification_scope(request, principal, app_id)
        collection = get_mongo_client()["mozaiks"]["platform_notifications"]
        unread_count = await collection.count_documents(
            _notification_query_for_principal(principal, app_id, tenant_id, workspace_id)
        )
        return {"count": int(unread_count), "unread_count": int(unread_count)}
    except Exception as exc:
        logger.debug("NOTIFICATION_COUNT_SKIPPED: %s", exc)
        return {"count": 0, "unread_count": 0}


@router.get("/api/notifications")
async def list_notifications(
    request: Request,
    status: str = "all",
    limit: int = 50,
    app_id: str | None = None,
    principal: UserPrincipal = Depends(require_user_scope),
):
    """
    List platform notifications visible to the authenticated principal.

    Returns notifications from the platform_notifications collection filtered by
    direct or app-wide audience and the loaded host app ID.

    Safe fields only — source_event (which may contain provider IDs) is excluded.

    Query params:
        status: "all" | "unread" | "read"  (default: "all")
        limit:  1–200  (default: 50)
        app_id: optional assertion that must match the loaded host app ID
    """
    bounded_limit = max(1, min(int(limit), 200))
    query: dict[str, Any] = {}
    if status in ("unread", "read"):
        query["status"] = status

    host_app_id = _notification_app_id(request, principal, app_id)
    query["app_id"] = host_app_id

    query["$or"] = _notification_visibility_filter(principal)

    try:
        from mozaiksai.core.core_config import get_mongo_client

        tenant_id, workspace_id = await _verified_notification_scope(request, principal, host_app_id)
        query["$and"] = _notification_scope_filters(tenant_id, workspace_id)
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
    request: Request,
    principal: UserPrincipal = Depends(require_user_scope),
):
    """Mark a single notification as read. Only updates records visible to the principal."""
    validate_path_id(notification_id, "notification_id")
    app_id = _notification_app_id(request, principal)
    try:
        from mozaiksai.core.core_config import get_mongo_client

        tenant_id, workspace_id = await _verified_notification_scope(request, principal, app_id)
        collection = get_mongo_client()["mozaiks"]["platform_notifications"]
        match_query: dict[str, Any] = {
            "notification_id": notification_id,
            "app_id": app_id,
            "$or": _notification_visibility_filter(principal),
            "$and": _notification_scope_filters(tenant_id, workspace_id),
        }
        result = await collection.update_one(match_query, {"$set": {"status": "read"}})
        return {"success": result.modified_count > 0, "notification_id": notification_id}
    except Exception as exc:
        logger.debug("NOTIFICATION_MARK_READ_SKIPPED: %s", exc)
        return {"success": False, "notification_id": notification_id}


@router.post("/api/notifications/mark-all-read")
async def mark_all_notifications_read(
    request: Request,
    app_id: str | None = None,
    principal: UserPrincipal = Depends(require_user_scope),
):
    """Mark all visible unread notifications as read for the authenticated principal."""
    host_app_id = _notification_app_id(request, principal, app_id)
    try:
        from mozaiksai.core.core_config import get_mongo_client

        tenant_id, workspace_id = await _verified_notification_scope(request, principal, host_app_id)
        collection = get_mongo_client()["mozaiks"]["platform_notifications"]
        query: dict[str, Any] = {"app_id": host_app_id, "status": "unread"}
        query["$or"] = _notification_visibility_filter(principal)
        query["$and"] = _notification_scope_filters(tenant_id, workspace_id)
        result = await collection.update_many(query, {"$set": {"status": "read"}})
        return {"success": True, "marked_count": result.modified_count}
    except Exception as exc:
        logger.debug("NOTIFICATION_MARK_ALL_READ_SKIPPED: %s", exc)
        return {"success": False, "marked_count": 0}


@router.delete("/api/notifications")
async def clear_all_notifications(
    request: Request,
    app_id: str | None = None,
    principal: UserPrincipal = Depends(require_user_scope),
):
    """Hard-delete all notifications visible to the authenticated principal."""
    host_app_id = _notification_app_id(request, principal, app_id)
    try:
        from mozaiksai.core.core_config import get_mongo_client

        tenant_id, workspace_id = await _verified_notification_scope(request, principal, host_app_id)
        collection = get_mongo_client()["mozaiks"]["platform_notifications"]
        query: dict[str, Any] = {"app_id": host_app_id}
        query["$or"] = _notification_visibility_filter(principal)
        query["$and"] = _notification_scope_filters(tenant_id, workspace_id)
        result = await collection.delete_many(query)
        return {"success": True, "cleared_count": result.deleted_count}
    except Exception as exc:
        logger.debug("NOTIFICATION_CLEAR_SKIPPED: %s", exc)
        return {"success": False, "cleared_count": 0}
