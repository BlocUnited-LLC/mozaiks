"""Real-Mongo proofs for canonical BuildRecord identity indexes.

BuildRecord documents carry (app_id, build_family, build_key, version_number);
the store's unique indexes must use those same fields. The retired indexes on
fields no record carries made every record contribute a null identity tuple,
so the second build family for an app collided with E11000.
"""
from __future__ import annotations

import os
from uuid import uuid4

import pytest
from motor.motor_asyncio import AsyncIOMotorClient
from pymongo.errors import DuplicateKeyError

from mozaiksai.core.artifacts.store import BuildRecordStore
from mozaiksai.core.runtime.persistence.indexes import DatabaseIndexApplyError

pytestmark = pytest.mark.skipif(
    os.environ.get("MOZAIKS_RUN_REAL_MONGO_TESTS") != "1",
    reason="set MOZAIKS_RUN_REAL_MONGO_TESTS=1 for real Mongo BuildRecord index tests",
)

_MONGO_URI = os.environ.get("MONGO_URI", "mongodb://127.0.0.1:27017")


class _TestDatabaseClient:
    """Route the store's fixed database name to one disposable test database."""

    def __init__(self, client: AsyncIOMotorClient, database_name: str) -> None:
        self._client = client
        self._database_name = database_name

    def __getitem__(self, _name: str):
        return self._client[self._database_name]


def _store(client: AsyncIOMotorClient, database_name: str) -> BuildRecordStore:
    store = BuildRecordStore()
    store.client = _TestDatabaseClient(client, database_name)
    return store


@pytest.mark.asyncio
async def test_two_build_families_do_not_collide_real_mongo() -> None:
    client = AsyncIOMotorClient(_MONGO_URI)
    database_name = f"mozaiks_build_record_test_{uuid4().hex}"
    try:
        store = _store(client, database_name)
        await store._ensure_indexes(client=store.client)

        first = await store.create_build_record(
            app_id="app-families",
            build_family="app_bundle",
            build_key="primary",
        )
        second = await store.create_build_record(
            app_id="app-families",
            build_family="workflow_bundle",
            build_key="primary",
        )

        assert first.build_family == "app_bundle"
        assert second.build_family == "workflow_bundle"
        assert first.version_number == 1
        assert second.version_number == 1
    finally:
        await client.drop_database(database_name)
        client.close()


@pytest.mark.asyncio
async def test_duplicate_canonical_identity_rejects_real_mongo() -> None:
    client = AsyncIOMotorClient(_MONGO_URI)
    database_name = f"mozaiks_build_record_test_{uuid4().hex}"
    try:
        store = _store(client, database_name)
        await store._ensure_indexes(client=store.client)
        record = await store.create_build_record(
            app_id="app-dup",
            build_family="app_bundle",
            build_key="primary",
        )

        versions = client[database_name]["ArtifactVersions"]
        duplicate = {
            "_id": f"av_{uuid4().hex[:24]}",
            "app_id": "app-dup",
            "build_family": record.build_family,
            "build_key": record.build_key,
            "version_number": record.version_number,
        }
        with pytest.raises(DuplicateKeyError):
            await versions.insert_one(duplicate)
    finally:
        await client.drop_database(database_name)
        client.close()


@pytest.mark.asyncio
async def test_retired_obsolete_indexes_are_dropped_real_mongo() -> None:
    client = AsyncIOMotorClient(_MONGO_URI)
    database_name = f"mozaiks_build_record_test_{uuid4().hex}"
    try:
        versions = client[database_name]["ArtifactVersions"]
        counters = client[database_name]["ArtifactVersionCounters"]
        await versions.create_index(
            [("app_id", 1), ("artifact_kind", 1), ("artifact_key", 1), ("version_number", -1)],
            name="av_app_kind_key_version",
            unique=True,
        )
        await counters.create_index(
            [("app_id", 1), ("artifact_kind", 1), ("artifact_key", 1)],
            name="avc_app_kind_key",
            unique=True,
        )

        store = _store(client, database_name)
        await store._ensure_indexes(client=store.client)

        version_indexes = {
            str(row["name"]): row
            for row in await versions.list_indexes().to_list(length=None)
        }
        counter_indexes = {
            str(row["name"]): row
            for row in await counters.list_indexes().to_list(length=None)
        }
        assert "av_app_kind_key_version" not in version_indexes
        assert "avc_app_kind_key" not in counter_indexes
        assert list(version_indexes["av_app_family_key_version"]["key"].items()) == [
            ("app_id", 1),
            ("build_family", 1),
            ("build_key", 1),
            ("version_number", -1),
        ]
        assert version_indexes["av_app_family_key_version"].get("unique") is True
        assert list(counter_indexes["avc_app_family_key"]["key"].items()) == [
            ("app_id", 1),
            ("build_family", 1),
            ("build_key", 1),
        ]
        assert counter_indexes["avc_app_family_key"].get("unique") is True

        # The corrected indexes now allow distinct families for one app.
        await store.create_build_record(
            app_id="app-migrated", build_family="app_bundle", build_key="k"
        )
        await store.create_build_record(
            app_id="app-migrated", build_family="workflow_bundle", build_key="k"
        )
    finally:
        await client.drop_database(database_name)
        client.close()


