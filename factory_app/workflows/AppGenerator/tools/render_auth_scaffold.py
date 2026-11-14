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
from mozaiksai.core.workflow.generator_support.design_access_policy import (
    resolve_design_access_policy,
)
from mozaiksai.core.workflow.generator_support.module_role_grants import (
    compiled_role_grants,
    validate_access_policy,
)
from mozaiksai.resources import resolve_factory_app_root

from .code_file_utils import (
    admitted_app_file_map,
    compose_bundle_auth_routes,
    overlay_pack_composed_roots,
)


def materialize_auth_scaffold(
    files: dict[str, str], *, data_contract: Any = None,
    design_surface_map: Any = None, access_policy: Any = None, auth_mode: str | None = None,
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
    policy = validate_access_policy(
        access_policy, data_contract=data_contract, design_surface_map=design_surface_map,
    ) if access_policy is not None else None
    if policy and policy["roles"]:
        manifest["authRequired"] = True
        generated["app.json"] = json.dumps(manifest, indent=2) + "\n"
    if auth_mode is not None:
        manifest["authRequired"] = True
        generated["app.json"] = json.dumps(manifest, indent=2) + "\n"
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
        else:
            try:
                config = yaml.safe_load(generated["config/auth.yaml"])
            except yaml.YAMLError:
                raise AppAuthContractError("Cannot read valid YAML from config/auth.yaml") from None
        if not isinstance(config, dict):
            raise AppAuthContractError("config/auth.yaml must be a YAML object")
        if auth_mode is not None:
            config["mode"] = auth_mode
            config["signup_enabled"] = auth_mode == "public_self_signup"
            for method in config.get("login_methods") or []:
                if method.get("kind") == "oidc_redirect" and method.get("primary"):
                    if config["signup_enabled"]:
                        method["label"] = "Sign in or create account"
                    elif method.get("label") == "Sign in or create account":
                        method["label"] = "Sign in"
        if policy is not None:
            config["authorization"] = {"roles": compiled_role_grants(policy)}
        validate_app_auth_contract(config)
        encoded = yaml.safe_dump(config, sort_keys=False)
        if "config/auth.yaml" not in generated or policy is not None or auth_mode is not None:
            rendered["config/auth.yaml"] = encoded
        if "ui/auth/authAdapter.js" not in generated:
            rendered["ui/auth/authAdapter.js"] = (templates / "ui" / "auth" / "authAdapter.js").read_text(encoding="utf-8")
        generated.update(rendered)
        compose_bundle_auth_routes(generated)
        rendered["ui/route_manifest.json"] = generated["ui/route_manifest.json"]

    return rendered


async def save_auth_scaffold(
    context_variables: Any, *, revision_baseline: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Fill auth files and compile approved roles while preserving provider setup.

    ``revision_baseline`` is the bound revision source the caller loaded
    (:func:`load_revision_baseline`). An admitted repair copy of a
    pack-composed UI root stays in the overlay as saved: the admitted
    projection composes it and adds the auth routes itself, while a manifest
    rendered here from a projection that could not compose it would replace
    the repair with the pre-repair root.
    """
    context = context_to_dict(context_variables)
    rendered = materialize_auth_scaffold(
        admitted_app_file_map(context_variables, revision_baseline=revision_baseline),
        data_contract=context.get("data_contract"),
        design_surface_map=context.get("design_surface_map"),
        access_policy=resolve_design_access_policy(context),
        auth_mode=(context.get("app_build_plan") or {}).get("auth_mode"),
    )
    for path in overlay_pack_composed_roots(context_variables) & rendered.keys():
        rendered.pop(path)

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
