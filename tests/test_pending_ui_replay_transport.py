from __future__ import annotations

import asyncio
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from ag2.events import ModelResponse
from ag2.events.types import ModelMessage

from mozaiksai.core.data.persistence import persistence_manager
from mozaiksai.core.events.unified_event_dispatcher import get_event_dispatcher
from mozaiksai.core.transport.event_contract import MozaiksEventEnvelope, send_event_envelope
from mozaiksai.core.transport.handlers import workflow_handlers
from mozaiksai.core.transport.run_replay import WorkflowRunReplayer
from mozaiksai.core.transport.session_registry import SessionRegistry
from mozaiksai.core.transport.ui_tools import UIToolsMixin
from mozaiksai.core.transport.ws_protocol import WebSocketProtocolMixin


class _Collection:
    def __init__(self):
        self.doc = {
            "_id": "chat", "app_id": "app", "user_id": "owner",
            "workflow_name": "DesignDocs", "status": 0,
        }
        self.queries = []

    def _matches(self, query):
        for key, value in query.items():
            actual = self.doc
            for part in key.split("."):
                actual = actual.get(part) if isinstance(actual, dict) else None
            if actual != value:
                return False
        return True

    async def find_one(self, query, projection=None):
        self.queries.append(deepcopy(query))
        return deepcopy(self.doc) if self._matches(query) else None

    async def update_one(self, query, update):
        if not self._matches(query):
            return SimpleNamespace(modified_count=0)
        for key, value in update.get("$set", {}).items():
            target = self.doc
            parts = key.split(".")
            for part in parts[:-1]:
                target = target.setdefault(part, {})
            target[parts[-1]] = deepcopy(value)
        return SimpleNamespace(modified_count=1)


class _Socket:
    def __init__(self):
        self.events = []

    async def send_json(self, event):
        MozaiksEventEnvelope.model_validate(event)
        self.events.append(deepcopy(event))


class _Transport(UIToolsMixin, WebSocketProtocolMixin):
    def __init__(self, pm, socket):
        self.pm = pm
        self.connections = {"chat": {
            "app_id": "app", "user_id": "owner", "workflow_name": "DesignDocs",
            "ws_id": "socket", "websocket": socket,
        }}
        self._message_queues = {}
        self._max_queue_size = 100
        self._ui_tool_metadata = {}
        self._input_request_registries = {}
        self.pending_tool_call_responses = {}
        self._background_tasks = {}

    def _get_conn_meta(self, chat_id):
        return self.connections.get(chat_id, {})

    def _get_or_create_persistence_manager(self):
        return self.pm

    async def _resolve_chat_context(self, chat_id, **kwargs):
        connection = self.connections[chat_id]
        return connection["app_id"], connection["workflow_name"]

    def _serialize_ag2_events(self, value):
        return value

    async def send_event_to_ui(self, event, chat_id):
        envelope = get_event_dispatcher().build_outbound_event_envelope(raw_event=event, chat_id=chat_id)
        await send_event_envelope(self.connections[chat_id]["websocket"], envelope)


@pytest.fixture
def replay_transport(monkeypatch):
    from mozaiksai.core import session
    from mozaiksai.core.workflow.workflow_manager import workflow_manager

    collection = _Collection()
    pm = persistence_manager.AG2PersistenceManager()
    monkeypatch.setattr(pm, "_coll", AsyncMock(return_value=collection))
    monkeypatch.setattr(pm, "load_run_events", AsyncMock(return_value=[]))
    monkeypatch.setattr(WorkflowRunReplayer, "_ensure_persistence_manager", AsyncMock(return_value=pm))
    monkeypatch.setattr(WorkflowRunReplayer, "_get_persistence_manager", lambda self: pm)
    monkeypatch.setattr(WorkflowRunReplayer, "_load_resume_state", AsyncMock(return_value=None))
    monkeypatch.setattr(session, "get_session_router_for_chat", AsyncMock())
    monkeypatch.setattr(workflow_manager, "reload_workflow", lambda name: None)
    monkeypatch.setattr(workflow_manager, "get_config", lambda name: {"workflow_startup_mode": "AgentDriven"})
    registry = SessionRegistry()
    registry.add_workflow("socket", "chat", "DesignDocs", "app", "owner")
    monkeypatch.setattr(workflow_handlers, "session_registry", registry)
    socket = _Socket()
    return _Transport(pm, socket), socket, collection, registry


async def _emit_review(transport):
    payload = {
        "review_id": "original-review", "checkpoints": ["Member may submit"],
        "actions": [{"id": "approve", "payload": {"review_id": "original-review"}}],
    }
    await transport.send_tool_call_event(
        event_id="original-event", chat_id="chat", tool_name="review_policy",
        component_name="ApprovalCard", display_type="inline", payload=payload,
        agent_name="DesignDocsAgent", awaiting_response=True,
    )
    return payload


