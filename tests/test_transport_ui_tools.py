from __future__ import annotations

import asyncio
import logging
import queue
import threading
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from mozaiksai.core.transport.ui_tools import UIToolsMixin


class _FakeTransport(UIToolsMixin):
    _instance = None

    def __init__(self) -> None:
        self.pending_tool_call_responses = {}
        self._buffered_tool_call_responses = {}
        self._ui_tool_metadata = {}
        self._resolved_tool_call_ids = {}
        self._derived_context_managers = {}
        self._input_request_registries = {}
        self.connections = {}
        self.sent_events = []

    @classmethod
    async def get_instance(cls):
        return cls._instance

    async def send_event_to_ui(self, event, chat_id=None):
        self.sent_events.append((event, chat_id))

    async def _persist_ui_tool_state(self, **kwargs):
        return None


@pytest.mark.asyncio
async def test_submit_tool_call_response_completes_future_on_owner_loop() -> None:
    transport = _FakeTransport()
    result_queue: queue.Queue[dict] = queue.Queue()
    ready = threading.Event()

    def _wait_on_foreign_loop() -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        async def _runner() -> None:
            future = loop.create_future()
            transport.pending_tool_call_responses["evt-1"] = future
            ready.set()
            result = await future
            result_queue.put(result)

        loop.run_until_complete(_runner())
        loop.close()

    worker = threading.Thread(target=_wait_on_foreign_loop, daemon=True)
    worker.start()

    assert ready.wait(timeout=2.0) is True

    accepted = await transport.submit_tool_call_response("evt-1", {"status": "submitted", "text": "approved"})

    worker.join(timeout=2.0)

    assert accepted is True
    assert not worker.is_alive()
    assert result_queue.get_nowait() == {"status": "submitted", "text": "approved"}


@pytest.mark.asyncio
async def test_submit_tool_call_response_buffers_early_response_until_waiter_registers() -> None:
    transport = _FakeTransport()
    _FakeTransport._instance = transport
    transport._ui_tool_metadata["evt-early"] = {
        "chat_id": "chat-1",
        "tool_name": "UserInputRequest",
        "display": "inline",
    }

    accepted = await transport.submit_tool_call_response(
        "evt-early",
        {"status": "submitted", "text": "approved"},
    )
    result = await _FakeTransport.wait_for_tool_call_response("evt-early", timeout=0.1)

    assert accepted is True
    assert result == {"status": "submitted", "text": "approved"}
    assert "evt-early" not in transport._buffered_tool_call_responses


@pytest.mark.asyncio
async def test_submit_tool_call_response_ignores_duplicate_resolved_response(caplog: pytest.LogCaptureFixture) -> None:
    transport = _FakeTransport()
    _FakeTransport._instance = transport
    transport._ui_tool_metadata["evt-dup"] = {
        "chat_id": "chat-1",
        "tool_name": "ApprovalCard",
        "display": "inline",
    }

    with caplog.at_level(logging.WARNING, logger="simple_transport.ui_tools"):
        accepted_first = await transport.submit_tool_call_response(
            "evt-dup",
            {"status": "submitted", "action": "approve", "approved": True},
        )
        accepted_second = await transport.submit_tool_call_response(
            "evt-dup",
            {"status": "submitted", "action": "approve", "approved": True},
        )

    assert accepted_first is True
    assert accepted_second is True
    assert "evt-dup" in transport._resolved_tool_call_ids
    assert "[UI_TOOL] No pending event found for evt-dup" not in caplog.text


@pytest.mark.asyncio
async def test_send_tool_call_event_emits_canonical_tool_call_envelope() -> None:
    transport = _FakeTransport()

    await transport.send_tool_call_event(
        event_id="evt-ui-1",
        chat_id="chat-1",
        tool_name="build_plan_card",
        component_name="ApprovalCard",
        display_type="inline",
        payload={"workflow_name": "PlannerFlow", "title": "Review the plan"},
        awaiting_response=True,
        agent_name="PlannerAgent",
    )

    assert len(transport.sent_events) == 1
    event, chat_id = transport.sent_events[0]
    assert chat_id == "chat-1"
    assert event["kind"] == "tool_call"
    assert event["tool_call_id"] == "evt-ui-1"
    assert event["corr"] == "evt-ui-1"
    assert event["tool_name"] == "build_plan_card"
    assert event["component_type"] == "ApprovalCard"
    assert event["workflow_name"] == "PlannerFlow"
    assert event["display"] == "inline"
    assert event["display_type"] == "inline"
    assert event["interaction_type"] == "ui_tool"
    assert event["awaiting_response"] is True
    assert event["agent"] == "PlannerAgent"
    assert event["agent_name"] == "PlannerAgent"


