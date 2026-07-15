from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from factory_app.app.modules.workspace_support.backend.service import WorkspaceSupportService
from mozaiksai.core.runtime.app.module_loader import ModuleLoader
from mozaiksai.core.runtime.composition.module_event_router import ModuleEventRouter
from mozaiksai.core.runtime.composition.module_executor import _validate_schema
from mozaiksai.core.runtime.persistence.migrations import load_data_migrations


class _FakeObjectId:
    def __init__(self, value="507f1f77bcf86cd799439011"):
        self.value = value

    def __str__(self):
        return self.value


class _FakeCursor:
    def __init__(self, rows):
        self._rows = list(rows)

    def __aiter__(self):
        self._iter = iter(self._rows)
        return self

    async def __anext__(self):
        try:
            return next(self._iter)
        except StopIteration as err:
            raise StopAsyncIteration from err


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
                if "$in" in value and row_value not in value["$in"]:
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
        if "app_id" in doc and doc["app_id"] != self.app_id:
            raise ValueError("document app_id cannot override context app_id")
        scoped_doc = {"app_id": self.app_id, **dict(doc)}
        self.inserted.append(scoped_doc)
        self.rows.append(scoped_doc)

    async def update_one(self, query, update, **kwargs):
        self.updates.append((self._scoped_query(query), update, kwargs))
        scoped_query = self._scoped_query(query)
        for row in self.rows:
            if self._matches(row, scoped_query):
                row.update(dict(update.get("$set") or {}))
                return SimpleNamespace(matched_count=1)
        if kwargs.get("upsert"):
            inserted = {**scoped_query, **dict(update.get("$setOnInsert") or {}), **dict(update.get("$set") or {})}
            self.rows.append(inserted)
            self.inserted.append(inserted)
            return SimpleNamespace(matched_count=0, upserted_id="upserted")
        return SimpleNamespace(matched_count=0)

    async def delete_one(self, query):
        scoped_query = self._scoped_query(query)
        for index, row in enumerate(self.rows):
            if self._matches(row, scoped_query):
                del self.rows[index]
                return SimpleNamespace(deleted_count=1)
        return SimpleNamespace(deleted_count=0)

    async def delete_many(self, query):
        scoped_query = self._scoped_query(query)
        before = len(self.rows)
        self.rows = [row for row in self.rows if not self._matches(row, scoped_query)]
        return SimpleNamespace(deleted_count=before - len(self.rows))

    def find(self, query, **_kwargs):
        raise AssertionError("workspace_support must use the canonical find_one/find_many persistence API")

    async def find_one(self, query, *_args, **_kwargs):
        scoped_query = self._scoped_query(query)
        for row in self.rows:
            if self._matches(row, scoped_query):
                return row
        return None

    async def find_many(self, query, *, limit=50, sort=None, **_kwargs):
        scoped_query = self._scoped_query(query)
        matched = [
            row
            for row in self.rows
            if self._matches(row, scoped_query)
        ]
        if sort:
            for key, direction in reversed(sort):
                matched.sort(key=lambda row: row.get(key) or "", reverse=int(direction) < 0)
        return matched[:limit]


class _FakePersistence:
    def __init__(self, app_id="app_1"):
        self.collections = {
            ("workspace_support", "requests"): _FakeCollection(app_id=app_id),
            ("workspace_support", "feedback"): _FakeCollection(app_id=app_id),
            ("messages", "threads"): _FakeCollection(app_id=app_id),
            ("messages", "messages"): _FakeCollection(app_id=app_id),
            ("messages", "thread_reads"): _FakeCollection(app_id=app_id),
        }

    def collection(self, module_id, entity_name):
        return self.collections[(module_id, entity_name)]


