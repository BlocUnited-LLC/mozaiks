# ==============================================================================
# FILE: mozaikscore/core/director.py
# DESCRIPTION: Core director routes — health, app config, navigation,
#              admin config, automation config, debug.
#              Domain routes live in mozaikscore.core.routes.* modules.
# ORIGIN: Migrated from mozaiks-core-public/backend/core/director.py
# ==============================================================================
import os
import time
import logging
import asyncio

from fastapi import APIRouter, Depends, HTTPException

from mozaikscore.core.auth import get_current_user
from mozaikscore.core.module_manager import module_manager
from mozaikscore.core.subscription_manager import subscription_manager
from mozaikscore.core.state_manager import state_manager
from mozaikscore.core.config_loader import (
    get_ai_config,
    get_admin_config,
    get_automation_event_catalog,
    get_automation_routes,
    get_module_registry,
    get_navigation_config,
    get_theme_config,
    get_settings_config,
    get_notifications_config,
    get_subscription_config,
)

logger = logging.getLogger("mozaikscore.director")

# ===========================================================================
# Environment
# ===========================================================================
APP_ID = os.getenv("MOZAIKS_APP_ID", "dev_app")
MONETIZATION = os.getenv("MONETIZATION", "0") == "1"
ENV = os.getenv("ENV", "development")

# Module refresh guard
_module_refresh_in_progress = False


def get_app_id() -> str:
    return APP_ID


# ===========================================================================
# Module refresh helper
# ===========================================================================
async def ensure_modules_up_to_date():
    global _module_refresh_in_progress
    if _module_refresh_in_progress:
        return
    last = state_manager.get("last_module_refresh_time")
    now = time.time()
    if not last or (now - last > 300):
        state_manager.set("last_module_refresh_time", now)
        asyncio.create_task(_async_refresh_modules())


async def _async_refresh_modules():
    global _module_refresh_in_progress
    if _module_refresh_in_progress:
        return
    try:
        _module_refresh_in_progress = True
        await module_manager.refresh_modules()
        logger.info("Completed background module refresh")
    except Exception as exc:
        logger.error("Error in background module refresh: %s", exc)
    finally:
        _module_refresh_in_progress = False


# ===========================================================================
# Config loader helper
# ===========================================================================
_CONFIG_LOADERS = {
    "ai.json": get_ai_config,
    "admin.json": get_admin_config,
    "automation_event_catalog.json": get_automation_event_catalog,
    "automation_routes.json": get_automation_routes,
    "module_registry.json": get_module_registry,
    "navigation_config.json": get_navigation_config,
    "subscription_config.json": get_subscription_config,
    "theme_config.json": get_theme_config,
    "settings_config.json": get_settings_config,
    "notifications_config.json": get_notifications_config,
}


def load_config(filename: str) -> dict:
    loader = _CONFIG_LOADERS.get(filename)
    if not loader:
        raise HTTPException(status_code=404, detail=f"Configuration file {filename} not found.")
    try:
        config = loader()
        return config or {}
    except Exception as exc:
        logger.error("Error loading %s: %s", filename, exc)
        raise HTTPException(status_code=500, detail=f"Error loading {filename}: {exc}")


# ===========================================================================
# Router
# ===========================================================================
router = APIRouter()


# ---------------------------------------------------------------------------
# Health / root
# ---------------------------------------------------------------------------
@router.get("/")
async def read_root():
    asyncio.create_task(ensure_modules_up_to_date())
    return {"message": "Mozaiks Core", "version": "1.0.0", "app_id": APP_ID}


# ---------------------------------------------------------------------------
# App config
# ---------------------------------------------------------------------------
@router.get("/api/app-config")
async def get_app_config():
    try:
        theme = load_config("theme_config.json")
        identity = theme.get("identity", {})
        return {
            "monetization_enabled": MONETIZATION,
            "app_name": identity.get("app_name", identity.get("name", "Mozaiks")),
            "app_version": "1.0.0",
            "env": ENV,
        }
    except Exception:
        return {"monetization_enabled": MONETIZATION, "app_name": "Mozaiks", "app_version": "1.0.0", "env": ENV}


