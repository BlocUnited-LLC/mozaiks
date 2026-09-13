"""Materialize provider-neutral auth from the assembled app's declared intent.

Deployment artifacts belong to generate_and_download's deployment renderer.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml

from mozaiksai.core.runtime.app.auth_contract import validate_app_auth_contract
from mozaiksai.core.workflow.context.context_utils import context_to_dict

from .code_file_utils import compose_bundle_auth_routes

_TEMPLATES = Path(__file__).resolve().parents[3] / "build_context" / "webapp_builder" / "templates"


async def save_auth_scaffold(context_variables: Any) -> dict[str, Any]:
    """Persist auth config, facade, and routes before runtime bundle validation."""
    context = context_to_dict(context_variables)
    generated = dict(context.get("generated_files") or {})
    if "app.json" not in generated:
        raise ValueError("Auth scaffolding requires the assembled app.json")
    manifest = json.loads(generated["app.json"])
    rendered: dict[str, str] = {}
    if manifest.get("authRequired") is True:
        route = str((manifest.get("startup") or {}).get("landing_spot") or "/")
        config = (_TEMPLATES / "config" / "auth.yaml").read_text(encoding="utf-8")
        config = yaml.safe_load(config.replace("{{AUTH_DEFAULT_ROUTE}}", "/"))
        config["routes"]["post_login_default"] = route
        validate_app_auth_contract(config)
        rendered["config/auth.yaml"] = yaml.safe_dump(config, sort_keys=False)
        rendered["ui/auth/authAdapter.js"] = (_TEMPLATES / "ui" / "auth" / "authAdapter.js").read_text(encoding="utf-8")
        generated.update(rendered)
        compose_bundle_auth_routes(generated)
        rendered["ui/route_manifest.json"] = generated["ui/route_manifest.json"]

    overlay = {item["filename"]: item["content"] for item in context.get("code_files") or []}
    overlay.update(rendered)
    entries = [{"filename": path, "content": content} for path, content in sorted(overlay.items())]
    if hasattr(context_variables, "set"):
        context_variables.set("code_files", entries)
    else:
        context_variables["code_files"] = entries
    return {"code_files": [{"filename": path, "content": content} for path, content in sorted(rendered.items())]}
