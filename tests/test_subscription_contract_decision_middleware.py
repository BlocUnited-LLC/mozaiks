"""The designer prompt receives the same concept decision as save validation."""

from __future__ import annotations

import json
from pathlib import Path
from types import MappingProxyType, SimpleNamespace

from factory_app.workflows._shared.subscription_contract_context import (
    concept_requires_contract,
    inject_subscription_action_inventory,
)
from mozaiksai.core.workflow.agents.factory import ContextVariablesBridge

_CONTEXT_FIXTURE = (
    Path(__file__).parent / "fixtures" / "subscription_contract_designer_4582201a_context.json"
)


def _recorded_context(**changes: object) -> ContextVariablesBridge:
    data = json.loads(_CONTEXT_FIXTURE.read_text(encoding="utf-8"))
    data.update(changes)
    return ContextVariablesBridge(data)


def _prompt(context: ContextVariablesBridge) -> str:
    agent = SimpleNamespace(
        name="ContractDesignerAgent", context_variables=context, system_message="BASE SYSTEM PROMPT",
    )
    inject_subscription_action_inventory(agent, [])
    return agent.system_message


def test_recorded_concept_decision_leads_designer_prompt() -> None:
    context = _recorded_context()
    blueprint = context.get("concept_blueprint")
    assert isinstance(blueprint, MappingProxyType)
    assert isinstance(blueprint["monetization_intent"], MappingProxyType)
    assert concept_requires_contract(context) is not None

    prompt = _prompt(context)

    assert prompt.startswith("[CONTRACT DECISION]")
    assert 'money_flow_summary: "Users can subscribe to a free tier or a paid Pro version, unlocking advanced features and unlimited task management."' in prompt
    assert "contract_required=true" in prompt
    assert "subscription_config_file plan design" in prompt
    assert "The no-op contract is unavailable." in prompt
    assert prompt.index("[CONTRACT DECISION]") < prompt.index("BASE SYSTEM PROMPT")


def test_undetermined_concept_adds_no_contract_decision() -> None:
    context = _recorded_context(concept_blueprint={"agentic_capabilities": []})
    assert concept_requires_contract(context) is None

    prompt = _prompt(context)

    assert "[CONTRACT DECISION]" not in prompt
    assert "[REQUIRED CORRECTION]" not in prompt
    assert "[APPROVED PRICING FEATURE INVENTORY]" in prompt


def test_brownfield_retains_no_forced_contract_decision() -> None:
    context = _recorded_context(brownfield_build_path="full_migration")
    assert concept_requires_contract(context) is None

    prompt = _prompt(context)

    assert "[CONTRACT DECISION]" not in prompt


def test_requested_changes_lead_retry_prompt() -> None:
    context = _recorded_context(subscription_contract_review_response={
        "action": "request_changes",
        "requested_changes": "Provide a valid subscription_config_file plan design.",
    })
    review = context.get("subscription_contract_review_response")
    assert isinstance(review, MappingProxyType)

    prompt = _prompt(context)

    assert prompt.startswith("[REQUIRED CORRECTION]\nProvide a valid subscription_config_file plan design.")
    assert prompt.index("[REQUIRED CORRECTION]") < prompt.index("[CONTRACT DECISION]")
    assert prompt.index("[CONTRACT DECISION]") < prompt.index("BASE SYSTEM PROMPT")
