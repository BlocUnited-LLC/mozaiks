"""Pricing selections close over DesignDocs features and derive runtime gates."""

from __future__ import annotations

from pathlib import Path
from types import MappingProxyType, SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import yaml

from factory_app.workflows._shared.subscription_contract_context import (
    approved_feature_inventory,
    approved_module_actions,
    inject_subscription_action_inventory,
    validate_module_contract_updates,
)
from factory_app.workflows.SubscriptionContractDesigner.tools import (
    save_subscription_contract as module,
)
from mozaiksai.core.workflow.agents.factory import ContextVariablesBridge
from mozaiksai.core.workflow.context.frozen import detach
from mozaiksai.core.workflow.context.structured_output_overlay import StructuredOutputOverlay
from mozaiksai.core.workflow.generator_support.module_action_inventory import all_module_actions
from tests.factory_context import factory_context


def _surface_map() -> dict:
    return {"surfaces": [
        {"surface_id": "tasks", "surface_kind": "module", "owner": "app",
         "primary_entities": ["Task"],
         "owned_mutations": ["create_task", "update_task", "delete_task"],
         "custom_reads": ["summarize_tasks"]},
        {"surface_id": "platform_identity", "surface_kind": "module", "owner": "platform",
         "owned_mutations": ["authenticate"]},
        {"surface_id": "TaskAnalysis", "surface_kind": "workflow", "owner": "app",
         "owned_mutations": ["analyze"]},
    ]}


def _data_contract() -> dict:
    return {"version": "1", "surfaces": [{
        "surface_id": "tasks", "surface_kind": "module", "collections": [{
            "name": "tasks", "entity": "Task", "tenancy": "per_user", "owner_field": "owner_id",
            "scope": "app", "lifecycle": {"write_mode": "module_action", "migration_policy": "additive_only"},
            "ownership": {"surface_id": "tasks", "surface_kind": "module"},
            "fields": [{"name": "owner_id", "type": "string", "required": True}],
        }],
    }]}


def _design() -> dict:
    return {
        "contract_required": True,
        "app_name": "Task Tracker",
        "subscription_config_file": {
            "schema_version": "mozaiks.subscriptions.v1", "label": "Task plans", "default_plan_id": "free",
            "plans": [
                {"plan_id": "free", "label": "Free", "included_features": [
                    "module.tasks.create_task", "module.tasks.update_task", "module.tasks.delete_task",
                ], "usage_limits": [{"meter_id": "tasks", "unit": "requests", "monthly_limit": 10,
                                     "feature_id": "module.tasks.create_task"}]},
                {"plan_id": "pro", "label": "Pro", "included_features": [
                    "module.tasks.create_task", "module.tasks.update_task", "module.tasks.delete_task",
                    "module.tasks.summarize_tasks",
                ]},
            ],
        },
        "metering_declarations": [],
    }


def _context(output: dict | None = None, *, agentic: bool = False,
             surface_map: dict | None = None) -> ContextVariablesBridge | StructuredOutputOverlay:
    context = ContextVariablesBridge(factory_context({
        "app_id": "task-tracker", "chat_id": "subscription-chat",
        "concept_blueprint": {"agentic_capabilities": ["task analysis"] if agentic else []},
        "design_surface_map": _surface_map() if surface_map is None else surface_map,
        "data_contract": _data_contract(),
    }))
    return StructuredOutputOverlay(context, output) if output is not None else context


@pytest.fixture
def side_effects(monkeypatch):
    review = AsyncMock(return_value={"approved": True})
    persist = AsyncMock(return_value=SimpleNamespace(id="subscription-contract-version"))
    monkeypatch.setattr(module, "use_ui_tool", review)
    monkeypatch.setattr(module, "persist_summary_artifact", persist)
    return review, persist


