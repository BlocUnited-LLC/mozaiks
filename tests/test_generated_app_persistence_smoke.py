from __future__ import annotations

import json
import re
import textwrap
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import pytest

import mozaiksai.core.runtime.composition.module_executor as module_executor_module
from mozaiksai.core.runtime.app.loader import AppLoader
from mozaiksai.core.runtime.composition.module_executor import ModuleExecutor, ModuleRequest
from mozaiksai.core.runtime.persistence import (
    apply_database_indexes,
    apply_data_migrations,
    load_data_migrations,
)


class FakePersistenceCollection:
    def __init__(
        self,
        *,
        app_id: str,
        module_id: str,
        entity_name: str,
        rows: list[dict[str, Any]],
        scope_metadata: dict[str, Any],
    ) -> None:
        self.app_id = app_id
        self.module_id = module_id
        self.entity_name = entity_name
        self.rows = rows
        self.scope_metadata = scope_metadata
        self.indexes: dict[str, dict[str, Any]] = {}
        self.inserted: list[dict[str, Any]] = []
        self.find_many_queries: list[dict[str, Any]] = []

    def _scoped_query(self, query: Mapping[str, Any] | None = None) -> dict[str, Any]:
        extra = dict(query or {})
        if "app_id" in extra and extra["app_id"] != self.app_id:
            raise ValueError("query app_id cannot override context app_id")
        return {"app_id": self.app_id, **extra}

    @staticmethod
    def _matches(row: Mapping[str, Any], query: Mapping[str, Any]) -> bool:
        return all(row.get(key) == value for key, value in query.items())

    async def find_one(self, query: Mapping[str, Any], projection=None) -> dict[str, Any] | None:
        scoped = self._scoped_query(query)
        for row in self.rows:
            if self._matches(row, scoped):
                return dict(row)
        return None

    async def find_many(
        self,
        query: Mapping[str, Any],
        *,
        limit: int = 50,
        sort: Sequence[tuple[str, int]] | None = None,
        projection=None,
    ) -> list[dict[str, Any]]:
        scoped = self._scoped_query(query)
        self.find_many_queries.append(scoped)
        rows = [dict(row) for row in self.rows if self._matches(row, scoped)]
        if sort:
            for field, direction in reversed(list(sort)):
                rows.sort(key=lambda row: row.get(field), reverse=direction == -1)
        return rows[: max(1, min(int(limit), 100))]

    async def insert_one(self, document: Mapping[str, Any]) -> dict[str, Any]:
        if "app_id" in document and document["app_id"] != self.app_id:
            raise ValueError("document app_id cannot override context app_id")
        row = {**dict(document), **self.scope_metadata}
        self.rows.append(row)
        self.inserted.append(row)
        return {"inserted_id": row.get("_id") or row.get("project_id") or row.get("task_id")}

    async def update_one(
        self,
        query: Mapping[str, Any],
        update: Mapping[str, Any],
        *,
        upsert: bool = False,
    ) -> dict[str, int]:
        scoped = self._scoped_query(query)
        for row in self.rows:
            if self._matches(row, scoped):
                row.update(dict(update.get("$set", {})))
                return {"matched_count": 1}
        return {"matched_count": 0}

    async def count(self, query: Mapping[str, Any]) -> int:
        return len(await self.find_many(query, limit=100))

    async def ensure_indexes(self, indexes: Sequence[Mapping[str, Any]]) -> None:
        for index in indexes:
            name = str(index.get("name") or "_".join(str(key[0]) for key in index.get("keys", [])))
            self.indexes[name] = dict(index)


