from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]


def _read(path: str) -> str:
    return (REPO_ROOT / path).read_text(encoding="utf-8")


def _read_yaml(path: str) -> dict[str, Any]:
    return yaml.safe_load(_read(path)) or {}


CANONICAL_MODELS = {
    "free",
    "subscriptions",
    "usage_based",
    "custom",
    "hybrid",
}


def test_appgenerator_build_plan_declares_core_monetization_scope() -> None:
    structured = _read_yaml("factory_app/workflows/AppGenerator/structured_outputs.yaml")
    models = structured["models"]

    assert "AppMonetizationPlan" in models
    fields = models["AppMonetizationPlan"]["fields"]
    assert set(fields["revenue_model"]["values"]) == CANONICAL_MODELS
    assert fields["subscription_contract_requirement"]["values"] == [
        "not_required",
        "conditional",
        "required",
    ]

    plan_fields = models["AppBuildPlan"]["fields"]
    assert plan_fields["monetization_plan"]["variants"] == ["AppMonetizationPlan", "null"]
    assert "Monetized" in plan_fields["revenue_model"]["description"]
    assert "pay_per_use -> usage_based" in plan_fields["revenue_model"]["description"]
    assert "one_time_purchase -> custom" in plan_fields["revenue_model"]["description"]
    assert "custom money-flow behavior uses domain modules" in plan_fields["revenue_model"]["description"]


def test_valueengine_concept_blueprint_emits_advisory_monetization_hint() -> None:
    structured = _read_yaml("factory_app/workflows/ValueEngine/structured_outputs.yaml")
    models = structured["models"]

    assert "MonetizationIntentHint" in models
    fields = models["MonetizationIntentHint"]["fields"]
    assert "likely_revenue_models" in fields
    assert "subscription_contract_likely" in fields
    assert models["ConceptBlueprint"]["fields"]["monetization_intent"]["variants"] == [
        "MonetizationIntentHint",
        "null",
    ]

    agents = _read("factory_app/workflows/ValueEngine/agents.yaml")
    assert "Do not treat \"monetized\" as a final model" in agents
    assert "Use likely_revenue_models from: subscriptions, usage_based, custom, hybrid" in agents
    assert "Use custom when the concept implies purchases" in agents
    assert "app/operator-specific money policy" in agents


def test_capability_routing_distinguishes_money_flow_from_subscription_contract() -> None:
    routing = _read_yaml("factory_app/build_context/AppGenerator/capability_routing.yaml")
    monetization = routing["layers"]["monetization"]
    entries = {entry["revenue_model"]: entry for entry in monetization["entries"]}

    assert set(entries) == CANONICAL_MODELS
    assert entries["subscriptions"]["subscription_contract"] == "required"
    assert entries["usage_based"]["subscription_contract"] == "conditional"
    assert entries["custom"]["subscription_contract"] == "not_required"
    assert entries["custom"]["aliases"] == ["one_time_purchase"]

    rule = monetization["rule"]
    assert "monetized as \"revenue model unresolved\"" in rule
    assert "true only for subscriptions, plan gates, seats, quotas, credits, token" in rule
    assert "instead of inventing provider internals" in rule
    assert "AppBuildPlan.revenue_model to free, subscriptions, usage_based" in rule


def test_generator_prompts_refuse_blanket_monetized_equals_subscriptions() -> None:
    app_agents = _read("factory_app/workflows/AppGenerator/agents.yaml")
    subscription_agents = _read("factory_app/workflows/SubscriptionContractDesigner/agents.yaml")

    assert "It does not mean subscriptions" in app_agents
    assert "Never set it to `monetized`" in app_agents
    assert "Do not create `config/subscriptions.yaml` unless the app also sells access tiers, quotas, credits, token wallets, or token allowances" in app_agents
    assert "Set `revenue_model` to one concrete core OSS value" in app_agents
    assert "Use `custom` for ecommerce" in app_agents
    assert "policy hook" in app_agents

    assert "\"monetized\" is only a broad intent signal" in subscription_agents
    assert "Do not emit a subscription contract for ordinary ecommerce" in subscription_agents
    assert "sponsored listings" in subscription_agents


def test_workflow_agent_prompts_keep_monetization_inside_runtime_and_facades() -> None:
    agent_generator = _read("factory_app/workflows/AgentGenerator/agents.yaml")
    universal_prompt = _read("factory_app/workflows/AgentGenerator/tools/hook_universal_prompts.py")
    app_structured = _read("factory_app/workflows/AppGenerator/structured_outputs.yaml")
    archetypes = _read("factory_app/build_context/AppGenerator/module_archetypes.yaml")

    assert "Monetization Boundary" in universal_prompt
    assert "declared app module actions, managed-capability facade actions" in universal_prompt
    assert "must not call raw payment providers" in universal_prompt
    assert "INSUFFICIENT_TOKENS" in universal_prompt
    assert "INSUFFICIENT_TOKENS" in agent_generator

    assert "hosted billing lifecycle events or BillingFulfillmentCommand inputs" in archetypes
    assert "managed lifecycle event or fulfillment-command state transitions" in app_structured
    assert "reacts to payment provider webhooks" not in archetypes
    assert "webhook-driven state transitions" not in app_structured
    assert "payment provider, SendGrid" not in universal_prompt


def test_core_monetization_scope_doc_is_present_and_boundary_aware() -> None:
    doc = _read("docs/architecture/mozaiksai/core-monetization-scope.md")

    for model in CANONICAL_MODELS:
        assert f"`{model}`" in doc

    assert "`pay_per_use` -> `usage_based`" in doc
    assert "`one_time_purchase` -> `custom`" in doc
    assert "Do not create `config/subscriptions.yaml` for custom money flows" in doc
    assert "Core Monetization Scope" in doc
    assert "Hosted product policy remains app-owned or operator-owned" in doc
