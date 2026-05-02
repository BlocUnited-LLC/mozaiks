from __future__ import annotations

import asyncio
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
        self._derived_context_managers = {}
        self._input_request_registries = {}
        self.connections = {}

    @classmethod
    async def get_instance(cls):
        return cls._instance


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
