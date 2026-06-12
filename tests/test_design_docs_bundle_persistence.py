from __future__ import annotations

import asyncio

from tests.import_utils import import_module_directly

design_docs_module = import_module_directly(
    "factory_app.workflows.DesignDocs.tools.save_design_doc"
)


class _Context:
    def __init__(self, initial=None) -> None:
        self.data = dict(initial or {})

    def set(self, key, value) -> None:
        self.data[key] = value

    def get(self, key, default=None):
        return self.data.get(key, default)


class _FakeCursor:
    def __init__(self, docs):
        self._docs = list(docs)

    async def to_list(self, length=None):
        return list(self._docs)


class _FakeCollection:
    def __init__(self) -> None:
        self.indexes = []
        self.updates = []

    def list_indexes(self):
        return _FakeCursor([])

    async def create_index(self, keys, **kwargs):
        self.indexes.append((list(keys), dict(kwargs)))

    async def update_one(self, query, update, upsert=False):
        self.updates.append((dict(query), dict(update), bool(upsert)))


class _FakeDatabase:
    def __init__(self) -> None:
        self._collections = {}

    def __getitem__(self, collection_name):
        if collection_name not in self._collections:
            self._collections[collection_name] = _FakeCollection()
        return self._collections[collection_name]


class _FakeClient:
    def __init__(self) -> None:
        self._databases = {}

    def __getitem__(self, database_name):
        if database_name not in self._databases:
            self._databases[database_name] = _FakeDatabase()
        return self._databases[database_name]


class _FakePersistence:
    def __init__(self, client) -> None:
        self.client = client

    async def _ensure_client(self) -> None:
        return None


class _FakePersistenceManager:
    client = _FakeClient()

    def __init__(self) -> None:
        self.persistence = _FakePersistence(self.client)


def _bundle():
    return {
        "agent_message": "Design docs prepared.",
        "frontend_markdown": "# Frontend Design\n\nFrontend doc.",
        "backend_markdown": "# Backend Design\n\nBackend doc.",
        "database_markdown": "# Database Design\n\nDatabase doc.",
        "experience_spec": {
            "navigation_model": "top-level routes with shell navigation",
            "brand_direction": "clean and professional",
            "pages": [
                {
                    "name": "Users",
                    "route": "/users",
                    "layout": "full-width",
                    "intent": "Manage users",
                    "sections": [
                        {
                            "id": "user-list",
                            "primitive": "DataTable",
                            "intent": "List all users",
                            "config_hint": "{\"columns\": [\"id\", \"name\", \"email\"], \"api_endpoint\": \"/api/users\"}",
                        }
                    ],
                }
            ],
        },
        "surface_map": {
            "surfaces": [
                {
                    "surface_id": "users",
                    "label": "Users",
                    "surface_kind": "module",
                    "owner": "app",
                    "source_capability_packs": ["crud_pack"],
                    "primary_entities": ["User"],
                    "owned_pages": ["Users"],
                    "owned_mutations": ["create_user"],
                    "events_emitted": ["domain.users.user_created"],
                    "workflow_triggers": [],
                    "integrations": [],
                    "notes": None,
                }
            ]
        },
        "data_contract": {
            "version": "1",
            "app_id": None,
            "artifact_version_id": None,
            "surfaces": [
                {
                    "surface_id": "users",
                    "surface_kind": "module",
                    "collections": [
                        {
                            "name": "users",
                            "scope": "app",
                            "ownership": {"surface_id": "users", "surface_kind": "module"},
                            "fields": [
                                {"name": "app_id", "type": "string", "required": True},
                                {"name": "user_id", "type": "string", "required": True},
                            ],
                            "indexes": [
                                {"keys": [["app_id", 1], ["user_id", 1]], "unique": True}
                            ],
                            "search_by": "user_id",
                            "lifecycle": {
                                "write_mode": "module_action",
                                "migration_policy": "additive_only",
                            },
                        }
                    ],
                }
            ],
            "shared_collections": [],
            "policies": {
                "default_scope_field": "app_id",
                "allow_destructive_migrations": False,
            },
        },
    }


def test_save_design_docs_bundle_persists_surface_map_and_data_contract(monkeypatch) -> None:
    monkeypatch.setattr(design_docs_module, "AG2PersistenceManager", _FakePersistenceManager)
    summary_artifact = {}

    async def _fake_persist_summary_artifact(**kwargs):
        summary_artifact.update(kwargs)
        return type("ArtifactVersion", (), {"id": "av_design_docs_1"})()

    monkeypatch.setattr(design_docs_module, "persist_summary_artifact", _fake_persist_summary_artifact)

    context = _Context(
        {
            "app_id": "app_123",
            "chat_id": "chat_123",
            "user_id": "user_123",
            "build_id": "build_123",
            "artifact_version_id": "artifact_123",
            "structured_output": _bundle(),
        }
    )

    result = asyncio.run(design_docs_module.save_design_docs_bundle(context_variables=context))

    design_docs_collection = _FakePersistenceManager.client["mozaiksai"]["DesignDocuments"]
    data_contracts_collection = _FakePersistenceManager.client["mozaiksai"]["DataContracts"]

    assert result["ok"] is True
    assert context.data["design_surface_map"]["surfaces"][0]["surface_id"] == "users"
    assert context.data["data_contract"]["app_id"] == "app_123"
    # Typed ExperienceSpec must be set on context as a structured object
    assert context.data["experience_spec"]["navigation_model"] == "top-level routes with shell navigation"
    assert context.data["experience_spec"]["pages"][0]["name"] == "Users"
    # Human-readable YAML string remains available for prompt consumers.
    assert "navigation_model" in context.data["experience_spec_document"]
    assert len(design_docs_collection.updates) >= 4
    assert any(
        update[0].get("kind") == "backend" and "surface_map" in update[1]["$set"]
        for update in design_docs_collection.updates
    )
    # ui_schema doc must carry both surface_map and experience_spec as extra_fields
    assert any(
        update[0].get("kind") == "ui_schema" and "experience_spec" in update[1]["$set"]
        for update in design_docs_collection.updates
    )
    assert data_contracts_collection.updates[0][1]["$set"]["data_contract"]["artifact_version_id"] == "artifact_123"
    assert summary_artifact["artifact_kind"] == "design_docs"
    assert summary_artifact["input_artifact_kinds"] == ("concept", "build_plan")
    assert summary_artifact["summary_payload"]["surface_map"]["surfaces"][0]["surface_id"] == "users"
    assert summary_artifact["summary_payload"]["experience_spec"]["pages"][0]["name"] == "Users"
    assert result["page_count"] == 1

