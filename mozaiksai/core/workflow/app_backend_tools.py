# ==============================================================================
# FILE: mozaiksai/core/workflow/app_backend_tools.py
# DESCRIPTION: AG2 tool functions that give workflow agents access to the
#              app backend via the generic AppBackendPort adapter.
#
#              These tools are backend-agnostic — they accept path and payload
#              as arguments, so the same runtime works with any CRUD backend.
#
# Usage in workflow tools.yaml:
#   {
#     "name": "backend_request",
#     "type": "Agent_Tool",
#     "module": "mozaiksai.core.workflow.app_backend_tools",
#     "function": "backend_request"
#   }
# ==============================================================================
from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger("mozaiksai.app_backend_tools")


async def backend_request(
    method: str,
    path: str,
    payload: dict[str, Any] | None = None,
    context_variables: dict[str, Any] | None = None,
) -> str:
    """Make a generic HTTP request to the app backend.

    Args:
        method: HTTP method (GET, POST, PUT, DELETE, PATCH).
        path: API path (e.g. "/api/execute/lineup_board").
        payload: Optional JSON body.
        context_variables: AG2-injected context (app_id, user_id, auth_token).

    Returns:
        JSON string with the backend response.
    """
    from mozaiksai.core.adapters.http_app_backend import get_app_backend

    ctx = context_variables or {}
    token = ctx.get("auth_token")

    backend = get_app_backend()
    result = await backend.request(method, path, json_body=payload, user_token=token)

    if result.success:
        return json.dumps({"status": "success", "data": result.data})
    return json.dumps({"status": "error", "error": result.error})


async def emit_event(
    event_type: str,
    event_data: dict[str, Any] | None = None,
    context_variables: dict[str, Any] | None = None,
) -> str:
    """Emit a domain event from a workflow agent.

    Args:
        event_type: Dot-delimited event name (e.g. "listing.saved").
        event_data: Arbitrary event payload.
        context_variables: AG2-injected context.

    Returns:
        JSON string confirming dispatch status.
    """
    from mozaiksai.core.adapters.http_app_backend import get_app_backend

    ctx = context_variables or {}
    data = {**(event_data or {}), "app_id": ctx.get("app_id", ""), "user_id": ctx.get("user_id", "")}

    backend = get_app_backend()
    ok = await backend.emit(event_type, data)

    return json.dumps({"status": "emitted" if ok else "failed", "event_type": event_type})


async def check_backend_health(
    context_variables: dict[str, Any] | None = None,
) -> str:
    """Check whether the app backend is reachable.

    Returns:
        JSON string with health status.
    """
    from mozaiksai.core.adapters.http_app_backend import get_app_backend

    backend = get_app_backend()
    health = await backend.health()

    return json.dumps({"healthy": health.healthy, "version": health.version})


__all__ = [
    "backend_request",
    "emit_event",
    "check_backend_health",
]
