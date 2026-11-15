"""Bounded transaction view tests; a real replica-set proof is separate."""

from __future__ import annotations

import asyncio
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pymongo import ReadPreference
from pymongo.errors import OperationFailure

from mozaiksai.core.runtime.persistence import (
    AppData,
    AppDataAliasError,
    MongoPersistenceContext,
    PersistencePrincipal,
    PersistenceScopeError,
    PlatformModuleDeclarations,
    collection_name_for,
    run_app_data_transaction,
)


class Cursor:
    def __init__(self, rows):
        self.rows = rows
        self.position = 0

    def sort(self, *args):
        return self

    def limit(self, limit):
        self.rows = self.rows[:limit]
        return self

    def skip(self, skip):
        self.rows = self.rows[skip:]
        return self

    async def to_list(self, length=None):
        return self.rows[:length]

    async def __anext__(self):
        if self.position >= len(self.rows):
            raise StopAsyncIteration
        row = self.rows[self.position]
        self.position += 1
        return row


class Collection:
    def __init__(self, client, name):
        self.client, self.name = client, name

    def _rows(self, session):
        assert session is self.client.session
        assert session.open and session.pending is not None
        if self.client.unsupported:
            raise OperationFailure("Transaction numbers require a replica set", code=20)
        return session.pending.setdefault(self.name, [])

    async def insert_one(self, document, *, session, **options):
        self._rows(session).append(deepcopy(document))
        self.client.operations.append(("insert", self.name, session))
        return SimpleNamespace(inserted_id=document["_id"])

    async def find_one(self, query, projection=None, *, session, **options):
        return next((deepcopy(row) for row in self._rows(session) if all(row.get(k) == v for k, v in query.items())), None)

    def find(self, query=None, projection=None, *, session, **options):
        return Cursor(deepcopy(self._rows(session)))

    def aggregate(self, pipeline, *, session, **options):
        return Cursor(deepcopy(self._rows(session)))


class Session:
    """Driver protocol double: retries are requested here, never by the app API."""

    def __init__(self, client):
        self.client = client
        self.open = False
        self.pending = None
        self.aborts = 0
        self.commits = 0
        self.options = None
        self.views = []

    async def __aenter__(self):
        self.open = True
        return self

    async def __aexit__(self, *args):
        self.open = False
        self.pending = None

    async def with_transaction(self, callback, **options):
        self.options = options
        for attempt in range(2 if self.client.retry else 1):
            self.pending = deepcopy(self.client.rows)
            try:
                result = await callback(self)
            except BaseException:
                self.aborts += 1
                self.pending = None
                raise
            if self.client.retry and attempt == 0:
                self.aborts += 1
                continue
            self.client.rows = deepcopy(self.pending)
            self.commits += 1
            if self.client.uncertain_commit:
                raise OperationFailure(
                    "Commit acknowledgement unavailable", code=64,
                    details={"errorLabels": ["UnknownTransactionCommitResult"]},
                )
            return result


class Client:
    def __init__(self, *, retry=False, uncertain_commit=False, unsupported=False):
        self.retry = retry
        self.uncertain_commit = uncertain_commit
        self.unsupported = unsupported
        self.rows = {}
        self.operations = []
        self.session = Session(self)
        self.start_session = AsyncMock(return_value=self.session)

    def __getitem__(self, database):
        return SimpleDatabase(self)


class SimpleDatabase:
    def __init__(self, client):
        self.client = client

    def __getitem__(self, name):
        return Collection(self.client, name)


def context(client=None, *, contract=None, platform=None, module_id=None, aliases=None):
    client = client or Client()
    persistence = MongoPersistenceContext(
        app_id="app-a", client=client, data_contract=contract,
        principal=PersistencePrincipal("human-a"), platform_modules=platform, module_id=module_id,
    )
    ctx = SimpleNamespace(app_id="app-a", persistence=persistence)
    # A resolver-backed view, like the one cached by real app alias repositories.
    ctx.app_data = AppData(
        aliases=aliases or {"messages": "messages", "journal": "journal"},
        collection_resolver=persistence.literal_collection,
    )
    return ctx, client


async def write_pair(data):
    await data.collection("messages").insert_one({"_id": "message-one", "app_id": "app-a"})
    await data.collection("journal").insert_one({"_id": "event-one", "app_id": "app-a"})
    return "message-one"


