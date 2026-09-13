"""HTTP and WebSocket responses must authorize the event before changing a waiter."""
from __future__ import annotations

import asyncio
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import httpx
import pytest
from fastapi import FastAPI

from mozaiksai.core.auth import UserPrincipal, WebSocketUser
from mozaiksai.core.auth.adapters import registry as auth_registry
from mozaiksai.core.transport.handlers import input_handlers
from mozaiksai.core.transport.ui_tools import UIToolsMixin


def _principal(user_id="owner", *, websocket=False, **claims):
    cls = WebSocketUser if websocket else UserPrincipal
    return cls(user_id=user_id, email=None, name=None, roles=[], scopes=["access_as_user"],
               raw_claims={}, provider="jwt", **claims)


class _Transport(UIToolsMixin):
    def __init__(self):
        self.pending_tool_call_responses = {}
        self._buffered_tool_call_responses = {}
        self._ui_tool_metadata = {}
        self._resolved_tool_call_ids = {}
        self._derived_context_managers = {}
        self._input_request_registries = {}
        self.connections = {}
        self._finalize_tool_call_response = AsyncMock()
        self.submit_user_input = AsyncMock(return_value=True)
        self._send_ws_error = AsyncMock()

    def _get_conn_meta(self, chat_id):
        return self.connections.get(chat_id, {})


@pytest.fixture
def harness(monkeypatch):
    from mozaiksai.hosts import runtime

    transport = _Transport()
    session = {"_id": "chat-owner", "app_id": "app-owner", "user_id": "owner"}
    lookup = AsyncMock(return_value=session)
    collection = SimpleNamespace(find_one=lookup)
    pm = SimpleNamespace(_coll=AsyncMock(return_value=collection))
    transport._get_or_create_persistence_manager = Mock(return_value=pm)
    monkeypatch.setattr(runtime, "simple_transport", transport)
    monkeypatch.setattr(runtime, "_chat_coll", AsyncMock(return_value=collection))
    monkeypatch.setattr(auth_registry, "is_auth_enabled", lambda: True)
    state = SimpleNamespace(transport=transport, session=session, lookup=lookup, runtime=runtime)
    state.principal = _principal()
    state.app = FastAPI()
    state.app.post("/api/tool-call/respond")(runtime.submit_tool_call_response)
    state.app.dependency_overrides[runtime.require_user_scope] = lambda: state.principal
    return state


def _pending(harness, kind="future"):
    transport = harness.transport
    if kind == "input":
        transport._input_request_registries["chat-owner"] = {"evt-owned": Mock()}
    else:
        transport._ui_tool_metadata["evt-owned"] = {
            "chat_id": "chat-owner", "tool_name": "ApprovalCard", "display": "artifact",
        }
    if kind == "future":
        transport.pending_tool_call_responses["evt-owned"] = asyncio.get_running_loop().create_future()


async def _http(harness, *, event_id="evt-owned", **extra):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=harness.app), base_url="http://test") as client:
        return await client.post("/api/tool-call/respond", json={
            "event_id": event_id, "response_data": {"action": "approve"}, **extra,
        })


def _unchanged(transport, metadata, registries):
    assert all(not future.done() for future in transport.pending_tool_call_responses.values())
    assert transport._ui_tool_metadata == metadata
    assert transport._input_request_registries == registries
    assert transport._buffered_tool_call_responses == {}
    assert transport._resolved_tool_call_ids == {}
    transport._finalize_tool_call_response.assert_not_awaited()
    transport.submit_user_input.assert_not_awaited()


@pytest.mark.parametrize("kind", ["future", "early", "input"])
@pytest.mark.parametrize("fault", ["foreign_user", "foreign_app", "foreign_chat", "missing_session", "missing_owner", "missing_app"])
async def test_http_denies_unowned_event_without_mutation(harness, kind, fault):
    _pending(harness, kind)
    if fault == "foreign_user":
        harness.principal = _principal("attacker")
    elif fault == "foreign_app":
        harness.principal = _principal(app_id="other-app")
    elif fault == "foreign_chat":
        harness.principal = _principal(chat_id="other-chat")
    elif fault == "missing_session":
        harness.lookup.return_value = None
    elif fault == "missing_owner":
        harness.session.pop("user_id")
    else:
        harness.session.pop("app_id")
    metadata = deepcopy(harness.transport._ui_tool_metadata)
    registries = {chat: dict(reg) for chat, reg in harness.transport._input_request_registries.items()}

    response = await _http(harness, chat_id="chat-owner", user_id="owner", app_id="app-owner")

    assert response.status_code == 404
    _unchanged(harness.transport, metadata, registries)


