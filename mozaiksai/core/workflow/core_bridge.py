# ==============================================================================
# FILE: core/workflow/core_bridge.py
# DESCRIPTION: AG2 tool functions that enable agents to interact with the
#              mozaikscore application substrate. These functions are designed
#              to be registered as AG2 agent tools so that AI workflows can
#              execute modules, send notifications, and query application state.
#
# Usage in workflow tools.json:
#   {
#     "name": "execute_core_module",
#     "type": "Agent_Tool",
#     "module": "mozaiksai.core.workflow.core_bridge",
#     "function": "execute_core_module"
#   }
# ==============================================================================
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from autogen import ConversableAgent

logger = logging.getLogger("mozaiksai.core_bridge")


async def execute_core_module(
    module_name: str,
    action: str,
    context_variables: Optional[Dict[str, Any]] = None,
    **kwargs: Any,
) -> str:
    """Execute a mozaikscore module from an AG2 agent.

    Args:
        module_name: Target module (e.g. "admin_portal").
        action: Module action (e.g. "get_dashboard", "list_users").
        context_variables: AG2-injected context with app_id, user_id, etc.
        **kwargs: Additional payload fields passed to the module.

    Returns:
        JSON string with the module result.
    """
    import json

    from mozaiksai.core.adapters.core_client import get_core_client
    from mozaiksai.core.ports.core_service import ModuleRequest

    ctx = context_variables or {}
    app_id = ctx.get("app_id", "")
    user_id = ctx.get("user_id", "")

    client = get_core_client()
    result = await client.execute_module(
        ModuleRequest(
            module_name=module_name,
            action=action,
            user_id=user_id,
            app_id=app_id,
            payload=kwargs,
        )
    )

    if result.success:
        return json.dumps({"status": "success", "data": result.data})
    else:
        return json.dumps({"status": "error", "error": result.error})


async def send_notification(
    user_id: str,
    title: str,
    message: str,
    category: str = "system",
    context_variables: Optional[Dict[str, Any]] = None,
) -> str:
    """Send a notification to a user via mozaikscore.

    Args:
        user_id: Target user ID.
        title: Notification title.
        message: Notification body.
        category: Notification category (system, account, subscription).
        context_variables: AG2-injected context.

    Returns:
        JSON string confirming delivery status.
    """
    import json

    from mozaiksai.core.adapters.core_client import get_core_client
    from mozaiksai.core.ports.core_service import NotificationRequest

    client = get_core_client()
    success = await client.create_notification(
        NotificationRequest(
            user_id=user_id,
            title=title,
            message=message,
            category=category,
        )
    )

    return json.dumps({"status": "sent" if success else "failed"})


async def get_user_subscription(
    context_variables: Optional[Dict[str, Any]] = None,
) -> str:
    """Fetch the current user's subscription status.

    Args:
        context_variables: AG2-injected context with user_id.

    Returns:
        JSON string with subscription data.
    """
    import json

    from mozaiksai.core.adapters.core_client import get_core_client

    ctx = context_variables or {}
    user_id = ctx.get("user_id", "")
    token = ctx.get("auth_token", "")

    client = get_core_client()
    data = await client.get_subscription(user_id, token)

    return json.dumps(data)


async def get_user_profile(
    context_variables: Optional[Dict[str, Any]] = None,
) -> str:
    """Fetch the current user's profile.

    Args:
        context_variables: AG2-injected context with user_id.

    Returns:
        JSON string with profile data.
    """
    import json

    from mozaiksai.core.adapters.core_client import get_core_client

    ctx = context_variables or {}
    user_id = ctx.get("user_id", "")
    token = ctx.get("auth_token", "")

    client = get_core_client()
    data = await client.get_user_profile(user_id, token)

    return json.dumps(data)


async def check_core_health(
    context_variables: Optional[Dict[str, Any]] = None,
) -> str:
    """Check the health of the mozaikscore substrate.

    Returns:
        JSON string with health status.
    """
    import json

    from mozaiksai.core.adapters.core_client import get_core_client

    client = get_core_client()
    health = await client.health()

    return json.dumps({
        "healthy": health.healthy,
        "version": health.version,
        "modules_loaded": health.modules_loaded,
    })


__all__ = [
    "execute_core_module",
    "send_notification",
    "get_user_subscription",
    "get_user_profile",
    "check_core_health",
]
