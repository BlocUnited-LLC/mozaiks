from __future__ import annotations

"""Customer-facing usage charge estimate helpers.

These helpers apply provider-neutral app policy to measured usage. They are
still estimates: payment processors, invoices, credits, and settlements remain
owned by app or hosted-product billing code.
"""

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class UsageChargeEstimate:
    billable_amount_usd: float
    charge_source: str


def _non_negative_float(value: Any) -> float:
    try:
        return max(0.0, float(value or 0.0))
    except (TypeError, ValueError):
        return 0.0


def _non_negative_int(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _round_charge(value: float, rounding: str) -> float:
    if rounding == "cent":
        return round(value, 2)
    if rounding == "micro_usd":
        return round(value, 6)
    return float(value)


def estimate_usage_charge(
    *,
    policy: Any,
    provider_cost_usd: Any,
    prompt_tokens: Any = 0,
    completion_tokens: Any = 0,
    total_tokens: Any = 0,
) -> UsageChargeEstimate:
    basis = str(getattr(policy, "basis", "provider_cost_usd") or "provider_cost_usd")
    markup_percent = _non_negative_float(getattr(policy, "markup_percent", 0.0))
    markup_multiplier = 1.0 + (markup_percent / 100.0)
    minimum_charge_usd = _non_negative_float(getattr(policy, "minimum_charge_usd", 0.0))
    rounding = str(getattr(policy, "rounding", "cent") or "cent")

    if basis == "tokens":
        resolved_total_tokens = _non_negative_int(total_tokens)
        if resolved_total_tokens <= 0:
            resolved_total_tokens = _non_negative_int(prompt_tokens) + _non_negative_int(completion_tokens)
        unit_price = _non_negative_float(getattr(policy, "unit_price_usd_per_1k", 0.0))
        charge = (resolved_total_tokens / 1000.0) * unit_price * markup_multiplier
    else:
        charge = _non_negative_float(provider_cost_usd) * markup_multiplier

    if charge > 0:
        charge = max(charge, minimum_charge_usd)
    charge = _round_charge(charge, rounding)
    meter_id = str(getattr(policy, "meter_id", "ai_tokens") or "ai_tokens")
    return UsageChargeEstimate(
        billable_amount_usd=charge,
        charge_source=f"subscription_usage_charge_policy:{meter_id}",
    )


def _charge_row(row: dict[str, Any], policy: Any) -> dict[str, Any]:
    estimate = estimate_usage_charge(
        policy=policy,
        provider_cost_usd=row.get("estimated_cost_usd"),
        prompt_tokens=row.get("prompt_tokens"),
        completion_tokens=row.get("completion_tokens"),
        total_tokens=row.get("total_tokens"),
    )
    return {
        **row,
        "billable_amount_usd": estimate.billable_amount_usd,
        "charge_source": estimate.charge_source,
    }


def enrich_usage_with_charge_policy(usage: dict[str, Any], policy: Any | None) -> dict[str, Any]:
    if policy is None:
        return usage

    enriched = dict(usage)
    totals = dict(enriched.get("totals") or {})
    total_estimate = estimate_usage_charge(
        policy=policy,
        provider_cost_usd=totals.get("estimated_cost_usd"),
        prompt_tokens=totals.get("prompt_tokens"),
        completion_tokens=totals.get("completion_tokens"),
        total_tokens=totals.get("total_tokens"),
    )
    totals["billable_amount_usd"] = total_estimate.billable_amount_usd
    enriched["totals"] = totals
    enriched["charge_source"] = total_estimate.charge_source
    enriched["charge_basis"] = str(getattr(policy, "basis", "provider_cost_usd") or "provider_cost_usd")
    enriched["by_workflow"] = [
        _charge_row(dict(row), policy)
        for row in enriched.get("by_workflow", []) or []
        if isinstance(row, dict)
    ]
    enriched["by_run"] = [
        _charge_row(dict(row), policy)
        for row in enriched.get("by_run", []) or []
        if isinstance(row, dict)
    ]
    enriched["events"] = [
        _charge_row(dict(row), policy)
        for row in enriched.get("events", []) or []
        if isinstance(row, dict)
    ]
    return enriched


__all__ = [
    "UsageChargeEstimate",
    "enrich_usage_with_charge_policy",
    "estimate_usage_charge",
]
