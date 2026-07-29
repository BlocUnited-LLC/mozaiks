from __future__ import annotations

import json
import re
from pathlib import PurePosixPath
from typing import Any

import yaml

from mozaiksai.core.runtime.app.provenance import (
    build_default_app_provenance,
    dump_app_provenance_yaml,
)

_MODULE_CONTRACT_FILENAMES = {
    "admin.yaml",
    "events.yaml",
    "notifications.yaml",
    "policy_hooks.yaml",
    "profile.yaml",
    "relationships.yaml",
    "reactions.yaml",
    "settings.yaml",
}


def safe_relpath(raw: str) -> str | None:
    if not isinstance(raw, str):
        return None
    path = raw.replace("\\", "/").strip()
    if not path or path.startswith("/"):
        return None
    pure_path = PurePosixPath(path)
    if pure_path.is_absolute():
        return None
    if any(part == ".." for part in pure_path.parts):
        return None
    return str(pure_path)


def _unwrap_output_envelope(payload: Any) -> Any:
    if not isinstance(payload, dict) or len(payload) != 1:
        return payload
    key, value = next(iter(payload.items()))
    if isinstance(key, str) and key.endswith("Output") and isinstance(value, dict):
        return value
    return payload


def _canonical_generated_path(path: str) -> str:
    pure_path = PurePosixPath(path)
    parts = pure_path.parts
    if (
        len(parts) == 3
        and parts[0] == "modules"
        and parts[2] in _MODULE_CONTRACT_FILENAMES
    ):
        return str(PurePosixPath(parts[0], parts[1], "contracts", parts[2]))
    return str(pure_path)


def _page_file_stem(page: dict[str, Any]) -> str:
    route = str(page.get("route") or "").strip()
    if route and route != "/":
        candidate = route.strip("/").split("/")[-1]
    else:
        candidate = str(page.get("name") or page.get("id") or "page").strip()
    normalized = re.sub(r"[^A-Za-z0-9]+", "_", candidate).strip("_").lower()
    return normalized or "page"


def _materialize_app_schema_file_map(payload: dict[str, Any]) -> dict[str, str]:
    manifest = payload.get("manifest")
    pages = payload.get("pages")
    if not isinstance(manifest, dict) or not isinstance(pages, list):
        return {}

    file_map: dict[str, str] = {}
    default_route = manifest.get("default_route") or "/"
    auth_strategy = manifest.get("auth_strategy")
    app_json = {  # type: ignore[var-annotated]
        "appName": manifest.get("app_name") or manifest.get("name") or "Generated App",
        "startup": {"landing_spot": default_route},
        "targets": {"web": True, "mobile": False},
        "authRequired": bool(auth_strategy and auth_strategy != "public"),
        "admins": [],
    }
    file_map["app.json"] = json.dumps(app_json, indent=2, ensure_ascii=False)
    file_map["provenance.yaml"] = dump_app_provenance_yaml(
        build_default_app_provenance(
            app_kind="generated",
            created_mode="factory",
            workflow="AppGenerator",
        )
    )

    for page in pages:
        if not isinstance(page, dict):
            continue
        file_map[f"ui/pages/{_page_file_stem(page)}.yaml"] = yaml.dump(
            page,
            allow_unicode=True,
            sort_keys=False,
            default_flow_style=False,
        )

    optional_json_outputs = {
        "theme_config_patch": "brand/theme_config.json",
        "shell_config": "config/shell.json",
        "asset_manifest": "config/asset_manifest.json",
        "data_contract": "data/contract.json",
    }
    for key, path in optional_json_outputs.items():
        value = payload.get(key)
        if isinstance(value, dict):
            file_map[path] = json.dumps(value, indent=2, ensure_ascii=False)

    custom_route_bundle = payload.get("custom_route_bundle")
    if isinstance(custom_route_bundle, dict):
        route_manifest = custom_route_bundle.get("route_manifest")
        if route_manifest is not None:
            file_map["ui/route_manifest.json"] = json.dumps(
                route_manifest,
                indent=2,
                ensure_ascii=False,
            )
        page_files = custom_route_bundle.get("page_files")
        if isinstance(page_files, list):
            for item in page_files:
                if not isinstance(item, dict):
                    continue
                safe = safe_relpath(str(item.get("path") or ""))
                content = item.get("content")
                if safe and content is not None:
                    file_map[safe] = str(content)
        ui_index = custom_route_bundle.get("ui_index")
        if ui_index is not None:
            file_map["ui/index.js"] = str(ui_index)

    return file_map


