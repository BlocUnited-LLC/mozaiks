"""
Token cost estimation unit tests.

Covers:
  _env_float:
    - missing env → None
    - empty string → None
    - whitespace-only → None
    - invalid string → None
    - negative float → None
    - zero → 0.0 (valid, non-negative)
    - positive float → that value
    - whitespace-padded → stripped and returned

  estimate_token_cost:
    - explicit_cost_usd provided → cost_source="provided"
    - explicit_cost_usd=0.0 → valid, returned as "provided"
    - explicit_cost_usd negative → falls through to rate lookup
    - explicit_cost_usd invalid string → falls through
    - no rates configured → cost_source="not_configured", cost=0.0
    - only input rate → uses input rate only
    - only output rate → uses output rate only
    - both rates → computes combined cost
    - model-specific rate overrides global
    - falls back to global rate when model-specific absent
    - model_name transform: dashes and dots → underscores, uppercased
    - negative token counts clamped to 0
    - zero tokens → 0.0 cost
    - cost_source="estimated" when rates present
"""
from __future__ import annotations

import pytest

from mozaiksai.core.usage import pricing
from mozaiksai.core.usage.pricing import (
    UsageCostEstimate,
    _env_float,
    estimate_token_cost,
)

# ---------------------------------------------------------------------------
# 1. _env_float
# ---------------------------------------------------------------------------

class TestEnvFloat:
    def test_missing_env_returns_none(self, monkeypatch):
        monkeypatch.delenv("TEST_RATE_VAL", raising=False)
        assert _env_float("TEST_RATE_VAL") is None

    def test_empty_string_returns_none(self, monkeypatch):
        monkeypatch.setenv("TEST_RATE_VAL", "")
        assert _env_float("TEST_RATE_VAL") is None

    def test_whitespace_only_returns_none(self, monkeypatch):
        monkeypatch.setenv("TEST_RATE_VAL", "   ")
        assert _env_float("TEST_RATE_VAL") is None

    def test_invalid_string_returns_none(self, monkeypatch):
        monkeypatch.setenv("TEST_RATE_VAL", "notanumber")
        assert _env_float("TEST_RATE_VAL") is None

    def test_negative_float_returns_none(self, monkeypatch):
        monkeypatch.setenv("TEST_RATE_VAL", "-1.5")
        assert _env_float("TEST_RATE_VAL") is None

    def test_zero_returns_zero(self, monkeypatch):
        monkeypatch.setenv("TEST_RATE_VAL", "0")
        assert _env_float("TEST_RATE_VAL") == 0.0

    def test_positive_float_returned(self, monkeypatch):
        monkeypatch.setenv("TEST_RATE_VAL", "0.03")
        assert _env_float("TEST_RATE_VAL") == pytest.approx(0.03)

    def test_whitespace_padded_stripped(self, monkeypatch):
        monkeypatch.setenv("TEST_RATE_VAL", "  1.5  ")
        assert _env_float("TEST_RATE_VAL") == pytest.approx(1.5)

    def test_integer_string_accepted(self, monkeypatch):
        monkeypatch.setenv("TEST_RATE_VAL", "2")
        assert _env_float("TEST_RATE_VAL") == 2.0


# ---------------------------------------------------------------------------
# 2. estimate_token_cost
# ---------------------------------------------------------------------------

def _clear_rates(monkeypatch):
    """Remove all pricing env vars to ensure a clean slate."""
    for name in [
        "MOZAIKS_USAGE_INPUT_PER_1K_USD",
        "MOZAIKS_USAGE_OUTPUT_PER_1K_USD",
        "MOZAIKS_USAGE_GPT_4_INPUT_PER_1K_USD",
        "MOZAIKS_USAGE_GPT_4_OUTPUT_PER_1K_USD",
        "MOZAIKS_USAGE_DEFAULT_INPUT_PER_1K_USD",
        "MOZAIKS_USAGE_DEFAULT_OUTPUT_PER_1K_USD",
        "MOZAIKS_USAGE_CLAUDE_3_5_SONNET_INPUT_PER_1K_USD",
        "MOZAIKS_USAGE_CLAUDE_3_5_SONNET_OUTPUT_PER_1K_USD",
        "MOZAIKS_USAGE_PRICING_CATALOG_PATH",
        "MOZAIKS_USAGE_PRICING_OVERRIDE_PATH",
        "MOZAIKS_USAGE_PRICING_DISABLE_DEFAULT_CATALOG",
    ]:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("MOZAIKS_USAGE_PRICING_DISABLE_DEFAULT_CATALOG", "1")


