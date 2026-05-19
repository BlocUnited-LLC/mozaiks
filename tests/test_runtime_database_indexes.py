from __future__ import annotations

from typing import Any

import pytest

from mozaiksai.core.runtime.app.definition import AppDefinition
from mozaiksai.core.runtime.app.loader import AppLoadResult
from mozaiksai.core.runtime.persistence import apply_database_indexes, collection_name_for
from mozaiksai.core.runtime.persistence.indexes import DatabaseIndexApplyError
from mozaiksai.core.runtime.persistence.mongo import (
    DEFAULT_APP_DATABASE_NAME,
    MongoPersistenceContext,
)


class FakeCursor:
    def __init__(self, rows: list[dict[str, Any]] | None = None) -> None:
        self.rows = rows or []

    async def to_list(self, length: int | None = None):
        return list(self.rows)


class FakeMongoCollection:
    def __init__(self) -> None:
        self.index_rows: list[dict[str, Any]] = []
        self.create_index_calls: list[tuple[list[tuple[str, int]], dict[str, Any]]] = []
        self.insert_calls: list[dict[str, Any]] = []
        self.update_calls: list[tuple[dict[str, Any], dict[str, Any]]] = []

    def list_indexes(self):
        return FakeCursor(self.index_rows)

    async def create_index(self, keys: list[tuple[str, int]], **kwargs: Any):
        self.create_index_calls.append((keys, kwargs))
        name = str(kwargs.get("name") or "_".join(field for field, _ in keys))
        self.index_rows.append({"name": name, "key": dict(keys)})
        return name

    async def insert_one(self, document: dict[str, Any]):
        self.insert_calls.append(document)
        return {"inserted_id": document.get("_id")}

    async def update_one(self, query: dict[str, Any], update: dict[str, Any], *, upsert: bool = False):
        self.update_calls.append((query, update))
        return {"matched_count": 1}


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


def _intent(indexes: list[dict[str, Any]] | None = None, *, collection_name: str = "projects") -> dict[str, Any]:
    collection: dict[str, Any] = {
        "name": collection_name,
        "scope": "app",
        "ownership": {"surface_id": "projects", "surface_kind": "module"},
        "fields": [{"name": "app_id", "type": "string", "required": True}],
    }
    if indexes is not None:
        collection["indexes"] = indexes
    return {
        "version": "1",
        "app_id": "app_1",
        "surfaces": [
            {
                "surface_id": "projects",
                "surface_kind": "module",
                "collections": [collection],
            }
        ],
        "shared_collections": [],
        "policies": {"default_scope_field": "app_id"},
    }


def _context(client: FakeMongoClient | None = None) -> tuple[MongoPersistenceContext, FakeMongoClient]:
    fake_client = client or FakeMongoClient()
    return MongoPersistenceContext(app_id="app_1", app_slug="app", client=fake_client), fake_client


def _collection(client: FakeMongoClient, *, entity_name: str = "projects") -> FakeMongoCollection:
    name = collection_name_for(app_id="app_1", app_slug="app", module_id="projects", entity_name=entity_name)
    return client[DEFAULT_APP_DATABASE_NAME][name]


@pytest.mark.asyncio
async def test_apply_database_indexes_noops_when_intent_is_none() -> None:
    count = await apply_database_indexes(None, app_id="app_1")

    assert count == 0


@pytest.mark.asyncio
async def test_apply_database_indexes_skips_collections_without_indexes() -> None:
    context, client = _context()

    count = await apply_database_indexes(_intent(indexes=None), persistence=context)

    assert count == 0
    assert client.databases == {}


@pytest.mark.asyncio
async def test_apply_database_indexes_applies_single_field_index() -> None:
    context, client = _context()

    count = await apply_database_indexes(
        _intent([{"name": "project_id_idx", "keys": [{"field": "project_id", "order": 1}]}]),
        persistence=context,
    )

    assert count == 1
    assert _collection(client).create_index_calls == [([("project_id", 1)], {"name": "project_id_idx"})]


@pytest.mark.asyncio
async def test_apply_database_indexes_applies_compound_index() -> None:
    context, client = _context()

    await apply_database_indexes(
        _intent(
            [
                {
                    "name": "owner_created_at",
                    "keys": [{"field": "owner_id", "order": 1}, {"field": "created_at", "order": -1}],
                }
            ]
        ),
        persistence=context,
    )

    assert _collection(client).create_index_calls[0] == (
        [("owner_id", 1), ("created_at", -1)],
        {"name": "owner_created_at"},
    )


@pytest.mark.asyncio
async def test_apply_database_indexes_applies_unique_index() -> None:
    context, client = _context()

    await apply_database_indexes(
        _intent([{"name": "project_id_unique", "keys": [{"field": "project_id", "order": 1}], "unique": True}]),
        persistence=context,
    )

    assert _collection(client).create_index_calls[0] == (
        [("project_id", 1)],
        {"unique": True, "name": "project_id_unique"},
    )


@pytest.mark.asyncio
async def test_apply_database_indexes_supports_list_key_format() -> None:
    context, client = _context()

    await apply_database_indexes(
        _intent([{"name": "status_created_at", "keys": [["status", 1], ["created_at", -1]]}]),
        persistence=context,
    )

    assert _collection(client).create_index_calls[0] == (
        [("status", 1), ("created_at", -1)],
        {"name": "status_created_at"},
    )


@pytest.mark.asyncio
async def test_apply_database_indexes_uses_collection_name_for_app_module_entity() -> None:
    context, client = _context()

    await apply_database_indexes(
        _intent([{"name": "project_id_idx", "keys": [["project_id", 1]]}]),
        persistence=context,
    )

    expected = collection_name_for(app_id="app_1", app_slug="app", module_id="projects", entity_name="projects")
    assert expected in client[DEFAULT_APP_DATABASE_NAME].collections


