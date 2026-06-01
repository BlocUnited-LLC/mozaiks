from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import yaml

from factory_app.workflows.AppGenerator.tools.assembly_phase import _merge_code_files

ROOT = Path(__file__).resolve().parents[1]
APPGEN = ROOT / "factory_app" / "workflows" / "AppGenerator"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _agent_block(agent_name: str) -> str:
    text = _read(APPGEN / "agents.yaml")
    marker = f"- name: {agent_name}"
    start = text.index(marker)
    next_start = text.find("\n- name: ", start + len(marker))
    if next_start == -1:
        return text[start:]
    return text[start:next_start]


def _app_build_plan_fixture() -> dict[str, Any]:
    return {
        "app_id": "project_management",
        "app_name": "Project Management",
        "initial_request": (
            "Build a project management app where users can create projects, "
            "create tasks, assign tasks to projects, mark tasks complete, and "
            "view project/task lists."
        ),
        "build_tasks": [
            {
                "task_id": "data_contract",
                "task_type": "persistence_contract",
                "owned_paths": [
                    "config/data.json",
                    "config/data_migrations/001_projects_tasks_indexes.json",
                    "modules/projects/backend/repo.py",
                    "modules/projects/backend/policy.py",
                    "modules/projects/backend/schemas.py",
                    "modules/tasks/backend/repo.py",
                    "modules/tasks/backend/policy.py",
                    "modules/tasks/backend/schemas.py",
                ],
            },
            {
                "task_id": "projects_module",
                "task_type": "module_contract",
                "capability_pack_id": "projects",
                "owned_paths": [
                    "modules/projects/module.yaml",
                    "modules/projects/contracts/events.yaml",
                    "modules/projects/backend/handler.py",
                    "modules/projects/backend/service.py",
                    "modules/projects/backend/repo.py",
                    "modules/projects/backend/policy.py",
                    "modules/projects/backend/schemas.py",
                ],
            },
            {
                "task_id": "tasks_module",
                "task_type": "module_contract",
                "capability_pack_id": "tasks",
                "owned_paths": [
                    "modules/tasks/module.yaml",
                    "modules/tasks/contracts/events.yaml",
                    "modules/tasks/backend/handler.py",
                    "modules/tasks/backend/service.py",
                    "modules/tasks/backend/repo.py",
                    "modules/tasks/backend/policy.py",
                    "modules/tasks/backend/schemas.py",
                ],
            },
        ],
        "data_contract": _data_contract(),
        "pending_schema_migration": _schema_migration(),
    }


def _data_contract() -> dict[str, Any]:
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


def _schema_migration() -> dict[str, Any]:
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


def _module_contract_output(module_id: str) -> dict[str, Any]:
    class_name = module_id.title().replace("_", "")
    singular = module_id[:-1] if module_id.endswith("s") else module_id
    return {
        "yaml_files": [
            {
                "path": f"modules/{module_id}/module.yaml",
                "kind": "module_yaml",
                "purpose": f"{module_id} module contract.",
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
                            },
                            {
                                "id": f"list_{module_id}",
                                "description": f"List {module_id}.",
                                "handler_method": f"list_{module_id}",
                            },
                        ],
                    },
                    sort_keys=False,
                ),
            }
        ],
        "python_stubs": [
            {"path": f"backend/{name}.py", "kind": name, "purpose": f"{name} layer.", "contract_refs": []}
            for name in ("handler", "service", "repo", "policy", "schemas")
        ],
    }


def _database_output() -> dict[str, Any]:
    return {
        "database_files": [
            {
                "path": "config/data.json",
                "kind": "data_contract_json",
                "purpose": "Canonical generated data contract.",
                "entity_refs": ["projects", "tasks"],
                "content": json.dumps(_data_contract(), indent=2) + "\n",
            },
            {
                "path": "config/data_migrations/001_projects_tasks_indexes.json",
                "kind": "database_migration_json",
                "purpose": "Additive project/task index migration.",
                "entity_refs": ["projects", "tasks"],
                "content": json.dumps(_schema_migration(), indent=2) + "\n",
            },
        ],
        "pending_schema_migration": _schema_migration(),
        "code_files": [
            {"filename": "config/data.json", "content": "BROKEN\n"},
            {
                "filename": "config/data_migrations/001_projects_tasks_indexes.json",
                "content": "BROKEN\n",
            },
        ],
    }


