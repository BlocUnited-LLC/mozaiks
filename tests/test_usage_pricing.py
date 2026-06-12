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
    ]:
        monkeypatch.delenv(name, raising=False)


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

    def test_no_rates_not_configured(self, monkeypatch):
        _clear_rates(monkeypatch)
        result = estimate_token_cost(
            model_name="gpt-4",
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

    def test_returns_usage_cost_estimate_instance(self, monkeypatch):
        _clear_rates(monkeypatch)
        result = estimate_token_cost(model_name=None, prompt_tokens=0, completion_tokens=0)
        assert isinstance(result, UsageCostEstimate)
