from __future__ import annotations

from types import SimpleNamespace

import pytest

from mozaiksai.core.transport.handlers import workflow_handlers


class _FailingWebSocket:
    async def send_json(self, payload: dict) -> None:
        _ = payload
        raise RuntimeError("socket already closed")


class _MemoryCollection:
    async def find_one(self, query: dict, projection: dict | None = None) -> dict:
        _ = query
        _ = projection
        return {"status": 0}


class _PersistenceManager:
    async def _coll(self) -> _MemoryCollection:
        return _MemoryCollection()

    async def load_run_history(self, *, chat_id: str, app_id: str) -> list:
        _ = chat_id
        _ = app_id
        return []


class _Transport:
    def __init__(self) -> None:
        self.connections = {
            "requested_chat": {
                "ws_id": "ws-1",
                "websocket": object(),
            }
        }
        self._background_tasks = {}
        self.background_runs: list[dict] = []

    def _get_conn_meta(self, chat_id: str) -> dict:
        return self.connections.get(chat_id, {})

    def _get_or_create_persistence_manager(self) -> _PersistenceManager:
        return _PersistenceManager()

    async def _run_workflow_background(self, **kwargs) -> None:
        self.background_runs.append(kwargs)


@pytest.mark.asyncio
async def test_switch_workflow_stale_ack_does_not_block_userdriven_autostart(monkeypatch: pytest.MonkeyPatch) -> None:
    transport = _Transport()
    active_context = SimpleNamespace(
        workflow_name="ValueEngine",
        artifact_id=None,
        app_id="demo-app",
        user_id="demo-user",
    )

    monkeypatch.setattr(
        workflow_handlers.session_registry,
        "switch_workflow",
        lambda ws_id, chat_id: active_context,
    )

    from mozaiksai.core.workflow.workflow_manager import workflow_manager

    monkeypatch.setattr(workflow_manager, "reload_workflow", lambda workflow_name: None)
    monkeypatch.setattr(
        workflow_manager,
        "get_config",
        lambda workflow_name: {
            "workflow_startup_mode": "UserDriven",
        },
    )

    await workflow_handlers.handle_switch_workflow(
        transport,
        {
            "chat_id": "target_chat",
            "replay_on_switch": False,
        },
        "requested_chat",
        _FailingWebSocket(),
    )

    task = transport._background_tasks.get("target_chat")
    assert task is not None
    await task
    assert transport.background_runs == [
        {
            "chat_id": "target_chat",
            "workflow_name": "ValueEngine",
            "app_id": "demo-app",
            "user_id": "demo-user",
            "ws_id": "ws-1",
            "initial_message": None,
        }
    ]
