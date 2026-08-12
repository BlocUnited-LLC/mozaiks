"""Deterministic ExistingAppDiscovery to AppGenerator handoff helpers."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any


def build_app_build_plan_from_discovery(
    discovery_artifact: Mapping[str, Any],
    *,
    module_decomposition_plan: Mapping[str, Any],
    app_id: str | None = None,
    app_name: str | None = None,
) -> dict[str, Any]:
    """Project captured brownfield discovery output into an AppBuildPlan.

    This is the deterministic seam after discovery reasoning. It intentionally
    consumes structured discovery/adoption evidence rather than source files or
    prompt text, then hands the resulting canonical plan to AppGenerator.
    """
    product_spec = _mapping(discovery_artifact.get("existing_product_spec"))
    augmentation_plan = _mapping(discovery_artifact.get("agent_augmentation_plan"))
    modules = _module_specs(module_decomposition_plan)
    pages = _page_specs(module_decomposition_plan, modules)
    if not modules:
        raise ValueError("module_decomposition_plan.proposed_modules must contain at least one module")
    if not pages:
        raise ValueError("module_decomposition_plan.proposed_pages must contain at least one page")

    resolved_app_id = _slug(app_id or product_spec.get("app_id") or product_spec.get("app_name"), fallback="existing_app")
    resolved_app_name = _clean(app_name or product_spec.get("app_name") or resolved_app_id.replace("_", " ").title())
    auth_strategy = "required" if _auth_required(product_spec) else "none"
    tasks = _build_tasks(modules, pages)

    return {
        "agent_message": "Captured brownfield discovery handoff AppBuildPlan.",
        "app_kind": "brownfield_migration",
        "app_id": resolved_app_id,
        "app_name": resolved_app_name,
        "source_discovery": {
            "artifact_version": discovery_artifact.get("artifact_version"),
            "app_type": discovery_artifact.get("app_type") or "brownfield_app",
            "adoption_level": augmentation_plan.get("adoption_level"),
        },
        "pages": pages,
        "entities": _entities(module_decomposition_plan, modules),
        "roles": [{"id": "user", "label": "User"}],
        "auth_strategy": auth_strategy,
        "service_scope": list(modules),
        "frontend_scope": [str(page["name"]) for page in pages],
        "capability_packs": [_capability_pack(module_id, spec, pages) for module_id, spec in modules.items()],
        "external_integrations": [],
        "agent_backend_required": bool(_list(module_decomposition_plan.get("proposed_workflows"))),
        "workflow_touchpoints": _workflow_touchpoints(module_decomposition_plan, pages),
        "build_tasks": tasks,
        "generation_order": [task["task_id"] for task in tasks],
    }


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _list(value: Any) -> list[Any]:
    return list(value) if isinstance(value, list) else []


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _slug(value: Any, *, fallback: str) -> str:
    text = re.sub(r"[^a-zA-Z0-9]+", "_", _clean(value).lower()).strip("_")
    return text or fallback


def _title(value: str) -> str:
    return value.replace("_", " ").replace("-", " ").title()


def _auth_required(product_spec: Mapping[str, Any]) -> bool:
    auth = _clean(product_spec.get("auth_model") or product_spec.get("auth"))
    return bool(auth and auth.lower() not in {"none", "public", "anonymous", "unauthenticated"})


def _module_specs(module_decomposition_plan: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    modules: dict[str, dict[str, Any]] = {}
    for raw in _list(module_decomposition_plan.get("proposed_modules")):
        spec = _mapping(raw)
        module_id = _slug(spec.get("module_id") or spec.get("id") or spec.get("name"), fallback="")
        if not module_id:
            continue
        actions = [_slug(action, fallback="") for action in _list(spec.get("proposed_actions"))]
        modules[module_id] = {**spec, "module_id": module_id, "proposed_actions": [action for action in actions if action]}
    return modules


def _page_specs(
    module_decomposition_plan: Mapping[str, Any],
    modules: Mapping[str, Mapping[str, Any]],
) -> list[dict[str, Any]]:
    pages: list[dict[str, Any]] = []
    for raw in _list(module_decomposition_plan.get("proposed_pages")):
        spec = _mapping(raw)
        page_name = _slug(spec.get("page_id") or spec.get("name") or spec.get("route"), fallback="")
        if not page_name:
            continue
        route = _clean(spec.get("route")) or f"/{page_name}"
        primary_module = _slug(spec.get("module_id") or spec.get("primary_module"), fallback="")
        if not primary_module:
            primary_module = next((module_id for module_id in modules if module_id in page_name), next(iter(modules), ""))
        actions = list(modules.get(primary_module, {}).get("proposed_actions") or [])
        list_action = next((action for action in actions if action.startswith("list_")), actions[0] if actions else "")
        pages.append(
            {
                "name": page_name,
                "route": route,
                "title": _clean(spec.get("title")) or _title(page_name),
                "page_type_hint": _clean(spec.get("page_type_hint") or spec.get("page_type")) or "record_list",
                "primary_entities": _list(spec.get("primary_entities")) or [_title(primary_module)] if primary_module else [],
                "primary_actions": actions,
                "sections_hint": [
                    {
                        "primitive": _clean(spec.get("primitive")) or "DataTable",
                        "section_id_hint": primary_module or page_name,
                        "title_hint": _clean(spec.get("title")) or _title(page_name),
                        "config_hint": (
                            f'{{"api_endpoint": "/api/modules/{primary_module}/{list_action}"}}'
                            if primary_module and list_action
                            else "{}"
                        ),
                    }
                ],
            }
        )
    return pages


def _entities(
    module_decomposition_plan: Mapping[str, Any],
    modules: Mapping[str, Mapping[str, Any]],
) -> list[dict[str, Any]]:
    seen: set[str] = set()
    entities: list[dict[str, Any]] = []
    for module_id, spec in modules.items():
        raw_entities = _list(spec.get("persistence_entities")) or [_title(module_id)]
        for raw_entity in raw_entities:
            name = _clean(raw_entity)
            if not name or name in seen:
                continue
            seen.add(name)
            entities.append({"name": name})
    for raw_entity in _list(module_decomposition_plan.get("entities")):
        name = _clean(raw_entity.get("name") if isinstance(raw_entity, Mapping) else raw_entity)
        if name and name not in seen:
            seen.add(name)
            entities.append({"name": name})
    return entities


def _capability_pack(module_id: str, spec: Mapping[str, Any], pages: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "capability_pack_id": module_id,
        "surface_id": module_id,
        "surface_kind": "module",
        "pack_type": "brownfield_module",
        "label": _clean(spec.get("label")) or _title(module_id),
        "summary": _clean(spec.get("summary")) or f"Brownfield generated {module_id} surface.",
        "implementation_mode": "deterministic",
        "primary_entities": _list(spec.get("persistence_entities")) or [_title(module_id)],
        "primary_pages": [
            page["name"]
            for page in pages
            if module_id in _clean(page.get("name")) or any(module_id in _clean(action) for action in page.get("primary_actions") or [])
        ],
        "operations": list(spec.get("proposed_actions") or []),
        "required_integrations": _list(spec.get("required_integrations")),
        "agentic_extensions": _list(spec.get("agentic_extensions")),
    }


def _workflow_touchpoints(module_decomposition_plan: Mapping[str, Any], pages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    touchpoints: list[dict[str, Any]] = []
    default_page = pages[0]["name"] if pages else None
    for raw in _list(module_decomposition_plan.get("proposed_workflows")):
        spec = _mapping(raw)
        workflow_id = _clean(spec.get("workflow_id") or spec.get("name"))
        if not workflow_id:
            continue
        touchpoints.append(
            {
                "page_name": _clean(spec.get("page_name")) or default_page,
                "workflow_id": workflow_id,
                "label": _clean(spec.get("label")) or _title(workflow_id),
                "context_variables": _mapping(spec.get("context_variables")),
            }
        )
    return touchpoints


def _build_tasks(modules: Mapping[str, Mapping[str, Any]], pages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    tasks: list[dict[str, Any]] = [
        {
            "task_id": "task_persistence",
            "task_type": "persistence_contract",
            "capability_pack_id": None,
            "surface_id": "database",
            "surface_kind": None,
            "execution_target": "AppGenerator",
            "initial_agent": "DatabaseAgent",
            "description": "Stage deterministic brownfield data contract.",
            "initial_message": "Generate data/contract.json and data/migrations/001_indexes.json.",
            "owned_paths": ["data/contract.json", "data/migrations/001_indexes.json"],
            "depends_on": [],
        }
    ]
    for module_id in modules:
        tasks.extend(
            [
                _task(
                    module_id,
                    "module",
                    "module_contract",
                    "ConfigMiddlewareAgent",
                    [f"modules/{module_id}/module.yaml"],
                    ["task_persistence"],
                ),
                _task(
                    module_id,
                    "models",
                    "data_models",
                    "ModelAgent",
                    [f"modules/{module_id}/backend/schemas.py"],
                    [f"task_{module_id}_module"],
                ),
                _task(
                    module_id,
                    "services",
                    "business_services",
                    "ServiceAgent",
                    [
                        f"modules/{module_id}/backend/handler.py",
                        f"modules/{module_id}/backend/service.py",
                        f"modules/{module_id}/backend/repo.py",
                        f"modules/{module_id}/backend/policy.py",
                    ],
                    [f"task_{module_id}_models"],
                ),
            ]
        )
    tasks.append(
        {
            "task_id": "task_pages",
            "task_type": "page_bundle",
            "capability_pack_id": None,
            "surface_id": "pages",
            "surface_kind": "ui_only",
            "execution_target": "AppGenerator",
            "initial_agent": "AppSchemaAgent",
            "description": "Generate declarative brownfield page schemas.",
            "initial_message": "Generate app.json and ui/pages/*.yaml files from the brownfield AppBuildPlan.",
            "owned_paths": ["app.json", *[f"ui/pages/{str(page['name']).lower().replace(' ', '_')}.yaml" for page in pages]],
            "depends_on": [f"task_{module_id}_services" for module_id in modules],
        }
    )
    return tasks


def _task(
    module_id: str,
    suffix: str,
    task_type: str,
    initial_agent: str,
    owned_paths: list[str],
    depends_on: list[str],
) -> dict[str, Any]:
    return {
        "task_id": f"task_{module_id}_{suffix}",
        "task_type": task_type,
        "capability_pack_id": module_id,
        "surface_id": module_id,
        "surface_kind": "module",
        "execution_target": "AppGenerator",
        "initial_agent": initial_agent,
        "description": f"Generate brownfield {module_id} {task_type}.",
        "initial_message": f"Generate {module_id} {task_type} from captured discovery output.",
        "owned_paths": owned_paths,
        "depends_on": depends_on,
    }


__all__ = ["build_app_build_plan_from_discovery"]
