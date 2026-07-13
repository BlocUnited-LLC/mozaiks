from __future__ import annotations

from types import SimpleNamespace

from mozaiksai.core.usage.charges import enrich_usage_with_charge_policy, estimate_usage_charge


def test_estimate_usage_charge_from_provider_cost_markup() -> None:
    policy = SimpleNamespace(
        meter_id="ai_tokens",
        basis="provider_cost_usd",
        markup_percent=35,
        minimum_charge_usd=0,
        rounding="micro_usd",
    )

    estimate = estimate_usage_charge(policy=policy, provider_cost_usd=0.02)

    assert estimate.billable_amount_usd == 0.027
    assert estimate.charge_source == "subscription_usage_charge_policy:ai_tokens"


def test_estimate_usage_charge_from_token_unit_price() -> None:
    policy = SimpleNamespace(
        meter_id="ai_tokens",
        basis="tokens",
        unit_price_usd_per_1k=0.01,
        markup_percent=20,
        minimum_charge_usd=0,
        rounding="micro_usd",
    )

    estimate = estimate_usage_charge(
        policy=policy,
        provider_cost_usd=0.0,
        prompt_tokens=1500,
        completion_tokens=500,
    )

    assert estimate.billable_amount_usd == 0.024


def test_enrich_usage_with_charge_policy_adds_totals_and_rows() -> None:
    policy = SimpleNamespace(
        meter_id="ai_tokens",
        basis="provider_cost_usd",
        markup_percent=50,
        minimum_charge_usd=0,
        rounding="micro_usd",
    )

    usage = {
        "totals": {"estimated_cost_usd": 0.03, "prompt_tokens": 1000, "completion_tokens": 1000},
        "by_run": [{"chat_id": "chat_1", "estimated_cost_usd": 0.02}],
        "by_workflow": [{"workflow_name": "Build", "estimated_cost_usd": 0.02}],
        "events": [{"event_id": "evt_1", "estimated_cost_usd": 0.02}],
    }

    enriched = enrich_usage_with_charge_policy(usage, policy)

    assert enriched["totals"]["billable_amount_usd"] == 0.045
    assert enriched["by_run"][0]["billable_amount_usd"] == 0.03
    assert enriched["by_workflow"][0]["billable_amount_usd"] == 0.03
    assert enriched["events"][0]["charge_source"] == "subscription_usage_charge_policy:ai_tokens"
