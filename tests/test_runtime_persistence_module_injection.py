from __future__ import annotations

from typing import Any

import pytest

import mozaiksai.core.runtime.composition.module_executor as module_executor_module
import mozaiksai.core.runtime.persistence.mongo as mongo_module
from mozaiksai.core.runtime.composition.module_context import ModuleContext
from mozaiksai.core.runtime.composition.module_executor import ModuleExecutor, ModuleRequest
from mozaiksai.core.runtime.persistence import MongoPersistenceContext


class FakeCursor:
    def __init__(self, rows: list[dict[str, Any]] | None = None) -> None:
        self.rows = rows or []

    def limit(self, _limit: int):
        return self

    async def to_list(self, length: int | None = None):
        return self.rows[:length]


class FakeMongoCollection:
    def __init__(self) -> None:
        self.find_one_queries: list[dict[str, Any]] = []

    async def find_one(self, query: dict[str, Any], projection=None):
        self.find_one_queries.append(query)
        return {"ok": True, "query": query}

    def find(self, query: dict[str, Any], projection=None):
        return FakeCursor([{"query": query}])

    async def insert_one(self, document: dict[str, Any]):
        return {"inserted_id": document.get("_id")}

    async def update_one(self, query: dict[str, Any], update: dict[str, Any], *, upsert: bool = False):
        return {"matched_count": 1}

    async def count_documents(self, query: dict[str, Any]):
        return 1

    def list_indexes(self):
        return FakeCursor([])

    async def create_index(self, keys, **kwargs):
        return kwargs.get("name")


class FakeDatabase:
    def __init__(self) -> None:
        self.collections: dict[str, FakeMongoCollection] = {}

    def __getitem__(self, name: str) -> FakeMongoCollection:
        if name not in self.collections:
            self.collections[name] = FakeMongoCollection()
        return self.collections[name]


class FakeMongoClient:
    def __init__(self) -> None:
        self.databases: dict[str, FakeDatabase] = {}

    def __getitem__(self, name: str) -> FakeDatabase:
        if name not in self.databases:
            self.databases[name] = FakeDatabase()
        return self.databases[name]


class CaptureContextHandler:
    async def inspect_context(self, ctx: ModuleContext):
        return {
            "has_persistence": ctx.persistence is not None,
            "persistence_type": type(ctx.persistence).__name__ if ctx.persistence is not None else None,
            "app_id": ctx.persistence.app_id if ctx.persistence is not None else None,
            "scope_metadata": getattr(ctx.persistence, "_scope_metadata", None),
            "has_db": hasattr(ctx, "db"),
            "settings": ctx.settings,
            "auth_token": ctx.auth_token,
        }


class PersistenceUsingHandler:
    async def read_project(self, ctx: ModuleContext):
        collection = ctx.persistence.collection("projects", "projects")
        doc = await collection.find_one({"_id": "project_1"})
        return {"doc": doc}


class EmitHandler:
    async def create(self, ctx: ModuleContext):
        await ctx.emit("domain.tasks.task_created", {"task_id": "task_1"})
        return {"ok": True, "settings": ctx.settings, "auth_token": ctx.auth_token}


class OptionalPersistenceHandler:
    async def run(self, ctx: ModuleContext):
        return {"persistence": ctx.persistence}


def test_module_context_has_persistence_attribute() -> None:
    ctx = ModuleContext(app_id="app_1")

    assert hasattr(ctx, "persistence")


def test_module_context_construction_without_persistence_remains_valid() -> None:
    ctx = ModuleContext(app_id="app_1", user_id="user_1", tenant_id="tenant_1")

    assert ctx.persistence is None
    assert ctx.app_id == "app_1"
    assert ctx.user_id == "user_1"
    assert ctx.tenant_id == "tenant_1"


@pytest.mark.asyncio
async def test_module_executor_injects_persistence_when_app_id_exists() -> None:
    executor = ModuleExecutor()
    executor.register("inspect", CaptureContextHandler())

    result = await executor.execute(ModuleRequest(module="inspect", action="inspect_context", app_id="app_1"))

    assert result.success is True
    assert result.data["has_persistence"] is True
    assert result.data["persistence_type"] == "MongoPersistenceContext"


