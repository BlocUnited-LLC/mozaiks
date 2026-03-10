# ==============================================================================
# FILE: modules/admin_portal/handler.py
# DESCRIPTION: Admin Portal module — dispatches admin actions.
#              This is the first mozaikscore module, conforming to the
#              execute(data) contract expected by ModuleManager.
# ==============================================================================
import logging
from datetime import datetime

logger = logging.getLogger("mozaikscore.modules.admin_portal")


async def execute(data: dict) -> dict:
    """
    Module entry point.  Called by ModuleManager.execute_module("admin_portal", data).

    Expected data shape:
        {
            "action": "<action_name>",
            "app_id": "...",       # injected by director
            "user_id": "...",      # injected by director
            "_context": {...},     # injected by director
            ...action-specific fields...
        }

    Returns:
        dict with action result or error.
    """
    action = data.get("action")
    context = data.get("_context", {})
    user_id = data.get("user_id", "unknown")

    logger.info("admin_portal execute: action=%s user=%s", action, user_id)

    # ------------------------------------------------------------------
    # Action dispatch
    # ------------------------------------------------------------------
    if action == "get_dashboard":
        return await _get_dashboard(context)
    elif action == "list_users":
        return await _list_users(context, data)
    elif action == "get_module_status":
        return await _get_module_status(context)
    elif action == "health":
        return _health()
    else:
        return {"error": f"Unknown action: {action}", "available_actions": ["get_dashboard", "list_users", "get_module_status", "health"]}


# ------------------------------------------------------------------
# Actions
# ------------------------------------------------------------------

async def _get_dashboard(context: dict) -> dict:
    """Return high-level platform stats for the admin dashboard."""
    from mozaikscore.core.database import get_users_collection, get_subscriptions_collection
    from mozaikscore.core.module_manager import module_manager

    users = get_users_collection()
    subs = get_subscriptions_collection()

    user_count = await users.count_documents({})
    active_subs = await subs.count_documents({"status": {"$in": ["active", "trialing"]}})

    return {
        "total_users": user_count,
        "active_subscriptions": active_subs,
        "loaded_modules": list(module_manager.modules.keys()),
        "timestamp": datetime.utcnow().isoformat(),
    }


async def _list_users(context: dict, data: dict) -> dict:
    """Paginated user list (superadmin only)."""
    if not context.get("is_superadmin"):
        return {"error": "Forbidden — superadmin required"}

    from mozaikscore.core.database import get_users_collection

    users = get_users_collection()
    limit = min(int(data.get("limit", 20)), 100)
    offset = int(data.get("offset", 0))

    cursor = users.find(
        {}, {"hashed_password": 0}
    ).skip(offset).limit(limit).sort("_id", -1)

    results = []
    async for doc in cursor:
        doc["_id"] = str(doc["_id"])
        results.append(doc)

    total = await users.count_documents({})
    return {"users": results, "total": total, "limit": limit, "offset": offset}


async def _get_module_status(context: dict) -> dict:
    """Return loaded module info."""
    from mozaikscore.core.module_manager import module_manager
    from mozaikscore.core.config_loader import get_module_registry

    registry = get_module_registry() or {"modules": []}
    return {
        "loaded": list(module_manager.modules.keys()),
        "registry": registry.get("modules", []),
    }


def _health() -> dict:
    return {"status": "ok", "module": "admin_portal", "timestamp": datetime.utcnow().isoformat()}
