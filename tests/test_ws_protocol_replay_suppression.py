from __future__ import annotations

import sys
import types
from unittest.mock import AsyncMock

import pytest

from mozaiksai.core.transport.ws_protocol import WebSocketProtocolMixin


class _Transport(WebSocketProtocolMixin):
    def __init__(self) -> None:
        self.connections = {
            "chat-1": {
                "workflow_name": "ExistingAppDiscovery",
            }
        }


class _FailingReplayer:
    def __init__(self) -> None:
        raise AssertionError("connect replay should have been suppressed")


@pytest.mark.asyncio
async def test_connect_replay_can_be_suppressed(monkeypatch: pytest.MonkeyPatch) -> None:
    transport = _Transport()

    fake_module = types.SimpleNamespace(WorkflowRunReplayer=_FailingReplayer)
    monkeypatch.setitem(sys.modules, "mozaiksai.core.transport.run_replay", fake_module)

    await transport._replay_run_on_connect_if_needed(
        "chat-1",
        object(),
        "demo-app",
        suppress_history_replay=True,
    )


@pytest.mark.asyncio
async def test_connect_replay_queues_versioned_envelopes(monkeypatch: pytest.MonkeyPatch) -> None:
    from mozaiksai.core.transport.event_contract import MozaiksEventEnvelope

    class Replayer:
        async def replay_on_connect_if_needed(self, *, send_event, **kwargs):
            for kind in ("text", "resume_boundary", "awaiting_reply"):
                await send_event({"kind": kind, "content": "Replay"}, "chat-1")

    transport = _Transport()
    transport._queue_message_with_backpressure = AsyncMock()
    transport._flush_message_queue = AsyncMock()
    monkeypatch.setitem(
        sys.modules, "mozaiksai.core.transport.run_replay",
        types.SimpleNamespace(WorkflowRunReplayer=Replayer),
    )
    await transport._replay_run_on_connect_if_needed("chat-1", object(), "demo-app")
    queued = transport._queue_message_with_backpressure.call_args_list
    assert len(queued) == 3
    assert [MozaiksEventEnvelope.model_validate(call.args[1]).type for call in queued] == [
        "chat.text", "chat.resume_boundary", "chat.awaiting_reply",
    ]
