"""Compile approved feature selections through real context and app assembly."""

from copy import deepcopy

import pytest
import yaml

from factory_app.workflows._shared.subscription_contract_context import (
    validate_module_contract_updates,
)
from factory_app.workflows.AppGenerator.tools import assemble_app_tasks as assembly
from factory_app.workflows.AppGenerator.tools.code_file_utils import save_generated_code
from factory_app.workflows.AppGenerator.tools.generated_bundle_scanner import scan_generated_bundle
from factory_app.workflows.AppGenerator.tools.module_entitlement_gates import (
    apply_entitlement_gates,
)
from mozaiksai.core.runtime.app.entitlements import ConfiguredEntitlementAdapter
from mozaiksai.core.runtime.app.subscriptions_loader import SubscriptionsConfig
from mozaiksai.core.runtime.composition.module_executor import ModuleExecutor, ModuleRequest
from mozaiksai.core.workflow.agents.factory import ContextVariablesBridge
from mozaiksai.core.workflow.generator_support.module_action_inventory import (
    all_module_actions,
    approved_workflow_surface_ids,
    ungated_module_actions,
)
from mozaiksai.core.workflow.generator_support.module_entitlement_gates import (
    approved_subscription_gates,
    compile_module_entitlement_gates,
    resolve_subscription_contract,
)
from tests.module_authority_test_helpers import enforce_authority

MODULE_PATH = "modules/task_management/module.yaml"
GATES = {
    "create_task": "feature.module.task_management.create_task",
    "edit_task": "feature.module.task_management.edit_task",
    "delete_task": "feature.module.task_management.delete_task",
    "view_dashboard": "feature.module.task_management.view_dashboard",
}
FREE_ACTIONS = {"create_task", "edit_task", "delete_task"}
DERIVED_GATES = {"view_dashboard": GATES["view_dashboard"]}


def _context(source="subscription_contract"):
    contract = {
        "contract_required": True,
        "subscription_config_file": {
            "schema_version": "mozaiks.subscriptions.v1",
            "label": "Task Plans",
            "default_plan_id": "free",
            "plans": [
                {"plan_id": "free", "label": "Free", "capabilities": [GATES[action] for action in sorted(FREE_ACTIONS)]},
                {"plan_id": "pro", "label": "Pro", "capabilities": sorted(GATES.values())},
            ],
        },
        "selected_features_by_plan": {
            "free": [f"module.task_management.{action}" for action in sorted(FREE_ACTIONS)],
            "pro": sorted([
                "module.task_management.create_task",
                "module.task_management.edit_task",
                "module.task_management.delete_task",
                "module.task_management.view_dashboard",
            ]),
        },
        "module_contract_updates": [
            {"module_id": "task_management", "action_id": action, "entitlement_gate": gate, "metering": None}
            for action, gate in DERIVED_GATES.items()
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
            "primary_entities": ["Task"], "owned_mutations": ["create_task", "edit_task", "delete_task"],
            "custom_reads": ["view_dashboard", "health"],
        }]},
        "data_contract": {"version": "1", "surfaces": [{
            "surface_id": "task_management", "surface_kind": "module", "collections": [{
                "name": "tasks", "entity": "Task", "tenancy": "per_user", "owner_field": "owner_id",
                "scope": "app", "ownership": {"surface_id": "task_management", "surface_kind": "module"},
                "fields": [{"name": "owner_id", "type": "string", "required": True}],
            }],
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
             **({"entitlement_gate": gate} if gate and action != "list_tasks" else {})}
            for action in [*GATES, "list_tasks", "health"]
        ],
    }
    return [{"filename": MODULE_PATH, "content": yaml.safe_dump(module)}]


def _actions(files):
    content = next(file["content"] for file in files if file["filename"] == MODULE_PATH)
    return {action["id"]: action for action in yaml.safe_load(content)["actions"]}


