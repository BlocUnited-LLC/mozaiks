from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from starlette.websockets import WebSocketState

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

    async def _send_ws_error(self, websocket: _FakeWebSocket, message: str, code: str, **_metadata) -> None:
        self.errors.append((message, code))

    async def _handle_general_agent_exchange(self, **kwargs) -> None:
        self.general_calls.append(kwargs)

    async def handle_user_input_from_api(self, **kwargs) -> None:
        await kwargs.pop("on_input_admitted")()
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
@pytest.mark.parametrize("message_id,valid", [("sent-123", True), ("x" * 129, False), ([], False), (" ", False)])
async def test_workflow_input_releases_receiver_while_tool_response_is_pending(message_id, valid) -> None:
    from mozaiksai.core.transport.simple_transport import SimpleTransport

    transport = _FakeTransport({"workflow_name": "Smoke", "app_id": "app-1", "user_id": "user-1"})
    transport._send_ws_error = SimpleTransport._send_ws_error.__get__(transport)
    websocket = _FakeWebSocket()
    websocket.application_state = websocket.client_state = WebSocketState.CONNECTED
    entered, approval = asyncio.Event(), asyncio.Event()

    async def resume(**kwargs):
        await kwargs["on_input_admitted"]()
        entered.set()
        await approval.wait()

    transport.handle_user_input_from_api = resume
    context = {"client_message_id": message_id}
    await asyncio.wait_for(handle_user_input_submit(
        transport, {"text": "Continue", "context": context}, "chat-1", websocket,
    ), timeout=1)
    task = transport._background_tasks["chat-1"]
    try:
        await asyncio.wait_for(entered.wait(), timeout=1)
        assert not task.done()
        assert websocket.sent[-1]["type"] == "chat.input_ack"
        assert websocket.sent[-1]["data"].get("client_message_id") == (message_id if valid else None)
        await handle_user_input_submit(transport, {
            "text": "Duplicate", "chat_id": "wrong-chat", "context": context,
        }, "chat-1", websocket)
        err = websocket.sent[-1]
        assert err["type"] == "chat.error" and err["data"]["error_code"] == "CHAT_BUSY"
        assert err["data"]["chat_id"] == "chat-1"
        assert err["data"].get("client_message_id") == (message_id if valid else None)
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
    assert not websocket.sent
    assert transport._background_tasks == {}


def _workflow_socket(monkeypatch, app_id, chat_id):
    from mozaiksai.core.transport.simple_transport import SimpleTransport
    from tests.test_chat_execution_lease import _FakePersistenceManager

    transport, websocket, pm = SimpleTransport(), _FakeWebSocket(), _FakePersistenceManager()
    websocket.application_state = websocket.client_state = WebSocketState.CONNECTED
    transport.connections[chat_id] = {"app_id": app_id, "user_id": "u1", "workflow_name": "AppGenerator"}
    monkeypatch.setattr(transport, "_get_or_create_persistence_manager", lambda: pm)
    monkeypatch.setattr(transport, "_apply_user_text_context_updates", AsyncMock(return_value={}))
    monkeypatch.setattr(transport, "process_incoming_user_message", AsyncMock())
    monkeypatch.setattr(transport, "send_error", AsyncMock())
    return transport, websocket, pm


async def _assert_contended_socket_admission(monkeypatch, app_id, chat_id):
    from tests.test_chat_execution_lease import _ag2_mod, _bridge_mod

    monkeypatch.setenv("DISTRIBUTED_LOCK_MAX_RETRIES", "0")
    monkeypatch.setattr(_bridge_mod, "get_workflow_lifecycle_hooks", lambda _: {})
    owner, owner_socket, owner_pm = _workflow_socket(monkeypatch, app_id, chat_id)
    other, other_socket, other_pm = _workflow_socket(monkeypatch, app_id, chat_id)
    entered, release = asyncio.Event(), asyncio.Event()

    async def provider(request):
        entered.set()
        await release.wait()
        return SimpleNamespace(status=SimpleNamespace(value="completed"))

    run = AsyncMock(side_effect=provider)
    monkeypatch.setattr(_ag2_mod, "get_ag2_adapter", lambda: SimpleNamespace(run=run))

    await handle_user_input_submit(owner, {
        "text": "First turn", "context": {"client_message_id": "first"},
    }, chat_id, owner_socket)
    owner_task = owner._background_tasks[chat_id]
    try:
        assert owner_socket.sent == []  # Scheduling the task is not admission.
        await asyncio.wait_for(entered.wait(), 10)
        assert [(f["type"], f["data"]) for f in owner_socket.sent] == [
            ("chat.input_ack", {"chat_id": chat_id, "client_message_id": "first", "status": "accepted"}),
        ]
        await handle_user_input_submit(other, {
            "chat_id": "wrong", "text": "Competing turn", "context": {"client_message_id": "second"},
        }, chat_id, other_socket)
        await asyncio.wait_for(other._background_tasks[chat_id], 10)
        assert len(other_socket.sent) == 1
        refusal = other_socket.sent[0]
        assert refusal["type"] == "chat.error"
        assert refusal["data"]["error_code"] == "CHAT_LOCK_BUSY"
        assert refusal["data"]["chat_id"] == chat_id
        assert refusal["data"]["client_message_id"] == "second"
        other.send_error.assert_not_awaited()  # The owner and observers receive no duplicate notice.
        other.process_incoming_user_message.assert_not_awaited()
        assert other_pm.pending_lookups == other_pm.pending_clears == other_pm.run_user_messages == []
        assert [m["content"] for m in owner_pm.run_user_messages] == ["First turn"]
        run.assert_awaited_once()
    finally:
        release.set()
        await asyncio.wait_for(owner_task, 10)
    assert len(owner_socket.sent) == 1


