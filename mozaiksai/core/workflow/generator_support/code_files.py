from __future__ import annotations

import json
import logging
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
from mozaiksai.core.runtime.persistence.intent_loader import (
    iter_data_contract_collections,
    validate_complete_data_contract_ownership,
)
from mozaiksai.core.semantics.closed_contract_schema import import_closed_contract_schema

from .persistence_artifacts import (
    managed_data_owners,
    materialize_data_migrations,
    normalize_data_contract_indexes,
)
from .subscription_data_contract import ensure_subscription_assignment_stores

logger = logging.getLogger(__name__)

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


def compile_data_contract(
    data_contract: dict[str, Any], *, subscription_contract: dict[str, Any] | None = None,
    context_variables: Any = None,
) -> dict[str, Any]:
    """Resolve mechanical persistence fields from approved design and subscriptions."""
    contract = normalize_data_contract_indexes(data_contract)
    managed_owners = managed_data_owners(context_variables)
    for surface in contract.get("surfaces") or []:
        if surface.get("surface_id") in managed_owners and surface.get("collections"):
            raise ValueError(f"Managed facade {surface['surface_id']!r} cannot own app collections")
    for collection in contract.get("shared_collections") or []:
        if (collection.get("ownership") or {}).get("surface_id") in managed_owners:
            raise ValueError(f"Managed facade cannot own app collection {collection.get('name')!r}")
    return ensure_subscription_assignment_stores(contract, subscription_contract)


def materialize_data_contract(
    files: dict[str, str], *, data_contract: Any, owned_paths: list[str] | None = None,
    subscription_contract: dict[str, Any] | None = None, context_variables: Any = None,
) -> dict[str, str]:
    """Compile persistence artifacts within the caller's file ownership."""
    files = materialize_data_migrations(files, managed_owners=managed_data_owners(context_variables))
    path = "data/contract.json"
    if owned_paths is not None and path not in owned_paths:
        return files
    if data_contract is None:
        assignment_contract = ensure_subscription_assignment_stores(
            {"version": "1", "surfaces": [], "shared_collections": []}, subscription_contract,
        )
        if assignment_contract.get("aliases"):
            data_contract = assignment_contract
    if data_contract is None:
        if path in files:
            raise ValueError("data/contract.json requires the approved DesignDocs data_contract")
        return files
    if not isinstance(data_contract, dict):
        raise ValueError("The approved DesignDocs data_contract must be an object")
    data_contract = compile_data_contract(
        data_contract, subscription_contract=subscription_contract, context_variables=context_variables,
    )
    validate_complete_data_contract_ownership(data_contract)
    return {**files, path: json.dumps(data_contract, indent=2, ensure_ascii=False)}


def data_contract_requires_auth(data_contract: Any) -> bool:
    """Owned collections require an authenticated app, regardless of model intent."""
    if data_contract is None:
        return False
    return any(
        collection.get("tenancy") in {"per_user", "per_workspace"}
        for _, _, collection in iter_data_contract_collections(data_contract, require_complete_ownership=False)
    )


def materialize_collection_auth(
    files: dict[str, str], *, data_contract: Any = None,
) -> dict[str, str]:
    """Apply ownership-derived auth only when materializing the app manifest."""
    if "app.json" not in files:
        return files
    if data_contract is None and "data/contract.json" in files:
        data_contract = json.loads(files["data/contract.json"])
    if not data_contract_requires_auth(data_contract):
        return files
    manifest = json.loads(files["app.json"])
    if not isinstance(manifest, dict):
        raise ValueError("app.json must be an object")
    if manifest.get("authRequired") is True:
        return files
    manifest["authRequired"] = True
    return {**files, "app.json": json.dumps(manifest, indent=2, ensure_ascii=False)}


