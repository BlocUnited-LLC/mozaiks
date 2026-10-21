from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from mozaiksai.core.transport.handlers import workflow_handlers
from mozaiksai.core.transport.workflow_bridge import WorkflowBridgeMixin


class _FailingWebSocket:
    async def send_json(self, payload: dict) -> None:
        _ = payload
        raise RuntimeError("socket already closed")


class _MemoryCollection:
    def __init__(self):
        self.doc = {
            "_id": "target_chat", "app_id": "demo-app", "user_id": "demo-user",
            "workflow_name": "ValueEngine", "status": 0,
        }
        self.queries = []

    async def find_one(self, query: dict, projection: dict | None = None) -> dict:
        self.queries.append(query)
        return dict(self.doc) if all(self.doc.get(k) == v for k, v in query.items()) else None


class _PersistenceManager:
    def __init__(self):
        self.collection = _MemoryCollection()

    async def _coll(self) -> _MemoryCollection:
        return self.collection

    async def load_run_events(self, *, chat_id: str, app_id: str) -> list:
        _ = chat_id
        _ = app_id
        return []


class _Transport(WorkflowBridgeMixin):
    def __init__(self) -> None:
        self.connections = {
            "requested_chat": {
                "ws_id": "ws-1",
                "app_id": "demo-app",
                "user_id": "demo-user",
                "websocket": object(),
            }
        }
        self._background_tasks = {}
        self.background_runs: list[dict] = []
        self._input_request_registries = {}
        self.pm = _PersistenceManager()
        self.errors = []

    def get_live_ag2_workflow_run(self, chat_id):
        return None

    async def send_error(self, **kwargs):
        self.errors.append(kwargs)

    def _get_conn_meta(self, chat_id: str) -> dict:
        return self.connections.get(chat_id, {})

    def _get_or_create_persistence_manager(self) -> _PersistenceManager:
        return self.pm

    async def _run_workflow_background(self, **kwargs) -> None:
        self.background_runs.append(kwargs)


@pytest.mark.asyncio
@pytest.mark.parametrize("saved_state", ["fresh", "native", "pending_ui", "read_error"])
async def test_switch_workflow_stale_ack_does_not_block_userdriven_autostart(monkeypatch: pytest.MonkeyPatch, saved_state) -> None:
    transport = _Transport()
    from mozaiksai.core import session
    monkeypatch.setattr(session, "get_session_router_for_chat", AsyncMock())
    from mozaiksai.core.adapters import ag2_orchestration
    native_presence = AsyncMock(return_value=saved_state == "native")
    if saved_state == "read_error":
        native_presence.side_effect = RuntimeError("unavailable")
    if saved_state == "pending_ui":
        transport.pm.collection.doc["workflow_ui_state"] = {"pending_input_request": {"request_id": "saved"}}
    monkeypatch.setattr(ag2_orchestration, "get_ag2_adapter", lambda: SimpleNamespace(has_persisted_execution=native_presence))
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
    assert transport.pm.collection.queries[-1] == {
        "_id": "target_chat", "app_id": "demo-app", "user_id": "demo-user", "workflow_name": "ValueEngine",
    }
    if saved_state != "fresh":
        assert task is None
        assert transport.background_runs == []
        assert transport.errors[0]["error_code"] == "WORKFLOW_REOPEN_UNAVAILABLE"
        return
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