# ---------------------------------------------------------------------------
# Navigation
# ---------------------------------------------------------------------------
@router.get("/api/navigation-config")
async def get_navigation_config_full():
    """Serve the full navigation_config.json (pages, landing_spot, etc.)."""
    nav = load_config("navigation_config.json")
    ai = load_config("ai.json")
    startup_mode = ((ai.get("chat") or {}).get("startup_mode"))
    if startup_mode is not None:
        nav = {**nav, "startup_mode": startup_mode}
    workflows = ai.get("workflows") or {}
    entry_point = workflows.get("entry_point")
    resume_policy = workflows.get("resume_policy")
    if entry_point is not None:
        nav = {**nav, "entry_point": entry_point}
    if resume_policy is not None:
        nav = {**nav, "resume_policy": resume_policy}
    return nav


@router.get("/api/admin-config")
async def get_admin_config_full():
    """Serve the full admin.json for Admin Portal layout and navigation zones."""
    return load_config("admin.json")


@router.get("/api/automation-config")
async def get_automation_config():
    return {
        "events": load_config("automation_event_catalog.json").get("events", []),
        "routes": load_config("automation_routes.json").get("routes", []),
    }


@router.get("/api/navigation")
async def get_navigation(user: dict = Depends(get_current_user)):
    cache_key = f"navigation:{user['user_id']}"
    cached = state_manager.get(cache_key)
    if cached and ENV != "development":
        return {"navigation": cached}

    await ensure_modules_up_to_date()
    try:
        nav_config = load_config("navigation_config.json")
        registry = get_module_registry() or {"modules": []}
        installed = registry.get("modules", registry.get("plugins", []))

        final_nav: list = []
        for item in nav_config.get("pages", []):
            page_path = item.get("path") or item.get("href")
            if page_path == "/subscriptions" and not MONETIZATION:
                continue
            final_nav.append(item)

        for mod_item in installed:
            mod_name = mod_item.get("name")
            if not mod_name or mod_name == "subscription_manager":
                continue
            if not mod_item.get("enabled", True):
                continue
            if MONETIZATION and not await subscription_manager.is_module_accessible(user["user_id"], mod_name):
                continue

            module_label = mod_item.get("label") or mod_item.get("display_name") or mod_name
            final_nav.append(
                {
                    "module_name": mod_name,
                    "label": module_label,
                    "path": mod_item.get("path") or f"/modules/{mod_name}",
                    "icon": mod_item.get("icon") or "puzzle",
                    "component": mod_item.get("component") or "ModulePage",
                    "showInHeader": bool(mod_item.get("showInHeader", False)),
                    "order": mod_item.get("order", 50),
                    "meta": mod_item.get("meta")
                    or {"title": module_label, "requiresAuth": True},
                }
            )

        ttl = 60 if ENV == "development" else 300
        state_manager.set(cache_key, final_nav, expire_in=ttl)
        return {"navigation": final_nav}
    except Exception as exc:
        logger.error("Error generating navigation: %s", exc)
        raise HTTPException(status_code=500, detail=f"Error generating navigation: {exc}")


# ---------------------------------------------------------------------------
# Debug
# ---------------------------------------------------------------------------
@router.get("/api/debug/module-status")
async def debug_module_status():
    await module_manager.refresh_modules()
    registry = get_module_registry() or {"modules": []}
    return {
        "module_directory": str(module_manager.MODULES_DIR) if hasattr(module_manager, "MODULES_DIR") else "unknown",
        "monetization_enabled": MONETIZATION,
        "loaded_modules": list(module_manager.modules.keys()),
        "registry": registry,
        "navigation_config": load_config("navigation_config.json"),
    }
