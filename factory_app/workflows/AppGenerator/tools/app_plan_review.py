"""Review model-authored plans before the existing task executor sees them."""

from __future__ import annotations

from typing import Annotated, Any

from factory_app.workflows.AppGenerator.tools.app_build_plan import (
    _context_available_pack_map,
    _normalized_owned_paths,
    _pack_id_from_descriptor,
    app_build_plan,
)
from mozaiksai.core.workflow.context.frozen import detach
from mozaiksai.core.workflow.generator_support.code_files import _page_file_stem
from mozaiksai.core.workflow.outputs.structured import load_workflow_structured_outputs


def _clear_plan(context: Any) -> None:
    context.set("app_plan_ready", False)
    context.set("app_build_plan", None)
    context.set("app_task_batch_items", [])
    context.set("app_task_batch_status", None)


def validate_plan_origins(plan: dict[str, Any], context: Any) -> None:
    available = _context_available_pack_map(context)
    packs = plan.get("capability_packs") or []
    errors: list[str] = []
    for pack in packs:
        pack_id = _pack_id_from_descriptor(pack)
        source = pack.get("capability_source")
        registered = available.get(pack_id)
        if source == "managed_capability" and not registered:
            errors.append(f"{pack_id}: managed_capability requires a registered provider pack; a product category is not a managed service")
        if source in {"framework_pack", "operator_pack"} and not registered:
            errors.append(f"{pack_id}: {source} requires an installed pack in capability_packs context. For app-owned code use generated_module with capability_pack_id=surface_id={pack.get('surface_id')!r}; product categories belong only in pack_type")
        if registered and source != registered.get("capability_source"):
            errors.append(f"{pack_id}: capability_source must match the registered pack ({registered.get('capability_source')})")

    design = detach(context.get("design_surface_map")) or {}
    for surface in design.get("surfaces") or []:
        if surface.get("owner") != "app" or surface.get("surface_kind") != "module":
            continue
        surface_id = surface["surface_id"]
        matching = [pack for pack in packs if pack.get("surface_id") == surface_id]
        if len(matching) != 1 or matching[0].get("surface_kind") != "module" or matching[0].get("capability_source") not in {"generated_module", "framework_pack", "operator_pack"}:
            errors.append(f"{surface_id}: the approved app-owned module requires exactly one module capability, normally generated_module; preserve its surface_id")
            continue
        pack = matching[0]
        if pack.get("capability_source") == "generated_module":
            if _pack_id_from_descriptor(pack) != surface_id:
                errors.append(f"{surface_id}: generated module capability_pack_id must match its approved surface_id, not the source product category")
            if set(pack.get("primary_entities") or []) != set(surface.get("primary_entities") or []):
                errors.append(f"{surface_id}: preserve the approved primary_entities: {surface.get('primary_entities') or []}")

    for task in plan.get("build_tasks") or []:
        module_ids = {path.split("/")[1] for path in _normalized_owned_paths(task) if path.startswith("modules/")}
        if not module_ids:
            continue
        pack_id = task.get("capability_pack_id")
        matching = [pack for pack in packs if _pack_id_from_descriptor(pack) == pack_id]
        if len(matching) != 1 or module_ids != {pack_id} or task.get("surface_id") != matching[0].get("surface_id"):
            errors.append(f"{task.get('task_id')}: module task capability_pack_id={pack_id!r}, surface_id={task.get('surface_id')!r}, path modules={sorted(module_ids)} must resolve to one declared module capability")

    if errors:
        raise ValueError("Plan ownership errors:\n- " + "\n- ".join(errors))


