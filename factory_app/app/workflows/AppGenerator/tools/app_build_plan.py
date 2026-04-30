import logging
from pathlib import PurePosixPath
from typing import Annotated, Any, Dict, List, Optional

from autogen.tools.dependency_injection import Field

_logger = logging.getLogger("tools.app_build_plan")

_RAW_FRONTEND_SOURCE_EXTENSIONS = {".css", ".html", ".jsx", ".less", ".sass", ".scss", ".tsx"}
_FRONTEND_JS_TS_SEGMENTS = (
    "/frontend/",
    "/chat-ui/",
    "/chatui/",
    "/src/pages/",
    "/src/components/",
)
_HOST_ADMIN_CONFIG_PATH = "app/config/admin.json"
_APP_BACKEND_ADMIN_PATHS = {"backend/admin_config.py", "backend/routes/admin.py"}


def _normalize_string_list(value: Any) -> List[str]:
    if not isinstance(value, list):
        return []
    items: List[str] = []
    for entry in value:
        text = str(entry).strip()
        if text:
            items.append(text)
    return items


def _normalize_object_list(value: Any) -> List[Dict[str, Any]]:
    if not isinstance(value, list):
        return []
    items: List[Dict[str, Any]] = []
    for entry in value:
        if isinstance(entry, dict):
            items.append(dict(entry))
    return items


def _task_sort_key(task: Dict[str, Any]) -> tuple[int, str]:
    task_id = str(task.get("task_id") or "")
    return (0 if task_id else 1, task_id)


def _raw_frontend_source_path(task: Dict[str, Any]) -> Optional[str]:
    for owned_path in _normalize_string_list(task.get("owned_paths")):
        normalized = owned_path.replace("\\", "/").strip().lower()
        suffix = PurePosixPath(normalized).suffix

        if suffix in _RAW_FRONTEND_SOURCE_EXTENSIONS:
            return owned_path

        if suffix in {".js", ".ts"} and any(segment in normalized for segment in _FRONTEND_JS_TS_SEGMENTS):
            return owned_path

    return None


def _normalized_owned_paths(task: Dict[str, Any]) -> List[str]:
    paths: List[str] = []
    for owned_path in _normalize_string_list(task.get("owned_paths")):
        normalized = owned_path.replace("\\", "/").strip()
        if normalized:
            paths.append(normalized)
    return paths