class FakePersistenceContext:
    """In-memory test double for ModulePersistenceContext.

    The generated-app smoke monkeypatches ModuleExecutor's persistence context
    symbol to this class only inside one test. That keeps the smoke end-to-end
    through ModuleExecutor without requiring a real MongoDB server. Production
    ModuleExecutor still imports and uses MongoPersistenceContext.
    """

    stores: dict[tuple[str, str], list[dict[str, Any]]] = {}
    collections_by_app: dict[tuple[str, str, str], FakePersistenceCollection] = {}
    constructed: list[FakePersistenceContext] = []

    def __init__(
        self,
        *,
        app_id: str,
        app_slug: str | None = None,
        tenant_id: str | None = None,
        workspace_id: str | None = None,
        user_id: str | None = None,
        database_name: str | None = None,
        client: Any | None = None,
    ) -> None:
        self._app_id = app_id
        self._scope_metadata = {"app_id": app_id}
        if tenant_id:
            self._scope_metadata["tenant_id"] = tenant_id
        if workspace_id:
            self._scope_metadata["workspace_id"] = workspace_id
        if user_id:
            self._scope_metadata["user_id"] = user_id
        self.collections_requested: list[tuple[str, str]] = []
        self.constructed.append(self)

    @property
    def app_id(self) -> str:
        return self._app_id

    def collection(self, module_id: str, entity_name: str) -> FakePersistenceCollection:
        self.collections_requested.append((module_id, entity_name))
        rows = self.stores.setdefault((module_id, entity_name), [])
        key = (self.app_id, module_id, entity_name)
        if key not in self.collections_by_app:
            self.collections_by_app[key] = FakePersistenceCollection(
                app_id=self.app_id,
                module_id=module_id,
                entity_name=entity_name,
                rows=rows,
                scope_metadata=dict(self._scope_metadata),
            )
        return self.collections_by_app[key]

    def scope_filter(self, extra: Mapping[str, Any] | None = None) -> dict[str, Any]:
        extra_filter = dict(extra or {})
        if "app_id" in extra_filter and extra_filter["app_id"] != self.app_id:
            raise ValueError("extra app_id cannot override context app_id")
        return {"app_id": self.app_id, **extra_filter}

    async def ensure_indexes(self) -> None:
        return None

    @classmethod
    def reset(cls) -> None:
        cls.stores = {}
        cls.collections_by_app = {}
        cls.constructed = []


class FakeCursor:
    def __init__(self, rows: list[dict[str, Any]] | None = None) -> None:
        self.rows = rows or []

    async def to_list(self, length: int | None = None) -> list[dict[str, Any]]:
        return list(self.rows)


class FakeHistoryCollection:
    def __init__(self) -> None:
        self.index_rows: list[dict[str, Any]] = []
        self.records: dict[tuple[str, str], dict[str, Any]] = {}
        self.update_calls: list[tuple[dict[str, Any], dict[str, Any], bool]] = []

    def list_indexes(self) -> FakeCursor:
        return FakeCursor(self.index_rows)

    async def create_index(self, keys, **kwargs):
        name = str(kwargs.get("name") or "_".join(field for field, _ in keys))
        self.index_rows.append({"name": name, "key": dict(keys)})
        return name

    async def find_one(self, query: Mapping[str, Any]) -> dict[str, Any] | None:
        key = (str(query.get("app_id") or ""), str(query.get("migration_id") or ""))
        return self.records.get(key)

    async def find_one_and_update(
        self,
        query: Mapping[str, Any],
        update: Mapping[str, Any],
        *,
        upsert: bool = False,
        return_document: Any = None,
    ) -> dict[str, Any] | None:
        key = (str(query.get("app_id") or ""), str(query.get("migration_id") or ""))
        existing = self.records.get(key)
        if existing is not None:
            return existing
        if not upsert:
            return None
        record = dict(update.get("$setOnInsert", {}))
        self.records[key] = record
        return record

    async def update_one(
        self,
        query: Mapping[str, Any],
        update: Mapping[str, Any],
        *,
        upsert: bool = False,
    ) -> dict[str, int]:
        self.update_calls.append((dict(query), dict(update), upsert))
        key = (str(query.get("app_id") or ""), str(query.get("migration_id") or ""))
        record = dict(self.records.get(key) or {})
        if upsert and not record:
            record.update(dict(update.get("$setOnInsert", {})))
        record.update(dict(update.get("$set", {})))
        self.records[key] = record
        return {"matched_count": 1}


class FakeHistoryDatabase:
    def __init__(self) -> None:
        self.collections: dict[str, FakeHistoryCollection] = {}

    def __getitem__(self, name: str) -> FakeHistoryCollection:
        if name not in self.collections:
            self.collections[name] = FakeHistoryCollection()
        return self.collections[name]


class FakeHistoryClient:
    def __init__(self) -> None:
        self.databases: dict[str, FakeHistoryDatabase] = {}

    def __getitem__(self, name: str) -> FakeHistoryDatabase:
        if name not in self.databases:
            self.databases[name] = FakeHistoryDatabase()
        return self.databases[name]


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(content).strip() + "\n", encoding="utf-8")