@pytest.mark.asyncio
async def test_create_support_request_creates_canonical_message_thread():
    emitted = []
    persistence = _FakePersistence()
    ctx = SimpleNamespace(
        app_id="app_1",
        user_id="user_1",
        persistence=persistence,
        emit=lambda event_type, payload: emitted.append((event_type, payload)),
    )
    service = WorkspaceSupportService()

    result = await service.create_support_request(
        ctx,
        message="Still seeing the issue",
        page_title="Checkout",
        severity="medium",
        app_id="checkout-app",
    )

    requests = persistence.collections[("workspace_support", "requests")]
    threads = persistence.collections[("messages", "threads")]
    messages = persistence.collections[("messages", "messages")]
    assert result["request_id"].startswith("sr_")
    assert result["app_id"] == "checkout-app"
    assert result["message_thread_id"].startswith("thr_")
    assert requests.inserted[0]["request_id"] == result["request_id"]
    assert requests.inserted[0]["subject_app_id"] == "checkout-app"
    assert requests.inserted[0]["last_message_by_role"] == "user"
    assert threads.inserted[0]["thread_type"] == "support"
    assert threads.inserted[0]["subject_app_id"] == "checkout-app"
    assert threads.inserted[0]["related_type"] == "workspace_support.request"
    assert messages.inserted[0]["body"] == "Still seeing the issue"
    assert messages.inserted[0]["sender_role"] == "user"
    assert [event_type for event_type, _ in emitted] == [
        "domain.messages.thread_created",
        "domain.messages.message_sent",
        "domain.workspace_support.request_created",
    ]


@pytest.mark.asyncio
async def test_create_support_request_seeds_message_thread_with_transcript():
    persistence = _FakePersistence()
    ctx = SimpleNamespace(
        app_id="app_1",
        user_id="user_1",
        persistence=persistence,
        emit=lambda *_args, **_kwargs: None,
    )
    service = WorkspaceSupportService()

    result = await service.create_support_request(
        ctx,
        message="human help",
        page_title="Widget",
        app_id="customer-app",
        conversation_transcript=[
            {"role": "user", "content": "hey"},
            {"role": "assistant", "content": "Hi there! How can I assist you today?"},
            {"role": "user", "content": "human help"},
        ],
    )

    messages = persistence.collections[("messages", "messages")].inserted
    assert result["message_thread_id"].startswith("thr_")
    assert [(item["sender_role"], item["body"]) for item in messages] == [
        ("user", "hey"),
        ("assistant", "Hi there! How can I assist you today?"),
        ("user", "human help"),
    ]
    assert persistence.collections[("workspace_support", "requests")].inserted[0]["conversation_transcript_count"] == 3


@pytest.mark.asyncio
async def test_add_support_message_uses_message_thread_and_targets_ticket_owner():
    emitted = []
    persistence = _FakePersistence()
    persistence.collections[("workspace_support", "requests")].rows.append(
        {"request_id": "sr_1", "app_id": "app_1", "subject_app_id": "customer-app", "user_id": "ticket_owner"}
    )
    ctx = SimpleNamespace(
        app_id="app_1",
        user_id="operator_1",
        persistence=persistence,
        emit=lambda event_type, payload: emitted.append((event_type, payload)),
    )
    service = WorkspaceSupportService()

    result = await service.add_support_message(
        ctx,
        request_id="sr_1",
        message="We fixed this",
        sender_role="operator",
    )

    assert result["success"] is True
    assert result["message_id"].startswith("msg_")
    assert persistence.collections[("messages", "messages")].inserted[0]["sender_role"] == "operator"
    message_event = next(payload for event_type, payload in emitted if event_type == "domain.messages.message_sent")
    assert message_event["recipient_ids"] == ["ticket_owner"]
    support_event = next(payload for event_type, payload in emitted if event_type == "domain.workspace_support.message_added")
    assert support_event["ticket_user_id"] == "ticket_owner"
    assert support_event["app_id"] == "customer-app"
    assert persistence.collections[("workspace_support", "requests")].rows[0]["last_message_by_role"] == "operator"
    assert persistence.collections[("workspace_support", "requests")].rows[0]["last_operator_response_at"]


