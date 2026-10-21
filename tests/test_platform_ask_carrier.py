"""Platform Ask sockets remain usable when the app declares no workflows."""
from __future__ import annotations

import base64
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from starlette.websockets import WebSocket

from mozaiksai.core.auth import websocket_auth
from mozaiksai.core.auth.adapters.base import UserClaims


def _socket(*, authenticated: bool = True, ask: bool = True):
    protocols = []
    if authenticated:
        encoded = base64.urlsafe_b64encode(b"fixture-token").decode().rstrip("=")
        protocols = [websocket_auth.WS_BEARER_SUBPROTOCOL, encoded]
    send = AsyncMock()
    websocket = WebSocket(
        {
            "type": "websocket",
            "path": "/ws/ask/app-1/chat-1/user-1",
            "query_string": b"transport_purpose=ask_carrier" if ask else b"",
            "headers": [(b"sec-websocket-protocol", ", ".join(protocols).encode())] if protocols else [],
            "subprotocols": protocols,
        },
        receive=AsyncMock(return_value={"type": "websocket.connect"}),
        send=send,
    )
    return websocket, send


@pytest.fixture
def harness(monkeypatch):
    from mozaiksai.core import session
    from mozaiksai.core.transport.session_registry import session_registry
    from mozaiksai.hosts import platform

    claims = UserClaims(user_id="user-1", app_id="app-1", chat_id="chat-1", scopes=["access_as_user"])
    adapter = SimpleNamespace(name="fixture", validate_token=AsyncMock(return_value=claims))
    monkeypatch.setattr(websocket_auth, "is_auth_enabled", lambda: True)
    monkeypatch.setattr(websocket_auth, "get_auth_adapter", lambda: adapter)
    monkeypatch.delenv("MOZAIKS_WS_ALLOW_QUERY_TOKEN", raising=False)
    collection = SimpleNamespace(find_one=AsyncMock(return_value=None), update_one=AsyncMock())
    chat_coll = AsyncMock(return_value=collection)
    create_chat = AsyncMock()

    async def accept_carrier(**kwargs):
        await websocket_auth.accept_websocket(kwargs["websocket"])

    transport = SimpleNamespace(
        handle_websocket=AsyncMock(side_effect=accept_carrier),
        handle_user_input_from_api=AsyncMock(),
    )
    ordered = Mock(return_value=[])
    prereqs = AsyncMock(side_effect=AssertionError("Ask must not check workflow prerequisites"))
    router = AsyncMock(side_effect=AssertionError("Ask must not resolve a workflow session"))
    routed_chat = AsyncMock(side_effect=AssertionError("Ask must not create a workflow session"))
    add_workflow = Mock()
    monkeypatch.setattr(platform.runtime_app, "_chat_coll", chat_coll)
    monkeypatch.setattr(platform.runtime_app, "simple_transport", transport)
    monkeypatch.setattr(platform.persistence_manager, "create_chat_session", create_chat)
    monkeypatch.setattr(platform, "get_ordered_workflow_names", ordered)
    monkeypatch.setattr(platform.get_platform_hooks(), "call_chat_prereqs", prereqs)
    monkeypatch.setattr(session, "get_session_router_for_chat", router)
    monkeypatch.setattr(session, "create_routed_chat_session", routed_chat)
    monkeypatch.setattr(session_registry, "add_workflow", add_workflow)
    monkeypatch.setattr(session_registry, "remove_session", Mock())
    return SimpleNamespace(
        host=platform, claims=claims, adapter=adapter, collection=collection,
        chat_coll=chat_coll, create_chat=create_chat, transport=transport, ordered=ordered,
        prereqs=prereqs, router=router, routed_chat=routed_chat, add_workflow=add_workflow,
    )


