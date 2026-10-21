from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from tests.test_workflow_bridge import (
    _ag2_mod,
    _bridge_mod,
    _DummyTransport,
    _FakePersistenceManager,
)


@pytest.fixture
def admission(monkeypatch):
    from mozaiksai.core import session

    doc = {
        "_id": "chat-1", "app_id": "app-1", "user_id": "user-1",
        "workflow_name": "ValueEngine", "status": 0,
        "workflow_ui_state": {"tool_calls": {}, "pending_input_request": None},
    }
    reads = []

    async def find_one(query, projection=None):
        reads.append(deepcopy(query))
        return deepcopy(doc) if all(doc.get(k) == v for k, v in query.items()) else None

    pm = _FakePersistenceManager()
    pm.pending_input_request = None
    pm._coll = AsyncMock(return_value=SimpleNamespace(find_one=find_one))
    pm.load_run_events = AsyncMock(return_value=[])
    transport = _DummyTransport(pm)
    transport._workflow_spawn_semaphore = asyncio.Semaphore(1)
    monkeypatch.setattr(transport, "_apply_user_text_context_updates", AsyncMock(return_value={}))
    adapter = SimpleNamespace(
        has_persisted_execution=AsyncMock(return_value=False),
        run=AsyncMock(return_value=SimpleNamespace(status=SimpleNamespace(value="paused"))),
        resume=AsyncMock(return_value=SimpleNamespace(status=SimpleNamespace(value="paused"))),
    )
    lifecycle = {key: AsyncMock() for key in ("on_start", "on_complete", "on_fail")}
    dispatcher = SimpleNamespace(emit=AsyncMock())
    completed = Mock()
    binding = AsyncMock()
    monkeypatch.setattr(session, "get_session_router_for_chat", binding)
    monkeypatch.setattr(_ag2_mod, "get_ag2_adapter", lambda: adapter)
    monkeypatch.setattr(_bridge_mod, "get_workflow_lifecycle_hooks", lambda _: lifecycle)
    monkeypatch.setattr(_bridge_mod.session_registry, "complete_workflow", completed)
    from mozaiksai.core.events import unified_event_dispatcher
    monkeypatch.setattr(unified_event_dispatcher, "get_event_dispatcher", lambda: dispatcher)
    return SimpleNamespace(
        doc=doc, reads=reads, pm=pm, transport=transport, adapter=adapter,
        lifecycle=lifecycle, dispatcher=dispatcher, completed=completed, binding=binding,
    )


async def _background(h):
    result = await h.transport._run_workflow_background(
        chat_id="chat-1", app_id="app-1", user_id="user-1", workflow_name="ValueEngine",
        ws_id=42, initial_message=None,
    )
    await asyncio.sleep(0)
    return result


def _assert_no_execution(h):
    h.adapter.run.assert_not_awaited()
    h.adapter.resume.assert_not_awaited()
    h.dispatcher.emit.assert_not_awaited()
    h.completed.assert_not_called()
    for hook in h.lifecycle.values():
        hook.assert_not_awaited()
    assert h.pm.pending_clears == h.pm.persisted_context == h.pm.run_user_messages == []
    assert h.pm.failed == h.pm.completed == []
    assert h.pm.status == 0
    assert h.transport.submitted_inputs == []


@pytest.mark.asyncio
async def test_background_passive_reopen_with_native_state_has_no_execution_outcome(admission):
    admission.adapter.has_persisted_execution.return_value = True
    before = deepcopy(admission.doc)
    result = await _background(admission)
    assert result["route"] == "passive_reopen"
    _assert_no_execution(admission)
    assert admission.doc == before
    assert admission.transport.errors[0]["error_code"] == "WORKFLOW_REOPEN_UNAVAILABLE"


@pytest.mark.asyncio
async def test_passive_open_does_not_submit_resume_signal_to_live_callback(admission):
    admission.transport._input_request_registries["chat-1"] = {"pending": object()}
    result = await _background(admission)
    assert result["route"] == "passive_reopen"
    _assert_no_execution(admission)


@pytest.mark.asyncio
async def test_under_lease_recheck_rejects_state_created_after_precheck(admission, monkeypatch):
    @asynccontextmanager
    async def lease(**kwargs):
        assert kwargs == {"app_id": "app-1", "chat_id": "chat-1"}
        admission.adapter.has_persisted_execution.return_value = True
        yield

    monkeypatch.setattr(_bridge_mod, "chat_execution_lease", lease)
    result = await _background(admission)
    assert result["route"] == "passive_reopen"
    assert admission.adapter.has_persisted_execution.await_count == 2
    _assert_no_execution(admission)


@pytest.mark.asyncio
@pytest.mark.parametrize("change", [
    {"user_id": "foreign"}, {"app_id": "foreign"}, {"workflow_name": "OtherWorkflow"},
    {"status": 1}, {"status": 2}, {"status": "0"}, {"status": False},
    {"workflow_ui_state": None}, {"workflow_ui_state": []},
    {"workflow_ui_state": {"schema_version": 2}},
    {"workflow_ui_state": {"schema_version": "1"}},
    {"workflow_ui_state": {"unexpected_pending_state": {}}},
    {"workflow_ui_state": {"pending_input_request": {"request_id": "pending"}}},
    {"workflow_ui_state": {"pending_input_request": "malformed"}},
    {"workflow_ui_state": {"tool_calls": {"pending": {"awaiting_response": True}}}},
    {"workflow_ui_state": {"tool_calls": None}},
    {"workflow_ui_state": {"last_artifact": {"payload": {"private": "not for notice"}}}},
])
async def test_owned_fresh_state_is_required_without_mutation(admission, change):
    admission.doc.update(change)
    before = deepcopy(admission.doc)
    result = await _background(admission)
    assert result["route"] == "passive_reopen"
    _assert_no_execution(admission)
    assert admission.doc == before
    assert admission.reads[0] == {
        "_id": "chat-1", "app_id": "app-1", "user_id": "user-1", "workflow_name": "ValueEngine",
    }
    assert "not for notice" not in str(admission.transport.errors)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["binding", "collection", "history", "native"])
