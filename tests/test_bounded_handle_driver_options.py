"""A bounded alias handle forwards only the options Mongo defines for each method.

The alias handle returned by ``ctx.persistence.literal_collection(...)`` (and the
``app_data`` alias path) wraps a real driver collection. The installed driver
merges any keyword it does not recognise into the command document it sends to
the server, and the server reads a few of those fields as the collection or
database to act on; a raw ``$query`` command envelope in a filter has the same effect
for reads. These tests pin the handle's contract: only the documented options
and positional arity for each method are forwarded, everything else fails closed
with :class:`PersistenceScopeError` before the driver builds a command, and the
options callers legitimately use keep working.
"""

from __future__ import annotations

import os
import uuid
from types import SimpleNamespace

import pytest

from mozaiksai.core.runtime.persistence import (
    MongoPersistenceContext,
    PersistencePrincipal,
    PersistenceScopeError,
)
from mozaiksai.core.runtime.persistence.alias_collection import (
    GuardedAliasCollection,
    GuardedAliasCursor,
)

# --------------------------------------------------------------------------- unit


class _RecordingCursor:
    def __init__(self, rows: list[dict] | None = None) -> None:
        self.rows = rows or []

    def sort(self, key_or_list, direction=None):
        self.sort_call = (key_or_list, direction)
        return self

    def limit(self, limit):
        return self

    def skip(self, skip):
        return self

    async def to_list(self, length=None):
        return list(self.rows)


