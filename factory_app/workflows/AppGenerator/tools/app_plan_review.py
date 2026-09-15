"""Review model-authored plans before the existing task executor sees them."""

from __future__ import annotations

import logging
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

logger = logging.getLogger(__name__)


def _clear_plan(context: Any) -> None:
    context.set("app_plan_ready", False)
    context.set("app_build_plan", None)
    context.set("app_task_batch_items", [])
    context.set("app_task_batch_status", None)


def _repair_plan(plan: dict[str, Any], context: Any) -> list[str]:
    """Fix plan fields whose correct value the validator already knows.

    A live build of a habit tracker failed three times and killed the run on
    four errors that were one mistake: the planner named its module after the
    product category it came from ("crud_pack") instead of the approved surface
    ("habits_module"), and the validator's own message said so - "must match its
    approved surface_id". Every downstream ownership check then keyed off the
    wrong name and failed too.

    Rejecting a plan over a name the system can derive, and asking the model to
    guess it again, costs three LLM rounds and then the whole run. Where the
    approved design already states the answer, apply it and say so. Anything not
    derivable is still rejected by the validators, unchanged.
    """
    repairs: list[str] = []
    design = detach(context.get("design_surface_map")) or {}
    packs = plan.get("capability_packs") or []

    approved = {
        surface["surface_id"]: surface
        for surface in design.get("surfaces") or []
        if surface.get("owner") == "app" and surface.get("surface_kind") == "module"
    }

    # 1. The approved surface_id is the module's identity. Adopt it.
    renames: dict[str, str] = {}
    for pack in packs:
        surface_id = pack.get("surface_id")
        surface = approved.get(surface_id)
        if surface is None or pack.get("surface_kind") != "module":
            continue
        if pack.get("capability_source") not in {"generated_module", None, ""}:
            continue
        pack.setdefault("capability_source", "generated_module")
        current = _pack_id_from_descriptor(pack)
        if current != surface_id:
            renames[str(current)] = str(surface_id)
            pack["capability_pack_id"] = surface_id
            repairs.append(f"capability_pack_id {current!r} -> approved surface_id {surface_id!r}")
        approved_entities = list(surface.get("primary_entities") or [])
        if set(pack.get("primary_entities") or []) != set(approved_entities):
            pack["primary_entities"] = approved_entities
            repairs.append(f"{surface_id}: primary_entities -> approved {approved_entities}")

    # A capability sourced from a provider that does not exist, for a surface the
    # design never approved, is invented scope. The live run produced exactly
    # one: a "notifications_pack" declared managed_capability on a habit tracker
    # whose approved design has no notifications surface and whose concept never
    # mentioned them. Three attempts produced it every time, so feedback does not
    # remove it - and one unapproved capability fails the whole plan.
    available = _context_available_pack_map(context)
    surviving: list[dict[str, Any]] = []
    dropped: set[str] = set()
    for pack in packs:
        pack_id = str(_pack_id_from_descriptor(pack))
        source = pack.get("capability_source")
        unbacked = source in {"managed_capability", "framework_pack", "operator_pack"} and pack_id not in available
        if unbacked and pack.get("surface_id") not in approved:
            dropped.add(pack_id)
            repairs.append(
                f"dropped {pack_id!r}: {source} with no registered provider and no approved surface"
            )
            continue
        surviving.append(pack)

    if dropped:
        plan["capability_packs"] = surviving
        kept_tasks = []
        for task in plan.get("build_tasks") or []:
            if str(task.get("capability_pack_id") or "") in dropped:
                repairs.append(f"dropped task {task.get('task_id')!r}: built a capability that was dropped")
                continue
            kept_tasks.append(task)
        dropped_ids = {
            str(t.get("task_id"))
            for t in (plan.get("build_tasks") or [])
            if str(t.get("capability_pack_id") or "") in dropped
        }
        for task in kept_tasks:
            depends = [d for d in (task.get("depends_on") or []) if str(d) not in dropped_ids]
            if len(depends) != len(task.get("depends_on") or []):
                task["depends_on"] = depends
                repairs.append(f"{task.get('task_id')}: dropped dependency on a removed task")
        plan["build_tasks"] = kept_tasks
        packs = surviving

    if not renames:
        return repairs

    # 2. Tasks must agree with the pack they build, including the directory
    #    their owned paths sit under - the rule is module_ids == {pack_id}.
    pack_surface = {_pack_id_from_descriptor(p): p.get("surface_id") for p in packs}
    for task in plan.get("build_tasks") or []:
        pack_id = str(task.get("capability_pack_id") or "")
        new_pack_id = renames.get(pack_id)
        module_dirs = {
            path.split("/")[1]
            for path in _normalized_owned_paths(task)
            if path.startswith("modules/") and len(path.split("/")) > 1
        }
        if new_pack_id is None and len(module_dirs) == 1:
            # The directory is the other way of naming the same module.
            new_pack_id = renames.get(next(iter(module_dirs)))
        if new_pack_id is None:
            continue

        if task.get("capability_pack_id") != new_pack_id:
            task["capability_pack_id"] = new_pack_id
            repairs.append(f"{task.get('task_id')}: capability_pack_id -> {new_pack_id!r}")
        surface_id = pack_surface.get(new_pack_id)
        if surface_id and task.get("surface_id") != surface_id:
            task["surface_id"] = surface_id
            repairs.append(f"{task.get('task_id')}: surface_id -> {surface_id!r}")

        stale = {d for d in module_dirs if d != new_pack_id}
        if stale:
            paths = list(task.get("owned_paths") or [])
            for index, path in enumerate(paths):
                parts = str(path).split("/")
                if len(parts) > 1 and parts[0] == "modules" and parts[1] in stale:
                    parts[1] = new_pack_id
                    paths[index] = "/".join(parts)
            task["owned_paths"] = paths
            repairs.append(
                f"{task.get('task_id')}: owned paths moved from modules/{sorted(stale)[0]}/ to modules/{new_pack_id}/"
            )

    return repairs

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
        for repair in _repair_plan(plan, context_variables):
            logger.info("[AppGenerator] plan repaired: %s", repair)
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
        # Without ok=False the runtime's failure detector reads a rejection as
        # success, so three rejected plans logged as three clean completions and
        # the real validator errors never reached the log at all.
        return {"ok": False, "outcome": outcome, "error": feedback}
    except BaseException:
        _clear_plan(context_variables)
        raise
    context_variables.set("app_plan_feedback", "")
    context_variables.set("app_plan_outcome", "ready")
    return {"outcome": "ready", "task_count": len(cached["build_tasks"])}
