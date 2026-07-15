from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

BACKEND = (
    Path(__file__).parent.parent
    / "factory_app"
    / "build_context"
    / "messaging"
    / "templates"
    / "modules"
    / "messages"
    / "backend"
)
PACKAGE = "tests.messaging_template_backend"


def _load_backend_module(name: str):
    if PACKAGE not in sys.modules:
        package = ModuleType(PACKAGE)
        package.__path__ = [str(BACKEND)]
        sys.modules[PACKAGE] = package

    path = BACKEND / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"{PACKAGE}.{name}", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[f"{PACKAGE}.{name}"] = module
    spec.loader.exec_module(module)
    return module


for _name in ["schemas", "policy", "repo"]:
    _load_backend_module(_name)

MessageService = _load_backend_module("service").MessageService


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
    def __init__(self, app_id="app_1"):
        self.collections = {
            ("messages", "threads"): _FakeCollection(app_id=app_id),
            ("messages", "messages"): _FakeCollection(app_id=app_id),
            ("messages", "thread_reads"): _FakeCollection(app_id=app_id),
        }

    def collection(self, module_id, entity_name):
        return self.collections[(module_id, entity_name)]


def _ctx(user_id="user_1", app_id="app_1", workspace_id="workspace_1"):
    emitted = []

    async def emit(event_type, payload):
        emitted.append((event_type, payload))

    return SimpleNamespace(
        app_id=app_id,
        workspace_id=workspace_id,
        user_id=user_id,
        persistence=_FakePersistence(app_id=app_id),
        emit=emit,
        emitted=emitted,
    )


@pytest.mark.asyncio
async def test_create_thread_persists_scope_and_subject_app_id() -> None:
    ctx = _ctx()
    service = MessageService()

    result = await service.create_thread(
        ctx,
        title="Support",
        participant_ids=["user_2"],
        thread_type="support",
        scope_type="app",
        subject_app_id="app_1",
        related_type="support.request",
        related_id="sr_1",
    )

    thread = result["thread"]
    assert result["success"] is True
    assert thread["scope_type"] == "app"
    assert thread["scope_id"] == "app_1"
    assert thread["subject_app_id"] == "app_1"
    assert thread["participant_ids"] == ["user_1", "user_2"]
    assert ctx.emitted[0][0] == "domain.messages.thread_created"
    assert ctx.emitted[0][1]["subject_app_id"] == "app_1"


@pytest.mark.asyncio
async def test_send_message_emits_recipient_notification_payload() -> None:
    ctx = _ctx()
    service = MessageService()
    created = await service.create_thread(ctx, participant_ids=["user_2"], thread_type="support")

    result = await service.send_message(ctx, thread_id=created["thread"]["thread_id"], body="Hello")

    assert result["success"] is True
    event_type, payload = ctx.emitted[-1]
    assert event_type == "domain.messages.message_sent"
    assert payload["recipient_ids"] == ["user_2"]
    assert payload["scope_type"] == "app"
    assert payload["subject_app_id"] == "app_1"


@pytest.mark.asyncio
async def test_list_threads_filters_by_subject_app_id_without_overriding_runtime_scope() -> None:
    ctx = _ctx(app_id="app_1")
    service = MessageService()
    await service.create_thread(ctx, participant_ids=["user_2"], subject_app_id="app_1")
    await service.create_thread(ctx, participant_ids=["user_2"], subject_app_id="app_2")

    result = await service.list_threads(ctx, subject_app_id="app_1")

    assert result["total"] == 1
    assert result["threads"][0]["subject_app_id"] == "app_1"


@pytest.mark.asyncio
async def test_workspace_scope_threads_can_be_queried_separately() -> None:
    ctx = _ctx(workspace_id="workspace_1")
    service = MessageService()
    await service.create_thread(
        ctx,
        participant_ids=["user_2"],
        thread_type="direct",
        scope_type="workspace",
        scope_id="workspace_1",
    )
    await service.create_thread(ctx, participant_ids=["user_2"], thread_type="direct", scope_type="app")

    result = await service.list_threads(ctx, scope_type="workspace", scope_id="workspace_1")

    assert result["total"] == 1
    assert result["threads"][0]["scope_type"] == "workspace"