@pytest.mark.asyncio
async def test_user_reply_requires_ticket_owner_or_support_manager():
    persistence = _FakePersistence()
    persistence.collections[("workspace_support", "requests")].rows.append(
        {"request_id": "sr_1", "app_id": "app_1", "subject_app_id": "customer-app", "user_id": "ticket_owner"}
    )
    service = WorkspaceSupportService()

    outsider_ctx = SimpleNamespace(
        app_id="app_1",
        user_id="other_user",
        permissions=["access_as_user"],
        persistence=persistence,
        emit=lambda *_args, **_kwargs: None,
    )
    with pytest.raises(PermissionError):
        await service.add_support_message(
            outsider_ctx,
            request_id="sr_1",
            message="Trying to write to another user's ticket",
            sender_role="user",
        )

    owner_ctx = SimpleNamespace(
        app_id="app_1",
        user_id="ticket_owner",
        permissions=["access_as_user"],
        persistence=persistence,
        emit=lambda *_args, **_kwargs: None,
    )
    result = await service.add_support_message(
        owner_ctx,
        request_id="sr_1",
        message="Here is more detail",
        sender_role="user",
    )
    assert result["success"] is True


@pytest.mark.asyncio
async def test_operator_reply_requires_support_manage_permission():
    persistence = _FakePersistence()
    persistence.collections[("workspace_support", "requests")].rows.append(
        {"request_id": "sr_1", "app_id": "app_1", "subject_app_id": "customer-app", "user_id": "ticket_owner"}
    )
    service = WorkspaceSupportService()

    ctx = SimpleNamespace(
        app_id="app_1",
        user_id="operator_1",
        permissions=["access_as_user", "workspace_support.read"],
        persistence=persistence,
        emit=lambda *_args, **_kwargs: None,
    )
    with pytest.raises(PermissionError):
        await service.add_support_message(
            ctx,
            request_id="sr_1",
            message="I should not be able to reply as support yet",
            sender_role="operator",
        )


@pytest.mark.asyncio
async def test_update_support_request_status_resolves_request_and_thread():
    emitted = []
    persistence = _FakePersistence()
    persistence.collections[("workspace_support", "requests")].rows.append(
        {
            "request_id": "sr_1",
            "app_id": "app_1",
            "subject_app_id": "customer-app",
            "user_id": "ticket_owner",
            "status": "open",
            "message_thread_id": "thr_1",
        }
    )
    persistence.collections[("messages", "threads")].rows.append(
        {"thread_id": "thr_1", "app_id": "app_1", "status": "open"}
    )
    ctx = SimpleNamespace(
        app_id="app_1",
        user_id="operator_1",
        permissions=["access_as_user", "workspace_support.manage"],
        persistence=persistence,
        emit=lambda event_type, payload: emitted.append((event_type, payload)),
    )
    service = WorkspaceSupportService()

    result = await service.update_support_request_status(ctx, request_id="sr_1", status="resolved")

    assert result["success"] is True
    assert result["status"] == "resolved"
    assert persistence.collections[("workspace_support", "requests")].rows[0]["status"] == "resolved"
    assert persistence.collections[("workspace_support", "requests")].rows[0]["resolved_by"] == "operator_1"
    assert persistence.collections[("messages", "threads")].rows[0]["status"] == "resolved"
    assert emitted[-1][0] == "domain.workspace_support.request_status_changed"


@pytest.mark.asyncio
async def test_ticket_owner_can_delete_own_support_request():
    persistence = _FakePersistence()
    persistence.collections[("workspace_support", "requests")].rows.append(
        {
            "request_id": "sr_owner",
            "app_id": "app_1",
            "subject_app_id": "customer-app",
            "user_id": "ticket_owner",
            "message_thread_id": "thr_owner",
        }
    )
    persistence.collections[("messages", "threads")].rows.append(
        {"thread_id": "thr_owner", "app_id": "app_1"}
    )
    ctx = SimpleNamespace(
        app_id="app_1",
        user_id="ticket_owner",
        permissions=["access_as_user"],
        persistence=persistence,
        emit=lambda *_args, **_kwargs: None,
    )
    service = WorkspaceSupportService()

    result = await service.delete_support_request(ctx, request_id="sr_owner")

    assert result["success"] is True
    assert persistence.collections[("workspace_support", "requests")].rows == []
    assert persistence.collections[("messages", "threads")].rows == []


