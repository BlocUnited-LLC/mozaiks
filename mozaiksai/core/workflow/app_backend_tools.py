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
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from mozaiksai.core.runtime.composition.workflow_trigger_guard import (
    WORKFLOW_TRIGGER_TRACE_HEADER,
    WORKFLOW_TRIGGER_TRACE_KEY,
)

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
    try:
        from mozaiksai.core.adapters.http_app_backend import get_app_backend

        ctx = context_variables or {}
        token = ctx.get("auth_token")
        headers: dict[str, str] | None = None
        trigger_trace = ctx.get(WORKFLOW_TRIGGER_TRACE_KEY)
        if isinstance(trigger_trace, dict):
            headers = {
                WORKFLOW_TRIGGER_TRACE_HEADER: json.dumps(
                    trigger_trace,
                    separators=(",", ":"),
                )
            }

        backend = get_app_backend()
        result = await backend.request(
            method,
            path,
            json_body=payload,
            headers=headers,
            user_token=token,
        )

        if result.success:
            return json.dumps({"status": "success", "data": result.data})
        return json.dumps({"status": "error", "error": result.error})
    except Exception as exc:
        logger.error("backend_request tool error %s %s: %s", method, path, exc)
        return json.dumps({"status": "error", "error": "backend_request_failed"})


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
    try:
        from mozaiksai.core.adapters.http_app_backend import get_app_backend

        ctx = context_variables or {}
        tenant = {"app_id": ctx.get("app_id", "")}
        if ctx.get("tenant_id"):
            tenant["tenant_id"] = ctx["tenant_id"]
        if ctx.get("workspace_id"):
            tenant["workspace_id"] = ctx["workspace_id"]
        data: dict[str, Any] = {
            "id": f"evt_{uuid4().hex}",
            "type": event_type,
            "version": 1,
            "occurred_at": datetime.now(UTC).isoformat(),
            "source": {
                "layer": "workflow",
                "workflow_id": ctx.get("workflow_id") or ctx.get("workflow_name"),
                "chat_id": ctx.get("chat_id"),
            },
            "tenant": tenant,
            "payload": dict(event_data or {}),
            "visibility": "internal",
        }
        if ctx.get("user_id"):
            data["actor"] = {"type": "user", "id": ctx["user_id"]}
        if ctx.get("correlation_id"):
            data["correlation"] = {"correlation_id": ctx["correlation_id"]}
        trigger_trace = ctx.get(WORKFLOW_TRIGGER_TRACE_KEY)
        if isinstance(trigger_trace, dict):
            data[WORKFLOW_TRIGGER_TRACE_KEY] = dict(trigger_trace)

        backend = get_app_backend()
        ok = await backend.emit(event_type, data)

        return json.dumps({"status": "emitted" if ok else "failed", "event_type": event_type})
    except Exception as exc:
        logger.error("emit_event tool error %s: %s", event_type, exc)
        return json.dumps({"status": "failed", "event_type": event_type, "error": "emit_event_failed"})


async def check_backend_health(
    context_variables: dict[str, Any] | None = None,
) -> str:
    """Check whether the app backend is reachable.

    Returns:
        JSON string with health status.
    """
    try:
        from mozaiksai.core.adapters.http_app_backend import get_app_backend

        backend = get_app_backend()
        health = await backend.health()

        return json.dumps({"healthy": health.healthy, "version": health.version})
    except Exception as exc:
        logger.error("check_backend_health tool error: %s", exc)
        return json.dumps({"healthy": False, "version": "unknown"})


__all__ = [
    "backend_request",
    "emit_event",
    "check_backend_health",
]
