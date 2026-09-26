"""Review repeated task-type IDs using the same runtime context as live builds."""

from __future__ import annotations

from copy import deepcopy

import pytest

from factory_app.workflows.AppGenerator.tools.app_plan_review import review_app_build_plan
from mozaiksai.core.workflow.context.frozen import detach
from tests.test_app_plan_managed_facade_repair import _plan_and_context, _task
from tests.test_continuous_deterministic_materialization import _load_models


@pytest.fixture(autouse=True)
def _load_factory_contracts():
    _load_models()


def _live_plan(*, monetized=True, repeated_ids=True):
    plan, context = _plan_and_context(monetized=monetized, collapsed=False)
    plan["build_tasks"].insert(0, _task(
        "persistence_contract", "persistence_contract", "DatabaseAgent", "task_management",
        ["data/contract.json"],
    ))
    if monetized:
        plan["build_tasks"].append({
            **_task("subscription_config", "subscription_config", "ConfigMiddlewareAgent", None, ["config/subscriptions.yaml"]),
            "surface_id": "subscription_contract", "surface_kind": "app_policy",
        })
        provider = next(task for task in plan["build_tasks"] if task["task_type"] == "api_surface")
        provider["surface_id"] = "billing_portal"
    if repeated_ids:
        by_original_id = {task["task_id"]: task["task_type"] for task in plan["build_tasks"]}
        for task in plan["build_tasks"]:
            task["depends_on"] = [by_original_id.get(dependency, dependency) for dependency in task["depends_on"]]
            task["task_id"] = task["task_type"]
    plan["generation_order"] = [task["task_id"] for task in plan["build_tasks"]]
    return plan, context


def _review(plan, context):
    result = review_app_build_plan(AppBuildPlan=plan, context_variables=context)
    assert result["outcome"] == "ready", result
    cached = detach(context.get("app_build_plan"))
    tasks = cached["build_tasks"]
    identifiers = [task["task_id"] for task in tasks]
    assert len(identifiers) == len(set(identifiers))
    assert all(set(task["depends_on"]) <= set(identifiers) for task in tasks)
    queued = detach(context.get("app_task_batch_items"))
    assert {task["task_id"] for task in queued} == set(identifiers)
    for task in queued:
        assert task["task_id"] == task["current_build_task_id"] == task["current_build_task"]["task_id"]
        assert task["depends_on"] == task["current_build_task"]["depends_on"]
    return cached


def test_reported_two_module_task_type_ids_pass_public_review():
    """The reported nine rows plus the page task needed for full traversal."""
    plan, context = _live_plan()
    cached = _review(plan, context)
    by_scope = {(task["capability_pack_id"], task["task_type"]): task for task in cached["build_tasks"]}
    for module_id in ("task_management", "billing_portal"):
        contract = by_scope[(module_id, "module_contract")]
        models = by_scope[(module_id, "data_models")]
        services = by_scope[(module_id, "business_services")]
        assert contract["task_id"] == f"{module_id}.module_contract"
        assert models["task_id"] == f"{module_id}.data_models"
        assert services["task_id"] == f"{module_id}.business_services"
        assert contract["task_id"] in models["depends_on"]
        assert {contract["task_id"], models["task_id"]} <= set(services["depends_on"])
        sibling = "billing_portal" if module_id == "task_management" else "task_management"
        assert not any(dependency.startswith(f"{sibling}.") for dependency in models["depends_on"] + services["depends_on"])
    pages = next(task for task in cached["build_tasks"] if task["task_type"] == "page_bundle")
    assert {"task_management.module_contract", "billing_portal.module_contract"} <= set(pages["depends_on"])


def test_unique_task_ids_survive_public_review_unchanged():
    plan, context = _live_plan(repeated_ids=False)
    before = {(task["capability_pack_id"], task["task_type"]): task["task_id"] for task in plan["build_tasks"]}
    cached = _review(plan, context)
    assert {(task["capability_pack_id"], task["task_type"]): task["task_id"] for task in cached["build_tasks"]} == before


def test_single_module_bare_task_type_ids_pass_public_review():
    plan, context = _live_plan(monetized=False)
    cached = _review(plan, context)
    assert all(task["task_id"] == task["task_type"] for task in cached["build_tasks"])


def test_identity_repair_leaves_unique_ids_untouched():
    from factory_app.workflows.AppGenerator.tools.app_plan_review import _repair_task_identities

    plan, context = _live_plan(repeated_ids=False)
    before = deepcopy(plan)
    assert _repair_task_identities(plan, context) == []
    assert plan == before