@pytest.mark.asyncio
async def test_delete_support_request_removes_request_and_message_thread():
    emitted = []
    persistence = _FakePersistence()
    persistence.collections[("workspace_support", "requests")].rows.append(
        {
            "request_id": "sr_1",
            "app_id": "app_1",
            "subject_app_id": "customer-app",
            "user_id": "ticket_owner",
            "message_thread_id": "thr_1",
        }
    )
    persistence.collections[("messages", "threads")].rows.append(
        {"thread_id": "thr_1", "app_id": "app_1"}
    )
    persistence.collections[("messages", "messages")].rows.extend(
        [
            {"message_id": "msg_1", "thread_id": "thr_1", "app_id": "app_1"},
            {"message_id": "msg_2", "thread_id": "thr_1", "app_id": "app_1"},
        ]
    )
    persistence.collections[("messages", "thread_reads")].rows.append(
        {"thread_id": "thr_1", "user_id": "ticket_owner", "app_id": "app_1"}
    )
    ctx = SimpleNamespace(
        app_id="app_1",
        user_id="operator_1",
        permissions=["access_as_user", "workspace_support.manage"],
        persistence=persistence,
        emit=lambda event_type, payload: emitted.append((event_type, payload)),
    )
    service = WorkspaceSupportService()

    result = await service.delete_support_request(ctx, request_id="sr_1")

    assert result["success"] is True
    assert result["deleted_requests"] == 1
    assert result["deleted_threads"] == 1
    assert result["deleted_messages"] == 2
    assert result["deleted_reads"] == 1
    assert persistence.collections[("workspace_support", "requests")].rows == []
    assert persistence.collections[("messages", "threads")].rows == []
    assert persistence.collections[("messages", "messages")].rows == []
    assert persistence.collections[("messages", "thread_reads")].rows == []
    assert emitted[-1][0] == "domain.workspace_support.request_deleted"


@pytest.mark.asyncio
async def test_delete_support_request_requires_manage_permission():
    persistence = _FakePersistence()
    persistence.collections[("workspace_support", "requests")].rows.append(
        {"request_id": "sr_1", "app_id": "app_1", "message_thread_id": "thr_1"}
    )
    ctx = SimpleNamespace(
        app_id="app_1",
        user_id="operator_1",
        permissions=["access_as_user", "workspace_support.read"],
        persistence=persistence,
        emit=lambda *_args, **_kwargs: None,
    )
    service = WorkspaceSupportService()

    with pytest.raises(PermissionError):
        await service.delete_support_request(ctx, request_id="sr_1")


@pytest.mark.asyncio
async def test_app_support_queue_requires_read_permission():
    persistence = _FakePersistence()
    service = WorkspaceSupportService()
    ctx = SimpleNamespace(
        app_id="app_1",
        user_id="user_1",
        permissions=["access_as_user"],
        persistence=persistence,
    )

    with pytest.raises(PermissionError):
        await service.list_support_requests(ctx, status="all", scope="app")


@pytest.mark.asyncio
async def test_message_notification_targets_ticket_owner():
    loaded = ModuleLoader("factory_app/app").load("messages")
    stored = []

    async def capture_notification(record):
        stored.append(record)

    router = ModuleEventRouter([loaded], notification_store=capture_notification)

    await router.handle_event(
        "domain.messages.message_sent",
        {
            "id": "evt_message_sent",
            "type": "domain.messages.message_sent",
            "tenant": {"app_id": "app_1", "tenant_id": "tenant_1"},
            "payload": {
                "thread_id": "thr_1",
                "message_id": "msg_1",
                "sender_role": "operator",
                "sender_id": "operator_1",
                "recipient_ids": ["ticket_owner"],
                "body_preview": "We fixed this for you.",
                "sent_at": "2026-01-01T00:00:00Z",
                "related_type": "workspace_support.request",
                "related_id": "sr_1",
            },
        },
    )

    assert stored[0]["rule_id"] == "message_sent"
    assert stored[0]["channels"] == ["in_app"]
    assert stored[0]["audience"]["user_ids"] == ["ticket_owner"]
    assert stored[0]["title"] == "New message"
    assert stored[0]["context"]["related_id"] == "sr_1"


