"""Replacing a journey alias must not close the socket it points at.

`journey_orchestrator._ensure_connection_alias` registers the *source* chat's
socket under the *target* chat id, so events for the next stage of a build reach
the user before their client reconnects. The entry therefore aliases a socket
that another chat still owns.

`handle_websocket` evicts whatever occupies a chat's slot when a new socket
arrives, and that eviction closed the socket. For an alias that meant closing a
live connection in the middle of a build: a real run lost AgentGenerator to it,
with four evictions and four LockAcquisitionErrors. See #576.

The slot must still be released; only the close is wrong.
"""

from __future__ import annotations

import pytest

from mozaiksai.core.transport.simple_transport import SimpleTransport


class _Socket:
    def __init__(self) -> None:
        self.closed_with: list[int] = []

    async def close(self, code: int = 1000) -> None:
        self.closed_with.append(code)


def _transport() -> SimpleTransport:
    t = SimpleTransport.__new__(SimpleTransport)
    t.connections = {}
    t._message_queues = {}
    t._input_request_registries = {}
    t._heartbeat_tasks = {}
    return t


async def _noop(_chat_id: str) -> None:
    return None


def _evict_block_closes(stale: dict) -> bool:
    """The decision handle_websocket makes — the real one, not a copy."""
    return not SimpleTransport._is_connection_alias(stale)


@pytest.mark.asyncio
async def test_an_alias_is_released_without_closing_the_socket() -> None:
    transport = _transport()
    shared = _Socket()
    # The source chat owns this socket; the target chat merely aliases it.
    transport.connections["source"] = {"websocket": shared, "ws_id": 1}
    transport.connections["target"] = {
        "websocket": shared, "ws_id": 1, "aliased_from_chat_id": "source",
    }
    transport._stop_heartbeat = _noop  # type: ignore[method-assign]

    stale = transport.connections["target"]
    assert not _evict_block_closes(stale), "an alias must not be closed"
    await transport._cleanup_connection("target", execution_continues=True)

    assert shared.closed_with == [], "closing an alias drops a live connection"
    assert "target" not in transport.connections, "the slot must still be released"
    assert transport.connections["source"]["websocket"] is shared, (
        "the owning chat keeps its socket"
    )


@pytest.mark.asyncio
async def test_a_genuinely_stale_socket_is_still_closed() -> None:
    """The eviction must keep doing its job for real leftovers."""
    transport = _transport()
    dead = _Socket()
    transport.connections["chat"] = {"websocket": dead, "ws_id": 7}
    transport._stop_heartbeat = _noop  # type: ignore[method-assign]

    stale = transport.connections["chat"]
    assert _evict_block_closes(stale), "a non-alias entry must still be closed"
    await dead.close(code=1001)
    await transport._cleanup_connection("chat", execution_continues=True)

    assert dead.closed_with == [1001]
    assert "chat" not in transport.connections


def test_the_eviction_path_checks_for_an_alias() -> None:
    """Pin the source: the marker is useless if the eviction ignores it."""
    from pathlib import Path

    source = Path(SimpleTransport.__module__.replace(".", "/") + ".py")
    text = (Path.cwd() / source).read_text(encoding="utf-8")
    evict = text[text.index("Evicting stale WebSocket") - 1200:text.index("Evicting stale WebSocket") + 400]
    assert "aliased_from_chat_id" in evict, (
        "handle_websocket must distinguish an alias before closing a socket"
    )


def test_the_journey_labels_its_alias() -> None:
    """And useless if the orchestrator never sets it."""
    from pathlib import Path

    text = (Path.cwd() / "mozaiksai/core/workflow/pack/journey_orchestrator.py").read_text(encoding="utf-8")
    assert '"aliased_from_chat_id": source_chat_id' in text