def _backend_output(module_id: str) -> dict[str, Any]:
    class_name = module_id.title().replace("_", "")
    singular = module_id[:-1] if module_id.endswith("s") else module_id
    id_field = f"{singular}_id"
    title_field = "name" if module_id == "projects" else "title"
    return {
        "python_files": [
            {
                "path": f"modules/{module_id}/backend/handler.py",
                "kind": "handler",
                "purpose": "Thin dispatch layer.",
                "contract_refs": ["module_yaml.actions[*].handler_method"],
                "content": (
                    f"from .service import {class_name}Service\n\n\n"
                    f"class {class_name}Handler:\n"
                    "    def __init__(self):\n"
                    f"        self.service = {class_name}Service()\n\n"
                    f"    async def create_{singular}(self, ctx, **payload):\n"
                    f"        return await self.service.create_{singular}(ctx, payload=payload)\n\n"
                    f"    async def list_{module_id}(self, ctx, **payload):\n"
                    f"        return await self.service.list_{module_id}(ctx, filters=payload)\n"
                ),
            },
            {
                "path": f"modules/{module_id}/backend/service.py",
                "kind": "service",
                "purpose": "Business logic and event emission.",
                "contract_refs": ["module_yaml.actions[*]", "events_yaml.events[*]"],
                "content": (
                    f"from .repo import {class_name}Repo\n"
                    "from .policy import scoped_query\n"
                    "from .schemas import build_record\n\n\n"
                    f"class {class_name}Service:\n"
                    "    def __init__(self, repo=None):\n"
                    f"        self.repo = repo or {class_name}Repo()\n\n"
                    f"    async def create_{singular}(self, ctx, *, payload):\n"
                    "        record = build_record(ctx, payload=payload)\n"
                    "        stored = await self.repo.create(ctx, record=record)\n"
                    f"        await ctx.emit(\"domain.{module_id}.{singular}_created\", "
                    f"{{\"{id_field}\": stored[\"{id_field}\"]}})\n"
                    "        return stored\n\n"
                    f"    async def list_{module_id}(self, ctx, *, filters=None):\n"
                    "        query = scoped_query(filters or {})\n"
                    "        items = await self.repo.list(ctx, query=query, limit=50)\n"
                    "        return {\"items\": items, \"count\": len(items)}\n"
                ),
            },
            {
                "path": f"modules/{module_id}/backend/repo.py",
                "kind": "repo",
                "purpose": "Persistence access through ctx.persistence.",
                "contract_refs": ["data_contract.surfaces[*].collections[*]"],
                "content": (
                    f"class {class_name}Repo:\n"
                    "    async def _collection(self, ctx):\n"
                    "        persistence = getattr(ctx, \"persistence\", None)\n"
                    "        if persistence is None:\n"
                    "            raise RuntimeError(\"Persistence is not available for this app context.\")\n"
                    f"        return persistence.collection(\"{module_id}\", \"{module_id}\")\n\n"
                    "    async def create(self, ctx, *, record):\n"
                    "        collection = await self._collection(ctx)\n"
                    "        await collection.insert_one(record)\n"
                    "        return record\n\n"
                    "    async def list(self, ctx, *, query=None, limit=50):\n"
                    "        collection = await self._collection(ctx)\n"
                    "        return await collection.find_many(query or {}, limit=limit)\n"
                ),
            },
            {
                "path": f"modules/{module_id}/backend/policy.py",
                "kind": "policy",
                "purpose": "Scope filter helpers.",
                "contract_refs": ["module_yaml.permissions[*]"],
                "content": (
                    "def scoped_query(filters):\n"
                    "    query = {}\n"
                    "    for key in (\"project_id\", \"status\", \"owner_id\"):\n"
                    "        if filters.get(key):\n"
                    "            query[key] = filters[key]\n"
                    "    return query\n"
                ),
            },
            {
                "path": f"modules/{module_id}/backend/schemas.py",
                "kind": "schemas",
                "purpose": "Typed document shapes and pure helpers.",
                "contract_refs": ["data_contract"],
                "content": (
                    "from typing import TypedDict\n\n\n"
                    "class Record(TypedDict):\n"
                    f"    {id_field}: str\n"
                    "    owner_id: str | None\n"
                    f"    {title_field}: str\n"
                    "    status: str\n\n\n"
                    "def build_record(ctx, *, payload):\n"
                    f"    return {{\"{id_field}\": payload[\"{id_field}\"], "
                    f"\"{title_field}\": payload[\"{title_field}\"], "
                    "\"owner_id\": getattr(ctx, \"user_id\", None), \"status\": \"open\"}}\n"
                ),
            },
        ],
        "code_files": [
            {"filename": f"modules/{module_id}/backend/repo.py", "content": "BROKEN\n"}
        ],
    }


