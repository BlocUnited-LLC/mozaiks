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
        transport=transport,
        persistence_manager=persistence_manager,
        wf_logger=logging.getLogger("test.input_request_handler"),
        workflow_name_upper="WORKFLOW",
    )


@pytest.mark.asyncio
async def test_input_handler_suppresses_generic_group_feedback_prompt() -> None:
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
    assert payload["prompt"] == ""
    assert payload["agent"] == "ValueInterviewAgent"
    assert payload["metadata"]["source"] == "ag2_group_feedback_compat"
    assert payload["metadata"]["generic_feedback_prompt_suppressed"] is True
    assert payload["request_id"] in state.pending_input_requests
    assert transport.calls == [("chat-1", payload["request_id"], _respond)]
    assert persistence_manager.saved[0]["prompt"] == ""


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
    assert payload["prompt"] == prompt
    assert payload["metadata"]["source"] == "input_request_event"
    assert payload["metadata"]["generic_feedback_prompt_suppressed"] is False
    assert persistence_manager.saved[0]["prompt"] == prompt