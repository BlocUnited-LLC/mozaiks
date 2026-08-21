from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest
from ag2.events import ModelResponse
from ag2.events.input_events import TextInput
from ag2.events.types import ModelMessage

from tests.import_utils import import_module_directly

_persistence_mod = import_module_directly("mozaiksai.core.data.persistence.persistence_manager")
_stream_storage_mod = import_module_directly("mozaiksai.core.adapters.ag2_stream_storage")

AG2PersistenceManager = _persistence_mod.AG2PersistenceManager
PersistenceManager = _persistence_mod.PersistenceManager


class _FakeUpdateResult:
    def __init__(
        self,
        modified_count: int,
        *,
        matched_count: int | None = None,
        acknowledged: bool = True,
    ):
        self.modified_count = modified_count
        self.matched_count = modified_count if matched_count is None else matched_count
        self.acknowledged = acknowledged


class _FakeInsertResult:
    def __init__(self, *, acknowledged: bool = True):
        self.acknowledged = acknowledged


def _apply_set(target: dict, dotted_path: str, value) -> None:  # noqa: ANN001
    cursor = target
    parts = dotted_path.split(".")
    for part in parts[:-1]:
        cursor = cursor.setdefault(part, {})
    cursor[parts[-1]] = value


class _FakeCollection:
    def __init__(
        self,
        doc: dict,
        *,
        find_error: Exception | None = None,
        update_error: Exception | None = None,
        insert_error: Exception | None = None,
        update_result: _FakeUpdateResult | None = None,
        insert_result: _FakeInsertResult | None = None,
    ):
        self.doc = doc
        self.find_error = find_error
        self.update_error = update_error
        self.insert_error = insert_error
        self.update_result = update_result
        self.insert_result = insert_result
        self.find_filters: list[dict] = []
        self.update_filters: list[dict] = []

    async def find_one(self, filter_doc, projection=None):  # noqa: ANN001
        self.find_filters.append(deepcopy(filter_doc))
        if self.find_error is not None:
            raise self.find_error
        if self.doc.get("_id") != filter_doc.get("_id"):
            return None
        expected_app_id = filter_doc.get("app_id")
        if expected_app_id and self.doc.get("app_id") != expected_app_id:
            return None
        expected_workflow = filter_doc.get("workflow_name")
        if expected_workflow and self.doc.get("workflow_name") != expected_workflow:
            return None
        return deepcopy(self.doc)

    async def update_one(self, filter_doc, update_doc, array_filters=None):  # noqa: ANN001
        self.update_filters.append(deepcopy(filter_doc))
        if self.update_error is not None:
            raise self.update_error
        if self.doc.get("_id") != filter_doc.get("_id"):
            return _FakeUpdateResult(0, matched_count=0)
        expected_app_id = filter_doc.get("app_id")
        if expected_app_id and self.doc.get("app_id") != expected_app_id:
            return _FakeUpdateResult(0, matched_count=0)
        expected_workflow = filter_doc.get("workflow_name")
        if expected_workflow and self.doc.get("workflow_name") != expected_workflow:
            return _FakeUpdateResult(0, matched_count=0)

        for dotted_path, value in (update_doc.get("$set") or {}).items():
            _apply_set(self.doc, dotted_path, value)
        return self.update_result or _FakeUpdateResult(1, matched_count=1)

    async def insert_one(self, document):  # noqa: ANN001
        if self.insert_error is not None:
            raise self.insert_error
        self.doc = deepcopy(document)
        return self.insert_result or _FakeInsertResult()


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


@pytest.fixture
def authentic_workflow_policies():
    """Pin these round trips to the real factory workflow root.

    An unresolvable workflow fails closed rather than silently dropping state.
    This local fixture explicitly binds the canonical Factory declarations for
    these persistence assertions and restores the previously active root afterward.
    """

    from mozaiksai.core.workflow.workflow_manager import (
        get_workflow_manager,
        initialize_workflows,
    )

    previous_root = str(get_workflow_manager().workflows_base_path)
    canonical_root = str(Path(__file__).resolve().parents[1] / "factory_app" / "workflows")
    initialize_workflows(canonical_root)
    try:
        yield
    finally:
        initialize_workflows(previous_root)