def _assembled_file_map() -> dict[str, str]:
    merged = _merge_code_files([
        _database_output(),
        _backend_output("projects"),
        _backend_output("tasks"),
    ])
    return {entry["filename"]: entry["content"] for entry in merged}


def test_app_build_plan_fixture_uses_canonical_persistence_paths() -> None:
    plan = _app_build_plan_fixture()
    owned_paths = {
        path
        for task in plan["build_tasks"]
        for path in task["owned_paths"]
    }

    assert plan["data_contract"]["surfaces"]
    assert plan["pending_schema_migration"]["migration_id"] == "001_projects_tasks_indexes"
    assert "config/data.json" in owned_paths
    assert "config/data_migrations/001_projects_tasks_indexes.json" in owned_paths
    for module_id in ("projects", "tasks"):
        assert f"modules/{module_id}/backend/repo.py" in owned_paths
        assert f"modules/{module_id}/backend/schemas.py" in owned_paths
        assert f"modules/{module_id}/backend/policy.py" in owned_paths
    assert all("backend/models.py" not in path for path in owned_paths)
    assert all("backend/models/" not in path for path in owned_paths)
    assert all("backend/database/schema.json" not in path for path in owned_paths)
    assert all("backend/database/seed.json" not in path for path in owned_paths)


def test_config_middleware_style_module_contracts_declare_repo_and_schemas() -> None:
    projects = _module_contract_output("projects")
    tasks = _module_contract_output("tasks")

    for output, module_id in ((projects, "projects"), (tasks, "tasks")):
        module_yaml = yaml.safe_load(output["yaml_files"][0]["content"])
        stub_paths = {stub["path"] for stub in output["python_stubs"]}

        assert module_yaml["module"]["id"] == module_id
        assert {action["id"] for action in module_yaml["actions"]} == {
            f"create_{module_id[:-1]}",
            f"list_{module_id}",
        }
        assert "backend/repo.py" in stub_paths
        assert "backend/schemas.py" in stub_paths
        assert "backend/models.py" not in stub_paths

    intent_keys = {
        (collection["module_id"], collection.get("entity_name") or collection["name"])
        for surface in _data_contract()["surfaces"]
        for collection in surface["collections"]
    }
    assert intent_keys == {("projects", "projects"), ("tasks", "tasks")}


