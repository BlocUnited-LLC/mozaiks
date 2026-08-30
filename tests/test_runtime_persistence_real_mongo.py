from __future__ import annotations

import inspect
import os
from uuid import uuid4

import pytest

from mozaiksai.core.core_config import close_mongo_client, get_mongo_client
from mozaiksai.core.runtime.composition.module_context import ModuleContext
from mozaiksai.core.runtime.composition.module_executor import ModuleExecutor, ModuleRequest
from mozaiksai.core.runtime.persistence import (
    APP_DATA_MIGRATIONS_COLLECTION,
    apply_data_migrations,
    apply_database_indexes,
    collection_name_for,
    migration_hash,
)
from mozaiksai.core.runtime.persistence.indexes import DatabaseIndexApplyError
from mozaiksai.core.runtime.persistence.mongo import DEFAULT_APP_DATABASE_NAME
from tests.module_authority_test_helpers import trusted_framework_authority

RUN_ENV = "MOZAIKS_RUN_REAL_MONGO_TESTS"
LEGACY_RUN_ENV = "MOZAIKS_RUN_MONGO_INTEGRATION"
TEST_DB_ENV = "MOZAIKS_TEST_APP_DATABASE_NAME"
APP_DB_ENV = "MOZAIKS_APP_DATABASE_NAME"


pytestmark = pytest.mark.skipif(
    os.getenv(RUN_ENV) != "1" and os.getenv(LEGACY_RUN_ENV) != "1",
    reason=f"set {RUN_ENV}=1 to run the optional real-Mongo generated-app persistence smoke test",
)


class RealPersistenceHandler:
    async def create_project(self, ctx, *, project_id: str, name: str):
        collection = ctx.persistence.collection("projects", "projects")
        await collection.insert_one({"project_id": project_id, "name": name})
        return {"project_id": project_id}

    async def list_projects(self, ctx):
        collection = ctx.persistence.collection("projects", "projects")
        return {"items": await collection.find_many({}, limit=20, sort=[("project_id", 1)])}


def _has_mongo_connection_env() -> bool:
    return any(os.getenv(name) for name in ("MONGO_URI", "MONGODB_URI", "MONGO_URL"))


def _test_database_name() -> tuple[str, bool]:
    configured = (os.getenv(TEST_DB_ENV) or "").strip()
    if configured:
        if configured == DEFAULT_APP_DATABASE_NAME or "test" not in configured.lower():
            pytest.fail(
                f"Unsafe {TEST_DB_ENV}. Use a dedicated test database name containing 'test'."
            )
        return configured, False
    return f"mozaiks_persistence_test_{uuid4().hex[:10]}", True


def _data_contract(app_id: str) -> dict:
    return {
        "version": "1",
        "app_id": app_id,
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
                                "name": "project_owner_idx",
                                "keys": [{"field": "owner_id", "order": 1}],
                            }
                        ],
                    }
                ],
            }
        ],
    }


def _migration() -> dict:
    return {
        "migration_id": "001_project_status_index",
        "version": "1",
        "description": "Ensure project status index.",
        "operations": [
            {
                "type": "ensure_index",
                "module_id": "projects",
                "entity_name": "projects",
                "index": {
                    "name": "project_status_idx",
                    "keys": [["status", 1]],
                },
            }
        ],
    }


def _literal_index_contract(app_id: str, collection_name: str, indexes: list[dict]) -> dict:
    return {
        "version": "1",
        "app_id": app_id,
        "surfaces": [
            {
                "surface_id": "checkout_records",
                "surface_kind": "module",
                "collections": [
                    {
                        "name": "refund_records",
                        "mongo_collection": collection_name,
                        "ownership": {"surface_id": "checkout_records", "surface_kind": "module"},
                        "indexes": indexes,
                    }
                ],
            }
        ],
    }


