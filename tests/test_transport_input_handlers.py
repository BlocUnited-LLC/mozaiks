from __future__ import annotations

import asyncio

import pytest

from mozaiksai.core.transport.handlers import input_handlers as input_handlers_module
from mozaiksai.core.transport.handlers.input_handlers import handle_user_input_submit


class _FakeWebSocket:
    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def send_json(self, payload: dict) -> None:
        self.sent.append(payload)


class _FakeTransport:
    def __init__(self, conn_meta: dict) -> None:
        self._conn_meta = dict(conn_meta)
        self.general_calls: list[dict] = []
        self.api_calls: list[dict] = []
        self.process_calls: list[dict] = []
        self.errors: list[tuple[str, str]] = []
        self._background_tasks: dict[str, asyncio.Task] = {}

    def _get_conn_meta(self, chat_id: str) -> dict:
        assert chat_id == "chat-1"
        return dict(self._conn_meta)

    async def _send_ws_error(self, websocket: _FakeWebSocket, message: str, code: str) -> None:
        self.errors.append((message, code))

    async def _handle_general_agent_exchange(self, **kwargs) -> None:
        self.general_calls.append(kwargs)

    async def handle_user_input_from_api(self, **kwargs) -> None:
        self.api_calls.append(kwargs)

    async def process_incoming_user_message(self, **kwargs) -> None:
        self.process_calls.append(kwargs)


@pytest.mark.asyncio
async def test_handle_user_input_submit_routes_general_mode_messages_to_general_exchange(monkeypatch) -> None:
    transport = _FakeTransport(
        {
            "ws_id": "ws-1",
            "workflow_name": "AgentGenerator",
            "app_id": "app-1",
            "user_id": "user-1",
        }
    )
    websocket = _FakeWebSocket()

    monkeypatch.setattr(
        input_handlers_module.session_registry,
        "is_in_general_mode",
        lambda ws_id: ws_id == "ws-1",
    )

    await handle_user_input_submit(
        transport,
        {"type": "user.input.submit", "text": "Hello from general mode."},
        "chat-1",
        websocket,
    )

    assert transport.general_calls == [
        {
            "chat_id": "chat-1",
            "ws_id": "ws-1",
            "user_message": "Hello from general mode.",
            "ui_context": {},
        }
    ]
    assert transport.api_calls == []
    assert transport.process_calls == []
    assert transport.errors == []
    assert websocket.sent == [
        {
            "schema_version": "mozaiks.ui.event.v1",
            "type": "chat.input_ack",
            "data": {"chat_id": "chat-1", "status": "accepted"},
            "timestamp": websocket.sent[0]["timestamp"],
        }
    ]


@pytest.mark.asyncio
async def test_handle_user_input_submit_routes_workflow_mode_messages_back_to_orchestration(monkeypatch) -> None:
    transport = _FakeTransport(
        {
            "ws_id": "ws-2",
            "workflow_name": "AgentGenerator",
            "app_id": "app-1",
            "user_id": "user-1",
        }
    )
    websocket = _FakeWebSocket()

    monkeypatch.setattr(
        input_handlers_module.session_registry,
        "is_in_general_mode",
        lambda ws_id: False,
    )
    monkeypatch.setattr(
        input_handlers_module.session_registry,
        "get_active_workflow",
        lambda ws_id: None,
    )

    await handle_user_input_submit(
        transport,
        {"type": "user.input.submit", "text": "Continue with the workflow."},
        "chat-1",
        websocket,
    )
    await transport._background_tasks["chat-1"]

    assert transport.api_calls == [
        {
            "chat_id": "chat-1",
            "user_id": "user-1",
            "workflow_name": "AgentGenerator",
            "message": "Continue with the workflow.",
            "app_id": "app-1",
        }
    ]
    assert transport.process_calls == []
    assert transport.errors == []
    assert websocket.sent == [
        {
            "schema_version": "mozaiks.ui.event.v1",
            "type": "chat.input_ack",
            "data": {"chat_id": "chat-1", "status": "accepted"},
            "timestamp": websocket.sent[0]["timestamp"],
        }
    ]


@pytest.mark.asyncio
async def test_workflow_input_releases_receiver_while_tool_response_is_pending(monkeypatch) -> None:
    transport = _FakeTransport({"workflow_name": "Smoke", "app_id": "app-1", "user_id": "user-1"})
    websocket = _FakeWebSocket()
    entered, approval = asyncio.Event(), asyncio.Event()

    async def resume(**kwargs):
        entered.set()
        await approval.wait()

    transport.handle_user_input_from_api = resume
    await asyncio.wait_for(handle_user_input_submit(
        transport, {"text": "Continue"}, "chat-1", websocket,
    ), timeout=1)
    task = transport._background_tasks["chat-1"]
    try:
        await asyncio.wait_for(entered.wait(), timeout=1)
        assert not task.done()
        assert websocket.sent[-1]["type"] == "chat.input_ack"
        await handle_user_input_submit(transport, {"text": "Duplicate"}, "chat-1", websocket)
        assert transport.errors[-1][1] == "CHAT_BUSY"
        assert transport._background_tasks["chat-1"] is task
        approval.set()
        await asyncio.wait_for(task, timeout=1)
        assert transport._background_tasks == {}
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_workflow_input_background_failure_is_reported_and_untracked() -> None:
    transport = _FakeTransport({"workflow_name": "Smoke", "app_id": "app-1"})
    websocket = _FakeWebSocket()

    async def resume(**kwargs):
        raise RuntimeError("controlled resume failure")

    transport.handle_user_input_from_api = resume
    await handle_user_input_submit(transport, {"text": "Continue"}, "chat-1", websocket)
    await transport._background_tasks["chat-1"]
    assert transport.errors[-1][1] == "USER_MESSAGE_FAILED"
    assert transport._background_tasks == {}