class TestEstimateTokenCost:
    def test_explicit_cost_returned(self, monkeypatch):
        _clear_rates(monkeypatch)
        result = estimate_token_cost(
            model_name=None,
            prompt_tokens=1000,
            completion_tokens=500,
            explicit_cost_usd=0.05,
        )
        assert result.cost_source == "provided"
        assert result.estimated_cost_usd == pytest.approx(0.05)

    def test_explicit_zero_is_valid(self, monkeypatch):
        _clear_rates(monkeypatch)
        result = estimate_token_cost(
            model_name=None,
            prompt_tokens=0,
            completion_tokens=0,
            explicit_cost_usd=0.0,
        )
        assert result.cost_source == "provided"
        assert result.estimated_cost_usd == 0.0

    def test_explicit_negative_falls_through(self, monkeypatch):
        _clear_rates(monkeypatch)
        result = estimate_token_cost(
            model_name=None,
            prompt_tokens=0,
            completion_tokens=0,
            explicit_cost_usd=-1.0,
        )
        assert result.cost_source == "not_configured"

    def test_explicit_invalid_string_falls_through(self, monkeypatch):
        _clear_rates(monkeypatch)
        result = estimate_token_cost(
            model_name=None,
            prompt_tokens=0,
            completion_tokens=0,
            explicit_cost_usd="not_a_number",
        )
        assert result.cost_source == "not_configured"

    def test_no_rates_known_model_uses_default_table(self, monkeypatch):
        _clear_rates(monkeypatch)
        result = estimate_token_cost(
            model_name="gpt-4",
            prompt_tokens=1000,
            completion_tokens=500,
        )
        assert result.cost_source == "default_table"
        assert result.estimated_cost_usd > 0.0

    def test_no_rates_unknown_model_not_configured(self, monkeypatch):
        _clear_rates(monkeypatch)
        result = estimate_token_cost(
            model_name="totally-unknown-model-xyz",
            prompt_tokens=1000,
            completion_tokens=500,
        )
        assert result.cost_source == "not_configured"
        assert result.estimated_cost_usd == 0.0

    def test_only_input_rate(self, monkeypatch):
        _clear_rates(monkeypatch)
        monkeypatch.setenv("MOZAIKS_USAGE_INPUT_PER_1K_USD", "0.01")
        result = estimate_token_cost(
            model_name=None,
            prompt_tokens=2000,
            completion_tokens=0,
        )
        assert result.cost_source == "estimated"
        # 2000 / 1000 * 0.01 = 0.02
        assert result.estimated_cost_usd == pytest.approx(0.02)

    def test_only_output_rate(self, monkeypatch):
        _clear_rates(monkeypatch)
        monkeypatch.setenv("MOZAIKS_USAGE_OUTPUT_PER_1K_USD", "0.03")
        result = estimate_token_cost(
            model_name=None,
            prompt_tokens=0,
            completion_tokens=1000,
        )
        assert result.cost_source == "estimated"
        # 1000 / 1000 * 0.03 = 0.03
        assert result.estimated_cost_usd == pytest.approx(0.03)

    def test_both_rates_combined(self, monkeypatch):
        _clear_rates(monkeypatch)
        monkeypatch.setenv("MOZAIKS_USAGE_INPUT_PER_1K_USD", "0.01")
        monkeypatch.setenv("MOZAIKS_USAGE_OUTPUT_PER_1K_USD", "0.03")
        result = estimate_token_cost(
            model_name=None,
            prompt_tokens=1000,
            completion_tokens=1000,
        )
        assert result.cost_source == "estimated"
        # 1000/1000*0.01 + 1000/1000*0.03 = 0.04
        assert result.estimated_cost_usd == pytest.approx(0.04)

    def test_model_specific_rate_used(self, monkeypatch):
        _clear_rates(monkeypatch)
        monkeypatch.setenv("MOZAIKS_USAGE_GPT_4_INPUT_PER_1K_USD", "0.05")
        monkeypatch.setenv("MOZAIKS_USAGE_INPUT_PER_1K_USD", "0.01")
        result = estimate_token_cost(
            model_name="gpt-4",
            prompt_tokens=1000,
            completion_tokens=0,
        )
        assert result.cost_source == "estimated"
        # model-specific: 1000/1000*0.05 = 0.05
        assert result.estimated_cost_usd == pytest.approx(0.05)

    def test_falls_back_to_global_rate(self, monkeypatch):
        _clear_rates(monkeypatch)
        monkeypatch.setenv("MOZAIKS_USAGE_INPUT_PER_1K_USD", "0.01")
        result = estimate_token_cost(
            model_name="unknown-model",
            prompt_tokens=1000,
            completion_tokens=0,
        )
        assert result.cost_source == "estimated"
        assert result.estimated_cost_usd == pytest.approx(0.01)

    def test_model_name_transform_dashes_to_underscores(self, monkeypatch):
        """'claude-3.5-sonnet' → env key 'CLAUDE_3_5_SONNET'"""
        _clear_rates(monkeypatch)
        monkeypatch.setenv("MOZAIKS_USAGE_CLAUDE_3_5_SONNET_INPUT_PER_1K_USD", "0.02")
        result = estimate_token_cost(
            model_name="claude-3.5-sonnet",
            prompt_tokens=1000,
            completion_tokens=0,
        )
        assert result.cost_source == "estimated"
        assert result.estimated_cost_usd == pytest.approx(0.02)

    def test_negative_token_counts_clamped_to_zero(self, monkeypatch):
        _clear_rates(monkeypatch)
        monkeypatch.setenv("MOZAIKS_USAGE_INPUT_PER_1K_USD", "0.01")
        monkeypatch.setenv("MOZAIKS_USAGE_OUTPUT_PER_1K_USD", "0.03")
        result = estimate_token_cost(
            model_name=None,
            prompt_tokens=-100,
            completion_tokens=-50,
        )
        assert result.estimated_cost_usd == 0.0

    def test_zero_tokens_zero_cost(self, monkeypatch):
        _clear_rates(monkeypatch)
        monkeypatch.setenv("MOZAIKS_USAGE_INPUT_PER_1K_USD", "0.01")
        monkeypatch.setenv("MOZAIKS_USAGE_OUTPUT_PER_1K_USD", "0.03")
        result = estimate_token_cost(
            model_name=None,
            prompt_tokens=0,
            completion_tokens=0,
        )
        assert result.estimated_cost_usd == 0.0
        assert result.cost_source == "estimated"

    def test_model_name_none_uses_default_key(self, monkeypatch):
        """model_name=None → key 'DEFAULT', looks for MOZAIKS_USAGE_DEFAULT_INPUT_PER_1K_USD."""
        _clear_rates(monkeypatch)
        monkeypatch.setenv("MOZAIKS_USAGE_DEFAULT_INPUT_PER_1K_USD", "0.005")
        result = estimate_token_cost(
            model_name=None,
            prompt_tokens=2000,
            completion_tokens=0,
        )
        assert result.cost_source == "estimated"
        assert result.estimated_cost_usd == pytest.approx(0.01)

    def test_catalog_rates_used_when_env_absent(self, monkeypatch, tmp_path):
        _clear_rates(monkeypatch)
        catalog = tmp_path / "pricing.yaml"
        catalog.write_text(
            """
schema_version: mozaiks.usage_pricing.v1
models:
  gpt-5-nano:
    input_per_1k_usd: 0.001
    output_per_1k_usd: 0.004
""",
            encoding="utf-8",
        )
        monkeypatch.setenv("MOZAIKS_USAGE_PRICING_CATALOG_PATH", str(catalog))

        result = estimate_token_cost(
            model_name="gpt-5-nano",
            prompt_tokens=2000,
            completion_tokens=500,
        )

        assert result.cost_source == "catalog"
        assert result.estimated_cost_usd == pytest.approx(0.004)

    def test_catalog_matches_provider_prefixed_model_name(self, monkeypatch, tmp_path):
        _clear_rates(monkeypatch)
        catalog = tmp_path / "pricing.yaml"
        catalog.write_text(
            """
schema_version: mozaiks.usage_pricing.v1
models:
  gpt-5-nano:
    input_per_1k_usd: 0.001
    output_per_1k_usd: 0.004
""",
            encoding="utf-8",
        )
        monkeypatch.setenv("MOZAIKS_USAGE_PRICING_CATALOG_PATH", str(catalog))

        result = estimate_token_cost(
            model_name="openai/gpt-5-nano",
            prompt_tokens=2000,
            completion_tokens=500,
        )

        assert result.cost_source == "catalog"
        assert result.estimated_cost_usd == pytest.approx(0.004)

    def test_catalog_supports_per_million_rates(self, monkeypatch, tmp_path):
        _clear_rates(monkeypatch)
        catalog = tmp_path / "pricing.json"
        catalog.write_text(
            """
{
  "models": {
    "gpt-5-mini": {
      "input_per_1m_usd": 0.25,
      "output_per_1m_usd": 2.0
    }
  }
}
""",
            encoding="utf-8",
        )
        monkeypatch.setenv("MOZAIKS_USAGE_PRICING_CATALOG_PATH", str(catalog))

        result = estimate_token_cost(
            model_name="gpt-5-mini",
            prompt_tokens=1000,
            completion_tokens=1000,
        )

        assert result.cost_source == "catalog"
        assert result.estimated_cost_usd == pytest.approx(0.00225)

    def test_catalog_supports_cached_input_rates(self, monkeypatch, tmp_path):
        _clear_rates(monkeypatch)
        catalog = tmp_path / "pricing.yaml"
        catalog.write_text(
            """
models:
  gpt-5-nano:
    input_per_1m_usd: 0.05
    cached_input_per_1m_usd: 0.005
    output_per_1m_usd: 0.40
""",
            encoding="utf-8",
        )
        monkeypatch.setenv("MOZAIKS_USAGE_PRICING_CATALOG_PATH", str(catalog))

        result = estimate_token_cost(
            model_name="gpt-5-nano",
            prompt_tokens=2000,
            cached_prompt_tokens=1500,
            completion_tokens=1000,
        )

        assert result.cost_source == "catalog"
        assert result.estimated_cost_usd == pytest.approx(0.0004325)

    def test_env_supports_zero_model_specific_rate(self, monkeypatch):
        _clear_rates(monkeypatch)
        monkeypatch.setenv("MOZAIKS_USAGE_GPT_5_NANO_INPUT_PER_1K_USD", "0")
        monkeypatch.setenv("MOZAIKS_USAGE_INPUT_PER_1K_USD", "0.01")

        result = estimate_token_cost(
            model_name="gpt-5-nano",
            prompt_tokens=1000,
            completion_tokens=0,
        )

        assert result.cost_source == "estimated"
        assert result.estimated_cost_usd == 0.0

    def test_catalog_default_model_fallback(self, monkeypatch, tmp_path):
        _clear_rates(monkeypatch)
        catalog = tmp_path / "pricing.yaml"
        catalog.write_text(
            """
models:
  default:
    input_per_1k_usd: 0.01
    output_per_1k_usd: 0.02
""",
            encoding="utf-8",
        )
        monkeypatch.setenv("MOZAIKS_USAGE_PRICING_CATALOG_PATH", str(catalog))

        result = estimate_token_cost(
            model_name="new-provider-model",
            prompt_tokens=1000,
            completion_tokens=1000,
        )

        assert result.cost_source == "catalog"
        assert result.estimated_cost_usd == pytest.approx(0.03)

    def test_packaged_default_catalog_used_when_repo_catalog_absent(self, monkeypatch, tmp_path):
        _clear_rates(monkeypatch)
        monkeypatch.delenv("MOZAIKS_USAGE_PRICING_DISABLE_DEFAULT_CATALOG", raising=False)
        pricing._load_pricing_catalog.cache_clear()
        pricing._load_pricing_catalog_payload.cache_clear()
        missing_repo_catalog = tmp_path / "missing" / "usage-pricing.generated.json"
        packaged_catalog = tmp_path / "package" / "usage-pricing.generated.json"
        packaged_catalog.parent.mkdir(parents=True)
        packaged_catalog.write_text(
            """
{
  "models": {
    "gpt-5-nano": {
      "input_per_1m_usd": 0.05,
      "output_per_1m_usd": 0.40
    }
  }
}
""",
            encoding="utf-8",
        )
        monkeypatch.setattr(pricing, "_DEFAULT_GENERATED_CATALOG_PATH", missing_repo_catalog)
        monkeypatch.setattr(pricing, "_PACKAGE_GENERATED_CATALOG_PATH", packaged_catalog)

        result = estimate_token_cost(
            model_name="gpt-5-nano",
            prompt_tokens=1000,
            completion_tokens=1000,
        )

        assert result.cost_source == "catalog"
        assert result.estimated_cost_usd == pytest.approx(0.00045)

    def test_env_rates_override_catalog(self, monkeypatch, tmp_path):
        _clear_rates(monkeypatch)
        catalog = tmp_path / "pricing.yaml"
        catalog.write_text(
            """
models:
  gpt-5-nano:
    input_per_1k_usd: 0.001
    output_per_1k_usd: 0.004
""",
            encoding="utf-8",
        )
        monkeypatch.setenv("MOZAIKS_USAGE_PRICING_CATALOG_PATH", str(catalog))
        monkeypatch.setenv("MOZAIKS_USAGE_GPT_5_NANO_INPUT_PER_1K_USD", "0.1")
        monkeypatch.setenv("MOZAIKS_USAGE_GPT_5_NANO_OUTPUT_PER_1K_USD", "0.2")

        result = estimate_token_cost(
            model_name="gpt-5-nano",
            prompt_tokens=1000,
            completion_tokens=1000,
        )

        assert result.cost_source == "estimated"
        assert result.estimated_cost_usd == pytest.approx(0.3)

    def test_override_catalog_wins_over_generated_catalog(self, monkeypatch, tmp_path):
        _clear_rates(monkeypatch)
        catalog = tmp_path / "generated.json"
        override = tmp_path / "override.json"
        catalog.write_text(
            """
{
  "models": {
    "gpt-5-nano": {
      "input_per_1m_usd": 0.05,
      "output_per_1m_usd": 0.40
    }
  }
}
""",
            encoding="utf-8",
        )
        override.write_text(
            """
{
  "models": {
    "gpt-5-nano": {
      "input_per_1m_usd": 1.0,
      "output_per_1m_usd": 3.0
    }
  }
}
""",
            encoding="utf-8",
        )
        monkeypatch.setenv("MOZAIKS_USAGE_PRICING_CATALOG_PATH", str(catalog))
        monkeypatch.setenv("MOZAIKS_USAGE_PRICING_OVERRIDE_PATH", str(override))

        result = estimate_token_cost(
            model_name="gpt-5-nano",
            prompt_tokens=1000,
            completion_tokens=1000,
        )

        assert result.cost_source == "override"
        assert result.estimated_cost_usd == pytest.approx(0.004)

    def test_raw_litellm_catalog_fields_are_supported(self, monkeypatch, tmp_path):
        _clear_rates(monkeypatch)
        catalog = tmp_path / "litellm.json"
        catalog.write_text(
            """
{
  "gpt-example": {
    "litellm_provider": "openai",
    "input_cost_per_token": 0.0000005,
    "cache_read_input_token_cost": 0.00000005,
    "output_cost_per_token": 0.0000015
  }
}
""",
            encoding="utf-8",
        )
        monkeypatch.setenv("MOZAIKS_USAGE_PRICING_CATALOG_PATH", str(catalog))

        result = estimate_token_cost(
            model_name="gpt-example",
            prompt_tokens=2000,
            cached_prompt_tokens=1000,
            completion_tokens=1000,
        )

        assert result.cost_source == "catalog"
        assert result.estimated_cost_usd == pytest.approx(0.00205)

    def test_returns_usage_cost_estimate_instance(self, monkeypatch):
        _clear_rates(monkeypatch)
        result = estimate_token_cost(model_name=None, prompt_tokens=0, completion_tokens=0)
        assert isinstance(result, UsageCostEstimate)
