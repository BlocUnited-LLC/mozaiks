from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from factory_app.workflows._shared.platform.build_target import require_build_binding


def _repo_root() -> Path:
    here = Path(__file__).resolve()
    for parent in [here] + list(here.parents):
        if (parent / "factory_app").is_dir() and (parent / "mozaiksai").is_dir():
            return parent
    return here.parents[-1]


def build_app_ai_config(app_manifest: dict[str, Any], *, workflow_entry_point: str | None = None) -> dict[str, Any]:
    """Render app-owned Ask behavior from the app's materialized identity."""
    name = app_manifest.get("appName") or app_manifest["appId"]
    purpose = app_manifest.get("description") or app_manifest.get("value_proposition") or ""
    prompt = (
        f"You are the assistant for {name}. {purpose}\n"
        "Help the user understand and use this app. Use the current app, page, user, "
        "and available action/workflow context supplied with each message. "
        "Respect the current user's permissions. Explain missing context honestly. "
        "Report an action as completed only after its tool confirms success."
    )
    return {
        "ask": {"ask_mode_prompt": prompt, "ask_context_variables": None},
        "chat": {"chat_startup_mode": "ask"},
        "workflows": {"entry_point": workflow_entry_point},
    }


def app_admin_bootstrap(context_variables: Any, *, baseline_manifest: dict[str, Any] | None = None) -> list[str]:
    """Bootstrap a genesis owner from trusted launch context, retaining revision grants."""
    if require_build_binding(context_variables).phase == "refinement":
        return list((baseline_manifest or {}).get("admins") or [])
    email = context_variables.get("platform_user_email") if context_variables is not None else None
    return [email.strip()] if isinstance(email, str) and email.strip() and email.isascii() else []


def load_default_control_plane_runtime_config() -> dict[str, Any]:
    path = _repo_root() / "factory_app" / "app" / "config" / "refinement_policy.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"Factory refinement_policy.yaml must contain a YAML object: {path}")
    return data


load_default_refinement_policy_config = load_default_control_plane_runtime_config

__all__ = [
    "build_app_ai_config",
    "app_admin_bootstrap",
    "load_default_control_plane_runtime_config",
    "load_default_refinement_policy_config",
]
