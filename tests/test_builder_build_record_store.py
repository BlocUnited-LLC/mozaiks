from __future__ import annotations

import asyncio

from mozaiksai.core.data.persistence.artifact_store import BuilderArtifactStore


class _FakeCursor:
    def __init__(self, docs):
        self._docs = list(docs)
        self._limit = None

    def sort(self, key, direction):
        reverse = int(direction) < 0
        self._docs.sort(key=lambda doc: doc.get(key, 0), reverse=reverse)
        return self

    def limit(self, value):
        self._limit = int(value)
        return self

    async def to_list(self, length=None):
        if self._limit is not None:
            return list(self._docs[: self._limit])
        if length is None:
            return list(self._docs)
        return list(self._docs[:length])


class _InsertResult:
    def __init__(self, inserted_id):
        self.inserted_id = inserted_id


class _FakeCollection:
    def __init__(self) -> None:
        self.docs = []
        self.indexes = []
        self._next_id = 1

    def list_indexes(self):
        return _FakeCursor([kwargs | {"name": kwargs.get("name")} for _keys, kwargs in self.indexes])

    async def create_index(self, keys, **kwargs):
        self.indexes.append((list(keys), dict(kwargs)))

    async def update_one(self, query, update, upsert=False):
        doc = None
        for existing in self.docs:
            if all(existing.get(key) == value for key, value in query.items()):
                doc = existing
                break
        is_new = False
        if doc is None and upsert:
            doc = dict(query)
            doc["_id"] = self._next_id
            self._next_id += 1
            self.docs.append(doc)
            is_new = True
        if doc is None:
            return
        for key, value in update.get("$setOnInsert", {}).items():
            if is_new:
                doc[key] = value
        for key, value in update.get("$set", {}).items():
            doc[key] = value
        for key, payload in update.get("$push", {}).items():
            existing_values = list(doc.get(key) or [])
            if isinstance(payload, dict) and "$each" in payload:
                existing_values.extend(list(payload.get("$each") or []))
                slice_value = payload.get("$slice")
                if isinstance(slice_value, int) and slice_value < 0:
                    existing_values = existing_values[slice_value:]
            else:
                existing_values.append(payload)
            doc[key] = existing_values

    async def find_one(self, query, projection=None):
        for existing in self.docs:
            if all(existing.get(key) == value for key, value in query.items()):
                return dict(existing)
        return None

    async def insert_one(self, doc):
        stored = dict(doc)
        stored["_id"] = self._next_id
        self._next_id += 1
        self.docs.append(stored)
        return _InsertResult(stored["_id"])

    def find(self, query):
        filtered = [
            dict(existing)
            for existing in self.docs
            if all(existing.get(key) == value for key, value in query.items())
        ]
        return _FakeCursor(filtered)


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
    def __init__(self) -> None:
        self.client = _FakeClient()
        self.persistence = _FakePersistence(self.client)


def test_builder_build_record_store_round_trips_concept_and_build_plan() -> None:
    pm = _FakePersistenceManager()
    store = BuilderArtifactStore(pm=pm)

    asyncio.run(
        store.save_concept(
            app_id="app_1",
            concept_record={"app_id": "app_1", "ConceptOverview": "Test concept"},
            created_at="2026-01-01T00:00:00+00:00",
        )
    )
    asyncio.run(
        store.save_build_plan(
            app_id="app_1",
            build_plan={"app_id": "app_1", "build_plan_id": "plan_1", "tasks": [{"task_id": "a"}]},
        )
    )

    concept = asyncio.run(store.get_concept(app_id="app_1"))
    build_plan = asyncio.run(store.get_build_plan(app_id="app_1"))

    assert concept["ConceptOverview"] == "Test concept"
    assert build_plan["build_plan_id"] == "plan_1"


def test_builder_build_record_store_records_workflow_exports_and_returns_latest() -> None:
    pm = _FakePersistenceManager()
    store = BuilderArtifactStore(pm=pm)

    asyncio.run(
        store.record_workflow_export(
            app_id="app_1",
            user_id="user_1",
            workflow_type="agent-generator",
            repo_url="https://example.com/one",
            job_id="job_1",
        )
    )
    asyncio.run(
        store.record_workflow_export(
            app_id="app_1",
            user_id="user_1",
            workflow_type="agent-generator",
            repo_url="https://example.com/two",
            job_id="job_2",
        )
    )

    latest = asyncio.run(store.get_latest_workflow_export(app_id="app_1", workflow_type="agent-generator"))
    collection = pm.client["mozaiksai"]["WorkflowExports"]

    assert latest["job_id"] == "job_2"
    assert latest["repo_url"] == "https://example.com/two"
    assert any(kwargs.get("name") == "wfe_app_type_created" for _keys, kwargs in collection.indexes)


def test_builder_build_record_store_persists_database_migration_history() -> None:
    pm = _FakePersistenceManager()
    store = BuilderArtifactStore(pm=pm)

    record = asyncio.run(
        store.save_database_migration(
            app_id="app_1",
            build_id="build_1",
            build_record_id="artifact_1",
            change_class="feature",
            migration={
                "migration_id": "m_20260503_120000",
                "changes": {"new_collections": [{"name": "users"}]},
            },
            status="staged",
            source_workflow="AppGenerator",
            source_chat_id="chat_1",
            generated_app_dir="C:/tmp/generated/apps/app_1/build_1/app",
        )
    )

    collection = pm.client["mozaiksai"]["DatabaseMigrations"]

    assert record["migration_id"] == "m_20260503_120000"
    assert record["bundle_relative_path"] == "data/migrations/m_20260503_120000.json"
    assert collection.docs[0]["build_record_id"] == "artifact_1"
    assert any(kwargs.get("name") == "dbm_app_migration" for _keys, kwargs in collection.indexes)


def test_builder_build_record_store_reads_design_docs_data_contracts_and_theme_capture() -> None:
    pm = _FakePersistenceManager()
    store = BuilderArtifactStore(pm=pm)

    asyncio.run(
        store.upsert_design_doc(
            app_id="app_1",
            user_id="user_1",
            kind="backend",
            stage="draft",
            content="Backend design",
            source_workflow="DesignDocs",
            source_chat_id="chat_1",
            extra_fields={"surface_map": {"surfaces": [{"surface_id": "workflow"}]}},
        )
    )
    asyncio.run(
        store.save_data_contract(
            app_id="app_1",
            build_id="build_1",
            build_record_id="artifact_1",
            change_class="feature",
            data_contract={"surfaces": [{"surface_id": "workflow"}], "shared_collections": [], "policies": {}},
            user_id="user_1",
            source_workflow="DesignDocs",
            source_chat_id="chat_1",
        )
    )
    asyncio.run(
        store.save_theme_capture(
            app_id="app_1",
            chat_id="chat_1",
            app_url="https://example.com",
            identity={"brand_name": "Investor Hub"},
            theme_config={"palette": "warm"},
        )
    )

    design_doc = asyncio.run(store.get_design_doc(app_id="app_1", kind="backend"))
    design_docs = asyncio.run(store.list_design_docs(app_id="app_1"))
    data_contract = asyncio.run(store.get_latest_data_contract(app_id="app_1"))
    theme_capture = asyncio.run(store.get_theme_capture(app_id="app_1"))

    assert design_doc["content"] == "Backend design"
    assert design_doc["surface_map"]["surfaces"][0]["surface_id"] == "workflow"
    assert len(design_docs) == 1
    assert data_contract["build_record_id"] == "artifact_1"
    assert theme_capture["identity"]["brand_name"] == "Investor Hub"