@pytest.mark.asyncio
@pytest.mark.parametrize("source", ["subscription_contract", "subscription_contract_artifact", "wrapped_artifact"])
@pytest.mark.parametrize("writer_gate", [None, "wrong.model.gate"])
async def test_selected_free_core_and_pro_dashboard_assemble_gates_and_pass_scanner(source, writer_gate):
    context = _context("subscription_contract_artifact" if source == "wrapped_artifact" else source)
    context.set("app_task_batch_results", {"task_contract": {"code_files": _files(writer_gate)}})
    # Uncompiled manifests fail bundle acceptance even with a valid selected-feature contract.
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
    assert {action: actions[action]["entitlement_gate"] for action in DERIVED_GATES} == DERIVED_GATES
    assert all("entitlement_gate" not in actions[action] for action in FREE_ACTIONS)
    assert "entitlement_gate" not in actions["health"]
    assert "entitlement_gate" not in actions["list_tasks"]
    assert yaml.safe_load(files[MODULE_PATH])["module"]["id"] == "task_management"
    assert context.get("generated_files")[MODULE_PATH] == files[MODULE_PATH]
    adapter = ConfiguredEntitlementAdapter(config=SubscriptionsConfig.model_validate(
        yaml.safe_load(files["config/subscriptions.yaml"]),
    ))
    for action in DERIVED_GATES:
        grant = await adapter.check(actions[action]["entitlement_gate"], app_id="task-app", user_id="free-user")
        assert grant.granted is False


@pytest.mark.asyncio
async def test_cancelled_pro_can_dispatch_shared_task_writes_but_not_paid_summary():
    core_actions = ("create_task", "update_task", "delete_task")
    paid_action = "summarize_tasks"
    capabilities = {action: f"feature.module.tasks.{action}" for action in (*core_actions, paid_action)}
    selected_core = [f"module.tasks.{action}" for action in core_actions]
    config = {
        "schema_version": "mozaiks.subscriptions.v1", "label": "Task Plans", "default_plan_id": "free",
        "assignment_store": {"data_alias": "billing.subscriptions", "user_id_field": "user_id",
                             "active_statuses": ["active"]},
        "plans": [
            {"plan_id": "free", "label": "Free", "capabilities": [capabilities[action] for action in core_actions]},
            {"plan_id": "pro", "label": "Pro", "capabilities": sorted(capabilities.values())},
        ],
    }
    contract = {
        "contract_required": True,
        "subscription_config_file": config,
        "selected_features_by_plan": {
            "free": selected_core, "pro": [*selected_core, f"module.tasks.{paid_action}"],
        },
        "module_contract_updates": [
            {"module_id": "tasks", "action_id": paid_action,
             "entitlement_gate": capabilities[paid_action], "metering": None},
        ],
    }
    context = ContextVariablesBridge({
        "subscription_contract": contract,
        "design_surface_map": {"surfaces": [{
            "surface_id": "tasks", "surface_kind": "module", "owner": "app",
            "owned_mutations": list(core_actions), "custom_reads": [paid_action],
        }]},
    })
    expected_gates = {"tasks": {paid_action: capabilities[paid_action]}}
    assert validate_module_contract_updates(contract, context) == expected_gates
    assert approved_subscription_gates(
        contract, approved_actions=all_module_actions(context),
        ungated_actions=ungated_module_actions(context), approved_workflows=[],
    ) == expected_gates

    manifest = {"module": {"id": "tasks"}, "actions": [
        {"id": action, "handler_method": action, "permissions": []}
        for action in (*core_actions, paid_action)
    ]}
    compiled = compile_module_entitlement_gates(
        {"modules/tasks/module.yaml": yaml.safe_dump(manifest)},
        gates_by_module=expected_gates,
        approved_actions=all_module_actions(context),
        ungated_actions=ungated_module_actions(context),
    )
    actions = {action["id"]: action for action in yaml.safe_load(compiled["modules/tasks/module.yaml"])["actions"]}
    assert all("entitlement_gate" not in actions[action] for action in core_actions)
    assert actions[paid_action]["entitlement_gate"] == capabilities[paid_action]

    class CancelledAssignment:
        async def find_one(self, query, projection=None):
            if query.get("app_id") == "task-app" and query.get("user_id") == "cancelled-user":
                return {"app_id": "task-app", "user_id": "cancelled-user", "plan_id": "pro",
                        "status": "cancelled", "granted_capabilities": sorted(capabilities.values())}
            return None

    class TaskHandler:
        def create_task(self, ctx):
            return {"action": "create_task"}

        def update_task(self, ctx):
            return {"action": "update_task"}

        def delete_task(self, ctx):
            return {"action": "delete_task"}

        def summarize_tasks(self, ctx):
            raise AssertionError("paid action must be blocked before handler dispatch")

    adapter = ConfiguredEntitlementAdapter(
        config=SubscriptionsConfig.model_validate(config),
        collection_resolver=lambda alias: CancelledAssignment(),
    )
    denied_grant = await adapter.check(
        capabilities[paid_action], app_id="task-app", user_id="cancelled-user",
    )
    assert denied_grant.granted is False

    executor = ModuleExecutor(entitlement_checker=adapter)
    executor.register(
        "tasks", TaskHandler(),
        action_method_map={action: action for action in actions},
        action_permissions={action: [] for action in actions},
        action_entitlements={action: details.get("entitlement_gate") for action, details in actions.items()},
    )
    for action in core_actions:
        result = await executor.execute(ModuleRequest(
            module="tasks", action=action, params={}, app_id="task-app", user_id="cancelled-user",
            authority=enforce_authority(),
        ))
        assert result.success is True
        assert result.data == {"action": action}
    denied = await executor.execute(ModuleRequest(
        module="tasks", action=paid_action, params={}, app_id="task-app", user_id="cancelled-user",
        authority=enforce_authority(),
    ))
    assert denied.success is False
    assert denied.error_code == "ENTITLEMENT_REQUIRED"


