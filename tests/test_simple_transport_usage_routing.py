"""Live usage receipts follow the socket that owns the displayed chat."""

from unittest.mock import AsyncMock

import pytest

from mozaiksai.core.transport.simple_transport import SimpleTransport


@pytest.mark.asyncio
async def test_general_usage_reaches_only_matching_active_carriers(monkeypatch):
    transport = SimpleTransport()
    send = AsyncMock()
    monkeypatch.setattr(transport, "send_event_to_ui", send)

    def carrier(*, app_id="app-1", user_id="user-1", general_chat_id="ask-1", active=True, websocket=True):
        return {
            "app_id": app_id,
            "user_id": user_id,
            "general_session": {"chat_id": general_chat_id},
            "active": active,
            "websocket": object() if websocket else None,
        }

    transport.connections = {
        "carrier-1": carrier(),
        "carrier-2": carrier(),
        "other-chat": carrier(general_chat_id="ask-2"),
        "other-app": carrier(app_id="app-2"),
        "other-user": carrier(user_id="user-2"),
        "inactive": carrier(active=False),
        "disconnected": carrier(websocket=False),
    }
    payload = {
        "event_id": "usage-1",
        "chat_id": "ask-1",
        "app_id": "app-1",
        "user_id": "user-1",
        "workflow_name": "GeneralCapability",
        "total_tokens": 37,
    }

    await transport._handle_usage_delta_event(payload)

    expected_event = {
        "kind": "usage_delta",
        "event_id": "usage-1",
        "chat_id": "ask-1",
        "total_tokens": 37,
    }
    assert send.await_count == 2
    assert [call.args for call in send.await_args_list] == [
        (expected_event, "carrier-1"),
        (expected_event, "carrier-2"),
    ]
    assert transport._pre_connection_buffers == {}


@pytest.mark.asyncio
async def test_general_usage_without_owner_is_not_buffered_under_general_chat_id(monkeypatch):
    transport = SimpleTransport()
    send = AsyncMock()
    monkeypatch.setattr(transport, "send_event_to_ui", send)
    transport.connections = {}

    await transport._handle_usage_delta_event({
        "chat_id": "ask-1",
        "app_id": "app-1",
        "user_id": "user-1",
        "workflow_name": "GeneralCapability",
        "total_tokens": 37,
    })

    send.assert_not_awaited()
    assert transport._pre_connection_buffers == {}


@pytest.mark.asyncio
async def test_general_usage_matches_anonymous_carrier_identity(monkeypatch):
    transport = SimpleTransport()
    send = AsyncMock()
    monkeypatch.setattr(transport, "send_event_to_ui", send)
    transport.connections = {
        "guest-carrier": {
            "app_id": "app-1",
            "user_id": None,
            "general_session": {"chat_id": "ask-1"},
            "active": True,
            "websocket": object(),
        }
    }

    await transport._handle_usage_delta_event({
        "event_id": "guest-usage",
        "chat_id": "ask-1",
        "app_id": "app-1",
        "user_id": "anonymous",
        "workflow_name": "GeneralCapability",
        "total_tokens": 12,
    })

    send.assert_awaited_once_with({
        "kind": "usage_delta",
        "event_id": "guest-usage",
        "chat_id": "ask-1",
        "total_tokens": 12,
    }, "guest-carrier")


@pytest.mark.asyncio
async def test_workflow_usage_keeps_direct_chat_routing(monkeypatch):
    transport = SimpleTransport()
    send = AsyncMock()
    monkeypatch.setattr(transport, "send_event_to_ui", send)
    payload = {
        "event_id": "usage-2",
        "chat_id": "workflow-1",
        "app_id": "app-1",
        "user_id": "user-1",
        "workflow_name": "AppGenerator",
        "total_tokens": 42,
    }

    await transport._handle_usage_delta_event(payload)

    send.assert_awaited_once_with({
        "kind": "usage_delta",
        "event_id": "usage-2",
        "chat_id": "workflow-1",
        "total_tokens": 42,
    }, "workflow-1")


@pytest.mark.asyncio
async def test_hidden_agent_usage_reaches_socket_without_agent_identity(monkeypatch):
    transport = SimpleTransport()
    broadcast = AsyncMock()
    monkeypatch.setattr(transport, "_broadcast_to_websockets", broadcast)
    monkeypatch.setattr(transport, "should_show_to_user", lambda *_args: False)
    payload = {
        "event_id": "usage-3",
        "chat_id": "workflow-1",
        "app_id": "app-1",
        "user_id": "user-1",
        "workflow_name": "AppGenerator",
        "agent_name": "HiddenPlanningAgent",
        "model_name": "private-model",
        "prompt_tokens": 15,
        "completion_tokens": 25,
        "total_tokens": 40,
    }

    await transport._handle_usage_delta_event(payload)

    broadcast.assert_awaited_once()
    envelope, target_chat_id = broadcast.await_args.args
    assert target_chat_id == "workflow-1"
    assert envelope["type"] == "chat.usage_delta"
    assert envelope["data"] == {
        "kind": "usage_delta",
        "event_id": "usage-3",
        "chat_id": "workflow-1",
        "prompt_tokens": 15,
        "completion_tokens": 25,
        "total_tokens": 40,
    }
    assert payload["agent_name"] == "HiddenPlanningAgent"
