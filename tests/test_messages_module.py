from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from factory_app.app.modules.messages.backend.service import MessageService
from mozaiksai.core.runtime.app.module_loader import ModuleLoader
from mozaiksai.core.runtime.composition.module_event_router import ModuleEventRouter
from mozaiksai.core.runtime.composition.module_executor import ModuleExecutor, ModuleRequest
from mozaiksai.core.runtime.persistence.adapter import PersistencePrincipal
from tests.module_authority_test_helpers import enforce_authority
from tests.test_messaging_pack_persistence_composition import _DocumentMongoClient

FACTORY_APP = Path(__file__).resolve().parents[1] / "factory_app" / "app"


class _FakeCollection:
    def __init__(self, rows=None, *, app_id="app_1"):
        self.rows = list(rows or [])
        self.inserted = []
        self.updates = []
        self.app_id = app_id

    def _scoped_query(self, query):
        if "app_id" in query:
            raise ValueError("extra filters cannot override app_id")
        return {"app_id": self.app_id, **query}

    @staticmethod
    def _matches(row, query):
        for key, value in query.items():
            row_value = row.get(key)
            if isinstance(value, dict):
                if "$ne" in value and row_value == value["$ne"]:
                    return False
                continue
            if isinstance(row_value, list):
                if value not in row_value:
                    return False
                continue
            if row_value != value:
                return False
        return True

    async def insert_one(self, doc):
        scoped_doc = {"app_id": self.app_id, **dict(doc)}
        self.inserted.append(scoped_doc)
        self.rows.append(scoped_doc)

    async def find_one(self, query, *_args, **_kwargs):
        scoped_query = self._scoped_query(query)
        for row in self.rows:
            if self._matches(row, scoped_query):
                return row
        return None

    async def find_many(self, query, *, limit=50, sort=None, **_kwargs):
        scoped_query = self._scoped_query(query)
        rows = [row for row in self.rows if self._matches(row, scoped_query)]
        if sort:
            for key, direction in reversed(sort):
                rows.sort(key=lambda row: row.get(key) or "", reverse=int(direction) < 0)
        return rows[:limit]

    async def update_one(self, query, update, **kwargs):
        scoped_query = self._scoped_query(query)
        self.updates.append((scoped_query, update, kwargs))
        for row in self.rows:
            if self._matches(row, scoped_query):
                row.update(dict(update.get("$set") or {}))
                return SimpleNamespace(matched_count=1)
        if kwargs.get("upsert"):
            inserted = {**scoped_query, **dict(update.get("$set") or {})}
            self.rows.append(inserted)
            return SimpleNamespace(matched_count=0, upserted_id="upserted")
        return SimpleNamespace(matched_count=0)


class _FakePersistence:
    def __init__(self, principal=None):
        self.principal = principal
        self.collections = {
            ("messages", "threads"): _FakeCollection(),
            ("messages", "messages"): _FakeCollection(),
            ("messages", "thread_reads"): _FakeCollection(),
        }

    def collection(self, module_id, entity_name):
        return self.collections[(module_id, entity_name)]


@pytest.mark.asyncio
async def test_messages_create_thread_and_send_message_emit_domain_events():
    emitted = []
    persistence = _FakePersistence()
    ctx = SimpleNamespace(
        app_id="app_1",
        user_id="user_1",
        persistence=persistence,
        emit=lambda event_type, payload: emitted.append((event_type, payload)),
    )
    service = MessageService()

    created = await service.create_thread(
        ctx,
        title="Support",
        participant_ids=["user_2"],
        thread_type="support",
        related_type="workspace_support.request",
        related_id="sr_1",
        allow_support_thread=True,
    )
    sent = await service.send_message(
        ctx, thread_id=created["thread"]["thread_id"], body="Hello", allow_support_thread=True
    )

    assert created["thread"]["participant_ids"] == ["user_1", "user_2"]
    assert sent["success"] is True
    assert created["thread"]["scope_type"] == "app"
    assert created["thread"]["subject_app_id"] == "app_1"
    assert [event_type for event_type, _ in emitted] == [
        "domain.messages.thread_created",
        "domain.messages.message_sent",
    ]
    assert emitted[1][1]["recipient_ids"] == ["user_2"]
    assert emitted[1][1]["subject_app_id"] == "app_1"


@pytest.mark.asyncio
async def test_message_thread_lookup_requires_authorized_scope():
    persistence = _FakePersistence()
    persistence.collections[("messages", "threads")].rows.append(
        {
            "thread_id": "thr_cross_scope",
            "app_id": "app_1",
            "scope_type": "app",
            "scope_id": "other_app",
            "participant_ids": ["user_1"],
            "status": "open",
        }
    )
    persistence.collections[("messages", "messages")].rows.append(
        {
            "message_id": "msg_cross_scope",
            "thread_id": "thr_cross_scope",
            "app_id": "app_1",
            "sender_role": "user",
            "body": "secret from another app scope",
            "is_deleted": False,
        }
    )
    ctx = SimpleNamespace(app_id="app_1", user_id="user_1", persistence=persistence)
    service = MessageService()

    fetched = await service.get_thread(ctx, thread_id="thr_cross_scope")
    sent = await service.send_message(ctx, thread_id="thr_cross_scope", body="hello")
    read = await service.mark_thread_read(ctx, thread_id="thr_cross_scope")

    assert fetched == {"thread": None, "messages": [], "error": "thread not found"}
    assert sent == {"success": False, "error": "thread not found"}
    assert read == {"success": False, "error": "thread not found"}


