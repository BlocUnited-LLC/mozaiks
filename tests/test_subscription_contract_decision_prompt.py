"""Guard the SCD entry prompt and provenance of the refused live output."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / "factory_app" / "workflows" / "SubscriptionContractDesigner"
FIXTURES = Path(__file__).parent / "fixtures"


def test_entry_prompt_does_not_invite_noop_before_contract_decision() -> None:
    orchestrator = yaml.safe_load((WORKFLOW / "orchestrator.yaml").read_text(encoding="utf-8"))
    agent = yaml.safe_load((WORKFLOW / "agents.yaml").read_text(encoding="utf-8"))["agents"][0]
    sections = {section["id"]: section["content"] for section in agent["prompt_sections"]}

    assert orchestrator["initial_message"] == (
        "ContractDesignerAgent: read the app concept, design docs, and monetization "
        "context. Emit one SubscriptionContractOutput."
    )
    assert "If no [CONTRACT DECISION] block is present" in sections["objective"]
    assert "If no [CONTRACT DECISION] block is present" in sections["semantic_plan_reasoning"]


def test_4582201a_fixture_keeps_the_recorded_refusal_and_context() -> None:
    context_bytes = (FIXTURES / "subscription_contract_designer_4582201a_context.json").read_bytes()
    outputs_bytes = (FIXTURES / "subscription_contract_designer_4582201a_outputs.json").read_bytes()
    assert hashlib.sha256(context_bytes).hexdigest() == (
        "09b0c278ebf7c46fb279508490c4b0308e3453f897f598ff147c7ba8afe3c2d3"
    )
    assert hashlib.sha256(outputs_bytes).hexdigest() == (
        "0a01c5913d96ec1e5b7e5e3da7d87bccf79193ac54482f52f28da7f6833bd391"
    )

    context = json.loads(context_bytes)
    outputs = json.loads(outputs_bytes)
    intent = context["concept_blueprint"]["monetization_intent"]
    assert context["chat_id"] == "4582201a-cc4c-4845-90e3-5232952cc391"
    assert context["monetization_enabled"] is True
    assert context["brownfield_build_path"] is None
    assert intent["monetized"] is True and intent["subscription_contract_likely"] is True
    assert len(outputs) == 4 and all(output == outputs[0] for output in outputs)
    assert outputs[0]["contract_required"] is False
    assert outputs[0]["subscription_config_file"] is None