def test_inventory_excludes_canonical_reads_facades_and_unapproved_workflows() -> None:
    context = _context()
    assert isinstance(context.get("design_surface_map"), MappingProxyType)
    assert approved_module_actions(context) == {
        "tasks": ["create_task", "delete_task", "summarize_tasks", "update_task"],
    }
    assert {"get_tasks", "list_tasks"} <= set(all_module_actions(context)["tasks"])
    inventory = approved_feature_inventory(context)
    assert set(inventory) == {
        "module.tasks.create_task", "module.tasks.delete_task",
        "module.tasks.summarize_tasks", "module.tasks.update_task",
    }
    assert "workflow.TaskAnalysis" not in approved_feature_inventory(_context(agentic=True))
    agent = SimpleNamespace(name="ContractDesignerAgent", context_variables=context, system_message="base")
    inject_subscription_action_inventory(agent, [])
    assert "[APPROVED PRICING FEATURE INVENTORY]" in agent.system_message
    assert "module.tasks.create_task" in agent.system_message
    assert "list_tasks" not in agent.system_message
    assert "workflow.TaskAnalysis" not in agent.system_message


@pytest.mark.asyncio
async def test_free_core_actions_and_pro_custom_read_are_derived_before_review(side_effects) -> None:
    context = _context(_design())
    result = await module.save_subscription_contract(context)
    assert result["success"] is True
    assert result["review_status"] == "confirmed"
    saved = detach(context.get("subscription_contract"))
    assert saved["selected_features_by_plan"] == {
        "free": ["module.tasks.create_task", "module.tasks.update_task", "module.tasks.delete_task"],
        "pro": ["module.tasks.create_task", "module.tasks.update_task", "module.tasks.delete_task",
                "module.tasks.summarize_tasks"],
    }
    gates = validate_module_contract_updates(saved, context)
    assert gates == {"tasks": {"summarize_tasks": "feature.module.tasks.summarize_tasks"}}
    plans = saved["subscription_config_file"]["plans"]
    assert "included_features" not in plans[0]
    assert plans[0]["usage_limits"][0]["capability_id"] == "feature.module.tasks.create_task"
    assert "feature.module.tasks.summarize_tasks" not in plans[0]["capabilities"]
    assert "feature.module.tasks.summarize_tasks" in plans[1]["capabilities"]
    assert saved["module_contract_updates"] == [
        {"module_id": "tasks", "action_id": action_id, "entitlement_gate": capability,
         "metering": None}
        for action_id, capability in gates["tasks"].items()
    ]
    review, persist = side_effects
    review.assert_awaited_once()
    persist.assert_awaited_once()
    assert persist.await_args.kwargs["summary_payload"]["module_contract_updates"] == saved["module_contract_updates"]


@pytest.mark.asyncio
async def test_unknown_feature_returns_complete_actionable_message_before_review(side_effects) -> None:
    output = _design()
    output["subscription_config_file"]["plans"][1]["included_features"].append("workflow.run")
    context = _context(output)
    result = await module.save_subscription_contract(context)
    assert result["review_status"] == "changes_requested"
    assert "workflow.run" in result["error"]
    assert "Valid features:" in result["error"]
    assert "module.tasks.summarize_tasks" in result["error"]
    assert "Remove the unavailable feature reference" in result["error"]
    assert context.get("subscription_contract") is None
    review, persist = side_effects
    review.assert_not_awaited()
    persist.assert_not_awaited()


def test_persisted_grants_and_updates_must_match_feature_selection() -> None:
    context = _context()
    saved = module.normalize_subscription_contract(_design(), context)
    saved["subscription_config_file"]["plans"][0]["capabilities"] = []
    with pytest.raises(ValueError, match="capabilities differ from its selected features"):
        validate_module_contract_updates(saved, context)
    saved = module.normalize_subscription_contract(_design(), context)
    saved["module_contract_updates"].pop()
    with pytest.raises(ValueError, match="module_contract_updates differ from gates derived"):
        validate_module_contract_updates(saved, context)
    saved = module.normalize_subscription_contract(_design(), context)
    saved["selected_features_by_plan"]["free"].append("module.tasks.create_task")
    with pytest.raises(ValueError, match="must not repeat a feature"):
        validate_module_contract_updates(saved, context)


