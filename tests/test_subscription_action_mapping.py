"""Pricing selections close over DesignDocs features and derive runtime gates."""

from __future__ import annotations

import logging
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
from tests.factory_export_fixture import summary_record


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


def _metering_declaration(*, wallet_id: str = "ai_tokens", action_id: str | None = "create_task") -> dict:
    return {
        "surface_type": "module_action", "surface_id": "tasks", "action_id": action_id,
        "wallet_id": wallet_id, "scope": "user", "enforcement": "commit_actual",
        "estimate": None, "idempotency_key_source": "user_id",
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


async def test_inventory_excludes_canonical_reads_facades_and_unapproved_workflows() -> None:
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
    await inject_subscription_action_inventory(agent, [])
    assert "[APPROVED PRICING FEATURE INVENTORY]" in agent.system_message
    assert "module.tasks.create_task" in agent.system_message
    assert "list_tasks" not in agent.system_message
    assert "workflow.TaskAnalysis" not in agent.system_message


@pytest.fixture
def reviewed_write_design(monkeypatch):
    from factory_app.workflows._shared import subscription_contract_context
    from mozaiksai.core.workflow.generator_support.design_access_policy import (
        access_policy_sha256,
        explicit_module_writes,
    )

    data = _context().snapshot()
    data["user_id"] = "owner"
    data["experience_spec"] = {"pages": []}
    surface = data["design_surface_map"]["surfaces"][0]
    surface["owned_mutations"] = ["create_task", "update_task"]
    collection = data["data_contract"]["surfaces"][0]["collections"][0]
    collection["fields"].extend([{"name": "title", "type": "string"}, {"name": "summary", "type": "string"}])
    collection["lifecycle"]["module_writes"] = [{"operation": op, "fields": ["title"]} for op in ("create", "update")]
    policy = {"roles": [], "grants": []}
    summary = {
        "backend_markdown": "Private design sentinel", "experience_spec": data["experience_spec"],
        "data_contract": data["data_contract"], "surface_map": data["design_surface_map"], "access_policy": policy,
        "access_policy_review": {
            "status": "approved", "target_app_id": "task-tracker", "build_id": "build-test",
            "review_id": "review-1", "reviewed_by": "owner", "policy_sha256": access_policy_sha256(
                policy, data["experience_spec"], module_writes=explicit_module_writes(data["data_contract"])),
        },
    }
    record = summary_record("design_docs", summary, app="task-tracker")
    data["design_docs_artifact_version_id"] = record.id
    store = SimpleNamespace(get_build_record=AsyncMock(return_value=record))
    monkeypatch.setattr(subscription_contract_context, "get_artifact_store", lambda: store)
    return data, record, store


async def test_pricing_prompt_and_save_use_exact_reviewed_write_inventory(reviewed_write_design, side_effects):
    data, record, store = reviewed_write_design
    context = ContextVariablesBridge(data)
    agent = SimpleNamespace(name="ContractDesignerAgent", context_variables=context, system_message="base")
    await inject_subscription_action_inventory(agent, [])
    assert "module.tasks.summarize_tasks" in agent.system_message and "module.tasks.update_task" in agent.system_message
    assert "delete_task" not in agent.system_message and "Private design sentinel" not in agent.system_message
    assert "design_docs_build_artifact" not in context.snapshot()
    output = _design()
    for plan in output["subscription_config_file"]["plans"]:
        plan["included_features"].remove("module.tasks.delete_task")
    result = await module.save_subscription_contract(StructuredOutputOverlay(context, output))
    assert result["success"] and store.get_build_record.await_count == 2
    store.get_build_record.assert_awaited_with(app_id="task-tracker", build_record_id=record.id)
    assert side_effects[1].await_args.kwargs["canonical_inputs_version"] == {"design_docs": record.id}
    assert "design_docs_build_artifact" not in context.snapshot()


@pytest.mark.parametrize("problem", ["absent", "foreign_owner", "changed_manifest", "different_surface", "foreign_pin"])
async def test_invalid_design_cannot_supply_inventory_or_save_a_subscription(reviewed_write_design, side_effects, problem):
    data, record, _store = reviewed_write_design
    data["design_docs_build_artifact"] = record.model_dump(mode="json", by_alias=True)
    if problem == "absent":
        data.pop("design_docs_artifact_version_id")
    elif problem == "foreign_owner":
        data["user_id"] = "another-owner"
    elif problem == "foreign_pin":
        data["design_docs_artifact_version_id"] = "different-design"
    elif problem == "changed_manifest":
        record.files_manifest[0].sha256 = "0" * 64
    else:
        data["design_surface_map"] = {"surfaces": []}
    context = ContextVariablesBridge(data)
    agent = SimpleNamespace(name="ContractDesignerAgent", context_variables=context, system_message="base")
    with pytest.raises(ValueError):
        await inject_subscription_action_inventory(agent, [])
    assert agent.system_message == "base"
    result = await module.save_subscription_contract(StructuredOutputOverlay(context, _design()))
    assert result["review_status"] == "changes_requested" and result.get("error")
    side_effects[0].assert_not_awaited()
    side_effects[1].assert_not_awaited()


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
    output["subscription_config_file"]["token_wallets"] = [{"wallet_id": "ai_tokens"}]
    declaration = {
        **_metering_declaration(), "surface_type": "workflow", "surface_id": "TaskAnalysis",
        "action_id": None,
    }
    output["metering_declarations"] = [declaration]
    saved = module.normalize_subscription_contract(output, _context(agentic=True))
    assert saved["metering_declarations"] == [declaration]
    assert saved["workflow_contract_updates"] == []


def test_no_wallet_drops_every_metering_declaration_and_explains_each_drop(caplog) -> None:
    output = _design()
    output["metering_declarations"] = [
        _metering_declaration(action_id=None),
        _metering_declaration(action_id="summarize_tasks"),
    ]
    with caplog.at_level(logging.INFO, logger=module.__name__):
        saved = module.normalize_subscription_contract(output, _context())
    assert saved["metering_declarations"] == []
    # Review notes are read by a person: plain sentences, never a dict repr.
    assert saved["validation_notes"] == [
        "Removed a usage-metering entry for tasks: this design sells no token wallets, "
        "so there is nothing to charge.",
        "Removed a usage-metering entry for tasks.summarize_tasks: this design sells no token wallets, "
        "so there is nothing to charge.",
    ]
    assert not any("{" in note for note in saved["validation_notes"])
    assert sum("METERING_DECLARATION_DROPPED" in record.message for record in caplog.records) == 2
    assert saved["module_contract_updates"] == [{
        "module_id": "tasks", "action_id": "summarize_tasks",
        "entitlement_gate": "feature.module.tasks.summarize_tasks", "metering": None,
    }]


def test_single_declared_wallet_rebinds_metering_before_gate_derivation(caplog) -> None:
    output = _design()
    output["subscription_config_file"]["token_wallets"] = [{"wallet_id": "actual_wallet"}]
    output["metering_declarations"] = [_metering_declaration(wallet_id="invented_wallet")]
    with caplog.at_level(logging.INFO, logger=module.__name__):
        saved = module.normalize_subscription_contract(output, _context())
    assert saved["metering_declarations"][0]["wallet_id"] == "actual_wallet"
    assert any("METERING_DECLARATION_WALLET_BOUND" in record.message for record in caplog.records)
    assert next(update for update in saved["module_contract_updates"]
                if update["action_id"] == "create_task") == {
        "module_id": "tasks", "action_id": "create_task", "entitlement_gate": None,
        "metering": saved["metering_declarations"][0],
    }


def test_undeclared_wallet_with_multiple_choices_lists_declared_wallets() -> None:
    output = _design()
    output["subscription_config_file"]["token_wallets"] = [
        {"wallet_id": "first_wallet"}, {"wallet_id": "second_wallet"},
    ]
    declaration = _metering_declaration(wallet_id="invented_wallet")
    output["metering_declarations"] = [declaration]
    with pytest.raises(ValueError) as error:
        module.normalize_subscription_contract(output, _context())
    message = str(error.value)
    assert repr(declaration) in message
    assert "['first_wallet', 'second_wallet']" in message
    assert "remove the declaration" in message


def test_invalid_module_metering_action_lists_all_approved_pairs() -> None:
    output = _design()
    output["subscription_config_file"]["token_wallets"] = [{"wallet_id": "ai_tokens"}]
    declaration = _metering_declaration(action_id=None)
    output["metering_declarations"] = [declaration]
    with pytest.raises(ValueError) as error:
        module.normalize_subscription_contract(output, _context())
    message = str(error.value)
    assert repr(declaration) in message
    assert "Valid (surface_id, action_id) pairs:" in message
    assert "('tasks', 'create_task')" in message
    assert "('tasks', 'summarize_tasks')" in message
    assert "remove the declaration" in message


def test_invalid_workflow_metering_surface_lists_approved_surfaces() -> None:
    output = _design()
    output["subscription_config_file"]["token_wallets"] = [{"wallet_id": "ai_tokens"}]
    declaration = {
        **_metering_declaration(), "surface_type": "workflow", "surface_id": "unknown_workflow",
        "action_id": None,
    }
    output["metering_declarations"] = [declaration]
    with pytest.raises(ValueError) as error:
        module.normalize_subscription_contract(output, _context(agentic=True))
    message = str(error.value)
    assert repr(declaration) in message
    assert "Valid workflow surfaces: ['TaskAnalysis']" in message
    assert "Remove the declaration" in message


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
    from mozaiksai.core.workflow.context.authority import build_context_authority_policy

    root = Path(__file__).resolve().parents[1] / "factory_app/workflows/SubscriptionContractDesigner"
    context = yaml.safe_load((root / "context_variables.yaml").read_text(encoding="utf-8"))
    pin = "design_docs_artifact_version_id"
    authority = build_context_authority_policy(workflow_name="SubscriptionContractDesigner", definitions=context["definitions"])
    assert authority.can_write(pin, writer_id="transition_router")
    for writer in ("caller_input", "agent_text", "structured_output", "deterministic_tool"):
        assert not authority.can_write(pin, writer_id=writer)
    assert context["definitions"][pin]["model_visible"] is False
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