@pytest.mark.asyncio
async def test_context_variable_persistence_resume_round_trip_honors_authority_policy(
    monkeypatch,
    authentic_workflow_policies,
    caplog,
):
    manager = AG2PersistenceManager()
    doc = {
        "_id": "chat-1",
        "app_id": "app-1",
        "workflow_name": "ValueEngine",
    }
    coll = _FakeCollection(doc)

    async def _fake_coll():
        return coll

    monkeypatch.setattr(manager, "_coll", _fake_coll)

    await manager.persist_context_variables(
        chat_id="chat-1",
        app_id="app-1",
        workflow_name="ValueEngine",
        variables={
            "build_registry_id": "registry-1",
            "chat_app_id": "authority-app-1",
            "build_mode": "revision",
            "app_name": "Round Trip App",
            "stale_unknown_context_key": "old",
        },
    )

    assert "build_registry_id" not in doc
    assert "chat_app_id" not in doc
    assert "stale_unknown_context_key" not in doc
    assert doc["build_mode"] == "revision"
    assert doc["app_name"] == "Round Trip App"
    assert coll.update_filters[-1]["workflow_name"] == "ValueEngine"

    doc["build_registry_id"] = "stale-registry"
    doc["chat_app_id"] = "stale-authority-app"
    doc["ag2_stream_id"] = "stale-stream"
    doc["session_version"] = 7
    doc["stale_historical_key"] = "SECRET_STALE_CONTEXT_VALUE"
    caplog.set_level(10)

    replayed = await manager.fetch_chat_session_extra_context(
        chat_id="chat-1",
        app_id="app-1",
        workflow_name="ValueEngine",
    )

    assert replayed == {
        "build_mode": "revision",
        "app_name": "Round Trip App",
    }
    assert coll.find_filters[-1]["workflow_name"] == "ValueEngine"
    assert "stale_historical_key" in caplog.text
    assert "ValueEngine" in caplog.text
    assert "SECRET_STALE_CONTEXT_VALUE" not in caplog.text


@pytest.mark.asyncio
async def test_registered_factory_workflow_persistence_resume_round_trip(
    monkeypatch,
    authentic_workflow_policies,
):
    workflow_name = "AppGenerator"
    variables = {
        "task_run_mode": True,
        "workflow_integration_repair_status": "needs_revision",
        "app_validation_status": "failed",
    }
    expected = dict(variables)
    manager = AG2PersistenceManager()
    doc = {
        "_id": f"chat-{workflow_name}",
        "app_id": "app-1",
        "workflow_name": workflow_name,
    }
    coll = _FakeCollection(doc)

    async def _fake_coll():
        return coll

    monkeypatch.setattr(manager, "_coll", _fake_coll)

    await manager.persist_context_variables(
        chat_id=f"chat-{workflow_name}",
        app_id="app-1",
        workflow_name=workflow_name,
        variables={**variables, "stale_unknown_context_key": "old"},
    )

    for key, value in expected.items():
        assert doc[key] == value
    assert "stale_unknown_context_key" not in doc

    doc["stale_unknown_context_key"] = "old"
    replayed = await manager.fetch_chat_session_extra_context(
        chat_id=f"chat-{workflow_name}",
        app_id="app-1",
        workflow_name=workflow_name,
    )

    assert replayed == expected


@pytest.mark.asyncio
async def test_known_malformed_persisted_value_fails_closed(monkeypatch, authentic_workflow_policies):
    manager = AG2PersistenceManager()
    coll = _FakeCollection({"_id": "chat-1", "app_id": "app-1", "workflow_name": "ValueEngine"})

    async def _fake_coll():
        return coll

    monkeypatch.setattr(manager, "_coll", _fake_coll)

    with pytest.raises(ValueError, match="invalid_value"):
        await manager.persist_context_variables(
            chat_id="chat-1",
            app_id="app-1",
            workflow_name="ValueEngine",
            variables={"interview_complete": "true"},
        )


@pytest.mark.asyncio
async def test_malformed_stored_value_fails_replay_closed(monkeypatch, authentic_workflow_policies):
    manager = AG2PersistenceManager()
    coll = _FakeCollection(
        {
            "_id": "chat-1",
            "app_id": "app-1",
            "workflow_name": "ValueEngine",
            "interview_complete": "true",
            "concept_presented": True,
        }
    )

    async def _fake_coll():
        return coll

    monkeypatch.setattr(manager, "_coll", _fake_coll)

    with pytest.raises(ValueError, match="invalid_value"):
        await manager.fetch_chat_session_extra_context(
            chat_id="chat-1",
            app_id="app-1",
            workflow_name="ValueEngine",
        )


