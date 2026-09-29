"""Replay recorded pricing choices through the closed DesignDocs feature inventory.

The fixture holds 27 distinct paid designs from 35 SubscriptionContractDesigner
WAL packets in 26 chats. ``recorded`` records only the old model-authored
capability and module gate fields. ``designer_output`` is their mechanical
translation: each old capability maps to the approved module actions it gated;
each plan selects the union of those actions. Unmapped names disappear. This
deliberately does not repair a product decision or invent a feature.

The source DesignDocs chat and its saved design projection travel with every
entry. These tests need no Mongo server, model call, or live infrastructure.
The ec56c080 concept repair below is separately labelled because it changes
the model's original product decision.
"""

from __future__ import annotations

import hashlib
import json
import warnings
from collections import defaultdict
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import yaml

from factory_app.workflows._shared import subscription_contract_context
from factory_app.workflows._shared.hook_utils import workflow_context_path
from factory_app.workflows._shared.subscription_contract_context import (
    approved_feature_inventory,
    validate_module_contract_updates,
)
from factory_app.workflows.AppGenerator.tools.app_plan_review import review_app_build_plan
from factory_app.workflows.SubscriptionContractDesigner.tools import save_subscription_contract
from mozaiksai.core.workflow.agents.factory import ContextVariablesBridge
from mozaiksai.core.workflow.context.frozen import detach
from mozaiksai.core.workflow.context.structured_output_overlay import StructuredOutputOverlay
from mozaiksai.resources import resolve_factory_app_root
from tests.test_app_plan_task_identity_repair import _live_plan
from tests.test_continuous_deterministic_materialization import _load_models

ROOT = Path(__file__).resolve().parents[1]
CORPUS = json.loads((Path(__file__).parent / "fixtures" / "pricing_design_corpus.json").read_text(encoding="utf-8"))


def _mapped_features(entry: dict) -> dict[str, set[str]]:
    result: dict[str, set[str]] = defaultdict(set)
    for update in entry["recorded"]["module_contract_updates"]:
        if gate := update.get("entitlement_gate"):
            result[gate].add(f"module.{update['module_id']}.{update['action_id']}")
    return result


def _gates(contract: dict) -> list[dict[str, str]]:
    return sorted(
        (
            {"module_id": update["module_id"], "action_id": update["action_id"],
             "capability_id": update["entitlement_gate"]}
            for update in contract["module_contract_updates"] if update.get("entitlement_gate")
        ),
        key=lambda item: (item["module_id"], item["action_id"]),
    )


def _plan_capabilities(contract: dict) -> list[dict]:
    return [
        {"plan_id": plan["plan_id"], "capabilities": sorted(plan["capabilities"])}
        for plan in contract["subscription_config_file"]["plans"]
    ]


