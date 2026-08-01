"""Emit the App Intelligence overview after deterministic repository indexing."""

from __future__ import annotations

import logging
from typing import Any

from mozaiksai.core.workflow.ui_tools import emit_ui_surface

logger = logging.getLogger(__name__)


def _ctx_get(context_variables: Any, key: str, default: Any = None) -> Any:
    if context_variables is None:
        return default
    if isinstance(context_variables, dict):
        return context_variables.get(key, default)
    data = getattr(context_variables, "data", None)
    if isinstance(data, dict):
        return data.get(key, default)
    getter = getattr(context_variables, "get", None)
    if callable(getter):
        try:
            return getter(key, default)
        except TypeError:
            try:
                value = getter(key)
                return default if value is None else value
            except Exception:
                return default
        except Exception:
            return default
    return default


async def emit_repo_access_recovery_card(
    context_variables: Any | None = None,
    **_: Any,
) -> dict[str, Any]:
    """Show a visible recovery card when a GitHub repo cannot be read."""
    ctx = context_variables if context_variables is not None else {}
    recovery = _dict_value(_ctx_get(ctx, "repo_access_recovery"))

    if not recovery:
        return {"skipped": True, "reason": "no_repo_access_recovery"}

    payload = _repo_access_recovery_payload(ctx=ctx, recovery=recovery)
    chat_id = _ctx_get(ctx, "chat_id")

    try:
        event_id = await emit_ui_surface(
            "RepoAccessRecoveryCard",
            payload,
            chat_id=str(chat_id) if chat_id else None,
            workflow_name="ExistingAppDiscovery",
            agent_name="App Intelligence",
            display="inline",
        )
        logger.info(
            "[ExistingAppDiscovery] Repo access recovery emitted: repo=%s status=%s",
            payload.get("github_repo") or "unknown",
            payload.get("http_status") or "unknown",
        )
    except Exception as exc:
        logger.warning("[ExistingAppDiscovery] Repo access recovery emission failed: %s", exc)

    return {
        "success": True,
        "repo_access_status": payload.get("repo_access_status"),
        "github_repo": payload.get("github_repo"),
        "ui_event_id": event_id if "event_id" in locals() else None,
    }


def _repo_access_recovery_payload(*, ctx: Any, recovery: dict[str, Any]) -> dict[str, Any]:
    progress = _dict_value(_ctx_get(ctx, "app_intelligence_progress"))
    return {
        "schema_version": "mozaiks.repo_access_recovery.ui.v1",
        "repo_access_status": str(_ctx_get(ctx, "repo_access_status") or "required").strip() or "required",
        "provider": str(recovery.get("provider") or "github").strip(),
        "code": str(recovery.get("code") or "github_repo_access_required").strip(),
        "github_repo": str(recovery.get("github_repo") or _ctx_get(ctx, "github_repo") or "").strip(),
        "github_url": str(recovery.get("github_url") or "").strip(),
        "http_status": recovery.get("http_status"),
        "phase": str(recovery.get("phase") or "").strip(),
        "auth_present": bool(recovery.get("auth_present")),
        "message": str(recovery.get("message") or "Repository access is required before indexing can continue.").strip(),
        "recovery_actions": _list_value(recovery.get("recovery_actions")),
        "app_intelligence_status": str(_ctx_get(ctx, "app_intelligence_status") or "").strip(),
        "activity_type": "app_intelligence_indexing",
        "activity_agent": "App Intelligence",
        "activity_display_variant": "app_intelligence_progress",
        "activity_component_type": "AppIntelligenceProgressCard",
        "display_variant": "app_intelligence_progress",
        "progress": progress,
        "app_intelligence_progress": progress,
        "warnings": _dedupe(
            [
                *_list_value(_ctx_get(ctx, "context_graph_warnings")),
                *_list_value(progress.get("warnings")),
            ]
        )[:8],
    }


def _dict_value(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list_value(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _dedupe(values: list[Any]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        out.append(text)
    return out


__all__ = [
    "emit_repo_access_recovery_card",
]
