"""Replay the reported free/pro grants through real context and app assembly."""

from copy import deepcopy

import pytest
import yaml

from factory_app.workflows.AppGenerator.tools import assemble_app_tasks as assembly
from factory_app.workflows.AppGenerator.tools.generated_bundle_scanner import scan_generated_bundle
from mozaiksai.core.runtime.app.entitlements import ConfiguredEntitlementAdapter
from mozaiksai.core.runtime.app.subscriptions_loader import SubscriptionsConfig
from mozaiksai.core.workflow.agents.factory import ContextVariablesBridge

MODULE_PATH = "modules/task_management/module.yaml"
GATES = {
    "create_task": "task.create",
    "list_tasks": "task.view",
    "edit_task": "task.edit",
    "view_dashboard": "dashboard.view",
}


def _context(source="subscription_contract"):
    contract = {
        "contract_required": True,
        "subscription_config_file": {
            "schema_version": "mozaiks.subscriptions.v1",
            "label": "Task Plans",
            "default_plan_id": "free",
            "plans": [
                {"plan_id": "free", "label": "Free", "capabilities": ["task.create", "task.view"]},
                {"plan_id": "pro", "label": "Pro", "capabilities": [
                    "task.create", "task.view", "task.edit", "dashboard.view",
                ]},
            ],
        },
        "module_contract_updates": [
            {"module_id": "task_management", "action_id": action, "entitlement_gate": gate, "metering": None}
            for action, gate in GATES.items()
        ],
    }
    return ContextVariablesBridge({
        "app_id": "factory-host",
        "run_build_binding": {
            "target_app_id": "task-app", "build_registry_id": "registry",
            "build_id": "build", "phase": "genesis",
        },
        source: contract,
        "design_surface_map": {"surfaces": [{
            "surface_id": "task_management", "surface_kind": "module", "owner": "app",
            "owned_mutations": [*GATES, "health"],
        }]},
    })


def _files(gate=None):
    module = {
        "schema_version": "mozaiks.module.v1",
        "module": {
            "id": "task_management", "display_name": "Tasks", "version": "1.0.0",
            "description": "Task management", "handler": "backend.handler:Handler",
        },
        "permissions": [],
        "actions": [
            {"id": action, "description": action, "handler_method": action, "permissions": [],
             **({"entitlement_gate": gate} if gate else {})}
            for action in [*GATES, "health"]
        ],
    }
    return [{"filename": MODULE_PATH, "content": yaml.safe_dump(module)}]


def _actions(files):
    content = next(file["content"] for file in files if file["filename"] == MODULE_PATH)
    return {action["id"]: action for action in yaml.safe_load(content)["actions"]}


@pytest.mark.asyncio
@pytest.mark.parametrize("source", ["subscription_contract", "subscription_contract_artifact", "wrapped_artifact"])
@pytest.mark.parametrize("writer_gate", [None, "wrong.model.gate"])
async def test_exact_reported_plan_shape_assembles_gates_and_passes_scanner(source, writer_gate):
    context = _context("subscription_contract_artifact" if source == "wrapped_artifact" else source)
    context.set("app_task_batch_results", {"task_contract": {"code_files": _files(writer_gate)}})
    # Before the fix, the exact plans with ungated action files fail the unchanged scanner.
    ungated = {file["filename"]: file["content"] for file in _files()}
    ungated["config/subscriptions.yaml"] = yaml.safe_dump(
        context.snapshot()["subscription_contract_artifact" if source == "wrapped_artifact" else source]["subscription_config_file"],
    )
    assert any("no module action declares" in error for error in scan_generated_bundle(ungated))
    if source == "wrapped_artifact":
        context.set("subscription_contract_artifact", {"metadata": {
            "summary_payload": context.snapshot()["subscription_contract_artifact"],
        }})

    result = await assembly.assemble_app_tasks(context_variables=context)

    files = {file["filename"]: file["content"] for file in result["code_files"]}
    assert scan_generated_bundle(files) == []
    actions = _actions(result["code_files"])
    assert {action: actions[action]["entitlement_gate"] for action in GATES} == GATES
    assert "entitlement_gate" not in actions["health"]
    assert yaml.safe_load(files[MODULE_PATH])["module"]["id"] == "task_management"
    assert context.get("generated_files")[MODULE_PATH] == files[MODULE_PATH]
    adapter = ConfiguredEntitlementAdapter(config=SubscriptionsConfig.model_validate(
        yaml.safe_load(files["config/subscriptions.yaml"]),
    ))
    for action in GATES:
        grant = await adapter.check(actions[action]["entitlement_gate"], app_id="task-app", user_id="free-user")
        assert grant.granted is (action in {"create_task", "list_tasks"})


