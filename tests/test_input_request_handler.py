from __future__ import annotations

import logging
from types import SimpleNamespace

import pytest

from mozaiksai.core.workflow.runtime_signals import SYSTEM_RESUME_SIGNAL
from mozaiksai.core.workflow.stream.context import StreamState
from mozaiksai.core.workflow.stream.handlers.input_handler import InputRequestHandler


class _FakeTransport:
    def __init__(self, recent_input_submit: bool = False) -> None:
        self.calls: list[tuple[str, str, object]] = []
        self.recent_input_submit = recent_input_submit
        self.sent_ui_events: list[tuple[dict, str | None]] = []

    def register_input_request(self, chat_id: str, request_id: str, respond_cb: object) -> str:
        self.calls.append((chat_id, request_id, respond_cb))
        return request_id

    def consume_recent_input_submit(self, _chat_id: str) -> bool:
        if self.recent_input_submit:
            self.recent_input_submit = False
            return True
        return False

    async def send_event_to_ui(self, event: dict, chat_id: str | None = None) -> None:
        self.sent_ui_events.append((event, chat_id))


class _FakePersistenceManager:
    def __init__(self, latest_message: dict | None = None) -> None:
        self.saved: list[dict] = []
        self.latest_message = latest_message
        self.cleared_requests: list[tuple[str, str | None]] = []

    async def save_pending_input_request(self, **kwargs):  # type: ignore[no-untyped-def]
        self.saved.append(kwargs)

    async def get_latest_message(self, **_kwargs):  # type: ignore[no-untyped-def]
        return self.latest_message

    async def clear_pending_input_request(
        self,
        *,
        chat_id: str,
        app_id: str | None = None,
    ) -> None:
        self.cleared_requests.append((chat_id, app_id))


def _build_ctx(transport: _FakeTransport, persistence_manager: _FakePersistenceManager) -> SimpleNamespace:
    return SimpleNamespace(
        chat_id="chat-1",
        app_id="app-1",
        workflow_name="Workflow",
        transport=transport,
        persistence_manager=persistence_manager,
        wf_logger=logging.getLogger("test.input_request_handler"),
        workflow_name_upper="WORKFLOW",
    )


@pytest.mark.asyncio
async def test_input_handler_suppresses_generic_group_feedback_prompt_text() -> None:
    handler = InputRequestHandler()
    transport = _FakeTransport()
    persistence_manager = _FakePersistenceManager(
        latest_message={"role": "assistant", "content": "What should we do next?"}
    )
    ctx = _build_ctx(transport, persistence_manager)
    state = StreamState(turn_agent="ValueInterviewAgent")

    def _respond(_value):  # type: ignore[no-untyped-def]
        return None

    prompt = (
        "Please give feedback to chat_manager. Press enter to skip and use auto-reply, "
        "or type 'exit' to stop the conversation: "
    )
    event = SimpleNamespace(
        content=SimpleNamespace(prompt=prompt, respond=_respond),
        prompt=prompt,
    )

    payload = await handler.handle(event, ctx, state)

    assert payload is not None
    assert payload["kind"] == "tool_call"
    assert payload["payload"]["prompt"] == ""
    assert payload["display"] == "composer"
    assert payload["payload"]["display"] == "composer"
    assert payload["metadata"]["source"] == "ag2_group_feedback_compat"
    assert payload["metadata"]["generic_feedback_prompt_suppressed"] is True
    assert state.awaiting_user_input is True
    assert payload["tool_call_id"] in state.pending_input_requests
    assert transport.calls[0][0] == "chat-1"
    assert transport.calls[0][1] == payload["tool_call_id"]
    assert callable(transport.calls[0][2])
    assert persistence_manager.saved[0]["prompt"] == ""
    assert persistence_manager.saved[0]["display"] == "composer"
    assert persistence_manager.saved[0]["raw_payload"]["resume_ui_kind"] == "awaiting_reply"
    assert transport.sent_ui_events == [
        (
            {
                "kind": "awaiting_reply",
                "agent": "ValueInterviewAgent",
                "chat_id": "chat-1",
                "workflow_name": "Workflow",
                "display": "composer",
                "interaction_type": "input_request",
                "reason": "awaiting_user_reply",
                "prompt": "",
                "source_agent": "ValueInterviewAgent",
                "metadata": {"source": "ag2_group_feedback_compat"},
            },
            "chat-1",
        )
    ]


@pytest.mark.asyncio
async def test_input_handler_auto_resumes_empty_generic_feedback_without_new_assistant_prompt() -> None:
    handler = InputRequestHandler()
    transport = _FakeTransport(recent_input_submit=True)
    persistence_manager = _FakePersistenceManager(
        latest_message={"role": "user", "content": "Approved. Proceed with implementation."}
    )
    ctx = _build_ctx(transport, persistence_manager)
    state = StreamState(
        turn_agent="ProjectOverviewAgent",
        last_text_role="user",
        last_text_content="Approved. Proceed with implementation.",
    )
    responded: list[str] = []

    def _respond(value):  # type: ignore[no-untyped-def]
        responded.append(value)
        return None

    prompt = (
        "Please give feedback to chat_manager. Press enter to skip and use auto-reply, "
        "or type 'exit' to stop the conversation: "
    )
    event = SimpleNamespace(
        content=SimpleNamespace(prompt=prompt, respond=_respond),
        prompt=prompt,
    )

    payload = await handler.handle(event, ctx, state)

    assert payload is None
    assert responded == [SYSTEM_RESUME_SIGNAL]
    assert state.awaiting_user_input is False
    assert state.pending_input_requests == {}
    assert transport.calls == []
    assert transport.sent_ui_events == []
    assert persistence_manager.saved == []
    assert persistence_manager.cleared_requests == [("chat-1", "app-1")]


