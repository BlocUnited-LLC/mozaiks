"""Endpoint helpers for generated agent backends."""

from __future__ import annotations

import os


def _render_template(template: str, app_id: str) -> str:
    return (
        template.replace("{app_id}", app_id)
        .replace("{{app_id}}", app_id)
        .replace("{appId}", app_id)
        .replace("{{appId}}", app_id)
    )


def _resolve_from_env(env_key: str, app_id: str) -> str | None:
    template = os.getenv(env_key, "").strip()
    if not template or not app_id:
        return None
    return _render_template(template, app_id)


def resolve_agent_websocket_url(app_id: str) -> str | None:
    return _resolve_from_env("MOZAIKS_AGENT_WEBSOCKET_URL_TEMPLATE", str(app_id or "").strip())


def resolve_agent_api_url(app_id: str) -> str | None:
    return _resolve_from_env("MOZAIKS_AGENT_API_URL_TEMPLATE", str(app_id or "").strip())


__all__ = ["resolve_agent_api_url", "resolve_agent_websocket_url"]
