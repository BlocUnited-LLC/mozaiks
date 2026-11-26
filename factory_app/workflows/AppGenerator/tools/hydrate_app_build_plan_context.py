"""Hydrate AppGenerator task-batch context from a preloaded AppBuildPlan."""

from __future__ import annotations

from typing import Any

from factory_app.workflows._shared.revision_app_source import (
    read_bound_revision_files,
    revision_baseline_required,
)
from factory_app.workflows.AppGenerator.tools.app_build_plan import (
    _apply_reviewed_page_writes,
    app_build_plan,
)
from factory_app.workflows.AppGenerator.tools.app_plan_review import (
    _clear_plan,
    _missing_revision_page_tasks,
)
from factory_app.workflows.AppGenerator.tools.hydrate_app_revision_context import (
    bind_retained_feature_login,
    discard_executable_plan,
)
from mozaiksai.core.workflow.generator_support.code_files import planned_page_path
from mozaiksai.core.workflow.generator_support.design_access_policy import (
    resolve_reviewed_page_writes,
)
from mozaiksai.core.workflow.generator_support.module_revision import bind_legacy_read_timestamps


def _context_data(context_variables: Any) -> dict[str, Any]:
    if context_variables is None:
        return {}
    if isinstance(context_variables, dict):
        return context_variables
    data = getattr(context_variables, "data", None)
    if isinstance(data, dict):
        return data
    if hasattr(context_variables, "to_dict"):
        try:
            value = context_variables.to_dict()
            return value if isinstance(value, dict) else {}
        except Exception:
            return {}
    return {}


async def hydrate_app_build_plan_context(context_variables: Any = None) -> dict[str, Any]:
    data = dict(_context_data(context_variables))
    plan = data.get("app_build_plan")
    if not isinstance(plan, dict):
        return {"status": "skipped", "reason": "missing_app_build_plan"}

    existing_items = data.get("app_task_batch_items")
    if isinstance(existing_items, list) and existing_items:
        rebound = dict(plan)
        try:
            bindings = data.get("retained_workflow_page_bindings") or {}
            if bindings:
                pages = [dict(page) for page in plan.get("pages") or []]
                _apply_reviewed_page_writes([page for page in pages if planned_page_path(page) in bindings], data)
                rebound["pages"] = pages
            bind_legacy_read_timestamps(rebound, data)
            bind_retained_feature_login(rebound, data)
        except ValueError:
            discard_executable_plan(context_variables)
            raise
        if rebound != plan:
            if isinstance(context_variables, dict):
                context_variables["app_build_plan"] = rebound
            else:
                context_variables.set("app_build_plan", rebound)
        return {"status": "skipped", "reason": "task_batch_items_already_present"}

    if hasattr(context_variables, "set"):
        _clear_plan(context_variables)
    marked = {
        planned_page_path(page) for page in plan.get("pages") or []
        if isinstance(page, dict) and "retained_ask_context" in page
    }
    retained_asks: dict[str, list[dict[str, Any]]] = {}
    if revision_baseline_required(data) and data.get("design_docs_build_artifact") and resolve_reviewed_page_writes(data):
        # A plan without its dispatch rows cannot prove that an unmarked page was
        # an explicit coordinated edit. Recover additive duties from the accepted
        # source, or leave AppPlanAgent to produce a fresh review.
        source = await read_bound_revision_files(context_variables)
        tasks = plan.get("build_tasks") or []
        without_page_owners = {**plan, "build_tasks": [task for task in tasks if task.get("task_type") != "page_bundle"]}
        baseline = {**data, "generated_files": source}
        missing = _missing_revision_page_tasks(without_page_owners, baseline, retained_asks)
        page_paths = {
            path for task in tasks if task.get("task_type") == "page_bundle" for path in task.get("owned_paths") or []
        }
        if not set(missing) <= page_paths:
            return {"status": "skipped", "reason": "revision_page_plan_requires_review"}
    if marked != retained_asks.keys() or (marked and not data.get("app_plan_ready")):
        return {"status": "skipped", "reason": "revision_page_plan_requires_review"}
    result = app_build_plan(AppBuildPlan=plan, context_variables=context_variables, _retained_page_asks=retained_asks)
    hydrated_items = _context_data(context_variables).get("app_task_batch_items")
    return {
        "status": "hydrated",
        "task_count": len(hydrated_items) if isinstance(hydrated_items, list) else 0,
        "message": result,
    }


__all__ = ["hydrate_app_build_plan_context"]

