# ==============================================================================
# FILE: mozaikscore/core/routes/theme.py
# DESCRIPTION: Theme configuration and switching routes.
# ==============================================================================
import logging

from fastapi import APIRouter, Depends

from mozaikscore.core.auth import get_current_user
from mozaikscore.core.config_loader import get_theme_config
from mozaikscore.core.event_bus import event_bus
from mozaikscore.core.schemas import ChangeThemeRequest
from mozaikscore.core.state_manager import state_manager

logger = logging.getLogger("mozaikscore.routes.theme")

router = APIRouter(tags=["theme"])


@router.get("/api/theme-config")
async def get_theme_config_route():
    return get_theme_config() or {}


@router.post("/api/change-theme")
async def change_theme(body: ChangeThemeRequest, user: dict = Depends(get_current_user)):
    state_manager.set(f"theme_{user['user_id']}", body.theme_name)
    event_bus.publish("theme_changed", {"user_id": user["user_id"], "theme": body.theme_name})
    return {"message": f"Theme changed to {body.theme_name}", "theme": body.theme_name}


@router.get("/api/current-theme")
async def get_current_theme(user: dict = Depends(get_current_user)):
    theme = state_manager.get(f"theme_{user['user_id']}")
    if not theme:
        cfg = get_theme_config() or {}
        theme = cfg.get("default_theme", "light")
    return {"theme": theme}
