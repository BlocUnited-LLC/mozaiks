from __future__ import annotations

import json
import re
import textwrap
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import pytest
import yaml

import mozaiksai.core.runtime.composition.module_executor as module_executor_module
from factory_app.workflows.AppGenerator.tools.assembly_phase import _merge_code_files
from mozaiksai.core.runtime.app.loader import AppLoader
from mozaiksai.core.runtime.composition.module_executor import ModuleExecutor, ModuleRequest
from mozaiksai.core.runtime.persistence import (
    apply_data_migrations,
    apply_database_indexes,
    load_data_migrations,
)
from tests.module_authority_test_helpers import trusted_framework_authority

ROOT = Path(__file__).resolve().parents[1]
FIXTURE_PATH = ROOT / "tests" / "fixtures" / "appplan_persistent_projects_output.json"

pytestmark = pytest.mark.skipif(
    not FIXTURE_PATH.exists(),
    reason="live persistent-projects AppBuildPlan fixture has not been saved",
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

    def list_indexes(self):
        return FakeCursor(
            [
                {"name": name, "key": dict(index["keys"]), **{k: v for k, v in index.items() if k not in {"name", "keys"}}}
                for name, index in self.indexes.items()
            ]
        )

    async def create_index(self, keys: list[tuple[str, int]], **kwargs: Any) -> str:
        name = str(kwargs.get("name") or "_".join(field for field, _ in keys))
        self.indexes[name] = {"name": name, "keys": list(keys), **{k: v for k, v in kwargs.items() if k != "name"}}
        return name

    async def ensure_indexes(self, indexes: Sequence[Mapping[str, Any]]) -> None:
        for index in indexes:
            name = str(index.get("name") or "_".join(str(key[0]) for key in index.get("keys", [])))
            self.indexes[name] = dict(index)


class FakePersistenceContext:
    """In-memory ModulePersistenceContext test double.

    The downstream smoke uses this only for unit-level runtime execution. It
    keeps the generated app path end-to-end through ModuleExecutor without
    requiring a real MongoDB server; production ModuleExecutor still constructs
    MongoPersistenceContext.
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
        self.constructed.append(self)

    @property
    def app_id(self) -> str:
        return self._app_id

    def collection(self, module_id: str, entity_name: str) -> FakePersistenceCollection:
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

    def list_indexes(self) -> FakeCursor:
        return FakeCursor(self.index_rows)

    async def create_index(self, keys, **kwargs):
        name = str(kwargs.get("name") or "_".join(field for field, _ in keys))
        self.index_rows.append(
            {"name": name, "key": dict(keys), **{key: value for key, value in kwargs.items() if key != "name"}}
        )
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


def _load_plan() -> dict[str, Any]:
    raw = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    return raw.get("AppBuildPlan", raw)


def _canonical_data_contract() -> dict[str, Any]:
    return {
        "version": "1",
        "app_id": "project_management",
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
    }


def _canonical_schema_migration() -> dict[str, Any]:
    return {
        "migration_id": "001_projects_tasks_indexes",
        "version": "1",
        "description": "Ensure project and task collection indexes.",
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
    }


def _database_output(plan: Mapping[str, Any]) -> dict[str, Any]:
    migration = dict(plan["pending_schema_migration"])
    return {
        "database_files": [
            {
                "path": "data/contract.json",
                "kind": "data_contract_json",
                "purpose": "Canonical generated data contract.",
                "entity_refs": ["projects", "tasks"],
                "content": json.dumps(_canonical_data_contract(), indent=2) + "\n",
            },
            {
                "path": f"data/migrations/{migration['migration_id']}.json",
                "kind": "database_migration_json",
                "purpose": "Additive generated project/task indexes.",
                "entity_refs": ["projects", "tasks"],
                "content": json.dumps(_canonical_schema_migration(), indent=2) + "\n",
            },
        ],
        "pending_schema_migration": migration,
        "code_files": [
            {"filename": "data/contract.json", "content": "BROKEN_MIRROR\n"},
            {
                "filename": f"data/migrations/{migration['migration_id']}.json",
                "content": "BROKEN_MIRROR\n",
            },
        ],
    }


def _module_output(module_id: str) -> dict[str, Any]:
    singular = module_id[:-1] if module_id.endswith("s") else module_id
    class_name = module_id.title().replace("_", "")
    id_field = f"{singular}_id"
    title_field = "name" if module_id == "projects" else "title"
    return {
        "code_files": [
            {
                "filename": f"modules/{module_id}/module.yaml",
                "content": yaml.safe_dump(
                    {
                        "schema_version": "mozaiks.module.v1",
                        "module": {
                            "id": module_id,
                            "display_name": class_name,
                            "version": "1.0.0",
                            "handler": f"backend.handler:{class_name}Handler",
                        },
                        "actions": [
                            {
                                "id": f"create_{singular}",
                                "description": f"Create {singular}.",
                                "handler_method": f"create_{singular}",
                                "input_schema": {"type": "object"},
                                "output_schema": {"type": "object"},
                                "emits": [f"domain.{module_id}.{singular}_created"],
                            },
                            {
                                "id": f"list_{module_id}",
                                "description": f"List {module_id}.",
                                "handler_method": f"list_{module_id}",
                                "input_schema": {"type": "object"},
                                "output_schema": {"type": "object"},
                            },
                        ],
                    },
                    sort_keys=False,
                ),
            },
            {
                "filename": f"modules/{module_id}/contracts/events.yaml",
                "content": yaml.safe_dump(
                    {
                        "schema_version": "mozaiks.events.v1",
                        "events": [
                            {
                                "type": f"domain.{module_id}.{singular}_created",
                                "version": 1,
                                "producer": module_id,
                                "description": f"{singular} created.",
                                "payload_schema": {"type": "object"},
                            }
                        ],
                    },
                    sort_keys=False,
                ),
            },
            {"filename": f"modules/{module_id}/backend/repo.py", "content": "BROKEN_MIRROR\n"},
        ],
        "python_files": [
            {
                "path": f"modules/{module_id}/backend/handler.py",
                "kind": "handler",
                "purpose": "Thin dispatch layer.",
                "contract_refs": ["module_yaml.actions[*].handler_method"],
                "content": textwrap.dedent(
                    f"""
                    from .service import {class_name}Service


                    class {class_name}Handler:
                        def __init__(self):
                            self.service = {class_name}Service()

                        async def create_{singular}(self, ctx, **payload):
                            return await self.service.create_{singular}(ctx, payload=payload)

                        async def list_{module_id}(self, ctx, **payload):
                            return await self.service.list_{module_id}(ctx, filters=payload)
                    """
                ).strip()
                + "\n",
            },
            {
                "path": f"modules/{module_id}/backend/service.py",
                "kind": "service",
                "purpose": "Business logic and event emission.",
                "contract_refs": ["module_yaml.actions[*]", "events_yaml.events[*]"],
                "content": textwrap.dedent(
                    f"""
                    from .policy import scoped_query
                    from .repo import {class_name}Repo
                    from .schemas import build_record


                    class {class_name}Service:
                        def __init__(self, repo=None):
                            self.repo = repo or {class_name}Repo()

                        async def create_{singular}(self, ctx, *, payload):
                            record = build_record(ctx, payload=payload)
                            stored = await self.repo.create(ctx, record=record)
                            await ctx.emit("domain.{module_id}.{singular}_created", {{"{id_field}": stored["{id_field}"]}})
                            return stored

                        async def list_{module_id}(self, ctx, *, filters=None):
                            query = scoped_query(filters or {{}})
                            records = await self.repo.list(ctx, query=query, limit=int((filters or {{}}).get("limit") or 50))
                            return {{"items": records, "count": len(records)}}
                    """
                ).strip()
                + "\n",
            },
            {
                "path": f"modules/{module_id}/backend/repo.py",
                "kind": "repo",
                "purpose": "Persistence access through ctx.persistence.",
                "contract_refs": ["data_contract.surfaces[*].collections[*]"],
                "content": textwrap.dedent(
                    f"""
                    class {class_name}Repo:
                        async def _collection(self, ctx):
                            persistence = getattr(ctx, "persistence", None)
                            if persistence is None:
                                raise RuntimeError("Persistence is not available for this app context.")
                            return persistence.collection("{module_id}", "{module_id}")

                        async def create(self, ctx, *, record):
                            collection = await self._collection(ctx)
                            await collection.insert_one(record)
                            return record

                        async def list(self, ctx, *, query=None, limit=50):
                            collection = await self._collection(ctx)
                            return await collection.find_many(query or {{}}, limit=limit)
                    """
                ).strip()
                + "\n",
            },
            {
                "path": f"modules/{module_id}/backend/policy.py",
                "kind": "policy",
                "purpose": "Scope filter helpers.",
                "contract_refs": ["module_yaml.permissions[*]"],
                "content": textwrap.dedent(
                    """
                    def scoped_query(filters):
                        query = {}
                        for key in ("project_id", "status", "owner_id"):
                            value = filters.get(key)
                            if value:
                                query[key] = value
                        return query
                    """
                ).strip()
                + "\n",
            },
            {
                "path": f"modules/{module_id}/backend/schemas.py",
                "kind": "schemas",
                "purpose": "Typed document shapes and pure helpers.",
                "contract_refs": ["data_contract"],
                "content": textwrap.dedent(
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
                    """
                ).strip()
                + "\n",
            },
        ],
    }