def test_service_agent_style_backend_uses_ctx_persistence_boundary() -> None:
    for module_id in ("projects", "tasks"):
        files = {file["path"]: file["content"] for file in _backend_output(module_id)["python_files"]}
        repo = files[f"modules/{module_id}/backend/repo.py"]
        service = files[f"modules/{module_id}/backend/service.py"]
        handler = files[f"modules/{module_id}/backend/handler.py"]
        policy = files[f"modules/{module_id}/backend/policy.py"]
        schemas = files[f"modules/{module_id}/backend/schemas.py"]

        assert f'persistence.collection("{module_id}", "{module_id}")' in repo
        assert "ctx.db" not in repo
        assert "context.db" not in repo
        assert "get_mongo_client" not in repo
        assert "pymongo" not in repo
        assert "motor" not in repo
        assert "persistence" not in handler
        assert "persistence" not in service
        assert "scoped_query" in policy
        assert "TypedDict" in schemas
        assert "get_mongo_client" not in schemas


def test_assembly_materializes_canonical_persistent_artifact_tree() -> None:
    file_map = _assembled_file_map()

    assert "config/data.json" in file_map
    assert "config/data_migrations/001_projects_tasks_indexes.json" in file_map
    for module_id in ("projects", "tasks"):
        for filename in ("handler.py", "service.py", "repo.py", "policy.py", "schemas.py"):
            assert f"modules/{module_id}/backend/{filename}" in file_map

    assert all("backend/models.py" not in path for path in file_map)
    assert all("backend/models/" not in path for path in file_map)
    assert all("backend/database/schema.json" not in path for path in file_map)
    assert all("backend/database/seed.json" not in path for path in file_map)

    all_generated = "\n".join(file_map.values())
    assert "ctx.db" not in all_generated
    assert "context.db" not in all_generated
    assert "get_mongo_client" not in all_generated
    assert "pymongo" not in all_generated
    assert "motor" not in all_generated


def test_assembled_data_contract_migration_and_repos_align() -> None:
    file_map = _assembled_file_map()
    intent = json.loads(file_map["config/data.json"])
    migration = json.loads(file_map["config/data_migrations/001_projects_tasks_indexes.json"])
    intent_keys = {
        (collection["module_id"], collection.get("entity_name") or collection["name"])
        for surface in intent["surfaces"]
        for collection in surface["collections"]
    }
    repo_keys = set()
    for module_id in ("projects", "tasks"):
        repo_keys.update(
            re.findall(
                r'collection\("([^"]+)",\s*"([^"]+)"\)',
                file_map[f"modules/{module_id}/backend/repo.py"],
            )
        )

    assert repo_keys == intent_keys
    assert all(
        operation["type"] in {"ensure_collection", "ensure_index"}
        for operation in migration["operations"]
    )


def test_appgenerator_guidance_still_targets_persistent_module_contract() -> None:
    service_agent = _agent_block("ServiceAgent")
    file_contracts = yaml.safe_load(_read(APPGEN / "tools" / "file_contracts.yaml"))
    structured_outputs = yaml.safe_load(_read(APPGEN / "structured_outputs.yaml"))
    generate_and_download = _read(APPGEN / "tools" / "generate_and_download.py")
    module_archetypes = _read(APPGEN / "tools" / "module_archetypes.yaml")
    domain_catalog = _read(APPGEN / "tools" / "domain_catalogs.yaml")

    persistence_contract = file_contracts["task_contracts"]["persistence_contract"]
    hard_constraints = "\n".join(persistence_contract["hard_constraints"])
    database_output_fields = structured_outputs["models"]["DatabaseOutput"]["fields"]

    assert "context.persistence.collection(module_id, entity_name)" in service_agent
    assert "config/data.json" in persistence_contract["required_outputs"]
    assert "config/data_migrations/{migration_id}.json" in persistence_contract["optional_outputs"]
    assert "must not use ctx.db" in hard_constraints
    assert "must not import or call get_mongo_client()" in hard_constraints
    assert "pending_schema_migration" in database_output_fields
    assert "config/data_migrations" in generate_and_download
    assert "backend/models.py" not in module_archetypes
    assert "backend/models/" not in module_archetypes
    assert "backend/models.py" not in domain_catalog
    assert "backend/models/" not in domain_catalog