@pytest.mark.parametrize("kind", ["future", "early", "input"])
async def test_http_owner_can_submit_and_retry_without_reapplying(harness, kind):
    _pending(harness, kind)
    first = await _http(harness)
    second = await _http(harness)
    assert first.status_code == second.status_code == 200
    assert first.json() == {"status": "success"}
    if kind == "future":
        assert harness.transport.pending_tool_call_responses["evt-owned"].result() == {"action": "approve"}
    elif kind == "early":
        assert harness.transport._buffered_tool_call_responses == {"evt-owned": {"action": "approve"}}
    else:
        harness.transport.submit_user_input.assert_awaited_once()
    assert harness.transport._finalize_tool_call_response.await_count == (0 if kind == "input" else 1)

    harness.principal = _principal("attacker")
    assert (await _http(harness)).status_code == 404


@pytest.mark.parametrize("kind", ["unknown", "unbound_future", "ambiguous"])
async def test_http_rejects_missing_or_ambiguous_event_binding(harness, kind):
    if kind == "unbound_future":
        harness.transport.pending_tool_call_responses["evt-owned"] = asyncio.get_running_loop().create_future()
    elif kind == "ambiguous":
        _pending(harness)
        harness.transport._input_request_registries["other-chat"] = {"evt-owned": Mock()}
    response = await _http(harness)
    assert response.status_code == 404
    assert all(not future.done() for future in harness.transport.pending_tool_call_responses.values())
    harness.transport._finalize_tool_call_response.assert_not_awaited()


async def test_http_rechecks_event_binding_after_awaited_ownership_lookup(harness):
    _pending(harness)

    async def rebind(*args, **kwargs):
        harness.transport._ui_tool_metadata["evt-owned"]["chat_id"] = "other-chat"
        return harness.session

    harness.lookup.side_effect = rebind
    assert (await _http(harness)).status_code == 404
    assert not harness.transport.pending_tool_call_responses["evt-owned"].done()
    harness.transport._finalize_tool_call_response.assert_not_awaited()


async def test_http_lookup_failure_cannot_complete_or_buffer(harness):
    _pending(harness)
    harness.lookup.side_effect = RuntimeError("ownership unavailable")
    response = await _http(harness)
    assert response.status_code in {404, 503}
    assert not harness.transport.pending_tool_call_responses["evt-owned"].done()
    assert harness.transport._buffered_tool_call_responses == {}


@pytest.mark.parametrize(("user_id", "auth_enabled", "allowed"), [
    ("anonymous", False, True),
    ("anonymous", True, False),
    ("other-dev-user", False, False),
    ("owner", False, True),
])
async def test_http_anonymous_bypass_requires_explicit_local_no_auth(harness, monkeypatch, user_id, auth_enabled, allowed):
    _pending(harness)
    harness.principal = _principal(user_id)
    monkeypatch.setattr(auth_registry, "is_auth_enabled", lambda: auth_enabled)
    response = await _http(harness)
    assert response.status_code == (200 if allowed else 404)
    assert harness.transport.pending_tool_call_responses["evt-owned"].done() is allowed
    assert harness.transport._finalize_tool_call_response.await_count == int(allowed)


async def test_http_waits_for_owner_lookup_before_mutation(harness):
    _pending(harness)
    entered = asyncio.Event()
    release = asyncio.Event()
    metadata = deepcopy(harness.transport._ui_tool_metadata)

    async def lookup(*args, **kwargs):
        entered.set()
        await release.wait()
        return harness.session

    harness.lookup.side_effect = lookup
    request = asyncio.create_task(_http(harness))
    try:
        await asyncio.wait_for(entered.wait(), timeout=2)
        _unchanged(harness.transport, metadata, {})
    finally:
        release.set()
        response = await asyncio.wait_for(request, timeout=2)
    assert response.status_code == 200
    assert harness.transport.pending_tool_call_responses["evt-owned"].done()