@pytest.mark.asyncio
async def test_generated_app_persistence_real_mongo_round_trip(monkeypatch: pytest.MonkeyPatch) -> None:
    """Opt-in smoke for the actual Mongo-backed generated-app persistence path.

    Normal CI uses fake/in-memory tests. This test requires an explicit env gate
    and a real Mongo connection, then exercises ModuleExecutor,
    MongoPersistenceContext, data contract indexes, additive migrations, and
    migration history with a dedicated test database.
    """

    if not _has_mongo_connection_env():
        pytest.skip("MONGO_URI, MONGODB_URI, or MONGO_URL is required for real-Mongo persistence smoke")

    database_name, generated_database = _test_database_name()
    monkeypatch.setenv(APP_DB_ENV, database_name)

    app_id = f"real_mongo_smoke_{uuid4().hex}"
    other_app_id = f"{app_id}_other"
    project_id = f"project_{uuid4().hex}"
    collection_name = collection_name_for(app_id=app_id, module_id="projects", entity_name="projects")
    other_collection_name = collection_name_for(app_id=other_app_id, module_id="projects", entity_name="projects")
    client = get_mongo_client()
    raw_collection = client[database_name][collection_name]
    other_collection = client[database_name][other_collection_name]
    history = client["mozaiksai"][APP_DATA_MIGRATIONS_COLLECTION]
    migration = _migration()

    try:
        assert not hasattr(ModuleContext(app_id=app_id), "db")
        handler_source = inspect.getsource(RealPersistenceHandler)
        assert "ctx.persistence" in handler_source
        assert "ctx.db" not in handler_source
        assert "get_mongo_client" not in handler_source

        executor = ModuleExecutor()
        executor.register("projects", RealPersistenceHandler())

        create_result = await executor.execute(
            ModuleRequest(
                module="projects",
                action="create_project",
                app_id=app_id,
                user_id="integration_user",
                params={"project_id": project_id, "name": "Real Mongo Smoke"}, authority=trusted_framework_authority(),
            )
        )
        assert create_result.success is True

        await other_collection.insert_one(
            {
                "app_id": other_app_id,
                "project_id": f"{project_id}_other",
                "name": "Other App Project",
            }
        )

        list_result = await executor.execute(
            ModuleRequest(module="projects", action="list_projects", app_id=app_id, authority=trusted_framework_authority())
        )
        other_list_result = await executor.execute(
            ModuleRequest(module="projects", action="list_projects", app_id=other_app_id, authority=trusted_framework_authority())
        )

        assert list_result.success is True
        assert other_list_result.success is True
        assert [item["project_id"] for item in list_result.data["items"]] == [project_id]
        assert list_result.data["items"][0]["app_id"] == app_id
        assert list_result.data["items"][0]["user_id"] == "integration_user"
        assert other_list_result.data["items"][0]["app_id"] == other_app_id

        index_result = await apply_database_indexes(_data_contract(app_id), app_id=app_id)
        assert index_result.created == 1
        assert index_result.verified == 1
        index_names = {index["name"] for index in await raw_collection.list_indexes().to_list(length=None)}
        assert "project_owner_idx" in index_names

        applied_count = await apply_data_migrations(app_id=app_id, migrations=[migration])
        skipped_count = await apply_data_migrations(app_id=app_id, migrations=[migration])
        assert applied_count == 1
        assert skipped_count == 0

        index_names = {index["name"] for index in await raw_collection.list_indexes().to_list(length=None)}
        assert "project_status_idx" in index_names

        history_record = await history.find_one({"app_id": app_id, "migration_id": migration["migration_id"]})
        assert history_record is not None
        assert history_record["status"] == "applied"
        assert history_record["migration_hash"] == migration_hash(migration)
        assert str(history_record.get("lock_owner") or "").startswith("migration_")
        assert history_record.get("claimed_at") is not None
        assert history_record["operations_summary"] == [
            {"type": "ensure_index", "module_id": "projects", "entity_name": "projects"}
        ]
    finally:
        await history.delete_many({"app_id": {"$in": [app_id, other_app_id]}})
        if generated_database:
            await client.drop_database(database_name)
        else:
            await raw_collection.drop()
            await other_collection.drop()
        close_mongo_client()


