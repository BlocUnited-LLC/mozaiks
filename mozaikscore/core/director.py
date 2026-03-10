# ==============================================================================
# FILE: mozaikscore/core/director.py
# DESCRIPTION: API routes for mozaikscore — application CRUD, settings, profiles,
#              navigation, theme, module execution, subscriptions, notifications.
#              AI/chat routes remain in mozaiksai; this is the non-AI substrate.
# ORIGIN: Migrated from mozaiks-core-public/backend/core/director.py
# ==============================================================================
import os
import json
import time
import logging
import asyncio
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from bson import ObjectId

from mozaikscore.core.module_manager import module_manager
from mozaikscore.core.subscription_manager import subscription_manager
from mozaikscore.core.event_bus import event_bus
from mozaikscore.core.state_manager import state_manager
from mozaikscore.core.settings_manager import settings_manager
from mozaikscore.core.notifications_manager import notifications_manager
from mozaikscore.core.database import get_users_collection, get_cached_document, db_cache
from mozaikscore.core.config_loader import (
    get_config_path,
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


def inject_request_context(user: dict, data: dict) -> dict:
    """Server-derived context — cannot be overridden by client."""
    data["app_id"] = APP_ID
    data["user_id"] = user["user_id"]
    data["_context"] = {
        "app_id": APP_ID,
        "user_id": user["user_id"],
        "username": user.get("username"),
        "roles": user.get("roles", []),
        "is_superadmin": user.get("is_superadmin", False),
    }
    return data


# ===========================================================================
# Auth dependency — imported from mozaiksai shared auth
# ===========================================================================
def _get_auth_dependency():
    """
    Lazily import require_user from mozaiksai auth.
    Falls back to a no-op stub in standalone dev mode.
    """
    try:
        from mozaiksai.core.auth.dependencies import require_user
        return require_user
    except ImportError:
        logger.warning("mozaiksai auth not available — using dev stub")

        async def _dev_user():
            return {"user_id": "dev_user", "username": "dev", "roles": ["admin"], "is_superadmin": True}

        return _dev_user


get_current_user = _get_auth_dependency()


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
    return load_config("navigation_config.json")


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
        for item in nav_config.get("default", []):
            if item.get("path") == "/subscriptions" and not MONETIZATION:
                continue
            final_nav.append(item)

        for mod_item in nav_config.get("modules", nav_config.get("plugins", [])):
            mod_name = mod_item.get("module_name") or mod_item.get("plugin_name")
            if not mod_name or mod_name == "subscription_manager":
                continue
            enabled = any(m.get("name") == mod_name and m.get("enabled", True) for m in installed)
            if enabled:
                if not MONETIZATION or await subscription_manager.is_module_accessible(user["user_id"], mod_name):
                    final_nav.append(mod_item)

        ttl = 60 if ENV == "development" else 300
        state_manager.set(cache_key, final_nav, expire_in=ttl)
        return {"navigation": final_nav}
    except Exception as exc:
        logger.error("Error generating navigation: %s", exc)
        raise HTTPException(status_code=500, detail=f"Error generating navigation: {exc}")


# ---------------------------------------------------------------------------
# Theme
# ---------------------------------------------------------------------------
@router.get("/api/theme-config")
async def get_theme_config_route():
    return load_config("theme_config.json")


@router.post("/api/change-theme")
async def change_theme(request: Request, user: dict = Depends(get_current_user)):
    data = await request.json()
    new_theme = data.get("theme_name")
    if not new_theme:
        raise HTTPException(status_code=400, detail="Theme name is required")
    state_manager.set(f"theme_{user['user_id']}", new_theme)
    event_bus.publish("theme_changed", {"user_id": user["user_id"], "theme": new_theme})
    return {"message": f"Theme changed to {new_theme}", "theme": new_theme}


@router.get("/api/current-theme")
async def get_current_theme(user: dict = Depends(get_current_user)):
    theme = state_manager.get(f"theme_{user['user_id']}")
    if not theme:
        cfg = load_config("theme_config.json")
        theme = cfg.get("default_theme", "light")
    return {"theme": theme}


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------
@router.get("/api/settings-config")
async def get_settings_config_route(user: dict = Depends(get_current_user)):
    try:
        settings_manager.refresh_settings_config()
        return await settings_manager.update_settings_visibility(MONETIZATION, user["user_id"])
    except Exception as exc:
        logger.error("Error loading settings: %s", exc)
        raise HTTPException(status_code=500, detail=f"Error loading settings: {exc}")


# ---------------------------------------------------------------------------
# User profile
# ---------------------------------------------------------------------------
@router.get("/api/user-profile")
async def get_user_profile(user: dict = Depends(get_current_user)):
    cache_key = f"user_profile:{user['user_id']}"
    cached = state_manager.get(cache_key)
    if cached and ENV != "development":
        return cached
    users = get_users_collection()
    user_data = await get_cached_document(users, {"username": user["username"]}, cache_key=f"user:{user['username']}")
    if not user_data:
        raise HTTPException(status_code=404, detail="User not found")
    user_data.pop("hashed_password", None)
    user_data["_id"] = str(user_data["_id"])
    ttl = 60 if ENV == "development" else 300
    state_manager.set(cache_key, user_data, expire_in=ttl)
    return user_data


@router.post("/api/update-profile")
async def update_user_profile(request: Request, user: dict = Depends(get_current_user)):
    try:
        data = await request.json()
        protected = {"_id", "username", "email", "hashed_password", "user_id"}
        update = {k: v for k, v in data.items() if k not in protected}
        if not update:
            raise HTTPException(status_code=400, detail="No valid fields to update")
        update["updated_at"] = datetime.utcnow().isoformat()
        users = get_users_collection()
        result = await users.update_one({"username": user["username"]}, {"$set": update})
        if result.modified_count == 0:
            raise HTTPException(status_code=404, detail="User not found or no changes")
        state_manager.delete(f"user_profile:{user['user_id']}")
        db_cache.invalidate(f"user:{user['username']}")
        event_bus.publish("profile_updated", {"user_id": user["user_id"]})
        return {"message": "Profile updated successfully"}
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Error updating profile: %s", exc)
        raise HTTPException(status_code=500, detail="Internal server error")


# ---------------------------------------------------------------------------
# Modules (execute, list, settings, access check)
# ---------------------------------------------------------------------------
@router.get("/api/available-modules")
async def get_available_modules(user: dict = Depends(get_current_user)):
    cache_key = f"available_modules:{user['user_id']}"
    cached = state_manager.get(cache_key)
    if cached and ENV != "development":
        return {"modules": cached}

    await ensure_modules_up_to_date()
    registry = get_module_registry() or {"modules": []}
    enabled = [m for m in registry.get("modules", registry.get("plugins", [])) if m.get("enabled", True)]

    if not MONETIZATION:
        ttl = 60 if ENV == "development" else 300
        state_manager.set(cache_key, enabled, expire_in=ttl)
        return {"modules": enabled}

    accessible = [m for m in enabled if await subscription_manager.is_module_accessible(user["user_id"], m["name"])]
    ttl = 60 if ENV == "development" else 300
    state_manager.set(cache_key, accessible, expire_in=ttl)
    return {"modules": accessible}


@router.post("/api/execute/{module_name}")
async def execute_module(module_name: str, request: Request, user: dict = Depends(get_current_user)):
    registry = get_module_registry() or {"modules": []}
    installed = registry.get("modules", registry.get("plugins", []))
    exists = any(m.get("name") == module_name and m.get("enabled", True) for m in installed)
    if not exists:
        raise HTTPException(status_code=404, detail=f"Module '{module_name}' not found or disabled")

    if module_name not in module_manager.modules:
        await module_manager.refresh_modules()
        if module_name not in module_manager.modules:
            raise HTTPException(status_code=404, detail=f"Module '{module_name}' not found")

    try:
        data = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON payload")

    data = inject_request_context(user, data)

    if MONETIZATION and module_name != "subscription_manager" and not await subscription_manager.is_module_accessible(user["user_id"], module_name):
        raise HTTPException(status_code=403, detail=f"Access denied: Subscription does not allow '{module_name}'.")

    t0 = time.time()
    try:
        result = await module_manager.execute_module(module_name, data)
        elapsed = time.time() - t0
        logger.info("Module %s executed in %.2fs for app=%s user=%s", module_name, elapsed, APP_ID, user["user_id"])
        if isinstance(result, dict) and "error" in result:
            raise HTTPException(status_code=500, detail=result["error"])
        event_bus.publish("module_executed", {"app_id": APP_ID, "module": module_name, "user": user["user_id"], "execution_time": elapsed})
        return result
    except HTTPException:
        raise
    except Exception as exc:
        elapsed = time.time() - t0
        logger.error("Error executing module %s (%.2fs): %s", module_name, elapsed, exc)
        event_bus.publish("module_execution_error", {"module": module_name, "user": user["user_id"], "error": str(exc), "execution_time": elapsed})
        raise HTTPException(status_code=500, detail=f"Error executing module: {exc}")


@router.get("/api/check-module-access/{module_name}")
async def check_module_access(module_name: str, user: dict = Depends(get_current_user)):
    cache_key = f"module_access:{user['user_id']}:{module_name}"
    cached = state_manager.get(cache_key)
    if cached is not None:
        return {"module": module_name, "access": cached}
    if not MONETIZATION:
        state_manager.set(cache_key, True, expire_in=60)
        return {"module": module_name, "access": True}
    access = await subscription_manager.is_module_accessible(user["user_id"], module_name)
    state_manager.set(cache_key, access, expire_in=60)
    return {"module": module_name, "access": access}


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


# ---------------------------------------------------------------------------
# Notification preferences
# ---------------------------------------------------------------------------
@router.post("/api/notification-preferences")
async def update_notification_preferences(request: Request, user: dict = Depends(get_current_user)):
    data = await request.json()
    result = await settings_manager.save_notification_preferences(user["user_id"], data)
    event_bus.publish("notification_preferences_updated", {"user_id": user["user_id"]})
    return result


# ---------------------------------------------------------------------------
# Subscription routes (MONETIZATION=1 only)
# ---------------------------------------------------------------------------
if MONETIZATION:

    @router.get("/api/subscription-plans")
    async def get_subscription_plans():
        return load_config("subscription_config.json")

    @router.get("/api/user-subscription")
    async def get_user_subscription(user: dict = Depends(get_current_user)):
        cache_key = f"user_subscription:{user['user_id']}"
        cached = state_manager.get(cache_key)
        if cached is not None:
            return cached
        sub = await subscription_manager.get_user_subscription(user["user_id"])
        state_manager.set(cache_key, sub, expire_in=300)
        return sub

    @router.post("/api/update-subscription")
    async def update_subscription(request: Request, user: dict = Depends(get_current_user)):
        data = await request.json()
        new_plan = data.get("new_plan")
        if not new_plan:
            raise HTTPException(status_code=400, detail="New plan is required")
        response = await subscription_manager.change_user_subscription(user["user_id"], new_plan)
        state_manager.delete(f"user_subscription:{user['user_id']}")
        state_manager.delete(f"navigation:{user['user_id']}")
        for key in list(state_manager.state.keys()):
            if key.startswith(f"module_access:{user['user_id']}:"):
                state_manager.delete(key)
        event_bus.publish("subscription_updated", {"user_id": user["user_id"], "plan": new_plan})
        return response

    @router.post("/api/cancel-subscription")
    async def cancel_subscription(user: dict = Depends(get_current_user)):
        response = await subscription_manager.cancel_user_subscription(user["user_id"])
        state_manager.delete(f"user_subscription:{user['user_id']}")
        state_manager.delete(f"navigation:{user['user_id']}")
        for key in list(state_manager.state.keys()):
            if key.startswith(f"module_access:{user['user_id']}:"):
                state_manager.delete(key)
        event_bus.publish("subscription_canceled", {"user_id": user["user_id"]})
        return response


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