def _materialize_schema_contract(schema: dict[str, Any], *, closed_request: bool = False) -> dict[str, Any]:
    """Compile the finite generator schema into the runtime's JSON Schema shape."""
    if "items_type" not in schema and not isinstance(schema.get("properties"), list):
        return schema
    if "required" in schema:
        raise ValueError(
            "Typed schema contracts must not declare a top-level required list; "
            "set each property's required flag instead"
        )
    result: dict[str, Any] = {"type": schema["type"]}
    if not closed_request and schema.get("description") is not None:
        result["description"] = schema["description"]
    if schema["type"] == "array" and schema.get("items_type") is not None:
        result["items"] = {"type": schema["items_type"]}
    properties = schema.get("properties") or []
    if schema["type"] != "object" and properties:
        raise ValueError("Only object schema contracts may declare properties")
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
            elif key == "policy_hooks_yaml" and isinstance(value, dict):
                for hook in value.get("hooks") or []:
                    for schema_key in ("input_schema", "output_schema"):
                        if isinstance(hook.get(schema_key), dict):
                            hook[schema_key] = _materialize_schema_contract(hook[schema_key])
            elif key == "admin_yaml" and isinstance(value, dict):
                # Admin panels share generated page sections and bind only to
                # actions in the owning module's closed contract.
                from .page_plan_utils import compile_page_data_sources, module_action_index

                compile_page_data_sources(value, module_action_index(file_map), reject_api_endpoints=True)
            file_map[str(path)] = yaml.safe_dump(
                value,
                allow_unicode=True,
                sort_keys=False,
                default_flow_style=False,
            )
        else:
            file_map[str(path)] = str(value)

    return file_map


def _is_empty_contract(content: str) -> bool:
    """Whether a raw contract file declares nothing beyond its schema_version."""
    try:
        document = yaml.safe_load(content)
    except yaml.YAMLError:
        return False
    if document in (None, [], {}):
        return True
    return isinstance(document, dict) and not any(
        value for key, value in document.items() if key != "schema_version"
    )


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
            # An absent key is treated as null. The strict structured output
            # always carries every key, so only a hand-built bundle can omit
            # one, and a raw companion file must not slip past on that alone.
            if bundle.get(key) is not None or path not in file_map:
                continue
            # Not a consistency nicety. Typed fields are materialized above -
            # action schemas compiled, event entries normalized - and a raw
            # file in code_files bypasses every bit of that. A raw file never
            # ships: the typed field is the side that wins.
            if key == "module_yaml":
                # module.yaml is the one required output, so a null field here
                # determines nothing and the raw file cannot stand in for it.
                raise ValueError(
                    f"module_contract.module_yaml is null but raw output emits {path}. "
                    "module.yaml is required and is emitted only through module_contract.module_yaml; "
                    "set it there, since a raw contract file skips schema materialization."
                )
            # A null companion field with a raw file that declares nothing is
            # determined: the module declares none, so code removes the empty
            # duplicate instead of failing the task on it. Both recorded live
            # rejections were this shape (events: [] / reactions: [] while the
            # worker followed a guard that listed the owned companion files).
            # A raw file that carries content is a contract the model meant to
            # declare: dropping it would lose a reaction, a notification rule or
            # a runtime router silently, so it is still rejected with the fix.
            if not _is_empty_contract(file_map[path]):
                raise ValueError(
                    f"module_contract.{key} is null but raw output emits {path} with content. "
                    f"Put that contract in module_contract.{key}; a raw contract file skips schema "
                    f"materialization and never ships. If the module declares none, leave "
                    f"module_contract.{key} null and omit {relative_path} from code_files."
                )
            del file_map[path]
            logger.info(
                "MODULE_CONTRACT_RAW_COMPANION_DROPPED: path=%s; module_contract.%s is null and the raw "
                "file declares nothing, so the module declares none",
                path, key,
            )

    return materialize_collection_auth(file_map, data_contract=payload.get("data_contract"))


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


_FILE_ENTRY_LANES = ("code_files", "python_files", "database_files", "model_files", "js_files")
_APP_SCHEMA_JSON_OUTPUTS = {
    "theme_config_patch": "brand/theme_config.json",
    "shell_config": "config/shell.json",
    "asset_manifest": "config/asset_manifest.json",
}