def test_page_dependencies_expand_all_owners_without_losing_explicit_foreign_edges():
    plan, context = _live_plan()
    billing_service = next(task for task in plan["build_tasks"] if task["task_type"] == "business_services" and task["capability_pack_id"] == "billing_portal")
    billing_service["task_id"] = "billing.services"
    task_service = next(task for task in plan["build_tasks"] if task["task_type"] == "business_services" and task["capability_pack_id"] == "task_management")
    task_service["depends_on"].append("billing.services")
    page = next(task for task in plan["build_tasks"] if task["task_type"] == "page_bundle")
    page["depends_on"] = ["module_contract", "data_models", "business_services", "billing.services"]

    cached = _review(plan, context)

    tasks = {task["task_id"]: task for task in cached["build_tasks"]}
    assert "billing.services" in tasks["business_services"]["depends_on"]
    assert {
        "task_management.module_contract", "billing_portal.module_contract",
        "task_management.data_models", "billing_portal.data_models",
        "business_services", "billing.services",
    } <= set(tasks["page_bundle"]["depends_on"])


@pytest.mark.parametrize("ambiguity", ["same_owner", "foreign_reference"])
def test_ambiguous_task_identity_is_rejected_without_queuing_workers(ambiguity):
    plan, context = _live_plan()
    if ambiguity == "same_owner":
        contract = next(task for task in plan["build_tasks"] if task["task_type"] == "module_contract")
        plan["build_tasks"].append(deepcopy(contract))
    else:
        provider = next(task for task in plan["build_tasks"] if task["task_type"] == "api_surface")
        provider["depends_on"] = ["module_contract"]

    result = review_app_build_plan(AppBuildPlan=plan, context_variables=context)

    assert result["outcome"] == "needs_revision", result
    assert not context.get("app_task_batch_items")
    assert context.get("app_plan_ready") is False


def test_qualified_name_collision_preserves_existing_task_and_scoped_dependencies():
    plan, context = _live_plan()
    plan["build_tasks"].append({
        **_task("task_management.module_contract", "service_foundation", "ConfigMiddlewareAgent", None, ["services/integrations/audit_client.py"]),
        "surface_id": "audit_service", "surface_kind": "external_integration",
    })

    cached = _review(plan, context)

    tasks = {task["task_id"]: task for task in cached["build_tasks"]}
    assert tasks["task_management.module_contract"]["task_type"] == "service_foundation"
    assert tasks["task_management.module_contract.2"]["task_type"] == "module_contract"
    assert "task_management.module_contract.2" in tasks["task_management.data_models"]["depends_on"]
    assert "task_management.module_contract" not in tasks["task_management.data_models"]["depends_on"]


def test_synthesized_coverage_task_cannot_reintroduce_an_existing_identity():
    plan, context = _live_plan(repeated_ids=False)
    plan["build_tasks"] = [task for task in plan["build_tasks"] if task["task_id"] != "7"]
    for task in plan["build_tasks"]:
        task["depends_on"] = [dependency for dependency in task["depends_on"] if dependency != "7"]
    plan["build_tasks"].append({
        **_task("task_task_management_data_models", "service_foundation", "ConfigMiddlewareAgent", None, ["services/integrations/audit_client.py"]),
        "surface_id": "audit_service", "surface_kind": "external_integration",
    })

    cached = _review(plan, context)

    tasks = {task["task_id"]: task for task in cached["build_tasks"]}
    assert tasks["task_task_management_data_models"]["task_type"] == "service_foundation"
    assert tasks["task_task_management_data_models.2"]["task_type"] == "data_models"
    assert "task_task_management_data_models.2" in tasks["8"]["depends_on"]


def test_task_references_in_plan_metadata_and_integration_needs_keep_their_scope():
    plan, context = _live_plan()
    plan["generation_order"] = ["design", "module_contract", "data_models", "module_contract", "publish"]
    plan["carry_forward_decisions"] = [{
        "module_id": module_id, "decision": "regenerate", "reason": "Regenerate approved module",
        "source": "planner", "affected_build_tasks": ["module_contract", "data_models", "business_services"],
    } for module_id in ("task_management", "billing_portal")]
    for task in plan["build_tasks"]:
        if task["task_type"] != "business_services":
            continue
        task["integration_needs"] = [{
            "service": "audit_service", "kind": "internal_service", "purpose": "Record module activity",
            "required_at": "runtime",
            "required_by": {"kind": kind, "id": "module_contract", "path": None},
        } for kind in ("task", "agent", "capability_pack")]

    cached = _review(plan, context)

    assert cached["generation_order"] == [
        "design", "task_management.module_contract", "billing_portal.module_contract",
        "task_management.data_models", "billing_portal.data_models", "publish",
    ]
    for decision in cached["carry_forward_decisions"]:
        assert decision["affected_build_tasks"] == [
            f"{decision['module_id']}.{kind}" for kind in ("module_contract", "data_models", "business_services")
        ]
    for task in cached["build_tasks"]:
        if task["task_type"] != "business_services":
            continue
        references = {need["required_by"]["kind"]: need["required_by"]["id"] for need in task["integration_needs"]}
        assert references == {
            "task": f"{task['capability_pack_id']}.module_contract",
            "agent": "module_contract", "capability_pack": "module_contract",
        }


def test_identity_repair_is_idempotent():
    from factory_app.workflows.AppGenerator.tools.app_plan_review import _repair_task_identities

    plan, context = _live_plan()
    assert _repair_task_identities(plan, context)
    repaired = deepcopy(plan)
    assert _repair_task_identities(plan, context) == []
    assert plan == repaired
