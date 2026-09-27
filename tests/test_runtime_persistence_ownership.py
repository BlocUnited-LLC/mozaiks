from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from mozaiksai.core.runtime.persistence.adapter import PersistencePrincipal, PersistenceScopeError
from mozaiksai.core.runtime.persistence.intent_loader import (
    DataContractLoadError,
    index_data_contract_by_entity,
)
from mozaiksai.core.runtime.persistence.mongo import (
    MongoPersistenceCollection,
    MongoPersistenceContext,
)
from mozaiksai.core.runtime.persistence.ownership import CollectionOwnership
from tests.test_runtime_persistence_mongo import FakeCursor, FakeMongoClient


class RecordingCollection:
    def __init__(self):
        self.calls = []

    def find(self, query, projection=None, **options):
        self.calls.append(("find", query, options))
        return FakeCursor([])

    async def find_one(self, query, projection=None, **options):
        self.calls.append(("find_one", query, options))
        return None

    async def insert_one(self, document):
        self.calls.append(("insert_one", document, {}))

    async def update_one(self, query, update, **options):
        self.calls.append(("update_one", query, update, options))

    async def delete_one(self, query, **options):
        self.calls.append(("delete_one", query, options))

    async def delete_many(self, query, **options):
        self.calls.append(("delete_many", query, options))

    async def count_documents(self, query, **options):
        self.calls.append(("count", query, options))
        return 0

    def aggregate(self, pipeline, **options):
        self.calls.append(("aggregate", pipeline, options))
        return FakeCursor([])


def owned_collection(tenancy="per_user"):
    raw = RecordingCollection()
    collection = MongoPersistenceCollection(
        collection=raw, app_id="app-a", user_id="untrusted-user", workspace_id="untrusted-workspace",
        ownership=CollectionOwnership(tenancy, "created_by"),
        principal=PersistencePrincipal("user-a", "workspace-a"),
    )
    return collection, raw


@pytest.mark.asyncio
@pytest.mark.parametrize("tenancy,identity", [("per_user", "user-a"), ("per_workspace", "workspace-a")])
async def test_every_query_preserves_principal_conjunction(tenancy, identity):
    collection, raw = owned_collection(tenancy)
    query = {"$or": [{"created_by": "victim"}, {"app_id": "another-app"}]}
    await collection.find_one(query)
    await collection.find_many(query)
    await collection.count(query)
    await collection.update_one(query, {"$set": {"title": "changed"}})
    await collection.delete_one(query)
    await collection.delete_many(query)
    assert [call[0] for call in raw.calls] == ["find_one", "find", "count", "update_one", "delete_one", "delete_many"]
    for call in raw.calls:
        assert call[1] == {"$and": [{
            "app_id": "app-a", "created_by": identity,
            "$nor": [{"app_id": {"$type": "array"}}, {"created_by": {"$type": "array"}}],
        }, query]}
        assert call[-1]["collation"] == {"locale": "simple"}
    assert query == {"$or": [{"created_by": "victim"}, {"app_id": "another-app"}]}


@pytest.mark.asyncio
@pytest.mark.parametrize("tenancy,identity", [("per_user", "user-a"), ("per_workspace", "workspace-a")])
async def test_insert_stamps_actual_declared_field_without_mutating_input(tenancy, identity):
    collection, raw = owned_collection(tenancy)
    document = {"title": "new", "owner_id": "old-model-field"}
    await collection.insert_one(document)
    assert raw.calls[0][1]["created_by"] == identity
    assert raw.calls[0][1]["app_id"] == "app-a"
    assert "created_by" not in document
    await collection.insert_one({"created_by": identity})
    with pytest.raises(PersistenceScopeError):
        await collection.insert_one({"created_by": "victim"})
    assert len(raw.calls) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("update", [
    {"$set": {"created_by": "victim"}},
    {"$unset": {"created_by": 1}},
    {"$rename": {"created_by": "previous_owner"}},
    {"$rename": {"other": "created_by"}},
    {"$rename": {"other": "created_by.child"}},
    {"$set": {"created_by.child": "victim"}},
    {"$inc": {"created_by": 1}},
    {"$setOnInsert": {"created_by": "victim"}},
    {"$set": {"app_id": "another-app"}},
    {"$unset": {"app_id": 1}},
    {"created_by": "victim"},
    [{"$replaceWith": {"created_by": "victim"}}],
    {"$futureOperator": {"title": "new"}},
])
async def test_owner_transfer_rejected_before_write(update):
    collection, raw = owned_collection()
    with pytest.raises(PersistenceScopeError):
        await collection.update_one({}, update, upsert=True)
    assert raw.calls == []


@pytest.mark.asyncio
async def test_upsert_stamps_owner_and_app_and_does_not_mutate_update():
    collection, raw = owned_collection()
    update = {"$set": {"title": "new"}, "$setOnInsert": {"created": "today"}}
    await collection.update_one({"task_id": "new"}, update, upsert=True)
    assert raw.calls[0][2] == {
        "$set": {"title": "new"},
        "$setOnInsert": {"created": "today", "app_id": "app-a", "created_by": "user-a"},
    }
    assert update == {"$set": {"title": "new"}, "$setOnInsert": {"created": "today"}}


@pytest.mark.asyncio
async def test_upsert_with_matching_owner_does_not_set_field_twice():
    collection, raw = owned_collection()
    await collection.update_one({}, {"$set": {"created_by": "user-a"}}, upsert=True)
    assert raw.calls[0][2] == {"$set": {"created_by": "user-a"}, "$setOnInsert": {"app_id": "app-a"}}