async def _reconnect(transport, socket, mode):
    if mode == "connect":
        await transport._replay_run_on_connect_if_needed("chat", socket, "app")
    else:
        await workflow_handlers.handle_switch_workflow(
            transport, {"chat_id": "chat", "replay_on_switch": True}, "chat", socket,
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["connect", "switch"])
@pytest.mark.parametrize("live", [False, True])
async def test_pending_review_emit_persist_and_replay_reaches_actual_socket(replay_transport, mode, live):
    transport, socket, collection, _ = replay_transport
    payload = await _emit_review(transport)
    future = asyncio.get_running_loop().create_future() if live else None
    if future is not None:
        transport.pending_tool_call_responses["original-event"] = future
    stored = next(iter(collection.doc["workflow_ui_state"]["tool_calls"].values()))
    assert stored["payload"] == payload
    assert "message_index" not in stored
    assert socket.events[0]["data"]["tool_call_id"] == "original-event"
    before = deepcopy(collection.doc)
    socket.events.clear()

    await _reconnect(transport, socket, mode)

    events = [event for event in socket.events if event["type"] == "chat.tool_call"]
    assert len(events) == 1
    event = events[0]["data"]
    assert event["tool_call_id"] == event["corr"] == "original-event"
    assert event["payload"] == payload
    assert event["component_type"] == "ApprovalCard"
    assert event["workflow_name"] == "DesignDocs"
    assert event["display"] == "inline"
    assert event["awaiting_response"] is True
    assert event["replay"] is True
    assert event["response_available"] is live
    assert len(transport.pending_tool_call_responses) == int(live)
    if future is not None:
        assert transport.pending_tool_call_responses["original-event"] is future
        assert not future.done()
    assert collection.doc == before
    assert all(query["app_id"] == "app" for query in collection.queries)


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["connect", "switch"])
@pytest.mark.parametrize("state", ["responded", "terminal", "other_app"])
async def test_actual_replay_adapters_do_not_reopen_or_leak_review(replay_transport, mode, state):
    transport, socket, collection, _ = replay_transport
    await _emit_review(transport)
    if state == "responded":
        await transport.pm.update_tool_call_state(
            chat_id="chat", app_id="app", event_id="original-event", status="responded", completed=False,
        )
    elif state == "terminal":
        collection.doc["status"] = 2
    else:
        collection.doc["app_id"] = "different-app"
    socket.events.clear()

    await _reconnect(transport, socket, mode)

    assert not any(event["type"] == "chat.tool_call" for event in socket.events)


@pytest.mark.asyncio
@pytest.mark.parametrize("identity", ["app_id", "user_id"])
async def test_switch_replay_rejects_other_session_owner(replay_transport, identity):
    transport, socket, _, registry = replay_transport
    await _emit_review(transport)
    setattr(registry._workflows["socket"][0], identity, "different-owner")
    socket.events.clear()

    with pytest.raises(ValueError, match="identity does not match"):
        await _reconnect(transport, socket, "switch")

    assert socket.events == []


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["connect", "switch"])
async def test_actual_replay_adapters_keep_history_tool_deduplication(replay_transport, mode):
    transport, socket, _, _ = replay_transport
    transport.pm.load_run_events.return_value = [
        ModelResponse(ModelMessage("Review permissions", metadata={"agent_name": "Reviewer"}), model="test"),
    ]
    payload = await _emit_review(transport)
    socket.events.clear()

    await _reconnect(transport, socket, mode)

    assert not any(event["type"] == "chat.tool_call" for event in socket.events)
    messages = [event["data"] for event in socket.events if event["type"] == "chat.text"]
    assert len(messages) == 1
    tool = messages[0]["metadata"]["tool_call"]
    assert tool["tool_call_id"] == "original-event"
    assert tool["payload"] == payload
    assert tool["response_available"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["connect", "switch"])
async def test_actual_replay_adapters_forward_canonical_composer_tool_call(replay_transport, mode):
    transport, socket, collection, _ = replay_transport
    transport.pm.load_run_events.return_value = [
        ModelResponse(ModelMessage("Please reply", metadata={"agent_name": "Reviewer"}), model="test"),
    ]
    await transport.pm.save_pending_input_request(
        chat_id="chat", app_id="app", request_id="original-input", agent="Reviewer",
        prompt="Explain the change", workflow_name="DesignDocs", component_type="UserInputRequest",
        raw_payload={"input_mode": "text"},
    )
    before = deepcopy(collection.doc)

    await _reconnect(transport, socket, mode)

    events = [event["data"] for event in socket.events if event["type"] == "chat.tool_call"]
    assert len(events) == 1
    event = events[0]
    assert event["tool_call_id"] == event["corr"] == "original-input"
    assert event["component_type"] == "UserInputRequest"
    assert event["display"] == "composer"
    assert event["interaction_type"] == "input_request"
    assert event["awaiting_response"] is True
    assert event["response_available"] is False
    assert event["payload"]["prompt"] == "Explain the change"
    assert event["payload"]["input_mode"] == "text"
    assert collection.doc == before