@pytest.mark.asyncio
async def test_approved_mapping_applies_after_template_overlay(monkeypatch):
    context = _context()
    context.set("app_task_batch_results", {"task_contract": {"code_files": _files()}})
    monkeypatch.setattr(assembly, "_apply_managed_capability_templates", lambda files, **kwargs: _files("wrong.template"))
    result = await assembly.assemble_app_tasks(context_variables=context)
    actions = _actions(result["code_files"])
    assert {action: actions[action]["entitlement_gate"] for action in DERIVED_GATES} == DERIVED_GATES
    assert all("entitlement_gate" not in actions[action] for action in FREE_ACTIONS)


def test_contract_overrides_are_idempotent_and_do_not_mutate_frozen_context():
    context = _context()
    before = context.snapshot()
    original = _files("wrong.model.gate")
    files = deepcopy(original)
    result = apply_entitlement_gates(files, context_variables=context)
    assert apply_entitlement_gates(result, context_variables=context) == result
    assert files == original
    assert context.snapshot() == before


def test_legacy_mapping_without_selected_features_cannot_authorize_assembly():
    context = _context()
    contract = context.snapshot()["subscription_contract"]
    del contract["selected_features_by_plan"]
    context.set("subscription_contract", contract)
    files = _files()

    with pytest.raises(ValueError, match="selected_features_by_plan is required"):
        apply_entitlement_gates(files, context_variables=context)
    assert files == _files()


def test_saved_contract_without_feature_selections_names_designer_rerun():
    context = _context()
    contract = context.snapshot()["subscription_contract"]
    del contract["selected_features_by_plan"]
    with pytest.raises(ValueError, match="Re-run SubscriptionContractDesigner"):
        validate_module_contract_updates(contract, context)
    with pytest.raises(ValueError, match="Re-run SubscriptionContractDesigner"):
        approved_subscription_gates(
            contract, approved_actions=all_module_actions(context),
            ungated_actions=ungated_module_actions(context), approved_workflows=[],
        )


def test_task_batch_projection_rejects_duplicate_selected_feature():
    context = _context()
    contract = context.snapshot()["subscription_contract"]
    contract["selected_features_by_plan"]["pro"].append("module.task_management.create_task")

    with pytest.raises(ValueError, match="repeats a selected feature"):
        approved_subscription_gates(
            contract,
            approved_actions=all_module_actions(context),
            ungated_actions=ungated_module_actions(context),
            approved_workflows=approved_workflow_surface_ids(context),
        )


def test_task_batch_projection_rejects_unapproved_workflow_selection():
    context = _context()
    contract = context.snapshot()["subscription_contract"]
    contract["selected_features_by_plan"]["pro"].append("workflow.Unapproved")
    contract["subscription_config_file"]["plans"][1]["capabilities"].append("feature.workflow.unapproved")
    contract["workflow_contract_updates"] = [
        {"design_surface_id": "Unapproved", "capability_id": "feature.workflow.unapproved"},
    ]

    with pytest.raises(ValueError, match=r"not an approved workflow surface.*Valid workflow features: \[\]"):
        approved_subscription_gates(
            contract,
            approved_actions=all_module_actions(context),
            ungated_actions=ungated_module_actions(context),
            approved_workflows=approved_workflow_surface_ids(context),
        )