@pytest.mark.parametrize("workflow_name", [None, "", "   "])
@pytest.mark.asyncio
async def test_nonempty_context_persistence_requires_workflow_identity(
    monkeypatch,
    workflow_name,
):
    manager = AG2PersistenceManager()
    coll = _FakeCollection(
        {"_id": "chat-1", "app_id": "app-1", "workflow_name": "ValueEngine"}
    )

    async def _fake_coll():
        return coll

    monkeypatch.setattr(manager, "_coll", _fake_coll)

    with pytest.raises(ValueError, match=r"workflow_required .*op=persist"):
        await manager.persist_context_variables(
            chat_id="chat-1",
            app_id="app-1",
            workflow_name=workflow_name,
            variables={"app_name": "Bounded App"},
        )

    assert coll.update_filters == []


@pytest.mark.asyncio
async def test_protected_only_nonempty_context_requires_workflow_identity(monkeypatch):
    manager = AG2PersistenceManager()
    coll = _FakeCollection(
        {"_id": "chat-1", "app_id": "app-1", "workflow_name": "ValueEngine"}
    )

    async def _fake_coll():
        return coll

    monkeypatch.setattr(manager, "_coll", _fake_coll)

    with pytest.raises(ValueError, match=r"workflow_required .*op=persist"):
        await manager.persist_context_variables(
            chat_id="chat-1",
            app_id="app-1",
            workflow_name=None,
            variables={"chat_id": "forged-chat-id"},
        )

    assert coll.update_filters == []


@pytest.mark.asyncio
async def test_protected_only_nonempty_context_requires_resolved_declarations(monkeypatch):
    manager = AG2PersistenceManager()
    coll = _FakeCollection(
        {"_id": "chat-1", "app_id": "app-1", "workflow_name": "ValueEngine"}
    )

    async def _fake_coll():
        return coll

    monkeypatch.setattr(manager, "_coll", _fake_coll)

    with pytest.raises(
        ValueError,
        match=r"unresolved_declarations workflow=NotARegisteredWorkflow .*op=persist",
    ):
        await manager.persist_context_variables(
            chat_id="chat-1",
            app_id="app-1",
            workflow_name="NotARegisteredWorkflow",
            variables={"chat_id": "forged-chat-id"},
        )

    assert coll.update_filters == []


@pytest.mark.parametrize("workflow_name", [None, "", "   "])
@pytest.mark.asyncio
async def test_nonempty_context_replay_requires_workflow_identity(
    monkeypatch,
    workflow_name,
):
    manager = AG2PersistenceManager()
    coll = _FakeCollection(
        {
            "_id": "chat-1",
            "app_id": "app-1",
            "workflow_name": "ValueEngine",
            "app_name": "Bounded App",
        }
    )

    async def _fake_coll():
        return coll

    monkeypatch.setattr(manager, "_coll", _fake_coll)

    with pytest.raises(ValueError, match=r"workflow_required .*op=replay"):
        await manager.fetch_chat_session_extra_context(
            chat_id="chat-1",
            app_id="app-1",
            workflow_name=workflow_name,
        )


@pytest.mark.asyncio
async def test_context_persistence_policy_resolution_failure_is_not_silenced(monkeypatch):
    manager = AG2PersistenceManager()
    coll = _FakeCollection(
        {"_id": "chat-1", "app_id": "app-1", "workflow_name": "ValueEngine"}
    )

    async def _fake_coll():
        return coll

    def _fail_policy(_workflow_name):
        raise RuntimeError("SECRET_POLICY_RESOLUTION_VALUE")

    monkeypatch.setattr(manager, "_coll", _fake_coll)
    monkeypatch.setattr(_persistence_mod, "_context_authority_policy_for_workflow", _fail_policy)

    with pytest.raises(ValueError, match=r"policy_unavailable workflow=ValueEngine op=persist") as exc_info:
        await manager.persist_context_variables(
            chat_id="chat-1",
            app_id="app-1",
            workflow_name="ValueEngine",
            variables={"app_name": "Bounded App"},
        )

    assert "SECRET_POLICY_RESOLUTION_VALUE" not in str(exc_info.value)
    assert coll.update_filters == []


@pytest.mark.asyncio
async def test_context_replay_policy_resolution_failure_is_not_silenced(monkeypatch):
    manager = AG2PersistenceManager()
    coll = _FakeCollection(
        {
            "_id": "chat-1",
            "app_id": "app-1",
            "workflow_name": "ValueEngine",
            "app_name": "Bounded App",
        }
    )

    async def _fake_coll():
        return coll

    def _fail_policy(_workflow_name):
        raise RuntimeError("SECRET_POLICY_RESOLUTION_VALUE")

    monkeypatch.setattr(manager, "_coll", _fake_coll)
    monkeypatch.setattr(_persistence_mod, "_context_authority_policy_for_workflow", _fail_policy)

    with pytest.raises(ValueError, match=r"policy_unavailable workflow=ValueEngine op=replay") as exc_info:
        await manager.fetch_chat_session_extra_context(
            chat_id="chat-1",
            app_id="app-1",
            workflow_name="ValueEngine",
        )

    assert "SECRET_POLICY_RESOLUTION_VALUE" not in str(exc_info.value)