@pytest.mark.asyncio
async def test_apply_database_indexes_does_not_create_duplicate_named_indexes_when_called_twice() -> None:
    context, client = _context()
    intent = _intent([{"name": "project_id_idx", "keys": [["project_id", 1]]}])

    await apply_database_indexes(intent, persistence=context)
    await apply_database_indexes(intent, persistence=context)

    assert len(_collection(client).create_index_calls) == 1


@pytest.mark.asyncio
async def test_apply_database_indexes_missing_module_id_fails_clearly() -> None:
    context, _ = _context()
    intent = _intent([{"name": "idx", "keys": [["field", 1]]}])
    intent["surfaces"][0]["surface_id"] = ""
    intent["surfaces"][0]["collections"][0]["ownership"] = {}

    with pytest.raises(DatabaseIndexApplyError, match="module_id is required"):
        await apply_database_indexes(intent, persistence=context)


@pytest.mark.asyncio
async def test_apply_database_indexes_missing_entity_name_fails_clearly() -> None:
    context, _ = _context()

    with pytest.raises(DatabaseIndexApplyError, match="entity_name is required"):
        await apply_database_indexes(
            _intent([{"name": "idx", "keys": [["field", 1]]}], collection_name=""),
            persistence=context,
        )


@pytest.mark.asyncio
async def test_apply_database_indexes_invalid_index_shape_fails_clearly() -> None:
    context, _ = _context()

    with pytest.raises(DatabaseIndexApplyError, match="keys must be a non-empty list"):
        await apply_database_indexes(_intent([{"name": "idx", "keys": []}]), persistence=context)


@pytest.mark.asyncio
async def test_apply_database_indexes_does_not_write_or_update_documents() -> None:
    context, client = _context()

    await apply_database_indexes(_intent([{"name": "idx", "keys": [["field", 1]]}]), persistence=context)

    collection = _collection(client)
    assert collection.insert_calls == []
    assert collection.update_calls == []


@pytest.mark.asyncio
async def test_apply_database_indexes_does_not_mark_migrations_applied() -> None:
    context, client = _context()

    await apply_database_indexes(_intent([{"name": "idx", "keys": [["field", 1]]}]), persistence=context)

    db = client[DEFAULT_APP_DATABASE_NAME]
    assert not any("migration" in name.lower() for name in db.collections)


@pytest.mark.asyncio
async def test_platform_startup_applies_indexes_when_database_intent_is_loaded(monkeypatch: pytest.MonkeyPatch) -> None:
    from mozaiksai.hosts import platform

    calls: list[dict[str, Any]] = []
    intent = _intent([{"name": "idx", "keys": [["field", 1]]}])

    async def fake_load(_path: str):
        return AppLoadResult(
            definition=AppDefinition(name="Intent Test", version="1.0"),
            modules=[],
            database_intent=intent,
            database_entities_by_key={},
        )

    async def fake_apply(database_intent, *, app_id=None):
        calls.append({"intent": database_intent, "app_id": app_id})
        return 1

    class FakeHooks:
        async def run_startup(self, _app):
            return None

    monkeypatch.setattr(platform.AppLoader, "load", fake_load)
    monkeypatch.setattr(platform, "apply_database_indexes", fake_apply)
    monkeypatch.setattr(platform, "get_platform_hooks", lambda: FakeHooks())

    await platform._platform_startup()

    assert calls == [{"intent": intent, "app_id": "app_1"}]


@pytest.mark.asyncio
async def test_platform_startup_best_effort_index_failure_logs_and_continues(monkeypatch: pytest.MonkeyPatch) -> None:
    from mozaiksai.hosts import platform

    warnings: list[str] = []
    intent = _intent([{"name": "idx", "keys": [["field", 1]]}])

    async def fake_load(_path: str):
        return AppLoadResult(
            definition=AppDefinition(name="Intent Test", version="1.0", config={"appId": "app_1"}),
            modules=[],
            database_intent=intent,
            database_entities_by_key={},
        )

    async def fail_apply(_intent, *, app_id=None):
        raise RuntimeError("index failure")

    class FakeHooks:
        async def run_startup(self, _app):
            return None

    monkeypatch.delenv("MOZAIKS_DATABASE_STARTUP_POLICY", raising=False)
    monkeypatch.setattr(platform.AppLoader, "load", fake_load)
    monkeypatch.setattr(platform, "apply_database_indexes", fail_apply)
    monkeypatch.setattr(platform, "load_database_migrations", lambda _root: [])
    monkeypatch.setattr(platform, "get_platform_hooks", lambda: FakeHooks())
    monkeypatch.setattr(platform.logger, "warning", lambda message, *args: warnings.append(message % args))

    await platform._platform_startup()

    assert any("DATABASE_INDEXES_NOT_APPLIED" in warning for warning in warnings)


@pytest.mark.asyncio
async def test_platform_startup_required_index_failure_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    from mozaiksai.hosts import platform

    intent = _intent([{"name": "idx", "keys": [["field", 1]]}])

    async def fake_load(_path: str):
        return AppLoadResult(
            definition=AppDefinition(name="Intent Test", version="1.0", config={"appId": "app_1"}),
            modules=[],
            database_intent=intent,
            database_entities_by_key={},
        )

    async def fail_apply(_intent, *, app_id=None):
        raise RuntimeError("index failure")

    monkeypatch.setenv("MOZAIKS_DATABASE_STARTUP_POLICY", "required")
    monkeypatch.setattr(platform.AppLoader, "load", fake_load)
    monkeypatch.setattr(platform, "apply_database_indexes", fail_apply)

    with pytest.raises(platform.DatabaseStartupError, match="Database indexes were not applied"):
        await platform._platform_startup()