def test_approved_workflow_inventory_requires_agentic_app_surface():
    context = _context()
    surface_map = context.snapshot()["design_surface_map"]
    surface_map["surfaces"].extend([
        {"surface_id": "TaskAnalysis", "surface_kind": "workflow", "owner": "app"},
        {"surface_id": "PlatformAnalysis", "surface_kind": "workflow", "owner": "platform"},
    ])
    context.set("design_surface_map", surface_map)
    assert approved_workflow_surface_ids(context) == []

    context.set("concept_blueprint", {"agentic_capabilities": ["task analysis"]})
    assert approved_workflow_surface_ids(context) == ["TaskAnalysis"]


def test_approved_surface_id_overrides_writer_module_identity():
    files = _files()
    data = yaml.safe_load(files[0]["content"])
    data["module"]["id"] = "tasks"
    files[0]["content"] = yaml.safe_dump(data)
    result = apply_entitlement_gates(files, context_variables=_context())
    assert yaml.safe_load(result[0]["content"])["module"]["id"] == "task_management"
    assert {action: _actions(result)[action]["entitlement_gate"] for action in DERIVED_GATES} == DERIVED_GATES
    assert all("entitlement_gate" not in _actions(result)[action] for action in FREE_ACTIONS)


@pytest.mark.parametrize("fault,expected", [
    ("missing_module", "Missing module.yaml"),
    ("missing_action", "missing=['view_dashboard']"),
    ("duplicate_action", "duplicate=['view_dashboard']"),
])
def test_unresolved_implementation_lists_valid_targets(fault, expected):
    files = _files()
    if fault == "missing_module":
        files = []
    else:
        data = yaml.safe_load(files[0]["content"])
        if fault == "missing_action":
            data["actions"] = [action for action in data["actions"] if action["id"] != "view_dashboard"]
        else:
            data["actions"].append(next(action for action in data["actions"] if action["id"] == "view_dashboard"))
        files[0]["content"] = yaml.safe_dump(data)
    with pytest.raises(ValueError) as exc:
        apply_entitlement_gates(files, context_variables=_context())
    assert expected in str(exc.value)
    assert "Valid approved" in str(exc.value)
    assert "task_management" in str(exc.value)


def test_unrelated_files_are_preserved():
    files = [*_files(), {"filename": "services/config.py", "content": "TIMEOUT = 10\n"}]
    result = apply_entitlement_gates(files, context_variables=_context())
    assert files[-1] in result


def test_no_contract_rejects_gated_manifests_without_mutating_files():
    files = _files("existing.gate")
    with pytest.raises(ValueError, match="approved subscription contract is required"):
        apply_entitlement_gates(files, context_variables=ContextVariablesBridge({}))
    assert all(action["entitlement_gate"] == "existing.gate" for name, action in _actions(files).items() if name != "list_tasks")


def test_no_contract_rejects_repair_that_removes_an_existing_gate():
    original = _files("existing.gate")
    context = ContextVariablesBridge({
        "generated_files": {f["filename"]: f["content"] for f in original},
        "structured_output": {"code_files": _files()},
    })
    before = context.snapshot()
    with pytest.raises(ValueError, match="existing entitlement gates cannot be removed"):
        save_generated_code(context)
    assert context.snapshot() == before


