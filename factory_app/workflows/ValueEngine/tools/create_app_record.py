"""
create_app_record — called at the start of the ValueEngine pipeline.

Creates a hosted product build-registry record for the current user so the Hub
immediately shows the app as in-progress while the factory pipeline runs.

Best-effort: never raises. If the hosted build-registry module is not installed
(OSS user without mozaiks-app) this silently no-ops.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Optional

logger = logging.getLogger(__name__)

_API_BASE = os.getenv("MOZAIKSAI_URL") or os.getenv("VITE_API_URL") or "http://localhost:8000"


def _set_context_value(context_variables: Optional[Any], key: str, value: Any) -> None:
    if context_variables is None:
        return
    try:
        if hasattr(context_variables, "set"):
            context_variables.set(key, value)
            return
    except Exception:
        pass
    try:
        if hasattr(context_variables, "__setitem__"):
            context_variables[key] = value
    except Exception:
        return


async def _call_module(action: str, payload: dict) -> Optional[dict]:
    try:
        import httpx
        url = f"{_API_BASE}/api/modules/app_registry/{action}"
        async with httpx.AsyncClient(timeout=5.0) as client:
            res = await client.post(url, json=payload)
            if res.status_code == 200:
                return res.json()
    except Exception as exc:
        logger.debug("app_registry module call failed (non-fatal): %s", exc)
    return None


async def create_app_record(context_variables: Optional[Any] = None) -> dict:
    """
    Create a hosted build-registry record with status 'building'.
    Called by the on_workflow_start hook in ValueEngine.
    """
    cv = context_variables or {}
    if hasattr(cv, "get"):
        user_id = cv.get("user_id") or "anonymous"
        app_id = cv.get("app_id") or ""
        app_name = cv.get("app_name") or ""
    else:
        user_id = "anonymous"
        app_id = ""
        app_name = ""

    payload = {
        "name": app_name or "New App",
        "description": "",
        "user_id": user_id,
        "app_id": app_id,
        "status": "building",
    }
    result = await _call_module("create_app_record", payload)

    if result and result.get("success"):
        app_payload = result.get("app", {}) if isinstance(result, dict) else {}
        build_registry_id = str(app_payload.get("build_registry_id") or "")
        if not build_registry_id:
            return {
                "success": False,
                "error": "Hosted build-registry response did not include build_registry_id.",
            }
        logger.info(
            "Created build registry record: %s for user %s",
            build_registry_id,
            user_id,
        )
        _set_context_value(cv, "build_registry_id", build_registry_id)
        return {
            "success": True,
            "build_registry_id": build_registry_id,
        }

    return {"success": False}
