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
_OBSOLETE_HOST_ADMIN_CONFIG_PATH = "app/config/admin.json"
_APP_BACKEND_ADMIN_PATHS = {"backend/admin_config.py", "backend/routes/admin.py"}
_INTEGRATIONS_PREFIX = "backend/integrations/"
_CLIENT_SUFFIX = "_client.py"
_ALLOWED_TASK_TYPES = {
    "backend_foundation",
    "module_contract",
    "data_models",
    "business_services",
    "api_surface",
    "page_bundle",
    "agent_backend_integration",
    "control_plane_pack",
    "pack_overlay",
}
_CANONICAL_INITIAL_AGENTS = {
    "backend_foundation": "ConfigMiddlewareAgent",
    "module_contract": "ConfigMiddlewareAgent",
    "control_plane_pack": "ControlPlaneAgent",
    "pack_overlay": "ConfigMiddlewareAgent",
    "api_surface": "ControllerAgent",
    "page_bundle": "AppSchemaAgent",
}
_SURFACE_KIND_ALLOWED_TASK_TYPES: dict[str, frozenset[str]] = {
    "external_integration": frozenset({"api_surface"}),
    "control_plane": frozenset({"control_plane_pack"}),
    "ui_only": frozenset({"page_bundle"}),
    "framework_pack": frozenset({"pack_overlay"}),
}
_WORKFLOW_SURFACE_KIND = "workflow"


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


def _infer_pack_id_from_integration_path(path: str) -> Optional[str]:
    """
    Extract the hosted pack id from a backend/integrations/{pack_id}_client.py path.

    Returns the inferred pack_id string, or None if the path does not match
    the hosted adapter pattern.
    """
    normalized = path.replace("\\", "/").strip()
    if not normalized.startswith(_INTEGRATIONS_PREFIX):
        return None
    filename = PurePosixPath(normalized).name
    if filename.endswith(_CLIENT_SUFFIX):
        return filename[: -len(_CLIENT_SUFFIX)]
    return None


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