@pytest.mark.asyncio
async def test_real_mongo_index_readiness_is_exact_and_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Prove creation, reread verification, hostile mismatch rejection, and no drops."""

    if not _has_mongo_connection_env():
        pytest.skip(
            "MONGO_URI, MONGODB_URI, or MONGO_URL is required for real-Mongo index readiness"
        )

    database_name, generated_database = _test_database_name()
    monkeypatch.setenv(APP_DB_ENV, database_name)
    app_id = f"index_readiness_{uuid4().hex}"
    prefix = f"refund_checkout_{uuid4().hex[:10]}"
    ready_name = f"{prefix}_ready"
    wrong_name = f"{prefix}_wrong_name"
    alternate_name = f"{prefix}_alternate_name"
    client = get_mongo_client()
    ready = client[database_name][ready_name]
    wrong = client[database_name][wrong_name]
    alternate = client[database_name][alternate_name]
    declared_indexes = [
        {
            "name": "refund_checkout_unique_partial",
            "keys": [["checkout_ref", 1]],
            "unique": True,
            "partialFilterExpression": {"checkout_ref": {"$type": "string"}},
            "collation": {"locale": "en", "strength": 2},
            "hidden": True,
        },
        {
            "name": "refund_expiry_ttl",
            "keys": [["expires_at", 1]],
            "expireAfterSeconds": 600,
            "hidden": True,
        },
        {"name": "refund_legacy_sparse", "keys": [["legacy_ref", 1]], "sparse": True},
        {
            "name": "refund_payload_wildcard",
            "keys": [["$**", 1]],
            "wildcardProjection": {"payload.secret": 0},
        },
    ]

    try:
        first = await apply_database_indexes(
            _literal_index_contract(app_id, ready_name, declared_indexes), app_id=app_id
        )
        second = await apply_database_indexes(
            _literal_index_contract(app_id, ready_name, declared_indexes), app_id=app_id
        )
        assert first.created == first.verified == 4
        assert second.created == 0
        assert second.verified == 4
        materialized = {row["name"]: row for row in await ready.list_indexes().to_list(length=None)}
        assert materialized["refund_checkout_unique_partial"]["unique"] is True
        assert materialized["refund_checkout_unique_partial"]["partialFilterExpression"] == {
            "checkout_ref": {"$type": "string"}
        }
        assert materialized["refund_checkout_unique_partial"]["collation"]["strength"] == 2
        assert materialized["refund_checkout_unique_partial"]["hidden"] is True
        assert materialized["refund_expiry_ttl"]["expireAfterSeconds"] == 600
        assert materialized["refund_legacy_sparse"]["sparse"] is True
        assert materialized["refund_payload_wildcard"]["wildcardProjection"] == {
            "payload.secret": 0
        }

        await wrong.create_index(
            [("wrong_field", 1)],
            name="checkout_refund_provider_reference_unique",
            unique=False,
        )
        before_wrong = {row["name"] for row in await wrong.list_indexes().to_list(length=None)}
        with pytest.raises(
            DatabaseIndexApplyError, match="requested index name exists.*keys expected"
        ):
            await apply_database_indexes(
                _literal_index_contract(
                    app_id,
                    wrong_name,
                    [
                        {
                            "name": "checkout_refund_provider_reference_unique",
                            "keys": [["stripe_refund_id", 1]],
                            "unique": True,
                            "partialFilterExpression": {"stripe_refund_id": {"$type": "string"}},
                        }
                    ],
                ),
                app_id=app_id,
            )
        assert {
            row["name"] for row in await wrong.list_indexes().to_list(length=None)
        } == before_wrong

        await alternate.create_index([("checkout_ref", 1)], name="legacy_checkout_lookup")
        before_alternate = {
            row["name"] for row in await alternate.list_indexes().to_list(length=None)
        }
        with pytest.raises(
            DatabaseIndexApplyError, match="under another name.*unique expected=True actual=False"
        ):
            await apply_database_indexes(
                _literal_index_contract(
                    app_id,
                    alternate_name,
                    [
                        {
                            "name": "refund_checkout_unique",
                            "keys": [["checkout_ref", 1]],
                            "unique": True,
                        }
                    ],
                ),
                app_id=app_id,
            )
        assert {
            row["name"] for row in await alternate.list_indexes().to_list(length=None)
        } == before_alternate
    finally:
        if generated_database:
            await client.drop_database(database_name)
        else:
            await ready.drop()
            await wrong.drop()
            await alternate.drop()
        close_mongo_client()