def discard_pack_owned_outputs(payload: Any, pack_paths: frozenset[str]) -> tuple[Any, list[str]]:
    """Remove every representation of a pack-owned path from a worker payload.

    Selected packs write these paths from their templates, so a worker's copy
    is never validated or assembled: file entries, typed pages, typed module
    contract fields and the other typed app-schema outputs that would
    materialize at one of these paths are dropped, and so is a request to
    delete one. Returns the payload without them and the sorted dropped paths.
    """
    unwrapped = _unwrap_output_envelope(payload)
    if not pack_paths or not isinstance(unwrapped, dict):
        return payload, []
    result = deepcopy(unwrapped)
    dropped: set[str] = set()

    def pack_owned(raw: Any) -> str | None:
        safe = safe_relpath(str(raw or ""))
        path = _canonical_generated_path(safe) if safe else None
        return path if path in pack_paths else None

    def keep_entries(entries: Any, *name_keys: str) -> Any:
        if not isinstance(entries, list):
            return entries
        kept = []
        for item in entries:
            if isinstance(item, str):
                raw: Any = item
            elif isinstance(item, dict):
                raw = next((item[key] for key in name_keys if item.get(key)), None)
            else:
                raw = None
            path = pack_owned(raw)
            if path:
                dropped.add(path)
                continue
            kept.append(item)
        return kept

    for lane in _FILE_ENTRY_LANES:
        if lane in result:
            result[lane] = keep_entries(result[lane], "filename", "path")
    foundation = result.get("service_foundation_bundle")
    if isinstance(foundation, dict) and "files" in foundation:
        foundation["files"] = keep_entries(foundation["files"], "path")
    if isinstance(result.get("pages"), list):
        kept_pages = []
        for page in result["pages"]:
            path = f"ui/pages/{_page_file_stem(page)}.yaml" if isinstance(page, dict) else None
            if path in pack_paths:
                dropped.add(path)
                continue
            kept_pages.append(page)
        result["pages"] = kept_pages
    for key, path in _APP_SCHEMA_JSON_OUTPUTS.items():
        if path in pack_paths and result.get(key) is not None:
            result[key] = None
            dropped.add(path)
    routes = result.get("custom_route_bundle")
    if isinstance(routes, dict):
        if "ui/route_manifest.json" in pack_paths and routes.get("route_manifest") is not None:
            routes["route_manifest"] = None
            dropped.add("ui/route_manifest.json")
        if "ui/index.js" in pack_paths and routes.get("ui_index") is not None:
            routes["ui_index"] = None
            dropped.add("ui/index.js")
        if "page_files" in routes:
            routes["page_files"] = keep_entries(routes["page_files"], "path")
    if "ui/index.js" in pack_paths and result.get("registration_barrel") is not None:
        result["registration_barrel"] = None
        dropped.add("ui/index.js")
    bundle = result.get("module_contract")
    if isinstance(bundle, dict) and str(bundle.get("module_id") or "").strip():
        prefix = PurePosixPath("modules", str(bundle["module_id"]).strip())
        for key, relative_path in _MODULE_CONTRACT_OUTPUT_PATHS.items():
            path = str(prefix / relative_path)
            if path in pack_paths and bundle.get(key) is not None:
                bundle[key] = None
                dropped.add(path)
    if "deleted_files" in result:
        result["deleted_files"] = keep_entries(result["deleted_files"], "filename", "path")
    if not dropped:
        return payload, []
    if unwrapped is not payload:
        key = next(iter(payload))
        return {key: result}, sorted(dropped)
    return result, sorted(dropped)


def extract_deleted_file_paths_from_payload(payload: Any) -> list[str]:
    """Resolve deleted generated file paths from a structured output payload."""
    payload = _unwrap_output_envelope(payload)
    if not isinstance(payload, dict):
        return []
    raw_deleted = payload.get("deleted_files")
    if not isinstance(raw_deleted, list):
        return []
    paths: list[str] = []
    seen: set[str] = set()
    for item in raw_deleted:
        if isinstance(item, str):
            raw_path = item
        elif isinstance(item, dict):
            raw_path = str(item.get("filename") or item.get("path") or "")
        else:
            continue
        safe = safe_relpath(raw_path)
        if not safe or safe in seen:
            continue
        seen.add(safe)
        paths.append(safe)
    return paths


__all__ = [
    "discard_pack_owned_outputs",
    "extract_code_file_entries_from_payload",
    "extract_code_file_map_from_payload",
    "extract_deleted_file_paths_from_payload",
    "safe_relpath",
]
