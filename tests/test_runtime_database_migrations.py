from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from mozaiksai.core.runtime.app.definition import AppDefinition
from mozaiksai.core.runtime.app.loader import AppLoadResult
from mozaiksai.core.runtime.persistence import (
    APP_DATA_MIGRATIONS_COLLECTION,
    apply_data_migrations,
    collection_name_for,
    get_database_startup_policy,
    get_migration_health_report,
    load_data_migrations,
    migration_hash,
)
from mozaiksai.core.runtime.persistence.migrations import DatabaseMigrationError
from mozaiksai.core.runtime.persistence.mongo import (
    DEFAULT_APP_DATABASE_NAME,
    MongoPersistenceContext,
)


class FakeCursor:
    def __init__(self, rows: list[dict[str, Any]] | None = None) -> None:
        self.rows = rows or []
        self.limit_value: int | None = None

    async def to_list(self, length: int | None = None):
        rows = list(self.rows)
        if self.limit_value is not None:
            rows = rows[: self.limit_value]
        if length is not None:
            rows = rows[:length]
        return rows

    def sort(self, sort_spec):
        for field, direction in reversed(list(sort_spec or [])):
            self.rows.sort(key=lambda row: str(row.get(field) or ""), reverse=int(direction) == -1)
        return self

    def limit(self, limit: int):
        self.limit_value = int(limit)
        return self
        return list(self.rows)


class FakeMongoCollection:
    def __init__(self) -> None:
        self.index_rows: list[dict[str, Any]] = []
        self.create_index_calls: list[tuple[list[tuple[str, int]], dict[str, Any]]] = []
        self.find_one_queries: list[dict[str, Any]] = []
        self.find_one_and_update_calls: list[tuple[dict[str, Any], dict[str, Any], bool]] = []
        self.update_one_calls: list[tuple[dict[str, Any], dict[str, Any], bool]] = []
        self.insert_calls: list[dict[str, Any]] = []
        self.document_by_key: dict[tuple[str, str], dict[str, Any]] = {}
        self.fail_next_find_one_and_update = False
        self.find_calls: list[dict[str, Any]] = []

    def list_indexes(self):
        return FakeCursor(self.index_rows)

    async def create_index(self, keys: list[tuple[str, int]], **kwargs: Any):
        self.create_index_calls.append((keys, kwargs))
        name = str(kwargs.get("name") or "_".join(field for field, _ in keys))
        self.index_rows.append({"name": name, "key": dict(keys)})
        return name

    async def find_one(self, query: dict[str, Any], projection=None):
        self.find_one_queries.append(query)
        key = (str(query.get("app_id") or ""), str(query.get("migration_id") or ""))
        return self.document_by_key.get(key)

    def find(self, query: dict[str, Any]):
        self.find_calls.append(dict(query))
        rows = []
        for row in self.document_by_key.values():
            if all(row.get(key) == value for key, value in query.items()):
                rows.append(dict(row))
        return FakeCursor(rows)

    async def find_one_and_update(
        self,
        query: dict[str, Any],
        update: dict[str, Any],
        *,
        upsert: bool = False,
        return_document=None,
    ):
        self.find_one_and_update_calls.append((query, update, upsert))
        if self.fail_next_find_one_and_update:
            self.fail_next_find_one_and_update = False
            raise RuntimeError("duplicate key race")
        key = (str(query.get("app_id") or ""), str(query.get("migration_id") or ""))
        if key not in self.document_by_key:
            if not upsert:
                return None
            self.document_by_key[key] = dict(update.get("$setOnInsert") or {})
        return self.document_by_key.get(key)

    async def update_one(self, query: dict[str, Any], update: dict[str, Any], *, upsert: bool = False):
        self.update_one_calls.append((query, update, upsert))
        key = (str(query.get("app_id") or ""), str(query.get("migration_id") or ""))
        doc = dict(self.document_by_key.get(key) or {})
        if key not in self.document_by_key:
            doc.update(update.get("$setOnInsert") or {})
        doc.update(update.get("$set") or {})
        self.document_by_key[key] = doc
        return {"matched_count": 1}

    async def insert_one(self, document: dict[str, Any]):
        self.insert_calls.append(document)
        return {"inserted_id": document.get("_id")}


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


