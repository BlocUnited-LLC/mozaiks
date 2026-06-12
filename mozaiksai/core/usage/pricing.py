from __future__ import annotations

"""Display-only token cost estimation.

This module is measurement support. It must not make billing, entitlement, or
quota decisions. Hosted products such as MozaiksPay own authoritative billing.
"""

import os
from dataclasses import dataclass
from typing import Any


def _env_float(name: str) -> float | None:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return None
    try:
        value = float(raw.strip())
    except ValueError:
        return None
    return value if value >= 0 else None


@dataclass(frozen=True)
class UsageCostEstimate:
    estimated_cost_usd: float
    cost_source: str


def estimate_token_cost(
    *,
    model_name: str | None,
    prompt_tokens: int,
    completion_tokens: int,
    explicit_cost_usd: Any = None,
) -> UsageCostEstimate:
    try:
        explicit = float(explicit_cost_usd)
    except (TypeError, ValueError):
        explicit = None
    if explicit is not None and explicit >= 0:
        return UsageCostEstimate(estimated_cost_usd=explicit, cost_source="provided")

    model_key = str(model_name or "default").strip().upper().replace("-", "_").replace(".", "_")
    input_rate = (
        _env_float(f"MOZAIKS_USAGE_{model_key}_INPUT_PER_1K_USD")
        or _env_float("MOZAIKS_USAGE_INPUT_PER_1K_USD")
    )
    output_rate = (
        _env_float(f"MOZAIKS_USAGE_{model_key}_OUTPUT_PER_1K_USD")
        or _env_float("MOZAIKS_USAGE_OUTPUT_PER_1K_USD")
    )
    if input_rate is None and output_rate is None:
        return UsageCostEstimate(estimated_cost_usd=0.0, cost_source="not_configured")

    cost = ((max(0, prompt_tokens) / 1000.0) * float(input_rate or 0.0)) + (
        (max(0, completion_tokens) / 1000.0) * float(output_rate or 0.0)
    )
    return UsageCostEstimate(estimated_cost_usd=cost, cost_source="estimated")


__all__ = ["UsageCostEstimate", "estimate_token_cost"]