@pytest.mark.asyncio
async def test_pending_inline_payload_is_persisted_before_delivery(monkeypatch) -> None:
    transport = _FakeTransport()
    pm = SimpleNamespace(attach_tool_call_metadata=AsyncMock(), update_last_artifact=AsyncMock())
    transport._get_or_create_persistence_manager = Mock(return_value=pm)
    transport._resolve_chat_context = AsyncMock(return_value=("app-1", "DesignDocs"))
    monkeypatch.setattr(transport, "_persist_ui_tool_state", UIToolsMixin._persist_ui_tool_state.__get__(transport))
    payload = {"review_id": "original-review", "checkpoints": ["Member may submit"]}

    async def receive(event, chat_id):
        pm.attach_tool_call_metadata.assert_awaited_once()
        assert chat_id == "chat-1" and event["tool_call_id"] == "original-event"

    transport.send_event_to_ui = receive
    await transport.send_tool_call_event(
        event_id="original-event", chat_id="chat-1", tool_name="review_policy",
        component_name="ApprovalCard", display_type="inline", payload=payload,
        agent_name="DesignDocsAgent", awaiting_response=True,
    )
    saved = pm.attach_tool_call_metadata.await_args.kwargs
    assert saved["chat_id"] == "chat-1" and saved["app_id"] == "app-1"
    assert saved["event_id"] == "original-event"
    assert saved["metadata"]["payload"] == payload
    assert saved["metadata"]["component_type"] == "ApprovalCard"
    assert saved["metadata"]["workflow_name"] == "DesignDocs"
    assert saved["metadata"]["agent_name"] == "DesignDocsAgent"
    assert saved["metadata"]["tool_call_status"] == "pending"
    assert saved["metadata"]["awaiting_response"] is True
    pm.update_last_artifact.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("early", [False, True])
async def test_response_state_is_saved_before_waiter_resumes_even_during_registration(early) -> None:
    transport = _FakeTransport()
    transport._ui_tool_metadata["review"] = {"chat_id": "chat", "tool_name": "review_policy", "display": "inline"}
    entered, release = asyncio.Event(), asyncio.Event()

    async def persist(**kwargs):
        assert kwargs["event_id"] == "review" and kwargs["status"] == "responded"
        assert kwargs["app_id"] == "app" and kwargs["chat_id"] == "chat"
        entered.set()
        await release.wait()

    pm = SimpleNamespace(update_tool_call_state=AsyncMock(side_effect=persist))
    transport._get_or_create_persistence_manager = Mock(return_value=pm)
    transport._resolve_chat_context = AsyncMock(return_value=("app", "DesignDocs"))
    future = asyncio.get_running_loop().create_future()
    if not early:
        transport.pending_tool_call_responses["review"] = future
    response = {"action": "approve", "review_id": "original"}
    task = asyncio.create_task(transport.submit_tool_call_response("review", response))
    await asyncio.wait_for(entered.wait(), timeout=2)
    if early:
        transport.pending_tool_call_responses["review"] = future
    assert not future.done()
    assert await transport.submit_tool_call_response("review", response) is True
    pm.update_tool_call_state.assert_awaited_once()
    release.set()
    assert await task is True
    assert future.result() == response
    assert not transport._buffered_tool_call_responses


@pytest.mark.asyncio
@pytest.mark.parametrize("waiter_registration", ["before", "during", "after"])
async def test_cancelled_submission_finishes_original_decision_before_releasing_waiter(waiter_registration) -> None:
    transport = _FakeTransport()
    _FakeTransport._instance = transport
    transport._ui_tool_metadata["review"] = {"chat_id": "chat", "tool_name": "review_policy", "display": "inline"}
    entered, release = asyncio.Event(), asyncio.Event()
    saved = []

    async def persist(**kwargs):
        entered.set()
        await release.wait()
        saved.append(kwargs)

    pm = SimpleNamespace(update_tool_call_state=AsyncMock(side_effect=persist))
    transport._get_or_create_persistence_manager = Mock(return_value=pm)
    transport._resolve_chat_context = AsyncMock(return_value=("app", "DesignDocs"))
    future = asyncio.get_running_loop().create_future()
    if waiter_registration == "before":
        transport.pending_tool_call_responses["review"] = future
    response = {"approved": True, "review_id": "original"}
    submission = asyncio.create_task(transport.submit_tool_call_response("review", response))
    await asyncio.wait_for(entered.wait(), timeout=2)
    submission.cancel()
    with pytest.raises(asyncio.CancelledError):
        await submission

    assert len(transport._tool_call_response_tasks) == 1
    finalization = next(iter(transport._tool_call_response_tasks))
    assert not finalization.done() and not future.done() and not saved
    assert await transport.submit_tool_call_response("review", {"approved": False}) is True
    pm.update_tool_call_state.assert_awaited_once()
    if waiter_registration == "during":
        transport.pending_tool_call_responses["review"] = future
    release.set()
    assert await asyncio.wait_for(finalization, timeout=2) is True
    assert saved[0]["status"] == "responded"
    if waiter_registration == "after":
        assert await _FakeTransport.wait_for_tool_call_response("review", timeout=0.1) == response
    else:
        assert future.result() == response
    assert not transport._tool_call_response_tasks
    assert not transport._buffered_tool_call_responses

