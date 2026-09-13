from __future__ import annotations

import json
import re
from copy import deepcopy
from pathlib import PurePosixPath
from typing import Any

import yaml
from jsonschema import Draft7Validator
from jsonschema.exceptions import SchemaError

from mozaiksai.core.runtime.app.provenance import (
    build_default_app_provenance,
    dump_app_provenance_yaml,
)
from mozaiksai.core.semantics.closed_contract_schema import import_closed_contract_schema

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

_MODULE_CONTRACT_OUTPUT_PATHS = {
    "module_yaml": "module.yaml",
    "events_yaml": "contracts/events.yaml",
    "reactions_yaml": "contracts/reactions.yaml",
    "notifications_yaml": "contracts/notifications.yaml",
    "policy_hooks_yaml": "contracts/policy_hooks.yaml",
    "settings_yaml": "contracts/settings.yaml",
    "admin_yaml": "contracts/admin.yaml",
    "profile_yaml": "contracts/profile.yaml",
    "relationships_yaml": "contracts/relationships.yaml",
    "runtime_extensions_yaml": "runtime_extensions.yaml",
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


def _materialize_app_schema_file_map(
    payload: dict[str, Any],
    *,
    build_timestamp: str | None = None,
) -> dict[str, str]:
    manifest = payload.get("manifest")
    pages = payload.get("pages")
    if "manifest" not in payload or not isinstance(pages, list):
        return {}
    if manifest is not None and not isinstance(manifest, dict):
        raise ValueError("AppSchemaOutput.manifest must be an object or null")

    file_map: dict[str, str] = {}
    # Scoped page workers leave app identity and provenance to their existing owner.
    if manifest is not None:
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
                timestamp=build_timestamp,
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


def _materialize_schema_contract(schema: dict[str, Any], *, closed_request: bool = False) -> dict[str, Any]:
    """Compile the finite generator schema into the runtime's JSON Schema shape."""
    if "items_type" not in schema and not isinstance(schema.get("properties"), list):
        return schema
    result: dict[str, Any] = {"type": schema["type"]}
    if not closed_request and schema.get("description") is not None:
        result["description"] = schema["description"]
    if schema["type"] == "array" and schema.get("items_type") is not None:
        result["items"] = {"type": schema["items_type"]}
    properties = schema.get("properties") or []
    required = schema.get("required")
    if schema["type"] != "object" and (properties or required):
        raise ValueError("Only object schema contracts may declare properties or required fields")
    if schema["type"] == "object":
        result["properties"] = {}
        if closed_request:
            result["additionalProperties"] = False
        required_names: list[str] = []
        for prop in properties:
            name = prop["name"]
            if not name or name in result["properties"]:
                raise ValueError("Schema contract property names must be nonempty and unique")
            rendered = {"type": prop["type"]}
            if not closed_request and prop.get("description") is not None:
                rendered["description"] = prop["description"]
            if prop.get("enum_values"):
                rendered["enum"] = prop["enum_values"]
            if prop["type"] == "array" and prop.get("items_type") is not None:
                rendered["items"] = {"type": prop["items_type"]}
            result["properties"][name] = rendered
            if prop.get("required") is True:
                required_names.append(name)
        if required is not None and (
            len(required) != len(set(required)) or set(required) != set(required_names)
        ):
            raise ValueError("Schema contract required names must match property required flags")
        if required_names:
            result["required"] = required_names
    try:
        Draft7Validator.check_schema(result)
    except SchemaError as exc:
        raise ValueError(f"Invalid generated JSON Schema: {exc.message}") from exc
    if closed_request:
        import_closed_contract_schema(result)
    return result


def _materialize_module_contract_file_map(payload: dict[str, Any]) -> dict[str, str]:
    bundle = payload.get("module_contract")
    if not isinstance(bundle, dict):
        return {}
    module_id = str(bundle.get("module_id") or "").strip()
    if not module_id:
        return {}

    prefix = PurePosixPath("modules", module_id)
    file_map: dict[str, str] = {}
    for key, relative_path in _MODULE_CONTRACT_OUTPUT_PATHS.items():
        path = prefix / relative_path
        value = bundle.get(key)
        if value is None:
            continue
        if isinstance(value, (dict, list)):
            value = deepcopy(value)
            if key == "module_yaml" and isinstance(value, dict):
                for action in value.get("actions") or []:
                    for schema_key in ("input_schema", "output_schema"):
                        if isinstance(action.get(schema_key), dict):
                            action[schema_key] = _materialize_schema_contract(
                                action[schema_key], closed_request=schema_key == "input_schema",
                            )
                for capability in value.get("capabilities") or []:
                    if isinstance(capability.get("input_schema"), dict):
                        capability["input_schema"] = _materialize_schema_contract(
                            capability["input_schema"], closed_request=True,
                        )
            elif key == "events_yaml" and isinstance(value, dict):
                for event in value.get("events") or []:
                    if isinstance(event.get("payload_schema"), dict):
                        event["payload_schema"] = _materialize_schema_contract(event["payload_schema"])
            file_map[str(path)] = yaml.safe_dump(
                value,
                allow_unicode=True,
                sort_keys=False,
                default_flow_style=False,
            )
        else:
            file_map[str(path)] = str(value)

    return file_map


def _normalize_code_file_entries(raw_entries: Any) -> dict[str, str]:
    file_map: dict[str, str] = {}
    if not isinstance(raw_entries, list):
        return file_map

    for item in raw_entries:
        if not isinstance(item, dict):
            continue
        filename = item.get("filename") or item.get("path")
        content = item.get("content")
        if content is None:
            content = item.get("filecontent")
        if not filename or content is None:
            continue
        safe = safe_relpath(str(filename))
        if not safe:
            continue
        file_map[_canonical_generated_path(safe)] = str(content)
    return file_map


def extract_code_file_map_from_payload(
    payload: Any,
    *,
    build_timestamp: str | None = None,
) -> dict[str, str]:
    """Resolve deterministic code files from a structured agent payload.

    Handles the generic file lanes used across all generator workflows.
    AppGenerator-specific expansions (e.g. app_backend_admin_config codegen)
    live in factory_app/workflows/AppGenerator/tools/code_file_utils.py.
    """

    payload = _unwrap_output_envelope(payload)
    if not isinstance(payload, dict):
        return {}

    file_map = _normalize_code_file_entries(payload.get("code_files"))
    file_map.update(
        _materialize_app_schema_file_map(
            payload,
            build_timestamp=build_timestamp,
        )
    )
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

    bundle = payload.get("module_contract")
    if isinstance(bundle, dict) and bundle.get("module_id"):
        prefix = PurePosixPath("modules", str(bundle["module_id"]).strip())
        for key, relative_path in _MODULE_CONTRACT_OUTPUT_PATHS.items():
            path = str(prefix / relative_path)
            if key in bundle and bundle[key] is None and path in file_map:
                raise ValueError(f"module_contract.{key} is null but raw output emits {path}")

    return file_map


def extract_code_file_entries_from_payload(
    payload: Any,
    *,
    build_timestamp: str | None = None,
) -> list[dict[str, str]]:
    file_map = extract_code_file_map_from_payload(
        payload,
        build_timestamp=build_timestamp,
    )
    return [{"filename": name, "content": content} for name, content in sorted(file_map.items())]


__all__ = [
    "extract_code_file_entries_from_payload",
    "extract_code_file_map_from_payload",
    "safe_relpath",
]
