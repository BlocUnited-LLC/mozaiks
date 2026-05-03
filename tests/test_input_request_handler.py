from __future__ import annotations

import logging
from types import SimpleNamespace

import pytest

from mozaiksai.core.workflow.stream.context import StreamState
from mozaiksai.core.workflow.stream.handlers.input_handler import InputRequestHandler


class _FakeTransport:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, object]] = []

    def register_input_request(self, chat_id: str, request_id: str, respond_cb: object) -> str:
        self.calls.append((chat_id, request_id, respond_cb))
        return request_id


class _FakePersistenceManager:
    def __init__(self) -> None:
        self.saved: list[dict] = []

    async def save_pending_input_request(self, **kwargs):  # type: ignore[no-untyped-def]
        self.saved.append(kwargs)


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
    persistence_manager = _FakePersistenceManager()
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
    assert transport.calls == [("chat-1", payload["tool_call_id"], _respond)]
    assert persistence_manager.saved[0]["prompt"] == ""
    assert persistence_manager.saved[0]["display"] == "composer"


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
    assert transport.calls == [("chat-1", payload["tool_call_id"], _respond)]
    assert persistence_manager.saved[0]["prompt"] == prompt
    assert persistence_manager.saved[0]["display"] == "composer"


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
