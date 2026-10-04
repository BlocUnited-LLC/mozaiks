"""A bounded alias handle forwards only the arguments the driver defines for each method.

The alias handle returned by ``ctx.persistence.literal_collection(...)`` (and the
``app_data`` alias path) wraps a real driver collection. These tests pin its
contract: primary arguments bind positionally or by the driver's own keyword,
never both; every other keyword must be on the operation's allow-list, which
holds only options the installed driver defines; a read filter carries no raw
query command envelope; an aggregation pipeline is validated and sent as one
plain copy. Anything else fails closed with :class:`PersistenceScopeError`
before the driver is called.
"""

from __future__ import annotations

import inspect
import os
import re
import uuid
from collections.abc import Iterator, Mapping
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest
from bson import Binary, Decimal128, ObjectId, Regex
from bson.son import SON
from pymongo.collection import Collection
from pymongo.cursor import Cursor

from mozaiksai.core.runtime.persistence import (
    MongoPersistenceContext,
    PersistencePrincipal,
    PersistenceScopeError,
)
from mozaiksai.core.runtime.persistence.alias_collection import (
    _OPERATIONS,
    GuardedAliasCollection,
    GuardedAliasCursor,
)
from mozaiksai.core.runtime.persistence.mongo import MongoPersistenceCollection
from mozaiksai.core.runtime.persistence.ownership import canonical_pipeline, validate_owned_pipeline

# --------------------------------------------------------------------------- contract

# Each method's primary arguments, by the driver's own keyword, in the driver's
# positional order.
_PRIMARIES: dict[str, dict[str, Any]] = {
    "find": {"filter": {"state": "open"}, "projection": {"_id": 0}},
    "find_one": {"filter": {"state": "open"}, "projection": {"_id": 0}},
    "insert_one": {"document": {"state": "open"}},
    "insert_many": {"documents": [{"state": "open"}]},
    "update_one": {"filter": {"a": 1}, "update": {"$set": {"b": 2}}},
    "update_many": {"filter": {"a": 1}, "update": {"$set": {"b": 2}}},
    "replace_one": {"filter": {"a": 1}, "replacement": {"a": 2}},
    "delete_one": {"filter": {"a": 1}},
    "delete_many": {"filter": {"a": 1}},
    "count_documents": {"filter": {"a": 1}},
    "distinct": {"key": "field", "filter": {"a": 1}},
    "find_one_and_update": {"filter": {"a": 1}, "update": {"$set": {"b": 2}}},
    "find_one_and_replace": {"filter": {"a": 1}, "replacement": {"a": 2}},
    "find_one_and_delete": {"filter": {"a": 1}},
    "aggregate": {"pipeline": [{"$match": {"a": 1}}]},
}

_CURSOR = {
    "sort", "skip", "limit", "batch_size", "max_time_ms", "allow_disk_use", "no_cursor_timeout",
    "cursor_type", "allow_partial_results", "return_key", "show_record_id", "max", "min", "hint",
    "collation", "let", "session", "comment",
}
_FIND_AND_MODIFY = {"projection", "sort", "hint", "let", "collation", "maxTimeMS", "session", "comment"}

# The reviewed allow-list. Changing what a bounded handle forwards is a boundary
# change, so it must change here too.
_OPTIONS: dict[str, set[str]] = {
    "find": _CURSOR,
    "find_one": _CURSOR,
    "insert_one": {"bypass_document_validation", "session", "comment"},
    "insert_many": {"ordered", "bypass_document_validation", "session", "comment"},
    "update_one": {
        "upsert", "bypass_document_validation", "collation", "array_filters", "hint", "let", "sort",
        "session", "comment",
    },
    "update_many": {
        "upsert", "bypass_document_validation", "collation", "array_filters", "hint", "let",
        "session", "comment",
    },
    "replace_one": {
        "upsert", "bypass_document_validation", "collation", "hint", "let", "sort", "session", "comment",
    },
    "delete_one": {"collation", "hint", "let", "session", "comment"},
    "delete_many": {"collation", "hint", "let", "session", "comment"},
    "count_documents": {"skip", "limit", "maxTimeMS", "collation", "hint", "session", "comment"},
    "distinct": {"hint", "maxTimeMS", "collation", "session", "comment"},
    "find_one_and_update": _FIND_AND_MODIFY | {"upsert", "return_document", "array_filters"},
    "find_one_and_replace": _FIND_AND_MODIFY | {"upsert", "return_document"},
    "find_one_and_delete": _FIND_AND_MODIFY,
    "aggregate": {
        "allowDiskUse", "maxTimeMS", "batchSize", "collation", "bypassDocumentValidation", "let",
        "session", "comment",
    },
}