def _materialize_module_contract_file_map(payload: dict[str, Any]) -> dict[str, str]:
    bundle = payload.get("module_contract")
    if not isinstance(bundle, dict):
        return {}
    module_id = str(bundle.get("module_id") or "").strip()
    if not module_id:
        return {}

    prefix = PurePosixPath("modules", module_id)
    file_map: dict[str, str] = {}
    yaml_outputs = {
        "module_yaml": prefix / "module.yaml",
        "events_yaml": prefix / "contracts" / "events.yaml",
        "reactions_yaml": prefix / "contracts" / "reactions.yaml",
        "notifications_yaml": prefix / "contracts" / "notifications.yaml",
        "policy_hooks_yaml": prefix / "contracts" / "policy_hooks.yaml",
        "settings_yaml": prefix / "contracts" / "settings.yaml",
        "admin_yaml": prefix / "contracts" / "admin.yaml",
        "relationships_yaml": prefix / "contracts" / "relationships.yaml",
        "runtime_extensions_yaml": prefix / "runtime_extensions.yaml",
    }
    for key, path in yaml_outputs.items():
        value = bundle.get(key)
        if value is None:
            continue
        if isinstance(value, (dict, list)):
            file_map[str(path)] = yaml.safe_dump(
                value,
                allow_unicode=True,
                sort_keys=False,
                default_flow_style=False,
            )
        else:
            file_map[str(path)] = str(value)

    profile_yaml = bundle.get("profile_yaml")
    if profile_yaml is not None:
        path = prefix / "contracts" / "profile.yaml"
        if isinstance(profile_yaml, (dict, list)):
            file_map[str(path)] = yaml.safe_dump(
                profile_yaml,
                allow_unicode=True,
                sort_keys=False,
                default_flow_style=False,
            )
        else:
            file_map[str(path)] = str(profile_yaml)

    return file_map


def _normalize_code_file_entries(raw_entries: Any) -> dict[str, str]:
    file_map: dict[str, str] = {}
    if not isinstance(raw_entries, list):
        return file_map

    for item in raw_entries:
        if not isinstance(item, dict):
            continue
        filename = item.get("filename") or item.get("path")
        content = item.get("content") or item.get("filecontent")
        if not filename or content is None:
            continue
        safe = safe_relpath(str(filename))
        if not safe:
            continue
        file_map[_canonical_generated_path(safe)] = str(content)
    return file_map


def extract_code_file_map_from_payload(payload: Any) -> dict[str, str]:
    """Resolve deterministic code files from a structured agent payload.

    Handles the generic file lanes used across all generator workflows.
    AppGenerator-specific expansions (e.g. app_backend_admin_config codegen)
    live in factory_app/workflows/AppGenerator/tools/code_file_utils.py.
    """

    payload = _unwrap_output_envelope(payload)
    if not isinstance(payload, dict):
        return {}

    file_map = _normalize_code_file_entries(payload.get("code_files"))
    file_map.update(_materialize_app_schema_file_map(payload))
    file_map.update(_materialize_module_contract_file_map(payload))

    raw_python_files = payload.get("python_files")
    if isinstance(raw_python_files, list):
        for item in raw_python_files:
            if not isinstance(item, dict):
                continue
            safe = safe_relpath(str(item.get("path") or ""))
            content = item.get("content")
            if not safe or content is None:
                continue
            file_map[_canonical_generated_path(safe)] = str(content)

    raw_database_files = payload.get("database_files")
    if isinstance(raw_database_files, list):
        for item in raw_database_files:
            if not isinstance(item, dict):
                continue
            safe = safe_relpath(str(item.get("path") or ""))
            content = item.get("content")
            if not safe or content is None:
                continue
            file_map[_canonical_generated_path(safe)] = str(content)

    raw_model_files = payload.get("model_files")
    if isinstance(raw_model_files, list):
        for item in raw_model_files:
            if not isinstance(item, dict):
                continue
            safe = safe_relpath(str(item.get("path") or ""))
            content = item.get("content")
            if not safe or content is None:
                continue
            file_map[_canonical_generated_path(safe)] = str(content)

    raw_service_foundation_bundle = payload.get("service_foundation_bundle")
    if isinstance(raw_service_foundation_bundle, dict):
        raw_service_foundation_files = raw_service_foundation_bundle.get("files")
        if isinstance(raw_service_foundation_files, list):
            for item in raw_service_foundation_files:
                if not isinstance(item, dict):
                    continue
                safe = safe_relpath(str(item.get("path") or ""))
                content = item.get("content")
                if not safe or content is None:
                    continue
                file_map[_canonical_generated_path(safe)] = str(content)

    raw_js_files = payload.get("js_files")
    if isinstance(raw_js_files, list):
        for item in raw_js_files:
            if not isinstance(item, dict):
                continue
            safe = safe_relpath(str(item.get("path") or ""))
            content = item.get("content")
            if not safe or content is None:
                continue
            file_map[_canonical_generated_path(safe)] = str(content)

    registration_barrel = payload.get("registration_barrel")
    if registration_barrel is not None:
        safe = safe_relpath("ui/index.js")
        if safe:
            file_map[_canonical_generated_path(safe)] = str(registration_barrel)

    return file_map


def extract_code_file_entries_from_payload(payload: Any) -> list[dict[str, str]]:
    file_map = extract_code_file_map_from_payload(payload)
    return [{"filename": name, "content": content} for name, content in sorted(file_map.items())]


__all__ = [
    "extract_code_file_entries_from_payload",
    "extract_code_file_map_from_payload",
    "safe_relpath",
]