@pytest.mark.asyncio
async def test_input_handler_does_not_auto_resume_generic_feedback_after_new_assistant_prompt() -> None:
    handler = InputRequestHandler()
    transport = _FakeTransport(recent_input_submit=True)
    persistence_manager = _FakePersistenceManager(
        latest_message={"role": "assistant", "content": "Who specifically are your target users?"}
    )
    ctx = _build_ctx(transport, persistence_manager)
    state = StreamState(
        turn_agent="ValueInterviewAgent",
        last_text_role="assistant",
        last_text_content="Who specifically are your target users?",
    )
    responded: list[str] = []

    def _respond(value):  # type: ignore[no-untyped-def]
        responded.append(value)
        return None

    prompt = (
        "Please give feedback to chat_manager. Press enter to skip and use auto-reply, "
        "or type 'exit' to stop the conversation: "
    )
    event = SimpleNamespace(
        content=SimpleNamespace(prompt=prompt, respond=_respond),
        prompt=prompt,
    )

    payload = await handler.handle(event, ctx, state)

    assert payload is not None
    assert payload["kind"] == "tool_call"
    assert responded == []
    assert state.awaiting_user_input is True
    assert payload["tool_call_id"] in state.pending_input_requests
    assert transport.calls[0][0] == "chat-1"
    assert transport.sent_ui_events[0][0]["kind"] == "awaiting_reply"
    assert persistence_manager.cleared_requests == []


@pytest.mark.asyncio
async def test_input_handler_preserves_non_generic_prompt_text() -> None:
    handler = InputRequestHandler()
    transport = _FakeTransport()
    persistence_manager = _FakePersistenceManager()
    ctx = _build_ctx(transport, persistence_manager)
    state = StreamState(turn_agent="ValueInterviewAgent")

    def _respond(_value):  # type: ignore[no-untyped-def]
        return None

    prompt = "Which customer segment should we focus on first?"
    event = SimpleNamespace(
        content=SimpleNamespace(prompt=prompt, respond=_respond),
        prompt=prompt,
    )

    payload = await handler.handle(event, ctx, state)

    assert payload is not None
    assert payload["kind"] == "tool_call"
    assert payload["payload"]["prompt"] == prompt
    assert payload["interaction_type"] == "input_request"
    assert payload["display"] == "composer"
    assert payload["payload"]["display"] == "composer"
    assert payload["metadata"]["source"] == "input_request_event"
    assert payload["metadata"]["generic_feedback_prompt_suppressed"] is False
    assert payload["tool_call_id"] in state.pending_input_requests
    assert transport.calls[0][0] == "chat-1"
    assert transport.calls[0][1] == payload["tool_call_id"]
    assert callable(transport.calls[0][2])
    assert persistence_manager.saved[0]["prompt"] == prompt
    assert persistence_manager.saved[0]["display"] == "composer"


@pytest.mark.asyncio
async def test_input_handler_tracked_callback_clears_awaiting_state_after_response() -> None:
    handler = InputRequestHandler()
    transport = _FakeTransport()
    persistence_manager = _FakePersistenceManager()
    ctx = _build_ctx(transport, persistence_manager)
    state = StreamState(turn_agent="ValueInterviewAgent")
    responded: list[str] = []

    def _respond(value):  # type: ignore[no-untyped-def]
        responded.append(value)
        return None

    prompt = "Approve the generated plan?"
    event = SimpleNamespace(
        content=SimpleNamespace(prompt=prompt, respond=_respond),
        prompt=prompt,
    )

    payload = await handler.handle(event, ctx, state)

    assert payload is not None
    tracked_callback = transport.calls[0][2]
    assert callable(tracked_callback)
    assert state.awaiting_user_input is True
    assert payload["tool_call_id"] in state.pending_input_requests

    await tracked_callback("Approved")

    assert responded == ["Approved"]
    assert state.awaiting_user_input is False
    assert state.pending_input_requests == {}


@pytest.mark.asyncio
async def test_input_handler_keeps_password_requests_inline() -> None:
    handler = InputRequestHandler()
    transport = _FakeTransport()
    persistence_manager = _FakePersistenceManager()
    ctx = _build_ctx(transport, persistence_manager)
    state = StreamState(turn_agent="ValueInterviewAgent")

    def _respond(_value):  # type: ignore[no-untyped-def]
        return None

    prompt = "Enter your deployment secret."
    event = SimpleNamespace(
        content=SimpleNamespace(prompt=prompt, respond=_respond),
        prompt=prompt,
        password=True,
    )

    payload = await handler.handle(event, ctx, state)

    assert payload is not None
    assert payload["display"] == "inline"
    assert payload["payload"]["display"] == "inline"
    assert payload["payload"]["password"] is True
    assert persistence_manager.saved[0]["display"] == "inline"
