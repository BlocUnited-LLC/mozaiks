import json
import logging
from collections.abc import Iterable
from pathlib import PurePosixPath
from typing import Annotated, Any

from pydantic import Field

from mozaiksai.core.runtime.app.paths import (
    APP_SECURITY_SECRETS_PATH,
    disallowed_legacy_app_paths,
    noncanonical_app_config_paths,
    noncanonical_app_root_paths,
    normalize_app_path,
)

_logger = logging.getLogger("tools.app_build_plan")

_CARRY_FORWARD_DECISION_VALUES: frozenset[str] = frozenset({"reuse", "adapt", "regenerate", "drop"})
_CARRY_FORWARD_SOURCE_VALUES: frozenset[str] = frozenset({"carry_forward_candidate", "human_override", "planner"})

_RAW_FRONTEND_SOURCE_EXTENSIONS = {".css", ".html", ".jsx", ".less", ".sass", ".scss", ".tsx"}
_FRONTEND_JS_TS_SEGMENTS = (
    "/frontend/",
    "/chat-ui/",
    "/chatui/",
    "/src/pages/",
    "/src/components/",
)
_OBSOLETE_HOST_ADMIN_CONFIG_PATH = "app/config/admin.json"
_APP_SERVICE_ADMIN_PATHS = {"services/admin_config.py", "services/routes/admin.py"}
_INTEGRATIONS_PREFIX = "services/integrations/"
_ADAPTERS_PREFIX = "services/adapters/"
_ROUTES_PREFIX = "services/routes/"
_CLIENT_SUFFIX = "_client.py"
_CANONICAL_SERVICE_FILES = frozenset({"services/config.py", "services/admin_config.py"})
_CANONICAL_SERVICE_PREFIXES = (_INTEGRATIONS_PREFIX, _ADAPTERS_PREFIX, _ROUTES_PREFIX)
_SERVICE_FOUNDATION_ALLOWED_FILES = frozenset({"services/config.py", APP_SECURITY_SECRETS_PATH})
_SERVICE_FOUNDATION_ALLOWED_PREFIXES = (_INTEGRATIONS_PREFIX, _ADAPTERS_PREFIX, _ROUTES_PREFIX)
_DEPLOYMENT_CONTRACT_ARTIFACT_FILES = frozenset(
    {
        "Dockerfile",
        "deployment.manifest.json",
        "docker-compose.yml",
        "env.example",
    }
)
_DEPLOYMENT_CONTRACT_ARTIFACT_PREFIXES = (".github/workflows/",)
_ALLOWED_TASK_TYPES = {
    "subscription_config",
    "service_foundation",
    "module_contract",
    "persistence_contract",
    "data_models",
    "business_services",
    "api_surface",
    "page_bundle",
    "agent_backend_integration",
    "control_plane_pack",
}
_CANONICAL_INITIAL_AGENTS = {
    "subscription_config": "ConfigMiddlewareAgent",
    "service_foundation": "ConfigMiddlewareAgent",
    "module_contract": "ConfigMiddlewareAgent",
    "persistence_contract": "DatabaseAgent",
    "data_models": "ModelAgent",
    "business_services": "ServiceAgent",
    "control_plane_pack": "ControlPlaneAgent",
    "api_surface": "ControllerAgent",
    "page_bundle": "AppSchemaAgent",
}
_SURFACE_KIND_ALLOWED_TASK_TYPES: dict[str, frozenset[str]] = {
    "external_integration": frozenset({"api_surface", "service_foundation", "agent_backend_integration"}),
    "control_plane": frozenset({"control_plane_pack", "subscription_config"}),
    "ui_only": frozenset({"page_bundle"}),
}
_SHARED_OWNED_PATHS = frozenset({"app.json"})
_WORKFLOW_SURFACE_KIND = "workflow"
_MODULE_LOCAL_TASK_TYPES = frozenset({"module_contract", "data_models", "business_services"})


def _normalize_string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    items: list[str] = []
    for entry in value:
        text = str(entry).strip()
        if text:
            items.append(text)
    return items


