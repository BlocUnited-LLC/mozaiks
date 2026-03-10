# ==============================================================================
# FILE: mozaikscore/core/routes/notifications.py
# DESCRIPTION: User-facing notification routes — /api/notifications
#              Get, read/unread, mark-all, delete, count, config, preferences.
# ORIGIN: Migrated from mozaiks-core-public/backend/core/routes/notifications.py
# ==============================================================================
import os
import logging
import time

from fastapi import APIRouter, Depends, HTTPException, Request

from mozaikscore.core.notifications_manager import notifications_manager
from mozaikscore.core.event_bus import event_bus
from mozaikscore.core.auth import get_current_user

logger = logging.getLogger("mozaikscore.routes.notifications")

router = APIRouter(prefix="/api/notifications", tags=["notifications"])

MONETIZATION = os.getenv("MONETIZATION", "0") == "1"


@router.get("")
async def get_notifications(
    unread_only: bool = False,
    limit: int = 20,
    offset: int = 0,
    user: dict = Depends(get_current_user),
):
    """Get notifications for the current user."""
    try:
        notifications = await notifications_manager.get_user_notifications(
            user_id=user["user_id"],
            unread_only=unread_only,
            limit=limit,
            offset=offset,
        )
        unread_notifications = await notifications_manager.get_user_notifications(
            user_id=user["user_id"],
            unread_only=True,
            limit=100,
            offset=0,
        )
        return {
            "notifications": notifications,
            "count": len(notifications),
            "unread_count": len(unread_notifications),
            "unread_only": unread_only,
            "timestamp": int(time.time()),
        }
    except Exception as exc:
        logger.error("Error fetching notifications: %s", exc)
        raise HTTPException(status_code=500, detail=f"Error fetching notifications: {exc}")


@router.get("/config")
async def get_notifications_config(user: dict = Depends(get_current_user)):
    """Get notification configuration and user preferences."""
    try:
        config = await notifications_manager.get_notification_config(
            user_id=user["user_id"],
            monetization_enabled=MONETIZATION,
        )
        preferences = await notifications_manager.get_user_notification_preferences(user["user_id"])
        return {"config": config, "preferences": preferences}
    except Exception as exc:
        logger.error("Error fetching notification config: %s", exc)
        raise HTTPException(status_code=500, detail=f"Error fetching notification config: {exc}")


@router.post("/preferences")
async def update_notification_preferences(request: Request, user: dict = Depends(get_current_user)):
    """Update notification preferences for the current user."""
    try:
        preferences = await request.json()
        updated = await notifications_manager.update_notification_preferences(
            user_id=user["user_id"],
            preferences=preferences,
        )
        event_bus.publish("notification_preferences_updated", {"user_id": user["user_id"]})
        return {"message": "Notification preferences updated successfully", "preferences": updated}
    except Exception as exc:
        logger.error("Error updating notification preferences: %s", exc)
        raise HTTPException(status_code=500, detail=f"Error updating notification preferences: {exc}")


@router.post("/{notification_id}/read")
async def mark_notification_read(notification_id: str, user: dict = Depends(get_current_user)):
    """Mark a notification as read."""
    try:
        success = await notifications_manager.mark_notification_read(
            user_id=user["user_id"],
            notification_id=notification_id,
        )
        if not success:
            raise HTTPException(status_code=404, detail="Notification not found")
        return {"message": "Notification marked as read"}
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Error marking notification as read: %s", exc)
        raise HTTPException(status_code=500, detail=f"Error marking notification as read: {exc}")


@router.post("/{notification_id}/unread")
async def mark_notification_unread(notification_id: str, user: dict = Depends(get_current_user)):
    """Mark a notification as unread."""
    try:
        success = await notifications_manager.mark_notification_read(
            user_id=user["user_id"],
            notification_id=notification_id,
            read=False,
        )
        if not success:
            raise HTTPException(status_code=404, detail="Notification not found")
        return {"message": "Notification marked as unread"}
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Error marking notification as unread: %s", exc)
        raise HTTPException(status_code=500, detail=f"Error marking notification as unread: {exc}")


@router.post("/mark-all-read")
async def mark_all_notifications_read(user: dict = Depends(get_current_user)):
    """Mark all notifications as read."""
    try:
        await notifications_manager.mark_all_notifications_read(user["user_id"])
        event_bus.publish("all_notifications_read", {"user_id": user["user_id"]})
        return {"message": "All notifications marked as read"}
    except Exception as exc:
        logger.error("Error marking all notifications as read: %s", exc)
        raise HTTPException(status_code=500, detail=f"Error marking all notifications as read: {exc}")


@router.delete("/{notification_id}")
async def delete_notification(notification_id: str, user: dict = Depends(get_current_user)):
    """Delete a notification."""
    try:
        success = await notifications_manager.delete_notification(
            user_id=user["user_id"],
            notification_id=notification_id,
        )
        if not success:
            raise HTTPException(status_code=404, detail="Notification not found")
        return {"message": "Notification deleted"}
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Error deleting notification: %s", exc)
        raise HTTPException(status_code=500, detail=f"Error deleting notification: {exc}")


@router.get("/count")
async def get_unread_notification_count(user: dict = Depends(get_current_user)):
    """Get count of unread notifications."""
    try:
        unread = await notifications_manager.get_user_notifications(
            user_id=user["user_id"],
            unread_only=True,
            limit=100,
            offset=0,
        )
        return {"unread_count": len(unread)}
    except Exception as exc:
        logger.error("Error fetching unread count: %s", exc)
        raise HTTPException(status_code=500, detail=f"Error fetching unread count: {exc}")
