from __future__ import annotations

"""Display-only token cost estimation.

This module is measurement support. It must not make billing, entitlement, or
quota decisions. Hosted products such as MozaiksPay own authoritative billing.
"""

import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[3]
_DEFAULT_GENERATED_CATALOG_PATH = _REPO_ROOT / "pricing" / "catalogs" / "usage-pricing.generated.json"
_PACKAGE_GENERATED_CATALOG_PATH = (
    Path(__file__).resolve().parent / "catalogs" / "usage-pricing.generated.json"
)

# Public list prices (USD per 1K tokens) as of mid-2025. This is a
# non-authoritative display fallback for local/dev visibility only. Production
# operators should use MOZAIKS_USAGE_PRICING_CATALOG_PATH or env overrides.
_DEFAULT_PRICES: dict[str, tuple[float, float]] = {
    # OpenAI
    "GPT_4O": (0.0025, 0.01),
    "GPT_4O_MINI": (0.00015, 0.0006),
    "GPT_4_TURBO": (0.01, 0.03),
    "GPT_4": (0.03, 0.06),
    "GPT_3_5_TURBO": (0.0005, 0.0015),
    "O1": (0.015, 0.06),
    "O1_MINI": (0.003, 0.012),
    "O3_MINI": (0.0011, 0.0044),
    # Anthropic
    "CLAUDE_3_5_SONNET_20241022": (0.003, 0.015),
    "CLAUDE_3_5_SONNET_20240620": (0.003, 0.015),
    "CLAUDE_3_5_HAIKU_20241022": (0.0008, 0.004),
    "CLAUDE_3_OPUS_20240229": (0.015, 0.075),
    "CLAUDE_3_SONNET_20240229": (0.003, 0.015),
    "CLAUDE_3_HAIKU_20240307": (0.00025, 0.00125),
    "CLAUDE_SONNET_4_5": (0.003, 0.015),
    "CLAUDE_SONNET_4_6": (0.003, 0.015),
    "CLAUDE_OPUS_4_6": (0.015, 0.075),
    "CLAUDE_HAIKU_4_5": (0.0008, 0.004),
}

# Prefix aliases: match any model whose normalized key starts with one of these.
_DEFAULT_PRICE_PREFIXES: list[tuple[str, tuple[float, float]]] = [
    ("GPT_4O_MINI", (0.00015, 0.0006)),
    ("GPT_4O", (0.0025, 0.01)),
    ("O1_MINI", (0.003, 0.012)),
    ("O3_MINI", (0.0011, 0.0044)),
    ("O1", (0.015, 0.06)),
    ("CLAUDE_3_5_HAIKU", (0.0008, 0.004)),
    ("CLAUDE_3_5_SONNET", (0.003, 0.015)),
    ("CLAUDE_3_HAIKU", (0.00025, 0.00125)),
    ("CLAUDE_3_SONNET", (0.003, 0.015)),
    ("CLAUDE_3_OPUS", (0.015, 0.075)),
    ("CLAUDE_HAIKU", (0.0008, 0.004)),
    ("CLAUDE_SONNET", (0.003, 0.015)),
    ("CLAUDE_OPUS", (0.015, 0.075)),
]


def _env_float(name: str) -> float | None:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return None
    try:
        value = float(raw.strip())
    except ValueError:
        return None
    return value if value >= 0 else None


def _first_float(*names: str) -> float | None:
    for name in names:
        value = _env_float(name)
        if value is not None:
            return value
    return None


def _default_rates(model_key: str) -> tuple[float, float] | None:
    if model_key in _DEFAULT_PRICES:
        return _DEFAULT_PRICES[model_key]
    for prefix, rates in _DEFAULT_PRICE_PREFIXES:
        if model_key.startswith(prefix):
            return rates
    return None


@dataclass(frozen=True)
class UsageCostEstimate:
    estimated_cost_usd: float
    cost_source: str


@dataclass(frozen=True)
class _CatalogCandidate:
    kind: str
    path: str
    env_name: str | None = None


def _model_key(model_name: str | None) -> str:
    return str(model_name or "default").strip().upper().replace("-", "_").replace(".", "_")


def _catalog_model_keys(model_name: str | None) -> list[str]:
    raw = str(model_name or "default").strip()
    keys: list[str] = []

    def add(value: str | None) -> None:
        key = _model_key(value)
        if key and key not in keys:
            keys.append(key)

    add(raw)
    if "/" in raw:
        add(raw.rsplit("/", 1)[-1])
    return keys


