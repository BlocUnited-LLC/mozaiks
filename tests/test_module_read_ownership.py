"""Read implementation ownership uses one compiler predicate for prompts and materialization."""
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from factory_app.workflows.AppGenerator.tools.app_validation import (
    validate_module_implementation_contract,
)
from factory_app.workflows.AppGenerator.tools.hook_file_contract_context import (
    _build_file_contracts_body,
)
from mozaiksai.core.workflow.generator_support.code_files import extract_code_file_map_from_payload
from mozaiksai.core.workflow.generator_support.module_read_actions import (
    materialize_module_read_implementations,
    module_read_implementation_ownership,
)
from mozaiksai.core.workflow.generator_support.module_write_actions import close_module_actions
from tests.test_appgenerator_module_read_actions import (
    BACKEND,
    MODULE,
    _closed,
    _contract,
    _plan,
)


@pytest.mark.parametrize("tenancy", ["per_user", "per_workspace", "app_wide"])
@pytest.mark.parametrize("protected", [False, True])
def test_read_owner_projection_matches_the_compiler(tenancy, protected):
    output = _closed()
    actions = output["module_contract"]["module_yaml"]["actions"]
    if protected:
        actions[0]["permissions"] = ["tasks.write"]
    contract = _contract(tenancy)
    output = _closed(output, contract)
    manifest = output["module_contract"]["module_yaml"]
    before = deepcopy(manifest)
    owners = module_read_implementation_ownership(MODULE, manifest, app_build_plan=_plan(), data_contract=contract)
    authored = tenancy == "app_wide" and protected
    assert owners == {"code_owned": [] if authored else ["get_tasks", "list_tasks"],
                      "authored": ["get_tasks", "list_tasks"] if authored else []}
    files = extract_code_file_map_from_payload(output)
    changes = materialize_module_read_implementations(files, app_build_plan=_plan(), data_contract=contract)
    assert manifest == before
    if authored:
        assert changes == {}
    else:
        assert all("async def get_tasks" in changes[f"{BACKEND}/{name}.py"]
                   and "async def list_tasks" in changes[f"{BACKEND}/{name}.py"]
                   for name in ("handler", "service", "repo"))


@pytest.mark.parametrize("access", [{"permissions": ["tasks.read"]}, {"api_surface": "public"},
                                  {"handler_method": "restricted_list"}])
def test_explicit_authored_read_does_not_change_ordinary_sibling_ownership(access):
    output = _closed(contract=_contract("app_wide"))
    manifest = output["module_contract"]["module_yaml"]
    next(action for action in manifest["actions"] if action["id"] == "list_tasks").update(access)
    owners = module_read_implementation_ownership(MODULE, manifest, app_build_plan=_plan(),
                                                  data_contract=_contract("app_wide"))
    assert owners == {"code_owned": ["get_tasks"], "authored": ["list_tasks"]}
    changes = materialize_module_read_implementations(extract_code_file_map_from_payload(output),
        app_build_plan=_plan(), data_contract=_contract("app_wide"))
    assert all("async def get_tasks" in value and "async def list_tasks" not in value for value in changes.values())


@pytest.mark.parametrize("stale", [False, True])
def test_service_read_inventory_uses_admitted_dependency_and_keeps_empty_permission_reads_authored(stale):
    from tests.test_authored_role_write_contracts import (
        BACKEND as backend,
    )
    from tests.test_authored_role_write_contracts import (
        MODULE as module,
    )
    from tests.test_authored_role_write_contracts import (
        _arguments,
        _candidate,
        _reviewed_context,
    )
    args = _arguments()
    admitted = close_module_actions(_candidate(), **args)
    context = {**_reviewed_context(args), "current_build_task": {
        "capability_pack_id": module, "owned_paths": [f"{backend}/service.py"]},
        "dependency_task_outputs": {"contract": admitted}}
    if stale:
        context["module_contract"] = {"module_yaml": {"module": {"id": "foreign"}, "actions": []}}
    body = _build_file_contracts_body(SimpleNamespace(name="ServiceAgent", context_variables=context), {})
    assert "Authored read actions" in body
    assert "Code-owned read methods" not in body
    assert "  - get_borrow_requests" in body
    assert "  - list_borrow_requests" in body
    assert "permissions is empty and api_surface is null" in body


def test_absent_authored_reads_still_fail_the_existing_implementation_gate():
    output = _closed()
    output["module_contract"]["module_yaml"]["actions"][0]["permissions"] = ["tasks.write"]
    output = _closed(output, _contract("app_wide"))
    files = extract_code_file_map_from_payload(output)
    files[f"{BACKEND}/handler.py"] = "class TaskManagementHandler:\n" + "".join(
        f"    async def {name}(self, ctx, **params):\n        return {{'ok': True}}\n"
        for name in ("create_task", "update_task", "delete_task"))
    # Do not turn authored reads into ordinary generated methods to obtain a pass.
    assert materialize_module_read_implementations(files, app_build_plan=_plan(),
                                                  data_contract=_contract("app_wide")) == {}
    result = validate_module_implementation_contract(files)
    assert result["passed"] is False
    report = next(row for row in result["modules"] if row["module_id"] == MODULE)
    assert set(report["missing_handler_methods"]) == {"get_tasks", "list_tasks"}


def test_static_file_contract_does_not_claim_every_canonical_implementation_is_rendered():
    path = Path(__file__).resolve().parents[1] / "factory_app/build_context/AppGenerator/file_contracts.yaml"
    contract = yaml.safe_load(path.read_text(encoding="utf-8"))["task_contracts"]["module_contract"]
    guidance = "\n".join(contract["hard_constraints"])
    assert "canonical create/update/delete/list/get implementations are rendered by code" not in guidance
    assert "injected read/write ownership inventories" in guidance
    assert "empty read permissions do not by themselves make an implementation code-owned" in guidance
