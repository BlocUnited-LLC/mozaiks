"""Hydrate AppGenerator task-batch context from a preloaded AppBuildPlan."""

from __future__ import annotations

from typing import Any, Dict

from factory_app.workflows.AppGenerator.tools.app_build_plan import app_build_plan


def _context_data(context_variables: Any) -> Dict[str, Any]:
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
    data = _context_data(context_variables)
    plan = data.get("app_build_plan")
    if not isinstance(plan, dict):
        return {"status": "skipped", "reason": "missing_app_build_plan"}

    existing_items = data.get("app_task_batch_items")
    if isinstance(existing_items, list) and existing_items:
        return {"status": "skipped", "reason": "task_batch_items_already_present"}

    result = app_build_plan(AppBuildPlan=plan, context_variables=context_variables)
    hydrated_items = _context_data(context_variables).get("app_task_batch_items")
    return {
        "status": "hydrated",
        "task_count": len(hydrated_items) if isinstance(hydrated_items, list) else 0,
        "message": result,
    }


__all__ = ["hydrate_app_build_plan_context"]