def _validate_build_tasks(build_tasks: List[Dict[str, Any]], hosted_pack_ids: frozenset[str] | None = None) -> None:
    hosted_pack_ids = hosted_pack_ids or frozenset()
    for task in build_tasks:
        task_id = str(task.get("task_id") or "<unknown>")
        task_type = str(task.get("task_type") or "<missing>")
        initial_agent = str(task.get("initial_agent") or "<missing>")
        capability_pack_id = task.get("capability_pack_id")
        owned_paths = _normalized_owned_paths(task)
        surface_kind_raw = task.get("surface_kind")
        normalized_capability_pack_id = str(capability_pack_id or "").strip()

        if normalized_capability_pack_id in hosted_pack_ids:
            hosted_module_prefix = f"modules/{normalized_capability_pack_id}/"
            if task_type == "module_contract":
                raise ValueError(
                    "Build task "
                    f"'{task_id}' tries to generate module_contract files for hosted pack "
                    f"'{normalized_capability_pack_id}'. Hosted packs must use api_surface adapters "
                    "with surface_kind=external_integration, not app-local module ownership."
                )
            if any(path.replace("\\", "/").startswith(hosted_module_prefix) for path in owned_paths):
                raise ValueError(
                    "Build task "
                    f"'{task_id}' owns '{hosted_module_prefix}' for hosted pack "
                    f"'{normalized_capability_pack_id}'. Hosted pack adapters must live under backend/integrations/."
                )
            if task_type == "api_surface" and surface_kind_raw and surface_kind_raw != "external_integration":
                raise ValueError(
                    f"Build task '{task_id}' is an adapter for hosted pack '{normalized_capability_pack_id}' "
                    f"but declares surface_kind='{surface_kind_raw}'. "
                    "Hosted pack adapter tasks must use surface_kind='external_integration'."
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

        # Hosted-pack adapter tasks must declare capability_pack_id so that template
        # expansion (resolve_hosted_pack_templates) can locate the correct pack template.
        # This check fires only when the path pattern matches a known hosted pack
        # (e.g. backend/integrations/mozaikspay_client.py → inferred pack id "mozaikspay").
        if (
            task_type == "api_surface"
            and surface_kind_raw == "external_integration"
            and not normalized_capability_pack_id
        ):
            for owned_path in owned_paths:
                inferred_pack_id = _infer_pack_id_from_integration_path(owned_path)
                if inferred_pack_id and inferred_pack_id in hosted_pack_ids:
                    raise ValueError(
                        f"Build task '{task_id}' generates '{owned_path}' for hosted_pack "
                        f"'{inferred_pack_id}' but has capability_pack_id=null. "
                        f"Set capability_pack_id: '{inferred_pack_id}' so that "
                        "resolve_hosted_pack_templates can locate the hosted pack template. "
                        "The capability_pack_id on api_surface adapter tasks identifies "
                        "which hosted pack template to copy into the generated app."
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
                "and api_surface for split app-backend admin APIs."
            )

        if _OBSOLETE_HOST_ADMIN_CONFIG_PATH in owned_paths:
            raise ValueError(
                "Build task "
                f"'{task_id}' owns obsolete path '{_OBSOLETE_HOST_ADMIN_CONFIG_PATH}'. "
                "Admin bootstrap lives in app/app.json admins; do not generate app/config/admin.json."
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

        if task_type == "pack_overlay" and not normalized_capability_pack_id:
            raise ValueError(
                "Build task "
                f"'{task_id}' uses task_type 'pack_overlay' but capability_pack_id is null. "
                "pack_overlay tasks must identify the framework_pack via capability_pack_id."
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

    hosted_pack_ids = frozenset(
        str(p.get("capability_pack_id") or "").strip()
        for p in capability_packs
        if isinstance(p, dict) and p.get("capability_source") == "hosted_pack"
    ) - {""}
    _validate_build_tasks(build_tasks, hosted_pack_ids=hosted_pack_ids)

    if not app_kind:
        raise ValueError("AppBuildPlan.app_kind is required")
    if not pages:
        raise ValueError("AppBuildPlan.pages must contain at least one page")

    child_workflows: List[Dict[str, Any]] = []
    if workflows:
        appgenerator_workflows = [w for w in workflows if w.get("name") == "AppGenerator"]
        appgenerator_task_ids = {str(t.get("task_id")) for t in build_tasks}
        covered_task_ids = {
            str(w.get("context_variables", {}).get("current_build_task_id"))
            for w in appgenerator_workflows
        }

        uncovered = appgenerator_task_ids - covered_task_ids
        if uncovered:
            raise ValueError(
                f"Child workflows must cover every AppGenerator build task. "
                f"Missing coverage for: {sorted(uncovered)}. "
                "Each AppGenerator build task must have a matching child workflow."
            )

        task_by_id = {str(t.get("task_id")): t for t in build_tasks}

        for workflow in appgenerator_workflows:
            ctx = dict(workflow.get("context_variables") or {})
            child_task_id = str(ctx.get("current_build_task_id") or "")
            child_task_type = str(ctx.get("current_build_task_type") or "")
            child_initial_agent = str(workflow.get("initial_agent") or "")

            canonical_task = task_by_id.get(child_task_id)
            if canonical_task:
                expected_task_type = str(canonical_task.get("task_type") or "")
                if child_task_type and expected_task_type and child_task_type != expected_task_type:
                    raise ValueError(
                        f"Child workflow for task '{child_task_id}' declares "
                        f"current_build_task_type='{child_task_type}' but the build plan "
                        f"task has task_type='{expected_task_type}'. These must match."
                    )

                expected_agent = _CANONICAL_INITIAL_AGENTS.get(expected_task_type or child_task_type)
                if expected_agent and child_initial_agent != expected_agent:
                    raise ValueError(
                        f"Child workflow for task '{child_task_id}' (task_type='{expected_task_type or child_task_type}') "
                        f"starts at '{expected_agent}', not '{child_initial_agent}'. "
                        f"task_type='{expected_task_type or child_task_type}' must use '{expected_agent}'."
                    )

                hydrated_task = dict(canonical_task)
                ctx["current_build_task"] = hydrated_task

            hydrated_workflow = dict(workflow)
            hydrated_workflow["context_variables"] = ctx
            child_workflows.append(hydrated_workflow)

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
            if child_workflows:
                context_variables.set("app_child_workflows", child_workflows)
            _logger.info(
                "Cached AppBuildPlan: kind=%s pages=%d entities=%d packs=%d build_tasks=%d integrations=%d child_workflows=%d",
                app_kind,
                len(pages),
                len(entities),
                len(capability_packs),
                len(build_tasks),
                len(external_integrations),
                len(child_workflows),
            )
        except Exception as exc:
            _logger.error("Failed to cache AppBuildPlan: %s", exc)
            return f"Error caching AppBuildPlan: {exc}"
    else:
        _logger.warning("context_variables not available or missing 'set' method")

    child_workflows_line = f"\nChild workflows: {len(child_workflows)}" if child_workflows else ""
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
        f"{child_workflows_line}"
    )


__all__ = ["app_build_plan"]