@pytest.mark.asyncio
async def test_unrelated_same_name_index_survives_and_store_fails_closed_real_mongo() -> None:
    """A foreign index reusing a retired name must never be dropped."""
    client = AsyncIOMotorClient(_MONGO_URI)
    database_name = f"mozaiks_build_record_test_{uuid4().hex}"
    try:
        versions = client[database_name]["ArtifactVersions"]
        await versions.create_index(
            [("security_boundary", 1)],
            name="av_app_kind_key_version",
        )

        store = _store(client, database_name)
        with pytest.raises(DatabaseIndexApplyError, match="Refusing to drop"):
            await store._ensure_indexes(client=store.client)

        surviving = {
            str(row["name"]): row
            for row in await versions.list_indexes().to_list(length=None)
        }
        assert "av_app_kind_key_version" in surviving
        assert list(surviving["av_app_kind_key_version"]["key"].items()) == [
            ("security_boundary", 1)
        ]
        # Initialization failed before installing anything canonical.
        assert "av_app_family_key_version" not in surviving
    finally:
        await client.drop_database(database_name)
        client.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("keys", "options", "reason"),
    [
        (
            [("app_id", 1), ("artifact_kind", 1)],
            {"unique": True},
            "partial key match",
        ),
        (
            [("app_id", 1), ("artifact_kind", 1), ("artifact_key", 1), ("version_number", -1)],
            {},
            "same keys wrong unique",
        ),
        (
            [("app_id", 1), ("artifact_kind", 1), ("artifact_key", 1), ("version_number", -1)],
            {"unique": True, "sparse": True},
            "same keys wrong option",
        ),
    ],
)
async def test_retired_name_with_divergent_definition_fails_closed_real_mongo(
    keys, options, reason
) -> None:
    client = AsyncIOMotorClient(_MONGO_URI)
    database_name = f"mozaiks_build_record_test_{uuid4().hex}"
    try:
        versions = client[database_name]["ArtifactVersions"]
        await versions.create_index(keys, name="av_app_kind_key_version", **options)

        store = _store(client, database_name)
        with pytest.raises(DatabaseIndexApplyError, match="Refusing to drop"):
            await store._ensure_indexes(client=store.client)

        surviving = {
            str(row["name"]) for row in await versions.list_indexes().to_list(length=None)
        }
        assert "av_app_kind_key_version" in surviving, reason
    finally:
        await client.drop_database(database_name)
        client.close()


@pytest.mark.asyncio
async def test_repeated_initialization_is_idempotent_real_mongo() -> None:
    client = AsyncIOMotorClient(_MONGO_URI)
    database_name = f"mozaiks_build_record_test_{uuid4().hex}"
    try:
        store = _store(client, database_name)
        await store._ensure_indexes(client=store.client)
        await store._ensure_indexes(client=store.client)
        versions = client[database_name]["ArtifactVersions"]
        names = {
            str(row["name"]) for row in await versions.list_indexes().to_list(length=None)
        }
        assert "av_app_family_key_version" in names
    finally:
        await client.drop_database(database_name)
        client.close()


@pytest.mark.asyncio
async def test_mismatched_canonical_definition_fails_closed_real_mongo() -> None:
    client = AsyncIOMotorClient(_MONGO_URI)
    database_name = f"mozaiks_build_record_test_{uuid4().hex}"
    try:
        versions = client[database_name]["ArtifactVersions"]
        # Same canonical name, different definition: the verifier must reject
        # rather than accept the index by name.
        await versions.create_index(
            [("app_id", 1), ("build_family", 1)],
            name="av_app_family_key_version",
        )

        store = _store(client, database_name)
        with pytest.raises(DatabaseIndexApplyError):
            await store._ensure_indexes(client=store.client)
    finally:
        await client.drop_database(database_name)
        client.close()