def test_corpus_provenance_and_worktree_origins() -> None:
    fixtures = CORPUS["fixtures"]
    assert (CORPUS["outputs_unique"], CORPUS["outputs_total"], CORPUS["chats"]) == (27, 35, 26)
    assert len(fixtures) == 27
    assert len({entry["id"] for entry in fixtures}) == 27
    assert len({entry["body_sha256"] for entry in fixtures}) == 27
    assert sum(entry["occurrences"] for entry in fixtures) == 35
    assert len({entry["chat_id"] for entry in fixtures}) == 26
    assert all(entry["design_docs_chat_id"] and entry["context"]["design_surface_map"]["surfaces"]
               for entry in fixtures)
    for entry in fixtures:
        projection = {
            key: entry["context"][key]
            for key in ("design_surface_map", "experience_spec")
        }
        if entry["context"].get("data_contract") is not None:
            projection["data_contract"] = entry["context"]["data_contract"]
        encoded = json.dumps(projection, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        assert hashlib.sha256(encoded).hexdigest() == entry["saved_design_projection_sha256"], entry["id"]
    assert "module.<module_id>.<action_id>" in CORPUS["translation"]
    assert Path(save_subscription_contract.__file__).resolve().is_relative_to(ROOT)
    assert Path(subscription_contract_context.__file__).resolve().is_relative_to(ROOT)
    assert resolve_factory_app_root() == (ROOT / "factory_app").resolve()
    build_context_path = workflow_context_path("mozaikspay", "context.yaml").resolve()
    assert build_context_path.is_relative_to(ROOT)
    assert build_context_path == (ROOT / "factory_app" / "build_context" / "mozaikspay" / "context.yaml")
    assert build_context_path.is_file()


@pytest.mark.parametrize("entry", CORPUS["fixtures"], ids=lambda entry: entry["id"])
def test_fixture_translation_preserves_only_actions_backed_by_old_gates(entry: dict) -> None:
    output = entry["designer_output"]
    assert not {"module_contract_updates", "workflow_contract_updates", "code_files"} & output.keys()
    mapping = _mapped_features(entry)
    plans = output["subscription_config_file"]["plans"]
    for original, plan in zip(entry["recorded"]["plans"], plans, strict=True):
        assert original["plan_id"] == plan["plan_id"]
        assert "capabilities" not in plan
        assert plan["included_features"] == sorted({
            feature for capability in original["capabilities"] for feature in mapping[capability]
        })
        assert all("capability_id" not in limit for limit in plan.get("usage_limits") or [])
    expected_gates = {
        (update["module_id"], update["action_id"])
        for update in entry["expected"]["module_gates"]
    }
    assert expected_gates == {
        tuple(feature.split(".")[1:])
        for plan in plans for feature in plan["included_features"]
    }


@pytest.mark.parametrize("entry", CORPUS["fixtures"], ids=lambda entry: entry["id"])
def test_recorded_design_derives_pinned_plans_and_gates(entry: dict) -> None:
    context = ContextVariablesBridge(deepcopy(entry["context"]))
    output = deepcopy(entry["designer_output"])
    approved = set(approved_feature_inventory(context))
    assert all(feature in approved for plan in output["subscription_config_file"]["plans"]
               for feature in plan["included_features"])

    normalized = save_subscription_contract.normalize_subscription_contract(output, context)
    assert output == entry["designer_output"], "normalization must not edit the model output"
    assert normalized["selected_features_by_plan"] == {
        plan["plan_id"]: plan["included_features"]
        for plan in output["subscription_config_file"]["plans"]
    }
    assert _gates(normalized) == entry["expected"]["module_gates"]
    assert _plan_capabilities(normalized) == entry["expected"]["plan_capabilities"]
    assert validate_module_contract_updates(normalized, context) == {
        module_id: {gate["action_id"]: gate["capability_id"] for gate in entry["expected"]["module_gates"]
                    if gate["module_id"] == module_id}
        for module_id in {gate["module_id"] for gate in entry["expected"]["module_gates"]}
    }
    assert yaml.safe_load(normalized["code_files"][0]["content"]) == normalized["subscription_config_file"]


def test_product_incoherence_flags_are_advisory() -> None:
    recorded_empty: list[str] = []
    translated_empty: list[str] = []
    top_only_create: list[str] = []
    for entry in CORPUS["fixtures"]:
        recorded_plans = entry["recorded"]["plans"]
        plans = entry["designer_output"]["subscription_config_file"]["plans"]
        if not recorded_plans[0]["capabilities"]:
            recorded_empty.append(entry["id"])
        if not plans[0]["included_features"]:
            translated_empty.append(entry["id"])
        non_top = {feature for plan in plans[:-1] for feature in plan["included_features"]}
        top_creates = {feature for feature in plans[-1]["included_features"]
                       if feature.startswith("module.") and feature.split(".")[-1].startswith("create_")}
        if top_creates - non_top:
            top_only_create.append(entry["id"])
    assert len(recorded_empty) == 3
    assert len(translated_empty) == 25  # Most old names had no approved action mapping.
    assert len(top_only_create) == 1
    assert top_only_create[0].startswith("ec56c080-")
    warnings.warn(
        "Pricing corpus advisory: recorded cheapest plan empty in " + ", ".join(recorded_empty)
        + "; mechanically translated cheapest plan empty in " + ", ".join(translated_empty)
        + "; core create available only to top plan in " + ", ".join(top_only_create),
        UserWarning,
        stacklevel=1,
    )


def test_57c78c5f_good_design_keeps_its_action_gate_set() -> None:
    entry = next(item for item in CORPUS["fixtures"] if item["chat_id"] == "0d620f75-828d-4a66-ad1e-b8b18dd293e7")
    context = ContextVariablesBridge(deepcopy(entry["context"]))
    normalized = save_subscription_contract.normalize_subscription_contract(
        deepcopy(entry["designer_output"]), context,
    )
    old = {(update["module_id"], update["action_id"])
           for update in entry["recorded"]["module_contract_updates"] if update.get("entitlement_gate")}
    new = {(update["module_id"], update["action_id"]) for update in normalized["module_contract_updates"]}
    assert old == new == {
        ("task_management", "create_task"),
        ("task_management", "delete_task"),
        ("task_management", "update_task"),
    }
    assert normalized["selected_features_by_plan"] == {
        "free": ["module.task_management.create_task", "module.task_management.delete_task"],
        "pro": ["module.task_management.create_task", "module.task_management.delete_task",
                "module.task_management.update_task"],
    }


@pytest.mark.asyncio
async def test_ec56c080_labelled_concept_repair_saves_and_app_plan_accepts(monkeypatch: pytest.MonkeyPatch) -> None:
    """The new choice follows the concept's limited-free-task intent, unlike the recorded choice."""
    entry = next(item for item in CORPUS["fixtures"] if item["chat_id"] == "ec56c080-ccbb-4d2b-9dbb-9cccd6931bbd")
    assert entry["design_docs_chat_id"] == "fdbfe738-2c10-473a-8178-cd4544e79379"
    assert "Free tier with limited tasks" in entry["context"]["concept_blueprint"]["monetization_intent"][
        "money_flow_summary"
    ]
    output = deepcopy(entry["designer_output"])
    plans = output["subscription_config_file"]["plans"]
    core = sorted(f"module.task_management.{action}" for action in ("create_task", "update_task", "delete_task"))
    advanced = "module.task_management.summarize_tasks"
    # Labelled human repair: the WAL packet gave free no capability and pro all
    # four actions. This replacement expresses the approved concept's decision.
    plans[0]["included_features"] = core
    plans[1]["included_features"] = sorted([*core, advanced])
    plans[0]["usage_limits"] = [{
        "meter_id": "tasks.created", "label": "Tasks created per month", "unit": "requests",
        "monthly_limit": 10, "feature_id": "module.task_management.create_task",
    }]
    context = ContextVariablesBridge(deepcopy(entry["context"]))
    review = AsyncMock(return_value={"action": "confirm", "approved": True, "status": "approved"})
    persist = AsyncMock(return_value=SimpleNamespace(id="av-ec56-pricing-proof"))
    monkeypatch.setattr(save_subscription_contract, "use_ui_tool", review)
    monkeypatch.setattr(save_subscription_contract, "persist_summary_artifact", persist)

    result = await save_subscription_contract.save_subscription_contract(StructuredOutputOverlay(context, output))

    assert result["success"] is True and result["review_status"] == "confirmed", result
    review.assert_awaited_once()
    persist.assert_awaited_once()
    saved = detach(context.get("subscription_contract"))
    assert persist.await_args.kwargs["summary_payload"] == saved
    assert saved["selected_features_by_plan"] == {"free": core, "pro": sorted([*core, advanced])}
    assert {update["action_id"] for update in saved["module_contract_updates"]} == {
        "create_task", "update_task", "delete_task", "summarize_tasks",
    }
    assert validate_module_contract_updates(saved, context) == {
        "task_management": {
            action: f"feature.module.task_management.{action}"
            for action in ("create_task", "update_task", "delete_task", "summarize_tasks")
        },
    }
    config = yaml.safe_load(context.get("subscription_contract_files")[0]["content"])
    assert config == saved["subscription_config_file"]
    free, pro = config["plans"]
    assert set(free["capabilities"]) == {f"feature.{feature}" for feature in core}
    assert set(pro["capabilities"]) == {f"feature.{feature}" for feature in [*core, advanced]}
    assert free["usage_limits"][0] == {
        "meter_id": "tasks.created", "label": "Tasks created per month", "unit": "requests",
        "monthly_limit": 10, "capability_id": "feature.module.task_management.create_task",
    }

    # Build a task plan from the captured DesignDocs actions and saved contract.
    # AppGenerator's public review performs the real gate.
    _load_models()
    plan, app_context = _live_plan(monetized=True, repeated_ids=False)
    task_surface = next(
        surface for surface in entry["context"]["design_surface_map"]["surfaces"]
        if surface["surface_id"] == "task_management"
    )
    approved_actions = [*task_surface["owned_mutations"], *task_surface["custom_reads"]]
    assert set(approved_actions) == {"create_task", "update_task", "delete_task", "summarize_tasks"}
    task_pack = next(
        pack for pack in plan["capability_packs"] if pack["capability_pack_id"] == "task_management"
    )
    task_pack["operations"] = list(dict.fromkeys([*task_pack["operations"], *approved_actions]))
    plan["entities"][0]["operations"] = ["create", "read", "update", "delete"]
    for task in plan["build_tasks"]:
        if task["capability_pack_id"] == "task_management" and task["task_type"] in {
            "module_contract", "business_services",
        }:
            task["initial_message"] += " Implement actions: " + ", ".join(approved_actions) + "."
    for key, value in entry["context"].items():
        if key not in {"user_id", "chat_id", "run_build_binding", "app_id"}:
            app_context.set(key, deepcopy(value))
    app_context.set("subscription_contract", saved)
    app_context.set("app_plan_attempts", 0)
    reviewed = review_app_build_plan(AppBuildPlan=plan, context_variables=app_context)
    assert reviewed["outcome"] == "ready", reviewed
    cached = detach(app_context.get("app_build_plan"))
    assert any(task["task_type"] == "subscription_config" for task in cached["build_tasks"])
    reviewed_pack = next(
        pack for pack in cached["capability_packs"] if pack["capability_pack_id"] == "task_management"
    )
    assert set(approved_actions) <= set(reviewed_pack["operations"])
    contract_task = next(
        task for task in cached["build_tasks"]
        if task["capability_pack_id"] == "task_management" and task["task_type"] == "module_contract"
    )
    assert all(action in contract_task["initial_message"] for action in approved_actions)
    assert {(page["name"], page["route"]) for page in cached["pages"]} == {
        (page["name"], page["route"]) for page in entry["context"]["experience_spec"]["pages"]
    }
