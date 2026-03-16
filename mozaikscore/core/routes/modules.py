# ==============================================================================
# FILE: mozaikscore/core/routes/modules.py
# DESCRIPTION: Module listing, execution, and access-check routes.
# ==============================================================================
import os
import time
import logging

from fastapi import APIRouter, Depends, HTTPException, Request

from mozaikscore.core.auth import get_current_user
from mozaikscore.core.config_loader import get_module_registry
from mozaikscore.core.event_bus import event_bus
from mozaikscore.core.module_manager import module_manager
from mozaikscore.core.schemas import ModuleExecuteRequest
from mozaikscore.core.state_manager import state_manager
from mozaikscore.core.subscription_manager import subscription_manager

logger = logging.getLogger("mozaikscore.routes.modules")

APP_ID = os.getenv("MOZAIKS_APP_ID", "dev_app")
MONETIZATION = os.getenv("MONETIZATION", "0") == "1"
ENV = os.getenv("ENV", "development")

router = APIRouter(tags=["modules"])


def _inject_request_context(user: dict, data: dict) -> dict:
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


@router.get("/api/available-modules")
async def get_available_modules(user: dict = Depends(get_current_user)):
    cache_key = f"available_modules:{user['user_id']}"
    cached = state_manager.get(cache_key)
    if cached and ENV != "development":
        return {"modules": cached}

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

    data = _inject_request_context(user, data)


    if MONETIZATION and module_name != "subscription_manager" and not await subscription_manager.is_module_accessible(user["user_id"], module_name):
        raise HTTPException(status_code=403, detail=f"Access denied: Subscription does not allow '{module_name}'.")

    t0 = time.time()
    try:
        result = await module_manager.execute_module(module_name, data)
        elapsed = time.time() - t0
        logger.info("Module %s executed in %.2fs for app=%s user=%s", module_name, elapsed, APP_ID, user["user_id"])
        if isinstance(result, dict) and "error" in result:
            raise HTTPException(status_code=500, detail=result["error"])
        event_bus.publish(
            "module_executed",
            {
                "app_id": APP_ID,
                "module": module_name,
                "action": data.get("action"),
                "user": user["user_id"],
                "execution_time": elapsed,
            },
        )
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
