from __future__ import annotations

from datetime import datetime, timezone

from scripts.run_live_mfj_smoke import SmokeResult, _resolve_assistant_message


def test_smoke_result_as_dict_serializes_nested_datetimes() -> None:
    result = SmokeResult(
        success=True,
        app_id="app-1",
        chat_id="chat-1",
        workflow_name="JokeWorker",
        prompt="prompt",
        assistant_message="done",
        structured_output={
            "ended_at": datetime(2026, 3, 31, 23, 30, tzinfo=timezone.utc),
            "nested": {"when": datetime(2026, 3, 31, 23, 31, tzinfo=timezone.utc)},
        },
        event_count=2,
        observed_event_types=["chat.text", "chat.workflow_complete"],
    )

    payload = result.as_dict()

    assert payload["structured_output"]["ended_at"] == "2026-03-31T23:30:00+00:00"
    assert payload["structured_output"]["nested"]["when"] == "2026-03-31T23:31:00+00:00"


def test_resolve_assistant_message_falls_back_to_structured_output() -> None:
    structured_output = {
        "agent_message": "The runtime smoke path was successfully summarized.",
    }

    message = _resolve_assistant_message([], structured_output)

    assert message == "The runtime smoke path was successfully summarized."