@pytest.mark.asyncio
async def test_workspace_support_notifications_target_support_operators():
    loaded = ModuleLoader("factory_app/app").load("workspace_support")
    stored = []

    async def capture_notification(record):
        stored.append(record)

    router = ModuleEventRouter([loaded], notification_store=capture_notification)

    await router.handle_event(
        "domain.workspace_support.request_created",
        {
            "id": "evt_support_created",
            "type": "domain.workspace_support.request_created",
            "tenant": {"app_id": "app_1", "tenant_id": "tenant_1"},
            "payload": {
                "request_id": "sr_1",
                "app_id": "customer-app",
                "subject_app_id": "customer-app",
                "severity": "medium",
                "message": "Checkout is failing",
                "page_title": "Checkout",
                "message_thread_id": "thr_1",
            },
        },
    )

    assert stored[0]["rule_id"] == "workspace_support.request_created"
    assert stored[0]["audience"]["permissions"] == ["workspace_support.read"]
    assert stored[0]["title"] == "New support request"
    assert stored[0]["body"] == "Checkout is failing"
    assert stored[0]["context"]["subject_app_id"] == "customer-app"


@pytest.mark.asyncio
async def test_workspace_support_user_reply_notification_skips_operator_replies():
    loaded = ModuleLoader("factory_app/app").load("workspace_support")
    stored = []

    async def capture_notification(record):
        stored.append(record)

    router = ModuleEventRouter([loaded], notification_store=capture_notification)

    base = {
        "id": "evt_support_message",
        "type": "domain.workspace_support.message_added",
        "tenant": {"app_id": "app_1", "tenant_id": "tenant_1"},
        "payload": {
            "request_id": "sr_1",
            "message_id": "msg_1",
            "app_id": "customer-app",
            "subject_app_id": "customer-app",
            "sender_id": "ticket_owner",
            "ticket_user_id": "ticket_owner",
            "message_preview": "Any update?",
            "message_thread_id": "thr_1",
        },
    }
    await router.handle_event(
        "domain.workspace_support.message_added",
        {**base, "payload": {**base["payload"], "sender_role": "operator"}},
    )
    assert stored == []

    await router.handle_event(
        "domain.workspace_support.message_added",
        {**base, "payload": {**base["payload"], "sender_role": "user"}},
    )
    assert stored[0]["rule_id"] == "workspace_support.user_reply"
    assert stored[0]["audience"]["permissions"] == ["workspace_support.read"]


@pytest.mark.asyncio
async def test_list_support_requests_defaults_to_current_user_scope():
    persistence = _FakePersistence()
    persistence.collections[("workspace_support", "requests")].rows.extend(
        [
            {
                "request_id": "sr_user_1",
                "app_id": "app_1",
                "subject_app_id": "app_1",
                "user_id": "operator_1",
                "status": "open",
                "created_at": "2026-01-01T00:00:00Z",
            },
            {
                "request_id": "sr_user_2",
                "app_id": "app_1",
                "subject_app_id": "app_1",
                "user_id": "other_user",
                "status": "open",
                "created_at": "2026-01-01T00:00:00Z",
            },
        ]
    )
    ctx = SimpleNamespace(app_id="app_1", user_id="operator_1", persistence=persistence)
    service = WorkspaceSupportService()

    result = await service.list_support_requests(ctx, status="all")

    assert [item["request_id"] for item in result["requests"]] == ["sr_user_1"]


@pytest.mark.asyncio
async def test_list_support_requests_user_scope_can_filter_subject_app():
    persistence = _FakePersistence()
    persistence.collections[("workspace_support", "requests")].rows.extend(
        [
            {
                "request_id": "sr_user_app_1",
                "app_id": "app_1",
                "subject_app_id": "customer_app_1",
                "user_id": "user_1",
                "status": "open",
                "created_at": "2026-01-01T00:00:00Z",
            },
            {
                "request_id": "sr_user_app_2",
                "app_id": "app_1",
                "subject_app_id": "customer_app_2",
                "user_id": "user_1",
                "status": "open",
                "created_at": "2026-01-01T00:00:00Z",
            },
        ]
    )
    ctx = SimpleNamespace(app_id="app_1", user_id="user_1", persistence=persistence)
    service = WorkspaceSupportService()

    result = await service.list_support_requests(ctx, status="all", app_id="customer_app_1")

    assert [item["request_id"] for item in result["requests"]] == ["sr_user_app_1"]


