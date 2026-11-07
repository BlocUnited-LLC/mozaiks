"""Unproven synthetic requests cannot justify weakening generated business guards."""
from __future__ import annotations

import time
from copy import deepcopy

import pytest

from factory_app.workflows.AppGenerator.tools import app_runtime_smoke as smoke
from factory_app.workflows.AppGenerator.tools.app_validation import _write_files_to_dir
from factory_app.workflows.AppGenerator.tools.repair_policy import prepare_bundle_repair
from factory_app.workflows.AppGenerator.tools.task_integrity import (
    RepairOwnershipError,
    validate_repair_candidate,
)
from mozaiksai.core.workflow.agents.factory import ContextVariablesBridge
from mozaiksai.core.workflow.context.frozen import detach
from mozaiksai.core.workflow.task_batches import task_evidence_digest, task_inventory_digest
from tests.test_app_runtime_smoke_fixtures import FIXTURE_PATH, MODULE_YAML, fixture_bundle

SERVICE = "modules/task_management/backend/service.py"
REPO = "modules/task_management/backend/repo.py"
ACTION = "approve_task"


def _context(files):
    tasks = [
        {"task_id": "task_management.contract", "task_type": "module_contract",
         "capability_pack_id": "task_management", "initial_agent": "ConfigMiddlewareAgent",
         "owned_paths": [MODULE_YAML], "depends_on": []},
        {"task_id": "task_management.service", "task_type": "business_services",
         "capability_pack_id": "task_management", "initial_agent": "ServiceAgent",
         "owned_paths": [SERVICE, REPO], "depends_on": ["task_management.contract"]},
    ]
    plan = {"build_tasks": deepcopy(tasks)}
    results = {task["task_id"]: {"code_files": [
        {"filename": path, "content": files[path]} for path in task["owned_paths"]
    ]} for task in tasks}
    results["_meta"] = {
        "evidence_version": 1, "task_inventory_digest": task_inventory_digest(tasks),
        "input_digests": {"app_build_plan": task_evidence_digest(plan)},
        "accepted_output_digests": {task["task_id"]: task_evidence_digest(results[task["task_id"]]) for task in tasks},
    }
    return ContextVariablesBridge({"app_build_plan": plan, "app_task_batch_items": deepcopy(tasks),
                                   "app_task_batch_results": results, "generated_files": deepcopy(files)})


def _run(tmp_path, *, declared):
    files = fixture_bundle()
    if not declared:
        files.pop(FIXTURE_PATH)
    root = tmp_path / "app"
    _write_files_to_dir(root, files)
    run = smoke._SmokeRun(root, None, "unused", lambda *_args, **_kwargs: None)
    smoke._probe_contracts(run)
    return run, files


def _result(run):
    return smoke._summary([outcome.to_dict() for outcome in run.outcomes], {}, started=time.monotonic())