_METHODS = sorted(_PRIMARIES)
_OPTION_PAIRS = [(method, option) for method in _METHODS for option in sorted(_OPTIONS[method])]
_PRIMARY_PAIRS = [(method, primary) for method in _METHODS for primary in _PRIMARIES[method]]

# A field of the server command each operation builds; never a caller option.
_COMMAND_FIELD = {
    "find": "find", "find_one": "find", "insert_one": "insert", "insert_many": "insert",
    "update_one": "updates", "update_many": "updates", "replace_one": "updates",
    "delete_one": "deletes", "delete_many": "deletes", "count_documents": "pipeline",
    "distinct": "distinct", "find_one_and_update": "findAndModify",
    "find_one_and_replace": "findAndModify", "find_one_and_delete": "findAndModify",
    "aggregate": "aggregate",
}
_REFUSED_PAIRS = [
    (method, option)
    for method in _METHODS
    for option in ("undeclared_option", "$db", _COMMAND_FIELD[method])
]


def _driver_method(method: str) -> Any:
    # find and find_one hand every argument after the filter to the cursor.
    return Cursor.__init__ if method in {"find", "find_one"} else getattr(Collection, method)


def test_allow_lists_are_the_reviewed_contract():
    assert {method: (primaries, set(options)) for method, (primaries, options) in _OPERATIONS.items()} == {
        method: (tuple(_PRIMARIES[method]), _OPTIONS[method]) for method in _METHODS
    }


@pytest.mark.parametrize("method,option", _OPTION_PAIRS)
def test_every_allowed_option_is_defined_by_the_installed_driver(method, option):
    # A named parameter of the driver method, or a command option its own
    # documentation names for a method that takes command options.
    driver = _driver_method(method)
    documented = re.search(rf"\b{re.escape(option)}\b", inspect.getdoc(driver) or "")
    assert option in inspect.signature(driver).parameters or documented


@pytest.mark.parametrize("method,primary", _PRIMARY_PAIRS)
def test_every_primary_is_a_driver_parameter(method, primary):
    assert primary in inspect.signature(_driver_method(method)).parameters


# --------------------------------------------------------------------------- unit


class _RecordingCursor:
    def __init__(self, calls: list) -> None:
        self.calls = calls

    def sort(self, *args, **kwargs):
        self.calls.append(("sort", args, kwargs))
        return self

    def limit(self, limit):
        return self

    def skip(self, skip):
        return self

    async def to_list(self, length=None):
        return [{"ok": 1}]