@pytest.mark.asyncio
async def test_module_executor_production_path_uses_real_mongo_persistence_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_get_mongo_client():
        raise AssertionError("Mongo client should not be opened while constructing ModuleContext")

    monkeypatch.setattr(mongo_module, "get_mongo_client", fail_get_mongo_client)
    assert module_executor_module.MongoPersistenceContext is MongoPersistenceContext

    executor = ModuleExecutor()
    executor.register("inspect", CaptureContextHandler())

    result = await executor.execute(ModuleRequest(module="inspect", action="inspect_context", app_id="app_1"))

    assert result.success is True
    assert result.data["persistence_type"] == "MongoPersistenceContext"


@pytest.mark.asyncio
async def test_injected_persistence_is_mongo_context_with_current_app_id() -> None:
    executor = ModuleExecutor()
    executor.register("inspect", CaptureContextHandler())

    result = await executor.execute(ModuleRequest(module="inspect", action="inspect_context", app_id="app_abc"))

    assert result.success is True
    assert result.data["app_id"] == "app_abc"


@pytest.mark.asyncio
async def test_user_and_tenant_scope_are_passed_to_persistence() -> None:
    executor = ModuleExecutor()
    executor.register("inspect", CaptureContextHandler())

    result = await executor.execute(
        ModuleRequest(
            module="inspect",
            action="inspect_context",
            app_id="app_1",
            user_id="user_1",
            tenant_id="tenant_1",
        )
    )

    assert result.success is True
    assert result.data["scope_metadata"]["app_id"] == "app_1"
    assert result.data["scope_metadata"]["user_id"] == "user_1"
    assert result.data["scope_metadata"]["tenant_id"] == "tenant_1"


@pytest.mark.asyncio
async def test_ctx_db_attribute_does_not_exist() -> None:
    executor = ModuleExecutor()
    executor.register("inspect", CaptureContextHandler())

    result = await executor.execute(ModuleRequest(module="inspect", action="inspect_context", app_id="app_1"))

    assert result.success is True
    assert result.data["has_db"] is False


@pytest.mark.asyncio
async def test_handler_can_access_ctx_persistence() -> None:
    executor = ModuleExecutor()
    executor.register("inspect", CaptureContextHandler())

    result = await executor.execute(ModuleRequest(module="inspect", action="inspect_context", app_id="app_1"))

    assert result.success is True
    assert result.data["has_persistence"] is True


@pytest.mark.asyncio
async def test_handler_can_call_persistence_collection(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_client = FakeMongoClient()
    monkeypatch.setattr(mongo_module, "get_mongo_client", lambda: fake_client)
    executor = ModuleExecutor()
    executor.register("projects", PersistenceUsingHandler())

    result = await executor.execute(ModuleRequest(module="projects", action="read_project", app_id="app_1"))

    assert result.success is True
    assert result.data["doc"]["query"] == {"app_id": "app_1", "_id": "project_1"}


@pytest.mark.asyncio
async def test_existing_event_emit_behavior_remains_unchanged() -> None:
    emitted: list[tuple[str, dict[str, Any]]] = []

    async def capture(event_type: str, payload: dict[str, Any]) -> None:
        emitted.append((event_type, payload))

    executor = ModuleExecutor(event_emitter=capture)
    executor.register("tasks", EmitHandler())

    result = await executor.execute(
        ModuleRequest(module="tasks", action="create", app_id="app_1", user_id="user_1", tenant_id="tenant_1")
    )

    assert result.success is True
    assert emitted[0][0] == "domain.tasks.task_created"
    assert emitted[0][1]["tenant"] == {"app_id": "app_1", "tenant_id": "tenant_1"}
    assert emitted[0][1]["actor"] == {"type": "user", "id": "user_1"}


@pytest.mark.asyncio
async def test_existing_settings_and_auth_fields_remain_unchanged() -> None:
    settings = [{"id": "max_items", "type": "integer", "default": 50}]
    executor = ModuleExecutor()
    executor.register("tasks", EmitHandler(), settings=settings)

    result = await executor.execute(
        ModuleRequest(module="tasks", action="create", app_id="app_1", auth_token="token_123")
    )

    assert result.success is True
    assert result.data["settings"] == settings
    assert result.data["auth_token"] == "token_123"


@pytest.mark.asyncio
async def test_module_execution_still_works_without_persistence_when_app_id_missing() -> None:
    executor = ModuleExecutor()
    executor.register("optional", OptionalPersistenceHandler())

    result = await executor.execute(ModuleRequest(module="optional", action="run"))

    assert result.success is True
    assert result.data == {"persistence": None}


def test_mongo_persistence_context_type_is_available_for_injection() -> None:
    ctx = MongoPersistenceContext(app_id="app_1")

    assert ctx.app_id == "app_1"
