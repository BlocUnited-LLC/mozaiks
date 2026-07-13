from __future__ import annotations

from datetime import UTC, datetime

import pytest

from mozaiksai.core.usage.ledger import summarize_usage_events


def test_summarize_usage_events_groups_by_workflow_and_run():
    docs = [
        {
            "app_id": "app-1",
            "chat_id": "chat-1",
            "user_id": "user-1",
            "workflow_name": "AppGenerator",
            "prompt_tokens": 10,
            "completion_tokens": 5,
            "total_tokens": 15,
            "estimated_cost_usd": 0.03,
            "event_ts": datetime(2026, 6, 1, tzinfo=UTC),
        },
        {
            "app_id": "app-1",
            "chat_id": "chat-1",
            "user_id": "user-1",
            "workflow_name": "AppGenerator",
            "prompt_tokens": 7,
            "completion_tokens": 3,
            "total_tokens": 10,
            "estimated_cost_usd": 0.02,
            "event_ts": datetime(2026, 6, 1, tzinfo=UTC),
        },
        {
            "app_id": "app-1",
            "chat_id": "chat-2",
            "user_id": "user-2",
            "workflow_name": "AgentGenerator",
            "prompt_tokens": 20,
            "completion_tokens": 10,
            "total_tokens": 30,
            "estimated_cost_usd": 0.06,
            "event_ts": datetime(2026, 6, 2, tzinfo=UTC),
        },
    ]

    summary = summarize_usage_events(docs, app_id="app-1")

    assert summary["totals"] == {
        "prompt_tokens": 37,
        "completion_tokens": 18,
        "total_tokens": 55,
        "cached_prompt_tokens": 0,
        "estimated_cost_usd": 0.11,
        "llm_calls": 3,
    }
    assert [row["workflow_name"] for row in summary["by_workflow"]] == ["AgentGenerator", "AppGenerator"]
    app_generator = next(row for row in summary["by_workflow"] if row["workflow_name"] == "AppGenerator")
    assert app_generator["runs"] == 1
    assert app_generator["llm_calls"] == 2
    chat_1 = next(row for row in summary["by_run"] if row["chat_id"] == "chat-1")
    assert chat_1["app_id"] == "app-1"
    assert chat_1["total_tokens"] == 25
    assert chat_1["llm_calls"] == 2


def test_summarize_usage_events_groups_runs_by_app_and_chat_id():
    docs = [
        {
            "app_id": "app-1",
            "chat_id": "shared-chat",
            "user_id": "user-1",
            "workflow_name": "AppGenerator",
            "prompt_tokens": 10,
            "completion_tokens": 5,
            "total_tokens": 15,
            "estimated_cost_usd": 0.03,
            "event_ts": datetime(2026, 6, 1, tzinfo=UTC),
        },
        {
            "app_id": "app-2",
            "chat_id": "shared-chat",
            "user_id": "user-2",
            "workflow_name": "AppGenerator",
            "prompt_tokens": 20,
            "completion_tokens": 10,
            "total_tokens": 30,
            "estimated_cost_usd": 0.06,
            "event_ts": datetime(2026, 6, 2, tzinfo=UTC),
        },
    ]

    summary = summarize_usage_events(docs)

    assert len(summary["by_run"]) == 2
    assert {row["app_id"] for row in summary["by_run"]} == {"app-1", "app-2"}
    assert summary["by_workflow"][0]["workflow_name"] == "AppGenerator"
    assert summary["by_workflow"][0]["runs"] == 2


def test_summarize_usage_events_reports_pricing_health():
    docs = [
        {
            "app_id": "app-1",
            "chat_id": "chat-1",
            "user_id": "user-1",
            "workflow_name": "AppGenerator",
            "model_name": "known-model",
            "prompt_tokens": 10,
            "completion_tokens": 5,
            "total_tokens": 15,
            "estimated_cost_usd": 0.03,
            "cost_source": "catalog",
            "event_ts": datetime(2026, 6, 1, tzinfo=UTC),
        },
        {
            "app_id": "app-1",
            "chat_id": "chat-2",
            "user_id": "user-1",
            "workflow_name": "AppGenerator",
            "model_name": "missing-model",
            "prompt_tokens": 20,
            "completion_tokens": 5,
            "total_tokens": 25,
            "estimated_cost_usd": 0.0,
            "cost_source": "not_configured",
            "event_ts": datetime(2026, 6, 2, tzinfo=UTC),
        },
        {
            "app_id": "app-1",
            "chat_id": "chat-3",
            "user_id": "user-1",
            "workflow_name": "AppGenerator",
            "model_name": "fallback-model",
            "prompt_tokens": 30,
            "completion_tokens": 5,
            "total_tokens": 35,
            "estimated_cost_usd": 0.01,
            "cost_source": "default_table",
            "event_ts": datetime(2026, 6, 3, tzinfo=UTC),
        },
    ]

    summary = summarize_usage_events(docs, app_id="app-1")

    assert summary["cost_source"] == "mixed"
    health = summary["pricing_health"]
    assert health["status"] == "unpriced_models"
    assert health["used_model_count"] == 3
    assert health["unpriced_models"] == ["missing-model"]
    assert health["default_table_models"] == ["fallback-model"]
    assert health["cost_source_counts"] == {
        "catalog": 1,
        "not_configured": 1,
        "default_table": 1,
    }


def test_summarize_usage_events_reprices_old_unconfigured_rows_from_current_catalog(monkeypatch, tmp_path):
    catalog = tmp_path / "pricing.yaml"
    catalog.write_text(
        """
schema_version: mozaiks.usage_pricing.v1
models:
  gpt-4o-mini-2024-07-18:
    input_per_1m_usd: 0.15
    output_per_1m_usd: 0.60
""",
        encoding="utf-8",
    )
    monkeypatch.setenv("MOZAIKS_USAGE_PRICING_CATALOG_PATH", str(catalog))
    monkeypatch.delenv("MOZAIKS_USAGE_PRICING_OVERRIDE_PATH", raising=False)

    docs = [
        {
            "app_id": "app-1",
            "chat_id": "chat-1",
            "user_id": "user-1",
            "workflow_name": "AppGenerator",
            "model_name": "gpt-4o-mini-2024-07-18",
            "prompt_tokens": 1000,
            "completion_tokens": 1000,
            "total_tokens": 2000,
            "estimated_cost_usd": 0.0,
            "cost_source": "not_configured",
            "event_ts": datetime(2026, 6, 1, tzinfo=UTC),
        },
    ]

    summary = summarize_usage_events(docs, app_id="app-1")

    assert summary["totals"]["estimated_cost_usd"] == pytest.approx(0.00075)
    assert summary["cost_source"] == "catalog"
    assert summary["events"][0]["cost_source"] == "catalog"
    assert summary["pricing_health"]["status"] == "ready"
    assert summary["pricing_health"]["unpriced_models"] == []