def _write_generated_persistence_app(root: Path) -> None:
    _write_json(
        root / "app.json",
        {
            "name": "Projects Tasks Smoke",
            "version": "1.0.0",
            "description": "Generated persistence smoke app",
        },
    )
    _write_json(
        root / "data" / "contract.json",
        {
            "version": "1",
            "app_id": "smoke_app",
            "surfaces": [
                {
                    "surface_id": "projects",
                    "surface_kind": "module",
                    "collections": [
                        {
                            "module_id": "projects",
                            "name": "projects",
                            "entity_name": "projects",
                            "indexes": [
                                {
                                    "name": "project_owner_created_at",
                                    "keys": [
                                        {"field": "owner_id", "order": 1},
                                        {"field": "created_at", "order": -1},
                                    ],
                                }
                            ],
                        }
                    ],
                },
                {
                    "surface_id": "tasks",
                    "surface_kind": "module",
                    "collections": [
                        {
                            "module_id": "tasks",
                            "name": "tasks",
                            "entity_name": "tasks",
                            "indexes": [
                                {
                                    "name": "task_project_status",
                                    "keys": [["project_id", 1], ["status", 1]],
                                }
                            ],
                        }
                    ],
                },
            ],
        },
    )
    _write_json(
        root / "data" / "migrations" / "001_projects_tasks_indexes.json",
        {
            "migration_id": "001_projects_tasks_indexes",
            "version": "1",
            "description": "Ensure generated project/task collections and indexes.",
            "operations": [
                {"type": "ensure_collection", "module_id": "projects", "entity_name": "projects"},
                {
                    "type": "ensure_index",
                    "module_id": "projects",
                    "entity_name": "projects",
                    "index": {
                        "name": "project_owner_created_at",
                        "keys": [
                            {"field": "owner_id", "order": 1},
                            {"field": "created_at", "order": -1},
                        ],
                    },
                },
                {"type": "ensure_collection", "module_id": "tasks", "entity_name": "tasks"},
                {
                    "type": "ensure_index",
                    "module_id": "tasks",
                    "entity_name": "tasks",
                    "index": {
                        "name": "task_project_status",
                        "keys": [["project_id", 1], ["status", 1]],
                    },
                },
            ],
        },
    )
    _write_module(root, module_id="projects", entity_name="projects", id_field="project_id", title_field="name")
    _write_module(root, module_id="tasks", entity_name="tasks", id_field="task_id", title_field="title")


def _write_module(
    root: Path,
    *,
    module_id: str,
    entity_name: str,
    id_field: str,
    title_field: str,
) -> None:
    class_prefix = module_id.title().replace("_", "")
    create_action = f"create_{entity_name[:-1] if entity_name.endswith('s') else entity_name}"
    list_action = f"list_{entity_name}"
    _write_text(
        root / "modules" / module_id / "module.yaml",
        f"""
        schema_version: mozaiks.module.v1
        module:
          id: {module_id}
          display_name: {class_prefix}
          version: 1.0.0
          handler: backend.handler:{class_prefix}Handler
        actions:
          - id: {create_action}
            description: Create {entity_name} record.
            handler_method: {create_action}
            input_schema:
              type: object
            output_schema:
              type: object
            emits:
              - domain.{module_id}.{entity_name[:-1] if entity_name.endswith('s') else entity_name}_created
          - id: {list_action}
            description: List {entity_name} records.
            handler_method: {list_action}
            input_schema:
              type: object
            output_schema:
              type: object
        """,
    )
    _write_text(
        root / "modules" / module_id / "contracts" / "events.yaml",
        f"""
        schema_version: mozaiks.events.v1
        events:
          - type: domain.{module_id}.{entity_name[:-1] if entity_name.endswith('s') else entity_name}_created
            version: 1
            producer: {module_id}
            description: Record created.
            payload_schema:
              type: object
        """,
    )
    _write_text(root / "modules" / module_id / "backend" / "__init__.py", "")
    _write_text(
        root / "modules" / module_id / "backend" / "handler.py",
        f"""
        from .service import {class_prefix}Service


        class {class_prefix}Handler:
            def __init__(self):
                self.service = {class_prefix}Service()

            async def {create_action}(self, ctx, **payload):
                return await self.service.{create_action}(ctx, payload=payload)

            async def {list_action}(self, ctx, **payload):
                return await self.service.{list_action}(ctx, filters=payload)
        """,
    )
    _write_text(
        root / "modules" / module_id / "backend" / "service.py",
        f"""
        from .policy import scoped_query
        from .repo import {class_prefix}Repo
        from .schemas import build_record


        class {class_prefix}Service:
            def __init__(self, repo=None):
                self.repo = repo or {class_prefix}Repo()

            async def {create_action}(self, ctx, *, payload):
                record = build_record(ctx, payload=payload)
                stored = await self.repo.create(ctx, record=record)
                await ctx.emit("domain.{module_id}.{entity_name[:-1] if entity_name.endswith('s') else entity_name}_created", {{"{id_field}": stored["{id_field}"]}})
                return stored

            async def {list_action}(self, ctx, *, filters=None):
                query = scoped_query(filters or {{}})
                records = await self.repo.list(ctx, query=query, limit=int((filters or {{}}).get("limit") or 50))
                return {{"items": records, "count": len(records)}}
        """,
    )
    _write_text(
        root / "modules" / module_id / "backend" / "repo.py",
        f"""
        class {class_prefix}Repo:
            async def _collection(self, ctx):
                persistence = getattr(ctx, "persistence", None)
                if persistence is None:
                    raise RuntimeError("Persistence is not available for this app context.")
                return persistence.collection("{module_id}", "{entity_name}")

            async def create(self, ctx, *, record):
                collection = await self._collection(ctx)
                await collection.insert_one(record)
                return record

            async def list(self, ctx, *, query=None, limit=50):
                collection = await self._collection(ctx)
                return await collection.find_many(query or {{}}, limit=limit)
        """,
    )
    _write_text(
        root / "modules" / module_id / "backend" / "policy.py",
        """
        def scoped_query(filters):
            query = {}
            for key in ("project_id", "status"):
                value = filters.get(key)
                if value:
                    query[key] = value
            return query
        """,
    )
    _write_text(
        root / "modules" / module_id / "backend" / "schemas.py",
        f"""
        from datetime import UTC, datetime
        from typing import TypedDict
        from uuid import uuid4


        class Record(TypedDict):
            {id_field}: str
            owner_id: str | None
            {title_field}: str
            status: str
            created_at: str


        def build_record(ctx, *, payload):
            now = datetime.now(UTC).isoformat()
            title = str(payload.get("{title_field}") or payload.get("name") or payload.get("title") or "").strip()
            if not title:
                raise ValueError("{title_field} is required")
            record = {{
                "{id_field}": str(payload.get("{id_field}") or uuid4().hex),
                "owner_id": getattr(ctx, "user_id", None),
                "{title_field}": title,
                "status": str(payload.get("status") or "open"),
                "created_at": now,
            }}
            if payload.get("project_id"):
                record["project_id"] = payload["project_id"]
            return record
        """,
    )


