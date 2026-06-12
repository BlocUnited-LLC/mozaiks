from __future__ import annotations

from copy import deepcopy

import pytest
from autogen.beta.events import ModelResponse
from autogen.beta.events.input_events import TextInput
from autogen.beta.events.types import ModelMessage

from tests.import_utils import import_module_directly

_persistence_mod = import_module_directly("mozaiksai.core.data.persistence.persistence_manager")
_stream_storage_mod = import_module_directly("mozaiksai.core.adapters.ag2_stream_storage")

AG2PersistenceManager = _persistence_mod.AG2PersistenceManager
PersistenceManager = _persistence_mod.PersistenceManager


class _FakeUpdateResult:
    def __init__(self, modified_count: int):
        self.modified_count = modified_count


def _apply_set(target: dict, dotted_path: str, value) -> None:  # noqa: ANN001
    cursor = target
    parts = dotted_path.split(".")
    for part in parts[:-1]:
        cursor = cursor.setdefault(part, {})
    cursor[parts[-1]] = value


class _FakeCollection:
    def __init__(self, doc: dict):
        self.doc = doc

    async def find_one(self, filter_doc, projection=None):  # noqa: ANN001
        if self.doc.get("_id") != filter_doc.get("_id"):
            return None
        expected_app_id = filter_doc.get("app_id")
        if expected_app_id and self.doc.get("app_id") != expected_app_id:
            return None
        return deepcopy(self.doc)

    async def update_one(self, filter_doc, update_doc, array_filters=None):  # noqa: ANN001
        if self.doc.get("_id") != filter_doc.get("_id"):
            return _FakeUpdateResult(0)
        expected_app_id = filter_doc.get("app_id")
        if expected_app_id and self.doc.get("app_id") != expected_app_id:
            return _FakeUpdateResult(0)

        for dotted_path, value in (update_doc.get("$set") or {}).items():
            _apply_set(self.doc, dotted_path, value)
        return _FakeUpdateResult(1)


def _apply_unset(target: dict, dotted_path: str) -> None:
    cursor = target
    parts = dotted_path.split(".")
    for part in parts[:-1]:
        next_cursor = cursor.get(part)
        if not isinstance(next_cursor, dict):
            return
        cursor = next_cursor
    cursor.pop(parts[-1], None)


class _FakeCursor:
    def __init__(self, docs: list[dict]):
        self._docs = [deepcopy(doc) for doc in docs]
        self._index = 0

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self._index >= len(self._docs):
            raise StopAsyncIteration
        doc = self._docs[self._index]
        self._index += 1
        return doc


class _FakeLegacyCollection:
    def __init__(self, docs: list[dict]):
        self.docs = {doc["_id"]: deepcopy(doc) for doc in docs}
        self.bulk_write_calls = 0

    def find(self, query, projection=None):  # noqa: ANN001
        _ = query
        _ = projection
        return _FakeCursor(list(self.docs.values()))

    async def bulk_write(self, operations, ordered=False):  # noqa: ANN001
        _ = ordered
        self.bulk_write_calls += 1
        for op in operations:
            filter_doc = getattr(op, "_filter", {})
            update_doc = getattr(op, "_doc", {})
            doc = self.docs.get(filter_doc.get("_id"))
            if not isinstance(doc, dict):
                continue
            for dotted_path, value in (update_doc.get("$set") or {}).items():
                _apply_set(doc, dotted_path, deepcopy(value))
            for dotted_path in (update_doc.get("$unset") or {}).keys():
                _apply_unset(doc, dotted_path)
        return _FakeUpdateResult(len(operations))



@pytest.mark.asyncio
async def test_load_run_history_maps_ag2_input_and_model_events(monkeypatch):
    manager = AG2PersistenceManager()

    async def _fake_load_run_events(*, chat_id: str, app_id: str):
        assert chat_id == "chat-1"
        assert app_id == "app-1"
        return [
            TextInput("hello"),
            ModelResponse(ModelMessage("hi there", metadata={"agent_name": "BuilderAgent"}), model="gpt-test"),
        ]

    monkeypatch.setattr(manager, "load_run_events", _fake_load_run_events)

    history = await manager.load_run_history(chat_id="chat-1", app_id="app-1")

    assert history == [
        {"role": "user", "name": "user", "content": "hello"},
        {
            "role": "assistant",
            "name": "BuilderAgent",
            "content": "hi there",
            "metadata": {
                "source": "agent",
                "model": "gpt-test",
                "agent_name": "BuilderAgent",
            },
        },
    ]


