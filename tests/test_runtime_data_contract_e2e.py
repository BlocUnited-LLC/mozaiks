"""End-to-end fixture tests for the data contract runtime path.

Covers the full load → index-apply chain using a neutral OSS fixture
(projects + tasks) with a fake MongoPersistenceContext. No Mongo process,
no migrations, no document writes, no product-specific examples.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

import mozaiksai.core.runtime.persistence.mongo as mongo_module
from mozaiksai.core.runtime.app.loader import AppLoader
from mozaiksai.core.runtime.persistence import apply_database_indexes, collection_name_for
from mozaiksai.core.runtime.persistence.mongo import (
    DEFAULT_APP_DATABASE_NAME,
    MongoPersistenceContext,
)

# ---------------------------------------------------------------------------
# Fake Mongo infrastructure (no live connection required)
# ---------------------------------------------------------------------------

class _FakeCursor:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    async def to_list(self, length: int | None = None) -> list[dict[str, Any]]:
        return list(self._rows)


class _FakeCollection:
    def __init__(self) -> None:
        self._index_rows: list[dict[str, Any]] = []
        self.create_index_calls: list[tuple[list[tuple[str, int]], dict[str, Any]]] = []
        self.insert_calls: list[dict[str, Any]] = []
        self.update_calls: list[tuple[dict[str, Any], dict[str, Any]]] = []

    def list_indexes(self) -> _FakeCursor:
        return _FakeCursor(list(self._index_rows))

    async def create_index(self, keys: list[tuple[str, int]], **kwargs: Any) -> str:
        self.create_index_calls.append((keys, kwargs))
        name = str(kwargs.get("name") or "_".join(f for f, _ in keys))
        self._index_rows.append({"name": name, "key": dict(keys)})
        return name

    async def insert_one(self, document: dict[str, Any]) -> dict[str, Any]:
        self.insert_calls.append(document)
        return {"inserted_id": document.get("_id")}

    async def update_one(
        self,
        query: dict[str, Any],
        update: dict[str, Any],
        *,
        upsert: bool = False,
    ) -> dict[str, Any]:
        self.update_calls.append((query, update))
        return {"matched_count": 1}


class _FakeDatabase:
    def __init__(self) -> None:
        self.collections: dict[str, _FakeCollection] = {}

    def __getitem__(self, name: str) -> _FakeCollection:
        if name not in self.collections:
            self.collections[name] = _FakeCollection()
        return self.collections[name]


class _FakeClient:
    def __init__(self) -> None:
        self.databases: dict[str, _FakeDatabase] = {}

    def __getitem__(self, name: str) -> _FakeDatabase:
        if name not in self.databases:
            self.databases[name] = _FakeDatabase()
        return self.databases[name]


def _make_context(client: _FakeClient | None = None) -> tuple[MongoPersistenceContext, _FakeClient]:
    fake = client or _FakeClient()
    ctx = MongoPersistenceContext(app_id="app_e2e", app_slug="e2e", client=fake)
    return ctx, fake


def _get_collection(client: _FakeClient, *, module_id: str, entity_name: str) -> _FakeCollection:
    name = collection_name_for(
        app_id="app_e2e",
        app_slug="e2e",
        module_id=module_id,
        entity_name=entity_name,
    )
    return client[DEFAULT_APP_DATABASE_NAME][name]


# ---------------------------------------------------------------------------
# Fixture factories
# ---------------------------------------------------------------------------

def _bundle_intent() -> dict[str, Any]:
    """Neutral 2-surface intent: projects (2 indexes) + tasks (2 indexes)."""
    return {
        "version": "1",
        "app_id": "app_e2e",
        "surfaces": [
            {
                "surface_id": "projects",
                "surface_kind": "module",
                "collections": [
                    {
                        "name": "projects",
                        "scope": "app",
                        "ownership": {"surface_id": "projects", "surface_kind": "module"},
                        "fields": [
                            {"name": "project_id", "type": "string", "required": True},
                            {"name": "app_id", "type": "string", "required": True},
                            {"name": "title", "type": "string", "required": True},
                            {"name": "status", "type": "string", "required": True},
                        ],
                        "indexes": [
                            {
                                "name": "projects_app_project_unique",
                                "keys": [["app_id", 1], ["project_id", 1]],
                                "unique": True,
                            },
                            {
                                "name": "projects_app_status",
                                "keys": [["app_id", 1], ["status", 1]],
                            },
                        ],
                        "lifecycle": {
                            "write_mode": "module_action",
                            "migration_policy": "additive_only",
                        },
                    }
                ],
            },
            {
                "surface_id": "tasks",
                "surface_kind": "module",
                "collections": [
                    {
                        "name": "tasks",
                        "scope": "app",
                        "ownership": {"surface_id": "tasks", "surface_kind": "module"},
                        "fields": [
                            {"name": "task_id", "type": "string", "required": True},
                            {"name": "project_id", "type": "string", "required": True},
                            {"name": "app_id", "type": "string", "required": True},
                            {"name": "title", "type": "string", "required": True},
                            {"name": "completed", "type": "bool", "required": False, "default": False},
                        ],
                        "indexes": [
                            {
                                "name": "tasks_app_project",
                                "keys": [["app_id", 1], ["project_id", 1]],
                            },
                            {
                                "name": "tasks_app_task_unique",
                                "keys": [["app_id", 1], ["task_id", 1]],
                                "unique": True,
                            },
                        ],
                        "lifecycle": {
                            "write_mode": "module_action",
                            "migration_policy": "additive_only",
                        },
                    }
                ],
            },
        ],
        "shared_collections": [],
        "policies": {
            "default_scope_field": "app_id",
            "allow_destructive_migrations": False,
        },
    }


def _write_bundle(root: Path, intent: dict[str, Any] | None = None) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "app.json").write_text(
        json.dumps({"appName": "E2E Test App", "version": "1.0.0"}),
        encoding="utf-8",
    )
    if intent is not None:
        config = root / "config"
        config.mkdir(exist_ok=True)
        (config / "data.json").write_text(
            json.dumps(intent),
            encoding="utf-8",
        )


# ---------------------------------------------------------------------------
# Tests: app loader integration
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_e2e_app_loads_data_contract_from_bundle(tmp_path: Path) -> None:
    _write_bundle(tmp_path, _bundle_intent())

    result = await AppLoader.load(str(tmp_path))

    assert result.data_contract is not None
    assert result.data_contract["version"] == "1"
    assert result.data_contract["app_id"] == "app_e2e"


@pytest.mark.asyncio
async def test_e2e_app_load_result_entity_index_contains_both_surfaces(tmp_path: Path) -> None:
    _write_bundle(tmp_path, _bundle_intent())

    result = await AppLoader.load(str(tmp_path))

    assert ("projects", "projects") in result.data_entities_by_key
    assert ("tasks", "tasks") in result.data_entities_by_key


@pytest.mark.asyncio
async def test_e2e_entity_index_carries_collection_metadata(tmp_path: Path) -> None:
    _write_bundle(tmp_path, _bundle_intent())

    result = await AppLoader.load(str(tmp_path))
    projects_entry = result.data_entities_by_key[("projects", "projects")]
    tasks_entry = result.data_entities_by_key[("tasks", "tasks")]

    assert len(projects_entry["indexes"]) == 2
    assert len(tasks_entry["indexes"]) == 2
    assert projects_entry["lifecycle"]["migration_policy"] == "additive_only"
    assert tasks_entry["lifecycle"]["migration_policy"] == "additive_only"


@pytest.mark.asyncio
async def test_e2e_missing_data_contract_is_allowed(tmp_path: Path) -> None:
    _write_bundle(tmp_path, intent=None)

    result = await AppLoader.load(str(tmp_path))

    assert result.data_contract is None
    assert result.data_entities_by_key == {}


@pytest.mark.asyncio
async def test_e2e_loader_does_not_call_mongo(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    def _must_not_call():
        raise AssertionError("AppLoader.load must not contact Mongo")

    monkeypatch.setattr(mongo_module, "get_mongo_client", _must_not_call)
    _write_bundle(tmp_path, _bundle_intent())

    result = await AppLoader.load(str(tmp_path))

    assert result.data_contract is not None


# ---------------------------------------------------------------------------
# Tests: index application
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_e2e_apply_indexes_creates_all_declared_indexes(tmp_path: Path) -> None:
    _write_bundle(tmp_path, _bundle_intent())
    load_result = await AppLoader.load(str(tmp_path))
    context, client = _make_context()

    applied = await apply_database_indexes(load_result.data_contract, persistence=context)

    assert applied == 4  # 2 projects + 2 tasks

    projects_col = _get_collection(client, module_id="projects", entity_name="projects")
    tasks_col = _get_collection(client, module_id="tasks", entity_name="tasks")

    assert len(projects_col.create_index_calls) == 2
    assert len(tasks_col.create_index_calls) == 2

    projects_names = {kw.get("name") for _, kw in projects_col.create_index_calls}
    assert projects_names == {"projects_app_project_unique", "projects_app_status"}

    tasks_names = {kw.get("name") for _, kw in tasks_col.create_index_calls}
    assert tasks_names == {"tasks_app_project", "tasks_app_task_unique"}


@pytest.mark.asyncio
async def test_e2e_unique_flag_is_forwarded_to_create_index(tmp_path: Path) -> None:
    _write_bundle(tmp_path, _bundle_intent())
    load_result = await AppLoader.load(str(tmp_path))
    context, client = _make_context()

    await apply_database_indexes(load_result.data_contract, persistence=context)

    projects_col = _get_collection(client, module_id="projects", entity_name="projects")
    unique_calls = [kw for _, kw in projects_col.create_index_calls if kw.get("unique")]
    assert len(unique_calls) == 1
    assert unique_calls[0]["name"] == "projects_app_project_unique"


@pytest.mark.asyncio
async def test_e2e_apply_indexes_is_idempotent(tmp_path: Path) -> None:
    _write_bundle(tmp_path, _bundle_intent())
    load_result = await AppLoader.load(str(tmp_path))
    context, client = _make_context()

    await apply_database_indexes(load_result.data_contract, persistence=context)
    # Second application must not call create_index again for any existing name.
    second_count = await apply_database_indexes(load_result.data_contract, persistence=context)

    projects_col = _get_collection(client, module_id="projects", entity_name="projects")
    tasks_col = _get_collection(client, module_id="tasks", entity_name="tasks")

    # Total create_index calls stay at 2 per collection after two applications.
    assert len(projects_col.create_index_calls) == 2
    assert len(tasks_col.create_index_calls) == 2
    # apply_database_indexes still returns the spec count (not new-only count).
    assert second_count == 4


@pytest.mark.asyncio
async def test_e2e_apply_indexes_produces_no_document_writes(tmp_path: Path) -> None:
    _write_bundle(tmp_path, _bundle_intent())
    load_result = await AppLoader.load(str(tmp_path))
    context, client = _make_context()

    await apply_database_indexes(load_result.data_contract, persistence=context)

    projects_col = _get_collection(client, module_id="projects", entity_name="projects")
    tasks_col = _get_collection(client, module_id="tasks", entity_name="tasks")

    assert projects_col.insert_calls == []
    assert projects_col.update_calls == []
    assert tasks_col.insert_calls == []
    assert tasks_col.update_calls == []


@pytest.mark.asyncio
async def test_e2e_apply_indexes_creates_no_migration_collection(tmp_path: Path) -> None:
    _write_bundle(tmp_path, _bundle_intent())
    load_result = await AppLoader.load(str(tmp_path))
    context, client = _make_context()

    await apply_database_indexes(load_result.data_contract, persistence=context)

    db = client[DEFAULT_APP_DATABASE_NAME]
    migration_collections = [n for n in db.collections if "migration" in n.lower()]
    assert migration_collections == []


@pytest.mark.asyncio
async def test_e2e_apply_indexes_noops_when_intent_is_none() -> None:
    context, client = _make_context()

    applied = await apply_database_indexes(None, persistence=context)

    assert applied == 0
    assert client.databases == {}


# ---------------------------------------------------------------------------
# Full end-to-end: load → apply → verify (single sequential test)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_e2e_full_load_then_apply_indexes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Single test covering the complete load → apply chain end-to-end.

    Verifies all guarantees in one pass:
      - loader reads intent from disk without contacting Mongo
      - AppLoadResult fields are populated
      - apply_database_indexes creates exactly the declared indexes
      - second application is idempotent
      - no document writes anywhere
      - no migration collection created
    """
    # Ensure Mongo is never contacted during load.
    def _no_mongo():
        raise AssertionError("Mongo must not be called during this test")

    monkeypatch.setattr(mongo_module, "get_mongo_client", _no_mongo)

    _write_bundle(tmp_path, _bundle_intent())

    # --- Phase 1: load ---
    load_result = await AppLoader.load(str(tmp_path))

    assert load_result.data_contract is not None, "data_contract must be populated"
    assert load_result.data_contract["version"] == "1"
    assert ("projects", "projects") in load_result.data_entities_by_key
    assert ("tasks", "tasks") in load_result.data_entities_by_key

    # --- Phase 2: first application ---
    context, client = _make_context()
    applied_first = await apply_database_indexes(load_result.data_contract, persistence=context)
    assert applied_first == 4

    # --- Phase 3: idempotent second application ---
    applied_second = await apply_database_indexes(load_result.data_contract, persistence=context)
    assert applied_second == 4  # spec count unchanged

    projects_col = _get_collection(client, module_id="projects", entity_name="projects")
    tasks_col = _get_collection(client, module_id="tasks", entity_name="tasks")

    # No extra create_index calls from the second round.
    assert len(projects_col.create_index_calls) == 2, "idempotency: projects indexes created exactly once"
    assert len(tasks_col.create_index_calls) == 2, "idempotency: tasks indexes created exactly once"

    # No document writes.
    assert projects_col.insert_calls == []
    assert projects_col.update_calls == []
    assert tasks_col.insert_calls == []
    assert tasks_col.update_calls == []

    # No migration collection.
    db = client[DEFAULT_APP_DATABASE_NAME]
    assert not any("migration" in n.lower() for n in db.collections)