class _RecordingCollection:
    """Records every forwarded driver call; a refused call must leave this empty."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple, dict]] = []

    def __getattr__(self, name):
        if name not in _OPERATIONS:
            raise AttributeError(name)

        def call(*args, **kwargs):
            self.calls.append((name, args, kwargs))
            if name in {"find", "aggregate"}:
                return _RecordingCursor(self.calls)

            async def result():
                return {"ok": 1}

            return result()

        return call


async def _invoke(handle: GuardedAliasCollection, method: str, *args, **kwargs):
    result = getattr(handle, method)(*args, **kwargs)
    if method in {"find", "aggregate"}:
        # Cursor-returning methods bind their call synchronously.
        assert isinstance(result, GuardedAliasCursor)
        return result
    return await result


def _positional(method: str) -> tuple:
    return tuple(_PRIMARIES[method].values())


class _Disguised(str):
    """A str whose content is one name while it compares and hashes as another."""

    def __new__(cls, content: str, poses_as: str) -> _Disguised:
        value = super().__new__(cls, content)
        value.poses_as = poses_as
        return value

    def __eq__(self, other):
        return other == self.poses_as

    def __hash__(self):
        return hash(self.poses_as)


class _Shifting(Mapping):
    """A mapping that answers with ``first`` until its items are read, then with ``later``."""

    def __init__(self, first: dict, later: dict) -> None:
        self.first, self.later, self.read = first, later, False

    def _view(self) -> dict:
        return self.later if self.read else self.first

    def items(self):
        view = self._view()
        self.read = True
        return view.items()

    def __getitem__(self, key):
        return self._view()[key]

    def __iter__(self) -> Iterator:
        return iter(self._view())

    def __len__(self) -> int:
        return len(self._view())


@pytest.mark.parametrize("method,option", _OPTION_PAIRS)
async def test_allowed_option_forwards_unchanged(method, option):
    raw = _RecordingCollection()
    value = object()
    await _invoke(GuardedAliasCollection(raw), method, *_positional(method), **{option: value})
    [(name, args, kwargs)] = raw.calls
    assert (name, args, kwargs) == (method, _positional(method), {option: value})
    assert kwargs[option] is value


@pytest.mark.parametrize("method,option", _REFUSED_PAIRS)
async def test_unlisted_option_fails_closed_without_calling_the_driver(method, option):
    raw = _RecordingCollection()
    with pytest.raises(PersistenceScopeError):
        await _invoke(GuardedAliasCollection(raw), method, *_positional(method), **{option: "other"})
    assert raw.calls == []


@pytest.mark.parametrize("method", _METHODS)
async def test_surplus_positional_fails_closed_without_calling_the_driver(method):
    raw = _RecordingCollection()
    with pytest.raises(PersistenceScopeError):
        await _invoke(GuardedAliasCollection(raw), method, *_positional(method), "surplus")
    assert raw.calls == []


@pytest.mark.parametrize("method", _METHODS)
async def test_primaries_bind_by_the_drivers_own_keyword(method):
    raw = _RecordingCollection()
    await _invoke(GuardedAliasCollection(raw), method, **_PRIMARIES[method])
    assert raw.calls == [(method, (), _PRIMARIES[method])]


@pytest.mark.parametrize("method,primary", _PRIMARY_PAIRS)
async def test_primary_given_positionally_and_by_keyword_is_refused(method, primary):
    raw = _RecordingCollection()
    position = list(_PRIMARIES[method]).index(primary)
    with pytest.raises(PersistenceScopeError):
        await _invoke(
            GuardedAliasCollection(raw), method,
            *_positional(method)[:position + 1], **{primary: _PRIMARIES[method][primary]},
        )
    assert raw.calls == []


async def test_disguised_keyword_is_judged_by_its_content():
    raw = _RecordingCollection()
    with pytest.raises(PersistenceScopeError):
        await GuardedAliasCollection(raw).count_documents(
            {}, **{_Disguised("undeclared_option", poses_as="comment"): "x"},
        )
    assert raw.calls == []


@pytest.mark.parametrize("method", ["find", "find_one"])
@pytest.mark.parametrize("passed", ["positionally", "by keyword"])
async def test_query_envelope_in_a_filter_is_refused_however_the_filter_is_passed(method, passed):
    raw = _RecordingCollection()
    envelope = {"$query": {}, "state": "open"}
    call = ((envelope,), {}) if passed == "positionally" else ((), {"filter": envelope})
    with pytest.raises(PersistenceScopeError):
        await _invoke(GuardedAliasCollection(raw), method, *call[0], **call[1])
    assert raw.calls == []


@pytest.mark.parametrize("method", ["find", "find_one"])
async def test_disguised_filter_key_is_judged_by_its_content(method):
    raw = _RecordingCollection()
    with pytest.raises(PersistenceScopeError):
        await _invoke(GuardedAliasCollection(raw), method, {_Disguised("$query", poses_as="state"): {}})
    assert raw.calls == []


@pytest.mark.parametrize("method", ["find", "find_one"])
@pytest.mark.parametrize("passed", ["positionally", "by keyword"])
async def test_filter_is_read_once_and_the_checked_copy_is_sent(method, passed):
    raw = _RecordingCollection()
    shifting = _Shifting({"state": "open"}, {"$query": {}, "state": "open"})
    call = ((shifting,), {}) if passed == "positionally" else ((), {"filter": shifting})
    await _invoke(GuardedAliasCollection(raw), method, *call[0], **call[1])
    [(_name, args, kwargs)] = raw.calls
    sent = args[0] if args else kwargs["filter"]
    assert type(sent) is dict and sent == {"state": "open"}


async def test_cursor_sort_forwards_only_the_drivers_arguments():
    raw = _RecordingCollection()
    cursor = GuardedAliasCollection(raw).find({})
    with pytest.raises(TypeError):
        cursor.sort("rank", 1, undeclared_option=1)
    cursor.sort("rank", -1)
    assert raw.calls[1:] == [("sort", ("rank", -1), {})]


async def test_aggregate_without_a_pipeline_is_refused():
    raw = _RecordingCollection()
    with pytest.raises(PersistenceScopeError):
        GuardedAliasCollection(raw).aggregate(allowDiskUse=True)
    assert raw.calls == []


@pytest.mark.parametrize("passed", ["positionally", "by keyword"])
async def test_aggregate_sends_the_stage_that_was_validated(passed):
    raw = _RecordingCollection()
    stage = _Shifting({"$match": {"a": 1}}, {"$unlisted_stage": {}})
    call = (([stage],), {}) if passed == "positionally" else ((), {"pipeline": [stage]})
    GuardedAliasCollection(raw).aggregate(*call[0], **call[1])
    [(_name, args, kwargs)] = raw.calls
    [sent] = args[0] if args else kwargs["pipeline"]
    assert type(sent) is dict and sent == {"$match": {"a": 1}}


async def test_aggregate_refuses_a_disguised_stage_operator():
    raw = _RecordingCollection()
    with pytest.raises(PersistenceScopeError):
        GuardedAliasCollection(raw).aggregate([{_Disguised("$unlisted_stage", poses_as="$match"): {}}])
    assert raw.calls == []


async def test_owned_collection_aggregate_sends_the_stage_that_was_validated():
    sent: list = []

    class _Raw:
        def aggregate(self, pipeline, **options):
            sent.extend(pipeline)
            return _RecordingCursor([])

    owned = MongoPersistenceCollection(collection=_Raw(), app_id="app-a", restrict_aggregation=True)
    await owned.aggregate([_Shifting({"$match": {"a": 1}}, {"$unlisted_stage": {}})])
    assert type(sent[1]) is dict and sent[1] == {"$match": {"a": 1}}
    with pytest.raises(PersistenceScopeError):
        await owned.aggregate([{_Disguised("$unlisted_stage", poses_as="$match"): {}}])


def test_canonical_pipeline_keeps_values_and_key_order():
    oid, when, amount = ObjectId(), datetime(2026, 1, 2, tzinfo=UTC), Decimal128("1.50")
    regex, pattern, blob = Regex("^a", "i"), re.compile("^b"), Binary(b"\x00\x01", 4)
    stage = SON([("$match", SON([
        ("z", oid), ("a", when), ("m", amount), ("r", regex), ("p", pattern), ("b", blob),
        ("in", (1, [2, (3,)])),
    ]))])
    [copy] = canonical_pipeline((stage,))

    assert type(copy) is dict and type(copy["$match"]) is dict
    assert list(copy["$match"]) == ["z", "a", "m", "r", "p", "b", "in"]
    for key, value in [("z", oid), ("a", when), ("m", amount), ("r", regex), ("p", pattern), ("b", blob)]:
        assert copy["$match"][key] is value
    assert copy["$match"]["in"] == [1, [2, [3]]]
    assert type(copy["$match"]["in"][1][1]) is list
    validate_owned_pipeline([copy])


def test_canonical_pipeline_reads_nested_facets_once():
    nested = _Shifting({"$match": {"a": 1}}, {"$unlisted_stage": {}})
    [copy] = canonical_pipeline([{"$facet": {"only": (nested,)}}])
    validate_owned_pipeline([copy])
    assert copy == {"$facet": {"only": [{"$match": {"a": 1}}]}}
    assert type(copy["$facet"]["only"][0]) is dict


@pytest.mark.parametrize("pipeline", [["$match"], [[("$match", {})]], [None]])
def test_canonical_pipeline_refuses_a_stage_that_is_not_a_document(pipeline):
    with pytest.raises(PersistenceScopeError):
        canonical_pipeline(pipeline)


@pytest.mark.parametrize("stage", [{1: {}}, {"$match": {b"a": 1}}, {"$facet": {"x": [{None: {}}]}}])
def test_canonical_pipeline_refuses_a_key_that_is_not_a_string(stage):
    with pytest.raises(PersistenceScopeError):
        canonical_pipeline([stage])


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


def _context(mongo) -> MongoPersistenceContext:
    return MongoPersistenceContext(
        app_id="app-a", client=mongo.client, database_name=mongo.database,
        data_contract=_owned_contract(), principal=PersistencePrincipal("user-a", "ws-a"),
    )


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
    handle = _context(mongo).literal_collection("alias_store")
    assert isinstance(handle, GuardedAliasCollection)
    await _seed_markers(mongo)

    async def refused(coro):
        with pytest.raises(PersistenceScopeError):
            await coro

    await refused(handle.count_documents({}, pipeline=[{"$collStats": {}}]))
    await refused(handle.distinct("marker", distinct="foreign_store"))
    await refused(handle.find_one_and_update({}, {"$set": {"marker": "x"}}, findAndModify="foreign_store"))
    await refused(handle.find_one_and_replace({}, {"marker": "x"}, findAndModify="foreign_store"))
    await refused(handle.find_one_and_delete({}, findAndModify="foreign_store"))
    await refused(handle.find_one({"$query": {}, "find": "foreign_store"}))
    await refused(handle.find_one(filter={"$query": {}, "find": "foreign_store"}))
    with pytest.raises(PersistenceScopeError):
        handle.aggregate([{"$match": {}}], aggregate="foreign_store")
    with pytest.raises(PersistenceScopeError):
        handle.aggregate([{"$match": {}}], {"surplus": True})
    with pytest.raises(PersistenceScopeError):
        handle.find(filter={"$query": {}, "find": "foreign_store"})

    assert await _markers_intact(mongo)


async def test_bounded_alias_still_runs_driver_options_on_real_mongo(mongo):
    from pymongo import ReturnDocument

    handle = _context(mongo).literal_collection("alias_store")

    await handle.insert_many(documents=[{"probe": 1, "state": "new"}, {"probe": 2, "state": "new"}])
    assert await handle.count_documents({"state": "new"}, limit=5) == 2
    assert await handle.count_documents(filter={}) == 2
    result = await handle.update_many(filter={"state": "new"}, update={"$set": {"state": "active"}})
    assert result.modified_count == 2
    assert sorted(await handle.distinct(key="probe", filter={"state": "active"})) == [1, 2]
    updated = await handle.find_one_and_update(
        {"probe": 1}, {"$set": {"state": "updated"}}, return_document=ReturnDocument.AFTER,
    )
    assert updated["state"] == "updated"
    replaced = await handle.find_one_and_replace(
        filter={"probe": 1}, replacement={"probe": 1, "state": "replaced"},
        return_document=ReturnDocument.AFTER,
    )
    assert replaced["state"] == "replaced"
    assert (await handle.replace_one({"probe": 2}, {"probe": 2, "state": "done"}, upsert=False)).modified_count == 1
    rows = await handle.find({}, {"_id": 0}).sort("probe", 1).to_list(length=None)
    assert [row["probe"] for row in rows] == [1, 2]
    rows = await handle.find(filter={}, projection={"_id": 0}, sort=[("probe", -1)]).to_list(length=None)
    assert [row["probe"] for row in rows] == [2, 1]
    one = await handle.find_one(filter={"probe": 1}, projection={"_id": 0})
    assert one["state"] == "replaced"
    grouped = await handle.aggregate(
        pipeline=[{"$group": {"_id": "$state", "n": {"$sum": 1}}}], allowDiskUse=True,
    ).to_list(length=None)
    assert {row["_id"] for row in grouped} == {"replaced", "done"}
    assert (await handle.find_one_and_delete(filter={"probe": 1}))["probe"] == 1
    assert (await handle.delete_one(filter={"probe": 2})).deleted_count == 1
    assert await handle.count_documents({}) == 0


@pytest.mark.parametrize("surface", ["alias handle", "owned collection"])
async def test_server_runs_the_stage_that_was_validated_on_real_mongo(surface, mongo):
    ctx = _context(mongo)
    stage = _Shifting({"$project": {"_id": 0, "probe": 1}}, {"$unlisted_stage": {}})
    if surface == "alias handle":
        handle = ctx.literal_collection("alias_store")
        await handle.insert_one({"probe": 1})
        rows = await handle.aggregate([stage]).to_list(length=None)
    else:
        owned = ctx.collection("secrets", "vault")
        await owned.insert_one({"probe": 1})
        rows = await owned.aggregate([stage])
    assert rows == [{"probe": 1}]
