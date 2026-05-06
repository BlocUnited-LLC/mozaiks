from __future__ import annotations

import asyncio
from collections import deque
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from scripts.run_live_mfj_smoke import (
    SmokeResult,
    _build_workflow_user_reply_message,
    _build_uvicorn_config,
    _build_tool_call_response_payload,
    _await_workflow_with_pending_input_fallback,
    _collect_events,
    _configure_event_loop_policy,
    _is_generic_feedback_pending_input,
    _is_input_request_tool_call,
    _load_prompt_file,
    _load_tool_response_file,
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


def test_build_workflow_user_reply_message_uses_workflow_transport_fields() -> None:
    payload = _build_workflow_user_reply_message("chat-42", "continue")

    assert payload == {
        "type": "user.input.submit",
        "chat_id": "chat-42",
        "text": "continue",
        "context": {
            "source": "live_mfj_smoke",
            "conversation_mode": "workflow",
        },
    }


def test_build_uvicorn_config_defaults_to_lifespan_off() -> None:
    config = _build_uvicorn_config(object(), 9001)

    assert config.host == "127.0.0.1"
    assert config.port == 9001
    assert config.access_log is False
    assert config.lifespan == "off"


def test_build_uvicorn_config_allows_lifespan_override() -> None:
    config = _build_uvicorn_config(object(), 9001, lifespan="auto")

    assert config.lifespan == "auto"


def test_load_tool_response_file_reads_scripted_replies_and_structured_payloads(tmp_path: Path) -> None:
    fixture = tmp_path / "responses.json"
    fixture.write_text(
        json.dumps(
            {
                "input_replies": ["First reply"],
                "default_input_reply": "Fallback reply",
                "assistant_reply_rules": [
                    {"contains": "final tweaks", "reply": "No final tweaks. Proceed."}
                ],
                "tool_responses": {
                    "ApprovalCard": {"action": "approve", "approved": True},
                },
            }
        ),
        encoding="utf-8",
    )

    payload = _load_tool_response_file(fixture)

    assert payload == {
        "input_replies": ["First reply"],
        "default_input_reply": "Fallback reply",
        "assistant_reply_rules": [
            {"contains": "final tweaks", "reply": "No final tweaks. Proceed."}
        ],
        "tool_responses": {
            "ApprovalCard": {"action": "approve", "approved": True},
        },
    }


def test_load_prompt_file_reads_non_empty_prompt(tmp_path: Path) -> None:
    fixture = tmp_path / "prompt.txt"
    fixture.write_text("Plan a deterministic approval workflow.\n", encoding="utf-8")

    prompt = _load_prompt_file(fixture)

    assert prompt == "Plan a deterministic approval workflow."


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


def test_is_generic_feedback_pending_input_detects_ag2_feedback_prompt() -> None:
    assert _is_generic_feedback_pending_input(
        {
            "raw_payload": {
                "prompt": "Please give feedback to chat_manager. Press enter to skip and use auto-reply, or type 'exit' to stop the conversation: "
            }
        }
    )
    assert not _is_generic_feedback_pending_input({"prompt": "Which urgency level should we use?"})


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


class _LaggyWebSocket:
    def __init__(self, steps: list[object]) -> None:
        self._steps = list(steps)
        self.sent: list[dict] = []

    async def recv(self) -> str:
        if not self._steps:
            raise TimeoutError("no more events")
        step = self._steps.pop(0)
        if step == "lag":
            await asyncio.sleep(1.0)
            raise AssertionError("lag step should be cancelled by wait_for before returning")
        return json.dumps(step)

    async def send(self, raw: str) -> None:
        self.sent.append(json.loads(raw))


class _FakeTransport:
    def __init__(self) -> None:
        self.responses: list[tuple[str, dict]] = []

    async def submit_tool_call_response(self, request_id: str, response: dict) -> bool:
        self.responses.append((request_id, response))
        return True


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
async def test_collect_events_uses_default_input_reply_when_scripted_replies_are_exhausted() -> None:
    websocket = _FakeWebSocket(
        [
            {
                "type": "chat.tool_call",
                "data": {
                    "tool_call_id": "tc-fallback",
                    "tool_name": "UserInputRequest",
                    "component_type": "UserInputRequest",
                    "interaction_type": "input_request",
                    "awaiting_response": True,
                    "payload": {
                        "interaction_type": "input_request",
                        "component_type": "UserInputRequest",
                        "prompt": "",
                    },
                },
            }
        ]
    )

    events = await _collect_events(
        websocket,
        chat_id="chat-test-fallback",
        timeout_seconds=0.1,
        user_replies=[],
        default_input_reply="Proceed with the workflow.",
    )

    assert [event["type"] for event in events] == ["chat.tool_call"]
    assert websocket.sent == [
        {
            "type": "tool_call_response",
            "tool_call_id": "tc-fallback",
            "response": {
                "status": "submitted",
                "text": "Proceed with the workflow.",
                "user_input": "Proceed with the workflow.",
                "user_response": "Proceed with the workflow.",
            },
        }
    ]


@pytest.mark.asyncio
async def test_collect_events_prefers_assistant_reply_rule_for_input_requests() -> None:
    websocket = _FakeWebSocket(
        [
            {
                "type": "chat.text",
                "data": {
                    "agent": "InterviewAgent",
                    "content": "Is everything correct, or do you have any final tweaks?",
                },
            },
            {
                "type": "chat.tool_call",
                "data": {
                    "tool_call_id": "tc-rule",
                    "tool_name": "UserInputRequest",
                    "component_type": "UserInputRequest",
                    "interaction_type": "input_request",
                    "awaiting_response": True,
                    "payload": {
                        "interaction_type": "input_request",
                        "component_type": "UserInputRequest",
                        "prompt": "",
                    },
                },
            },
        ]
    )

    events = await _collect_events(
        websocket,
        chat_id="chat-test-rule",
        timeout_seconds=0.1,
        user_replies=["Fallback queue reply"],
        assistant_reply_rules=[
            {
                "contains": "final tweaks",
                "reply": "No final tweaks. Proceed to workflow planning.",
            }
        ],
        default_input_reply="Default reply.",
    )

    assert [event["type"] for event in events] == ["chat.text", "chat.tool_call"]
    assert websocket.sent == [
        {
            "type": "tool_call_response",
            "tool_call_id": "tc-rule",
            "response": {
                "status": "submitted",
                "text": "No final tweaks. Proceed to workflow planning.",
                "user_input": "No final tweaks. Proceed to workflow planning.",
                "user_response": "No final tweaks. Proceed to workflow planning.",
            },
        }
    ]


@pytest.mark.asyncio
async def test_collect_events_keeps_waiting_through_quiet_gap_when_workflow_is_still_running() -> None:
    websocket = _LaggyWebSocket(
        [
            {
                "type": "chat.text",
                "data": {
                    "agent": "InterviewAgent",
                    "content": "Let me think through the workflow shape.",
                },
            },
            "lag",
            {
                "type": "chat.run_complete",
                "data": {
                    "status": 1,
                    "reason": "completed",
                },
            },
        ]
    )

    async def _pending_input_provider():
        return None

    events = await _collect_events(
        websocket,
        chat_id="chat-test-lag",
        timeout_seconds=2.5,
        pending_input_provider=_pending_input_provider,
    )

    assert [event["type"] for event in events] == ["chat.text", "chat.run_complete"]
    assert websocket.sent == []


@pytest.mark.asyncio
async def test_collect_events_responds_to_persisted_pending_input_when_websocket_goes_quiet() -> None:
    websocket = _FakeWebSocket(
        [
            {
                "type": "chat.text",
                "data": {
                    "agent": "ProjectOverviewAgent",
                    "content": "Review the agent flow below, then reply in chat with approval or requested changes.",
                },
            }
        ]
    )
    pending_request = {
        "request_id": "tc-persisted",
        "tool_name": "UserInputRequest",
        "component_type": "UserInputRequest",
        "interaction_type": "input_request",
        "prompt": "",
    }

    async def _pending_input_provider():
        return pending_request

    events = await _collect_events(
        websocket,
        chat_id="chat-test-persisted",
        timeout_seconds=0.1,
        assistant_reply_rules=[
            {
                "contains": "Review the agent flow below",
                "reply": "Approved. The sequence diagram looks good. Proceed with implementation.",
            }
        ],
        pending_input_provider=_pending_input_provider,
    )

    assert [event["type"] for event in events] == ["chat.text"]
    assert websocket.sent == [
        {
            "type": "tool_call_response",
            "tool_call_id": "tc-persisted",
            "response": {
                "status": "submitted",
                "text": "Approved. The sequence diagram looks good. Proceed with implementation.",
                "user_input": "Approved. The sequence diagram looks good. Proceed with implementation.",
                "user_response": "Approved. The sequence diagram looks good. Proceed with implementation.",
            },
        }
    ]


@pytest.mark.asyncio
async def test_collect_events_deduplicates_persisted_pending_input_and_late_tool_call() -> None:
    websocket = _LaggyWebSocket(
        [
            {
                "type": "chat.text",
                "data": {
                    "agent": "ProjectOverviewAgent",
                    "content": "Review the agent flow below, then reply in chat with approval or requested changes.",
                },
            },
            "lag",
            {
                "type": "chat.tool_call",
                "data": {
                    "tool_call_id": "tc-dedupe",
                    "tool_name": "UserInputRequest",
                    "component_type": "UserInputRequest",
                    "interaction_type": "input_request",
                    "awaiting_response": True,
                    "payload": {
                        "interaction_type": "input_request",
                        "component_type": "UserInputRequest",
                        "prompt": "",
                    },
                },
            },
        ]
    )
    pending_request = {
        "request_id": "tc-dedupe",
        "tool_name": "UserInputRequest",
        "component_type": "UserInputRequest",
        "interaction_type": "input_request",
        "prompt": "",
        "assistant_message": "Review the agent flow below, then reply in chat with approval or requested changes.",
    }

    async def _pending_input_provider():
        return pending_request

    events = await _collect_events(
        websocket,
        chat_id="chat-test-dedupe",
        timeout_seconds=2.5,
        assistant_reply_rules=[
            {
                "contains": "Review the agent flow below",
                "reply": "Approved. The sequence diagram looks good. Proceed with implementation.",
            }
        ],
        pending_input_provider=_pending_input_provider,
    )

    assert [event["type"] for event in events] == ["chat.text", "chat.tool_call"]
    assert websocket.sent == [
        {
            "type": "tool_call_response",
            "tool_call_id": "tc-dedupe",
            "response": {
                "status": "submitted",
                "text": "Approved. The sequence diagram looks good. Proceed with implementation.",
                "user_input": "Approved. The sequence diagram looks good. Proceed with implementation.",
                "user_response": "Approved. The sequence diagram looks good. Proceed with implementation.",
            },
        }
    ]


@pytest.mark.asyncio
async def test_await_workflow_with_pending_input_fallback_submits_direct_response() -> None:
    transport = _FakeTransport()
    workflow_wait_task = asyncio.create_task(asyncio.sleep(1.1, result={"run_status": "completed"}))

    async def _pending_input_provider():
        return {
            "request_id": "tc-direct",
            "assistant_message": "Review the agent flow below and approve it.",
        }

    reply_state = {
        "reply_queue": deque(["Fallback queue reply"]),
        "assistant_reply_rules": [
            {
                "contains": "Review the agent flow below",
                "reply": "Approved. The sequence diagram looks good. Proceed with implementation.",
            }
        ],
    }

    result = await _await_workflow_with_pending_input_fallback(
        workflow_wait_task=workflow_wait_task,
        transport=transport,
        pending_input_provider=_pending_input_provider,
        events=[],
        reply_state=reply_state,
        default_input_reply="Default reply.",
    )

    assert result == {"run_status": "completed"}
    assert transport.responses == [
        (
            "tc-direct",
            {
                "status": "submitted",
                "text": "Approved. The sequence diagram looks good. Proceed with implementation.",
                "user_input": "Approved. The sequence diagram looks good. Proceed with implementation.",
                "user_response": "Approved. The sequence diagram looks good. Proceed with implementation.",
            },
        )
    ]


@pytest.mark.asyncio
async def test_await_workflow_with_pending_input_fallback_skips_already_responded_request() -> None:
    transport = _FakeTransport()
    workflow_wait_task = asyncio.create_task(asyncio.sleep(1.1, result={"run_status": "completed"}))

    async def _pending_input_provider():
        return {
            "request_id": "tc-direct",
            "assistant_message": "Review the agent flow below and approve it.",
        }

    reply_state = {
        "reply_queue": deque(["Fallback queue reply"]),
        "assistant_reply_rules": [],
        "responded_pending_requests": {"tc-direct"},
        "responded_tool_calls": {"tc-direct"},
    }

    result = await _await_workflow_with_pending_input_fallback(
        workflow_wait_task=workflow_wait_task,
        transport=transport,
        pending_input_provider=_pending_input_provider,
        events=[],
        reply_state=reply_state,
        default_input_reply="Default reply.",
    )

    assert result == {"run_status": "completed"}
    assert transport.responses == []


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
async def test_collect_events_uses_structured_tool_response_payload_for_matching_component() -> None:
    websocket = _FakeWebSocket(
        [
            {
                "type": "chat.tool_call",
                "data": {
                    "tool_call_id": "tc-3",
                    "tool_name": "ApprovalCard",
                    "component_type": "ApprovalCard",
                    "interaction_type": "ui_tool",
                    "awaiting_response": True,
                    "payload": {
                        "interaction_type": "ui_tool",
                        "component_type": "ApprovalCard",
                        "workflow_primitive": "approval_card",
                    },
                },
            }
        ]
    )

    events = await _collect_events(
        websocket,
        chat_id="chat-test-4",
        timeout_seconds=0.1,
        tool_response_payloads={
            "ApprovalCard": {
                "action": "approve",
                "approved": True,
                "rationale": "Structured approval worked.",
            }
        },
    )

    assert [event["type"] for event in events] == ["chat.tool_call"]
    assert websocket.sent == [
        {
            "type": "tool_call_response",
            "tool_call_id": "tc-3",
            "response": {
                "status": "submitted",
                "action": "approve",
                "approved": True,
                "rationale": "Structured approval worked.",
            },
        }
    ]


@pytest.mark.asyncio
async def test_collect_events_resumes_paused_workflow_with_scripted_reply() -> None:
    websocket = _FakeWebSocket(
        [
            {
                "type": "chat.run_complete",
                "data": {
                    "status": 0,
                    "reason": "awaiting_user_input",
                    "awaiting_user_input": True,
                },
            },
            {
                "type": "chat.input_ack",
                "data": {"chat_id": "chat-test-pause", "status": "accepted"},
            },
        ]
    )

    events = await _collect_events(
        websocket,
        chat_id="chat-test-pause",
        timeout_seconds=0.1,
        user_replies=["Continue with the implementation plan."],
    )

    assert [event["type"] for event in events] == ["chat.run_complete", "chat.input_ack"]
    assert websocket.sent == [
        {
            "type": "user.input.submit",
            "chat_id": "chat-test-pause",
            "text": "Continue with the implementation plan.",
            "context": {
                "source": "live_mfj_smoke",
                "conversation_mode": "workflow",
            },
        }
    ]


@pytest.mark.asyncio
async def test_collect_events_uses_awaiting_reply_signal_for_freeform_resume() -> None:
    websocket = _FakeWebSocket(
        [
            {
                "type": "chat.awaiting_reply",
                "data": {
                    "display": "composer",
                    "interaction_type": "input_request",
                    "reason": "awaiting_user_reply",
                },
            }
        ]
    )

    events = await _collect_events(
        websocket,
        chat_id="chat-test-awaiting-reply",
        timeout_seconds=0.1,
        default_input_reply="Continue after reviewing the diagram.",
    )

    assert [event["type"] for event in events] == ["chat.awaiting_reply"]
    assert websocket.sent == [
        {
            "type": "user.input.submit",
            "chat_id": "chat-test-awaiting-reply",
            "text": "Continue after reviewing the diagram.",
            "context": {
                "source": "live_mfj_smoke",
                "conversation_mode": "workflow",
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
