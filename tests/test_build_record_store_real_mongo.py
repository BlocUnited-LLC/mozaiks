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

from mozaiksai.core.artifacts.store import (
    ArtifactLifecycleStatus,
    BuildRecordCurrentConflictError,
    BuildRecordStore,
)
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


class _NoSupersedeCollectionProxy:
    """Delegate to the real ArtifactVersions collection but skip supersede.

    Freezes an accept/create between its supersede step and its publish step,
    reproducing the interleaving where a concurrent independent client
    published CURRENT in that window. The uniqueness rejection itself comes
    from the real Mongo partial unique index, never from this proxy.
    """

    def __init__(self, collection) -> None:
        self._collection = collection

    def __getattr__(self, name: str):
        return getattr(self._collection, name)

    async def update_many(self, *_args, **_kwargs):
        return None


class _NoSupersedeStore(BuildRecordStore):
    async def _coll(self, name: str):
        collection = await super()._coll(name)
        if name == "ArtifactVersions":
            return _NoSupersedeCollectionProxy(collection)
        return collection


async def _current_records(client: AsyncIOMotorClient, database_name: str, *, app_id: str, build_family: str, build_key: str) -> list[dict]:
    versions = client[database_name]["ArtifactVersions"]
    return await versions.find(
        {
            "app_id": app_id,
            "build_family": build_family,
            "build_key": build_key,
            "lifecycle_status": "current",
        }
    ).to_list(length=None)


@pytest.mark.asyncio
async def test_storage_rejects_second_current_bypassing_store_real_mongo() -> None:
    """The invariant holds even for writers that bypass store code entirely."""
    client_a = AsyncIOMotorClient(_MONGO_URI)
    client_b = AsyncIOMotorClient(_MONGO_URI)
    database_name = f"mozaiks_build_record_test_{uuid4().hex}"
    try:
        store = _store(client_a, database_name)
        await store._ensure_indexes(client=store.client)

        r1 = await store.create_build_record(
            app_id="app-race",
            build_family="app_bundle",
            build_key="primary",
            lifecycle_status=ArtifactLifecycleStatus.DRAFT,
        )
        r2 = await store.create_build_record(
            app_id="app-race",
            build_family="app_bundle",
            build_key="primary",
            lifecycle_status=ArtifactLifecycleStatus.DRAFT,
        )
        accepted = await store.accept_build_record(app_id="app-race", build_record_id=r1.id)
        assert accepted is not None and accepted.lifecycle_status == ArtifactLifecycleStatus.CURRENT

        # Independent client, raw driver write: try to mint a second CURRENT.
        raw_versions = client_b[database_name]["ArtifactVersions"]
        with pytest.raises(DuplicateKeyError):
            await raw_versions.update_one(
                {"_id": r2.id}, {"$set": {"lifecycle_status": "current"}}
            )

        current = await _current_records(
            client_b, database_name, app_id="app-race", build_family="app_bundle", build_key="primary"
        )
        assert [row["_id"] for row in current] == [r1.id]
    finally:
        await client_a.drop_database(database_name)
        client_a.close()
        client_b.close()


@pytest.mark.asyncio
async def test_interleaved_accept_conflict_is_typed_and_keeps_one_current_real_mongo() -> None:
    """Codex 1's race: independent clients interleaving supersede/publish."""
    client_a = AsyncIOMotorClient(_MONGO_URI)
    client_b = AsyncIOMotorClient(_MONGO_URI)
    database_name = f"mozaiks_build_record_test_{uuid4().hex}"
    try:
        store_a = _store(client_a, database_name)
        await store_a._ensure_indexes(client=store_a.client)
        store_b = _NoSupersedeStore()
        store_b.client = _TestDatabaseClient(client_b, database_name)

        r1 = await store_a.create_build_record(
            app_id="app-race",
            build_family="app_bundle",
            build_key="primary",
            lifecycle_status=ArtifactLifecycleStatus.DRAFT,
        )
        r2 = await store_a.create_build_record(
            app_id="app-race",
            build_family="app_bundle",
            build_key="primary",
            lifecycle_status=ArtifactLifecycleStatus.DRAFT,
        )
        unrelated = await store_a.create_build_record(
            app_id="app-race",
            build_family="workflow_bundle",
            build_key="primary",
        )

        # Client A publishes r1 in the window after client B's supersede step
        # already ran (store_b skips supersede to freeze that interleaving).
        accepted = await store_a.accept_build_record(app_id="app-race", build_record_id=r1.id)
        assert accepted is not None and accepted.lifecycle_status == ArtifactLifecycleStatus.CURRENT

        with pytest.raises(BuildRecordCurrentConflictError) as conflict:
            await store_b.accept_build_record(app_id="app-race", build_record_id=r2.id)
        assert conflict.value.build_record_id == r2.id
        assert conflict.value.build_family == "app_bundle"

        current = await _current_records(
            client_a, database_name, app_id="app-race", build_family="app_bundle", build_key="primary"
        )
        assert [row["_id"] for row in current] == [r1.id]

        # The losing record was not published and unrelated families are untouched.
        r2_doc = await client_a[database_name]["ArtifactVersions"].find_one({"_id": r2.id})
        assert r2_doc is not None and r2_doc["lifecycle_status"] == "draft"
        unrelated_current = await _current_records(
            client_a, database_name, app_id="app-race", build_family="workflow_bundle", build_key="primary"
        )
        assert [row["_id"] for row in unrelated_current] == [unrelated.id]
    finally:
        await client_a.drop_database(database_name)
        client_a.close()
        client_b.close()