class _RecordingCollection:
    """Records every forwarded driver call; a refused call must leave this empty."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple, dict]] = []

    def _record_sync(self, name, args, kwargs, result):
        self.calls.append((name, args, kwargs))
        return result

    async def _record(self, name, args, kwargs, result):
        self.calls.append((name, args, kwargs))
        return result

    def find(self, *args, **kwargs):
        return self._record_sync("find", args, kwargs, _RecordingCursor([{"ok": 1}]))

    def aggregate(self, *args, **kwargs):
        return self._record_sync("aggregate", args, kwargs, _RecordingCursor([{"ok": 1}]))

    async def find_one(self, *args, **kwargs):
        return await self._record("find_one", args, kwargs, {"ok": 1})

    async def insert_one(self, *args, **kwargs):
        return await self._record("insert_one", args, kwargs, SimpleNamespace(inserted_id="x"))

    async def insert_many(self, *args, **kwargs):
        return await self._record("insert_many", args, kwargs, SimpleNamespace(inserted_ids=["x"]))

    async def update_one(self, *args, **kwargs):
        return await self._record("update_one", args, kwargs, SimpleNamespace(modified_count=1))

    async def update_many(self, *args, **kwargs):
        return await self._record("update_many", args, kwargs, SimpleNamespace(modified_count=1))

    async def replace_one(self, *args, **kwargs):
        return await self._record("replace_one", args, kwargs, SimpleNamespace(modified_count=1))

    async def delete_one(self, *args, **kwargs):
        return await self._record("delete_one", args, kwargs, SimpleNamespace(deleted_count=1))

    async def delete_many(self, *args, **kwargs):
        return await self._record("delete_many", args, kwargs, SimpleNamespace(deleted_count=1))

    async def count_documents(self, *args, **kwargs):
        return await self._record("count_documents", args, kwargs, 1)

    async def distinct(self, *args, **kwargs):
        return await self._record("distinct", args, kwargs, [1, 2])

    async def find_one_and_update(self, *args, **kwargs):
        return await self._record("find_one_and_update", args, kwargs, {"ok": 1})

    async def find_one_and_replace(self, *args, **kwargs):
        return await self._record("find_one_and_replace", args, kwargs, {"ok": 1})

    async def find_one_and_delete(self, *args, **kwargs):
        return await self._record("find_one_and_delete", args, kwargs, {"ok": 1})


async def _invoke(handle: GuardedAliasCollection, method: str, *args, **kwargs):
    attribute = getattr(handle, method)
    result = attribute(*args, **kwargs)
    if method in {"find", "aggregate"}:
        # Cursor-returning methods validate synchronously at call time.
        assert isinstance(result, GuardedAliasCursor)
        return result
    return await result


# (method, args, allowed-option) — allowed options forward unchanged.
_ALLOWED = [
    ("find", ({"state": "open"},), {"projection": {"_id": 0}, "sort": [("rank", 1)]}),
    ("find_one", ({"state": "open"},), {"projection": {"_id": 0}, "comment": "probe"}),
    ("insert_one", ({"state": "open"},), {"bypass_document_validation": False}),
    ("insert_many", ([{"state": "open"}],), {"ordered": True}),
    ("update_one", ({"a": 1}, {"$set": {"b": 2}}), {"upsert": True}),
    ("update_many", ({"a": 1}, {"$set": {"b": 2}}), {"array_filters": []}),
    ("replace_one", ({"a": 1}, {"a": 1}), {"upsert": True}),
    ("delete_one", ({"a": 1},), {"comment": "probe"}),
    ("delete_many", ({"a": 1},), {"hint": "a_1"}),
    ("count_documents", ({"a": 1},), {"skip": 1, "limit": 5, "maxTimeMS": 50}),
    ("distinct", ("field", {"a": 1}), {"maxTimeMS": 50}),
    ("find_one_and_update", ({"a": 1}, {"$set": {"b": 2}}), {"upsert": True, "return_document": True}),
    ("find_one_and_replace", ({"a": 1}, {"a": 1}), {"return_document": True, "projection": {"_id": 0}}),
    ("find_one_and_delete", ({"a": 1},), {"projection": {"_id": 0}, "sort": [("rank", 1)]}),
    ("aggregate", ([{"$match": {"a": 1}}],), {"allowDiskUse": True, "batchSize": 10}),
]

# (method, args, refused-option) — an option Mongo would otherwise merge into the
# command document. The option names a verb/namespace/database/pipeline.
_REFUSED_OPTION = [
    ("count_documents", ({"a": 1},), {"pipeline": [{"$collStats": {}}]}),
    ("count_documents", ({"a": 1},), {"aggregate": "other"}),
    ("distinct", ("field", {"a": 1}), {"distinct": "other"}),
    ("aggregate", ([{"$match": {"a": 1}}],), {"aggregate": "other"}),
    ("aggregate", ([{"$match": {"a": 1}}],), {"$db": "other"}),
    ("find_one_and_update", ({"a": 1}, {"$set": {"b": 2}}), {"findAndModify": "other"}),
    ("find_one_and_update", ({"a": 1}, {"$set": {"b": 2}}), {"$db": "other"}),
    ("find_one_and_replace", ({"a": 1}, {"a": 1}), {"findAndModify": "other"}),
    ("find_one_and_delete", ({"a": 1},), {"findAndModify": "other"}),
    ("find", ({"a": 1},), {"find": "other"}),
    ("find_one", ({"a": 1},), {"find": "other"}),
    ("insert_one", ({"a": 1},), {"insert": "other"}),
    ("update_one", ({"a": 1}, {"$set": {"b": 2}}), {"update": "other"}),
    ("delete_one", ({"a": 1},), {"delete": "other"}),
]


@pytest.mark.parametrize("method,args,option", _ALLOWED)
async def test_allowed_options_forward_unchanged(method, args, option):
    raw = _RecordingCollection()
    handle = GuardedAliasCollection(raw)
    await _invoke(handle, method, *args, **option)
    assert len(raw.calls) == 1
    recorded_name, recorded_args, recorded_kwargs = raw.calls[0]
    assert recorded_name == method
    assert recorded_args == args
    assert recorded_kwargs == option


@pytest.mark.parametrize("method,args,option", _REFUSED_OPTION)
async def test_refused_option_fails_closed_without_calling_the_driver(method, args, option):
    raw = _RecordingCollection()
    handle = GuardedAliasCollection(raw)
    with pytest.raises(PersistenceScopeError):
        await _invoke(handle, method, *args, **option)
    assert raw.calls == []


@pytest.mark.parametrize("method", ["find", "find_one"])
async def test_filter_command_envelope_is_refused(method):
    raw = _RecordingCollection()
    handle = GuardedAliasCollection(raw)
    with pytest.raises(PersistenceScopeError):
        await _invoke(handle, method, {"$query": {}, "find": "other"})
    assert raw.calls == []


@pytest.mark.parametrize("method,args", [
    ("find", ({"a": 1}, {"_id": 0}, "surplus")),
    ("find_one", ({"a": 1}, {"_id": 0}, "surplus")),
    ("insert_one", ({"a": 1}, "surplus")),
    ("count_documents", ({"a": 1}, "surplus")),
    ("update_one", ({"a": 1}, {"$set": {"b": 2}}, "surplus")),
    ("delete_one", ({"a": 1}, "surplus")),
    ("find_one_and_update", ({"a": 1}, {"$set": {"b": 2}}, "surplus")),
    ("distinct", ("field", {"a": 1}, "surplus")),
])
async def test_surplus_positional_is_refused(method, args):
    raw = _RecordingCollection()
    handle = GuardedAliasCollection(raw)
    with pytest.raises(PersistenceScopeError):
        await _invoke(handle, method, *args)
    assert raw.calls == []


# --------------------------------------------------------------------------- real Mongo


def _owned_contract() -> dict:
    """One per-user owned collection, which turns on bounded alias handles."""
    return {"version": "1", "surfaces": [
        {"surface_id": "secrets", "surface_kind": "module", "collections": [{
            "name": "vault", "entity": "Secret", "scope": "app",
            "tenancy": "per_user", "owner_field": "user_id",
            "fields": [{"name": "user_id", "type": "string", "required": True}],
        }]},
    ], "shared_collections": []}


@pytest.fixture
async def mongo():
    from motor.motor_asyncio import AsyncIOMotorClient

    uri = os.environ.get("MONGO_URI")
    if not uri:
        if os.getenv("MOZAIKS_REQUIRE_REAL_MONGO"):
            pytest.fail("Real MongoDB is required")
        pytest.skip("MONGO_URI is not set")
    client = AsyncIOMotorClient(uri, serverSelectionTimeoutMS=2000)
    try:
        await client.admin.command("ping")
    except Exception:
        client.close()
        if os.getenv("MOZAIKS_REQUIRE_REAL_MONGO"):
            pytest.fail("Real MongoDB is required")
        pytest.skip("MongoDB is unavailable")
    database = f"bounded_handle_options_{uuid.uuid4().hex[:12]}"
    other = f"{database}_other"
    yield SimpleNamespace(client=client, database=database, other=other)
    await client.drop_database(database)
    await client.drop_database(other)
    client.close()


async def _seed_markers(mongo) -> None:
    await mongo.client[mongo.database]["foreign_store"].insert_many(
        [{"k": 1, "marker": "foreign"}, {"k": 2, "marker": "foreign"}]
    )
    await mongo.client[mongo.other]["foreign_store"].insert_one({"k": 1, "marker": "other-db"})


async def _markers_intact(mongo) -> bool:
    same = await mongo.client[mongo.database]["foreign_store"].count_documents({})
    other = await mongo.client[mongo.other]["foreign_store"].count_documents({})
    changed = await mongo.client[mongo.database]["foreign_store"].count_documents({"marker": {"$ne": "foreign"}})
    return same == 2 and other == 1 and changed == 0


async def test_bounded_alias_refuses_option_routes_on_real_mongo(mongo):
    ctx = MongoPersistenceContext(
        app_id="app-a", client=mongo.client, database_name=mongo.database,
        data_contract=_owned_contract(), principal=PersistencePrincipal("user-a", "ws-a"),
    )
    handle = ctx.literal_collection("alias_store")
    assert isinstance(handle, GuardedAliasCollection)
    await _seed_markers(mongo)

    # Each of these, forwarded to the installed driver, would merge an option
    # into the command document (or lift a filter envelope) and reach the
    # foreign collection or the second database. All must now fail closed.
    async def refused(coro):
        with pytest.raises(PersistenceScopeError):
            await coro

    await refused(handle.count_documents({}, pipeline=[{"$collStats": {}}]))
    await refused(handle.distinct("marker", distinct="foreign_store"))
    await refused(handle.find_one_and_update({}, {"$set": {"marker": "x"}}, findAndModify="foreign_store"))
    await refused(handle.find_one_and_replace({}, {"marker": "x"}, findAndModify="foreign_store"))
    await refused(handle.find_one_and_delete({}, findAndModify="foreign_store"))
    await refused(handle.find_one({"$query": {}, "find": "foreign_store"}))
    with pytest.raises(PersistenceScopeError):
        handle.aggregate([{"$match": {}}], aggregate="foreign_store")
    with pytest.raises(PersistenceScopeError):
        handle.find({"$query": {}, "find": "foreign_store"})

    assert await _markers_intact(mongo)


async def test_bounded_alias_still_runs_legitimate_options_on_real_mongo(mongo):
    from pymongo import ReturnDocument

    ctx = MongoPersistenceContext(
        app_id="app-a", client=mongo.client, database_name=mongo.database,
        data_contract=_owned_contract(), principal=PersistencePrincipal("user-a", "ws-a"),
    )
    handle = ctx.literal_collection("alias_store")

    await handle.insert_many([{"probe": 1, "state": "new"}, {"probe": 2, "state": "new"}])
    assert await handle.count_documents({"state": "new"}, limit=5) == 2
    assert await handle.count_documents({}) == 2
    assert (await handle.update_many({"state": "new"}, {"$set": {"state": "active"}})).modified_count == 2
    assert sorted(await handle.distinct("probe", {"state": "active"})) == [1, 2]
    updated = await handle.find_one_and_update(
        {"probe": 1}, {"$set": {"state": "updated"}}, return_document=ReturnDocument.AFTER,
    )
    assert updated["state"] == "updated"
    replaced = await handle.find_one_and_replace(
        {"probe": 1}, {"probe": 1, "state": "replaced"}, return_document=ReturnDocument.AFTER,
    )
    assert replaced["state"] == "replaced"
    assert (await handle.replace_one({"probe": 2}, {"probe": 2, "state": "done"}, upsert=False)).modified_count == 1
    rows = await handle.find({}, {"_id": 0}).sort("probe", 1).to_list(length=None)
    assert [row["probe"] for row in rows] == [1, 2]
    one = await handle.find_one({"probe": 1}, {"_id": 0})
    assert one["state"] == "replaced"
    grouped = await handle.aggregate(
        [{"$group": {"_id": "$state", "n": {"$sum": 1}}}], allowDiskUse=True,
    ).to_list(length=None)
    assert {row["_id"] for row in grouped} == {"replaced", "done"}
    assert (await handle.find_one_and_delete({"probe": 1}))["probe"] == 1
    assert (await handle.delete_one({"probe": 2})).deleted_count == 1
    assert await handle.count_documents({}) == 0
