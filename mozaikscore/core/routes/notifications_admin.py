# ==============================================================================
# FILE: mozaikscore/core/routes/notifications_admin.py
# DESCRIPTION: Admin notification routes — /__mozaiks/admin/notifications
#              Broadcast, scheduling, channels, templates.
#              Operations that require unbuilt services (broadcast_service,
#              digest_scheduler, template_renderer) return 501 stubs until
#              those services are implemented.
# ORIGIN: Migrated from mozaiks-core-public/backend/core/routes/notifications_admin.py
# ==============================================================================
import logging
from typing import Optional, List
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from mozaikscore.core.auth import require_admin_or_internal
from mozaikscore.core.notifications_manager import notifications_manager

logger = logging.getLogger("mozaikscore.routes.notifications_admin")

router = APIRouter(
    prefix="/__mozaiks/admin/notifications",
    tags=["admin-notifications"],
    dependencies=[Depends(require_admin_or_internal)],
)


# ---------------------------------------------------------------------------
# DTOs
# ---------------------------------------------------------------------------

class BroadcastTarget(BaseModel):
    type: str = Field(..., description="all, subscription, module, query, user_ids")
    tier: Optional[str] = None
    module_name: Optional[str] = None
    filter: Optional[dict] = None
    ids: Optional[List[str]] = None


class BroadcastRequest(BaseModel):
    notification_type: str = Field(default="admin_broadcast")
    title: str
    message: str
    target: BroadcastTarget
    channels: Optional[List[str]] = Field(default=["in_app"])
    metadata: Optional[dict] = None


class ScheduledNotificationRequest(BaseModel):
    user_id: str
    notification_type: str
    title: str
    message: str
    scheduled_for: datetime
    metadata: Optional[dict] = None


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.post("/broadcast")
async def send_broadcast(request: BroadcastRequest):
    """Broadcast notification to multiple users.

    Target types: all, subscription, module, query, user_ids.
    """
    # Simple in-app broadcast for user_ids target via notifications_manager
    if request.target.type == "user_ids" and request.target.ids:
        results = []
        for uid in request.target.ids:
            await notifications_manager.create_notification(
                user_id=uid,
                notification_type=request.notification_type,
                title=request.title,
                message=request.message,
                metadata=request.metadata,
            )
            results.append(uid)
        return {"success": True, "sent_to": len(results), "target_type": "user_ids"}

    # Broadcast to all users requires iterating the users collection
    if request.target.type == "all":
        from mozaikscore.core.database import get_users_collection

        users_coll = get_users_collection()
        cursor = users_coll.find({}, {"user_id": 1, "username": 1})
        count = 0
        async for user_doc in cursor:
            uid = user_doc.get("user_id") or str(user_doc["_id"])
            await notifications_manager.create_notification(
                user_id=uid,
                notification_type=request.notification_type,
                title=request.title,
                message=request.message,
                metadata=request.metadata,
            )
            count += 1
        return {"success": True, "sent_to": count, "target_type": "all"}

    # Advanced targeting (subscription tier, module access, custom query) — not yet implemented
    raise HTTPException(
        status_code=501,
        detail=f"Broadcast target type '{request.target.type}' not yet implemented. "
        "Supported: 'all', 'user_ids'.",
    )


@router.get("/broadcasts")
async def get_broadcast_history(
    limit: int = Query(50, ge=1, le=200),
    skip: int = Query(0, ge=0),
):
    """Get broadcast history. (Requires broadcast_service — stub.)"""
    raise HTTPException(status_code=501, detail="Broadcast history not yet implemented")


@router.get("/broadcasts/{broadcast_id}")
async def get_broadcast_details(broadcast_id: str):
    """Get details of a specific broadcast. (Requires broadcast_service — stub.)"""
    raise HTTPException(status_code=501, detail="Broadcast details not yet implemented")


@router.post("/schedule")
async def schedule_notification(request: ScheduledNotificationRequest):
    """Schedule a notification for future delivery. (Requires digest_scheduler — stub.)"""
    raise HTTPException(status_code=501, detail="Notification scheduling not yet implemented")


@router.delete("/schedule/{notification_id}")
async def cancel_scheduled(notification_id: str):
    """Cancel a scheduled notification. (Requires digest_scheduler — stub.)"""
    raise HTTPException(status_code=501, detail="Notification scheduling not yet implemented")


@router.get("/channels")
async def get_channels():
    """Get notification channels and their status."""
    return {
        "channels": [
            {"name": "in_app", "enabled": True, "description": "In-app notifications"},
            {"name": "email", "enabled": False, "description": "Email notifications (not configured)"},
            {"name": "web_push", "enabled": False, "description": "Web push (not configured)"},
        ]
    }


@router.get("/templates")
async def get_templates():
    """Get notification templates. (Requires template_renderer — stub.)"""
    return {"templates": [], "digest_templates": []}


@router.post("/templates/reload")
async def reload_templates():
    """Reload notification templates. (Requires template_renderer — stub.)"""
    return {"success": True, "templates_count": 0}


@router.get("/schema")
async def get_admin_schema():
    """Schema for admin notification operations."""
    return {
        "broadcast": {
            "endpoint": "POST /__mozaiks/admin/notifications/broadcast",
            "description": "Send notification to multiple users",
            "target_types": {
                "all": "All users",
                "user_ids": "Specific users (requires 'ids' field)",
                "subscription": "Users with subscription tier (not yet implemented)",
                "module": "Users with module access (not yet implemented)",
                "query": "Custom MongoDB filter (not yet implemented)",
            },
            "channels": ["in_app"],
        },
        "schedule": {
            "endpoint": "POST /__mozaiks/admin/notifications/schedule",
            "description": "Schedule notification for future delivery (not yet implemented)",
        },
        "templates": {
            "endpoint": "GET /__mozaiks/admin/notifications/templates",
            "reload_endpoint": "POST /__mozaiks/admin/notifications/templates/reload",
        },
    }
