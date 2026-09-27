"""Undeclared action events retain their contract owner through real validation."""

from __future__ import annotations

from copy import deepcopy

import pytest
import yaml

from factory_app.workflows.AppGenerator.tools.app_plan_review import review_app_build_plan
from factory_app.workflows.AppGenerator.tools.app_validation import _app_runtime_load_result
from factory_app.workflows.AppGenerator.tools.repair_policy import prepare_bundle_repair
from factory_app.workflows.AppGenerator.tools.task_integrity import (
    RepairOwnershipError,
    planned_artifact_diagnostics,
    validate_repair_candidate,
)
from mozaiksai.core.workflow.agents.factory import ContextVariablesBridge
from mozaiksai.core.workflow.context.frozen import detach
from mozaiksai.core.workflow.task_batches import task_evidence_digest, task_inventory_digest
from tests.test_app_plan_managed_facade_repair import _plan_and_context
from tests.test_continuous_deterministic_materialization import _load_models
from tests.test_offline_generated_build_acceptance import _generated_build_files

MODULE_PATH = "modules/orders/module.yaml"
EVENT_PATH = "modules/orders/contracts/events.yaml"
EVENT_TYPE = "domain.orders.order_created"


def _event_files():
    files = _generated_build_files()
    module = yaml.safe_load(files[MODULE_PATH])
    module["actions"][1]["emits"] = [EVENT_TYPE]
    files[MODULE_PATH] = yaml.safe_dump(module, sort_keys=False)
    return files


def _context(files, *, event_owner=None):
    tasks = [
        {"task_id": "orders.contract", "task_type": "module_contract",
         "capability_pack_id": "orders", "initial_agent": "ConfigMiddlewareAgent",
         "owned_paths": [MODULE_PATH], "depends_on": []},
        {"task_id": "orders.service", "task_type": "business_services",
         "capability_pack_id": "orders", "initial_agent": "ServiceAgent",
         "owned_paths": ["modules/orders/backend/handler.py"], "depends_on": ["orders.contract"]},
    ]
    if event_owner:
        tasks.append(event_owner)
    plan = {"build_tasks": deepcopy(tasks)}
    results = {
        task["task_id"]: {"code_files": [
            {"filename": path, "content": files[path]}
            for path in task["owned_paths"] if path in files
        ]} for task in tasks
    }
    results["_meta"] = {
        "evidence_version": 1, "task_inventory_digest": task_inventory_digest(tasks),
        "input_digests": {"app_build_plan": task_evidence_digest(plan)},
        "accepted_output_digests": {
            task["task_id"]: task_evidence_digest(results[task["task_id"]]) for task in tasks
        },
    }
    return ContextVariablesBridge({
        "app_build_plan": plan, "app_task_batch_items": deepcopy(tasks),
        "app_task_batch_results": results, "generated_files": deepcopy(files),
    })


@pytest.mark.asyncio
async def test_real_runtime_event_failure_routes_to_contract_owner_without_mutating_evidence():
    files = _event_files()
    context = _context(files)
    before = {key: detach(context.get(key)) for key in (
        "app_build_plan", "app_task_batch_items", "app_task_batch_results", "generated_files",
    )}

    runtime = await _app_runtime_load_result(files)
    assert runtime["passed"] is False
    assert len(runtime["failed_tests"]) == 1
    diagnostic = runtime["failed_tests"][0]
    assert diagnostic["path"] == EVENT_PATH
    assert diagnostic["error"] == (
        f"module.yaml action 'create_order' emits undeclared event '{EVENT_TYPE}'"
    )
    repair = prepare_bundle_repair({"passed": False, "diagnostics": [diagnostic]}, context)

    assert repair["status"] == "needs_revision"
    assert repair["target_agent"] == "ConfigMiddlewareAgent"
    assert repair["active"]["task_id"] == "orders.contract"
    assert repair["active"]["allowed_paths"] == [MODULE_PATH, EVENT_PATH]
    assert EVENT_TYPE in repair["repair_request"]
    assert validate_repair_candidate(context, {EVENT_PATH: "approved event declaration"}) == {
        EVENT_PATH: "approved event declaration",
    }
    for key, value in before.items():
        assert detach(context.get(key)) == value


@pytest.mark.asyncio
async def test_declaring_event_resolves_loader_failure_without_weakening_loader():
    files = _event_files()
    failed = await _app_runtime_load_result(files)
    assert failed["passed"] is False
    files[EVENT_PATH] = yaml.safe_dump({"schema_version": "mozaiks.events.v1", "events": [{
        "type": EVENT_TYPE, "version": 1, "producer": "orders",
        "payload_schema": {"type": "object", "properties": {"order_id": {"type": "string"}}},
    }]})
    repaired = await _app_runtime_load_result(files)
    assert repaired["passed"] is True, repaired


@pytest.mark.parametrize("foreign_path", [
    "modules/other/contracts/events.yaml", "modules/orders/backend/handler.py",
])
def test_contract_repair_cannot_change_foreign_files(foreign_path):
    context = _context(_event_files())
    prepare_bundle_repair({"passed": False, "diagnostics": [{
        "path": EVENT_PATH, "error": "undeclared event",
    }]}, context)
    with pytest.raises(RepairOwnershipError, match="outside owned_paths"):
        validate_repair_candidate(context, {foreign_path: "foreign change"})


def test_existing_explicit_companion_owner_is_never_overridden():
    owner = {"task_id": "orders.events", "task_type": "module_contract",
             "capability_pack_id": "orders", "initial_agent": "ConfigMiddlewareAgent",
             "owned_paths": [EVENT_PATH], "depends_on": []}
    context = _context(_event_files(), event_owner=owner)
    repair = prepare_bundle_repair({"passed": False, "diagnostics": [{
        "path": EVENT_PATH, "error": "undeclared event",
    }]}, context)
    assert repair["active"]["task_id"] == "orders.events"
    assert repair["active"]["allowed_paths"] == [EVENT_PATH]


def test_plan_reserves_optional_events_path_without_requiring_zero_event_placeholder():
    _load_models()
    plan, context = _plan_and_context(monetized=False, collapsed=False)
    result = review_app_build_plan(AppBuildPlan=plan, context_variables=context)
    assert result["outcome"] == "ready", result
    cached = detach(context.get("app_build_plan"))
    tasks = cached["build_tasks"]
    contract = next(task for task in tasks if task["task_type"] == "module_contract")
    path = "modules/task_management/contracts/events.yaml"
    assert path in contract["owned_paths"]
    assert [task["task_id"] for task in tasks if path in task["owned_paths"]] == [contract["task_id"]]
    context.set("app_task_batch_results", {
        task["task_id"]: {"code_files": [{"filename": owned, "content": "accepted"}
                                        for owned in task["owned_paths"] if owned != path]}
        for task in tasks
    })
    files = {owned: "accepted" for task in tasks for owned in task["owned_paths"] if owned != path}
    assert not [item for item in planned_artifact_diagnostics(context, files) if item["path"] == path]


def test_unaccepted_contract_stays_blocked_even_for_its_optional_events():
    context = _context(_event_files())
    results = detach(context.get("app_task_batch_results"))
    del results["orders.contract"]
    context.set("app_task_batch_results", results)
    repair = prepare_bundle_repair({"passed": False, "diagnostics": [{
        "path": EVENT_PATH, "error": "undeclared event",
    }]}, context)
    assert repair["status"] == "blocked"
    assert repair["diagnostics"][0]["block_reason"] == "missing_accepted_task_evidence"