@pytest.mark.asyncio
async def test_append_run_assistant_message_persists_model_response_to_ag2_stream(monkeypatch):
    manager = AG2PersistenceManager()
    captured: dict[str, object] = {}

    class _FakeStorage:
        def __init__(self, *, app_id: str):
            captured["app_id"] = app_id

        async def save_event(self, event, context):  # noqa: ANN001
            captured["event"] = event
            captured["stream_id"] = context.stream.id

    monkeypatch.setattr(_stream_storage_mod, "MongoAG2StreamStorage", _FakeStorage)

    await manager.append_run_assistant_message(
        chat_id="chat-1",
        app_id="app-1",
        content="Tell me about your idea.",
        agent_name="ValueInterviewAgent",
        metadata={"source": "orchestrator.initial_message_to_user"},
    )

    assert captured["app_id"] == "app-1"
    assert captured["stream_id"] == manager._run_stream_id(chat_id="chat-1", app_id="app-1")
    event = captured["event"]
    assert isinstance(event, ModelResponse)
    assert event.content == "Tell me about your idea."
    assert event.metadata["agent_name"] == "ValueInterviewAgent"
    assert event.metadata["source"] == "orchestrator.initial_message_to_user"


@pytest.mark.asyncio
async def test_attach_tool_call_metadata_persists_workflow_ui_state(monkeypatch):
    manager = AG2PersistenceManager()
    doc = {"_id": "chat-1", "app_id": "app-1"}
    coll = _FakeCollection(doc)

    async def _fake_coll():
        return coll

    async def _fake_load_run_history(*, chat_id: str, app_id: str):
        assert chat_id == "chat-1"
        assert app_id == "app-1"
        return [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi there"},
        ]

    monkeypatch.setattr(manager, "_coll", _fake_coll)
    monkeypatch.setattr(manager, "load_run_history", _fake_load_run_history)

    await manager.attach_tool_call_metadata(
        chat_id="chat-1",
        app_id="app-1",
        event_id="tool.alpha_123",
        metadata={
            "tool_name": "ToolAlpha",
            "tool_call_status": "pending",
            "tool_call_completed": False,
            "payload": {"section": "plan"},
        },
    )

    stored = doc["workflow_ui_state"]["tool_calls"]
    assert len(stored) == 1
    record = next(iter(stored.values()))
    assert record["tool_call_id"] == "tool.alpha_123"
    assert record["message_index"] == 1
    assert record["tool_name"] == "ToolAlpha"
    assert record["tool_call_status"] == "pending"
    assert record["tool_call_completed"] is False
    assert record["payload"] == {"section": "plan"}
    assert isinstance(record["timestamp"], str)
    assert isinstance(record["updated_at"], str)


@pytest.mark.asyncio
async def test_update_tool_call_state_updates_workflow_ui_state(monkeypatch):
    manager = AG2PersistenceManager()
    doc = {
        "_id": "chat-1",
        "app_id": "app-1",
        "workflow_ui_state": {
            "tool_calls": {
                manager._workflow_tool_call_storage_key("tool.alpha_123"): {
                    "tool_call_id": "tool.alpha_123",
                    "tool_name": "ToolAlpha",
                    "message_index": 1,
                    "tool_call_status": "pending",
                    "tool_call_completed": False,
                    "timestamp": "2026-01-01T00:00:00+00:00",
                }
            }
        },
    }
    coll = _FakeCollection(doc)

    async def _fake_coll():
        return coll

    monkeypatch.setattr(manager, "_coll", _fake_coll)

    await manager.update_tool_call_state(
        chat_id="chat-1",
        app_id="app-1",
        event_id="tool.alpha_123",
        status="completed",
        completed=True,
    )

    record = next(iter(doc["workflow_ui_state"]["tool_calls"].values()))
    assert record["tool_call_status"] == "completed"
    assert record["tool_call_completed"] is True
    assert isinstance(record["completed_at"], str)
    assert isinstance(record["updated_at"], str)