@pytest.mark.asyncio
async def test_interleaved_create_current_conflict_is_typed_real_mongo() -> None:
    client_a = AsyncIOMotorClient(_MONGO_URI)
    client_b = AsyncIOMotorClient(_MONGO_URI)
    database_name = f"mozaiks_build_record_test_{uuid4().hex}"
    try:
        store_a = _store(client_a, database_name)
        await store_a._ensure_indexes(client=store_a.client)
        store_b = _NoSupersedeStore()
        store_b.client = _TestDatabaseClient(client_b, database_name)

        first = await store_a.create_build_record(
            app_id="app-race",
            build_family="app_bundle",
            build_key="primary",
        )
        assert first.lifecycle_status == ArtifactLifecycleStatus.CURRENT

        with pytest.raises(BuildRecordCurrentConflictError):
            await store_b.create_build_record(
                app_id="app-race",
                build_family="app_bundle",
                build_key="primary",
            )

        current = await _current_records(
            client_a, database_name, app_id="app-race", build_family="app_bundle", build_key="primary"
        )
        assert [row["_id"] for row in current] == [first.id]
    finally:
        await client_a.drop_database(database_name)
        client_a.close()
        client_b.close()


@pytest.mark.asyncio
async def test_same_target_accept_retry_is_idempotent_real_mongo() -> None:
    client = AsyncIOMotorClient(_MONGO_URI)
    database_name = f"mozaiks_build_record_test_{uuid4().hex}"
    try:
        store = _store(client, database_name)
        await store._ensure_indexes(client=store.client)

        r1 = await store.create_build_record(
            app_id="app-retry",
            build_family="app_bundle",
            build_key="primary",
            lifecycle_status=ArtifactLifecycleStatus.DRAFT,
        )
        first = await store.accept_build_record(app_id="app-retry", build_record_id=r1.id)
        retried = await store.accept_build_record(app_id="app-retry", build_record_id=r1.id)
        assert first is not None and retried is not None
        assert first.lifecycle_status == ArtifactLifecycleStatus.CURRENT
        assert retried.lifecycle_status == ArtifactLifecycleStatus.CURRENT

        current = await _current_records(
            client, database_name, app_id="app-retry", build_family="app_bundle", build_key="primary"
        )
        assert [row["_id"] for row in current] == [r1.id]
    finally:
        await client.drop_database(database_name)
        client.close()


@pytest.mark.asyncio
async def test_sequential_different_target_accept_supersedes_real_mongo() -> None:
    """Non-interleaved publication is last-writer-wins, never two CURRENT."""
    client = AsyncIOMotorClient(_MONGO_URI)
    database_name = f"mozaiks_build_record_test_{uuid4().hex}"
    try:
        store = _store(client, database_name)
        await store._ensure_indexes(client=store.client)

        r1 = await store.create_build_record(
            app_id="app-seq",
            build_family="app_bundle",
            build_key="primary",
            lifecycle_status=ArtifactLifecycleStatus.DRAFT,
        )
        r2 = await store.create_build_record(
            app_id="app-seq",
            build_family="app_bundle",
            build_key="primary",
            lifecycle_status=ArtifactLifecycleStatus.DRAFT,
        )
        await store.accept_build_record(app_id="app-seq", build_record_id=r1.id)
        await store.accept_build_record(app_id="app-seq", build_record_id=r2.id)

        current = await _current_records(
            client, database_name, app_id="app-seq", build_family="app_bundle", build_key="primary"
        )
        assert [row["_id"] for row in current] == [r2.id]
        r1_doc = await client[database_name]["ArtifactVersions"].find_one({"_id": r1.id})
        assert r1_doc is not None and r1_doc["lifecycle_status"] == "superseded"
    finally:
        await client.drop_database(database_name)
        client.close()


@pytest.mark.asyncio
async def test_unique_current_index_mismatch_fails_closed_real_mongo() -> None:
    client = AsyncIOMotorClient(_MONGO_URI)
    database_name = f"mozaiks_build_record_test_{uuid4().hex}"
    try:
        versions = client[database_name]["ArtifactVersions"]
        # Same canonical name, weaker filter: verifier must reject, not adopt.
        await versions.create_index(
            [("app_id", 1), ("build_family", 1), ("build_key", 1)],
            name="av_unique_current",
            unique=True,
            partialFilterExpression={"lifecycle_status": "draft"},
        )

        store = _store(client, database_name)
        with pytest.raises(DatabaseIndexApplyError):
            await store._ensure_indexes(client=store.client)
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
