"""Plan construction preserves approved actions without guessing read semantics."""

from __future__ import annotations

from typing import Any

import pytest

from factory_app.workflows.AppGenerator.tools.app_plan_review import review_app_build_plan
from mozaiksai.core.workflow.agents.factory import ContextVariablesBridge
from mozaiksai.core.workflow.context.frozen import detach
from tests.test_app_plan_review import _context, _plan
from tests.test_continuous_deterministic_materialization import _load_models


@pytest.fixture(autouse=True)
def _load_factory_contracts():
    _load_models()


def _approved_plan(operations: list[str]) -> tuple[dict[str, Any], ContextVariablesBridge]:
    plan, context = _plan(), _context()
    assert isinstance(context, ContextVariablesBridge)
    plan["capability_packs"][0]["operations"] = list(operations)
    contract = next(task for task in plan["build_tasks"] if task["task_type"] == "module_contract")
    contract["initial_message"] = "Emit the approved report actions."
    return plan, context


@pytest.mark.parametrize("operations", [
    ["create_report", "archive_report"],
    ["enumerate_visible_records", "publish_report"],
    [],
])
def test_public_review_does_not_invent_read_semantics(operations):
    plan, context = _approved_plan(operations)

    result = review_app_build_plan(AppBuildPlan=plan, context_variables=context)

    assert result["outcome"] == "ready", result
    cached = detach(context.get("app_build_plan"))
    assert cached["capability_packs"][0]["operations"] == operations
    contract = next(task for task in cached["build_tasks"] if task["task_type"] == "module_contract")
    assert "list_reports" not in contract["initial_message"]
    for operation in operations:
        assert f"`{operation}`" in contract["initial_message"]


@pytest.mark.parametrize("omit_capability", [False, True])
def test_approved_mutations_reach_existing_or_constructed_capability(omit_capability):
    plan, context = _approved_plan([])
    design = detach(context.get("design_surface_map"))
    approved_operations = ["create_report", "archive_report"]
    design["surfaces"][0]["owned_mutations"] = approved_operations
    context.set("design_surface_map", design)
    if omit_capability:
        plan["capability_packs"] = []
    plan["build_tasks"] = [
        task for task in plan["build_tasks"] if task["task_type"] == "page_bundle"
    ]
    plan["build_tasks"][0]["depends_on"] = []

    result = review_app_build_plan(AppBuildPlan=plan, context_variables=context)

    assert result["outcome"] == "ready", result
    cached = detach(context.get("app_build_plan"))
    assert cached["capability_packs"][0]["operations"] == approved_operations
    contract = next(task for task in cached["build_tasks"] if task["task_type"] == "module_contract")
    assert "list_reports" not in contract["initial_message"]
    assert all(f"`{operation}`" in contract["initial_message"] for operation in approved_operations)
    queued = detach(context.get("app_task_batch_items"))
    queued_contract = next(task for task in queued if task["task_type"] == "module_contract")
    assert queued_contract["current_build_task"]["initial_message"] == contract["initial_message"]
