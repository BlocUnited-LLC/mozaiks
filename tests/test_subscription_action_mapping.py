"""Capability decisions close over approved actions before review or materialization."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from types import MappingProxyType, SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import yaml

from factory_app.workflows._shared.subscription_contract_context import (
    approved_module_actions,
    inject_subscription_action_inventory,
    validate_module_contract_updates,
)
from factory_app.workflows.SubscriptionContractDesigner.tools import (
    save_subscription_contract as module,
)
from mozaiksai.core.workflow.agents.factory import ContextVariablesBridge
from mozaiksai.core.workflow.context.structured_output_overlay import StructuredOutputOverlay
from tests.factory_context import factory_context


def _surface_map() -> dict:
    return {"surfaces": [
        {"surface_id": "task_management", "surface_kind": "module", "owner": "app",
         "owned_mutations": ["create_task", "list_tasks", "update_task", "view_dashboard"]},
        {"surface_id": "platform_identity", "surface_kind": "module", "owner": "platform",
         "owned_mutations": ["authenticate"]},
        {"surface_id": "TaskAnalysis", "surface_kind": "workflow", "owner": "app",
         "owned_mutations": ["analyze"]},
    ]}


def _contract() -> dict:
    # Exact capability sets from OSS 93a703e8 / chat 6ebcbc4b-5422-4953-a1bb-cb4ada3db176.
    return {
        "contract_required": True,
        "app_name": "Task Tracker",
        "subscription_config_file": {
            "schema_version": "mozaiks.subscriptions.v1", "label": "Task plans", "default_plan_id": "free",
            "plans": [
                {"plan_id": "free", "label": "Free", "capabilities": ["task.create", "task.view"]},
                {"plan_id": "pro", "label": "Pro",
                 "capabilities": ["task.create", "task.view", "task.edit", "dashboard.view"]},
            ],
        },
        "module_contract_updates": [
            {"module_id": "task_management", "action_id": "update_task", "entitlement_gate": "task.edit", "metering": None},
            {"module_id": "task_management", "action_id": "view_dashboard", "entitlement_gate": "dashboard.view", "metering": None},
        ],
    }


def _context(output: dict, surface_map: dict | None = None) -> StructuredOutputOverlay:
    context = ContextVariablesBridge(factory_context({
        "app_id": "task-tracker", "chat_id": "subscription-chat",
        "design_surface_map": _surface_map() if surface_map is None else surface_map,
    }))
    return StructuredOutputOverlay(context, output)


@pytest.fixture
def side_effects(monkeypatch):
    review = AsyncMock(return_value={"approved": True})
    persist = AsyncMock(return_value=SimpleNamespace(id="subscription-contract-version"))
    monkeypatch.setattr(module, "use_ui_tool", review)
    monkeypatch.setattr(module, "persist_summary_artifact", persist)
    return review, persist


def test_inventory_injection_reads_real_bridge_and_contains_only_approved_module_actions() -> None:
    context = ContextVariablesBridge({"design_surface_map": _surface_map()})
    assert isinstance(context.get("design_surface_map"), MappingProxyType)
    assert approved_module_actions(context) == {
        "task_management": ["create_task", "list_tasks", "update_task", "view_dashboard"],
    }
    agent = SimpleNamespace(name="ContractDesignerAgent", context_variables=context, system_message="base")

    inject_subscription_action_inventory(agent, [])

    assert "[APPROVED ENTITLEMENT ACTION INVENTORY]" in agent.system_message
    assert "task_management:" in agent.system_message
    assert "- update_task" in agent.system_message
    assert "platform_identity" not in agent.system_message
    assert "TaskAnalysis" not in agent.system_message


@pytest.mark.asyncio
async def test_exact_live_plan_shape_saves_model_selected_gates(side_effects) -> None:
    context = _context(_contract())
    assert isinstance(context.get("design_surface_map"), MappingProxyType)

    result = await module.save_subscription_contract(context)

    assert result["success"] is True
    assert result["review_status"] == "confirmed"
    contract = context.get("subscription_contract")
    assert validate_module_contract_updates(contract, context) == {
        "task_management": {"update_task": "task.edit", "view_dashboard": "dashboard.view"},
    }
    review, persist = side_effects
    review.assert_awaited_once()
    persist.assert_awaited_once()
    assert persist.await_args.kwargs["summary_payload"]["module_contract_updates"] == _contract()["module_contract_updates"]


@pytest.mark.asyncio
@pytest.mark.parametrize("updates, expected", [
    ([], "Unmapped capability ids: ['dashboard.view', 'task.edit']"),
    ([{"module_id": "tasks", "action_id": "update_task", "entitlement_gate": "task.edit"}], "unapproved action 'tasks'"),
    ([{"module_id": "task_management", "action_id": "edit", "entitlement_gate": "task.edit"}], "unapproved action"),
    ([{"module_id": "task_management", "action_id": "update_task", "entitlement_gate": "invented.capability"}], "Valid capability ids"),
    ([{"module_id": "platform_identity", "action_id": "authenticate", "entitlement_gate": "task.edit"}], "unapproved action"),
    ([{"module_id": "task_management", "action_id": "update_task", "entitlement_gate": "task.edit"},
      {"module_id": "task_management", "action_id": "update_task", "entitlement_gate": "dashboard.view"}], "Conflicting entitlement_gate"),
])
async def test_ambiguous_mapping_returns_valid_identifiers_before_review(updates, expected, side_effects) -> None:
    output = _contract()
    output["module_contract_updates"] = updates
    context = _context(output)

    result = await module.save_subscription_contract(context)

    assert result["review_status"] == "changes_requested"
    assert expected in result["error"]
    assert "task_management" in result["error"]
    assert "update_task" in result["error"]
    assert "view_dashboard" in result["error"]
    assert context.get("subscription_contract") is None
    review, persist = side_effects
    review.assert_not_awaited()
    persist.assert_not_awaited()


def test_differing_capabilities_cannot_hide_behind_one_valid_gate() -> None:
    output = _contract()
    output["module_contract_updates"].pop()
    with pytest.raises(ValueError, match=r"Unmapped capability ids: \['dashboard.view'\]"):
        validate_module_contract_updates(output, _context(output))


def test_absent_approved_inventory_does_not_authorize_invented_actions() -> None:
    output = _contract()
    with pytest.raises(ValueError, match=r"Valid approved module/action ids: \{\}"):
        validate_module_contract_updates(output, _context(output, {}))


def test_identical_plan_grants_still_require_a_model_selected_action_gate() -> None:
    output = _contract()
    for plan in output["subscription_config_file"]["plans"]:
        plan["capabilities"] = ["task.view"]
    output["module_contract_updates"] = []
    with pytest.raises(ValueError, match="does not select any action entitlement_gate"):
        validate_module_contract_updates(output, _context(output))
    output["module_contract_updates"] = [
        {"module_id": "task_management", "action_id": "list_tasks", "entitlement_gate": "task.view"},
    ]
    assert validate_module_contract_updates(output, _context(output)) == {"task_management": {"list_tasks": "task.view"}}


def test_identical_mapping_duplicates_are_deterministic() -> None:
    output = _contract()
    output["module_contract_updates"].append(deepcopy(output["module_contract_updates"][0]))
    assert validate_module_contract_updates(output, _context(output)) == {
        "task_management": {"update_task": "task.edit", "view_dashboard": "dashboard.view"},
    }


def test_product_scoped_plans_close_the_same_action_mapping() -> None:
    output = _contract()
    config = output["subscription_config_file"]
    config["schema_version"] = "mozaiks.subscriptions.v2"
    config["products"] = [
        {"product_id": "tasks", "plans": config.pop("plans")},
        {"product_id": "notes", "plans": [{"plan_id": "notes", "capabilities": ["notes.view"]}]},
    ]
    assert validate_module_contract_updates(output, _context(output)) == {
        "task_management": {"update_task": "task.edit", "view_dashboard": "dashboard.view"},
    }
    output["module_contract_updates"].pop()
    with pytest.raises(ValueError, match=r"Unmapped capability ids: \['dashboard.view'\]"):
        validate_module_contract_updates(output, _context(output))


def test_metering_only_update_still_closes_action_reference() -> None:
    output = _contract()
    for plan in output["subscription_config_file"]["plans"]:
        plan["capabilities"] = []
    output["module_contract_updates"] = [{"module_id": "task_management", "action_id": "update_task", "entitlement_gate": None}]
    assert validate_module_contract_updates(output, _context(output)) == {}
    output["module_contract_updates"][0]["action_id"] = "missing"
    with pytest.raises(ValueError, match="unapproved action"):
        validate_module_contract_updates(output, _context(output))


def test_noop_contract_does_not_require_action_inventory() -> None:
    assert validate_module_contract_updates({"contract_required": False}, ContextVariablesBridge({})) == {}


def test_designer_declares_inventory_hook_and_typed_reference_contract() -> None:
    root = Path(__file__).resolve().parents[1] / "factory_app/workflows/SubscriptionContractDesigner"
    middleware = yaml.safe_load((root / "middleware.yaml").read_text(encoding="utf-8"))
    assert middleware["prompt_middleware"] == [{
        "agent": "ContractDesignerAgent", "filename": "../_shared/subscription_contract_context.py",
        "function": "inject_subscription_action_inventory",
    }]
    schema = yaml.safe_load((root / "structured_outputs.yaml").read_text(encoding="utf-8"))
    fields = schema["models"]["ModuleContractUpdate"]["fields"]
    assert "surface_id" in fields["module_id"]["description"]
    assert "owned_mutations" in fields["action_id"]["description"]
    assert "plans" in fields["entitlement_gate"]["description"]