@pytest.mark.asyncio
@pytest.mark.parametrize("source", ["direct", "artifact", "commit_artifact"])
@pytest.mark.parametrize("decision", [{"unrelated": True}, {"contract_required": None}, {"contract_required": 0}])
async def test_malformed_contract_cannot_authorize_gate_removal_at_any_writer(source, decision):
    context = _context()
    original = {file["filename"]: file["content"] for file in _files("existing.gate")}
    context.set("generated_files", original)
    context.set("structured_output", {"code_files": _files()})
    context.set("app_task_batch_results", {"task_contract": {"code_files": _files()}})
    context.set("subscription_contract", decision if source == "direct" else None)
    if source != "direct":
        artifact = {"metadata": {"summary_payload": decision}}
        context.set("subscription_contract_artifact", {"commit_metadata": artifact} if source == "commit_artifact" else artifact)
    before = context.snapshot()
    assert resolve_subscription_contract(context) is None
    # The task boundary and the Factory save/assembly boundary share the same
    # absence-of-authority meaning, including replacement by an ungated manifest.
    assert approved_subscription_gates(
        decision, approved_actions={}, ungated_actions={}, approved_workflows=[],
    ) is None
    with pytest.raises(ValueError, match="existing entitlement gates cannot be removed"):
        compile_module_entitlement_gates(
            {file["filename"]: file["content"] for file in _files()},
            gates_by_module=approved_subscription_gates(
                resolve_subscription_contract(context), approved_actions={}, ungated_actions={},
                approved_workflows=[],
            ),
            approved_actions={}, ungated_actions={}, existing_files=original,
        )
    with pytest.raises(ValueError, match="existing entitlement gates cannot be removed"):
        save_generated_code(context)
    assert context.snapshot() == before
    result = await assembly.assemble_app_tasks(context_variables=context)
    assert result["success"] is False
    assert result["status"] == "failed"
    assert result["error"].startswith("ValueError:")
    assert "existing entitlement gates cannot be removed" in result["error"]
    assert context.get("app_assembly_error") == result["error"]
    assert context.get("app_assembly_status") == "failed"
    assert context.get("generated_files") == original
    assert {key: context.snapshot()[key] for key in before} == before


def test_explicit_noop_contract_is_authority_to_remove_old_gates():
    context = ContextVariablesBridge({"subscription_contract": {"contract_required": False}})
    result = apply_entitlement_gates(_files("old.gate"), context_variables=context)
    assert all("entitlement_gate" not in action for action in _actions(result).values())


def test_live_noop_contract_does_not_apply_stale_artifact_mapping():
    context = _context("subscription_contract_artifact")
    context.set("subscription_contract", {"contract_required": False, "module_contract_updates": []})
    files = _files()
    assert apply_entitlement_gates(files, context_variables=context) == files


@pytest.mark.asyncio
async def test_unapproved_action_cannot_alias_a_gated_handler_at_any_writer():
    context = _context()
    original = {
        file["filename"]: file["content"]
        for file in apply_entitlement_gates(_files(), context_variables=context)
    }
    context.set("generated_files", original)
    files = _files()
    manifest = yaml.safe_load(files[0]["content"])
    manifest["actions"].append({"id": "shadow_edit", "handler_method": "edit_task", "permissions": []})
    files[0]["content"] = yaml.safe_dump(manifest)
    context.set("structured_output", {"code_files": files})
    context.set("app_task_batch_results", {"task_contract": {"code_files": files}})
    before = context.snapshot()
    with pytest.raises(ValueError, match="unapproved module actions.*shadow_edit"):
        compile_module_entitlement_gates(
            {file["filename"]: file["content"] for file in files},
            gates_by_module=approved_subscription_gates(
                resolve_subscription_contract(context),
                approved_actions=all_module_actions(context),
                ungated_actions=ungated_module_actions(context),
                approved_workflows=approved_workflow_surface_ids(context),
            ),
            approved_actions=all_module_actions(context),
            ungated_actions=ungated_module_actions(context),
        )
    with pytest.raises(ValueError, match="unapproved module actions.*shadow_edit"):
        save_generated_code(context)
    assert context.snapshot() == before
    result = await assembly.assemble_app_tasks(context_variables=context)
    assert result["success"] is False
    assert result["status"] == "failed"
    assert result["error"].startswith("ValueError:")
    assert "unapproved module actions" in result["error"]
    assert "shadow_edit" in result["error"]
    assert context.get("app_assembly_error") == result["error"]
    assert context.get("app_assembly_status") == "failed"
    assert context.get("generated_files") == original
    assert {key: context.snapshot()[key] for key in before} == before


def test_repaired_manifest_recompiles_approved_gates_with_real_context_bridge():
    context = _context()
    before = context.snapshot()
    context.set("structured_output", {"code_files": _files()})
    result = save_generated_code(context)
    assert MODULE_PATH in result["saved_files"]
    actions = _actions(context.get("code_files"))
    assert {action: actions[action]["entitlement_gate"] for action in DERIVED_GATES} == DERIVED_GATES
    assert all("entitlement_gate" not in actions[action] for action in FREE_ACTIONS)
    assert context.snapshot()["subscription_contract"] == before["subscription_contract"]
    assert context.snapshot()["data_contract"] == before["data_contract"]