async def test_two_aliases_commit_together_without_replacing_cached_view():
    ctx, client = context()
    original = ctx.app_data

    async def callback(data):
        assert data is not original and data.db is None
        assert dict(data.aliases) == dict(original.aliases)
        assert client.rows == {}
        result = await write_pair(data)
        assert client.rows == {}
        assert await data.collection("messages").find_one({"_id": result}) == {"_id": result, "app_id": "app-a"}
        return result

    assert await run_app_data_transaction(ctx, callback) == "message-one"
    assert set(client.rows) == {"messages", "journal"}
    assert ctx.app_data is original and ctx.persistence.app_id == "app-a"
    assert len({id(call[2]) for call in client.operations}) == 1
    assert not client.session.open
    options = client.session.options
    assert options["read_concern"].level == "snapshot"
    assert options["write_concern"].document == {"w": "majority"}
    assert options["read_preference"] is ReadPreference.PRIMARY
    assert options["max_commit_time_ms"] == 10_000


async def test_failure_aborts_and_closes_the_session():
    ctx, client = context()

    async def fail(data):
        await write_pair(data)
        raise ValueError("second operation refused")

    with pytest.raises(ValueError, match="second operation"):
        await run_app_data_transaction(ctx, fail)
    assert client.rows == {} and client.session.aborts == 1
    assert not client.session.open
    # A failed invocation does not poison the task-local nesting guard.
    assert await run_app_data_transaction(ctx, write_pair) == "message-one"


async def test_driver_retry_gets_a_fresh_view_and_expires_the_previous_attempt():
    ctx, client = context(Client(retry=True))
    previous = []

    async def callback(data):
        if previous:
            with pytest.raises(PersistenceScopeError, match="outside"):
                await previous[0].insert_one({"_id": "stale"})
        previous.append(data.collection("messages"))
        return await write_pair(data)

    assert await run_app_data_transaction(ctx, callback) == "message-one"
    assert len(previous) == 2
    assert client.session.aborts == client.session.commits == 1
    assert len(client.rows["messages"]) == len(client.rows["journal"]) == 1


async def test_unknown_commit_propagates_without_repeating_or_claiming_rollback():
    ctx, client = context(Client(uncertain_commit=True))
    callback = AsyncMock(side_effect=write_pair)
    with pytest.raises(OperationFailure) as failure:
        await run_app_data_transaction(ctx, callback)
    assert failure.value.has_error_label("UnknownTransactionCommitResult")
    callback.assert_awaited_once()
    assert client.session.commits == 1 and not client.session.open
    assert client.rows["journal"][0]["_id"] == "event-one"


async def test_unsupported_topology_never_falls_back_to_separate_writes():
    ctx, client = context(Client(unsupported=True))
    with pytest.raises(OperationFailure) as failure:
        await run_app_data_transaction(ctx, write_pair)
    assert failure.value.code == 20
    assert client.rows == {} and client.session.commits == 0
    assert client.session.aborts == 1 and not client.session.open


@pytest.mark.parametrize("session_override", [None, object()])
async def test_session_override_is_refused_before_any_driver_operation(session_override):
    ctx, client = context()

    async def callback(data):
        with pytest.raises(PersistenceScopeError, match="runtime-owned"):
            await data.collection("messages").insert_one({"_id": "bad"}, session=session_override)
        with pytest.raises(PersistenceScopeError, match="runtime-owned"):
            data.collection("messages").find({}, session=session_override)

    await run_app_data_transaction(ctx, callback)
    assert client.operations == []


async def test_handles_methods_cursors_and_view_expire_after_callback():
    ctx, client = context()

    async def callback(data):
        await write_pair(data)
        collection = data.collection("messages")
        cursor = collection.find({}).sort("_id").skip(0).limit(2)
        assert await cursor.to_list(length=2) == client.session.pending["messages"]
        assert [row async for row in collection.aggregate([])] == client.session.pending["messages"]
        return data, collection, cursor, collection.insert_one

    data, collection, cursor, bound_write = await run_app_data_transaction(ctx, callback)
    for operation in (lambda: data.collection("messages"), lambda: collection.find({}), lambda: cursor.limit(1), lambda: cursor.__aiter__()):
        with pytest.raises(PersistenceScopeError, match="outside"):
            operation()
    for operation in (cursor.to_list, cursor.__anext__, lambda: bound_write({"_id": "later"})):
        with pytest.raises(PersistenceScopeError, match="outside"):
            await operation()
    assert len(client.rows["messages"]) == 1