@pytest.mark.asyncio
async def test_two_socket_transports_acknowledge_only_admitted_turn(monkeypatch):
    from mozaiksai.core.runtime.persistence import distributed_lock as dl

    monkeypatch.setenv(dl.CHAT_LOCK_MODE_ENV, "local")
    await _assert_contended_socket_admission(monkeypatch, "app-socket", "chat-socket")


@pytest.mark.asyncio
async def test_socket_model_refusal_returns_only_to_sender_before_ack(monkeypatch):
    from mozaiksai.core.workflow import llm_config
    from tests.test_chat_execution_lease import _bridge_mod

    chat_id = "model-socket"
    transport, websocket, pm = _workflow_socket(monkeypatch, "app-model", chat_id)
    monkeypatch.setattr(_bridge_mod, "get_workflow_lifecycle_hooks", lambda _: {})
    pm.assert_chat_resumable = AsyncMock(return_value=SimpleNamespace(selected="openai:removed", rebind_pending=False))
    monkeypatch.setattr(llm_config, "resolve_workflow_model", AsyncMock(return_value=None))

    await handle_user_input_submit(transport, {
        "text": "Keep this draft", "context": {"client_message_id": "model-send"},
    }, chat_id, websocket)
    await transport._background_tasks[chat_id]
    assert len(websocket.sent) == 1
    refusal = websocket.sent[0]
    assert refusal["type"] == "chat.error"
    assert refusal["data"]["error_code"] == "WORKFLOW_MODEL_UNAVAILABLE"
    assert refusal["data"]["client_message_id"] == "model-send"
    assert refusal["data"]["chat_id"] == chat_id
    transport.send_error.assert_not_awaited()
    assert pm.run_user_messages == []


@pytest.mark.asyncio
async def test_socket_authority_outage_refuses_before_ack(monkeypatch):
    from mozaiksai.core.runtime.persistence import distributed_lock as dl
    from tests.test_chat_execution_lease import _bridge_mod

    chat_id = "unavailable-socket"
    transport, websocket, pm = _workflow_socket(monkeypatch, "app-1", chat_id)
    monkeypatch.setattr(_bridge_mod, "get_workflow_lifecycle_hooks", lambda _: {})
    monkeypatch.setenv(dl.CHAT_LOCK_MODE_ENV, "required")
    monkeypatch.setattr(dl, "_get_lock_collection", lambda: None)
    await handle_user_input_submit(transport, {
        "text": "Keep this draft", "context": {"client_message_id": "outage"},
    }, chat_id, websocket)
    await transport._background_tasks[chat_id]
    assert [(f["type"], f["data"].get("error_code"), f["data"].get("client_message_id"), f["data"]["chat_id"])
            for f in websocket.sent] == [("chat.error", "CHAT_LOCK_UNAVAILABLE", "outage", chat_id)]
    assert pm.run_user_messages == []
    transport.send_error.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["LockAcquisitionError", "ChatLockAuthorityUnavailableError", "ChatLeaseLostError", "TokenUsageDenied"])