@pytest.mark.asyncio
async def test_module_cannot_create_collection_wide_ttl_deletion():
    collection, raw = owned_collection()
    with pytest.raises(PersistenceScopeError):
        await collection.ensure_indexes([{"keys": [["created_at", 1]], "name": "expire_everyone", "expireAfterSeconds": 0}])
    assert raw.calls == []


@pytest.mark.parametrize("tenancy,principal", [
    ("per_user", None), ("per_user", PersistencePrincipal("")),
    ("per_workspace", None), ("per_workspace", PersistencePrincipal("user-a")),
    ("per_workspace", PersistencePrincipal("user-a", " ")),
])
def test_metadata_cannot_substitute_for_authenticated_identity(tenancy, principal):
    with pytest.raises(PersistenceScopeError):
        MongoPersistenceCollection(
            collection=RecordingCollection(), app_id="app-a", user_id="user-a", workspace_id="workspace-a",
            ownership=CollectionOwnership(tenancy, "created_by"), principal=principal,
        )


@pytest.mark.asyncio
async def test_aggregate_only_counts_principals_initial_rows():
    collection, raw = owned_collection()
    pipeline = [{"$facet": {"counts": [{"$group": {"_id": "$status", "total": {"$sum": 1}}}]}}]
    await collection.aggregate(pipeline)
    assert raw.calls[0][1] == [{"$match": {
        "app_id": "app-a", "created_by": "user-a",
        "$nor": [{"app_id": {"$type": "array"}}, {"created_by": {"$type": "array"}}],
    }}, *pipeline]
    assert len(pipeline) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["$lookup", "$unionWith", "$graphLookup", "$out", "$merge", "$search", "$collStats", "$future"])
@pytest.mark.parametrize("nested", [False, True])
async def test_aggregate_rejects_sources_sinks_and_unknown_stages(stage, nested):
    collection, raw = owned_collection()
    pipeline = [{stage: "victim_collection"}]
    if nested:
        pipeline = [{"$facet": {"leak": pipeline}}]
    with pytest.raises(PersistenceScopeError):
        await collection.aggregate(pipeline)
    assert raw.calls == []


def contract(tenancy="per_user", **extra):
    return {"version": "1", "surfaces": [{
        "surface_id": "tasks", "surface_kind": "module", "collections": [{
            "name": "tasks", "entity": "Task", "tenancy": tenancy,
            "owner_field": "created_by" if tenancy != "app_wide" else None,
            **extra,
        }],
    }]}


def test_runtime_applies_partial_generation_metadata_and_normalized_storage_names():
    context = MongoPersistenceContext(
        app_id="app-a", client=FakeMongoClient(), data_contract=contract(), principal=PersistencePrincipal("user-a"),
    )
    # No fields/scope metadata required for runtime; casing cannot escape storage ownership.
    assert context.collection("TASKS", "TASKS")._owner_scope == {"created_by": "user-a"}
    with pytest.raises(PersistenceScopeError):
        context.literal_collection(context.collection_name("tasks", "tasks"))


@pytest.mark.asyncio
async def test_shared_collection_cannot_aggregate_foreign_owned_rows():
    context = MongoPersistenceContext(app_id="app-a", client=FakeMongoClient(), data_contract=contract())
    with pytest.raises(PersistenceScopeError):
        await context.collection("shared", "summary").aggregate([{"$lookup": {"from": "tasks"}}])


@pytest.mark.parametrize("data_contract", [None, contract("app_wide"), {"version": "1", "surfaces": [{
    "surface_id": "tasks", "surface_kind": "module", "collections": [{"name": "tasks"}],
}]}])
def test_ownerless_and_appwide_preserve_existing_raw_access(data_contract):
    client = FakeMongoClient()
    context = MongoPersistenceContext(app_id="app-a", client=client, data_contract=data_contract)
    assert context.collection("tasks", "tasks")._owner_scope == {}
    assert context.literal_collection("legacy") is client[context.database_name]["legacy"]


def test_principal_is_readonly_and_copied_scope_cannot_be_changed_by_context_metadata():
    context = MongoPersistenceContext(
        app_id="app-a", client=FakeMongoClient(), data_contract=contract(), principal=PersistencePrincipal("user-a"),
    )
    with pytest.raises(FrozenInstanceError):
        context.principal.user_id = "victim"
    with pytest.raises(AttributeError):
        context.principal = PersistencePrincipal("victim")
    context._scope_metadata["user_id"] = "victim"
    assert context.collection("tasks", "tasks")._owner_scope == {"created_by": "user-a"}


@pytest.mark.parametrize("legacy_first", [False, True])
def test_legacy_metadata_cannot_shadow_protected_collection(legacy_first):
    data_contract = contract()
    rows = data_contract["surfaces"][0]["collections"]
    rows.insert(0 if legacy_first else 1, {"name": "other_tasks", "entity_name": "Task"})
    with pytest.raises(DataContractLoadError, match="cannot replace ownership metadata"):
        index_data_contract_by_entity(data_contract)


def test_colliding_physical_names_cannot_change_ownership():
    data_contract = contract()
    data_contract["surfaces"][0]["collections"].append({"name": "TASKS", "entity_name": "other"})
    with pytest.raises(DataContractLoadError, match="Conflicting ownership"):
        MongoPersistenceContext(app_id="app-a", client=FakeMongoClient(), data_contract=data_contract)


def test_shared_scoped_rows_require_a_resolved_surface_owner():
    data_contract = {"version": "1", "surfaces": [], "shared_collections": [{
        "name": "tasks", "tenancy": "per_user", "owner_field": "created_by", "mongo_collection": "tasks",
    }]}
    with pytest.raises(DataContractLoadError, match="ownership is required"):
        index_data_contract_by_entity(data_contract)