def test_model_authored_capabilities_and_gate_mapping_are_rejected() -> None:
    output = _design()
    output["subscription_config_file"]["plans"][0]["capabilities"] = ["task.create"]
    with pytest.raises(ValueError, match="select included_features instead"):
        module.normalize_subscription_contract(output, _context())
    output = _design()
    output["module_contract_updates"] = []
    with pytest.raises(ValueError, match="must not author derived fields"):
        module.normalize_subscription_contract(output, _context())


def test_workflow_feature_requires_agentic_concept_and_approved_surface() -> None:
    output = _design()
    output["subscription_config_file"]["plans"][1]["included_features"].append("workflow.TaskAnalysis")
    with pytest.raises(ValueError, match="unavailable features"):
        module.normalize_subscription_contract(output, _context())
    with pytest.raises(ValueError, match="unavailable features"):
        module.normalize_subscription_contract(output, _context(agentic=True))


def test_approved_workflow_metering_does_not_make_workflow_access_sellable() -> None:
    output = _design()
    declaration = {"surface_type": "workflow", "surface_id": "TaskAnalysis", "action_id": None}
    output["metering_declarations"] = [declaration]
    saved = module.normalize_subscription_contract(output, _context(agentic=True))
    assert saved["metering_declarations"] == [declaration]
    assert saved["workflow_contract_updates"] == []


def test_distinct_approved_features_cannot_share_a_derived_capability() -> None:
    output = _design()
    output["subscription_config_file"]["plans"][1]["included_features"].extend(
        ["module.Tasks.create_task"],
    )
    surfaces = _surface_map()
    surfaces["surfaces"].append({
        "surface_id": "Tasks", "surface_kind": "module", "owner": "app",
        "owned_mutations": ["create_task"],
    })
    with pytest.raises(ValueError, match="derive the same capability id"):
        module.normalize_subscription_contract(output, _context(surface_map=surfaces))


def test_usage_limit_names_unavailable_feature_instead_of_missing_plan_selection() -> None:
    output = _design()
    output["subscription_config_file"]["plans"][0]["usage_limits"][0]["feature_id"] = "workflow.TaskAnalysis"
    with pytest.raises(ValueError, match="usage limit references unavailable feature 'workflow.TaskAnalysis'") as error:
        module.normalize_subscription_contract(output, _context(agentic=True))
    assert "that it does not include" not in str(error.value)
    assert "Valid features:" in str(error.value)
    assert "from the plan, usage limit, or add-on" in str(error.value)


def test_usage_limit_feature_must_belong_to_its_plan() -> None:
    output = _design()
    output["subscription_config_file"]["plans"][0]["usage_limits"][0]["feature_id"] = (
        "module.tasks.summarize_tasks"
    )
    with pytest.raises(ValueError, match="that it does not include"):
        module.normalize_subscription_contract(output, _context())


def test_noop_contract_does_not_require_action_inventory() -> None:
    assert validate_module_contract_updates({"contract_required": False}, ContextVariablesBridge({})) == {}


def test_designer_declares_inventory_hook_and_no_model_authored_gates() -> None:
    root = Path(__file__).resolve().parents[1] / "factory_app/workflows/SubscriptionContractDesigner"
    middleware = yaml.safe_load((root / "middleware.yaml").read_text(encoding="utf-8"))
    assert middleware["prompt_middleware"] == [{
        "agent": "ContractDesignerAgent", "filename": "../_shared/subscription_contract_context.py",
        "function": "inject_subscription_action_inventory",
    }]
    schema = yaml.safe_load((root / "structured_outputs.yaml").read_text(encoding="utf-8"))
    models = schema["models"]
    plan_fields = models["SubscriptionPlan"]["fields"]
    assert "included_features" in plan_fields
    assert "capabilities" not in plan_fields
    output_fields = models["SubscriptionContractOutput"]["fields"]
    assert "module_contract_updates" not in output_fields
    assert "workflow_contract_updates" not in output_fields
    assert "code_files" not in output_fields