def _non_negative_float(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed >= 0 else None


def _rate_pair_from_mapping(raw: Any) -> tuple[float, float] | None:
    if not isinstance(raw, dict):
        return None

    input_rate = _non_negative_float(
        raw.get("input_per_1k_usd")
        or raw.get("prompt_per_1k_usd")
        or raw.get("input")
        or raw.get("prompt")
    )
    output_rate = _non_negative_float(
        raw.get("output_per_1k_usd")
        or raw.get("completion_per_1k_usd")
        or raw.get("output")
        or raw.get("completion")
    )
    input_per_1m = _non_negative_float(raw.get("input_per_1m_usd") or raw.get("prompt_per_1m_usd"))
    output_per_1m = _non_negative_float(
        raw.get("output_per_1m_usd") or raw.get("completion_per_1m_usd")
    )
    input_per_token = _non_negative_float(
        raw.get("input_cost_per_token") or raw.get("prompt_cost_per_token")
    )
    output_per_token = _non_negative_float(
        raw.get("output_cost_per_token") or raw.get("completion_cost_per_token")
    )
    if input_rate is None and input_per_1m is not None:
        input_rate = input_per_1m / 1000.0
    if output_rate is None and output_per_1m is not None:
        output_rate = output_per_1m / 1000.0
    if input_rate is None and input_per_token is not None:
        input_rate = input_per_token * 1000.0
    if output_rate is None and output_per_token is not None:
        output_rate = output_per_token * 1000.0

    if input_rate is None and output_rate is None:
        return None
    return (float(input_rate or 0.0), float(output_rate or 0.0))


def _cached_rate_from_mapping(raw: Any) -> float | None:
    if not isinstance(raw, dict):
        return None

    cached_rate = _non_negative_float(
        raw.get("cached_input_per_1k_usd")
        or raw.get("cached_prompt_per_1k_usd")
        or raw.get("cached_input")
        or raw.get("cached_prompt")
    )
    cached_per_1m = _non_negative_float(
        raw.get("cached_input_per_1m_usd") or raw.get("cached_prompt_per_1m_usd")
    )
    cached_per_token = _non_negative_float(
        raw.get("cache_read_input_token_cost")
        or raw.get("cached_input_cost_per_token")
        or raw.get("cached_prompt_cost_per_token")
    )
    if cached_rate is None and cached_per_1m is not None:
        cached_rate = cached_per_1m / 1000.0
    if cached_rate is None and cached_per_token is not None:
        cached_rate = cached_per_token * 1000.0
    return cached_rate


@lru_cache(maxsize=8)
def _load_pricing_catalog_payload(path_text: str) -> dict[str, Any]:
    path = Path(path_text).expanduser()
    if not path.exists() or not path.is_file():
        return {"payload": None, "error": "missing", "exists": False}

    try:
        text = path.read_text(encoding="utf-8")
        if path.suffix.lower() in {".yaml", ".yml"}:
            import yaml

            payload = yaml.safe_load(text)
        else:
            payload = json.loads(text)
    except Exception as exc:
        return {"payload": None, "error": f"{type(exc).__name__}: {exc}", "exists": True}

    if not isinstance(payload, dict):
        return {"payload": None, "error": "catalog must be an object", "exists": True}

    return {"payload": payload, "error": None, "exists": True}


@lru_cache(maxsize=8)
def _load_pricing_catalog(path_text: str) -> dict[str, dict[str, float]]:
    payload = _load_pricing_catalog_payload(path_text).get("payload")
    if not isinstance(payload, dict):
        return {}

    raw_models = payload.get("models", payload)
    if not isinstance(raw_models, dict):
        return {}

    catalog: dict[str, dict[str, float]] = {}
    for raw_name, raw_rates in raw_models.items():
        key = _model_key(str(raw_name))
        rates = _rate_pair_from_mapping(raw_rates)
        if rates is not None:
            input_rate, output_rate = rates
            catalog[key] = {
                "input": input_rate,
                "output": output_rate,
            }
            cached_rate = _cached_rate_from_mapping(raw_rates)
            if cached_rate is not None:
                catalog[key]["cached_input"] = cached_rate
    return catalog


def _catalog_candidates() -> list[_CatalogCandidate]:
    candidates: list[_CatalogCandidate] = []
    override_path = os.getenv("MOZAIKS_USAGE_PRICING_OVERRIDE_PATH", "").strip()
    if override_path:
        candidates.append(
            _CatalogCandidate(kind="override", path=override_path, env_name="MOZAIKS_USAGE_PRICING_OVERRIDE_PATH")
        )

    catalog_path = os.getenv("MOZAIKS_USAGE_PRICING_CATALOG_PATH", "").strip()
    if catalog_path:
        candidates.append(
            _CatalogCandidate(kind="catalog", path=catalog_path, env_name="MOZAIKS_USAGE_PRICING_CATALOG_PATH")
        )
    elif os.getenv("MOZAIKS_USAGE_PRICING_DISABLE_DEFAULT_CATALOG", "").strip().lower() not in {
        "1",
        "true",
        "yes",
        "on",
    }:
        for path in (_DEFAULT_GENERATED_CATALOG_PATH, _PACKAGE_GENERATED_CATALOG_PATH):
            if path.exists():
                candidates.append(_CatalogCandidate(kind="catalog", path=str(path)))
                break
    return candidates


def _catalog_rates(model_key: str) -> tuple[dict[str, float], str] | None:
    candidate_keys = _catalog_model_keys(model_key)
    for candidate in _catalog_candidates():
        catalog = _load_pricing_catalog(candidate.path)
        rates = None
        for candidate_key in candidate_keys:
            rates = catalog.get(candidate_key)
            if rates is not None:
                break
        rates = rates or catalog.get("DEFAULT")
        if rates is not None:
            return rates, candidate.kind
    return None


def _parse_iso(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def pricing_catalog_health(
    *,
    used_models: list[str] | None = None,
    unpriced_models: list[str] | None = None,
    default_table_models: list[str] | None = None,
    cost_source_counts: dict[str, int] | None = None,
) -> dict[str, Any]:
    """Return display-safe pricing catalog and coverage health."""

    catalog_entries: list[dict[str, Any]] = []
    active_model_count = 0
    updated_candidates: list[datetime] = []
    for candidate in _catalog_candidates():
        load_result = _load_pricing_catalog_payload(candidate.path)
        payload = load_result.get("payload")
        catalog = _load_pricing_catalog(candidate.path)
        source = payload.get("source") if isinstance(payload, dict) else None
        if isinstance(source, dict):
            fetched_at = source.get("fetched_at")
            parsed_fetched_at = _parse_iso(fetched_at)
            if parsed_fetched_at is not None:
                updated_candidates.append(parsed_fetched_at)
        else:
            fetched_at = None
        model_count = len(catalog)
        active_model_count += model_count
        catalog_entries.append(
            {
                "kind": candidate.kind,
                "env_name": candidate.env_name,
                "path": candidate.path,
                "exists": bool(load_result.get("exists")),
                "status": "ready" if model_count > 0 else ("missing" if not load_result.get("exists") else "invalid"),
                "error": load_result.get("error"),
                "model_count": model_count,
                "source_name": source.get("name") if isinstance(source, dict) else None,
                "source_url": source.get("url") if isinstance(source, dict) else None,
                "source_revision": source.get("source_revision") if isinstance(source, dict) else None,
                "fetched_at": fetched_at,
            }
        )

    used = sorted({str(model or "unknown") for model in used_models or [] if str(model or "").strip()})
    unpriced = sorted({str(model or "unknown") for model in unpriced_models or [] if str(model or "").strip()})
    fallback = sorted({str(model or "unknown") for model in default_table_models or [] if str(model or "").strip()})
    counts = dict(cost_source_counts or {})
    priced_event_count = sum(
        int(counts.get(source) or 0)
        for source in ("provided", "estimated", "override", "catalog")
    )
    unpriced_event_count = int(counts.get("not_configured") or 0)
    total_event_count = sum(int(value or 0) for value in counts.values())
    coverage_percent = (
        round((priced_event_count / total_event_count) * 100.0, 2)
        if total_event_count > 0
        else None
    )

    if unpriced_event_count > 0:
        status = "unpriced_models"
    elif counts.get("default_table"):
        status = "fallback_prices"
    elif active_model_count > 0:
        status = "ready"
    elif catalog_entries:
        status = "catalog_unavailable"
    else:
        status = "not_configured"

    latest_update = max(updated_candidates).isoformat() if updated_candidates else None
    return {
        "status": status,
        "catalogs": catalog_entries,
        "catalog_model_count": active_model_count,
        "catalog_updated_at": latest_update,
        "used_model_count": len(used),
        "used_models": used,
        "priced_event_count": priced_event_count,
        "unpriced_event_count": unpriced_event_count,
        "unpriced_model_count": len(unpriced),
        "unpriced_models": unpriced,
        "default_table_event_count": int(counts.get("default_table") or 0),
        "default_table_models": fallback,
        "cost_source_counts": counts,
        "coverage_percent": coverage_percent,
    }


def _token_cost(
    *,
    prompt_tokens: int,
    completion_tokens: int,
    input_per_1k: float,
    output_per_1k: float,
    cached_prompt_tokens: int = 0,
    cached_input_per_1k: float | None = None,
) -> float:
    cached = min(max(0, int(cached_prompt_tokens or 0)), max(0, int(prompt_tokens or 0)))
    uncached_prompt = max(0, int(prompt_tokens or 0)) - cached
    cached_rate = input_per_1k if cached_input_per_1k is None else cached_input_per_1k
    return ((uncached_prompt / 1000.0) * input_per_1k) + (
        (cached / 1000.0) * cached_rate
    ) + ((max(0, completion_tokens) / 1000.0) * output_per_1k)


def estimate_token_cost(
    *,
    model_name: str | None,
    prompt_tokens: int,
    completion_tokens: int,
    cached_prompt_tokens: int = 0,
    explicit_cost_usd: Any = None,
) -> UsageCostEstimate:
    try:
        explicit = float(explicit_cost_usd)
    except (TypeError, ValueError):
        explicit = None
    if explicit is not None and explicit >= 0:
        return UsageCostEstimate(estimated_cost_usd=explicit, cost_source="provided")

    model_key = _model_key(model_name)
    input_rate = _first_float(
        f"MOZAIKS_USAGE_{model_key}_INPUT_PER_1K_USD",
        "MOZAIKS_USAGE_INPUT_PER_1K_USD",
    )
    output_rate = _first_float(
        f"MOZAIKS_USAGE_{model_key}_OUTPUT_PER_1K_USD",
        "MOZAIKS_USAGE_OUTPUT_PER_1K_USD",
    )
    cached_input_rate = _first_float(
        f"MOZAIKS_USAGE_{model_key}_CACHED_INPUT_PER_1K_USD",
        f"MOZAIKS_USAGE_{model_key}_CACHED_PROMPT_PER_1K_USD",
        "MOZAIKS_USAGE_CACHED_INPUT_PER_1K_USD",
        "MOZAIKS_USAGE_CACHED_PROMPT_PER_1K_USD",
    )
    if input_rate is not None or output_rate is not None:
        cost = _token_cost(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            input_per_1k=float(input_rate or 0.0),
            output_per_1k=float(output_rate or 0.0),
            cached_prompt_tokens=cached_prompt_tokens,
            cached_input_per_1k=cached_input_rate,
        )
        return UsageCostEstimate(estimated_cost_usd=cost, cost_source="estimated")

    catalog_match = _catalog_rates(model_key)
    if catalog_match is not None:
        catalog_rates, catalog_source = catalog_match
        cost = _token_cost(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            input_per_1k=float(catalog_rates.get("input") or 0.0),
            output_per_1k=float(catalog_rates.get("output") or 0.0),
            cached_prompt_tokens=cached_prompt_tokens,
            cached_input_per_1k=catalog_rates.get("cached_input"),
        )
        return UsageCostEstimate(estimated_cost_usd=cost, cost_source=catalog_source)

    defaults = _default_rates(model_key)
    if defaults is not None:
        default_input, default_output = defaults
        cost = _token_cost(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            input_per_1k=default_input,
            output_per_1k=default_output,
            cached_prompt_tokens=cached_prompt_tokens,
        )
        return UsageCostEstimate(estimated_cost_usd=cost, cost_source="default_table")

    return UsageCostEstimate(estimated_cost_usd=0.0, cost_source="not_configured")


__all__ = ["UsageCostEstimate", "estimate_token_cost", "pricing_catalog_health"]
