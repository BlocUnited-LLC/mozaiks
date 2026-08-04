from __future__ import annotations

import sys
import types

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
