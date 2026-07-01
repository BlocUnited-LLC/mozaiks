from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import pytest

import mozaiksai.core.runtime.persistence.mongo as mongo_module
from mozaiksai.core.runtime.persistence import (
    ModulePersistenceContext,
    MongoPersistenceCollection,
    MongoPersistenceContext,
    PersistenceCollection,
    app_data_from_context,
    collection_name_for,
)
from mozaiksai.core.runtime.persistence.mongo import DEFAULT_APP_DATABASE_NAME, MAX_FIND_MANY_LIMIT


class FakeCursor:
    def __init__(self, rows: list[dict[str, Any]] | None = None) -> None:
        self.rows = rows or []
        self.sort_value: list[tuple[str, int]] | None = None
        self.limit_value: int | None = None

    def sort(self, sort: list[tuple[str, int]]):
        self.sort_value = sort
        return self

    def limit(self, limit: int):
        self.limit_value = limit
        return self

    async def to_list(self, length: int | None = None):
        if length is None:
            return list(self.rows)
        return list(self.rows[:length])


class FakeMongoCollection:
    def __init__(self) -> None:
        self.find_one_calls: list[tuple[dict[str, Any], Mapping[str, Any] | None]] = []
        self.find_calls: list[tuple[dict[str, Any], Mapping[str, Any] | None]] = []
        self.inserted: list[dict[str, Any]] = []
        self.update_calls: list[tuple[dict[str, Any], dict[str, Any], bool]] = []
        self.count_queries: list[dict[str, Any]] = []
        self.create_index_calls: list[tuple[list[tuple[str, int]], dict[str, Any]]] = []
        self.index_rows: list[dict[str, Any]] = []
        self.last_cursor: FakeCursor | None = None

    async def find_one(self, query: dict[str, Any], projection: Mapping[str, Any] | None = None):
        self.find_one_calls.append((query, projection))
        return {"query": query}

    def find(self, query: dict[str, Any], projection: Mapping[str, Any] | None = None):
        self.find_calls.append((query, projection))
        self.last_cursor = FakeCursor([{"n": i} for i in range(200)])
        return self.last_cursor

    async def insert_one(self, document: dict[str, Any]):
        self.inserted.append(document)
        return {"inserted_id": document.get("_id")}

    async def update_one(self, query: dict[str, Any], update: dict[str, Any], *, upsert: bool = False):
        self.update_calls.append((query, update, upsert))
        return {"matched_count": 1}

    async def count_documents(self, query: dict[str, Any]):
        self.count_queries.append(query)
        return 7

    def list_indexes(self):
        return FakeCursor(self.index_rows)

    async def create_index(self, keys: list[tuple[str, int]], **kwargs: Any):
        self.create_index_calls.append((keys, kwargs))
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


def _context(
    *,
    app_id: str = "app_1",
    app_slug: str = "Task Tracker",
    tenant_id: str | None = None,
    workspace_id: str | None = None,
    user_id: str | None = None,
    client: FakeMongoClient | None = None,
) -> tuple[MongoPersistenceContext, FakeMongoClient]:
    fake_client = client or FakeMongoClient()
    return (
        MongoPersistenceContext(
            app_id=app_id,
            app_slug=app_slug,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            user_id=user_id,
            client=fake_client,
        ),
        fake_client,
    )


def test_collection_uses_deterministic_collection_name() -> None:
    context, client = _context()

    context.collection("projects", "projects")

    expected = collection_name_for(
        app_id="app_1",
        app_slug="Task Tracker",
        module_id="projects",
        entity_name="projects",
    )
    assert expected in client.databases[DEFAULT_APP_DATABASE_NAME].collections


def test_literal_collection_uses_raw_contract_collection_name() -> None:
    context, client = _context()

    collection = context.literal_collection("hosted_workspace_memberships")

    assert collection is client.databases[DEFAULT_APP_DATABASE_NAME].collections["hosted_workspace_memberships"]


def test_app_data_from_context_uses_literal_collection_resolver_for_mongo_persistence() -> None:
    context, client = _context()
    app_data = app_data_from_context(
        type("Ctx", (), {"persistence": context})(),
        contract={
            "aliases": [
                {
                    "alias": "tenant_identity.memberships",
                    "collection": "hosted_workspace_memberships",
                }
            ]
        },
    )

    collection = app_data.collection("tenant_identity.memberships")

    assert collection is client.databases[DEFAULT_APP_DATABASE_NAME].collections["hosted_workspace_memberships"]


@pytest.mark.asyncio
async def test_find_one_injects_app_id() -> None:
    context, _ = _context()
    collection = context.collection("projects", "projects")

    await collection.find_one({"status": "open"})

    assert collection._collection.find_one_calls[0][0] == {"app_id": "app_1", "status": "open"}  # noqa: SLF001