def _page_output() -> dict[str, Any]:
    return {
        "code_files": [
            {
                "filename": "app.json",
                "content": json.dumps(
                    {
                        "name": "Project Management",
                        "version": "1.0.0",
                        "description": "Persistent projects/tasks generated app.",
                    },
                    indent=2,
                )
                + "\n",
            },
            {
                "filename": "ui/pages/projects.yaml",
                "content": yaml.safe_dump(
                    {
                        "schema_version": "mozaiks.app_page.v1",
                        "name": "Projects",
                        "route": "/projects",
                        "title": "Projects",
                        "page_type": "record_list",
                        "layout": "full-width",
                        "sections": [
                            {
                                "id": "projects",
                                "primitive": "DataTable",
                                "config": {
                                    "columns": ["id", "name", "status"],
                                    "api_endpoint": "/api/modules/projects/list_projects",
                                },
                            }
                        ],
                    },
                    sort_keys=False,
                ),
            },
            {
                "filename": "ui/pages/tasks.yaml",
                "content": yaml.safe_dump(
                    {
                        "schema_version": "mozaiks.app_page.v1",
                        "name": "Tasks",
                        "route": "/tasks",
                        "title": "Tasks",
                        "page_type": "record_list",
                        "layout": "full-width",
                        "sections": [
                            {
                                "id": "tasks",
                                "primitive": "DataTable",
                                "config": {
                                    "columns": ["id", "title", "status"],
                                    "api_endpoint": "/api/modules/tasks/list_tasks",
                                },
                            }
                        ],
                    },
                    sort_keys=False,
                ),
            },
        ]
    }