@pytest.mark.asyncio
async def test_context_replay_storage_failure_propagates(monkeypatch):
    manager = AG2PersistenceManager()
    storage_error = RuntimeError("find failed")
    coll = _FakeCollection({}, find_error=storage_error)

    async def _fake_coll():
        return coll

    monkeypatch.setattr(manager, "_coll", _fake_coll)

    with pytest.raises(RuntimeError, match="failed to fetch persisted workflow context") as exc_info:
        await manager.fetch_chat_session_extra_context(
            chat_id="chat-1",
            app_id="app-1",
            workflow_name="ValueEngine",
        )

    assert exc_info.value.__cause__ is storage_error


@pytest.mark.asyncio
async def test_context_update_storage_failure_propagates(
    monkeypatch,
    authentic_workflow_policies,
):
    manager = AG2PersistenceManager()
    coll = _FakeCollection(
        {"_id": "chat-1", "app_id": "app-1", "workflow_name": "ValueEngine"},
        update_error=RuntimeError("update failed"),
    )

    async def _fake_coll():
        return coll

    monkeypatch.setattr(manager, "_coll", _fake_coll)

    with pytest.raises(RuntimeError, match="update failed"):
        await manager.persist_context_variables(
            chat_id="chat-1",
            app_id="app-1",
            workflow_name="ValueEngine",
            variables={"app_name": "Bounded App"},
        )


@pytest.mark.parametrize(
    ("update_result", "message"),
    [
        (
            _FakeUpdateResult(0, matched_count=1, acknowledged=False),
            "write was not acknowledged",
        ),
        (
            _FakeUpdateResult(0, matched_count=0, acknowledged=True),
            "scoped session was not found",
        ),
    ],
)
@pytest.mark.asyncio
async def test_context_update_rejects_unacknowledged_or_unmatched_writes(
    monkeypatch,
    authentic_workflow_policies,
    update_result,
    message,
):
    manager = AG2PersistenceManager()
    coll = _FakeCollection(
        {"_id": "chat-1", "app_id": "app-1", "workflow_name": "ValueEngine"},
        update_result=update_result,
    )

    async def _fake_coll():
        return coll

    monkeypatch.setattr(manager, "_coll", _fake_coll)

    with pytest.raises(RuntimeError, match=message):
        await manager.persist_context_variables(
            chat_id="chat-1",
            app_id="app-1",
            workflow_name="ValueEngine",
            variables={"app_name": "Bounded App"},
        )


@pytest.mark.asyncio
async def test_context_update_accepts_matched_noop_write(
    monkeypatch,
    authentic_workflow_policies,
):
    manager = AG2PersistenceManager()
    coll = _FakeCollection(
        {"_id": "chat-1", "app_id": "app-1", "workflow_name": "ValueEngine"},
        update_result=_FakeUpdateResult(0, matched_count=1, acknowledged=True),
    )

    async def _fake_coll():
        return coll

    monkeypatch.setattr(manager, "_coll", _fake_coll)

    await manager.persist_context_variables(
        chat_id="chat-1",
        app_id="app-1",
        workflow_name="ValueEngine",
        variables={"app_name": "Bounded App"},
    )

    assert coll.update_filters[-1] == {
        "_id": "chat-1",
        "app_id": "app-1",
        "workflow_name": "ValueEngine",
    }


@pytest.mark.asyncio
async def test_required_chat_session_insert_failure_propagates(monkeypatch):
    manager = AG2PersistenceManager()
    insert_error = RuntimeError("insert failed")
    coll = _FakeCollection({}, insert_error=insert_error)

    async def _fake_coll():
        return coll

    monkeypatch.setattr(manager, "_coll", _fake_coll)

    with pytest.raises(RuntimeError, match="insert failed") as exc_info:
        await manager.create_chat_session(
            chat_id="chat-1",
            app_id="app-1",
            workflow_name="ValueEngine",
            user_id="user-1",
        )

    assert exc_info.value is insert_error


