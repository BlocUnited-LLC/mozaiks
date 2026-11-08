"""Durable surfaces confirm the scoped canonical save, never browser rendering."""

import asyncio
from contextlib import contextmanager
from unittest.mock import AsyncMock

import pytest
from pymongo.results import UpdateResult

from mozaiksai.core.data.persistence.persistence_manager import AG2PersistenceManager
from mozaiksai.core.transport.simple_transport import SimpleTransport
from mozaiksai.core.transport.ui_tools import UIToolsMixin
from mozaiksai.core.workflow.agents.factory import (
    ContextVariablesBridge,
    _workflow_tool_invocation,
)
from mozaiksai.core.workflow.context.authority import build_context_authority_policy
from mozaiksai.core.workflow.ui_tools import UIToolError, emit_ui_surface


@contextmanager
def active_run():
    state = {
        "app_id": "app",
        "chat_id": "chat",
        "user_id": "owner",
        "workflow_name": "DraftFlow",
    }
    definitions = {
        key: {"type": "string", "source": {"type": "state", "default": value}}
        for key, value in state.items()
    }
    policy = build_context_authority_policy(workflow_name="DraftFlow", definitions=definitions)
    bridge = ContextVariablesBridge(state, authority_policy=policy)
    bridge._bind_run(("DraftFlow", "app", "chat"), policy)
    with _workflow_tool_invocation(bridge):
        yield


class ScopedCollection:
    """A database boundary with one app-owned chat and real driver receipts."""

    def __init__(self):
        self.app_id = "app"
        self.chat_id = "chat"
        self.acknowledged = True
        self.error = None
        self.calls = []
        self.saved = None
        self.order = []

    async def update_one(self, query, update):
        self.calls.append((query, update))
        self.order.append("save")
        if self.error is not None:
            raise self.error
        matched = query == {"_id": self.chat_id, "app_id": self.app_id}
        if matched and self.acknowledged:
            self.saved = update["$set"]["workflow_ui_state.last_artifact"]
        return UpdateResult({"n": int(matched), "nModified": int(matched)}, self.acknowledged)


class SurfaceTransport(UIToolsMixin):
    def __init__(self, pm, collection):
        self.pm = pm
        self.collection = collection
        self.connections = {}
        self.sent = []
        self.delivery_error = None
        self.context_lookups = []
        self._ui_tool_metadata = {}

    def _get_or_create_persistence_manager(self):
        return self.pm

    async def _resolve_chat_context(self, chat_id, **kwargs):
        self.context_lookups.append(chat_id)
        return "app", "DraftFlow"

    async def send_event_to_ui(self, event, chat_id):
        self.collection.order.append("send")
        if self.delivery_error is not None:
            raise self.delivery_error
        self.sent.append((event, chat_id))


@pytest.fixture
def surface_runtime(monkeypatch):
    collection = ScopedCollection()
    pm = AG2PersistenceManager.__new__(AG2PersistenceManager)
    monkeypatch.setattr(pm, "_coll", AsyncMock(return_value=collection))
    transport = SurfaceTransport(pm, collection)
    monkeypatch.setattr(SimpleTransport, "get_instance", AsyncMock(return_value=transport))
    return collection, transport


async def emit(**overrides):
    options = {
        "tool_id": "DraftReview",
        "payload": {"draft": "Example caption"},
        "chat_id": "chat",
        "workflow_name": "DraftFlow",
        "display": "artifact",
        "require_persistence": True,
    }
    options.update(overrides)
    return await emit_ui_surface(**options)


@pytest.mark.asyncio
@pytest.mark.parametrize("connected", [False, True])
async def test_confirmed_save_precedes_delivery_and_preserves_artifact(surface_runtime, connected):
    collection, transport = surface_runtime
    if connected:
        transport.connections["chat"] = {"app_id": "app", "workflow_name": "DraftFlow"}
    with active_run():
        event_id = await emit()
    assert isinstance(event_id, str) and event_id
    assert collection.order == ["save", "send"]
    assert transport.context_lookups == []
    assert collection.calls[0][0] == {"_id": "chat", "app_id": "app"}
    assert collection.saved["tool_call_id"] == event_id
    assert collection.saved["payload"]["draft"] == "Example caption"
    assert collection.saved["display"] == "artifact"
    assert collection.saved["workflow_name"] == "DraftFlow"
    assert transport.sent[0][0]["awaiting_response"] is False


