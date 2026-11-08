"""Reviewed read grants survive compilation and real HTTP/Mongo dispatch."""

from __future__ import annotations

import json

import pytest

from factory_app.workflows.AppGenerator.tools.render_auth_scaffold import materialize_auth_scaffold
from mozaiksai.core.workflow.generator_support.code_files import extract_code_file_map_from_payload
from mozaiksai.core.workflow.generator_support.module_account_data import (
    materialize_module_account_handlers,
)
from mozaiksai.core.workflow.generator_support.module_policy import materialize_module_policies
from mozaiksai.core.workflow.generator_support.module_read_actions import (
    materialize_module_read_implementations,
)
from mozaiksai.core.workflow.generator_support.module_write_actions import (
    close_module_actions,
    materialize_module_schemas,
    materialize_module_write_implementations,
)
from mozaiksai.core.workflow.generator_support.persistence_artifacts import (
    normalize_data_contract_indexes,
)
from tests.test_app_runtime_smoke import _by_check, _good, _smoke
from tests.test_app_runtime_smoke import mongo as _mongo
from tests.test_appgenerator_module_write_actions import WRITES, _contract, _output, _plan
from tests.test_generated_role_grants import _surface

mongo = _mongo
READS = ("get_tasks", "list_tasks")


def _compiled_role_bundle():
    contract, plan, surface = normalize_data_contract_indexes(_contract()), _plan(), _surface()
    policy = {
        "roles": [{"role_id": "member", "label": "Member", "description": "Reads and maintains owned tasks."}],
        "grants": [{"role_id": "member", "collection": "tasks", "actions": [*READS, *WRITES]}],
    }
    arguments = {"app_build_plan": plan, "data_contract": contract,
                 "design_surface_map": surface, "access_policy": policy}
    files = extract_code_file_map_from_payload(close_module_actions(_output(), **arguments))
    for materialize in (materialize_module_schemas, materialize_module_read_implementations,
                        materialize_module_account_handlers):
        files.update(materialize(files, app_build_plan=plan, data_contract=contract))
    files.update(materialize_module_write_implementations(files, **arguments))
    files.update(materialize_module_policies(files, contract))
    files["app.json"] = json.dumps({"appName": "Read Role Proof", "authRequired": True})
    files["config/auth.yaml"] = _good()["config/auth.yaml"]
    files["data/contract.json"] = json.dumps(contract)
    files.update(materialize_auth_scaffold(files, data_contract=contract,
                                           design_surface_map=surface, access_policy=policy))
    return files


@pytest.mark.asyncio
async def test_compiled_read_roles_deny_nonholders_and_preserve_owned_records(mongo):
    result = await _smoke(_compiled_role_bundle(), mongo.uri)
    assert result["passed"] is True, result["failed_tests"]
    checks = _by_check(result)
    for action in (*READS, *WRITES):
        assert checks[f"role.task_management.{action}.holder_allowed"]["status"] == "passed"
        denied = checks[f"role.task_management.{action}.non_holder_denied"]
        assert denied["status"] == "passed"
        assert denied["details"]["records_unchanged"] is True
    for check in ("a_create", "a_list", "a_get", "get_missing", "b_list_isolated", "b_get_isolated",
                  "b_update_denied", "b_delete_denied", "a_update", "a_delete"):
        assert checks[f"crud.task_management.tasks.{check}"]["status"] == "passed"
    assert not any(scope.startswith("tasks.") for principal in result["principals"].values()
                   for scope in principal["scopes"])
    assert all(principal["roles"] in ([], ["member"]) for principal in result["principals"].values())
