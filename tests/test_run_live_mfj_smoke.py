from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from scripts.run_live_mfj_smoke import (
    SmokeResult,
    _build_tool_call_response_payload,
    _collect_events,
    _configure_event_loop_policy,
    _is_input_request_tool_call,
    _resolve_assistant_message,
    _resolve_default_workflows_root,
)


def test_smoke_result_as_dict_serializes_nested_datetimes() -> None:
    result = SmokeResult(
        success=True,
        app_id="app-1",
        chat_id="chat-1",
        workflow_name="RuntimeSmoke",
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


def test_resolve_default_workflows_root_uses_repo_factory_workflows(monkeypatch) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    monkeypatch.chdir(repo_root)
    monkeypatch.delenv("MOZAIKS_WORKFLOW_ROOTS", raising=False)
    monkeypatch.delenv("MOZAIKS_WORKFLOWS_PATH", raising=False)
    monkeypatch.delenv("PLATFORM_PATH", raising=False)
    monkeypatch.delenv("MOZAIKS_APP_WORKSPACE_PATH", raising=False)

    root = _resolve_default_workflows_root()

    assert root == (repo_root / "factory_app" / "workflows").resolve()


def test_build_tool_call_response_payload_uses_canonical_fields() -> None:
    payload = _build_tool_call_response_payload("approved")

    assert payload == {
        "status": "submitted",
        "text": "approved",
        "user_input": "approved",
        "user_response": "approved",
    }


def test_configure_event_loop_policy_uses_selector_on_windows(monkeypatch) -> None:
    captured = {}

    class _FakePolicy:
        pass

    monkeypatch.setattr("scripts.run_live_mfj_smoke.os.name", "nt")
    monkeypatch.setattr(
        "scripts.run_live_mfj_smoke.asyncio.WindowsSelectorEventLoopPolicy",
        _FakePolicy,
        raising=False,
    )
    monkeypatch.setattr(
        "scripts.run_live_mfj_smoke.asyncio.set_event_loop_policy",
        lambda policy: captured.setdefault("policy", policy),
    )

    _configure_event_loop_policy()

    assert isinstance(captured["policy"], _FakePolicy)


def test_is_input_request_tool_call_detects_canonical_payloads() -> None:
    assert _is_input_request_tool_call(
        {
            "tool_name": "UserInputRequest",
            "component_type": "UserInputRequest",
            "interaction_type": "input_request",
            "payload": {"interaction_type": "input_request"},
        }
    )
    assert not _is_input_request_tool_call(
        {
            "tool_name": "ConceptBlueprint",
            "component_type": "ConceptBlueprint",
            "interaction_type": "ui_surface",
            "payload": {"interaction_type": "ui_surface"},
        }
    )


class _FakeWebSocket:
    def __init__(self, events: list[dict]) -> None:
        self._events = [json.dumps(event) for event in events]
        self.sent: list[dict] = []

    async def recv(self) -> str:
        if not self._events:
            raise TimeoutError("no more events")
        return self._events.pop(0)

    async def send(self, raw: str) -> None:
        self.sent.append(json.loads(raw))


@pytest.mark.asyncio
async def test_collect_events_sends_scripted_user_reply_with_chat_id() -> None:
    websocket = _FakeWebSocket(
        [
            {
                "type": "chat.tool_call",
                "data": {
                    "tool_call_id": "tc-1",
                    "tool_name": "UserInputRequest",
                    "component_type": "UserInputRequest",
                    "interaction_type": "input_request",
                    "awaiting_response": True,
                    "payload": {
                        "interaction_type": "input_request",
                        "component_type": "UserInputRequest",
                        "prompt": "Who is your first target user?",
                    },
                },
            },
            {
                "type": "ack.tool_call_response",
                "data": {"tool_call_id": "tc-1", "status": "accepted"},
            },
        ]
    )

    events = await _collect_events(
        websocket,
        chat_id="chat-test-1",
        timeout_seconds=0.1,
        user_replies=["Independent consultants with 5-10 active clients."],
    )

    assert [event["type"] for event in events] == ["chat.tool_call", "ack.tool_call_response"]
    assert websocket.sent == [
        {
            "type": "tool_call_response",
            "tool_call_id": "tc-1",
            "response": {
                "status": "submitted",
                "text": "Independent consultants with 5-10 active clients.",
                "user_input": "Independent consultants with 5-10 active clients.",
                "user_response": "Independent consultants with 5-10 active clients.",
            },
        }
    ]


@pytest.mark.asyncio
async def test_collect_events_uses_fallback_tool_response_for_non_input_tool_call() -> None:
    websocket = _FakeWebSocket(
        [
            {
                "type": "chat.tool_call",
                "data": {
                    "tool_call_id": "tc-2",
                    "tool_name": "ApprovalCard",
                    "component_type": "ApprovalCard",
                    "interaction_type": "ui_tool",
                    "awaiting_response": True,
                    "payload": {
                        "interaction_type": "ui_tool",
                        "component_type": "ApprovalCard",
                    },
                },
            }
        ]
    )

    events = await _collect_events(
        websocket,
        chat_id="chat-test-2",
        timeout_seconds=0.1,
        tool_response_text="approved",
    )

    assert [event["type"] for event in events] == ["chat.tool_call"]
    assert websocket.sent == [
        {
            "type": "tool_call_response",
            "tool_call_id": "tc-2",
            "response": {
                "status": "submitted",
                "text": "approved",
                "user_input": "approved",
                "user_response": "approved",
            },
        }
    ]


@pytest.mark.asyncio
async def test_collect_events_raises_on_websocket_error_event() -> None:
    websocket = _FakeWebSocket(
        [
            {
                "type": "error",
                "data": {
                    "code": "USER_MESSAGE_FAILED",
                    "message": "User message failed",
                },
            }
        ]
    )

    with pytest.raises(RuntimeError, match="USER_MESSAGE_FAILED"):
        await _collect_events(
            websocket,
            chat_id="chat-test-3",
            timeout_seconds=0.1,
        )
