"""Replay the omitted-page traversal and erase all derivable plan structure.

The fixture's model payload is the unchanged response captured three times in
the 2026-09-26 live traversal. Its context is a projection of the stored approved
inputs; no database, credentials, or model calls are needed to replay review.
"""

from __future__ import annotations

import json
from collections import Counter
from copy import deepcopy
from pathlib import Path

import pytest

from factory_app.workflows.AppGenerator.tools.app_plan_review import (
    review_app_build_plan,
    validate_plan_coverage,
    validate_plan_dependencies,
    validate_plan_origins,
)
from mozaiksai.core.workflow.agents.factory import ContextVariablesBridge
from mozaiksai.core.workflow.context.frozen import detach
from tests.test_app_plan_closed_inventory import _approved_plan
from tests.test_app_plan_managed_facade_repair import _capability, _task
from tests.test_continuous_deterministic_materialization import _load_models

FIXTURE = Path(__file__).parent / "fixtures/appplan_monetized_missing_structure.json"
MODULE_KINDS = {"module_contract", "data_models", "business_services"}
PAGE_PATHS = {
    "app.json", "ui/pages/dashboard.yaml", "ui/pages/tasks.yaml",
    "ui/pages/auth.yaml", "ui/pages/pricing.yaml", "ui/pages/billing.yaml",
    "ui/pages/usage.yaml",
}


@pytest.fixture(autouse=True)
def _load_factory_contracts():
    _load_models()


def _live_inputs():
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    values = fixture["context"]
    values["app_plan_attempts"] = 0
    for pack in values["capability_packs"]:
        pack["pack_source_path"] = str(
            Path(__file__).resolve().parents[1] / "factory_app/build_context" / pack["id"]
        )
    return fixture["plan"], ContextVariablesBridge(values)


def _assert_complete_review(plan, context):
    assert isinstance(context, ContextVariablesBridge)
    original = deepcopy(plan)
    result = review_app_build_plan(AppBuildPlan=plan, context_variables=context)
    assert result["outcome"] == "ready", result
    assert plan == original
    cached = detach(context.get("app_build_plan"))
    validate_plan_coverage(cached, context)
    validate_plan_origins(cached, context)
    validate_plan_dependencies(cached, context)
    tasks = cached["build_tasks"]
    ids = [task["task_id"] for task in tasks]
    assert len(ids) == len(set(ids))
    paths = Counter(path for task in tasks for path in task["owned_paths"])
    assert all(count == 1 for count in paths.values())
    page_tasks = [task for task in tasks if task["task_type"] == "page_bundle"]
    assert PAGE_PATHS <= {path for task in page_tasks for path in task["owned_paths"]}
    assert all(task["initial_agent"] == "AppSchemaAgent" for task in page_tasks)
    assert "ui/pages/user_authentication.yaml" not in paths
    for module in ("tasks", "auth", "billing_portal"):
        trio = [task for task in tasks if task["capability_pack_id"] == module and task["task_type"] in MODULE_KINDS]
        assert Counter(task["task_type"] for task in trio) == dict.fromkeys(MODULE_KINDS, 1)
    subscription = [task for task in tasks if task["task_type"] == "subscription_config"]
    assert len(subscription) == 1
    assert subscription[0]["owned_paths"] == ["config/subscriptions.yaml"]
    queued = detach(context.get("app_task_batch_items"))
    assert {task["task_id"] for task in queued} == set(ids)
    assert all(set(task["depends_on"]) <= set(ids) for task in tasks)
    return cached


def test_exact_live_missing_page_bundle_plan_passes_review():
    plan, context = _live_inputs()
    assert len(plan["build_tasks"]) == 4
    assert all(task["task_type"] != "page_bundle" for task in plan["build_tasks"])
    _assert_complete_review(plan, context)


@pytest.mark.parametrize("omit_capabilities", [False, True], ids=["wrong_labels", "no_capabilities"])
def test_minimal_judgment_plan_constructs_all_omitted_structure(omit_capabilities):
    plan, context = _live_inputs()
    plan["build_tasks"] = []
    plan["generation_order"] = []
    plan["capability_packs"] = []
    if not omit_capabilities:
        for surface in detach(context.get("design_surface_map"))["surfaces"]:
            capability = _capability(surface["surface_id"], entities=surface["primary_entities"])
            capability.update(
                capability_pack_id=f"wrong_{surface['surface_id']}_label",
                operations=surface["owned_mutations"],
            )
            plan["capability_packs"].append(capability)

    # Four authored pages retain judgment about layout and purpose. The other
    # two approved pages, every worker task, module trio, subscription task,
    # managed-provider entry, and all dependency edges are absent.
    assert len(plan["pages"]) == 4
    cached = _assert_complete_review(plan, context)
    assert {"tasks", "auth", "billing_portal", "mozaikspay"} <= {
        pack["capability_pack_id"] for pack in cached["capability_packs"]
    }


