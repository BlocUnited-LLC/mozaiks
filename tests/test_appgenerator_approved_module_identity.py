"""Approved subscription action targets survive AppGenerator plan review."""

from __future__ import annotations

import pytest

from factory_app.workflows.AppGenerator.tools.app_plan_review import review_app_build_plan
from mozaiksai.core.workflow.agents.factory import ContextVariablesBridge
from mozaiksai.core.workflow.context.frozen import detach
from tests.test_app_plan_task_identity_repair import _live_plan
from tests.test_continuous_deterministic_materialization import _load_models


@pytest.fixture(autouse=True)
def _load_factory_contracts():
    _load_models()


@pytest.mark.parametrize("draft_module_id", ["task_management", "crud_pack"])
def test_approved_task_management_identity_and_actions_reach_workers(draft_module_id):
    plan, context = _live_plan(monetized=False, repeated_ids=False)
    assert isinstance(context, ContextVariablesBridge)
    approved_actions = ["create_task", "list_tasks", "edit_task", "view_dashboard"]
    design = detach(context.get("design_surface_map"))
    design["surfaces"][0]["owned_mutations"] = approved_actions
    context.set("design_surface_map", design)
    module = plan["capability_packs"][0]
    module["capability_pack_id"] = draft_module_id
    module["operations"] = []
    for task in plan["build_tasks"]:
        if task["capability_pack_id"] == "task_management":
            task["capability_pack_id"] = draft_module_id
            task["owned_paths"] = [
                path.replace("modules/task_management/", f"modules/{draft_module_id}/")
                for path in task["owned_paths"]
            ]

    result = review_app_build_plan(AppBuildPlan=plan, context_variables=context)

    assert result["outcome"] == "ready", result
    cached = detach(context.get("app_build_plan"))
    module = next(pack for pack in cached["capability_packs"] if pack["surface_id"] == "task_management")
    assert module["capability_pack_id"] == "task_management"
    assert module["operations"] == approved_actions
    module_tasks = [
        task for task in cached["build_tasks"]
        if task["task_type"] in {"module_contract", "data_models", "business_services"}
    ]
    assert len(module_tasks) == 3
    for task in module_tasks:
        assert task["surface_id"] == task["capability_pack_id"] == "task_management"
        assert all(path.startswith("modules/task_management/") for path in task["owned_paths"])
    queued = detach(context.get("app_task_batch_items"))
    for item in queued:
        task = item["current_build_task"]
        if task["task_type"] == "module_contract":
            assert item["surface_id"] == task["surface_id"] == "task_management"
            assert item["capability_pack_id"] == task["capability_pack_id"] == "task_management"
            assert all(f"`{action}`" in task["initial_message"] for action in approved_actions)


def test_unapproved_module_identity_reports_the_closed_valid_inventory():
    plan, context = _live_plan(monetized=False, repeated_ids=False)
    plan["capability_packs"][0]["surface_id"] = "tasks"

    result = review_app_build_plan(AppBuildPlan=plan, context_variables=context)

    assert result["outcome"] == "needs_revision", result
    assert "unapproved surface 'tasks'" in result["error"]
    assert "Valid approved surface_ids: ['task_management']" in result["error"]
    assert not context.get("app_task_batch_items")