@pytest.mark.asyncio
async def test_required_chat_session_rejects_unacknowledged_insert(monkeypatch):
    manager = AG2PersistenceManager()
    coll = _FakeCollection({}, insert_result=_FakeInsertResult(acknowledged=False))

    async def _fake_coll():
        return coll

    monkeypatch.setattr(manager, "_coll", _fake_coll)

    with pytest.raises(RuntimeError, match="write was not acknowledged"):
        await manager.create_chat_session(
            chat_id="chat-1",
            app_id="app-1",
            workflow_name="ValueEngine",
            user_id="user-1",
        )


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
async def test_load_run_history_filters_hidden_control_and_structured_projection_events(monkeypatch):
    manager = AG2PersistenceManager()

    async def _fake_load_run_events(*, chat_id: str, app_id: str):
        assert chat_id == "chat-1"
        assert app_id == "app-1"
        return [
            TextInput("build this"),
            ModelResponse(
                ModelMessage(
                    "NEXT",
                    metadata={
                        "agent_name": "ValueInterviewAgent",
                        "source": "ag2_network_wal",
                    },
                ),
                model="mozaiks.runtime",
            ),
            ModelResponse(
                ModelMessage(
                    '{"app_name": "ContractorFlow CRM"}',
                    metadata={
                        "agent_name": "GapAnalysisAgent",
                        "source": "ag2_network_wal",
                    },
                ),
                model="mozaiks.runtime",
            ),
            ModelResponse(
                ModelMessage(
                    '```json\n{"app_name": "ContractorFlow CRM"}\n```',
                    metadata={
                        "agent_name": "GapAnalysisAgent",
                        "source": "ag2_network_wal",
                    },
                ),
                model="mozaiks.runtime",
            ),
            ModelResponse(
                ModelMessage(
                    "## Competitor Landscape\n\nUseful narrative.",
                    metadata={
                        "agent_name": "ResearchAgent",
                        "source": "ag2_network_wal",
                    },
                ),
                model="mozaiks.runtime",
            ),
            ModelResponse(
                ModelMessage(
                    "hidden trace",
                    metadata={
                        "agent_name": "TraceAgent",
                        "ui_visibility": "hidden",
                    },
                ),
                model="mozaiks.runtime",
            ),
        ]

    monkeypatch.setattr(manager, "load_run_events", _fake_load_run_events)

    history = await manager.load_run_history(chat_id="chat-1", app_id="app-1")

    assert [message["content"] for message in history] == [
        "build this",
        "## Competitor Landscape\n\nUseful narrative.",
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
        metadata={"source": "orchestrator.initial_message"},
    )

    assert captured["app_id"] == "app-1"
    assert captured["stream_id"] == manager._run_stream_id(chat_id="chat-1", app_id="app-1")
    event = captured["event"]
    assert isinstance(event, ModelResponse)
    assert event.content == "Tell me about your idea."
    assert event.metadata["agent_name"] == "ValueInterviewAgent"
    assert event.metadata["source"] == "orchestrator.initial_message"


@pytest.mark.asyncio
async def test_append_run_user_message_persists_text_input_to_ag2_stream(monkeypatch):
    manager = AG2PersistenceManager()
    captured: dict[str, object] = {}

    class _FakeStorage:
        def __init__(self, *, app_id: str):
            captured["app_id"] = app_id

        async def save_event(self, event, context):  # noqa: ANN001
            captured["event"] = event
            captured["stream_id"] = context.stream.id

    monkeypatch.setattr(_stream_storage_mod, "MongoAG2StreamStorage", _FakeStorage)

    await manager.append_run_user_message(
        chat_id="chat-1",
        app_id="app-1",
        content="launch demand maybe",
        metadata={"source": "workflow_user"},
    )

    assert captured["app_id"] == "app-1"
    assert captured["stream_id"] == manager._run_stream_id(chat_id="chat-1", app_id="app-1")
    event = captured["event"]
    assert isinstance(event, TextInput)
    assert event.content == "launch demand maybe"


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



@pytest.mark.asyncio
async def test_unloaded_workflow_fails_closed_instead_of_dropping_all_state(
    monkeypatch, authentic_workflow_policies
):
    """A workflow absent from this process must not silently lose persisted state."""

    manager = AG2PersistenceManager()
    doc = {"_id": "chat-1", "app_id": "app-1", "workflow_name": "NotARegisteredWorkflow"}
    coll = _FakeCollection(doc)

    async def _fake_coll():
        return coll

    monkeypatch.setattr(manager, "_coll", _fake_coll)

    with pytest.raises(ValueError, match="unresolved_declarations"):
        await manager.persist_context_variables(
            chat_id="chat-1",
            app_id="app-1",
            workflow_name="NotARegisteredWorkflow",
            variables={"interview_complete": True},
        )

    doc["interview_complete"] = True
    with pytest.raises(ValueError, match="unresolved_declarations"):
        await manager.fetch_chat_session_extra_context(
            chat_id="chat-1",
            app_id="app-1",
            workflow_name="NotARegisteredWorkflow",
        )