@pytest.mark.asyncio
async def test_list_support_requests_serializes_mongo_documents_for_http_response():
    persistence = _FakePersistence()
    persistence.collections[("workspace_support", "requests")].rows.append(
        {
            "_id": _FakeObjectId(),
            "request_id": "sr_oid",
            "app_id": "app_1",
            "subject_app_id": "app_1",
            "user_id": "operator_1",
            "status": "open",
            "created_at": "2026-01-01T00:00:00Z",
            "metadata": {"raw_id": _FakeObjectId("nested-object")},
        }
    )
    ctx = SimpleNamespace(
        app_id="app_1",
        user_id="operator_1",
        permissions=["access_as_user"],
        persistence=persistence,
    )
    service = WorkspaceSupportService()

    result = await service.list_support_requests(ctx, status="all")

    request = result["requests"][0]
    assert "_id" not in request
    assert request["metadata"]["raw_id"] == "nested-object"
    assert request["app_id"] == "app_1"
    json.dumps(result)


@pytest.mark.asyncio
async def test_list_support_requests_supports_current_app_scope():
    persistence = _FakePersistence()
    persistence.collections[("workspace_support", "requests")].rows.extend(
        [
            {"request_id": "sr_app_1", "app_id": "app_1", "subject_app_id": "app_1", "status": "open", "created_at": "2026-01-01T00:00:00Z"},
            {"request_id": "sr_app_2", "app_id": "app_1", "subject_app_id": "app_2", "status": "open", "created_at": "2026-01-01T00:00:00Z"},
        ]
    )
    ctx = SimpleNamespace(app_id="app_1", user_id="operator_1", persistence=persistence)
    service = WorkspaceSupportService()

    result = await service.list_support_requests(ctx, status="all", scope="app")

    assert [item["request_id"] for item in result["requests"]] == ["sr_app_1"]


def test_workspace_support_action_schemas_accept_frontend_support_payloads():
    app_root = Path(__file__).resolve().parents[1] / "factory_app" / "app"
    loaded = ModuleLoader(str(app_root)).load("workspace_support")
    schemas = loaded.action_schemas_map

    create_input = schemas["create_support_request"]["input"]
    assert _validate_schema(
        {
            "message": "Can I speak to a person?",
            "page_url": None,
            "page_title": None,
            "app_id": None,
            "severity": "low",
            "conversation_transcript": [
                {"role": "user", "content": "hello"},
                {"role": "assistant", "content": "How can I help?"},
            ],
        },
        create_input,
    ) is None
    assert _validate_schema(
        {
            "request_id": "sr_1",
            "app_id": "app_1",
            "subject_app_id": "app_1",
            "status": "open",
            "created_at": "2026-01-01T00:00:00Z",
            "message_thread_id": None,
        },
        schemas["create_support_request"]["output"],
    ) is None
    assert _validate_schema({"status": "all", "app_id": None}, schemas["list_support_requests"]["input"]) is None
    assert _validate_schema(
        {"request_id": "sr_1", "status": "resolved"},
        schemas["update_support_request_status"]["input"],
    ) is None
    assert _validate_schema(
        {
            "success": True,
            "request_id": "sr_1",
            "status": "resolved",
            "updated_at": "2026-01-01T00:00:00Z",
            "message_thread_id": "thr_1",
        },
        schemas["update_support_request_status"]["output"],
    ) is None
    assert _validate_schema(
        {"rating": 0, "session_id": None, "workflow_name": None, "app_id": None},
        schemas["submit_session_feedback"]["input"],
    ) is None


