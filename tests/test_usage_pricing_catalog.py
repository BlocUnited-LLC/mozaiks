from __future__ import annotations

from datetime import UTC, datetime

from mozaiksai.core.usage.pricing_catalog import normalize_litellm_pricing_catalog
from scripts.update_usage_pricing_catalog import (
    _catalog_change_summary,
    _validate_catalog_refresh,
)


def test_normalize_litellm_pricing_catalog_converts_per_token_to_per_million() -> None:
    payload = normalize_litellm_pricing_catalog(
        {
            "sample_spec": {"input_cost_per_token": 0.0},
            "gpt-example": {
                "litellm_provider": "openai",
                "mode": "chat",
                "input_cost_per_token": 0.0000005,
                "cache_read_input_token_cost": 0.00000005,
                "output_cost_per_token": 0.0000015,
                "max_input_tokens": 128000,
                "max_output_tokens": 16000,
            },
            "image-model": {
                "litellm_provider": "openai",
                "mode": "image_generation",
                "output_cost_per_image": 0.04,
            },
        },
        source_url="https://example.com/model_prices.json",
        fetched_at=datetime(2026, 1, 2, tzinfo=UTC),
        source_revision='"etag-1"',
        source_content_sha256="abc123",
    )

    assert payload["schema_version"] == "mozaiks.usage_pricing.v1"
    assert payload["source"]["name"] == "litellm"
    assert payload["source"]["source_revision"] == '"etag-1"'
    assert payload["source"]["content_sha256"] == "abc123"
    assert "sample_spec" not in payload["models"]
    assert "image-model" not in payload["models"]
    assert payload["models"]["gpt-example"] == {
        "provider": "openai",
        "mode": "chat",
        "source": "litellm",
        "input_per_1m_usd": 0.5,
        "cached_input_per_1m_usd": 0.05,
        "output_per_1m_usd": 1.5,
        "max_input_tokens": 128000,
        "max_output_tokens": 16000,
    }


def test_catalog_change_summary_counts_added_removed_and_changed() -> None:
    existing = {
        "models": {
            "removed-model": {"input_per_1m_usd": 1.0},
            "changed-model": {"input_per_1m_usd": 1.0},
            "same-model": {"input_per_1m_usd": 1.0},
        }
    }
    payload = {
        "models": {
            "added-model": {"input_per_1m_usd": 1.0},
            "changed-model": {"input_per_1m_usd": 2.0},
            "same-model": {"input_per_1m_usd": 1.0},
        }
    }

    summary = _catalog_change_summary(existing, payload)

    assert summary["existing_models"] == 3
    assert summary["next_models"] == 3
    assert summary["added"] == 1
    assert summary["removed"] == 1
    assert summary["changed"] == 1
    assert summary["added_sample"] == ["added-model"]
    assert summary["removed_sample"] == ["removed-model"]
    assert summary["changed_sample"] == ["changed-model"]


def test_validate_catalog_refresh_rejects_unexpectedly_small_catalog() -> None:
    raw_catalog = {
        f"model-{index}": {"input_cost_per_token": 0.000001}
        for index in range(10)
    }
    payload = {"models": {"model-1": {"input_per_1m_usd": 1.0}}}

    try:
        _validate_catalog_refresh(
            raw_catalog=raw_catalog,
            payload=payload,
            existing=None,
            min_normalized_model_count=5,
            min_normalized_ratio=0.5,
            max_row_drop_percent=20.0,
            allow_large_row_drop=False,
        )
    except RuntimeError as exc:
        assert "unexpectedly small" in str(exc)
    else:
        raise AssertionError("expected catalog validation to fail")


def test_validate_catalog_refresh_rejects_large_existing_row_drop() -> None:
    raw_catalog = {
        f"model-{index}": {"input_cost_per_token": 0.000001}
        for index in range(80)
    }
    payload = {
        "models": {
            f"model-{index}": {"input_per_1m_usd": 1.0}
            for index in range(80)
        }
    }
    existing = {
        "models": {
            f"model-{index}": {"input_per_1m_usd": 1.0}
            for index in range(100)
        }
    }

    try:
        _validate_catalog_refresh(
            raw_catalog=raw_catalog,
            payload=payload,
            existing=existing,
            min_normalized_model_count=1,
            min_normalized_ratio=0.5,
            max_row_drop_percent=10.0,
            allow_large_row_drop=False,
        )
    except RuntimeError as exc:
        assert "row count dropped too much" in str(exc)
    else:
        raise AssertionError("expected catalog validation to fail")
