"""
create_app_record — called at the start of the ValueEngine pipeline.

Creates or reopens an app lifecycle record for the current user so Studio
immediately shows the app as building while the factory pipeline runs.

Best-effort: never raises. Studio owns app-registry management endpoints; this
tool intentionally does not call the permissioned module action route.
"""

from __future__ import annotations

import hashlib
import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

_API_BASE = os.getenv("MOZAIKSAI_URL") or os.getenv("VITE_API_URL") or "http://localhost:8000"


def _set_context_value(context_variables: Any | None, key: str, value: Any) -> None:
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


def _context_get(context_variables: Any | None, key: str, default: Any = None) -> Any:
    if context_variables is None:
        return default
    if hasattr(context_variables, "get"):
        try:
            return context_variables.get(key) or default
        except Exception:
            pass
    if isinstance(context_variables, dict):
        return context_variables.get(key, default)
    data = getattr(context_variables, "data", None)
    if isinstance(data, dict):
        return data.get(key, default)
    return default


def _compact_ref_list(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    refs: list[dict[str, Any]] = []
    for item in value[:50]:
        if isinstance(item, str):
            text = item.strip()
            if text:
                refs.append({"id": text})
            continue
        if not isinstance(item, dict):
            continue
        ref: dict[str, Any] = {}
        for key in ("id", "context_id", "pack_id", "name", "kind", "source", "version"):
            text = str(item.get(key) or "").strip()
            if text:
                ref[key] = text
        if ref:
            refs.append(ref)
    return refs


def _build_context_profile(context_variables: Any | None) -> dict[str, Any]:
    workflow_sequence = str(_context_get(context_variables, "workflow_sequence", "build") or "build")
    selected_context_ids = _context_get(context_variables, "selected_context_ids", [])
    if not isinstance(selected_context_ids, list):
        selected_context_ids = []
    profile: dict[str, Any] = {
        "source": "factory_app",
        "workflow_sequence": workflow_sequence,
        "selected_context_ids": [str(item).strip() for item in selected_context_ids if str(item or "").strip()][:50],
    }
    capability_packs = _compact_ref_list(_context_get(context_variables, "capability_packs", []))
    operator_contracts = _compact_ref_list(_context_get(context_variables, "operator_contracts", []))
    operator_catalogs = _compact_ref_list(_context_get(context_variables, "operator_catalogs", []))
    if capability_packs:
        profile["capability_packs"] = capability_packs
    if operator_contracts:
        profile["operator_contracts"] = operator_contracts
    if operator_catalogs:
        profile["operator_catalogs"] = operator_catalogs
    return profile


async def _create_studio_app(payload: dict) -> dict | None:
    try:
        import httpx
        url = f"{_API_BASE}/api/studio/apps"
        async with httpx.AsyncClient(timeout=5.0) as client:
            res = await client.post(url, json=payload)
            if res.status_code == 200:
                return res.json()  # type: ignore[no-any-return]
    except Exception as exc:
        logger.debug("Studio app registry create failed (non-fatal): %s", exc)
    return None


def _provisional_build_app_id(*, chat_app_id: str, chat_id: str, build_id: str) -> str:
    seed = f"{chat_app_id}:{chat_id or build_id}".encode()
    digest = hashlib.sha1(seed).hexdigest()[:12]
    return f"draft-build-{digest}"


async def create_app_record(context_variables: Any | None = None) -> dict:
    """
    Create or reopen an app lifecycle record with status 'building'.
    Called by the on_workflow_start hook in ValueEngine.

    Intentionally does NOT pass the factory session app_id as the registry app_id,
    because all ValueEngine sessions share the same factory app_id (from MOZAIKS_APP_ID).
    Instead, it reserves a deterministic provisional app_id per workflow chat and
    stores the factory session app_id as chat_app_id for correct chat session resume
    routing from the Apps directory.
    """
    cv = context_variables or {}
    user_id = _context_get(cv, "user_id", "anonymous")
    context_app_id = str(_context_get(cv, "app_id", "") or "").strip()
    chat_app_id = str(
        _context_get(cv, "chat_app_id")
        or _context_get(cv, "factory_app_id")
        or _context_get(cv, "session_app_id")
        or context_app_id
        or ""
    ).strip()
    app_name = _context_get(cv, "app_name", "")
    chat_id = str(_context_get(cv, "chat_id", "") or "").strip()
    workflow_name = _context_get(cv, "workflow_name", "ValueEngine")
    build_id = str(_context_get(cv, "build_id", "") or chat_id).strip()
    build_registry_id = str(_context_get(cv, "build_registry_id", "") or "").strip()
    if not chat_id:
        return {
            "success": False,
            "skipped": True,
            "reason": "No workflow chat id available for app registry tracking.",
        }

    registry_app_id = (
        context_app_id
        if build_registry_id and context_app_id
        else _provisional_build_app_id(chat_app_id=chat_app_id, chat_id=chat_id, build_id=build_id)
    )
    build_context_profile = _build_context_profile(cv)
    current_build_run = {
        "build_id": build_id,
        "workflow_sequence": build_context_profile.get("workflow_sequence") or "build",
        "status": "building",
        "active_chat_id": chat_id,
        "active_workflow_id": workflow_name,
    }

    payload = {
        "name": app_name or None,
        "name_source": "manual" if app_name else "provisional",
        "description": "",
        "app_id": registry_app_id,
        "chat_app_id": chat_app_id,  # Factory session app_id needed for chat resume routing
        "status": "building",
        "active_chat_id": chat_id,
        "active_workflow_id": workflow_name,
        "build_context_profile": build_context_profile,
        "current_build_run": current_build_run,
    }
    _ = user_id

    result = await _create_studio_app(payload)

    if result and result.get("success"):
        app_payload = result.get("app", {}) if isinstance(result, dict) else {}
        build_registry_id = str(app_payload.get("build_registry_id") or "")
        new_app_id = str(app_payload.get("app_id") or "")
        if not build_registry_id:
            return {
                "success": False,
                "error": "Hosted build-registry response did not include build_registry_id.",
            }
        logger.info(
            "Created build registry record: %s app_id=%s for user %s",
            build_registry_id,
            new_app_id,
            user_id,
        )
        _set_context_value(cv, "build_registry_id", build_registry_id)
        if chat_app_id:
            _set_context_value(cv, "chat_app_id", chat_app_id)
        if new_app_id:
            _set_context_value(cv, "app_id", new_app_id)
        return {
            "success": True,
            "build_registry_id": build_registry_id,
            "app_id": new_app_id,
        }

    return {"success": False}