def _assembled_file_map(plan: Mapping[str, Any] | None = None) -> dict[str, str]:
    resolved_plan = plan or _load_plan()
    merged = _merge_code_files(
        [
            _database_output(resolved_plan),
            _module_output("projects"),
            _module_output("tasks"),
            _page_output(),
        ]
    )
    return {entry["filename"]: entry["content"] for entry in merged}


def _write_file_map(root: Path, file_map: Mapping[str, str]) -> None:
    for rel_path, content in file_map.items():
        path = root / rel_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")


def _repo_collection_calls(repo_text: str) -> set[tuple[str, str]]:
    return set(re.findall(r'collection\("([^"]+)",\s*"([^"]+)"\)', repo_text))


def _all_generated_text(file_map: Mapping[str, str]) -> str:
    return "\n".join(file_map.values())


def test_live_fixture_replay_has_downstream_persistence_tasks() -> None:
    plan = _load_plan()
    tasks = {task["task_id"]: task for task in plan["build_tasks"]}
    owned_paths = {path for task in tasks.values() for path in task.get("owned_paths", [])}

    persistence_tasks = [
        task for task in tasks.values() if task.get("task_type") == "persistence_contract"
    ]
    assert len(persistence_tasks) == 1
    assert persistence_tasks[0]["initial_agent"] == "DatabaseAgent"
    assert plan["data_contract"]
    assert plan["pending_schema_migration"]["migration_id"] == "001_projects_tasks_indexes"
    assert any(task.get("surface_id") == "projects" for task in tasks.values())
    assert any(task.get("surface_id") == "tasks" for task in tasks.values())
    assert any(task.get("task_type") == "page_bundle" for task in tasks.values())

    expected_paths = {
        "data/contract.json",
        "data/migrations/001_projects_tasks_indexes.json",
        "modules/projects/backend/repo.py",
        "modules/projects/backend/policy.py",
        "modules/projects/backend/schemas.py",
        "modules/tasks/backend/repo.py",
        "modules/tasks/backend/policy.py",
        "modules/tasks/backend/schemas.py",
        "ui/pages/projects.yaml",
        "ui/pages/tasks.yaml",
    }
    assert expected_paths <= owned_paths
    assert all("backend/models.py" not in path for path in owned_paths)
    assert all("backend/models/" not in path for path in owned_paths)
    assert all("backend/database/schema.json" not in path for path in owned_paths)
    assert all("backend/database/seed.json" not in path for path in owned_paths)
    assert "ctx.db" not in json.dumps(plan)
    assert "get_mongo_client" not in json.dumps(plan)