@pytest.mark.asyncio
async def test_saved_surface_survives_live_delivery_failure(surface_runtime):
    collection, transport = surface_runtime
    transport.delivery_error = RuntimeError("viewer disconnected")
    with active_run():
        event_id = await emit()
    assert collection.saved["tool_call_id"] == event_id
    assert collection.order == ["save", "send"]
    assert transport.sent == []


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["database", "missing_chat", "foreign_app", "unacknowledged"])
async def test_unconfirmed_scoped_save_fails_before_delivery(surface_runtime, failure):
    collection, transport = surface_runtime
    if failure == "database":
        collection.error = RuntimeError("database unavailable")
    elif failure == "missing_chat":
        collection.chat_id = "missing"
    elif failure == "foreign_app":
        collection.app_id = "other-app"
    else:
        collection.acknowledged = False
    with active_run(), pytest.raises(UIToolError):
        await emit()
    assert collection.saved is None
    assert collection.order == ["save"]
    assert transport.sent == []


@pytest.mark.asyncio
async def test_durable_surface_requires_active_tool_invocation(surface_runtime):
    collection, transport = surface_runtime
    with pytest.raises(UIToolError):
        await emit(payload={"app_id": "app", "chat_id": "chat", "workflow_name": "DraftFlow"})
    assert collection.calls == []
    assert transport.sent == []


@pytest.mark.asyncio
@pytest.mark.parametrize("override", [{"chat_id": "other-chat"}, {"workflow_name": "OtherFlow"}])
async def test_caller_cannot_override_active_run_scope(surface_runtime, override):
    collection, transport = surface_runtime
    with active_run(), pytest.raises(UIToolError):
        await emit(**override)
    assert collection.calls == []
    assert transport.sent == []


@pytest.mark.asyncio
async def test_payload_app_identity_cannot_redirect_database_write(surface_runtime):
    collection, _ = surface_runtime
    with active_run():
        await emit(payload={"app_id": "other-app", "draft": "Example"})
    assert collection.calls[0][0] == {"_id": "chat", "app_id": "app"}


@pytest.mark.asyncio
@pytest.mark.parametrize("connection", [
    {"app_id": "other-app", "workflow_name": "DraftFlow"},
    {"app_id": "app", "workflow_name": "OtherFlow"},
])
async def test_connected_chat_scope_mismatch_is_rejected(surface_runtime, connection):
    collection, transport = surface_runtime
    transport.connections["chat"] = connection
    with active_run(), pytest.raises(UIToolError):
        await emit()
    assert collection.calls == []
    assert transport.sent == []


@pytest.mark.asyncio
@pytest.mark.parametrize("display", ["inline", "unknown"])
async def test_durable_confirmation_requires_artifact_mode(surface_runtime, display):
    collection, transport = surface_runtime
    with active_run(), pytest.raises(UIToolError):
        await emit(display=display)
    assert collection.calls == []
    assert transport.sent == []


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid", [float("nan"), float("inf"), object()])
async def test_invalid_json_payload_fails_before_database_write(surface_runtime, invalid):
    collection, transport = surface_runtime
    with active_run(), pytest.raises(UIToolError):
        await emit(payload={"draft": invalid})
    assert collection.calls == []
    assert transport.sent == []


@pytest.mark.asyncio
async def test_cancelled_save_is_not_converted_to_success(surface_runtime):
    collection, transport = surface_runtime
    collection.error = asyncio.CancelledError()
    with active_run(), pytest.raises(asyncio.CancelledError):
        await emit()
    assert collection.saved is None
    assert transport.sent == []


@pytest.mark.asyncio
async def test_default_emission_retains_best_effort_behavior_without_run(surface_runtime):
    collection, transport = surface_runtime
    collection.error = RuntimeError("database unavailable")
    event_id = await emit(require_persistence=False)
    assert isinstance(event_id, str) and event_id
    assert collection.saved is None
    assert collection.order == ["save", "send"]
    assert len(transport.sent) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("receipt", [None, False, 1, "saved"])
async def test_public_helper_requires_literal_confirmation(surface_runtime, monkeypatch, receipt):
    _, transport = surface_runtime
    monkeypatch.setattr(transport, "send_tool_call_event", AsyncMock(return_value=receipt))
    with active_run(), pytest.raises(UIToolError):
        await emit()


@pytest.mark.asyncio
async def test_payload_workflow_cannot_replace_runtime_provenance(surface_runtime):
    collection, _ = surface_runtime
    with active_run():
        await emit(payload={"workflow_name": "OtherFlow", "draft": "Example"})
    assert collection.saved["workflow_name"] == "DraftFlow"
    assert collection.saved["payload"]["workflow_name"] == "DraftFlow"


@pytest.mark.asyncio
async def test_connection_scope_change_after_save_prevents_cross_scope_delivery(surface_runtime, monkeypatch):
    collection, transport = surface_runtime
    original_update = collection.update_one

    async def change_scope(query, update):
        result = await original_update(query, update)
        transport.connections["chat"] = {"app_id": "other-app", "workflow_name": "DraftFlow"}
        return result

    monkeypatch.setattr(collection, "update_one", change_scope)
    with active_run():
        event_id = await emit()
    assert collection.saved["tool_call_id"] == event_id
    assert collection.order == ["save"]
    assert transport.sent == []