def test_workspace_support_data_migration_uses_runtime_contract():
    app_root = Path(__file__).resolve().parents[1] / "factory_app" / "app"

    migrations = load_data_migrations(app_root)
    migration = next(
        item for item in migrations
        if item["migration_id"] == "workspace_support_001_collections"
    )

    assert migration["schema_version"] == "mozaiks.data_migration.v1"
    assert [operation["type"] for operation in migration["operations"]].count("ensure_collection") == 2
    assert all(operation["type"] in {"ensure_collection", "ensure_index"} for operation in migration["operations"])


@pytest.mark.asyncio
async def test_list_support_requests_embeds_thread_messages_with_canonical_persistence_api():
    persistence = _FakePersistence()
    thread_id = "thr_1"
    persistence.collections[("workspace_support", "requests")].rows.append(
        {
            "request_id": "sr_app_1",
            "app_id": "app_1",
            "subject_app_id": "app_1",
            "status": "open",
            "created_at": "2026-01-01T00:00:00Z",
            "message_thread_id": thread_id,
        }
    )
    persistence.collections[("messages", "threads")].rows.append(
        {
            "thread_id": thread_id,
            "app_id": "app_1",
            "participant_ids": ["user_1"],
            "status": "open",
            "updated_at": "2026-01-01T00:02:00Z",
        }
    )
    persistence.collections[("messages", "messages")].rows.extend(
        [
            {
                "message_id": "msg_1",
                "thread_id": thread_id,
                "app_id": "app_1",
                "sender_role": "user",
                "body": "I need help",
                "is_deleted": False,
                "created_at": "2026-01-01T00:01:00Z",
            },
            {
                "message_id": "msg_2",
                "thread_id": thread_id,
                "app_id": "app_1",
                "sender_role": "operator",
                "body": "I can help",
                "is_deleted": False,
                "created_at": "2026-01-01T00:02:00Z",
            },
        ]
    )
    ctx = SimpleNamespace(app_id="app_1", user_id="operator_1", persistence=persistence)
    service = WorkspaceSupportService()

    result = await service.list_support_requests(ctx, status="all", scope="app")

    assert result["requests"][0]["messages"] == [
        {"role": "user", "content": "I need help", "senderLabel": None, "sentAt": "2026-01-01T00:01:00Z"},
        {"role": "operator", "content": "I can help", "senderLabel": "Support", "sentAt": "2026-01-01T00:02:00Z"},
    ]


@pytest.mark.asyncio
async def test_workspace_scope_remains_bound_to_current_persistence_context():
    persistence = _FakePersistence()
    persistence.collections[("workspace_support", "requests")].rows.extend(
        [
            {"request_id": "sr_app_1", "app_id": "app_1", "subject_app_id": "app_1", "status": "open", "created_at": "2026-01-01T00:00:00Z"},
            {"request_id": "sr_app_2", "app_id": "app_1", "subject_app_id": "app_2", "status": "resolved", "created_at": "2026-01-01T00:00:00Z"},
        ]
    )
    ctx = SimpleNamespace(app_id="app_1", user_id="operator_1", persistence=persistence)
    service = WorkspaceSupportService()

    result = await service.list_support_requests(ctx, status="all", scope="workspace")

    assert [item["request_id"] for item in result["requests"]] == ["sr_app_1", "sr_app_2"]


@pytest.mark.asyncio
async def test_app_id_filter_does_not_override_persistence_context_scope():
    persistence = _FakePersistence(app_id="app_2")
    persistence.collections[("workspace_support", "requests")].rows.extend(
        [
            {"request_id": "sr_app_1", "app_id": "app_2", "subject_app_id": "app_1", "status": "open", "created_at": "2026-01-01T00:00:00Z"},
            {"request_id": "sr_app_2", "app_id": "app_2", "subject_app_id": "app_2", "status": "open", "created_at": "2026-01-01T00:00:00Z"},
        ]
    )
    ctx = SimpleNamespace(app_id="app_2", user_id="operator_1", persistence=persistence)
    service = WorkspaceSupportService()

    result = await service.list_support_requests(ctx, status="all", scope="workspace", app_id="app_1")

    assert [item["request_id"] for item in result["requests"]] == ["sr_app_1"]