def test_deterministic_downstream_outputs_assemble_canonical_tree() -> None:
    file_map = _assembled_file_map()

    expected_paths = {
        "app.json",
        "data/contract.json",
        "data/migrations/001_projects_tasks_indexes.json",
        "modules/projects/module.yaml",
        "modules/projects/contracts/events.yaml",
        "modules/projects/backend/handler.py",
        "modules/projects/backend/service.py",
        "modules/projects/backend/repo.py",
        "modules/projects/backend/policy.py",
        "modules/projects/backend/schemas.py",
        "modules/tasks/module.yaml",
        "modules/tasks/contracts/events.yaml",
        "modules/tasks/backend/handler.py",
        "modules/tasks/backend/service.py",
        "modules/tasks/backend/repo.py",
        "modules/tasks/backend/policy.py",
        "modules/tasks/backend/schemas.py",
        "ui/pages/projects.yaml",
        "ui/pages/tasks.yaml",
    }
    assert expected_paths <= set(file_map)
    assert file_map["data/contract.json"] != "BROKEN_MIRROR\n"
    assert file_map["data/migrations/001_projects_tasks_indexes.json"] != "BROKEN_MIRROR\n"
    assert file_map["modules/projects/backend/repo.py"] != "BROKEN_MIRROR\n"
    assert file_map["modules/tasks/backend/repo.py"] != "BROKEN_MIRROR\n"
    assert all("backend/models.py" not in path for path in file_map)
    assert all("backend/models/" not in path for path in file_map)
    assert all("backend/database/schema.json" not in path for path in file_map)
    assert all("backend/database/seed.json" not in path for path in file_map)