async def test_provider_failure_after_admission_never_marks_input_resendable(monkeypatch, failure):
    from mozaiksai.core.runtime.persistence import distributed_lock as dl
    from mozaiksai.core.session import router
    from tests.test_chat_execution_lease import _ag2_mod, _bridge_mod

    app_id, chat_id = "app-1", "provider-socket"
    transport, websocket, pm = _workflow_socket(monkeypatch, app_id, chat_id)
    monkeypatch.setenv(dl.CHAT_LOCK_MODE_ENV, "local")
    monkeypatch.setattr(_bridge_mod, "get_workflow_lifecycle_hooks", lambda _: {})
    monkeypatch.setattr(router, "get_session_router_for_chat",
                        AsyncMock(return_value=SimpleNamespace(fail_active_revision=AsyncMock())))
    if failure == "TokenUsageDenied":
        from mozaiksai.core.tokens.guard import TokenUsageDecision, TokenUsageDenied

        exc = TokenUsageDenied(TokenUsageDecision(allowed=False, reason="insufficient_balance", error_code="INSUFFICIENT_TOKENS"))
    else:
        exc = getattr(dl, failure)(dl.chat_lock_resource(app_id, chat_id))
    run = AsyncMock(side_effect=exc)
    monkeypatch.setattr(_ag2_mod, "get_ag2_adapter", lambda: SimpleNamespace(run=run))
    await handle_user_input_submit(transport, {
        "text": "Already admitted", "context": {"client_message_id": "admitted"},
    }, chat_id, websocket)
    await transport._background_tasks[chat_id]
    ack, terminal = websocket.sent
    assert (ack["type"], ack["data"]["status"], ack["data"]["client_message_id"]) == ("chat.input_ack", "accepted", "admitted")
    assert terminal["type"] == "chat.error"
    assert terminal["data"]["chat_id"] == chat_id and terminal["data"]["client_message_id"] == "admitted"
    assert terminal["data"]["delivery_state"] == "accepted"
    assert [m["content"] for m in pm.run_user_messages] == ["Already admitted"]
    transport.send_error.assert_awaited_once()
    expected = {"ChatLeaseLostError": "CHAT_LOCK_LOST", "TokenUsageDenied": "INSUFFICIENT_TOKENS"}.get(failure, "WORKFLOW_EXECUTION_FAILED")
    assert transport.send_error.await_args.kwargs["error_code"] == terminal["data"]["error_code"] == expected
    assert terminal["data"]["message"] == transport.send_error.await_args.kwargs["error_message"]


@pytest.mark.asyncio
@pytest.mark.parametrize("callback_attempted", [False, True])
async def test_failure_before_admission_distinguishes_an_undelivered_turn_from_a_partial_callback(monkeypatch, callback_attempted):
    from mozaiksai.core.tokens.guard import TokenUsageDecision, TokenUsageDenied
    from tests.test_chat_execution_lease import _bridge_mod

    chat_id = "admission-error"
    transport, websocket, pm = _workflow_socket(monkeypatch, "app-1", chat_id)
    denial = TokenUsageDenied(TokenUsageDecision(allowed=False, reason="insufficient_balance", error_code="INSUFFICIENT_TOKENS"))
    if callback_attempted:
        monkeypatch.setattr(_bridge_mod, "get_workflow_lifecycle_hooks", lambda _: {})
        transport._input_request_registries[chat_id] = {"request": AsyncMock()}
        monkeypatch.setattr(transport, "submit_user_input", AsyncMock(side_effect=denial))
    else:
        monkeypatch.setattr(_bridge_mod, "get_workflow_lifecycle_hooks", Mock(side_effect=denial))
    await handle_user_input_submit(transport, {
        "text": "My question", "context": {"client_message_id": "first"},
    }, chat_id, websocket)
    await transport._background_tasks[chat_id]
    receipt, = websocket.sent
    assert receipt["type"] == "chat.error"
    assert receipt["data"]["client_message_id"] == "first" and receipt["data"]["chat_id"] == chat_id
    assert receipt["data"]["delivery_state"] == ("unknown" if callback_attempted else "refused")
    assert receipt["data"]["error_code"] == "INSUFFICIENT_TOKENS"
    assert pm.run_user_messages == []


@pytest.mark.asyncio
async def test_pending_callback_freeform_emits_one_correlated_ack(monkeypatch):
    from tests.test_chat_execution_lease import _bridge_mod

    app_id, chat_id = "app-1", "callback-socket"
    transport, websocket, _ = _workflow_socket(monkeypatch, app_id, chat_id)
    recipient = AsyncMock()
    transport._input_request_registries[chat_id] = {"request": recipient}
    monkeypatch.setattr(_bridge_mod, "get_workflow_lifecycle_hooks", lambda _: {})
    monkeypatch.setattr(transport, "_resolve_chat_context", AsyncMock(return_value=(app_id, "AppGenerator")))
    monkeypatch.setattr(transport, "send_event_to_ui", AsyncMock())
    await handle_user_input_submit(transport, {
        "text": "My callback reply", "context": {"client_message_id": "callback"},
    }, chat_id, websocket)
    await transport._background_tasks[chat_id]
    recipient.assert_awaited_once_with("My callback reply")
    transport.send_event_to_ui.assert_not_awaited()
    assert [(f["type"], f["data"]) for f in websocket.sent] == [
        ("chat.input_ack", {"chat_id": chat_id, "client_message_id": "callback", "status": "accepted"}),
    ]

