from __future__ import annotations

import pytest

from mozaiksai.core.transport.simple_transport import SimpleTransport


class _Socket:
    def __init__(self) -> None:
        self.closed_with: list[int] = []

    async def close(self, code: int = 1000) -> None:
        self.closed_with.append(code)


def _transport() -> SimpleTransport:
    transport = SimpleTransport.__new__(SimpleTransport)
    transport.connections = {}
    transport._message_queues = {}
    transport._input_request_registries = {}
    transport._heartbeat_tasks = {}
    return transport


async def _noop(_chat_id: str) -> None:
    return None


@pytest.mark.asyncio
async def test_alias_cleanup_releases_slot_without_closing_owner_socket() -> None:
    transport = _transport()
    socket = _Socket()
    transport.connections["source"] = {"websocket": socket, "ws_id": 1}
    transport.connections["target"] = {
        "websocket": socket,
        "ws_id": 1,
        "aliased_from_chat_id": "source",
    }
    transport._stop_heartbeat = _noop  # type: ignore[method-assign]

    await transport._cleanup_connection("target", execution_continues=True)

    assert socket.closed_with == []
    assert "target" not in transport.connections
    assert transport.connections["source"]["websocket"] is socket


@pytest.mark.asyncio
async def test_normal_cleanup_still_closes_socket() -> None:
    transport = _transport()
    socket = _Socket()
    transport.connections["chat"] = {"websocket": socket, "ws_id": 1}
    transport._stop_heartbeat = _noop  # type: ignore[method-assign]

    await socket.close(code=1001)
    await transport._cleanup_connection("chat", execution_continues=True)

    assert socket.closed_with == [1001]
    assert "chat" not in transport.connections