@pytest.mark.asyncio
@pytest.mark.parametrize(("declared", "status", "owner"), [
    (False, 400, "ConfigMiddlewareAgent"), (False, 403, "ConfigMiddlewareAgent"),
    (False, 404, "ConfigMiddlewareAgent"), (True, 404, "ServiceAgent"),
    (False, 500, "ServiceAgent"), (True, 500, "ServiceAgent"), (False, 0, "ServiceAgent"),
])
async def test_actual_role_probe_routes_unproven_inputs_before_implementation_repair(tmp_path, declared, status, owner):
    # The live a9dc4efe run returned 404 for synthetic approval/return IDs with no
    # fixtures. Exercise that exact observer-to-owned-repair boundary, not a tool-name heuristic.
    run, files = _run(tmp_path, declared=declared)
    for module in run.load.modules:
        for action in module.action_permissions_map:
            if action != ACTION:
                run.role_successes.add((module.name, action))
                run.role_denials_checked.add((module.name, action))
    calls = []

    async def snapshot():
        return {}

    async def request(method, module, action, principal, params):
        calls.append((action, principal.roles, params))
        if action == "create_task":
            return 201, {"item": {"task_id": "prepared-record"}}, "201"
        if not principal.roles:
            return 403, {"detail": {"error_code": "PERMISSION_DENIED"}}, "403 PERMISSION_DENIED"
        return status, {"detail": {"error_code": "RECORD_NOT_FOUND"}}, f"{status} RECORD_NOT_FOUND: Record not found."

    run._data_snapshot = snapshot
    run._request = request
    await smoke._remaining_role_checks(run, [])
    result = _result(run)
    assert result["passed"] is False
    assert len(result["failed_tests"]) == 1
    diagnostic = result["failed_tests"][0]
    expected_path = FIXTURE_PATH if owner == "ConfigMiddlewareAgent" else SERVICE
    assert diagnostic["path"] == expected_path
    assert diagnostic["input_provenance"] == {
        "source": "declared_fixture" if declared else "observer_default", "module": "task_management",
        "action": ACTION, "http_status": status, "fixture_input_required": owner == "ConfigMiddlewareAgent",
    }
    denial = next(item for item in result["results"] if item["check"].endswith("non_holder_denied"))
    assert denial["status"] == "passed" and denial["details"]["records_unchanged"] is True
    target = next(params for action, roles, params in calls if action == ACTION and roles)
    if declared:
        assert target["task_id"] == "prepared-record"
        assert calls[0][0] == "create_task"
    else:
        assert all(action != "create_task" for action, _roles, _params in calls)
    context = _context(files)
    before = {key: detach(context.get(key)) for key in (
        "app_build_plan", "app_task_batch_items", "app_task_batch_results", "generated_files",
    )}
    repair = prepare_bundle_repair({"passed": False, "diagnostics": result["failed_tests"]}, context)
    assert repair["target_agent"] == owner
    assert expected_path in repair["active"]["allowed_paths"]
    if owner == "ConfigMiddlewareAgent":
        assert "preserve the admitted action schema, authorization and business rules" in repair["repair_request"]
        with pytest.raises(RepairOwnershipError, match="outside owned_paths"):
            validate_repair_candidate(context, {SERVICE: "remove a prerequisite check"})
    for key, value in before.items():
        assert detach(context.get(key)) == value


@pytest.mark.asyncio
@pytest.mark.parametrize(("status", "expected_path"), [(400, FIXTURE_PATH), (500, SERVICE), (201, REPO)])
async def test_crud_positive_rejection_preserves_output_failure_owner(tmp_path, status, expected_path):
    run, _files = _run(tmp_path, declared=False)
    entity = smoke._contract_entities(run)[0]
    entity.actions = {"create": entity.actions["create"]}

    async def call(*args):
        return status, {"unexpected": "no record id"}, str(status)

    run.call = call
    await smoke._crud(run, entity)
    failure = _result(run)["failed_tests"][0]
    assert failure["check"].endswith("a_create") and failure["path"] == expected_path
    if status == 201:
        assert "input_provenance" not in failure


def test_malformed_declared_fixture_is_rejected_instead_of_treated_as_absent(tmp_path):
    files = fixture_bundle()
    files[FIXTURE_PATH] = "schema_version: mozaiks.module_smoke_fixtures.v1\nfixtures: wrong-type\n"
    root = tmp_path / "app"
    _write_files_to_dir(root, files)
    run = smoke._SmokeRun(root, None, "unused", lambda *_args, **_kwargs: None)
    with pytest.raises(ValueError):
        smoke._probe_contracts(run)
    assert not run.outcomes and not run.role_denials_checked


def test_existing_negative_storage_and_event_diagnostics_keep_exact_owners(tmp_path):
    run, _files = _run(tmp_path, declared=False)
    for check, path in [("role.task_management.approve_task.non_holder_denied", MODULE_YAML),
                        ("crud.task_management.tasks.b_get_isolated", REPO),
                        ("crud.task_management.tasks.get_missing", SERVICE),
                        ("event.task_management.approve_task.domain.task.updated", SERVICE)]:
        run.record(check, False, "Observed invariant failure", path=path)
    result = _result(run)
    assert [item["path"] for item in result["failed_tests"]] == [MODULE_YAML, REPO, SERVICE, SERVICE]
    assert all("input_provenance" not in item for item in result["failed_tests"])