def test_backend_layers_use_ctx_persistence_boundary() -> None:
    file_map = _assembled_file_map()
    all_text = _all_generated_text(file_map)

    for forbidden in (
        "ctx.db",
        "context.db",
        "get_mongo_client",
        "pymongo",
        "motor",
        "backend/models.py",
        "backend/database/schema.json",
        "backend/database/seed.json",
        '"mozaiks_apps"',
        '"mozaiksai"',
    ):
        assert forbidden not in all_text

    for module_id in ("projects", "tasks"):
        repo = file_map[f"modules/{module_id}/backend/repo.py"]
        service = file_map[f"modules/{module_id}/backend/service.py"]
        handler = file_map[f"modules/{module_id}/backend/handler.py"]
        policy = file_map[f"modules/{module_id}/backend/policy.py"]
        schemas = file_map[f"modules/{module_id}/backend/schemas.py"]

        assert f'persistence.collection("{module_id}", "{module_id}")' in repo
        assert "persistence" not in service
        assert "persistence" not in handler
        assert "scoped_query" in policy
        assert "TypedDict" in schemas


def test_assembled_database_artifacts_align_with_repo_collections() -> None:
    file_map = _assembled_file_map()
    intent = json.loads(file_map["data/contract.json"])
    migration = json.loads(file_map["data/migrations/001_projects_tasks_indexes.json"])
    intent_keys = {
        (collection["module_id"], collection.get("entity_name") or collection["name"])
        for surface in intent["surfaces"]
        for collection in surface["collections"]
    }
    repo_keys = set()
    for module_id in ("projects", "tasks"):
        repo_keys |= _repo_collection_calls(file_map[f"modules/{module_id}/backend/repo.py"])

    assert intent_keys == {("projects", "projects"), ("tasks", "tasks")}
    assert repo_keys == intent_keys
    assert all(
        operation["type"] in {"ensure_collection", "ensure_index"}
        for operation in migration["operations"]
    )


@pytest.mark.asyncio
async def test_downstream_artifact_loads_indexes_migrations_and_executes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    file_map = _assembled_file_map()
    _write_file_map(tmp_path, file_map)

    load_result = await AppLoader.load(str(tmp_path))
    assert load_result.data_contract is not None
    assert ("projects", "projects") in load_result.data_entities_by_key
    assert ("tasks", "tasks") in load_result.data_entities_by_key

    FakePersistenceContext.reset()
    persistence = FakePersistenceContext(app_id="app_downstream")
    applied_indexes = await apply_database_indexes(
        load_result.data_contract,
        app_id="app_downstream",
        persistence=persistence,
    )
    migrations = load_data_migrations(tmp_path)
    applied_migrations = await apply_data_migrations(
        app_id="app_downstream",
        migrations=migrations,
        persistence=persistence,
        history_client=FakeHistoryClient(),
    )
    assert applied_indexes.created == 2
    assert applied_indexes.verified == 2
    assert applied_migrations == 1
    assert "project_owner_created_at" in persistence.collection("projects", "projects").indexes
    assert "task_project_status" in persistence.collection("tasks", "tasks").indexes

    # Test-only swap for runtime execution; this avoids real Mongo in unit tests.
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
            params={"project_id": "project_1", "name": "Launch Plan"}, authority=trusted_framework_authority(),
        )
    )
    listed_projects = await executor.execute(
        ModuleRequest(module="projects", action="list_projects", app_id="app_a", params={}, authority=trusted_framework_authority())
    )
    created_task = await executor.execute(
        ModuleRequest(
            module="tasks",
            action="create_task",
            app_id="app_a",
            user_id="user_1",
            params={"task_id": "task_1", "title": "Draft scope", "project_id": "project_1"}, authority=trusted_framework_authority(),
        )
    )
    listed_tasks = await executor.execute(
        ModuleRequest(module="tasks", action="list_tasks", app_id="app_a", params={"project_id": "project_1"}, authority=trusted_framework_authority())
    )
    app_b_projects = await executor.execute(
        ModuleRequest(module="projects", action="list_projects", app_id="app_b", params={}, authority=trusted_framework_authority())
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