def _validate_build_tasks(build_tasks: List[Dict[str, Any]]) -> None:
    for task in build_tasks:
        task_id = str(task.get("task_id") or "<unknown>")
        task_type = str(task.get("task_type") or "<missing>")
        initial_agent = str(task.get("initial_agent") or "<missing>")
        capability_pack_id = task.get("capability_pack_id")
        owned_paths = _normalized_owned_paths(task)

        if task_type == "page_bundle" and initial_agent != "AppSchemaAgent":
            raise ValueError(
                "Build task "
                f"'{task_id}' assigns persistent page output to a non-schema owner "
                f"({initial_agent}). `page_bundle` must start at AppSchemaAgent and emit "
                "declarative page artifacts only."
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
            if initial_agent != "ConfigMiddlewareAgent":
                raise ValueError(
                    "Build task "
                    f"'{task_id}' assigns admin config output to {initial_agent}. "
                    "admin_config must start at ConfigMiddlewareAgent."
                )
            if capability_pack_id is not None:
                raise ValueError(
                    "Build task "
                    f"'{task_id}' must keep capability_pack_id null for host-owned admin config."
                )
            if owned_paths != [_HOST_ADMIN_CONFIG_PATH]:
                raise ValueError(
                    "Build task "
                    f"'{task_id}' must own only '{_HOST_ADMIN_CONFIG_PATH}'."
                )

        if _HOST_ADMIN_CONFIG_PATH in owned_paths and task_type != "admin_config":
            raise ValueError(
                "Build task "
                f"'{task_id}' owns '{_HOST_ADMIN_CONFIG_PATH}' but uses task_type '{task_type}'. "
                "Host admin config must be generated through the explicit admin_config task."
            )

        has_app_backend_admin_path = bool(_APP_BACKEND_ADMIN_PATHS.intersection(owned_paths))
        if has_app_backend_admin_path:
            if task_type != "api_surface":
                raise ValueError(
                    "Build task "
                    f"'{task_id}' owns split app-backend admin files but uses task_type '{task_type}'. "
                    "Use the explicit api_surface task for backend/admin_config.py + backend/routes/admin.py."
                )
            if initial_agent != "ControllerAgent":
                raise ValueError(
                    "Build task "
                    f"'{task_id}' assigns split app-backend admin files to {initial_agent}. "
                    "`api_surface` must start at ControllerAgent."
                )
            if capability_pack_id is not None:
                raise ValueError(
                    "Build task "
                    f"'{task_id}' must keep capability_pack_id null for app-level split admin APIs."
                )
            if _APP_BACKEND_ADMIN_PATHS.difference(owned_paths):
                raise ValueError(
                    "Build task "
                    f"'{task_id}' must own both backend/admin_config.py and backend/routes/admin.py together."
                )


def app_build_plan(
    *,
    AppBuildPlan: Annotated[
        Optional[Dict[str, Any]],
        Field(description="Canonical app build plan emitted by AppPlanAgent."),
    ],
    workflows: Annotated[
        Optional[List[Dict[str, Any]]],
        Field(description="Optional child workflow specs emitted alongside the build plan."),
    ] = None,
    context_variables: Annotated[
        Optional[Any],
        Field(description="AG2-injected workflow context variables."),
    ] = None,
) -> str:
    if not AppBuildPlan or not isinstance(AppBuildPlan, dict):
        raise ValueError("AppBuildPlan payload is required and must be a dictionary")

    agent_message = str(AppBuildPlan.get("agent_message") or "").strip()
    app_kind = str(AppBuildPlan.get("app_kind") or "").strip()
    pages = _normalize_object_list(AppBuildPlan.get("pages"))
    entities = _normalize_object_list(AppBuildPlan.get("entities"))
    roles = _normalize_string_list(AppBuildPlan.get("roles"))
    auth_strategy = AppBuildPlan.get("auth_strategy")
    backend_scope = _normalize_string_list(AppBuildPlan.get("backend_scope"))
    frontend_scope = _normalize_string_list(AppBuildPlan.get("frontend_scope"))
    theme_preferences = AppBuildPlan.get("theme_preferences")
    brand_intent = AppBuildPlan.get("brand_intent")
    capability_packs = _normalize_object_list(AppBuildPlan.get("capability_packs"))
    external_integrations = _normalize_object_list(AppBuildPlan.get("external_integrations"))
    build_tasks = sorted(_normalize_object_list(AppBuildPlan.get("build_tasks")), key=_task_sort_key)
    generation_order = _normalize_string_list(AppBuildPlan.get("generation_order"))
    agent_backend_required = bool(AppBuildPlan.get("agent_backend_required", False))

    _validate_build_tasks(build_tasks)

    if not app_kind:
        raise ValueError("AppBuildPlan.app_kind is required")
    if not pages:
        raise ValueError("AppBuildPlan.pages must contain at least one page")

    normalized_plan = {
        "agent_message": agent_message or "App build plan cached successfully.",
        "app_kind": app_kind,
        "pages": pages,
        "entities": entities,
        "roles": roles,
        "auth_strategy": auth_strategy,
        "backend_scope": backend_scope,
        "frontend_scope": frontend_scope,
        "theme_preferences": theme_preferences,
        "brand_intent": brand_intent if isinstance(brand_intent, dict) else None,
        "capability_packs": capability_packs,
        "external_integrations": external_integrations,
        "agent_backend_required": agent_backend_required,
        "build_tasks": build_tasks,
        "generation_order": generation_order,
    }

    if context_variables and hasattr(context_variables, "set"):
        try:
            context_variables.set("app_build_plan", normalized_plan)
            context_variables.set("app_plan_ready", True)
            _logger.info(
                "Cached AppBuildPlan: kind=%s pages=%d entities=%d packs=%d build_tasks=%d integrations=%d",
                app_kind,
                len(pages),
                len(entities),
                len(capability_packs),
                len(build_tasks),
                len(external_integrations),
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
        f"Roles: {len(roles)}\n"
        f"Agent backend required: {agent_backend_required}\n"
        f"Integrations: {len(external_integrations)}"
    )


__all__ = ["app_build_plan"]