@pytest.mark.asyncio
async def test_approved_mapping_applies_after_template_overlay(monkeypatch):
    context = _context()
    context.set("app_task_batch_results", {"task_contract": {"code_files": _files()}})
    monkeypatch.setattr(assembly, "_apply_managed_capability_templates", lambda files, **kwargs: _files("wrong.template"))
    result = await assembly.assemble_app_tasks(context_variables=context)
    actions = _actions(result["code_files"])
    assert {action: actions[action]["entitlement_gate"] for action in GATES} == GATES


def test_contract_overrides_are_idempotent_and_do_not_mutate_frozen_context():
    context = _context()
    before = context.snapshot()
    original = _files("wrong.model.gate")
    files = deepcopy(original)
    result = assembly._apply_entitlement_gates(files, context_variables=context)
    assert assembly._apply_entitlement_gates(result, context_variables=context) == result
    assert files == original
    assert context.snapshot() == before


def test_approved_surface_id_overrides_writer_module_identity():
    files = _files()
    data = yaml.safe_load(files[0]["content"])
    data["module"]["id"] = "tasks"
    files[0]["content"] = yaml.safe_dump(data)
    result = assembly._apply_entitlement_gates(files, context_variables=_context())
    assert yaml.safe_load(result[0]["content"])["module"]["id"] == "task_management"
    assert {action: _actions(result)[action]["entitlement_gate"] for action in GATES} == GATES


@pytest.mark.parametrize("fault,expected", [
    ("missing_module", "Missing module.yaml"),
    ("missing_action", "missing=['edit_task']"),
    ("duplicate_action", "duplicate=['edit_task']"),
])
def test_unresolved_implementation_lists_valid_targets(fault, expected):
    files = _files()
    if fault == "missing_module":
        files = []
    else:
        data = yaml.safe_load(files[0]["content"])
        if fault == "missing_action":
            data["actions"] = [action for action in data["actions"] if action["id"] != "edit_task"]
        else:
            data["actions"].append(next(action for action in data["actions"] if action["id"] == "edit_task"))
        files[0]["content"] = yaml.safe_dump(data)
    with pytest.raises(ValueError) as exc:
        assembly._apply_entitlement_gates(files, context_variables=_context())
    assert expected in str(exc.value)
    assert "Valid approved" in str(exc.value)
    assert "task_management" in str(exc.value)


def test_unrelated_files_are_preserved():
    files = [*_files(), {"filename": "services/config.py", "content": "TIMEOUT = 10\n"}]
    result = assembly._apply_entitlement_gates(files, context_variables=_context())
    assert files[-1] in result


def test_no_contract_leaves_module_files_unchanged():
    files = _files("existing.gate")
    assert assembly._apply_entitlement_gates(files, context_variables=ContextVariablesBridge({})) == files


def test_live_noop_contract_does_not_apply_stale_artifact_mapping():
    context = _context("subscription_contract_artifact")
    context.set("subscription_contract", {"contract_required": False, "module_contract_updates": []})
    files = _files()
    assert assembly._apply_entitlement_gates(files, context_variables=context) == files