def validate_plan_coverage(plan: dict[str, Any], context: Any) -> None:
    tasks = plan.get("build_tasks") or []
    if not tasks:
        raise ValueError("A build plan must declare materializing build_tasks, not just a page or capability inventory")
    if context.get("build_mode") == "revision" or context.get("brownfield_build_path"):
        return

    errors: list[str] = []
    pages = plan.get("pages") or []
    planned_routes = [page.get("route") for page in pages]
    if len(set(planned_routes)) != len(planned_routes):
        errors.append("page routes must be unique")
    experience = detach(context.get("experience_spec")) or {}
    expected_pages = {(page["name"], page["route"]) for page in experience.get("pages") or []}
    if expected_pages and {(page["name"], page["route"]) for page in pages} != expected_pages:
        errors.append(f"pages must preserve the approved name/route inventory: {sorted(expected_pages)}")
    page_paths = [f"ui/pages/{_page_file_stem(page)}.yaml" for page in pages]
    if len(set(page_paths)) != len(page_paths):
        errors.append("page routes resolve to colliding materialized filenames")
    page_owned = {
        path for task in tasks if task.get("task_type") == "page_bundle"
        for path in _normalized_owned_paths(task)
    }
    missing = {"app.json", *page_paths} - page_owned
    if missing:
        errors.append(f"page_bundle/AppSchemaAgent must own all page files and app.json; missing {sorted(missing)}")
        errors.append(
            "Page paths are case-sensitive and use the materializer's lowercase route-derived stem, "
            f"not the display name. Required page paths: {page_paths}; received page_bundle paths: {sorted(page_owned)}. "
            "Replace differently cased filenames; do not add both spellings."
        )

    for pack in plan.get("capability_packs") or []:
        if pack.get("surface_kind") != "module" or pack.get("capability_source") != "generated_module":
            continue
        module_id = _pack_id_from_descriptor(pack)
        module_tasks = [task for task in tasks if task.get("capability_pack_id") == module_id]
        required = {
            "module_contract": {f"modules/{module_id}/module.yaml"},
            "data_models": {f"modules/{module_id}/backend/schemas.py"},
            "business_services": {f"modules/{module_id}/backend/{name}.py" for name in ("handler", "service")},
        }
        if pack.get("primary_entities"):
            required["business_services"].update(f"modules/{module_id}/backend/{name}.py" for name in ("repo", "policy"))
        if pack.get("user_data_scope") is True:
            required["business_services"].add(f"modules/{module_id}/backend/account_data_handler.py")
        for kind, paths in required.items():
            owned = {path for task in module_tasks if task.get("task_type") == kind for path in _normalized_owned_paths(task)}
            if paths - owned:
                errors.append(f"{module_id}/{kind} is incomplete; missing {sorted(paths - owned)}")
    if errors:
        raise ValueError("Incomplete build plan:\n- " + "\n- ".join(errors))


def review_app_build_plan(
    *,
    AppBuildPlan: Annotated[dict[str, Any] | None, "Complete AppBuildPlan output"] = None,
    context_variables: Annotated[Any | None, "Runtime-owned workflow context"] = None,
) -> dict[str, Any]:
    if context_variables is None:
        raise ValueError("Plan review requires runtime context")
    _clear_plan(context_variables)
    context_variables.set("app_plan_outcome", "blocked")
    attempts = context_variables.get("app_plan_attempts") or 0
    if type(attempts) is not int or attempts < 0:
        raise ValueError("Invalid runtime plan attempt counter")
    if attempts >= 3:
        return {"outcome": "blocked", "error": "Plan review attempt budget exhausted"}
    attempts += 1
    context_variables.set("app_plan_attempts", attempts)
    try:
        models, _ = load_workflow_structured_outputs("AppGenerator")
        plan = models["AppBuildPlan"].model_validate(detach(AppBuildPlan)).model_dump(mode="json")
        validate_plan_origins(plan, context_variables)
        validate_plan_coverage(plan, context_variables)
        app_build_plan(AppBuildPlan=plan, context_variables=context_variables)
        cached = detach(context_variables.get("app_build_plan"))
        if not context_variables.get("app_plan_ready") or not isinstance(cached, dict):
            raise RuntimeError("Validated plan was not cached")
        validate_plan_coverage(cached, context_variables)
    except ValueError as error:
        _clear_plan(context_variables)
        feedback = str(error)[:6000]
        context_variables.set("app_plan_feedback", feedback)
        outcome = "needs_revision" if attempts < 3 else "blocked"
        context_variables.set("app_plan_outcome", outcome)
        return {"outcome": outcome, "error": feedback}
    except BaseException:
        _clear_plan(context_variables)
        raise
    context_variables.set("app_plan_feedback", "")
    context_variables.set("app_plan_outcome", "ready")
    return {"outcome": "ready", "task_count": len(cached["build_tasks"])}