def test_complete_correct_plan_passes_unchanged():
    # Complete the independent prior-PR fixture explicitly, without using the
    # repair under test to manufacture its own supposedly correct input.
    plan, context = _approved_plan(monetized=False)
    assert isinstance(context, ContextVariablesBridge)
    tasks = {task["task_type"]: task for task in plan["build_tasks"]}
    tasks["module_contract"]["initial_message"] = "Define create_task and list_tasks."
    tasks["data_models"]["depends_on"] = ["6", "persistence_contract"]
    tasks["business_services"]["depends_on"] = ["6", "7", "persistence_contract"]
    tasks["page_bundle"]["depends_on"] = ["6"]
    tasks["page_bundle"]["owned_paths"] = [
        "brand/theme_config.json", "app.json", "ui/pages/dashboard.yaml", "ui/pages/tasks.yaml",
    ]
    plan["build_tasks"] = sorted(plan["build_tasks"], key=lambda task: task["task_id"])
    model = _load_models()["AppBuildPlan"]
    complete = model.model_validate(plan).model_dump(mode="json", exclude_none=True)
    expected = model.model_validate(complete).model_dump(mode="json")
    original = deepcopy(complete)
    validate_plan_coverage(complete, context)
    validate_plan_origins(complete, context)
    validate_plan_dependencies(complete, context)

    result = review_app_build_plan(AppBuildPlan=complete, context_variables=context)

    assert result["outcome"] == "ready", result
    assert complete == original
    cached = detach(context.get("app_build_plan"))
    for key in ("build_tasks", "capability_packs", "pages", "generation_order"):
        assert cached[key] == expected[key]


def test_unapproved_surface_is_rejected_before_missing_structure_is_constructed():
    plan, context = _live_inputs()
    plan["build_tasks"] = []
    plan["capability_packs"].append(_capability("unapproved_surface"))
    original = deepcopy(plan)

    result = review_app_build_plan(AppBuildPlan=plan, context_variables=context)

    assert result["outcome"] == "needs_revision", result
    assert "unapproved surface 'unapproved_surface'" in result["error"]
    assert context.get("app_build_plan") is None
    assert not context.get("app_task_batch_items")
    assert context.get("app_plan_ready") is False
    assert plan == original


def test_explicit_custom_operations_are_preserved_without_inventing_read_actions():
    plan, context = _live_inputs()
    plan["build_tasks"] = []
    plan["capability_packs"] = [{
        **_capability("auth", entities=["User"]),
        "operations": ["login_user"],
    }]

    cached = _assert_complete_review(plan, context)

    auth = next(pack for pack in cached["capability_packs"] if pack["capability_pack_id"] == "auth")
    assert auth["operations"] == ["login_user"]


def test_selected_module_lane_constructs_missing_paths_and_labels():
    plan, context = _live_inputs()
    task = next(task for task in plan["build_tasks"] if task["surface_id"] == "tasks")
    task.update(capability_pack_id="wrong_label", surface_kind="app_policy", owned_paths=[])

    cached = _assert_complete_review(plan, context)

    constructed = next(item for item in cached["build_tasks"] if item["task_id"] == task["task_id"])
    assert constructed["capability_pack_id"] == "tasks"
    assert constructed["surface_kind"] == "module"
    assert constructed["owned_paths"] == ["modules/tasks/module.yaml"]


@pytest.mark.parametrize("identity", ["blank", "repeated_task_type"])
def test_missing_or_repeated_task_ids_compose_with_omitted_structure(identity):
    plan, context = _live_inputs()
    if identity == "blank":
        plan["build_tasks"][0]["task_id"] = ""
    else:
        for task in plan["build_tasks"]:
            task["task_id"] = task["task_type"]
    for task in plan["build_tasks"]:
        task["initial_agent"] = "WrongAgent"

    cached = _assert_complete_review(plan, context)

    assert all(task["task_id"] for task in cached["build_tasks"])
    assert all(task["initial_agent"] != "WrongAgent" for task in cached["build_tasks"])


@pytest.mark.parametrize("misuse", ["capability", "module_task", "service_file"])
def test_structural_page_scope_cannot_authorize_an_unapproved_module(misuse):
    plan, context = _live_inputs()
    if misuse == "capability":
        plan["capability_packs"].append({
            **_capability("page_bundle"), "surface_kind": "ui_only",
        })
    elif misuse == "module_task":
        plan["build_tasks"].append({
            **_task("unapproved_module", "module_contract", "ConfigMiddlewareAgent", None, ["modules/page_bundle/module.yaml"]),
            "surface_id": "page_bundle", "surface_kind": "ui_only",
        })
    else:
        plan["build_tasks"].append({
            **_task("unapproved_service", "page_bundle", "AppSchemaAgent", None, [*sorted(PAGE_PATHS), "services/integrations/unapproved_client.py"]),
            "surface_id": "page_bundle", "surface_kind": "ui_only",
        })

    result = review_app_build_plan(AppBuildPlan=plan, context_variables=context)

    assert result["outcome"] == "needs_revision", result
    assert "unapproved surface 'page_bundle'" in result["error"]
    assert context.get("app_build_plan") is None
    assert not context.get("app_task_batch_items")