def test_repair_compiles_only_supplied_manifests_and_keeps_facades_ungated():
    context = _context()
    surfaces = deepcopy(context.snapshot()["design_surface_map"])
    surfaces["surfaces"].append({
        "surface_id": "billing_portal", "surface_kind": "module", "owner": "app",
        "source_capability_packs": ["mozaikspay"], "custom_reads": ["list_plans"],
    })
    context.set("design_surface_map", surfaces)
    content = yaml.safe_dump({"module": {"id": "billing_portal"}, "actions": [
        {"id": "list_plans"},
    ]})
    result = apply_entitlement_gates(
        [{"filename": "modules/billing_portal/module.yaml", "content": content}],
        context_variables=context, require_all_modules=False,
    )
    assert "entitlement_gate" not in yaml.safe_load(result[0]["content"])["actions"][0]


@pytest.mark.asyncio
@pytest.mark.parametrize("action_id", ["list_tasks", "get_tasks"])
@pytest.mark.parametrize("gate_source", ["selected_feature", "authored_manifest"])
async def test_canonical_read_gate_rejected_without_mutating_any_writer(action_id, gate_source):
    context = _context()
    original = {
        file["filename"]: file["content"]
        for file in apply_entitlement_gates(_files(), context_variables=context)
    }
    context.set("generated_files", original)
    files = _files()
    manifest = yaml.safe_load(files[0]["content"])
    manifest["actions"].append({"id": "get_tasks", "handler_method": "get_tasks", "permissions": []})
    if gate_source == "selected_feature":
        contract = context.snapshot()["subscription_contract"]
        feature_id = f"module.task_management.{action_id}"
        contract["selected_features_by_plan"]["pro"].append(feature_id)
        contract["subscription_config_file"]["plans"][1]["capabilities"].append(f"feature.{feature_id}")
        contract["module_contract_updates"].append({
            "module_id": "task_management", "action_id": action_id,
            "entitlement_gate": f"feature.{feature_id}", "metering": None,
        })
        context.set("subscription_contract", contract)
    else:
        next(action for action in manifest["actions"] if action["id"] == action_id)["entitlement_gate"] = GATES["view_dashboard"]
    files[0]["content"] = yaml.safe_dump(manifest)
    context.set("structured_output", {"code_files": files})
    context.set("app_task_batch_results", {"task_contract": {"code_files": files}})
    before = context.snapshot()
    compile_error = (
        "not an approved gate target" if gate_source == "selected_feature"
        else "canonical collection reads.*cannot have entitlement gates"
    )
    with pytest.raises(ValueError, match=compile_error):
        compile_module_entitlement_gates(
            {file["filename"]: file["content"] for file in files},
            gates_by_module=approved_subscription_gates(
                resolve_subscription_contract(context),
                approved_actions=all_module_actions(context),
                ungated_actions=ungated_module_actions(context),
                approved_workflows=approved_workflow_surface_ids(context),
            ),
            approved_actions=all_module_actions(context), ungated_actions=ungated_module_actions(context),
        )
    message = action_id if gate_source == "selected_feature" else "cannot have entitlement gates"
    with pytest.raises(ValueError, match=message):
        save_generated_code(context)
    assert context.snapshot() == before
    result = await assembly.assemble_app_tasks(context_variables=context)
    assert result["success"] is False
    assert result["status"] == "failed"
    assert result["error"].startswith("ValueError:")
    assert message in result["error"]
    assert context.get("app_assembly_error") == result["error"]
    assert context.get("app_assembly_status") == "failed"
    assert context.get("generated_files") == original
    assert {key: context.snapshot()[key] for key in before} == before


def test_managed_facade_authored_gate_is_rejected_instead_of_silently_removed():
    context = _context()
    surfaces = context.snapshot()["design_surface_map"]
    surfaces["surfaces"].append({
        "surface_id": "billing_portal", "surface_kind": "module", "owner": "app",
        "source_capability_packs": ["mozaikspay"], "custom_reads": ["list_plans"],
    })
    context.set("design_surface_map", surfaces)
    files = [{"filename": "modules/billing_portal/module.yaml", "content": yaml.safe_dump({
        "module": {"id": "billing_portal"}, "actions": [
            {"id": "list_plans", "entitlement_gate": "dashboard.view"},
        ],
    })}]
    before = deepcopy(files)
    with pytest.raises(ValueError, match="managed facade actions cannot have entitlement gates"):
        apply_entitlement_gates(files, context_variables=context, require_all_modules=False)
    assert files == before
