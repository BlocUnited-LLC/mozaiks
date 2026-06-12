from __future__ import annotations

from datetime import datetime, timezone

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
            "event_ts": datetime(2026, 6, 1, tzinfo=timezone.utc),
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
            "event_ts": datetime(2026, 6, 1, tzinfo=timezone.utc),
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
            "event_ts": datetime(2026, 6, 2, tzinfo=timezone.utc),
        },
    ]

    summary = summarize_usage_events(docs, app_id="app-1")

    assert summary["totals"] == {
        "prompt_tokens": 37,
        "completion_tokens": 18,
        "total_tokens": 55,
        "estimated_cost_usd": 0.11,
        "llm_calls": 3,
    }
    assert [row["workflow_name"] for row in summary["by_workflow"]] == ["AgentGenerator", "AppGenerator"]
    app_generator = next(row for row in summary["by_workflow"] if row["workflow_name"] == "AppGenerator")
    assert app_generator["runs"] == 1
    assert app_generator["llm_calls"] == 2
    chat_1 = next(row for row in summary["by_run"] if row["chat_id"] == "chat-1")
    assert chat_1["total_tokens"] == 25
    assert chat_1["llm_calls"] == 2