async def test_unavailable_authority_is_not_treated_as_empty(admission, failure):
    if failure == "binding":
        admission.binding.side_effect = ValueError("binding no longer owned")
    elif failure == "collection":
        admission.pm._coll.side_effect = RuntimeError("private connection details")
    elif failure == "history":
        admission.pm.load_run_events.side_effect = RuntimeError("history unavailable")
    else:
        admission.adapter.has_persisted_execution.side_effect = RuntimeError("native state unavailable")
    result = await _background(admission)
    assert result["route"] == "passive_reopen"
    _assert_no_execution(admission)
    assert "private connection details" not in str(admission.transport.errors)


@pytest.mark.asyncio
async def test_fresh_no_input_start_is_admitted_and_binding_rechecked(admission):
    result = await _background(admission)
    assert result["status"] == "success"
    admission.adapter.run.assert_awaited_once()
    assert admission.adapter.has_persisted_execution.await_count == 2
    assert admission.binding.await_count == 2
    admission.binding.assert_awaited_with(app_id="app-1", user_id="user-1", chat_id="chat-1")
    assert admission.transport.errors == []


@pytest.mark.asyncio
async def test_nonvisual_stream_event_prevents_fresh_start_without_native_namespace(admission):
    from ag2.events import ModelResponse
    from ag2.events.types import ModelMessage

    from mozaiksai.core.data.persistence.persistence_manager import AG2PersistenceManager

    event = ModelResponse(ModelMessage("NEXT", metadata={"source": "ag2_network_wal"}))
    assert AG2PersistenceManager().project_run_events_to_messages([event]) == []
    admission.pm.load_run_events.return_value = [event]
    assert (await _background(admission))["route"] == "passive_reopen"
    _assert_no_execution(admission)


@pytest.mark.asyncio
@pytest.mark.parametrize("native_state", [False, True])
async def test_admission_uses_real_adapter_and_native_store_presence(admission, monkeypatch, native_state):
    from ag2.knowledge import MemoryKnowledgeStore

    from mozaiksai.core.adapters import ag2_knowledge_store

    store = MemoryKnowledgeStore()
    if native_state:
        await store.write("/channels/saved/wal.jsonl", "native pending turn")
    monkeypatch.setattr(ag2_knowledge_store, "MongoAG2KnowledgeStore", lambda **kwargs: store)
    admission.adapter.has_persisted_execution = _ag2_mod.AG2OrchestrationAdapter().has_persisted_execution
    result = await _background(admission)
    assert result["route"] == ("passive_reopen" if native_state else "new_workflow")
    if native_state:
        _assert_no_execution(admission)
    else:
        admission.adapter.run.assert_awaited_once()


@pytest.mark.asyncio
async def test_owner_change_during_lease_acquisition_prevents_start(admission, monkeypatch):
    @asynccontextmanager
    async def lease(**kwargs):
        admission.doc["user_id"] = "foreign"
        yield

    monkeypatch.setattr(_bridge_mod, "chat_execution_lease", lease)
    result = await _background(admission)
    assert result["route"] == "passive_reopen"
    _assert_no_execution(admission)


@pytest.mark.asyncio
@pytest.mark.parametrize("message,initial_agent", [("Continue", None), (None, "ReviewAgent")])
async def test_explicit_input_and_declared_agent_continuation_keep_existing_path(admission, message, initial_agent):
    admission.adapter.has_persisted_execution.return_value = True
    admission.doc["workflow_ui_state"]["tool_calls"] = {"saved": {"awaiting_response": True}}
    result = await admission.transport.handle_user_input_from_api(
        chat_id="chat-1", app_id="app-1", user_id="user-1", workflow_name="ValueEngine",
        message=message, initial_agent_name_override=initial_agent,
    )
    assert result["status"] == "success"
    admission.adapter.has_persisted_execution.assert_not_awaited()
    if initial_agent:
        admission.adapter.resume.assert_awaited_once()
    else:
        admission.adapter.run.assert_awaited_once()


@pytest.mark.asyncio
async def test_two_passive_starts_cannot_execute_the_same_fresh_chat_twice(admission, monkeypatch):
    lock = asyncio.Lock()

    @asynccontextmanager
    async def lease(**kwargs):
        async with lock:
            yield

    async def run(request):
        admission.adapter.has_persisted_execution.return_value = True
        await asyncio.sleep(0)
        return SimpleNamespace(status=SimpleNamespace(value="paused"))

    monkeypatch.setattr(_bridge_mod, "chat_execution_lease", lease)
    admission.adapter.run.side_effect = run
    results = await asyncio.gather(_background(admission), _background(admission))
    assert sorted(r["route"] for r in results) == ["new_workflow", "passive_reopen"]
    admission.adapter.run.assert_awaited_once()
    admission.dispatcher.emit.assert_not_awaited()
