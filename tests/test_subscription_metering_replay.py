"""Replay the SubscriptionContractDesigner output from live chat 5d930588."""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from types import MappingProxyType, SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from factory_app.workflows.SubscriptionContractDesigner.tools import save_subscription_contract
from mozaiksai.core.workflow.agents.factory import ContextVariablesBridge
from mozaiksai.core.workflow.context.frozen import detach
from mozaiksai.core.workflow.context.structured_output_overlay import StructuredOutputOverlay

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.mark.asyncio
async def test_5d930588_undeclared_wallet_metering_is_dropped_before_gate_derivation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = json.loads(
        (FIXTURES / "subscription_contract_designer_5d930588_body.json").read_text(encoding="utf-8")
    )
    starting_context = json.loads(
        (FIXTURES / "subscription_contract_designer_5d930588_context.json").read_text(encoding="utf-8")
    )
    recorded = deepcopy(output)
    declaration = {
        "surface_type": "module_action",
        "surface_id": "task_management_module",
        "action_id": None,
        "wallet_id": "ai_tokens",
        "scope": "user",
        "enforcement": "commit_actual",
        "estimate": None,
        "idempotency_key_source": "user_id",
    }
    assert output["metering_declarations"] == [declaration]
    assert output["subscription_config_file"]["token_wallets"] == []
    assert starting_context["chat_id"] == "5d930588-2fe5-41d8-8b2a-9c59f0c8319c"

    context = ContextVariablesBridge(deepcopy(starting_context))
    assert isinstance(context.get("design_surface_map"), MappingProxyType)
    review = AsyncMock(return_value={"action": "confirm", "approved": True, "status": "approved"})
    persist = AsyncMock(return_value=SimpleNamespace(id="av-5d930588-replay"))
    monkeypatch.setattr(save_subscription_contract, "use_ui_tool", review)
    monkeypatch.setattr(save_subscription_contract, "persist_summary_artifact", persist)

    result = await save_subscription_contract.save_subscription_contract(
        StructuredOutputOverlay(context, output)
    )

    assert result["success"] is True and result["review_status"] == "confirmed", result
    assert output == recorded, "normalization must not edit the recorded model output"
    saved = detach(context.get("subscription_contract"))
    assert saved["metering_declarations"] == []
    assert saved["module_contract_updates"] == [{
        "module_id": "task_management_module",
        "action_id": "summarize_tasks",
        "entitlement_gate": "feature.module.task_management_module.summarize_tasks",
        "metering": None,
    }]
    assert any(
        "task_management_module" in note
        and "ai_tokens" in note
        and "token_wallets is empty" in note
        for note in saved["validation_notes"]
    )
    review.assert_awaited_once()
    assert review.await_args.args[1]["validation_notes"] == saved["validation_notes"]
    persist.assert_awaited_once()
    assert persist.await_args.kwargs["summary_payload"] == saved