async def _connect(harness, websocket, *, workflow="ask"):
    await harness.host.websocket_endpoint(
        websocket, workflow_name=workflow, app_id="app-1", chat_id="chat-1", user_id="user-1",
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("workflows", [[], ["Builder"]])
async def test_ask_carrier_accepts_without_resolving_or_launching_workflows(harness, workflows):
    harness.ordered.return_value = workflows
    websocket, send = _socket()

    await _connect(harness, websocket)

    assert send.await_args.args[0] == {
        "type": "websocket.accept", "subprotocol": websocket_auth.WS_BEARER_SUBPROTOCOL, "headers": [],
    }
    harness.adapter.validate_token.assert_awaited_once_with("fixture-token")
    harness.create_chat.assert_awaited_once_with(
        "chat-1", "app-1", workflow_name="", user_id="user-1",
        extra_fields={"transport_purpose": "ask_carrier"},
    )
    handoff = harness.transport.handle_websocket.await_args.kwargs
    assert handoff["chat_id"] == "chat-1"
    assert handoff["app_id"] == "app-1"
    assert handoff["user_id"] == "user-1"
    assert handoff["workflow_name"] == ""
    assert handoff["suppress_history_replay"] is True
    harness.ordered.assert_not_called()
    harness.prereqs.assert_not_awaited()
    harness.router.assert_not_awaited()
    harness.routed_chat.assert_not_awaited()
    harness.add_workflow.assert_not_called()
    harness.transport.handle_user_input_from_api.assert_not_awaited()


@pytest.mark.asyncio
async def test_ask_carrier_rejects_missing_auth_before_session_access(harness):
    websocket, send = _socket(authenticated=False)

    await _connect(harness, websocket)

    assert send.await_args.args[0] == {
        "type": "websocket.close", "code": 1008, "reason": "Missing access_token",
    }
    harness.adapter.validate_token.assert_not_awaited()
    harness.chat_coll.assert_not_awaited()
    harness.ordered.assert_not_called()
    harness.transport.handle_websocket.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("claim", ["user_id", "app_id", "chat_id"])
async def test_ask_carrier_preserves_authenticated_path_binding(harness, claim):
    setattr(harness.claims, claim, "another-identity")
    websocket, send = _socket()

    await _connect(harness, websocket)

    assert send.await_args.args[0] == {
        "type": "websocket.close", "code": 1008, "reason": f"{claim} mismatch",
    }
    harness.chat_coll.assert_not_awaited()
    harness.ordered.assert_not_called()
    harness.transport.handle_websocket.assert_not_awaited()


@pytest.mark.asyncio
async def test_ask_carrier_rejects_another_users_persisted_chat(harness):
    harness.collection.find_one.return_value = {"_id": "chat-1", "user_id": "another-user"}
    websocket, send = _socket()

    await _connect(harness, websocket)

    assert send.await_args.args[0] == {
        "type": "websocket.close", "code": 1008, "reason": "Chat not found",
    }
    harness.create_chat.assert_not_awaited()
    harness.collection.update_one.assert_not_awaited()
    harness.transport.handle_websocket.assert_not_awaited()


@pytest.mark.asyncio
async def test_non_ask_socket_without_workflows_closes_with_policy_violation(harness):
    websocket, send = _socket(ask=False)

    await _connect(harness, websocket, workflow="MissingWorkflow")

    assert send.await_args.args[0] == {
        "type": "websocket.close", "code": 1008, "reason": "Workflow not found",
    }
    harness.adapter.validate_token.assert_awaited_once()
    harness.ordered.assert_called_once()
    harness.chat_coll.assert_not_awaited()
    harness.transport.handle_websocket.assert_not_awaited()


@pytest.mark.asyncio
async def test_non_ask_socket_keeps_workflow_resolution_and_ownership_check(harness):
    harness.ordered.return_value = ["Builder"]
    harness.collection.find_one.return_value = {
        "_id": "chat-1", "user_id": "another-user", "workflow_name": "Builder",
    }
    websocket, send = _socket(ask=False)

    await _connect(harness, websocket, workflow="Builder")

    assert send.await_args.args[0] == {
        "type": "websocket.close", "code": 1008, "reason": "Chat not found",
    }
    harness.ordered.assert_called_once()
    harness.create_chat.assert_not_awaited()
    harness.transport.handle_websocket.assert_not_awaited()
