from __future__ import annotations

import asyncio
import logging
import queue
import threading

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
