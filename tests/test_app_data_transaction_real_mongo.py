"""Opt-in transaction proof against an explicitly selected local replica set."""

from __future__ import annotations

import os
from types import SimpleNamespace
from urllib.parse import urlparse
from uuid import uuid4

import pytest
from motor.motor_asyncio import AsyncIOMotorClient
from pymongo.errors import OperationFailure

from mozaiksai.core.runtime.persistence import (
    AppData,
    MongoPersistenceContext,
    PersistencePrincipal,
    PersistenceScopeError,
    collection_name_for,
    run_app_data_transaction,
)

URI_ENV = "MOZAIKS_APP_TRANSACTION_TEST_URI"
pytestmark = pytest.mark.skipif(not os.getenv(URI_ENV), reason=f"set {URI_ENV} to an owned local replica set")


@pytest.fixture
async def database():
    uri = os.environ[URI_ENV]
    parsed = urlparse(uri)
    if parsed.scheme != "mongodb" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"} or parsed.username:
        pytest.fail("Transaction proof requires an owned local Mongo server without real credentials")
    client = AsyncIOMotorClient(uri, serverSelectionTimeoutMS=3000)
    name = f"mozaiks_transaction_test_{uuid4().hex}"
    try:
        hello = await client.admin.command("hello")
        assert hello.get("setName") and hello.get("logicalSessionTimeoutMinutes")
        db = client[name]
        await db.create_collection("messages")
        await db.create_collection("journal")
        persistence = MongoPersistenceContext(app_id="test-app", client=client, database_name=name)
        ctx = SimpleNamespace(app_id="test-app", persistence=persistence)
        ctx.app_data = AppData(
            aliases={"messages": "messages", "journal": "journal"},
            collection_resolver=persistence.literal_collection,
        )
        yield ctx, client, db
    finally:
        # This fixture creates and drops only its own unpredictable database.
        await client.drop_database(name)
        client.close()


async def persist_pair(data):
    await data.collection("messages").insert_one({"_id": "message-one", "app_id": "test-app"})
    await data.collection("journal").insert_one({"_id": "event-one", "app_id": "test-app"})
    return "event-one"


async def test_committed_pair_is_visible_only_after_callback_returns(database):
    ctx, _client, db = database
    cached = ctx.app_data

    async def callback(data):
        result = await persist_pair(data)
        assert await db.messages.count_documents({}) == 0
        assert await db.journal.count_documents({}) == 0
        assert await data.collection("journal").find_one({"_id": result}) is not None
        return result

    assert await run_app_data_transaction(ctx, callback) == "event-one"
    assert await db.messages.count_documents({}) == await db.journal.count_documents({}) == 1
    assert ctx.app_data is cached


async def test_failed_callback_rolls_back_both_aliases(database):
    ctx, _client, db = database

    async def callback(data):
        await persist_pair(data)
        raise ValueError("abort the pair")

    with pytest.raises(ValueError, match="abort the pair"):
        await run_app_data_transaction(ctx, callback)
    assert await db.messages.count_documents({}) == await db.journal.count_documents({}) == 0


async def test_real_driver_retries_a_transient_callback_without_duplicate_rows(database):
    ctx, _client, db = database
    attempts = []

    async def callback(data):
        if attempts:
            with pytest.raises(PersistenceScopeError, match="outside"):
                attempts[0].collection("messages")
        attempts.append(data)
        result = await persist_pair(data)
        if len(attempts) == 1:
            raise OperationFailure(
                "synthetic transient conflict after real writes", code=112,
                details={"errorLabels": ["TransientTransactionError"]},
            )
        return result

    assert await run_app_data_transaction(ctx, callback) == "event-one"
    assert len(attempts) == 2
    assert await db.messages.count_documents({}) == await db.journal.count_documents({}) == 1


async def test_real_guarded_aliases_reject_scope_escape_and_expire_after_commit(database):
    ctx, client, db = database
    contract = {"version": "1", "surfaces": [{
        "surface_id": "private", "surface_kind": "module", "collections": [{
            "name": "records", "entity": "Record", "tenancy": "per_user", "owner_field": "created_by",
        }],
    }]}
    ctx.persistence = MongoPersistenceContext(
        app_id="test-app", client=client, database_name=db.name, data_contract=contract,
        principal=PersistencePrincipal("human-one"),
    )
    owned = collection_name_for(app_id="test-app", module_id="private", entity_name="records")
    ctx.app_data = AppData(
        aliases={"messages": "messages", "journal": "journal", "private": owned},
        collection_resolver=ctx.persistence.literal_collection,
    )

    async def callback(data):
        with pytest.raises(PersistenceScopeError, match="owned collections"):
            data.collection("private")
        messages = data.collection("messages")
        with pytest.raises(PersistenceScopeError):
            messages.aggregate([{"$lookup": {"from": owned}}])
        with pytest.raises(PersistenceScopeError, match="runtime-owned"):
            await messages.insert_one({"_id": "bad"}, session=None)
        await persist_pair(data)
        cursor = messages.find({"app_id": "test-app"}).sort("_id").limit(10)
        assert len(await cursor.to_list(length=10)) == 1
        return messages, cursor

    messages, cursor = await run_app_data_transaction(ctx, callback)
    with pytest.raises(PersistenceScopeError, match="outside"):
        await messages.insert_one({"_id": "late"})
    with pytest.raises(PersistenceScopeError, match="outside"):
        await cursor.to_list(length=10)
    assert await db.messages.count_documents({}) == await db.journal.count_documents({}) == 1
    assert owned not in await db.list_collection_names()