def test_resolved_event_owner_bindings_share_the_bounded_retry_registry():
    transport = _Transport()
    for index in range(1025):
        event_id = f"evt-{index}"
        transport._ui_tool_metadata[event_id] = {"chat_id": "chat-owner", "tool_name": "ApprovalCard"}
        transport._mark_tool_call_response_resolved(event_id)
    assert len(transport._resolved_tool_call_ids) == len(transport._ui_tool_metadata) == 1024
    assert "evt-0" not in transport._ui_tool_metadata
    assert transport._ui_tool_metadata["evt-1024"] == {"chat_id": "chat-owner"}


@pytest.mark.parametrize("kind", ["future", "early", "input"])
@pytest.mark.parametrize("fault", ["foreign_user", "other_socket_chat", "missing_principal", "wrong_app", "stale_socket", "connection_user"])
async def test_websocket_cannot_answer_another_session(harness, monkeypatch, kind, fault):
    _pending(harness, kind)
    principal = _principal("attacker" if fault == "foreign_user" else "owner", websocket=True)
    websocket = SimpleNamespace(state=SimpleNamespace(user=principal), send_json=AsyncMock())
    chat_id = "other-chat" if fault == "other_socket_chat" else "chat-owner"
    harness.transport.connections[chat_id] = {
        "websocket": websocket, "user_id": principal.user_id, "app_id": "app-owner", "ws_id": "ws-1",
    }
    if fault == "missing_principal":
        websocket.state.user = None
    elif fault == "wrong_app":
        harness.transport.connections[chat_id]["app_id"] = "other-app"
    elif fault == "stale_socket":
        harness.transport.connections[chat_id]["websocket"] = object()
    elif fault == "connection_user":
        harness.transport.connections[chat_id]["user_id"] = "other-user"
    monkeypatch.setattr(input_handlers.session_registry, "get_active_workflow", lambda _: None)
    metadata = deepcopy(harness.transport._ui_tool_metadata)
    registries = {chat: dict(reg) for chat, reg in harness.transport._input_request_registries.items()}

    await input_handlers.handle_tool_call_response(harness.transport, {
        "tool_call_id": "evt-owned", "response": {"action": "approve"},
        "chat_id": "chat-owner", "user_id": "owner", "app_id": "app-owner",
    }, chat_id, websocket)

    assert not any(call.args[0].get("data", {}).get("status") == "accepted"
                   for call in websocket.send_json.await_args_list)
    _unchanged(harness.transport, metadata, registries)


async def test_websocket_anonymous_local_identity_still_requires_session_ownership(harness, monkeypatch):
    _pending(harness)
    principal = _principal("anonymous", websocket=True)
    websocket = SimpleNamespace(state=SimpleNamespace(user=principal), send_json=AsyncMock())
    harness.transport.connections["chat-owner"] = {
        "websocket": websocket, "user_id": "anonymous", "app_id": "app-owner", "ws_id": "ws-1",
    }
    monkeypatch.setattr(auth_registry, "is_auth_enabled", lambda: False)
    monkeypatch.setattr(input_handlers.session_registry, "get_active_workflow", lambda _: None)
    metadata = deepcopy(harness.transport._ui_tool_metadata)
    await input_handlers.handle_tool_call_response(harness.transport, {
        "tool_call_id": "evt-owned", "response": {"action": "approve"},
    }, "chat-owner", websocket)
    assert websocket.send_json.await_args.args[0]["data"]["status"] == "rejected"
    _unchanged(harness.transport, metadata, {})


@pytest.mark.parametrize("switched", [False, True])
async def test_websocket_owner_can_answer_server_selected_active_chat(harness, monkeypatch, switched):
    _pending(harness)
    websocket = SimpleNamespace(state=SimpleNamespace(user=_principal(websocket=True)), send_json=AsyncMock())
    chat_id = "carrier-chat" if switched else "chat-owner"
    harness.transport.connections[chat_id] = {
        "websocket": websocket, "user_id": "owner", "app_id": "app-owner", "ws_id": "ws-1",
    }
    active = SimpleNamespace(chat_id="chat-owner", app_id="app-owner", user_id="owner") if switched else None
    monkeypatch.setattr(input_handlers.session_registry, "get_active_workflow", lambda _: active)

    await input_handlers.handle_tool_call_response(harness.transport, {
        "tool_call_id": "evt-owned", "response": {"action": "approve"},
    }, chat_id, websocket)

    assert harness.transport.pending_tool_call_responses["evt-owned"].result() == {"action": "approve"}
    assert websocket.send_json.await_args.args[0]["data"]["status"] == "accepted"