def _all_fixture_files(root: Path) -> list[Path]:
    return [path for path in root.rglob("*") if path.is_file()]


def _repo_collection_calls(repo_text: str) -> set[tuple[str, str]]:
    return set(re.findall(r'collection\("([^"]+)",\s*"([^"]+)"\)', repo_text))


def test_fixture_app_structure_is_canonical(tmp_path: Path) -> None:
    _write_generated_persistence_app(tmp_path)

    assert (tmp_path / "app.json").exists()
    assert (tmp_path / "data" / "contract.json").exists()
    assert (tmp_path / "data" / "migrations" / "001_projects_tasks_indexes.json").exists()
    for module_id in ("projects", "tasks"):
        module_root = tmp_path / "modules" / module_id
        assert (module_root / "module.yaml").exists()
        assert (module_root / "contracts" / "events.yaml").exists()
        for filename in ("handler.py", "service.py", "repo.py", "policy.py", "schemas.py"):
            assert (module_root / "backend" / filename).exists()
        assert not (module_root / "backend" / "models.py").exists()
        assert not (module_root / "backend" / "models").exists()
    assert not (tmp_path / "backend" / "database" / "schema.json").exists()


@pytest.mark.asyncio
async def test_app_loader_loads_data_contract_and_modules(tmp_path: Path) -> None:
    _write_generated_persistence_app(tmp_path)

    result = await AppLoader.load(str(tmp_path))

    assert result.data_contract is not None
    assert ("projects", "projects") in result.data_entities_by_key
    assert ("tasks", "tasks") in result.data_entities_by_key
    assert {module.name for module in result.modules} == {"projects", "tasks"}


def test_generated_fixture_code_uses_only_canonical_persistence(tmp_path: Path) -> None:
    _write_generated_persistence_app(tmp_path)
    all_text = "\n".join(path.read_text(encoding="utf-8") for path in _all_fixture_files(tmp_path))

    assert "ctx.db" not in all_text
    assert "context.db" not in all_text
    assert "get_mongo_client" not in all_text
    assert "pymongo" not in all_text
    assert "motor" not in all_text
    assert "backend/models.py" not in all_text
    assert "backend/database/schema.json" not in all_text
    assert '"mozaiks_apps"' not in all_text
    assert '"mozaiksai"' not in all_text

    for module_id in ("projects", "tasks"):
        repo_text = (tmp_path / "modules" / module_id / "backend" / "repo.py").read_text(encoding="utf-8")
        assert f'persistence.collection("{module_id}", "{module_id}")' in repo_text


