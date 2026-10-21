"""Materialize provider-neutral auth from the assembled app's declared intent.

Deployment artifacts belong to generate_and_download's deployment renderer.
"""

from __future__ import annotations

import json
from typing import Any

import yaml

from mozaiksai.core.runtime.app.auth_contract import (
    AppAuthContractError,
    validate_app_auth_contract,
)
from mozaiksai.core.workflow.context.context_utils import context_to_dict
from mozaiksai.core.workflow.generator_support.code_files import materialize_collection_auth
from mozaiksai.resources import resolve_factory_app_root

from .code_file_utils import admitted_app_file_map, compose_bundle_auth_routes


def materialize_auth_scaffold(
    files: dict[str, str], *, data_contract: Any = None,
) -> dict[str, str]:
    """Render the canonical auth file delta for a constructed app bundle."""
    generated = dict(files)
    if "app.json" not in generated:
        raise ValueError("Auth scaffolding requires the assembled app.json")
    original_manifest = generated["app.json"]
    generated = materialize_collection_auth(generated, data_contract=data_contract)
    manifest = json.loads(generated["app.json"])
    if not isinstance(manifest, dict):
        raise AppAuthContractError("Auth scaffolding requires app.json to be an object")
    rendered: dict[str, str] = {}
    if generated["app.json"] != original_manifest:
        rendered["app.json"] = generated["app.json"]
    if manifest.get("authRequired") is True:
        root = resolve_factory_app_root()
        if root is None:
            raise AppAuthContractError("Auth scaffolding requires the Factory templates")
        templates = root / "build_context/webapp_builder/templates"
        if "config/auth.yaml" not in generated:
            startup = manifest.get("startup") or {}
            if not isinstance(startup, dict):
                raise AppAuthContractError("Auth scaffolding requires app.json.startup to be an object")
            route = str(startup.get("landing_spot") or "/")
            config = (templates / "config" / "auth.yaml").read_text(encoding="utf-8")
            config = yaml.safe_load(config.replace("{{AUTH_DEFAULT_ROUTE}}", "/"))
            config["routes"]["post_login_default"] = route
            validate_app_auth_contract(config)
            rendered["config/auth.yaml"] = yaml.safe_dump(config, sort_keys=False)
        if "ui/auth/authAdapter.js" not in generated:
            rendered["ui/auth/authAdapter.js"] = (templates / "ui" / "auth" / "authAdapter.js").read_text(encoding="utf-8")
        generated.update(rendered)
        compose_bundle_auth_routes(generated)
        rendered["ui/route_manifest.json"] = generated["ui/route_manifest.json"]

    return rendered


async def save_auth_scaffold(context_variables: Any) -> dict[str, Any]:
    """Fill missing auth files and compose routes without replacing declared auth."""
    context = context_to_dict(context_variables)
    rendered = materialize_auth_scaffold(
        admitted_app_file_map(context_variables), data_contract=context.get("data_contract"),
    )

    if not rendered:
        return {"code_files": []}
    overlay = {item["filename"]: item["content"] for item in context.get("code_files") or []}
    overlay.update(rendered)
    entries = [{"filename": path, "content": content} for path, content in sorted(overlay.items())]
    updates = {
        "code_files": entries,
        "deleted_files": sorted(set(context.get("deleted_files") or []) - rendered.keys()),
    }
    for key, value in updates.items():
        if hasattr(context_variables, "set"):
            context_variables.set(key, value)
        else:
            context_variables[key] = value
    return {"code_files": [{"filename": path, "content": content} for path, content in sorted(rendered.items())]}