@pytest.mark.asyncio
async def test_pending_input_request_persists_inside_workflow_ui_state(monkeypatch):
    manager = AG2PersistenceManager()
    doc = {
        "_id": "chat-1",
        "app_id": "app-1",
        "workflow_ui_state": {
            "schema_version": 1,
            "last_artifact": None,
            "pending_input_request": None,
            "tool_calls": {},
        },
    }
    coll = _FakeCollection(doc)

    async def _fake_coll():
        return coll

    monkeypatch.setattr(manager, "_coll", _fake_coll)

    await manager.save_pending_input_request(
        chat_id="chat-1",
        app_id="app-1",
        request_id="req-1",
        agent="InterviewAgent",
        prompt="Tell me more.",
        component_type="UserInputRequest",
        workflow_name="AppGenerator",
        tool_name="UserInputRequest",
        raw_payload={"resume_ui_kind": "awaiting_reply"},
    )

    pending = doc["workflow_ui_state"]["pending_input_request"]
    assert pending["request_id"] == "req-1"
    assert pending["agent"] == "InterviewAgent"
    assert pending["workflow_name"] == "AppGenerator"
    assert pending["raw_payload"] == {"resume_ui_kind": "awaiting_reply"}

    loaded = await manager.get_pending_input_request(chat_id="chat-1", app_id="app-1")
    assert loaded == pending

    await manager.clear_pending_input_request(chat_id="chat-1", app_id="app-1")
    assert doc["workflow_ui_state"]["pending_input_request"] is None


@pytest.mark.asyncio
async def test_update_last_artifact_persists_inside_workflow_ui_state(monkeypatch):
    manager = AG2PersistenceManager()
    doc = {
        "_id": "chat-1",
        "app_id": "app-1",
        "workflow_ui_state": {
            "schema_version": 1,
            "last_artifact": None,
            "pending_input_request": None,
            "tool_calls": {},
        },
    }
    coll = _FakeCollection(doc)

    async def _fake_coll():
        return coll

    monkeypatch.setattr(manager, "_coll", _fake_coll)

    await manager.update_last_artifact(
        chat_id="chat-1",
        app_id="app-1",
        artifact={
            "tool_name": "RenderPlan",
            "tool_call_id": "plan_123",
            "component_type": "RenderPlan",
            "display": "artifact",
            "workflow_name": "AgentGenerator",
            "payload": {"plan": "v1"},
        },
    )

    artifact = doc["workflow_ui_state"]["last_artifact"]
    assert artifact["tool_name"] == "RenderPlan"
    assert artifact["tool_call_id"] == "plan_123"
    assert artifact["workflow_name"] == "AgentGenerator"
    assert artifact["payload"] == {"plan": "v1"}
    assert isinstance(artifact["updated_at"], str)


@pytest.mark.asyncio
async def test_persistence_manager_backfills_pre_migration_workflow_ui_state_without_read_fallbacks():
    manager = PersistenceManager()
    coll = _FakeLegacyCollection(
        [
            {
                "_id": "chat-pre-migration-1",
                "app_id": "app-1",
                "last_artifact": {"tool_name": "RenderPlan"},
                "pending_input_request": {
                    "request_id": "req-1",
                    "agent": "InterviewAgent",
                    "prompt": "Tell me more.",
                },
            },
            {
                "_id": "chat-pre-migration-2",
                "app_id": "app-1",
                "workflow_ui_state": {
                    "tool_calls": {},
                    "last_artifact": {"tool_name": "NestedArtifact"},
                },
                "last_artifact": {"tool_name": "LegacyArtifact"},
                "pending_input_request": {
                    "request_id": "req-2",
                    "agent": "Planner",
                    "prompt": "Approve this.",
                },
            },
            {
                "_id": "chat-modern",
                "app_id": "app-1",
                "workflow_ui_state": {
                    "schema_version": 1,
                    "last_artifact": {"tool_name": "CurrentArtifact"},
                    "pending_input_request": None,
                    "tool_calls": {},
                },
            },
        ]
    )

    await manager._backfill_chat_sessions_workflow_ui_state(coll)

    migrated = coll.docs["chat-pre-migration-1"]
    assert migrated["workflow_ui_state"]["schema_version"] == 1
    assert migrated["workflow_ui_state"]["last_artifact"] == {"tool_name": "RenderPlan"}
    assert migrated["workflow_ui_state"]["pending_input_request"] == {
        "request_id": "req-1",
        "agent": "InterviewAgent",
        "prompt": "Tell me more.",
    }
    assert "last_artifact" not in migrated
    assert "pending_input_request" not in migrated

    merged = coll.docs["chat-pre-migration-2"]
    assert merged["workflow_ui_state"]["schema_version"] == 1
    assert merged["workflow_ui_state"]["last_artifact"] == {"tool_name": "NestedArtifact"}
    assert merged["workflow_ui_state"]["pending_input_request"] == {
        "request_id": "req-2",
        "agent": "Planner",
        "prompt": "Approve this.",
    }
    assert "last_artifact" not in merged
    assert "pending_input_request" not in merged

    modern = coll.docs["chat-modern"]
    assert modern["workflow_ui_state"]["schema_version"] == 1
    assert modern["workflow_ui_state"]["last_artifact"] == {"tool_name": "CurrentArtifact"}
    assert coll.bulk_write_calls == 1

