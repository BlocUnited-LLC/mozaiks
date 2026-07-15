from __future__ import annotations

from types import SimpleNamespace

import pytest

from factory_app.app.modules.messages.backend.service import MessageService
from mozaiksai.core.runtime.app.module_loader import ModuleLoader


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
    def __init__(self):
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
    )
    sent = await service.send_message(ctx, thread_id=created["thread"]["thread_id"], body="Hello")

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


def test_active_messages_module_contract_loads():
    loaded = ModuleLoader("factory_app/app").load("messages")

    assert loaded.name == "messages"
    assert "domain.messages.message_sent" in loaded.manifests.events.event_types
    assert loaded.manifests.notifications.notifications[0].audience.user_id_field == "recipient_ids"