def _normalize_object_list(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    items: list[dict[str, Any]] = []
    for entry in value:
        if isinstance(entry, dict):
            items.append(dict(entry))
    return items


def _normalize_context_variables(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    if isinstance(value, dict):
        return dict(value)
    if not isinstance(value, list):
        return None
    normalized: dict[str, Any] = {}
    for item in value:
        if not isinstance(item, dict):
            continue
        key = str(item.get("key") or "").strip()
        if not key:
            continue
        raw_value = item.get("value")
        value_type = str(item.get("value_type") or "string").strip()
        if value_type == "boolean":
            normalized[key] = str(raw_value).strip().lower() in {"1", "true", "yes", "on"}
        elif value_type == "integer":
            try:
                normalized[key] = int(str(raw_value).strip())
            except ValueError:
                normalized[key] = 0
        elif value_type == "number":
            try:
                normalized[key] = float(str(raw_value).strip())
            except ValueError:
                normalized[key] = 0.0
        elif value_type == "json":
            try:
                normalized[key] = json.loads(str(raw_value or "null"))
            except json.JSONDecodeError:
                normalized[key] = raw_value
        elif value_type == "null":
            normalized[key] = None
        else:
            normalized[key] = "" if raw_value is None else str(raw_value)
    return normalized


def _unwrap_app_build_plan_payload(payload: dict[str, Any]) -> dict[str, Any]:
    if "app_kind" in payload:
        return payload
    for key in ("AppBuildPlan", "app_build_plan"):
        value = payload.get(key)
        if isinstance(value, dict):
            unwrapped = _unwrap_app_build_plan_payload(value)
            if "app_kind" in unwrapped:
                return unwrapped
    if len(payload) == 1:
        value = next(iter(payload.values()))
        if isinstance(value, dict):
            unwrapped = _unwrap_app_build_plan_payload(value)
            if "app_kind" in unwrapped:
                return unwrapped
    return payload


def _task_sort_key(task: dict[str, Any]) -> tuple[int, str]:
    task_id = str(task.get("task_id") or "")
    return (0 if task_id else 1, task_id)


def _infer_pack_id_from_integration_path(path: str) -> str | None:
    """
    Extract the managed capability id from an app-bundle-relative
    services/integrations/{pack_id}_client.py path.

    Returns the inferred pack_id string, or None if the path does not match
    the managed capability adapter pattern.
    """
    normalized = path.replace("\\", "/").strip()
    if not normalized.startswith(_INTEGRATIONS_PREFIX):
        return None
    filename = PurePosixPath(normalized).name
    if filename.endswith(_CLIENT_SUFFIX):
        return filename[: -len(_CLIENT_SUFFIX)]
    return None


def _raw_frontend_source_path(task: dict[str, Any]) -> str | None:
    for owned_path in _normalize_string_list(task.get("owned_paths")):
        normalized = owned_path.replace("\\", "/").strip().lower()
        suffix = PurePosixPath(normalized).suffix

        if suffix in _RAW_FRONTEND_SOURCE_EXTENSIONS:
            return owned_path

        if suffix in {".js", ".ts"} and any(segment in normalized for segment in _FRONTEND_JS_TS_SEGMENTS):
            return owned_path

    return None


def _normalized_owned_paths(task: dict[str, Any]) -> list[str]:
    paths: list[str] = []
    for owned_path in _normalize_string_list(task.get("owned_paths")):
        normalized = normalize_app_path(owned_path)
        if normalized:
            paths.append(normalized)
    return paths


def _noncanonical_service_paths(paths: list[str]) -> list[str]:
    invalid: list[str] = []
    for path in paths:
        normalized = normalize_app_path(path)
        if not normalized.startswith("services/"):
            continue
        if normalized in _CANONICAL_SERVICE_FILES:
            continue
        if any(normalized.startswith(prefix) for prefix in _CANONICAL_SERVICE_PREFIXES):
            continue
        invalid.append(normalized)
    return sorted(set(invalid))


def _invalid_service_foundation_paths(paths: list[str]) -> list[str]:
    invalid: list[str] = []
    for path in paths:
        normalized = normalize_app_path(path)
        if normalized in _SERVICE_FOUNDATION_ALLOWED_FILES:
            continue
        if any(normalized.startswith(prefix) for prefix in _SERVICE_FOUNDATION_ALLOWED_PREFIXES):
            continue
        invalid.append(normalized)
    return sorted(set(invalid))


def _deployment_contract_artifact_paths(paths: list[str]) -> list[str]:
    invalid: list[str] = []
    for path in paths:
        normalized = normalize_app_path(path)
        if normalized in _DEPLOYMENT_CONTRACT_ARTIFACT_FILES:
            invalid.append(normalized)
            continue
        if any(normalized.startswith(prefix) for prefix in _DEPLOYMENT_CONTRACT_ARTIFACT_PREFIXES):
            invalid.append(normalized)
    return sorted(set(invalid))


def _infer_module_id_from_owned_paths(task: dict[str, Any]) -> str | None:
    module_ids: set[str] = set()
    for path in _normalized_owned_paths(task):
        parts = PurePosixPath(path).parts
        if len(parts) >= 2 and parts[0] == "modules" and parts[1]:
            module_ids.add(parts[1])
    if len(module_ids) == 1:
        return next(iter(module_ids))
    return None


def _normalize_build_task_identity(task: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(task)
    task_type = str(normalized.get("task_type") or "").strip()
    if task_type in _MODULE_LOCAL_TASK_TYPES:
        module_id = _infer_module_id_from_owned_paths(normalized)
        if module_id:
            declared_id = str(normalized.get("capability_pack_id") or "").strip()
            if declared_id != module_id:
                _logger.info(
                    "Normalized build task %s capability_pack_id from %r to %r based on owned_paths",
                    normalized.get("task_id") or "<unknown>",
                    declared_id or None,
                    module_id,
                )
                normalized["capability_pack_id"] = module_id
    return normalized


def _dedupe_preserving_order(values: Iterable[Any]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value or "").strip()
        if not text or text in seen:
            continue
        result.append(text)
        seen.add(text)
    return result


def _join_unique_text(parts: Iterable[Any], *, separator: str = " ") -> str:
    return separator.join(_dedupe_preserving_order(parts)).strip()


def _merge_persistence_contract_tasks(build_tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    persistence_tasks = [
        task
        for task in build_tasks
        if isinstance(task, dict) and str(task.get("task_type") or "").strip() == "persistence_contract"
    ]
    if len(persistence_tasks) <= 1:
        return build_tasks

    primary = dict(sorted(persistence_tasks, key=_task_sort_key)[0])
    primary_id = str(primary.get("task_id") or "task_persistence_contract").strip()
    removed_ids = {
        str(task.get("task_id") or "").strip()
        for task in persistence_tasks
        if str(task.get("task_id") or "").strip() and str(task.get("task_id") or "").strip() != primary_id
    }

    primary["task_id"] = primary_id
    primary["description"] = _join_unique_text(
        task.get("description") for task in persistence_tasks
    ) or str(primary.get("description") or "")
    primary["initial_message"] = _join_unique_text(
        task.get("initial_message") for task in persistence_tasks
    ) or str(primary.get("initial_message") or "")
    primary["owned_paths"] = _dedupe_preserving_order(
        path for task in persistence_tasks for path in _normalized_owned_paths(task)
    )
    primary["depends_on"] = _dedupe_preserving_order(
        dep
        for task in persistence_tasks
        for dep in _normalize_string_list(task.get("depends_on"))
        if str(dep or "").strip() != primary_id and str(dep or "").strip() not in removed_ids
    )
    primary["acceptance_criteria"] = _dedupe_preserving_order(
        criterion
        for task in persistence_tasks
        for criterion in _normalize_string_list(task.get("acceptance_criteria"))
    )

    result: list[dict[str, Any]] = []
    inserted_primary = False
    for task in build_tasks:
        if not isinstance(task, dict):
            continue
        task_type = str(task.get("task_type") or "").strip()
        str(task.get("task_id") or "").strip()
        if task_type == "persistence_contract":
            if not inserted_primary:
                result.append(primary)
                inserted_primary = True
            continue

        item = dict(task)
        deps = []
        for dep in _normalize_string_list(item.get("depends_on")):
            deps.append(primary_id if dep in removed_ids else dep)
        item["depends_on"] = _dedupe_preserving_order(deps)
        result.append(item)

    if not inserted_primary:
        result.insert(0, primary)
    return sorted(result, key=_task_sort_key)


def _context_get(context_variables: Any | None, key: str, default: Any = None) -> Any:
    if context_variables is None or not hasattr(context_variables, "get"):
        return default
    try:
        return context_variables.get(key, default)
    except Exception:
        return default


def _context_available_pack_map(context_variables: Any | None) -> dict[str, dict[str, Any]]:
    raw_groups = [
        _context_get(context_variables, "capability_packs", []),
        _context_get(context_variables, "available_managed_capabilities", []),
    ]
    result: dict[str, dict[str, Any]] = {}
    for raw_packs in raw_groups:
        if not isinstance(raw_packs, list):
            continue
        for pack in raw_packs:
            if not isinstance(pack, dict):
                continue
            pack_id = str(pack.get("id") or pack.get("pack_id") or pack.get("capability_pack_id") or "").strip()
            if pack_id:
                result[pack_id] = {**result.get(pack_id, {}), **dict(pack)}
    return result


def _context_managed_capability_ids(context_variables: Any | None) -> frozenset[str]:
    if context_variables is None or not hasattr(context_variables, "get"):
        return frozenset()
    pack_ids: set[str] = set()
    for key in ("capability_packs", "available_managed_capabilities"):
        raw_packs = _context_get(context_variables, key, [])
        if not isinstance(raw_packs, list):
            continue
        for pack in raw_packs:
            if not isinstance(pack, dict):
                continue
            pack_id = str(pack.get("id") or pack.get("pack_id") or pack.get("capability_pack_id") or "").strip()
            if pack_id and str(pack.get("capability_source") or "").strip() == "managed_capability":
                pack_ids.add(pack_id)
    return frozenset(pack_ids)


def _pack_required_integrations(descriptor: dict[str, Any] | None) -> list[Any]:
    if not isinstance(descriptor, dict):
        return []
    raw = descriptor.get("required_integrations")
    if isinstance(raw, list):
        return list(raw)
    pack = descriptor.get("pack")
    if isinstance(pack, dict):
        raw = pack.get("required_integrations")
        if isinstance(raw, list):
            return list(raw)
    return []


def _merge_available_pack_defaults(
    item: dict[str, Any],
    available: dict[str, Any] | None,
) -> dict[str, Any]:
    if not isinstance(available, dict) or not available:
        return item
    merged = dict(item)
    for key in ("capabilities", "facades", "operator_contracts"):
        if not merged.get(key) and isinstance(available.get(key), list):
            merged[key] = list(available[key])

    if not merged.get("required_integrations"):
        required_integrations = _pack_required_integrations(available)
        if required_integrations:
            merged["required_integrations"] = required_integrations

    for key in ("pack_source_path", "context_id"):
        if not merged.get(key) and available.get(key):
            merged[key] = available[key]
    return merged


def _normalize_capability_pack_sources(
    capability_packs: list[dict[str, Any]],
    *,
    context_variables: Any | None,
) -> list[dict[str, Any]]:
    available_packs = _context_available_pack_map(context_variables)
    context_managed_capability_ids = _context_managed_capability_ids(context_variables)
    normalized: list[dict[str, Any]] = []
    for pack in capability_packs:
        item = dict(pack)
        pack_id = str(item.get("capability_pack_id") or item.get("id") or item.get("pack_id") or "").strip()
        source = str(item.get("capability_source") or "").strip()
        pack_type = str(item.get("pack_type") or "").strip()
        available = available_packs.get(pack_id) if pack_id else None
        available_source = str((available or {}).get("capability_source") or "").strip()
        item = _merge_available_pack_defaults(item, available)
        if not source and (pack_type == "managed_capability" or available_source == "managed_capability" or pack_id in context_managed_capability_ids):
            item["capability_source"] = "managed_capability"
        if item.get("capability_source") == "managed_capability":
            item.setdefault("implementation_mode", "external_integration")
            item.setdefault("surface_kind", "external_integration")
        normalized.append(item)
    return normalized


def _infer_managed_capability_ids_from_adapter_tasks(
    build_tasks: list[dict[str, Any]],
    *,
    context_variables: Any | None,
) -> frozenset[str]:
    available_packs = _context_available_pack_map(context_variables)
    inferred: set[str] = set()
    for task in build_tasks:
        if not isinstance(task, dict):
            continue
        if str(task.get("task_type") or "").strip() != "api_surface":
            continue
        if str(task.get("surface_kind") or "").strip() != "external_integration":
            continue
        for owned_path in _normalized_owned_paths(task):
            pack_id = _infer_pack_id_from_integration_path(owned_path)
            if not pack_id:
                continue
            available = available_packs.get(pack_id) or {}
            if available.get("capability_source") == "managed_capability":
                inferred.add(pack_id)
    return frozenset(inferred)


def _ensure_managed_capability_entries(
    capability_packs: list[dict[str, Any]],
    *,
    managed_capability_ids: frozenset[str],
    context_variables: Any | None,
) -> list[dict[str, Any]]:
    if not managed_capability_ids:
        return capability_packs
    available_packs = _context_available_pack_map(context_variables)
    existing = {
        str(pack.get("capability_pack_id") or pack.get("id") or pack.get("pack_id") or "").strip()
        for pack in capability_packs
        if isinstance(pack, dict)
    }
    result = [dict(pack) for pack in capability_packs]
    for pack_id in sorted(managed_capability_ids - existing):
        available = available_packs.get(pack_id) or {}
        result.append(
            {
                "capability_pack_id": pack_id,
                "surface_id": f"{pack_id}_managed",
                "surface_kind": "external_integration",
                "pack_type": "managed_capability",
                "label": available.get("display_name") or available.get("label") or pack_id,
                "summary": available.get("description") or f"Managed {pack_id} capability.",
                "implementation_mode": "external_integration",
                "primary_entities": [],
                "primary_pages": [],
                "operations": [
                    str(capability.get("capability_id") or "").strip()
                    for capability in available.get("capabilities") or []
                    if isinstance(capability, dict) and str(capability.get("capability_id") or "").strip()
                ],
                "required_integrations": _pack_required_integrations(available),
                "agentic_extensions": [],
                "capability_source": "managed_capability",
            }
        )
    return result


def _pack_id_from_descriptor(pack: dict[str, Any]) -> str:
    return str(pack.get("capability_pack_id") or pack.get("id") or pack.get("pack_id") or "").strip()


def _ensure_context_selected_capability_packs(
    capability_packs: list[dict[str, Any]],
    *,
    context_variables: Any | None,
) -> list[dict[str, Any]]:
    """Include packs projected by selected build_context even if the LLM omitted them."""
    available_packs = _context_available_pack_map(context_variables)
    if not available_packs:
        return capability_packs

    existing = {
        _pack_id_from_descriptor(pack)
        for pack in capability_packs
        if isinstance(pack, dict)
    } - {""}
    result = [dict(pack) for pack in capability_packs]
    for pack_id, descriptor in sorted(available_packs.items()):
        if pack_id in existing:
            continue
        if str(descriptor.get("capability_source") or "").strip() != "managed_capability":
            continue
        item = dict(descriptor)
        item.setdefault("capability_pack_id", pack_id)
        item.setdefault("surface_id", str(item.get("surface_id") or pack_id))
        item.setdefault("surface_kind", str(item.get("surface_kind") or "external_integration"))
        item.setdefault("implementation_mode", str(item.get("implementation_mode") or "external_integration"))
        item.setdefault("pack_type", str(item.get("pack_type") or "managed_capability"))
        item.setdefault("label", item.get("display_name") or pack_id)
        item.setdefault("summary", item.get("description") or f"Managed {pack_id} capability.")
        result.append(item)
        existing.add(pack_id)
    return result


def _pack_facades(descriptor: dict[str, Any]) -> list[dict[str, Any]]:
    facades = _normalize_object_list(descriptor.get("facades"))
    if facades:
        return facades
    pack_section = descriptor.get("pack")
    if isinstance(pack_section, dict):
        return _normalize_object_list(pack_section.get("facades"))
    return []


def _facade_pack_descriptor(facade: dict[str, Any]) -> dict[str, Any] | None:
    facade_id = str(facade.get("module_id") or facade.get("facade_id") or "").strip()
    if not facade_id:
        return None
    pages = _normalize_object_list(facade.get("pages"))
    operations = _dedupe_preserving_order(
        action
        for page in pages
        for action in _normalize_string_list(page.get("primary_actions"))
    )
    return {
        "capability_pack_id": facade_id,
        "surface_id": facade_id,
        "surface_kind": "module",
        "pack_type": "managed_facade",
        "label": facade.get("label") or facade_id.replace("_", " ").title(),
        "summary": facade.get("summary") or f"App-owned facade for {facade_id}.",
        "implementation_mode": "declarative_module",
        "capability_source": "generated_module",
        "primary_entities": _dedupe_preserving_order(
            entity
            for page in pages
            for entity in _normalize_string_list(page.get("primary_entities"))
        ),
        "primary_pages": [
            str(page.get("name") or page.get("page_id") or "").strip().lower().replace(" ", "_")
            for page in pages
            if str(page.get("name") or page.get("page_id") or "").strip()
        ],
        "operations": operations,
        "required_integrations": _dedupe_preserving_order([facade.get("provider_module")]),
    }


def _apply_selected_pack_files(
    *,
    capability_packs: list[dict[str, Any]],
    pages: list[dict[str, Any]],
    build_tasks: list[dict[str, Any]],
    context_variables: Any | None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Apply deterministic facade pages from selected packs."""
    available_packs = _context_available_pack_map(context_variables)
    if not available_packs:
        return capability_packs, pages, build_tasks

    selected_managed_capability_ids = {
        _pack_id_from_descriptor(pack)
        for pack in capability_packs
        if isinstance(pack, dict) and str(pack.get("capability_source") or "").strip() == "managed_capability"
    } - {""}
    if not selected_managed_capability_ids:
        return capability_packs, pages, build_tasks

    result_packs = [dict(pack) for pack in capability_packs]
    existing_pack_ids = {
        _pack_id_from_descriptor(pack)
        for pack in result_packs
        if isinstance(pack, dict)
    } - {""}

    result_pages = [dict(page) for page in pages]
    existing_page_routes = {
        str(page.get("route") or "").strip()
        for page in result_pages
        if isinstance(page, dict)
    } - {""}
    existing_page_names = {
        str(page.get("name") or page.get("page_id") or "").strip().lower()
        for page in result_pages
        if isinstance(page, dict)
    } - {""}

    result_tasks = [dict(task) for task in build_tasks]

    for pack_id in sorted(selected_managed_capability_ids):
        descriptor = available_packs.get(pack_id) or {}
        facades = _pack_facades(descriptor)
        for facade in facades:
            facade_pack = _facade_pack_descriptor(facade)
            if facade_pack:
                facade_pack_id = _pack_id_from_descriptor(facade_pack)
                if facade_pack_id not in existing_pack_ids:
                    result_packs.append(facade_pack)
                    existing_pack_ids.add(facade_pack_id)
                else:
                    for index, existing_pack in enumerate(result_packs):
                        if _pack_id_from_descriptor(existing_pack) == facade_pack_id:
                            result_packs[index] = {**existing_pack, **facade_pack}
                            break

            for page in _normalize_object_list(facade.get("pages")):
                route = str(page.get("route") or "").strip()
                name = str(page.get("name") or page.get("page_id") or "").strip().lower()
                if (route and route in existing_page_routes) or (name and name in existing_page_names):
                    continue
                result_pages.append(page)
                if route:
                    existing_page_routes.add(route)
                if name:
                    existing_page_names.add(name)

    return result_packs, result_pages, sorted(result_tasks, key=_task_sort_key)


def _selected_managed_capability_descriptors(
    capability_packs: list[dict[str, Any]],
    *,
    context_variables: Any | None,
) -> dict[str, dict[str, Any]]:
    available_packs = _context_available_pack_map(context_variables)
    descriptors: dict[str, dict[str, Any]] = {}
    for pack in capability_packs:
        if not isinstance(pack, dict) or str(pack.get("capability_source") or "").strip() != "managed_capability":
            continue
        pack_id = _pack_id_from_descriptor(pack)
        if not pack_id:
            continue
        descriptor = dict(available_packs.get(pack_id) or pack)
        descriptors[pack_id] = descriptor
    return descriptors


def _managed_facade_route_rules(
    capability_packs: list[dict[str, Any]],
    *,
    context_variables: Any | None,
) -> dict[tuple[str, str], str]:
    rules: dict[tuple[str, str], str] = {}
    for pack_id, descriptor in _selected_managed_capability_descriptors(
        capability_packs,
        context_variables=context_variables,
    ).items():
        for facade in _pack_facades(descriptor):
            provider_module = str(facade.get("provider_module") or pack_id).strip()
            facade_module = str(facade.get("module_id") or facade.get("facade_id") or "").strip()
            if not provider_module or not facade_module:
                continue
            for action in facade.get("provider_actions") or []:
                action_id = str(action or "").strip()
                if action_id:
                    rules[(provider_module, action_id)] = facade_module
    return rules


def _route_page_api_endpoint_to_facade(endpoint: str, rules: dict[tuple[str, str], str]) -> str:
    normalized = endpoint.replace("\\", "/").strip()
    prefix = "/api/modules/"
    if not normalized.startswith(prefix):
        return endpoint
    remainder = normalized[len(prefix):]
    parts = remainder.split("/", 2)
    if len(parts) < 2:
        return endpoint
    module_id, action_id = parts[0], parts[1]
    target_module = rules.get((module_id, action_id))
    if not target_module:
        return endpoint
    suffix = f"/{parts[2]}" if len(parts) > 2 and parts[2] else ""
    return f"{prefix}{target_module}/{action_id}{suffix}"


def _route_page_api_endpoints_to_facades(value: Any, rules: dict[tuple[str, str], str]) -> Any:
    if isinstance(value, dict):
        rewritten: dict[str, Any] = {}
        for key, nested in value.items():
            if key == "api_endpoint" and isinstance(nested, str):
                rewritten[key] = _route_page_api_endpoint_to_facade(nested, rules)
            else:
                rewritten[key] = _route_page_api_endpoints_to_facades(nested, rules)
        return rewritten
    if isinstance(value, list):
        return [_route_page_api_endpoints_to_facades(item, rules) for item in value]
    return value


def _normalize_managed_capability_page_bindings(
    pages: list[dict[str, Any]],
    *,
    capability_packs: list[dict[str, Any]],
    context_variables: Any | None,
) -> list[dict[str, Any]]:
    rules = _managed_facade_route_rules(
        capability_packs,
        context_variables=context_variables,
    )
    if not rules:
        return pages
    return [
        _route_page_api_endpoints_to_facades(dict(page), rules)
        for page in pages
        if isinstance(page, dict)
    ]


def _normalize_managed_capability_adapter_task_pack_ids(
    build_tasks: list[dict[str, Any]],
    *,
    managed_capability_ids: frozenset[str],
) -> list[dict[str, Any]]:
    if not managed_capability_ids:
        return build_tasks
    normalized: list[dict[str, Any]] = []
    for task in build_tasks:
        item = dict(task)
        if (
            str(item.get("task_type") or "").strip() == "api_surface"
            and str(item.get("surface_kind") or "").strip() == "external_integration"
        ):
            for owned_path in _normalized_owned_paths(item):
                inferred_pack_id = _infer_pack_id_from_integration_path(owned_path)
                if inferred_pack_id and inferred_pack_id in managed_capability_ids:
                    declared_id = str(item.get("capability_pack_id") or "").strip()
                    if declared_id != inferred_pack_id:
                        _logger.info(
                            "Normalized managed capability adapter task %s capability_pack_id from %r to %r based on %s",
                            item.get("task_id") or "<unknown>",
                            declared_id or None,
                            inferred_pack_id,
                            owned_path,
                        )
                        item["capability_pack_id"] = inferred_pack_id
                    break
        normalized.append(item)
    return normalized


def _normalize_page_task_dependencies(build_tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Ensure page tasks explicitly depend on their app-owned module contract."""
    module_contract_by_pack: dict[str, str] = {}
    module_contract_task_ids: list[str] = []
    service_task_by_pack: dict[str, str] = {}
    for task in build_tasks:
        if not isinstance(task, dict):
            continue
        task_type = str(task.get("task_type") or "").strip()
        pack_id = str(task.get("capability_pack_id") or "").strip()
        task_id = str(task.get("task_id") or "").strip()
        if task_type == "module_contract" and pack_id and task_id:
            module_contract_by_pack.setdefault(pack_id, task_id)
        if task_type == "module_contract" and task_id:
            module_contract_task_ids.append(task_id)
        if task_type == "business_services" and pack_id and task_id:
            service_task_by_pack.setdefault(pack_id, task_id)

    normalized: list[dict[str, Any]] = []
    for task in build_tasks:
        if not isinstance(task, dict):
            continue
        item = dict(task)
        if str(item.get("task_type") or "").strip() == "page_bundle":
            pack_id = str(item.get("capability_pack_id") or "").strip()
            facade_task_id = module_contract_by_pack.get(pack_id)
            if not facade_task_id:
                deps = set(_normalize_string_list(item.get("depends_on")))
                candidate_packs = [
                    candidate_pack
                    for candidate_pack, service_task_id in service_task_by_pack.items()
                    if service_task_id in deps
                ]
                if len(candidate_packs) == 1:
                    facade_task_id = module_contract_by_pack.get(candidate_packs[0])
            if not facade_task_id and len(module_contract_task_ids) == 1:
                facade_task_id = module_contract_task_ids[0]
            if facade_task_id:
                deps = _normalize_string_list(item.get("depends_on"))  # type: ignore[assignment]
                if facade_task_id not in deps:
                    deps.insert(0, facade_task_id)  # type: ignore[attr-defined]
                item["depends_on"] = deps
        normalized.append(item)
    return normalized


def _normalize_facade_task_dependencies(
    build_tasks: list[dict[str, Any]],
    *,
    managed_capability_ids: frozenset[str],
) -> list[dict[str, Any]]:
    """Ensure generated facades depend on the managed capability adapter task they call."""
    adapter_task_by_pack: dict[str, str] = {}
    for task in build_tasks:
        if not isinstance(task, dict):
            continue
        if str(task.get("task_type") or "").strip() != "api_surface":
            continue
        pack_id = str(task.get("capability_pack_id") or "").strip()
        task_id = str(task.get("task_id") or "").strip()
        if task_id and (pack_id in managed_capability_ids or str(task.get("surface_kind") or "").strip() == "external_integration"):
            if pack_id:
                adapter_task_by_pack.setdefault(pack_id, task_id)

    if not adapter_task_by_pack:
        return build_tasks

    normalized: list[dict[str, Any]] = []
    for task in build_tasks:
        if not isinstance(task, dict):
            continue
        item = dict(task)
        if str(item.get("task_type") or "").strip() == "module_contract":
            pack_id = str(item.get("capability_pack_id") or "").strip()
            if pack_id and pack_id not in managed_capability_ids:
                adapter_task_id = _facade_adapter_dependency(item, adapter_task_by_pack)
                deps = _normalize_string_list(item.get("depends_on"))
                if adapter_task_id and adapter_task_id not in deps:
                    deps.insert(0, adapter_task_id)
                item["depends_on"] = deps
        normalized.append(item)
    return normalized


def _facade_adapter_dependency(
    task: dict[str, Any],
    adapter_task_by_pack: dict[str, str],
) -> str | None:
    """Return the managed capability adapter task a facade module explicitly references."""
    text_parts = [
        str(task.get("description") or ""),
        str(task.get("initial_message") or ""),
        " ".join(str(item) for item in task.get("acceptance_criteria") or []),
    ]
    integration_needs = task.get("integration_needs")
    if isinstance(integration_needs, list):
        for need in integration_needs:
            if isinstance(need, dict):
                text_parts.append(" ".join(str(value) for value in need.values()))
            else:
                text_parts.append(str(need))
    haystack = "\n".join(text_parts).lower()
    matches = [
        adapter_task_id
        for pack_id, adapter_task_id in adapter_task_by_pack.items()
        if pack_id.lower() in haystack
        or f"{pack_id.lower()}_client" in haystack
        or f"{pack_id.lower()}client" in haystack
    ]
    unique_matches = sorted(set(matches))
    if len(unique_matches) == 1:
        return unique_matches[0]
    if len(adapter_task_by_pack) == 1:
        return next(iter(adapter_task_by_pack.values()))
    return None


def _managed_capability_backing_module_ids(
    capability_packs: list[dict[str, Any]],
    *,
    context_variables: Any | None,
) -> frozenset[str]:
    available_packs = _context_available_pack_map(context_variables)
    module_ids: set[str] = set()
    managed_capability_ids = {
        str(pack.get("capability_pack_id") or pack.get("id") or pack.get("pack_id") or "").strip()
        for pack in capability_packs
        if isinstance(pack, dict) and pack.get("capability_source") == "managed_capability"
    } - {""}
    for pack_id in managed_capability_ids:
        available = available_packs.get(pack_id) or {}
        for key in ("backing_module",):
            value = str(available.get(key) or "").strip()
            if value:
                module_ids.add(value)
        for value in available.get("requires_modules") or []:
            value = str(value or "").strip()
            if value:
                module_ids.add(value)
        for capability in available.get("capabilities") or []:
            if isinstance(capability, dict):
                value = str(capability.get("module") or "").strip()
                if value:
                    module_ids.add(value)
    return frozenset(module_ids - managed_capability_ids)


def _iter_page_api_endpoints(value: Any) -> Iterable[str]:
    if isinstance(value, dict):
        for key, nested in value.items():
            if key == "api_endpoint" and isinstance(nested, str):
                endpoint = nested.strip()
                if endpoint:
                    yield endpoint
            else:
                yield from _iter_page_api_endpoints(nested)
    elif isinstance(value, list):
        for item in value:
            yield from _iter_page_api_endpoints(item)


def _validate_page_bindings(
    pages: list[dict[str, Any]],
    *,
    managed_capability_ids: frozenset[str],
    managed_capability_backing_module_ids: frozenset[str],
) -> None:
    forbidden_ids = managed_capability_ids | managed_capability_backing_module_ids
    if not forbidden_ids:
        return
    for page in pages:
        page_name = str(page.get("name") or page.get("page_id") or "<unknown>")
        for endpoint in _iter_page_api_endpoints(page):
            normalized = endpoint.replace("\\", "/")
            for module_id in forbidden_ids:
                if normalized.startswith(f"/api/modules/{module_id}/"):
                    raise ValueError(
                        f"Page '{page_name}' binds api_endpoint '{endpoint}' directly to managed "
                        f"module '{module_id}'. Managed capability pages must bind to an app-owned "
                        "facade module endpoint instead."
                    )


def _managed_capability_reference_tokens(pack: dict[str, Any]) -> frozenset[str]:
    tokens: set[str] = set()
    pack_id = _pack_id_from_descriptor(pack)
    for value in [pack_id, pack.get("surface_id"), pack.get("label"), pack.get("display_name")]:
        for token in str(value or "").lower().replace("-", "_").split("_"):
            if len(token) >= 3:
                tokens.add(token)
    for operation in _normalize_string_list(pack.get("operations")):
        for token in operation.lower().replace(".", "_").replace("-", "_").split("_"):
            if len(token) >= 3:
                tokens.add(token)
    return frozenset(tokens)


def _page_reference_text(page: dict[str, Any]) -> str:
    values: list[str] = []
    for key in ("name", "page_id", "route", "purpose"):
        value = page.get(key)
        if value:
            values.append(str(value))
    for key in ("primary_entities", "primary_actions"):
        values.extend(_normalize_string_list(page.get(key)))
    return " ".join(values).lower().replace("-", "_")


def _managed_capability_is_user_facing(pack: dict[str, Any], pages: list[dict[str, Any]]) -> bool:
    if _normalize_string_list(pack.get("primary_pages")):
        return True
    tokens = _managed_capability_reference_tokens(pack)
    if not tokens:
        return False
    for page in pages:
        if not isinstance(page, dict):
            continue
        page_text = _page_reference_text(page)
        if any(token in page_text for token in tokens):
            return True
    return False


def _validate_user_facing_managed_capability_tasks(
    capability_packs: list[dict[str, Any]],
    pages: list[dict[str, Any]],
    build_tasks: list[dict[str, Any]],
    *,
    managed_capability_ids: frozenset[str],
) -> None:
    if not managed_capability_ids or not pages:
        return

    page_bundle_tasks = [
        task
        for task in build_tasks
        if isinstance(task, dict) and str(task.get("task_type") or "").strip() == "page_bundle"
    ]
    facade_module_tasks = [
        task
        for task in build_tasks
        if isinstance(task, dict)
        and str(task.get("task_type") or "").strip() == "module_contract"
        and str(task.get("capability_pack_id") or "").strip() not in managed_capability_ids
    ]

    for pack in capability_packs:
        if not isinstance(pack, dict) or str(pack.get("capability_source") or "").strip() != "managed_capability":
            continue
        pack_id = _pack_id_from_descriptor(pack)
        if not pack_id or pack_id not in managed_capability_ids:
            continue
        if not _managed_capability_is_user_facing(pack, pages):
            continue
        if not page_bundle_tasks:
            raise ValueError(
                f"Managed capability '{pack_id}' appears in page intent but no page_bundle build task was planned. "
                "User-facing managed capabilities must include a page_bundle task whose endpoints bind to an app-owned facade module."
            )
        if not facade_module_tasks:
            raise ValueError(
                f"Managed capability '{pack_id}' appears in page intent but no app-owned facade module_contract task was planned. "
                f"Do not generate modules/{pack_id}/. Plan a separate generated facade module and bind pages to that facade."
            )


def _validate_build_tasks(build_tasks: list[dict[str, Any]], managed_capability_ids: frozenset[str] | None = None) -> None:
    managed_capability_ids = managed_capability_ids or frozenset()
    for task in build_tasks:
        task_id = str(task.get("task_id") or "<unknown>")
        task_type = str(task.get("task_type") or "<missing>")
        initial_agent = str(task.get("initial_agent") or "<missing>")
        capability_pack_id = task.get("capability_pack_id")
        owned_paths = _normalized_owned_paths(task)
        surface_kind_raw = task.get("surface_kind")
        normalized_capability_pack_id = str(capability_pack_id or "").strip()

        deployment_artifact_paths = _deployment_contract_artifact_paths(owned_paths)
        if deployment_artifact_paths:
            raise ValueError(
                "Build task "
                f"'{task_id}' owns deployment contract artifact path(s): "
                f"{deployment_artifact_paths}. AppBuildPlan build tasks must not own "
                "Dockerfile, docker-compose.yml, env.example, deployment.manifest.json, "
                "or .github/workflows/*. Deployment artifacts are emitted by the "
                "generate_and_download deployment contract renderer through DownloadRequest "
                "deployment_profile/include_* fields."
            )

        if normalized_capability_pack_id in managed_capability_ids:
            managed_capability_module_prefix = f"modules/{normalized_capability_pack_id}/"
            if task_type == "module_contract":
                raise ValueError(
                    "Build task "
                    f"'{task_id}' tries to generate module_contract files for managed capability "
                    f"'{normalized_capability_pack_id}'. Managed capabilities must use api_surface adapters "
                    "with surface_kind=external_integration, not app-local module ownership."
                )
            if any(path.replace("\\", "/").startswith(managed_capability_module_prefix) for path in owned_paths):
                raise ValueError(
                    "Build task "
                    f"'{task_id}' owns '{managed_capability_module_prefix}' for managed capability "
                    f"'{normalized_capability_pack_id}'. Managed capability adapters must use the app-bundle-relative "
                    "services/integrations/ lane, which assembles under app/services/integrations/."
                )
            if any(path.replace("\\", "/").startswith(_ADAPTERS_PREFIX) for path in owned_paths):
                raise ValueError(
                    "Build task "
                    f"'{task_id}' owns services/adapters/ for managed capability "
                    f"'{normalized_capability_pack_id}'. Managed capability adapters must be thin API clients under "
                    "app-bundle-relative services/integrations/."
                )
            if task_type == "service_foundation":
                raise ValueError(
                    "Build task "
                    f"'{task_id}' uses service_foundation for managed capability "
                    f"'{normalized_capability_pack_id}'. Managed capabilities must use api_surface adapters under "
                    "app-bundle-relative services/integrations/."
                )
            if task_type == "api_surface" and surface_kind_raw and surface_kind_raw != "external_integration":
                raise ValueError(
                    f"Build task '{task_id}' is an adapter for managed capability '{normalized_capability_pack_id}' "
                    f"but declares surface_kind='{surface_kind_raw}'. "
                    "Managed capability adapter tasks must use surface_kind='external_integration'."
                )

        if surface_kind_raw == _WORKFLOW_SURFACE_KIND:
            raise ValueError(
                f"Build task '{task_id}' declares surface_kind='workflow'. "
                "Workflow surfaces are owned by AgentGenerator, not AppGenerator. "
                "Remove this task from AppBuildPlan and use a workflow_touchpoint entry instead."
            )

        if surface_kind_raw in _SURFACE_KIND_ALLOWED_TASK_TYPES:
            permitted = _SURFACE_KIND_ALLOWED_TASK_TYPES[surface_kind_raw]
            if task_type not in permitted:
                permitted_str = ", ".join(sorted(permitted))
                raise ValueError(
                    f"Build task '{task_id}' uses task_type='{task_type}' with "
                    f"surface_kind='{surface_kind_raw}'. Only these task types are valid "
                    f"for surface_kind='{surface_kind_raw}': {permitted_str}."
                )

        if task_type == "page_bundle" and initial_agent != "AppSchemaAgent":
            raise ValueError(
                "Build task "
                f"'{task_id}' assigns persistent page output to a non-schema owner "
                f"({initial_agent}). `page_bundle` must start at AppSchemaAgent and emit "
                "declarative page artifacts only."
            )

        if task_type in _MODULE_LOCAL_TASK_TYPES:
            if not normalized_capability_pack_id:
                raise ValueError(
                    "Build task "
                    f"'{task_id}' uses task_type '{task_type}' but capability_pack_id is null. "
                    "Module-local tasks must identify the generated module folder."
                )
            module_prefix = f"modules/{normalized_capability_pack_id}/"
            invalid_module_paths = [
                path
                for path in owned_paths
                if not path.replace("\\", "/").startswith(module_prefix)
            ]
            if invalid_module_paths:
                raise ValueError(
                    "Build task "
                    f"'{task_id}' owns paths outside module '{normalized_capability_pack_id}': "
                    f"{invalid_module_paths}."
                )

            backend_paths = [
                path
                for path in owned_paths
                if "/backend/" in path.replace("\\", "/")
            ]
            if task_type == "module_contract" and backend_paths:
                raise ValueError(
                    "Build task "
                    f"'{task_id}' mixes module contract YAML with backend Python files: "
                    f"{backend_paths}. `module_contract` tasks must emit module.yaml, contracts/*, "
                    "and optional runtime_extensions.yaml only. Use `data_models` and "
                    "`business_services` tasks for backend Python."
                )

            if task_type == "data_models":
                expected = f"{module_prefix}backend/schemas.py"
                invalid = [
                    path
                    for path in owned_paths
                    if path.replace("\\", "/") != expected
                ]
                if invalid:
                    raise ValueError(
                        "Build task "
                        f"'{task_id}' uses task_type 'data_models' but owns non-schema paths: "
                        f"{invalid}. `data_models` tasks may only own {expected}."
                    )

            if task_type == "business_services":
                schemas_path = f"{module_prefix}backend/schemas.py"
                invalid = [
                    path
                    for path in owned_paths
                    if not path.replace("\\", "/").startswith(f"{module_prefix}backend/")
                    or path.replace("\\", "/") == schemas_path
                ]
                if invalid:
                    raise ValueError(
                        "Build task "
                        f"'{task_id}' uses task_type 'business_services' but owns invalid "
                        f"backend paths: {invalid}. `business_services` tasks own handler.py, "
                        "service.py, repo.py, policy.py, and declared hooks; schemas.py belongs "
                        "to the `data_models` task."
                    )

        # Managed-capability adapter tasks must declare capability_pack_id so that template
        # expansion (resolve_managed_capability_templates) can locate the correct pack template.
        # This check fires only when the path pattern matches a known managed capability
        # (e.g. services/integrations/provider_client.py -> inferred pack id "provider").
        if (
            task_type == "api_surface"
            and surface_kind_raw == "external_integration"
            and not normalized_capability_pack_id
        ):
            for owned_path in owned_paths:
                inferred_pack_id = _infer_pack_id_from_integration_path(owned_path)
                if inferred_pack_id and inferred_pack_id in managed_capability_ids:
                    raise ValueError(
                        f"Build task '{task_id}' generates '{owned_path}' for managed_capability "
                        f"'{inferred_pack_id}' but has capability_pack_id=null. "
                        f"Set capability_pack_id: '{inferred_pack_id}' so that "
                        "resolve_managed_capability_templates can locate the managed capability template. "
                        "The capability_pack_id on api_surface adapter tasks identifies "
                        "which managed capability template to copy into the generated app."
                    )

        raw_frontend_path = _raw_frontend_source_path(task)
        if raw_frontend_path:
            raise ValueError(
                "Build task "
                f"'{task_id}' plans raw frontend source output ('{raw_frontend_path}'). "
                "Persistent app UI must compile through AppSchemaAgent/page_bundle plus "
                "shell/theme artifacts, not source files."
            )

        if task_type == "admin_config":
            raise ValueError(
                "Build task "
                f"'{task_id}' uses obsolete task_type 'admin_config'. "
                "Admin bootstrap lives in app/app.json admins; use module_contract for feature panels "
                "and api_surface for split service admin APIs."
            )

        if _OBSOLETE_HOST_ADMIN_CONFIG_PATH in owned_paths:
            raise ValueError(
                "Build task "
                f"'{task_id}' owns obsolete path '{_OBSOLETE_HOST_ADMIN_CONFIG_PATH}'. "
                "Admin bootstrap lives in app/app.json admins; do not generate app/config/admin.json."
            )

        has_app_service_admin_path = bool(_APP_SERVICE_ADMIN_PATHS.intersection(owned_paths))
        if has_app_service_admin_path:
            if task_type != "api_surface":
                raise ValueError(
                    "Build task "
                    f"'{task_id}' owns split service admin files but uses task_type '{task_type}'. "
                    "Use the explicit api_surface task for services/admin_config.py + services/routes/admin.py."
                )
            if initial_agent != "ControllerAgent":
                raise ValueError(
                    "Build task "
                    f"'{task_id}' assigns split service admin files to {initial_agent}. "
                    "`api_surface` must start at ControllerAgent."
                )
            if capability_pack_id is not None:
                raise ValueError(
                    "Build task "
                    f"'{task_id}' must keep capability_pack_id null for app-level split admin APIs."
                )
            if _APP_SERVICE_ADMIN_PATHS.difference(owned_paths):
                raise ValueError(
                    "Build task "
                    f"'{task_id}' must own both services/admin_config.py and services/routes/admin.py together."
                )

        legacy_paths = disallowed_legacy_app_paths(owned_paths)
        if legacy_paths:
            raise ValueError(
                "Build task "
                f"'{task_id}' owns removed app path(s) that are no longer canonical: "
                f"{legacy_paths}. Use data/contract.json, data/migrations/*.json, "
                f"and {APP_SECURITY_SECRETS_PATH}."
            )

        invalid_roots = noncanonical_app_root_paths(owned_paths)
        if invalid_roots:
            raise ValueError(
                "Build task "
                f"'{task_id}' owns path(s) outside canonical app planes: {invalid_roots}. "
                "Allowed app-root planes are admin, backend, brand, config, control_plane, "
                "data, modules, security, services, and ui."
            )

        invalid_config_paths = noncanonical_app_config_paths(owned_paths)
        if invalid_config_paths:
            raise ValueError(
                "Build task "
                f"'{task_id}' owns noncanonical app config path(s): {invalid_config_paths}. "
                "Use config/ai.json, config/shell.json, config/asset_manifest.json, "
                "config/targets.json, or safe metadata under config/integrations/."
            )

        invalid_service_paths = _noncanonical_service_paths(owned_paths)
        if invalid_service_paths:
            raise ValueError(
                "Build task "
                f"'{task_id}' owns noncanonical services path(s): {invalid_service_paths}. "
                "Use services/integrations/, services/adapters/, services/routes/, or "
                "services/config.py. Data and security are first-class planes under "
                f"data/ and {APP_SECURITY_SECRETS_PATH}."
            )

        if task_type == "service_foundation":
            invalid_foundation_paths = _invalid_service_foundation_paths(owned_paths)
            if invalid_foundation_paths:
                raise ValueError(
                    "Build task "
                    f"'{task_id}' uses service_foundation but owns non-service-foundation "
                    f"path(s): {invalid_foundation_paths}. service_foundation may only own "
                    "services/config.py, services/integrations/, services/adapters/, "
                    f"services/routes/, and {APP_SECURITY_SECRETS_PATH}."
                )

        if task_type == "control_plane_pack":
            allowed_config_paths = {
                "control_plane/config/control_plane.yaml",
                "control_plane/config/tools.yaml",
                "control_plane/config/policies.yaml",
            }
            if normalized_capability_pack_id:
                raise ValueError(
                    "Build task "
                    f"'{task_id}' uses task_type 'control_plane_pack' but capability_pack_id is not null. "
                    "Control-plane packs are app-level harness artifacts, not capability-pack modules."
                )
            if surface_kind_raw != "control_plane":
                raise ValueError(
                    "Build task "
                    f"'{task_id}' uses task_type 'control_plane_pack' but surface_kind is "
                    f"'{surface_kind_raw}'. Use surface_kind='control_plane'."
                )
            invalid = [
                path
                for path in owned_paths
                if path not in allowed_config_paths
                and not (
                    path.startswith("control_plane/prompts/")
                    and PurePosixPath(path).suffix == ".yaml"
                )
            ]
            if invalid:
                raise ValueError(
                    "Build task "
                    f"'{task_id}' owns invalid control-plane pack paths: {invalid}. "
                    "Control-plane pack tasks may only own control_plane/config/* and "
                    "control_plane/prompts/*.yaml."
                )
            required = {
                "control_plane/config/control_plane.yaml",
                "control_plane/config/tools.yaml",
            }
            missing = sorted(required.difference(owned_paths))
            if missing:
                raise ValueError(
                    "Build task "
                    f"'{task_id}' is missing required control-plane pack paths: {missing}."
                )

        if task_type not in _ALLOWED_TASK_TYPES:
            allowed = ", ".join(sorted(_ALLOWED_TASK_TYPES))
            raise ValueError(
                "Build task "
                f"'{task_id}' uses unsupported task_type '{task_type}'. "
                f"Use only the canonical AppGenerator task types: {allowed}."
            )

        expected_initial_agent = _CANONICAL_INITIAL_AGENTS.get(task_type)
        if expected_initial_agent and initial_agent != expected_initial_agent:
            raise ValueError(
                "Build task "
                f"'{task_id}' assigns task_type '{task_type}' to {initial_agent}. "
                f"`{task_type}` must start at {expected_initial_agent}."
            )


def _validate_unique_owned_paths(build_tasks: list[dict[str, Any]]) -> None:
    owners_by_path: dict[str, list[str]] = {}
    for task in build_tasks:
        if not isinstance(task, dict):
            continue
        task_id = str(task.get("task_id") or "<unknown>").strip()
        for path in _normalized_owned_paths(task):
            if path in _SHARED_OWNED_PATHS:
                continue
            owners_by_path.setdefault(path, []).append(task_id)

    duplicates = {
        path: _dedupe_preserving_order(task_ids)
        for path, task_ids in owners_by_path.items()
        if len(set(task_ids)) > 1
    }
    if duplicates:
        raise ValueError(
            "Build tasks declare overlapping owned_paths: "
            f"{duplicates}. Each generated artifact must have one owner task; "
            "merge the work or split paths so task batches cannot race."
        )


def _validate_carry_forward_decisions(
    decisions: list[dict[str, Any]],
    task_ids: frozenset[str],
) -> None:
    """Validate carry_forward_decisions entries.

    Rules:
    - module_id must be a non-empty string.
    - decision must be one of: reuse, adapt, regenerate, drop.
    - reason must be non-empty.
    - source must be one of: carry_forward_candidate, human_override, planner.
    - affected_build_tasks entries must reference existing task ids when provided.
    """
    for i, decision in enumerate(decisions):
        if not isinstance(decision, dict):
            raise ValueError(
                f"carry_forward_decisions[{i}] must be a dict, got {type(decision).__name__}"
            )
        label = f"carry_forward_decisions[{i}]"

        module_id = str(decision.get("module_id") or "").strip()
        if not module_id:
            raise ValueError(f"{label}: module_id must be a non-empty string")

        decision_value = str(decision.get("decision") or "").strip()
        if decision_value not in _CARRY_FORWARD_DECISION_VALUES:
            allowed = ", ".join(sorted(_CARRY_FORWARD_DECISION_VALUES))
            raise ValueError(
                f"{label} (module_id={module_id!r}): decision must be one of [{allowed}], "
                f"got {decision_value!r}"
            )

        reason = str(decision.get("reason") or "").strip()
        if not reason:
            raise ValueError(
                f"{label} (module_id={module_id!r}): reason must be a non-empty string"
            )

        source = str(decision.get("source") or "").strip()
        if source and source not in _CARRY_FORWARD_SOURCE_VALUES:
            allowed_src = ", ".join(sorted(_CARRY_FORWARD_SOURCE_VALUES))
            raise ValueError(
                f"{label} (module_id={module_id!r}): source must be one of [{allowed_src}], "
                f"got {source!r}"
            )

        affected = decision.get("affected_build_tasks")
        if affected:
            if not isinstance(affected, list):
                raise ValueError(
                    f"{label} (module_id={module_id!r}): "
                    "affected_build_tasks must be a list when provided"
                )
            unknown = [t for t in affected if isinstance(t, str) and t not in task_ids]
            if unknown:
                raise ValueError(
                    f"{label} (module_id={module_id!r}): "
                    f"affected_build_tasks references unknown task ids: {unknown}. "
                    "Task ids must exist in build_tasks."
                )


def _validate_task_dependencies(
    build_tasks: list[dict[str, Any]],
    task_ids: frozenset[str],
) -> None:
    """Validate depends_on references on build tasks.

    Rules:
    - depends_on must be a list when provided.
    - Every entry in depends_on must reference a task_id that exists in
      build_tasks. Unknown references indicate typos or missing tasks and
      will produce broken generation ordering at runtime.
    """
    for task in build_tasks:
        task_id = str(task.get("task_id") or "<unknown>")
        depends_on = task.get("depends_on")

        if depends_on is None:
            continue

        if not isinstance(depends_on, list):
            raise ValueError(
                f"Build task '{task_id}': depends_on must be a list when provided."
            )

        unknown = [
            d for d in depends_on
            if isinstance(d, str) and d not in task_ids
        ]
        if unknown:
            raise ValueError(
                f"Build task '{task_id}': depends_on references unknown task "
                f"ids: {unknown}. All depends_on entries must reference "
                "task_ids declared in build_tasks."
            )


def app_build_plan(
    *,
    AppBuildPlan: Annotated[
        dict[str, Any] | None,
        Field(description="Canonical app build plan emitted by AppPlanAgent."),
    ],
    context_variables: Annotated[
        Any | None,
        Field(description="AG2-injected workflow context variables."),
    ] = None,
) -> str:
    if not AppBuildPlan or not isinstance(AppBuildPlan, dict):
        raise ValueError("AppBuildPlan payload is required and must be a dictionary")
    AppBuildPlan = _unwrap_app_build_plan_payload(AppBuildPlan)
    if "app_kind" not in AppBuildPlan and context_variables and hasattr(context_variables, "get"):
        existing_plan = context_variables.get("app_build_plan")
        if isinstance(existing_plan, dict):
            AppBuildPlan = _unwrap_app_build_plan_payload(existing_plan)

    agent_message = str(AppBuildPlan.get("agent_message") or "").strip()
    app_kind = str(AppBuildPlan.get("app_kind") or "").strip()
    pages = _normalize_object_list(AppBuildPlan.get("pages"))
    entities = _normalize_object_list(AppBuildPlan.get("entities"))
    roles = _normalize_string_list(AppBuildPlan.get("roles"))
    auth_strategy = AppBuildPlan.get("auth_strategy")
    service_scope = _normalize_string_list(AppBuildPlan.get("service_scope"))
    frontend_scope = _normalize_string_list(AppBuildPlan.get("frontend_scope"))
    theme_preferences = AppBuildPlan.get("theme_preferences")
    brand_intent = AppBuildPlan.get("brand_intent")
    capability_packs = _ensure_context_selected_capability_packs(
        _normalize_object_list(AppBuildPlan.get("capability_packs")),
        context_variables=context_variables,
    )
    capability_packs = _normalize_capability_pack_sources(
        capability_packs,
        context_variables=context_variables,
    )
    external_integrations = _normalize_object_list(AppBuildPlan.get("external_integrations"))
    build_tasks = _merge_persistence_contract_tasks(sorted(
        [
            _normalize_build_task_identity(task)
            for task in _normalize_object_list(AppBuildPlan.get("build_tasks"))
        ],
        key=_task_sort_key,
    ))
    inferred_managed_capability_ids = _infer_managed_capability_ids_from_adapter_tasks(
        build_tasks,
        context_variables=context_variables,
    )
    if inferred_managed_capability_ids:
        capability_packs = _ensure_managed_capability_entries(
            capability_packs,
            managed_capability_ids=inferred_managed_capability_ids,
            context_variables=context_variables,
        )
        capability_packs = _normalize_capability_pack_sources(
            capability_packs,
            context_variables=context_variables,
        )
        build_tasks = _normalize_managed_capability_adapter_task_pack_ids(
            build_tasks,
            managed_capability_ids=inferred_managed_capability_ids,
        )
    data_contract = AppBuildPlan.get("data_contract")
    pending_schema_migration = AppBuildPlan.get("pending_schema_migration")
    generation_order = _normalize_string_list(AppBuildPlan.get("generation_order"))
    agent_backend_required = bool(AppBuildPlan.get("agent_backend_required", False))
    carry_forward_decisions = _normalize_object_list(AppBuildPlan.get("carry_forward_decisions"))

    managed_capability_ids = frozenset(
        _pack_id_from_descriptor(p)
        for p in capability_packs
        if isinstance(p, dict) and p.get("capability_source") == "managed_capability"
    ) - {""}
    capability_packs, pages, build_tasks = _apply_selected_pack_files(
        capability_packs=capability_packs,
        pages=pages,
        build_tasks=build_tasks,
        context_variables=context_variables,
    )
    pages = _normalize_managed_capability_page_bindings(
        pages,
        capability_packs=capability_packs,
        context_variables=context_variables,
    )
    managed_capability_ids = frozenset(
        _pack_id_from_descriptor(p)
        for p in capability_packs
        if isinstance(p, dict) and p.get("capability_source") == "managed_capability"
    ) - {""}
    build_tasks = _normalize_page_task_dependencies(build_tasks)
    build_tasks = _normalize_facade_task_dependencies(
        build_tasks,
        managed_capability_ids=managed_capability_ids,
    )
    managed_capability_backing_module_ids = _managed_capability_backing_module_ids(
        capability_packs,
        context_variables=context_variables,
    )
    _validate_build_tasks(build_tasks, managed_capability_ids=managed_capability_ids)
    _validate_unique_owned_paths(build_tasks)
    _validate_page_bindings(
        pages,
        managed_capability_ids=managed_capability_ids,
        managed_capability_backing_module_ids=managed_capability_backing_module_ids,
    )
    _validate_user_facing_managed_capability_tasks(
        capability_packs,
        pages,
        build_tasks,
        managed_capability_ids=managed_capability_ids,
    )

    task_ids: frozenset[str] = frozenset(
        str(t.get("task_id") or "") for t in build_tasks if t.get("task_id")
    )
    _validate_task_dependencies(build_tasks, task_ids=task_ids)
    if carry_forward_decisions:
        _validate_carry_forward_decisions(carry_forward_decisions, task_ids=task_ids)

    if not app_kind:
        raise ValueError("AppBuildPlan.app_kind is required")
    if not pages:
        raise ValueError("AppBuildPlan.pages must contain at least one page")

    task_batch_items: list[dict[str, Any]] = []
    normalized_build_tasks: list[dict[str, Any]] = []
    for task in build_tasks:
        normalized_task = dict(task)
        normalized_context = _normalize_context_variables(normalized_task.get("context_variables"))
        if normalized_context is not None:
            normalized_task["context_variables"] = normalized_context
        task_id = str(normalized_task.get("task_id") or "").strip()
        task_type = str(normalized_task.get("task_type") or "").strip()
        item = {
            **normalized_task,
            "task_run_mode": True,
            "current_build_task_id": task_id,
            "current_build_task_type": task_type,
            "current_build_task": dict(normalized_task),
        }
        task_batch_items.append(item)
        normalized_build_tasks.append(normalized_task)

    normalized_plan = {
        "agent_message": agent_message or "App build plan cached successfully.",
        "app_kind": app_kind,
        "pages": pages,
        "entities": entities,
        "roles": roles,
        "auth_strategy": auth_strategy,
        "service_scope": service_scope,
        "frontend_scope": frontend_scope,
        "theme_preferences": theme_preferences,
        "brand_intent": brand_intent if isinstance(brand_intent, dict) else None,
        "capability_packs": capability_packs,
        "external_integrations": external_integrations,
        "agent_backend_required": agent_backend_required,
        "build_tasks": normalized_build_tasks,
        "data_contract": data_contract if isinstance(data_contract, dict) else None,
        "pending_schema_migration": pending_schema_migration if isinstance(pending_schema_migration, dict) else None,
        "generation_order": generation_order,
        "carry_forward_decisions": carry_forward_decisions,
    }

    if context_variables and hasattr(context_variables, "set"):
        try:
            context_variables.set("app_build_plan", normalized_plan)
            context_variables.set("app_plan_ready", True)
            context_variables.set("app_task_batch_items", task_batch_items)
            context_variables.set("app_task_batch_status", "planned")
            _logger.info(
                "Cached AppBuildPlan: kind=%s pages=%d entities=%d packs=%d build_tasks=%d integrations=%d batch_items=%d",
                app_kind,
                len(pages),
                len(entities),
                len(capability_packs),
                len(build_tasks),
                len(external_integrations),
                len(task_batch_items),
            )
        except Exception as exc:
            _logger.error("Failed to cache AppBuildPlan: %s", exc)
            return f"Error caching AppBuildPlan: {exc}"
    else:
        _logger.warning("context_variables not available or missing 'set' method")

    return (
        f"{normalized_plan['agent_message']}\n\n"
        f"App kind: {app_kind}\n"
        f"Pages: {len(pages)}\n"
        f"Entities: {len(entities)}\n"
        f"Capability packs: {len(capability_packs)}\n"
        f"Build tasks: {len(build_tasks)}\n"
        f"Task batch items: {len(task_batch_items)}\n"
        f"Roles: {len(roles)}\n"
        f"Agent backend required: {agent_backend_required}\n"
        f"Integrations: {len(external_integrations)}"
    )


__all__ = ["app_build_plan"]