@pytest.mark.asyncio
async def test_workspace_scope_thread_can_be_messaged_from_current_workspace():
    emitted = []
    persistence = _FakePersistence(PersistencePrincipal("user_1", "workspace_1"))
    ctx = SimpleNamespace(
        app_id="app_1",
        workspace_id="workspace_1",
        user_id="user_1",
        persistence=persistence,
        emit=lambda event_type, payload: emitted.append((event_type, payload)),
    )
    service = MessageService()
    created = await service.create_thread(
        ctx,
        participant_ids=["user_2"],
        thread_type="direct",
        scope_type="workspace",
    )

    sent = await service.send_message(ctx, thread_id=created["thread"]["thread_id"], body="Workspace hello")
    fetched = await service.get_thread(ctx, thread_id=created["thread"]["thread_id"])
    read = await service.mark_thread_read(ctx, thread_id=created["thread"]["thread_id"])

    assert created["thread"]["scope_type"] == "workspace"
    assert created["thread"]["scope_id"] == "workspace_1"
    assert sent["success"] is True
    assert fetched["messages"][0]["body"] == "Workspace hello"
    assert read["success"] is True
    assert persistence.collections[("messages", "thread_reads")].rows[0]["scope_id"] == "workspace_1"


@pytest.mark.asyncio
async def test_create_thread_rejects_scope_id_outside_current_context():
    ctx = SimpleNamespace(
        app_id="app_1",
        workspace_id="workspace_1",
        user_id="user_1",
        persistence=_FakePersistence(PersistencePrincipal("user_1", "workspace_1")),
    )

    with pytest.raises(PermissionError, match="app message scope"):
        await MessageService().create_thread(ctx, scope_type="app", scope_id="other_app")

    with pytest.raises(PermissionError, match="workspace message scope"):
        await MessageService().create_thread(ctx, scope_type="workspace", scope_id="other_workspace")


def test_active_messages_module_contract_loads():
    loaded = ModuleLoader("factory_app/app").load("messages")

    assert loaded.name == "messages"
    schemas = loaded.action_schemas_map
    assert "support" not in schemas["create_thread"]["input"]["properties"]["thread_type"]["enum"]
    assert "support" not in schemas["list_threads"]["input"]["properties"]["thread_type"]["enum"]
    assert "allow_support_thread" not in schemas["get_thread"]["input"].get("properties", {})
    assert "domain.messages.message_sent" in loaded.manifests.events.event_types
    assert loaded.manifests.notifications.notifications[0].audience.user_id_field == "recipient_ids"


@pytest.mark.asyncio
@pytest.mark.parametrize("scope_type", ["app", "workspace"])
async def test_plain_direct_thread_events_pass_the_executor_and_notify_the_recipient(scope_type):
    """A thread with no related record (and, in workspace scope, no subject app) emits nulls.

    The executor validates each emitted event against contracts/events.yaml; a rejected
    event never reaches the router, so the recipient would get no notification.
    """
    loaded = ModuleLoader(str(FACTORY_APP)).load("messages")
    stored = []

    async def store(record):
        stored.append(record)

    router = ModuleEventRouter([loaded], notification_store=store)
    dispatched = []

    async def emit(event_type, envelope):
        dispatched.append((event_type, envelope["payload"]))
        await router.handle_event(event_type, envelope)

    executor = ModuleExecutor(
        event_emitter=emit, persistence_client=_DocumentMongoClient(), persistence_database="factory_messages_test",
    )
    executor.register_loaded_module(loaded)

    async def dispatch(user_id, action, **params):
        result = await executor.execute(ModuleRequest(
            module="messages", action=action, params=params, app_id="app_1", user_id=user_id,
            workspace_id="workspace_1", authority=enforce_authority("messages.read", "messages.write", actor_id=user_id),
            persistence_principal=PersistencePrincipal(user_id=user_id, workspace_id="workspace_1"),
        ))
        assert result.success, result
        assert list(result.rejected_events) == []
        return result.data

    created = await dispatch("alice", "create_thread", participant_ids=["bob"], thread_type="direct",
                             scope_type=scope_type)
    thread_id = created["thread"]["thread_id"]
    sent = await dispatch("alice", "send_message", thread_id=thread_id, body="hello bob")
    assert sent["success"] is True

    (_, thread_created), (_, message_sent) = dispatched
    nulls = ["related_type", "related_id", *(["subject_app_id"] if scope_type == "workspace" else [])]
    for payload in (thread_created, message_sent):
        assert [field for field in nulls if payload[field] is None] == nulls
    assert [event_type for event_type, _ in dispatched] == [
        "domain.messages.thread_created", "domain.messages.message_sent",
    ]
    (notification,) = stored
    assert (notification["audience"]["user_ids"], notification["context"]["thread_id"]) == (["bob"], thread_id)