async def test_nested_transaction_and_child_task_operations_are_refused():
    ctx, client = context()

    async def callback(data):
        with pytest.raises(PersistenceScopeError, match="Nested"):
            await run_app_data_transaction(ctx, write_pair)
        operation = data.collection("messages").insert_one
        with pytest.raises(PersistenceScopeError, match="sequentially"):
            await asyncio.create_task(operation({"_id": "parallel"}))

    await run_app_data_transaction(ctx, callback)
    client.start_session.assert_awaited_once()
    assert client.operations == []


async def test_guarded_namespace_and_pipeline_contracts_remain_in_force():
    ctx, client = context()

    async def callback(data):
        with pytest.raises(AppDataAliasError):
            data.collection("undeclared")
        collection = data.collection("messages")
        for name in ("database", "client", "with_options", "watch", "command"):
            with pytest.raises(AttributeError):
                getattr(collection, name)
        with pytest.raises(PersistenceScopeError):
            await collection.find_one({"$query": {}}, projection=None)
        with pytest.raises(PersistenceScopeError):
            collection.aggregate([{"$lookup": {"from": "secret"}}])
        with pytest.raises(PersistenceScopeError):
            await collection.insert_one({"_id": "bad"}, "unexpected positional")
        with pytest.raises(PersistenceScopeError):
            await collection.find_one({}, collection="secret")

    await run_app_data_transaction(ctx, callback)
    assert client.operations == []


async def test_owned_and_reserved_platform_collections_cannot_be_opened_as_aliases():
    contract = {"version": "1", "surfaces": [{
        "surface_id": "tasks", "surface_kind": "module", "collections": [{
            "name": "tasks", "entity": "Task", "tenancy": "per_user", "owner_field": "created_by",
        }],
    }]}
    owned_name = collection_name_for(app_id="app-a", module_id="tasks", entity_name="tasks")
    ctx, client = context(contract=contract, aliases={"owned": owned_name})

    async def callback(data):
        with pytest.raises(PersistenceScopeError, match="owned collections"):
            data.collection("owned")

    await run_app_data_transaction(ctx, callback)
    assert client.operations == []
    platform_contract = {"version": "1", "surfaces": [{
        "surface_id": "platform", "surface_kind": "module", "collections": [{
            "name": "secrets", "mongo_collection": "reserved-secrets", "data_alias": "secrets",
        }],
    }]}
    ctx, client = context(
        platform=PlatformModuleDeclarations(platform_contract, ["platform"]),
        module_id="other", aliases={"reserved": "reserved-secrets"},
    )

    async def forbidden(data):
        with pytest.raises(PersistenceScopeError, match="platform module"):
            data.collection("reserved")

    await run_app_data_transaction(ctx, forbidden)
    assert client.operations == []


async def test_invalid_context_or_app_identity_is_rejected_before_session_creation():
    with pytest.raises(PersistenceScopeError, match="runtime-injected"):
        await run_app_data_transaction(SimpleNamespace(db=object()), write_pair)
    ctx, client = context()
    ctx.app_id = "another-app"
    with pytest.raises(PersistenceScopeError, match="identity"):
        await run_app_data_transaction(ctx, write_pair)
    client.start_session.assert_not_awaited()


async def test_resolver_read_only_restrictions_are_not_unwrapped(monkeypatch):
    ctx, client = context()
    resolve = ctx.persistence.literal_collection

    class ReadOnly:
        def __init__(self, collection):
            self.collection = collection

        async def find_one(self, *args, **kwargs):
            return await self.collection.find_one(*args, **kwargs)

        async def insert_one(self, *args, **kwargs):
            raise PersistenceScopeError("resolver grants read access only")

    # Model a narrower resolver result: the transaction must delegate through it,
    # never reconstruct a raw collection from a name or use its private fields.
    monkeypatch.setattr(ctx.persistence, "literal_collection", lambda name: ReadOnly(resolve(name)))

    async def callback(data):
        assert await data.collection("messages").find_one({"_id": "absent"}) is None
        with pytest.raises(PersistenceScopeError, match="read access only"):
            await data.collection("messages").insert_one({"_id": "forbidden"})

    await run_app_data_transaction(ctx, callback)
    assert client.operations == []
