import json
import logging
from pathlib import PurePosixPath
from typing import Annotated, Any, Dict, List, Optional

from autogen.tools.dependency_injection import Field

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
_CLIENT_SUFFIX = "_client.py"
_ALLOWED_TASK_TYPES = {
    "service_foundation",
    "module_contract",
    "persistence_contract",
    "data_models",
    "business_services",
    "api_surface",
    "page_bundle",
    "agent_backend_integration",
    "control_plane_pack",
    "pack_overlay",
}
_CANONICAL_INITIAL_AGENTS = {
    "service_foundation": "ConfigMiddlewareAgent",
    "module_contract": "ConfigMiddlewareAgent",
    "persistence_contract": "DatabaseAgent",
    "data_models": "ModelAgent",
    "business_services": "ServiceAgent",
    "control_plane_pack": "ControlPlaneAgent",
    "pack_overlay": "ConfigMiddlewareAgent",
    "api_surface": "ControllerAgent",
    "page_bundle": "AppSchemaAgent",
}
_SURFACE_KIND_ALLOWED_TASK_TYPES: dict[str, frozenset[str]] = {
    "external_integration": frozenset({"api_surface", "service_foundation", "agent_backend_integration"}),
    "control_plane": frozenset({"control_plane_pack"}),
    "ui_only": frozenset({"page_bundle"}),
    "framework_pack": frozenset({"pack_overlay"}),
}
_WORKFLOW_SURFACE_KIND = "workflow"
_MODULE_LOCAL_TASK_TYPES = frozenset({"module_contract", "data_models", "business_services"})


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


def _normalize_context_variables(value: Any) -> Optional[Dict[str, Any]]:
    if value is None:
        return None
    if isinstance(value, dict):
        return dict(value)
    if not isinstance(value, list):
        return None
    normalized: Dict[str, Any] = {}
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


def _unwrap_app_build_plan_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
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


def _task_sort_key(task: Dict[str, Any]) -> tuple[int, str]:
    task_id = str(task.get("task_id") or "")
    return (0 if task_id else 1, task_id)


def _infer_pack_id_from_integration_path(path: str) -> Optional[str]:
    """
    Extract the hosted pack id from an app-bundle-relative
    services/integrations/{pack_id}_client.py path.

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


def _infer_module_id_from_owned_paths(task: Dict[str, Any]) -> Optional[str]:
    module_ids: set[str] = set()
    for path in _normalized_owned_paths(task):
        parts = PurePosixPath(path).parts
        if len(parts) >= 2 and parts[0] == "modules" and parts[1]:
            module_ids.add(parts[1])
    if len(module_ids) == 1:
        return next(iter(module_ids))
    return None


def _normalize_build_task_identity(task: Dict[str, Any]) -> Dict[str, Any]:
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
                    f"'{normalized_capability_pack_id}'. Hosted pack adapters must use the app-bundle-relative "
                    "services/integrations/ lane, which assembles under app/services/integrations/."
                )
            if any(path.replace("\\", "/").startswith(_ADAPTERS_PREFIX) for path in owned_paths):
                raise ValueError(
                    "Build task "
                    f"'{task_id}' owns services/adapters/ for hosted pack "
                    f"'{normalized_capability_pack_id}'. Hosted pack adapters must be thin API clients under "
                    "app-bundle-relative services/integrations/."
                )
            if task_type == "service_foundation":
                raise ValueError(
                    "Build task "
                    f"'{task_id}' uses service_foundation for hosted pack "
                    f"'{normalized_capability_pack_id}'. Hosted packs must use api_surface adapters under "
                    "app-bundle-relative services/integrations/."
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

        # Hosted-pack adapter tasks must declare capability_pack_id so that template
        # expansion (resolve_hosted_pack_templates) can locate the correct pack template.
        # This check fires only when the path pattern matches a known hosted pack
        # (e.g. services/integrations/mozaikspay_client.py -> inferred pack id "mozaikspay").
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


def _validate_carry_forward_decisions(
    decisions: List[Dict[str, Any]],
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
    build_tasks: List[Dict[str, Any]],
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
        Optional[Dict[str, Any]],
        Field(description="Canonical app build plan emitted by AppPlanAgent."),
    ],
    context_variables: Annotated[
        Optional[Any],
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
    capability_packs = _normalize_object_list(AppBuildPlan.get("capability_packs"))
    external_integrations = _normalize_object_list(AppBuildPlan.get("external_integrations"))
    build_tasks = sorted(
        [
            _normalize_build_task_identity(task)
            for task in _normalize_object_list(AppBuildPlan.get("build_tasks"))
        ],
        key=_task_sort_key,
    )
    data_contract = AppBuildPlan.get("data_contract")
    pending_schema_migration = AppBuildPlan.get("pending_schema_migration")
    generation_order = _normalize_string_list(AppBuildPlan.get("generation_order"))
    agent_backend_required = bool(AppBuildPlan.get("agent_backend_required", False))
    carry_forward_decisions = _normalize_object_list(AppBuildPlan.get("carry_forward_decisions"))

    hosted_pack_ids = frozenset(
        str(p.get("capability_pack_id") or "").strip()
        for p in capability_packs
        if isinstance(p, dict) and p.get("capability_source") == "hosted_pack"
    ) - {""}
    _validate_build_tasks(build_tasks, hosted_pack_ids=hosted_pack_ids)

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

    task_batch_items: List[Dict[str, Any]] = []
    normalized_build_tasks: List[Dict[str, Any]] = []
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