def _migration(migration_id: str = "m_001", operations: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return {
        "migration_id": migration_id,
        "version": "1",
        "description": "Add project indexes",
        "operations": operations
        if operations is not None
        else [
            {"type": "ensure_collection", "module_id": "projects", "entity_name": "projects"},
            {
                "type": "ensure_index",
                "module_id": "projects",
                "entity_name": "projects",
                "index": {"name": "project_id_idx", "keys": [["project_id", 1]]},
            },
        ],
    }


def _write_migration(root: Path, filename: str, migration: dict[str, Any] | str) -> None:
    path = root / "config" / "data_migrations" / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(migration, str):
        path.write_text(migration, encoding="utf-8")
    else:
        path.write_text(json.dumps(migration), encoding="utf-8")


def _context(client: FakeMongoClient | None = None) -> tuple[MongoPersistenceContext, FakeMongoClient]:
    fake_client = client or FakeMongoClient()
    return MongoPersistenceContext(app_id="app_1", app_slug="app", client=fake_client), fake_client


def _app_collection(client: FakeMongoClient) -> FakeMongoCollection:
    name = collection_name_for(app_id="app_1", app_slug="app", module_id="projects", entity_name="projects")
    return client[DEFAULT_APP_DATABASE_NAME][name]


def _history_collection(client: FakeMongoClient) -> FakeMongoCollection:
    return client["mozaiksai"][APP_DATA_MIGRATIONS_COLLECTION]


def _history_doc(
    *,
    app_id: str = "app_1",
    migration_id: str = "m_001",
    status: str = "applied",
) -> dict[str, Any]:
    return {
        "app_id": app_id,
        "migration_id": migration_id,
        "migration_hash": f"hash_{migration_id}",
        "status": status,
        "applied_at": "2026-01-01T00:00:00Z" if status == "applied" else None,
        "failed_at": "2026-01-01T00:00:00Z" if status == "failed" else None,
        "error_message": "index failed" if status == "failed" else None,
        "failed_operation_index": 1 if status == "failed" else None,
    }


def test_load_data_migrations_returns_empty_when_directory_missing(tmp_path: Path) -> None:
    assert load_data_migrations(tmp_path) == []


def test_load_data_migrations_reads_json_files_in_filename_order(tmp_path: Path) -> None:
    _write_migration(tmp_path, "002.json", _migration("m_002", []))
    _write_migration(tmp_path, "001.json", _migration("m_001", []))

    migrations = load_data_migrations(tmp_path)

    assert [migration["migration_id"] for migration in migrations] == ["m_001", "m_002"]


def test_load_data_migrations_invalid_json_raises_clear_error(tmp_path: Path) -> None:
    _write_migration(tmp_path, "001.json", "{bad json")

    with pytest.raises(DatabaseMigrationError, match="Failed to read"):
        load_data_migrations(tmp_path)


def test_load_data_migrations_missing_migration_id_raises_error(tmp_path: Path) -> None:
    _write_migration(tmp_path, "001.json", {"version": "1", "operations": []})

    with pytest.raises(DatabaseMigrationError, match="migration_id is required"):
        load_data_migrations(tmp_path)


def test_load_data_migrations_allows_unsupported_operation_for_apply_time_failure(tmp_path: Path) -> None:
    _write_migration(tmp_path, "001.json", _migration(operations=[{"type": "rewrite_documents"}]))

    assert load_data_migrations(tmp_path)[0]["operations"][0]["type"] == "rewrite_documents"


@pytest.mark.asyncio
async def test_ensure_collection_operation_is_accepted_without_document_writes() -> None:
    context, client = _context()

    count = await apply_data_migrations(
        app_id="app_1",
        migrations=[_migration(operations=[{"type": "ensure_collection", "module_id": "projects", "entity_name": "projects"}])],
        persistence=context,
        history_client=client,
    )

    assert count == 1
    assert _app_collection(client).insert_calls == []
    assert _app_collection(client).create_index_calls == []


@pytest.mark.asyncio
async def test_ensure_index_operation_calls_ensure_indexes() -> None:
    context, client = _context()

    await apply_data_migrations(
        app_id="app_1",
        migrations=[_migration(operations=[{
            "type": "ensure_index",
            "module_id": "projects",
            "entity_name": "projects",
            "index": {"name": "status_idx", "keys": [{"field": "status", "order": 1}]},
        }])],
        persistence=context,
        history_client=client,
    )

    assert _app_collection(client).create_index_calls == [([("status", 1)], {"name": "status_idx"})]


@pytest.mark.asyncio
async def test_applied_migration_is_recorded_in_history_collection() -> None:
    context, client = _context()
    migration = _migration()

    await apply_data_migrations(app_id="app_1", migrations=[migration], persistence=context, history_client=client)

    history = _history_collection(client)
    record = history.document_by_key[("app_1", "m_001")]
    assert record["status"] == "applied"
    assert record["migration_hash"] == migration_hash(migration)
    assert record["started_at"]
    assert record["applied_at"]
    assert record["completed_at"]
    assert record["operations_summary"][0] == {
        "type": "ensure_collection",
        "module_id": "projects",
        "entity_name": "projects",
    }


@pytest.mark.asyncio
async def test_migration_writes_in_progress_before_operations() -> None:
    context, client = _context()

    await apply_data_migrations(app_id="app_1", migrations=[_migration()], persistence=context, history_client=client)

    history = _history_collection(client)
    claim = history.find_one_and_update_calls[0][1]["$setOnInsert"]
    assert claim["status"] == "in_progress"
    assert claim["migration_id"] == "m_001"
    assert claim["lock_owner"].startswith("migration_")
    assert claim["claimed_at"]
    assert claim["operations_summary"][0]["type"] == "ensure_collection"


@pytest.mark.asyncio
async def test_history_collection_ensures_unique_app_migration_index() -> None:
    context, client = _context()

    await apply_data_migrations(app_id="app_1", migrations=[_migration()], persistence=context, history_client=client)

    history = _history_collection(client)
    assert history.create_index_calls[0] == (
        [("app_id", 1), ("migration_id", 1)],
        {"name": "adm_app_migration", "unique": True},
    )


@pytest.mark.asyncio
async def test_failed_migration_updates_history_with_error_details() -> None:
    context, client = _context()
    migration = _migration(operations=[{"type": "rewrite_documents", "module_id": "projects", "entity_name": "projects"}])

    with pytest.raises(DatabaseMigrationError, match="operation 0"):
        await apply_data_migrations(app_id="app_1", migrations=[migration], persistence=context, history_client=client)

    record = _history_collection(client).document_by_key[("app_1", "m_001")]
    assert record["status"] == "failed"
    assert record["error_message"]
    assert record["failed_operation_index"] == 0
    assert record["failed_operation_summary"] == {
        "type": "rewrite_documents",
        "module_id": "projects",
        "entity_name": "projects",
    }
    assert record["failed_at"]


@pytest.mark.asyncio
async def test_already_applied_same_hash_skips() -> None:
    context, client = _context()
    migration = _migration()
    history = _history_collection(client)
    history.document_by_key[("app_1", "m_001")] = {
        "app_id": "app_1",
        "migration_id": "m_001",
        "migration_hash": migration_hash(migration),
        "status": "applied",
    }

    count = await apply_data_migrations(app_id="app_1", migrations=[migration], persistence=context, history_client=client)

    assert count == 0
    assert _app_collection(client).create_index_calls == []
    assert history.find_one_and_update_calls


@pytest.mark.asyncio
async def test_already_applied_different_hash_raises() -> None:
    context, client = _context()
    history = _history_collection(client)
    history.document_by_key[("app_1", "m_001")] = {
        "app_id": "app_1",
        "migration_id": "m_001",
        "migration_hash": "different",
        "status": "applied",
    }

    with pytest.raises(DatabaseMigrationError, match="different hash"):
        await apply_data_migrations(app_id="app_1", migrations=[_migration()], persistence=context, history_client=client)


@pytest.mark.asyncio
async def test_existing_in_progress_migration_raises() -> None:
    context, client = _context()
    migration = _migration()
    _history_collection(client).document_by_key[("app_1", "m_001")] = {
        "app_id": "app_1",
        "migration_id": "m_001",
        "migration_hash": migration_hash(migration),
        "status": "in_progress",
    }

    with pytest.raises(DatabaseMigrationError, match="m_001.*already in progress"):
        await apply_data_migrations(app_id="app_1", migrations=[migration], persistence=context, history_client=client)


@pytest.mark.asyncio
async def test_existing_failed_migration_raises_until_operator_clears_record() -> None:
    context, client = _context()
    migration = _migration()
    _history_collection(client).document_by_key[("app_1", "m_001")] = {
        "app_id": "app_1",
        "migration_id": "m_001",
        "migration_hash": migration_hash(migration),
        "status": "failed",
    }

    with pytest.raises(DatabaseMigrationError, match="m_001.*clear the failed history record"):
        await apply_data_migrations(app_id="app_1", migrations=[migration], persistence=context, history_client=client)


@pytest.mark.asyncio
async def test_duplicate_claim_race_is_handled_deterministically() -> None:
    context, client = _context()
    migration = _migration()
    history = _history_collection(client)
    history.document_by_key[("app_1", "m_001")] = {
        "app_id": "app_1",
        "migration_id": "m_001",
        "migration_hash": migration_hash(migration),
        "status": "in_progress",
        "lock_owner": "other_instance",
    }
    history.fail_next_find_one_and_update = True

    with pytest.raises(DatabaseMigrationError, match="m_001.*already in progress"):
        await apply_data_migrations(app_id="app_1", migrations=[migration], persistence=context, history_client=client)

    assert _app_collection(client).create_index_calls == []


@pytest.mark.asyncio
async def test_migrations_are_applied_in_loaded_order(tmp_path: Path) -> None:
    _write_migration(tmp_path, "002.json", _migration("m_002", []))
    _write_migration(tmp_path, "001.json", _migration("m_001", []))
    context, client = _context()

    await apply_data_migrations(
        app_id="app_1",
        migrations=load_data_migrations(tmp_path),
        persistence=context,
        history_client=client,
    )

    history = _history_collection(client)
    in_progress_ids = [
        call[0]["migration_id"]
        for call in history.find_one_and_update_calls
        if call[1].get("$setOnInsert", {}).get("status") == "in_progress"
    ]
    assert in_progress_ids == ["m_001", "m_002"]


@pytest.mark.parametrize("operation_type", ["drop_collection", "delete_field", "rename_field"])
def test_destructive_operations_are_rejected(tmp_path: Path, operation_type: str) -> None:
    _write_migration(tmp_path, "001.json", _migration(operations=[{"type": operation_type}]))

    with pytest.raises(DatabaseMigrationError, match="destructive"):
        load_data_migrations(tmp_path)


@pytest.mark.asyncio
async def test_no_ctx_db_is_introduced() -> None:
    from mozaiksai.core.runtime.composition.module_context import ModuleContext

    assert not hasattr(ModuleContext(app_id="app_1"), "db")


@pytest.mark.asyncio
async def test_migration_health_report_empty_summary() -> None:
    client = FakeMongoClient()

    report = await get_migration_health_report(client=client)

    assert report == {
        "summary": {"total": 0, "applied": 0, "in_progress": 0, "failed": 0, "unknown": 0},
        "items": [],
        "has_blockers": False,
        "has_unknown_statuses": False,
    }


@pytest.mark.asyncio
async def test_migration_health_report_counts_statuses() -> None:
    client = FakeMongoClient()
    history = _history_collection(client)
    history.document_by_key[("app_1", "m_001")] = _history_doc(status="applied")
    history.document_by_key[("app_1", "m_002")] = _history_doc(migration_id="m_002", status="in_progress")
    history.document_by_key[("app_1", "m_003")] = _history_doc(migration_id="m_003", status="failed")

    report = await get_migration_health_report(client=client)

    assert report["summary"] == {"total": 3, "applied": 1, "in_progress": 1, "failed": 1, "unknown": 0}
    assert report["has_blockers"] is True


@pytest.mark.asyncio
async def test_migration_health_report_has_blocker_for_failed() -> None:
    client = FakeMongoClient()
    _history_collection(client).document_by_key[("app_1", "m_001")] = _history_doc(status="failed")

    report = await get_migration_health_report(client=client)

    assert report["has_blockers"] is True
    assert report["items"][0]["is_blocker"] is True
    assert report["items"][0]["error_message"] == "index failed"
    assert report["items"][0]["failed_operation_index"] == 1


@pytest.mark.asyncio
async def test_migration_health_report_has_blocker_for_in_progress() -> None:
    client = FakeMongoClient()
    _history_collection(client).document_by_key[("app_1", "m_001")] = _history_doc(status="in_progress")

    report = await get_migration_health_report(client=client)

    assert report["has_blockers"] is True
    assert report["items"][0]["is_blocker"] is True


@pytest.mark.asyncio
async def test_migration_health_report_all_applied_has_no_blockers() -> None:
    client = FakeMongoClient()
    history = _history_collection(client)
    history.document_by_key[("app_1", "m_001")] = _history_doc(status="applied")
    history.document_by_key[("app_1", "m_002")] = _history_doc(migration_id="m_002", status="applied")

    report = await get_migration_health_report(client=client)

    assert report["has_blockers"] is False
    assert all(item["is_blocker"] is False for item in report["items"])


@pytest.mark.asyncio
async def test_migration_health_report_app_id_filter() -> None:
    client = FakeMongoClient()
    history = _history_collection(client)
    history.document_by_key[("app_1", "m_001")] = _history_doc(app_id="app_1")
    history.document_by_key[("app_2", "m_001")] = _history_doc(app_id="app_2")

    report = await get_migration_health_report(app_id="app_2", client=client)

    assert report["summary"]["total"] == 1
    assert report["items"][0]["app_id"] == "app_2"
    assert history.find_calls[-1] == {"app_id": "app_2"}


@pytest.mark.asyncio
async def test_migration_health_report_status_filter() -> None:
    client = FakeMongoClient()
    history = _history_collection(client)
    history.document_by_key[("app_1", "m_001")] = _history_doc(status="applied")
    history.document_by_key[("app_1", "m_002")] = _history_doc(migration_id="m_002", status="failed")

    report = await get_migration_health_report(status="failed", client=client)

    assert report["summary"] == {"total": 1, "applied": 0, "in_progress": 0, "failed": 1, "unknown": 0}
    assert report["items"][0]["status"] == "failed"
    assert history.find_calls[-1] == {"status": "failed"}


@pytest.mark.asyncio
async def test_migration_health_report_limit_is_enforced() -> None:
    client = FakeMongoClient()
    history = _history_collection(client)
    for index in range(3):
        migration_id = f"m_{index}"
        history.document_by_key[("app_1", migration_id)] = _history_doc(migration_id=migration_id)

    report = await get_migration_health_report(client=client, limit=2)

    assert report["summary"]["total"] == 2
    assert len(report["items"]) == 2


@pytest.mark.asyncio
async def test_migration_health_report_surfaces_unknown_status() -> None:
    client = FakeMongoClient()
    _history_collection(client).document_by_key[("app_1", "m_001")] = _history_doc(status="paused")

    report = await get_migration_health_report(client=client)

    assert report["summary"]["unknown"] == 1
    assert report["has_unknown_statuses"] is True
    assert report["has_blockers"] is False
    assert report["items"][0]["unknown_status"] is True


@pytest.mark.asyncio
async def test_migration_health_report_does_not_mutate_history_collection() -> None:
    client = FakeMongoClient()
    history = _history_collection(client)
    history.document_by_key[("app_1", "m_001")] = _history_doc(status="failed")

    await get_migration_health_report(client=client)

    assert history.update_one_calls == []
    assert history.find_one_and_update_calls == []
    assert history.create_index_calls == []
    assert history.document_by_key[("app_1", "m_001")]["status"] == "failed"


@pytest.mark.asyncio
async def test_migration_health_report_uses_injected_client_database_name() -> None:
    client = FakeMongoClient()
    history = client["custom_history"][APP_DATA_MIGRATIONS_COLLECTION]
    history.document_by_key[("app_1", "m_001")] = _history_doc(status="applied")

    report = await get_migration_health_report(client=client, database_name="custom_history")

    assert report["summary"]["total"] == 1
    assert client["mozaiksai"][APP_DATA_MIGRATIONS_COLLECTION].find_calls == []


@pytest.mark.asyncio
async def test_platform_startup_calls_migration_application_after_index_application(monkeypatch: pytest.MonkeyPatch) -> None:
    from mozaiksai.hosts import platform

    calls: list[str] = []
    migration = _migration()

    async def fake_load(_path: str):
        return AppLoadResult(
            definition=AppDefinition(name="Migration Test", version="1.0", config={"appId": "app_1"}),
            modules=[],
            data_contract={"version": "1", "app_id": "app_1", "surfaces": []},
            data_entities_by_key={},
        )

    async def fake_apply_indexes(_intent, *, app_id=None):
        calls.append("indexes")
        return 0

    def fake_load_migrations(_root):
        calls.append("load_migrations")
        return [migration]

    async def fake_apply_migrations(*, app_id, migrations):
        calls.append("migrations")
        assert app_id == "app_1"
        assert migrations == [migration]
        return 1

    class FakeHooks:
        async def run_startup(self, _app):
            return None

    monkeypatch.setattr(platform.AppLoader, "load", fake_load)
    monkeypatch.setattr(platform, "apply_database_indexes", fake_apply_indexes)
    monkeypatch.setattr(platform, "load_data_migrations", fake_load_migrations)
    monkeypatch.setattr(platform, "apply_data_migrations", fake_apply_migrations)
    monkeypatch.setattr(platform, "get_platform_hooks", lambda: FakeHooks())

    await platform._platform_startup()

    assert calls == ["indexes", "load_migrations", "migrations"]


@pytest.mark.asyncio
async def test_platform_startup_logs_migration_failure_and_continues(monkeypatch: pytest.MonkeyPatch) -> None:
    from mozaiksai.hosts import platform

    warnings: list[str] = []

    async def fake_load(_path: str):
        return AppLoadResult(
            definition=AppDefinition(name="Migration Test", version="1.0", config={"appId": "app_1"}),
            modules=[],
            data_contract=None,
            data_entities_by_key={},
        )

    def fake_load_migrations(_root):
        return [_migration()]

    async def fail_apply_migrations(*, app_id, migrations):
        raise RuntimeError("mongo unavailable")

    class FakeHooks:
        async def run_startup(self, _app):
            return None

    monkeypatch.setattr(platform.AppLoader, "load", fake_load)
    monkeypatch.setattr(platform, "load_data_migrations", fake_load_migrations)
    monkeypatch.setattr(platform, "apply_data_migrations", fail_apply_migrations)
    monkeypatch.setattr(platform, "get_platform_hooks", lambda: FakeHooks())
    monkeypatch.setattr(platform.logger, "warning", lambda message, *args: warnings.append(message % args))
    monkeypatch.delenv("MOZAIKS_DATABASE_STARTUP_POLICY", raising=False)

    await platform._platform_startup()

    assert any("data_migrations_NOT_APPLIED" in warning for warning in warnings)


@pytest.mark.asyncio
async def test_platform_startup_best_effort_logs_concurrent_migration_claim_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from mozaiksai.hosts import platform

    warnings: list[str] = []

    async def fake_load(_path: str):
        return AppLoadResult(
            definition=AppDefinition(name="Migration Test", version="1.0", config={"appId": "app_1"}),
            modules=[],
            data_contract=None,
            data_entities_by_key={},
        )

    def fake_load_migrations(_root):
        return [_migration()]

    async def fail_apply_migrations(*, app_id, migrations):
        raise DatabaseMigrationError("Migration 'm_001' for app_id='app_1' is already in progress")

    class FakeHooks:
        async def run_startup(self, _app):
            return None

    monkeypatch.delenv("MOZAIKS_DATABASE_STARTUP_POLICY", raising=False)
    monkeypatch.setattr(platform.AppLoader, "load", fake_load)
    monkeypatch.setattr(platform, "load_data_migrations", fake_load_migrations)
    monkeypatch.setattr(platform, "apply_data_migrations", fail_apply_migrations)
    monkeypatch.setattr(platform, "get_platform_hooks", lambda: FakeHooks())
    monkeypatch.setattr(platform.logger, "warning", lambda message, *args: warnings.append(message % args))

    await platform._platform_startup()

    assert any("data_migrations_NOT_APPLIED" in warning and "m_001" in warning for warning in warnings)


def test_database_startup_policy_defaults_to_best_effort(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MOZAIKS_DATABASE_STARTUP_POLICY", raising=False)

    assert get_database_startup_policy() == "best_effort"


def test_database_startup_policy_accepts_required(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MOZAIKS_DATABASE_STARTUP_POLICY", "required")

    assert get_database_startup_policy() == "required"


def test_database_startup_policy_accepts_best_effort(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MOZAIKS_DATABASE_STARTUP_POLICY", "best_effort")

    assert get_database_startup_policy() == "best_effort"


def test_database_startup_policy_rejects_invalid_value(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MOZAIKS_DATABASE_STARTUP_POLICY", "ignore")

    with pytest.raises(ValueError, match="MOZAIKS_DATABASE_STARTUP_POLICY"):
        get_database_startup_policy()


@pytest.mark.asyncio
async def test_platform_startup_required_migration_failure_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    from mozaiksai.hosts import platform

    async def fake_load(_path: str):
        return AppLoadResult(
            definition=AppDefinition(name="Migration Test", version="1.0", config={"appId": "app_1"}),
            modules=[],
            data_contract=None,
            data_entities_by_key={},
        )

    def fake_load_migrations(_root):
        return [_migration()]

    async def fail_apply_migrations(*, app_id, migrations):
        raise RuntimeError("mongo unavailable")

    monkeypatch.setenv("MOZAIKS_DATABASE_STARTUP_POLICY", "required")
    monkeypatch.setattr(platform.AppLoader, "load", fake_load)
    monkeypatch.setattr(platform, "load_data_migrations", fake_load_migrations)
    monkeypatch.setattr(platform, "apply_data_migrations", fail_apply_migrations)

    with pytest.raises(platform.DatabaseStartupError, match="Data migrations were not applied"):
        await platform._platform_startup()


@pytest.mark.asyncio
async def test_platform_startup_required_concurrent_migration_claim_failure_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from mozaiksai.hosts import platform

    async def fake_load(_path: str):
        return AppLoadResult(
            definition=AppDefinition(name="Migration Test", version="1.0", config={"appId": "app_1"}),
            modules=[],
            data_contract=None,
            data_entities_by_key={},
        )

    def fake_load_migrations(_root):
        return [_migration()]

    async def fail_apply_migrations(*, app_id, migrations):
        raise DatabaseMigrationError("Migration 'm_001' for app_id='app_1' is already in progress")

    monkeypatch.setenv("MOZAIKS_DATABASE_STARTUP_POLICY", "required")
    monkeypatch.setattr(platform.AppLoader, "load", fake_load)
    monkeypatch.setattr(platform, "load_data_migrations", fake_load_migrations)
    monkeypatch.setattr(platform, "apply_data_migrations", fail_apply_migrations)

    with pytest.raises(platform.DatabaseStartupError, match="m_001"):
        await platform._platform_startup()


@pytest.mark.asyncio
async def test_platform_startup_without_database_artifacts_is_unaffected(monkeypatch: pytest.MonkeyPatch) -> None:
    from mozaiksai.hosts import platform

    async def fake_load(_path: str):
        return AppLoadResult(
            definition=AppDefinition(name="No Persistence", version="1.0", config={"appId": "app_1"}),
            modules=[],
            data_contract=None,
            data_entities_by_key={},
        )

    class FakeHooks:
        async def run_startup(self, _app):
            return None

    monkeypatch.setenv("MOZAIKS_DATABASE_STARTUP_POLICY", "required")
    monkeypatch.setattr(platform.AppLoader, "load", fake_load)
    monkeypatch.setattr(platform, "load_data_migrations", lambda _root: [])
    monkeypatch.setattr(platform, "get_platform_hooks", lambda: FakeHooks())

    await platform._platform_startup()
