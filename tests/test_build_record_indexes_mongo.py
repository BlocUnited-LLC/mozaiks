"""Index behavior against MongoDB, using disposable test-only databases."""

from __future__ import annotations

import os
from uuid import uuid4

import pytest
import pytest_asyncio
from motor.motor_asyncio import AsyncIOMotorClient
from pymongo.errors import DuplicateKeyError

from mozaiksai.core.artifacts.store import BuildRecordStore


@pytest_asyncio.fixture
async def records(monkeypatch):
    database_name = f"build_record_indexes_{uuid4().hex}"
    client = AsyncIOMotorClient(os.getenv("MONGO_URI", "mongodb://127.0.0.1:27017"), serverSelectionTimeoutMS=1500)
    try:
        await client.admin.command("ping")
    except Exception:
        client.close()
        if os.getenv("MOZAIKS_REQUIRE_REAL_MONGO"):
            pytest.fail("Real MongoDB is required")
        pytest.skip("MongoDB is unavailable")
    database = client[database_name]
    store = BuildRecordStore()

    async def collection(name):
        return database[name]

    monkeypatch.setattr(store, "_coll", collection)
    try:
        yield store, database
    finally:
        assert database_name.startswith("build_record_indexes_")
        await client.drop_database(database_name)
        client.close()


@pytest.mark.asyncio
async def test_index_upgrade_preserves_records_and_separates_families(records):
    store, database = records
    await database.ArtifactVersions.create_index(
        [("app_id", 1), ("artifact_kind", 1), ("artifact_key", 1), ("version_number", -1)],
        name="av_app_kind_key_version", unique=True,
    )
    await database.ArtifactVersionCounters.create_index(
        [("app_id", 1), ("artifact_kind", 1), ("artifact_key", 1)],
        name="avc_app_kind_key", unique=True,
    )
    preexisting = {"_id": "old-record", "app_id": "app", "artifact_kind": "concept", "artifact_key": "concept", "version_number": 1}
    await database.ArtifactVersions.insert_one(dict(preexisting))
    await store._ensure_indexes(database)
    await store._ensure_indexes(database)
    assert await database.ArtifactVersions.find_one({"_id": "old-record"}) == preexisting
    assert "av_app_kind_key_version" not in await database.ArtifactVersions.index_information()
    assert "avc_app_kind_key" not in await database.ArtifactVersionCounters.index_information()

    created = []
    for app, family, key in [("app", "concept", "concept"), ("app", "design_docs", "design_docs"), ("app", "design_docs", "alternate"), ("other", "concept", "concept")]:
        record = await store.create_build_record(app_id=app, build_family=family, build_key=key)
        assert record.version_number == 1
        created.append(record)
    next_version = await store.create_build_record(app_id="app", build_family="concept", build_key="concept")
    assert next_version.version_number == 2
    duplicate = created[0].model_dump(by_alias=True)
    duplicate["_id"] = "duplicate"
    with pytest.raises(DuplicateKeyError):
        await database.ArtifactVersions.insert_one(duplicate)
    assert await database.ArtifactVersions.count_documents({}) == 6


@pytest.mark.asyncio
async def test_existing_canonical_records_do_not_collide_on_missing_retired_fields(records):
    store, database = records
    for family in ("concept", "design_docs"):
        await store.create_build_record(app_id="app", build_family=family, build_key=family)
    await store._ensure_indexes(database)
    assert await database.ArtifactVersions.count_documents({}) == 2


@pytest.mark.asyncio
async def test_unexpected_operator_index_is_not_dropped(records):
    store, database = records
    await database.ArtifactVersions.create_index([("operator_field", 1)], name="av_app_kind_key_version", unique=True)
    with pytest.raises(RuntimeError, match="unexpected index definition"):
        await store._ensure_indexes(database)
    assert "av_app_kind_key_version" in await database.ArtifactVersions.index_information()