@pytest.mark.asyncio
async def test_find_many_injects_app_id_and_respects_limit() -> None:
    context, _ = _context()
    collection = context.collection("projects", "projects")

    rows = await collection.find_many({"status": "open"}, limit=5, sort=[("created_at", -1)])

    assert len(rows) == 5
    assert collection._collection.find_calls[0][0] == {"app_id": "app_1", "status": "open"}  # noqa: SLF001
    assert collection._collection.last_cursor.limit_value == 5  # noqa: SLF001
    assert collection._collection.last_cursor.sort_value == [("created_at", -1)]  # noqa: SLF001


@pytest.mark.asyncio
async def test_find_many_clamps_excessive_limit() -> None:
    context, _ = _context()
    collection = context.collection("projects", "projects")

    rows = await collection.find_many({}, limit=10_000)

    assert len(rows) == MAX_FIND_MANY_LIMIT
    assert collection._collection.last_cursor.limit_value == MAX_FIND_MANY_LIMIT  # noqa: SLF001


@pytest.mark.asyncio
async def test_insert_one_injects_app_id() -> None:
    context, _ = _context()
    collection = context.collection("projects", "projects")

    await collection.insert_one({"_id": "project_1", "name": "Launch"})

    assert collection._collection.inserted[0]["app_id"] == "app_1"  # noqa: SLF001


@pytest.mark.asyncio
async def test_insert_one_injects_tenant_and_workspace_scope() -> None:
    context, _ = _context(tenant_id="tenant_1", workspace_id="workspace_1")
    collection = context.collection("projects", "projects")

    await collection.insert_one({"_id": "project_1"})

    assert collection._collection.inserted[0]["tenant_id"] == "tenant_1"  # noqa: SLF001
    assert collection._collection.inserted[0]["workspace_id"] == "workspace_1"  # noqa: SLF001


@pytest.mark.asyncio
async def test_update_one_injects_app_id_into_query() -> None:
    context, _ = _context()
    collection = context.collection("projects", "projects")

    await collection.update_one({"_id": "project_1"}, {"$set": {"status": "done"}}, upsert=True)

    assert collection._collection.update_calls[0] == (  # noqa: SLF001
        {"app_id": "app_1", "_id": "project_1"},
        {"$set": {"status": "done"}},
        True,
    )


@pytest.mark.asyncio
async def test_update_one_refuses_query_app_id_override() -> None:
    context, _ = _context()
    collection = context.collection("projects", "projects")

    with pytest.raises(ValueError, match="cannot override app_id"):
        await collection.update_one({"app_id": "app_2"}, {"$set": {"status": "done"}})


def test_scope_filter_refuses_extra_app_id_override() -> None:
    context, _ = _context()

    with pytest.raises(ValueError, match="cannot override app_id"):
        context.scope_filter({"app_id": "app_2"})


@pytest.mark.asyncio
async def test_count_injects_app_id() -> None:
    context, _ = _context()
    collection = context.collection("projects", "projects")

    count = await collection.count({"status": "open"})

    assert count == 7
    assert collection._collection.count_queries[0] == {"app_id": "app_1", "status": "open"}  # noqa: SLF001


@pytest.mark.asyncio
async def test_ensure_indexes_calls_create_index_with_expected_keys() -> None:
    context, _ = _context()
    collection = context.collection("projects", "projects")

    await collection.ensure_indexes(
        [{"keys": [["status", 1], ["created_at", -1]], "name": "status_created_at"}]
    )

    assert collection._collection.create_index_calls == [  # noqa: SLF001
        ([("status", 1), ("created_at", -1)], {"name": "status_created_at"})
    ]


@pytest.mark.asyncio
async def test_ensure_indexes_is_noop_for_empty_indexes() -> None:
    context, _ = _context()
    collection = context.collection("projects", "projects")

    await collection.ensure_indexes([])

    assert collection._collection.create_index_calls == []  # noqa: SLF001


def test_adapter_uses_injected_client_in_tests() -> None:
    context, client = _context()

    context.collection("projects", "projects")

    assert context._client is client  # noqa: SLF001


def test_no_get_mongo_client_call_when_client_is_injected(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail_get_mongo_client():
        raise AssertionError("get_mongo_client should not be called")

    monkeypatch.setattr(mongo_module, "get_mongo_client", fail_get_mongo_client)
    context, _ = _context(client=FakeMongoClient())

    context.collection("projects", "projects")


def test_default_database_name_is_stable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MOZAIKS_APP_DATABASE_NAME", raising=False)
    monkeypatch.delenv("MOZAIKS_APPS_DATABASE", raising=False)
    context, _ = _context()

    assert context.database_name == DEFAULT_APP_DATABASE_NAME


def test_collection_wrapper_keeps_raw_collection_private() -> None:
    context, _ = _context()
    collection = context.collection("projects", "projects")

    assert isinstance(collection, MongoPersistenceCollection)
    assert "collection" not in vars(collection)
    assert "_collection" in vars(collection)


def test_protocols_pass_with_mongo_implementation() -> None:
    context, _ = _context()
    collection = context.collection("projects", "projects")

    assert isinstance(context, ModulePersistenceContext)
    assert isinstance(collection, PersistenceCollection)

