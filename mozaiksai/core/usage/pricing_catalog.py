from __future__ import annotations

"""Provider pricing catalog helpers.

This module normalizes external model-price references into the small
``mozaiks.usage_pricing.v1`` catalog shape consumed by ``pricing.py``. It does
not own customer markups, payment-provider prices, or billing decisions.
"""

from datetime import UTC, datetime
from typing import Any

USAGE_PRICING_SCHEMA_VERSION = "mozaiks.usage_pricing.v1"


def _text(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def _non_negative_float(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed >= 0 else None


def _per_token_to_per_1m(value: Any) -> float | None:
    parsed = _non_negative_float(value)
    if parsed is None:
        return None
    return parsed * 1_000_000.0


def _first_per_token_as_per_1m(raw: dict[str, Any], *field_names: str) -> float | None:
    for field_name in field_names:
        value = _per_token_to_per_1m(raw.get(field_name))
        if value is not None:
            return value
    return None


def _int_or_none(value: Any) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed >= 0 else None


def _rounded_rate(value: float | None) -> float | None:
    if value is None:
        return None
    return round(float(value), 12)


def normalize_litellm_pricing_catalog(
    raw_catalog: dict[str, Any],
    *,
    source_url: str,
    fetched_at: datetime | None = None,
    source_revision: str | None = None,
    source_content_sha256: str | None = None,
) -> dict[str, Any]:
    """Normalize LiteLLM's model price JSON into Mozaiks usage pricing.

    LiteLLM stores token prices as per-token USD fields. Mozaiks stores prices
    as per-million USD to match provider pricing pages and avoid tiny decimals
    in operator-edited files.
    """

    fetched = fetched_at or datetime.now(UTC)
    if fetched.tzinfo is None:
        fetched = fetched.replace(tzinfo=UTC)

    models: dict[str, dict[str, Any]] = {}
    for raw_model_name, raw_model in sorted(raw_catalog.items(), key=lambda item: str(item[0])):
        if raw_model_name == "sample_spec" or not isinstance(raw_model, dict):
            continue

        input_per_1m = _first_per_token_as_per_1m(
            raw_model,
            "input_cost_per_token",
            "prompt_cost_per_token",
        )
        cached_input_per_1m = _first_per_token_as_per_1m(
            raw_model,
            "cache_read_input_token_cost",
            "cached_input_cost_per_token",
            "cached_prompt_cost_per_token",
        )
        output_per_1m = _first_per_token_as_per_1m(
            raw_model,
            "output_cost_per_token",
            "completion_cost_per_token",
        )

        if input_per_1m is None and output_per_1m is None and cached_input_per_1m is None:
            continue

        entry: dict[str, Any] = {
            "provider": _text(raw_model.get("litellm_provider")),
            "mode": _text(raw_model.get("mode")),
            "source": "litellm",
        }
        if input_per_1m is not None:
            entry["input_per_1m_usd"] = _rounded_rate(input_per_1m)
        if cached_input_per_1m is not None:
            entry["cached_input_per_1m_usd"] = _rounded_rate(cached_input_per_1m)
        if output_per_1m is not None:
            entry["output_per_1m_usd"] = _rounded_rate(output_per_1m)

        for source_key, target_key in (
            ("max_input_tokens", "max_input_tokens"),
            ("max_output_tokens", "max_output_tokens"),
            ("max_tokens", "max_tokens"),
        ):
            value = _int_or_none(raw_model.get(source_key))
            if value is not None:
                entry[target_key] = value

        models[str(raw_model_name)] = {
            key: value
            for key, value in entry.items()
            if value is not None
        }

    source: dict[str, Any] = {
        "name": "litellm",
        "url": source_url,
        "source_revision": source_revision,
        "fetched_at": fetched.astimezone(UTC).isoformat(),
    }
    if source_content_sha256:
        source["content_sha256"] = source_content_sha256

    return {
        "schema_version": USAGE_PRICING_SCHEMA_VERSION,
        "source": source,
        "models": models,
    }


__all__ = [
    "USAGE_PRICING_SCHEMA_VERSION",
    "normalize_litellm_pricing_catalog",
]
