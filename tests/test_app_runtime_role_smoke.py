"""Real HTTP/Mongo role admission, denial and ownership in the runtime smoke."""

from __future__ import annotations

import json
import os
from pathlib import Path
from uuid import uuid4

import pytest
import yaml

from factory_app.workflows.AppGenerator.tools import app_runtime_smoke as smoke
from factory_app.workflows.AppGenerator.tools.app_validation import _write_files_to_dir
from factory_app.workflows.SecurityReadiness.tools.inspect_generated_app_security import (
    _scan_module_contracts,
)
from tests.test_app_runtime_smoke import (
    MODULE_YAML,
    _by_check,
    _good,
    _smoke,
)
from tests.test_app_runtime_smoke import (
    mongo as _mongo,
)

mongo = _mongo
WRITES = ("create_task", "update_task", "delete_task")


def _role_bundle(tenancy="per_user"):
    files = _good()
    if tenancy == "app_wide":
        contract = json.loads(files["data/contract.json"])
        surface = next(item for item in contract["surfaces"] if item["surface_id"] == "task_management")
        surface["collections"][0].update(tenancy="app_wide", owner_field=None)
        files["data/contract.json"] = json.dumps(contract)
    auth = yaml.safe_load(files["config/auth.yaml"])
    auth["schema_version"] = "mozaiks.auth.v2"
    auth["authorization"] = {"roles": [
        {"id": "coordinator", "permissions": [f"tasks.{action}" for action in WRITES]},
        {"id": "unrelated", "permissions": ["tasks.audit"]},
    ]}
    files["config/auth.yaml"] = yaml.safe_dump(auth)
    module = yaml.safe_load(files[MODULE_YAML])
    module["permissions"] = [{"id": f"tasks.{action}", "description": action}
                              for action in WRITES]
    for action in module["actions"]:
        if action["id"] in WRITES:
            action["permissions"] = [f"tasks.{action['id']}"]
    files[MODULE_YAML] = yaml.safe_dump(module)
    return files


def test_security_readiness_accepts_only_valid_loaded_role_grants():
    files = _role_bundle()
    module = yaml.safe_load(files[MODULE_YAML])
    module["permissions"] = []
    files[MODULE_YAML] = yaml.safe_dump(module)
    assert not [finding for finding in _scan_module_contracts(files, "")
                if finding["finding_id"].startswith("module_permissions:undeclared:")]
    auth = yaml.safe_load(files["config/auth.yaml"])
    auth["authorization"]["roles"][0]["permissions"].remove("tasks.delete_task")
    files["config/auth.yaml"] = yaml.safe_dump(auth)
    undeclared = [finding for finding in _scan_module_contracts(files, "")
                  if finding["finding_id"].startswith("module_permissions:undeclared:")]
    assert [finding["finding_id"] for finding in undeclared] == [
        "module_permissions:undeclared:task_management:delete_task",
    ]
    auth["schema_version"] = "invalid"
    files["config/auth.yaml"] = yaml.safe_dump(auth)
    undeclared = [finding for finding in _scan_module_contracts(files, "")
                  if finding["finding_id"].startswith("module_permissions:undeclared:")]
    assert len(undeclared) == 3


@pytest.mark.asyncio
@pytest.mark.parametrize("tenancy", ["per_user", "app_wide"])
async def test_role_smoke_proves_allow_deny_and_preserves_owner_isolation(mongo, tenancy):
    result = await _smoke(_role_bundle(tenancy), mongo.uri)
    assert result["passed"] is True, result["failed_tests"]
    checks = _by_check(result)
    for action in WRITES:
        assert checks[f"role.task_management.{action}.holder_allowed"]["status"] == "passed"
        denied = checks[f"role.task_management.{action}.non_holder_denied"]
        assert denied["status"] == "passed" and denied["details"]["records_unchanged"] is True
    if tenancy == "per_user":
        for check in ("b_list_isolated", "b_get_isolated", "b_update_denied", "b_delete_denied"):
            assert checks[f"crud.task_management.tasks.{check}"]["status"] == "passed"
    principals = result["principals"]
    assert principals["A"]["roles"] == principals["B"]["roles"] == []
    assert any(principal["roles"] == ["coordinator"] for principal in principals.values())
    assert all("unrelated" not in principal["roles"] for principal in principals.values())
    assert not any(scope.startswith("tasks.") for principal in principals.values() for scope in principal["scopes"])


@pytest.mark.asyncio
async def test_observer_assertion_transports_roles_without_turning_them_into_scopes(tmp_path: Path, mongo):
    from motor.motor_asyncio import AsyncIOMotorClient

    root = tmp_path / "app"
    _write_files_to_dir(root, _role_bundle())
    client = AsyncIOMotorClient(mongo.uri, serverSelectionTimeoutMS=2000)
    database = f"mozaiks_runtime_smoke_{uuid4().hex}"
    run = smoke._SmokeRun(root, client, database, lambda *_args, **_kwargs: None)
    run.external_server = run.external_probe = True
    run.probe_nonce = uuid4().hex
    try:
        assert await smoke._boot(run, set(os.environ))
        entities = smoke._contract_entities(run)
        for entity in entities:
            await smoke._crud(run, entity)
        await smoke._entitlements(run, entities)
        await smoke._remaining_role_checks(run, entities)
        assert not [outcome.to_dict() for outcome in run.outcomes if outcome.passed is False]
        assert len(run.role_successes) == len(run.role_denials_checked) == 3
        assert run.scopes == ["email", "openid", "profile"]
        assert run.app.state.auth_contract == run.load.auth_contract
    finally:
        if run.http is not None:
            await run.http.aclose()
        await client.drop_database(database)
        client.close()


@pytest.mark.asyncio
async def test_role_smoke_refuses_to_claim_unobserved_custom_action_success(mongo):
    files = _role_bundle()
    module = yaml.safe_load(files[MODULE_YAML])
    module["permissions"].append({"id": "tasks.approve_task", "description": "Approve a task."})
    module["actions"].append({
        "id": "approve_task", "handler_method": "approve_task", "description": "Approve a task.",
        "permissions": ["tasks.approve_task"], "api_surface": None,
        "input_schema": {"type": "object", "properties": {}},
        "output_schema": {"type": "object", "properties": {}},
    })
    files[MODULE_YAML] = yaml.safe_dump(module)
    auth = yaml.safe_load(files["config/auth.yaml"])
    auth["authorization"]["roles"][0]["permissions"].append("tasks.approve_task")
    files["config/auth.yaml"] = yaml.safe_dump(auth)
    files["modules/task_management/backend/handler.py"] += (
        "\n    async def approve_task(self, ctx):\n"
        "        from mozaiksai.core.runtime import ModuleRecordNotFoundError\n"
        "        raise ModuleRecordNotFoundError('No pending task')\n"
    )
    result = await _smoke(files, mongo.uri)
    checks = _by_check(result)
    assert result["passed"] is False
    assert checks["role.task_management.approve_task.non_holder_denied"]["status"] == "passed"
    assert checks["role.task_management.approve_task.holder_allowed"]["status"] == "failed"
    assert "No successful role-bearing execution" in checks["role.task_management.approve_task.holder_allowed"]["message"]