def test_data_contract_matches_repo_collection_calls_and_is_additive(tmp_path: Path) -> None:
    _write_generated_persistence_app(tmp_path)
    intent = json.loads((tmp_path / "data" / "contract.json").read_text(encoding="utf-8"))
    migrations = load_data_migrations(tmp_path)

    intent_keys = {
        (collection["module_id"], collection.get("entity_name") or collection["name"])
        for surface in intent["surfaces"]
        for collection in surface["collections"]
    }
    repo_keys = set()
    for module_id in ("projects", "tasks"):
        repo_text = (tmp_path / "modules" / module_id / "backend" / "repo.py").read_text(encoding="utf-8")
        repo_keys |= _repo_collection_calls(repo_text)

    assert repo_keys == {("projects", "projects"), ("tasks", "tasks")}
    assert repo_keys <= intent_keys
    assert all(operation["type"] in {"ensure_collection", "ensure_index"} for migration in migrations for operation in migration["operations"])


@pytest.mark.asyncio
async def test_indexes_and_migrations_apply_with_fake_persistence(tmp_path: Path) -> None:
    _write_generated_persistence_app(tmp_path)
    load_result = await AppLoader.load(str(tmp_path))
    FakePersistenceContext.reset()
    persistence = FakePersistenceContext(app_id="smoke_app")

    applied_indexes = await apply_database_indexes(
        load_result.data_contract,
        app_id="smoke_app",
        persistence=persistence,
    )
    migrations = load_data_migrations(tmp_path)
    applied_migrations = await apply_data_migrations(
        app_id="smoke_app",
        migrations=migrations,
        persistence=persistence,
        history_client=FakeHistoryClient(),
    )

    assert applied_indexes == 2
    assert applied_migrations == 1
    assert "project_owner_created_at" in persistence.collection("projects", "projects").indexes
    assert "task_project_status" in persistence.collection("tasks", "tasks").indexes


@pytest.mark.asyncio
async def test_module_executor_runs_generated_persistent_modules_end_to_end(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _write_generated_persistence_app(tmp_path)
    load_result = await AppLoader.load(str(tmp_path))
    FakePersistenceContext.reset()
    # Test-only dependency swap: this lets the generated fixture execute
    # create/list actions against in-memory app-scoped storage. The monkeypatch
    # is scoped to this test by pytest and does not alter production behavior.
    monkeypatch.setattr(module_executor_module, "MongoPersistenceContext", FakePersistenceContext)

    executor = ModuleExecutor()
    for module in load_result.modules:
        executor.register(
            module.name,
            module.handler,
            action_method_map=module.action_method_map,
            action_permissions=module.action_permissions_map,
            action_schemas=module.action_schemas_map,
        )

    created_project = await executor.execute(
        ModuleRequest(
            module="projects",
            action="create_project",
            app_id="app_a",
            tenant_id="tenant_1",
            user_id="user_1",
            params={"name": "Launch Plan", "project_id": "project_1"},
        )
    )
    listed_projects = await executor.execute(
        ModuleRequest(module="projects", action="list_projects", app_id="app_a", params={})
    )
    created_task = await executor.execute(
        ModuleRequest(
            module="tasks",
            action="create_task",
            app_id="app_a",
            user_id="user_1",
            params={"title": "Draft scope", "task_id": "task_1", "project_id": "project_1"},
        )
    )
    listed_tasks = await executor.execute(
        ModuleRequest(module="tasks", action="list_tasks", app_id="app_a", params={"project_id": "project_1"})
    )
    app_b_projects = await executor.execute(
        ModuleRequest(module="projects", action="list_projects", app_id="app_b", params={})
    )

    assert created_project.success is True
    assert created_project.data["project_id"] == "project_1"
    assert listed_projects.success is True
    assert listed_projects.data["count"] == 1
    assert listed_projects.data["items"][0]["app_id"] == "app_a"
    assert listed_projects.data["items"][0]["tenant_id"] == "tenant_1"
    assert listed_projects.data["items"][0]["user_id"] == "user_1"
    assert created_task.success is True
    assert created_task.data["task_id"] == "task_1"
    assert listed_tasks.success is True
    assert listed_tasks.data["count"] == 1
    assert listed_tasks.data["items"][0]["project_id"] == "project_1"
    assert app_b_projects.success is True
    assert app_b_projects.data == {"items": [], "count": 0}

    assert any(context.app_id == "app_a" for context in FakePersistenceContext.constructed)
    assert not hasattr(module_executor_module.ModuleRequest(module="x", action="y"), "db")

