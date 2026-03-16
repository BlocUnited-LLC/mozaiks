# ==============================================================================
# FILE: mozaikscore/core/routes/settings.py
# DESCRIPTION: User settings and notification preference routes.
# ==============================================================================
import os
import logging

from fastapi import APIRouter, Depends, HTTPException, Request

from mozaikscore.core.auth import get_current_user
from mozaikscore.core.event_bus import event_bus
from mozaikscore.core.schemas import NotificationPreferencesRequest
from mozaikscore.core.settings_manager import settings_manager
from mozaikscore.core.subscription_manager import subscription_manager

logger = logging.getLogger("mozaikscore.routes.settings")

MONETIZATION = os.getenv("MONETIZATION", "0") == "1"

router = APIRouter(tags=["settings"])


@router.get("/api/settings-config")
async def get_settings_config_route(user: dict = Depends(get_current_user)):
    try:
        settings_manager.refresh_settings_config()
        return await settings_manager.update_settings_visibility(MONETIZATION, user["user_id"])
    except Exception as exc:
        logger.error("Error loading settings: %s", exc)
        raise HTTPException(status_code=500, detail=f"Error loading settings: {exc}")


@router.get("/api/module-settings/{module_name}")
async def get_module_settings(module_name: str, user: dict = Depends(get_current_user)):
    if MONETIZATION and module_name != "subscription_manager":
        if not await subscription_manager.is_module_accessible(user["user_id"], module_name):
            raise HTTPException(status_code=403, detail="Access denied to module settings")
    return await settings_manager.get_module_settings(user["user_id"], module_name)


@router.post("/api/module-settings/{module_name}")
async def save_module_settings(module_name: str, request: Request, user: dict = Depends(get_current_user)):
    if MONETIZATION and module_name != "subscription_manager":
        if not await subscription_manager.is_module_accessible(user["user_id"], module_name):
            raise HTTPException(status_code=403, detail="Access denied to module settings")
    data = await request.json()
    result = await settings_manager.save_module_settings(user["user_id"], module_name, data)
    event_bus.publish("module_settings_updated", {"user_id": user["user_id"], "module": module_name})
    return result


@router.post("/api/notification-preferences")
async def update_notification_preferences(body: NotificationPreferencesRequest, user: dict = Depends(get_current_user)):
    data = body.model_dump(exclude_unset=True)
    result = await settings_manager.save_notification_preferences(user["user_id"], data)
    event_bus.publish("notification_preferences_updated", {"user_id": user["user_id"]})
    return result
