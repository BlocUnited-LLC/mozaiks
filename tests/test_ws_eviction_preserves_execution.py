"""A second socket for a chat must not disarm the workflow running on it.

When a client reconnects, double-navigates, or races a sequence transition, a
second WebSocket arrives for the same chat_id. ``handle_websocket`` evicts the
stale connection so the old receive loop's ``finally`` cannot delete the new
slot — that part is correct and deliberate.

The eviction calls ``_cleanup_connection`` without a ``ws_id``, which the
docstring notes bypasses the takeover guard on purpose. But that cleanup also
drops ``_input_request_registries[chat_id]`` — the callbacks a *running*
workflow registered so a user's reply can reach it. Those belong to the
execution, not to the socket: the registry is keyed by chat_id, and the new
socket serves the very same run.

Observed live on 468c9c08: AgentGenerator died 11s into a real build right after
"Evicting stale WebSocket for chat_id=2b097215…". See issue #576.
"""

from __future__ import annotations

import asyncio

import pytest

from mozaiksai.core.transport.simple_transport import SimpleTransport


class _ClosedWebSocket:
    """A socket the client has already gone away from."""

    def __init__(self) -> None:
        self.close_calls: list[int] = []

    async def close(self, code: int = 1000) -> None:
        self.close_calls.append(code)

    async def send_json(self, payload: dict) -> None:  # pragma: no cover - must not be reached
        raise RuntimeError("Unexpected ASGI message 'websocket.send', after sending 'websocket.close'")


def _transport() -> SimpleTransport:
    return SimpleTransport.__new__(SimpleTransport)


def _seed(transport: SimpleTransport, chat_id: str, ws: object, ws_id: int) -> None:
    transport.connections = {chat_id: {"websocket": ws, "ws_id": ws_id, "active": True}}
    transport._message_queues = {chat_id: []}
    transport._input_request_registries = {}
    transport._heartbeat_tasks = {}


async def _stop_heartbeat(_chat_id: str) -> None:
    return None


@pytest.mark.asyncio
async def test_eviction_keeps_the_running_workflow_able_to_receive_input() -> None:
    transport = _transport()
    chat_id = "chat-evict"
    _seed(transport, chat_id, _ClosedWebSocket(), ws_id=111)
    transport._stop_heartbeat = _stop_heartbeat  # type: ignore[method-assign]

    delivered: list[str] = []

    async def respond(answer: str) -> None:
        delivered.append(answer)

    # A workflow is mid-run and waiting on the user.
    transport.register_input_request(chat_id, "req-1", respond)
    assert transport._input_request_registries[chat_id]["req-1"] is respond

    # A second socket arrives for the same chat; the stale one is evicted.
    await transport._cleanup_connection(chat_id, execution_continues=True)

    registry = transport._input_request_registries.get(chat_id) or {}
    assert "req-1" in registry, (
        "evicting a stale socket disarmed the running workflow: the user's reply "
        "can no longer reach it"
    )
    await registry["req-1"]("the user's answer")
    assert delivered == ["the user's answer"]


@pytest.mark.asyncio
async def test_eviction_still_releases_socket_scoped_resources() -> None:
    """Preserving the run must not turn the eviction into a leak."""
    transport = _transport()
    chat_id = "chat-evict-2"
    _seed(transport, chat_id, _ClosedWebSocket(), ws_id=222)
    transport._message_queues[chat_id] = [{"type": "chat.message", "data": {}}]
    stopped: list[str] = []

    async def _stop(cid: str) -> None:
        stopped.append(cid)

    transport._stop_heartbeat = _stop  # type: ignore[method-assign]
    transport.register_input_request(chat_id, "req-2", lambda _v: None)

    await transport._cleanup_connection(chat_id, execution_continues=True)

    assert chat_id not in transport.connections, "the dead socket must be released"
    assert chat_id not in transport._message_queues, "its queued messages must not be replayed"
    assert stopped == [chat_id], "its heartbeat must stop"
    assert "req-2" in (transport._input_request_registries.get(chat_id) or {})


@pytest.mark.asyncio
async def test_a_real_disconnect_still_clears_the_registry() -> None:
    """Only takeover preserves the run.

    When the client genuinely leaves — the disconnect path, which passes its own
    ws_id and still owns the slot — pending callbacks must be released, or every
    abandoned chat leaks one.
    """
    transport = _transport()
    chat_id = "chat-gone"
    _seed(transport, chat_id, _ClosedWebSocket(), ws_id=333)
    transport._stop_heartbeat = _stop_heartbeat  # type: ignore[method-assign]
    transport.register_input_request(chat_id, "req-3", lambda _v: None)

    await transport._cleanup_connection(chat_id, ws_id=333, execution_continues=False)

    assert not (transport._input_request_registries.get(chat_id) or {}), (
        "a real disconnect must not leak pending input callbacks"
    )


@pytest.mark.asyncio
async def test_guarded_cleanup_after_takeover_leaves_the_new_owner_alone() -> None:
    """The existing ws_id guard must keep working."""
    transport = _transport()
    chat_id = "chat-raced"
    _seed(transport, chat_id, _ClosedWebSocket(), ws_id=999)
    transport._stop_heartbeat = _stop_heartbeat  # type: ignore[method-assign]
    transport.register_input_request(chat_id, "req-4", lambda _v: None)

    # The losing socket's finally-block cleanup arrives after a new owner took
    # the slot; it must not touch anything.
    await transport._cleanup_connection(chat_id, ws_id=444, execution_continues=False)

    assert chat_id in transport.connections
    assert "req-4" in transport._input_request_registries[chat_id]


def test_asyncio_marker_available() -> None:
    assert asyncio.iscoroutinefunction(SimpleTransport._cleanup_connection)
