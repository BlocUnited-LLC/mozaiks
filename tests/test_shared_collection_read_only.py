"""Explicit reader grants restrict both injected persistence surfaces before I/O."""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest

from mozaiksai.core.runtime.composition.module_executor import ModuleExecutor, ModuleRequest
from mozaiksai.core.runtime.persistence import MongoPersistenceContext, PersistenceScopeError
from mozaiksai.core.runtime.persistence.adapter import PersistencePrincipal
from mozaiksai.core.runtime.persistence.app_data import app_data_from_context
from mozaiksai.core.runtime.persistence.intent_loader import (
    DataContractLoadError,
    load_data_contract,
)
from mozaiksai.core.runtime.persistence.platform_modules import PlatformModuleDeclarations
from tests.module_authority_test_helpers import trusted_framework_authority


class Cursor:
    def sort(self, *_args):
        return self

    def limit(self, *_args):
        return self

    async def to_list(self, length=None):
        return [{"ok": True}]


class Driver:
    def __init__(self):
        self.calls = []

    def find(self, *args, **kwargs):
        self.calls.append(("find", args, kwargs))
        return Cursor()

    def aggregate(self, *args, **kwargs):
        self.calls.append(("aggregate", args, kwargs))
        return Cursor()

    def __getattr__(self, method):
        async def operation(*args, **kwargs):
            self.calls.append((method, args, kwargs))
            return 1 if method == "count_documents" else {"ok": True}
        return operation


class Client:
    def __init__(self):
        self.driver = Driver()
        self.lookups = []

    def __getitem__(self, name):
        self.lookups.append(name)
        return Database(self)


class Database:
    def __init__(self, client):
        self.client = client

    def __getitem__(self, name):
        self.client.lookups.append(name)
        return self.client.driver


def contract(access="read_only", *, full=False):
    row = {
        "name": "records", "entity": "Record", "scope": "app", "tenancy": "app_wide", "owner_field": None,
        "ownership": {"surface_id": "writer", "surface_kind": "module"}, "mongo_collection": "shared_records",
    }
    reference = {
        "owner_module": "writer", "name": "records", "mongo_collection": "shared_records", "primary_alias": "shared.records",
        "read_by": [{"module": "reader", "access": access}],
    }
    return {
        "version": "1",
        "surfaces": [] if full else [{"surface_id": "writer", "surface_kind": "module", "collections": [row]}],
        "shared_collections": [{**row, **reference} if full else reference],
        "aliases": [{"alias": "alternate.records", "collection": "shared_records", "owner_module": "writer"}],
    }


def context(*, module="reader", mounted=False, value=None, principal=None):
    value = contract() if value is None else value
    client = Client()
    platform = PlatformModuleDeclarations(value, ["writer", "reader", "outsider"]) if mounted else None
    ctx = MongoPersistenceContext(
        app_id="app-a", module_id=module, client=client,
        data_contract=None if mounted else value, platform_modules=platform, principal=principal,
    )
    return ctx, client


def handle(ctx, kind, value=None):
    if kind == "logical":
        return ctx.collection("writer", "Record")
    return app_data_from_context(SimpleNamespace(persistence=ctx), contract=value or contract()).collection(
        "alternate.records",
    )


LOGICAL_WRITES = [
    ("insert_one", ({"x": 1},), {}),
    ("update_one", ({}, {"$set": {"x": 1}}), {"upsert": True}),
    ("delete_one", ({},), {}), ("delete_many", ({},), {}),
    ("ensure_indexes", ([{"keys": [("x", 1)]}],), {}),
]
ALIAS_WRITES = [
    *LOGICAL_WRITES[:-1],
    ("insert_many", ([{"x": 1}],), {}),
    ("update_many", ({}, {"$set": {"x": 1}}), {"upsert": True}),
    ("replace_one", ({}, {"x": 1}), {"upsert": True}),
    ("find_one_and_update", ({}, {"$set": {"x": 1}}), {"upsert": True}),
    ("find_one_and_replace", ({}, {"x": 1}), {"upsert": True}),
    ("find_one_and_delete", ({},), {}),
]


@pytest.mark.parametrize("mounted", [False, True])
@pytest.mark.parametrize("kind,operation", [*(('logical', row) for row in LOGICAL_WRITES),
                                          *(('alias', row) for row in ALIAS_WRITES)])
async def test_reader_mutations_never_reach_driver(mounted, kind, operation):
    ctx, client = context(mounted=mounted)
    collection = handle(ctx, kind)
    method, args, kwargs = operation
    with pytest.raises(PersistenceScopeError, match="read_only"):
        await getattr(collection, method)(*args, **kwargs)
    assert client.driver.calls == []


@pytest.mark.parametrize("kind", ["logical", "alias"])
@pytest.mark.parametrize("stage", [
    {"$out": "other"}, {"$merge": "other"}, {"$lookup": {"from": "other"}},
    {"$facet": {"nested": [{"$merge": "other"}]}}, {"$unknown": {}},
])
async def test_read_only_aggregation_keeps_existing_pipeline_fence(kind, stage):
    ctx, client = context()
    collection = handle(ctx, kind)
    with pytest.raises(PersistenceScopeError):
        if kind == "logical":
            await collection.aggregate([stage])
        else:
            collection.aggregate([stage])
    assert client.driver.calls == []


@pytest.mark.parametrize("kind", ["logical", "alias"])
@pytest.mark.parametrize("mounted", [False, True])
async def test_read_only_reads_and_safe_aggregation_work(kind, mounted):
    ctx, client = context(mounted=mounted)
    collection = handle(ctx, kind)
    assert await collection.find_one({}) == {"ok": True}
    if kind == "logical":
        assert await collection.count({}) == 1
        assert await collection.find_many({}) == [{"ok": True}]
        assert await collection.aggregate([{"$match": {"x": 1}}]) == [{"ok": True}]
        assert client.driver.calls[0][1][0] == {"app_id": "app-a"}
    else:
        assert await collection.count_documents({}) == 1
        assert await collection.find({}).to_list() == [{"ok": True}]
        assert await collection.aggregate([{"$match": {"x": 1}}]).to_list() == [{"ok": True}]


@pytest.mark.parametrize("kind", ["logical", "alias"])
@pytest.mark.parametrize("mounted", [False, True])
async def test_owner_keeps_writes(kind, mounted):
    ctx, client = context(module="writer", mounted=mounted)
    await handle(ctx, kind).insert_one({"x": 1})
    assert client.driver.calls[0][0] == "insert_one"


@pytest.mark.parametrize("access", [None, "absent"])
@pytest.mark.parametrize("kind", ["logical", "alias"])
@pytest.mark.parametrize("grant_field", ["read_by", "shared_with"])
@pytest.mark.parametrize("mounted", [False, True])
async def test_unmarked_grants_retain_existing_operations(access, kind, grant_field, mounted):
    value = contract(access)
    entry = value["shared_collections"][0]
    entry[grant_field] = entry.pop("read_by")
    if access == "absent":
        entry[grant_field][0].pop("access")
    ctx, client = context(value=value, mounted=mounted)
    await handle(ctx, kind, value).insert_one({"x": 1})
    assert client.driver.calls[0][0] == "insert_one"


@pytest.mark.parametrize("module", [None, ""])
@pytest.mark.parametrize("kind", ["logical", "alias"])
def test_missing_module_cannot_open_restricted_target(module, kind):
    ctx, client = context(module=module)
    with pytest.raises(PersistenceScopeError, match="module identity"):
        handle(ctx, kind)
    assert client.lookups == []


@pytest.mark.parametrize("kind", ["logical", "alias"])
async def test_full_shared_definition_is_also_read_only(kind):
    value = contract(full=True)
    ctx, client = context(value=value)
    with pytest.raises(PersistenceScopeError, match="read_only"):
        await handle(ctx, kind, value).insert_one({})
    assert client.driver.calls == []


@pytest.mark.parametrize("full", [False, True])
@pytest.mark.parametrize("grant_field", ["read_by", "shared_with"])
@pytest.mark.parametrize("access", ["read_olny", "read_write", "read", "support_thread_lifecycle", "", True])
def test_unknown_access_fails_load_and_direct_context_before_driver(tmp_path, full, grant_field, access):
    value = contract(access, full=full)
    entry = value["shared_collections"][0]
    entry[grant_field] = entry.pop("read_by")
    (tmp_path / "data").mkdir()
    (tmp_path / "data/contract.json").write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(DataContractLoadError, match="access must be read_only"):
        load_data_contract(tmp_path)
    client = Client()
    with pytest.raises(DataContractLoadError, match="access must be read_only"):
        MongoPersistenceContext(app_id="app-a", module_id="reader", client=client, data_contract=value)
    assert client.lookups == client.driver.calls == []


@pytest.mark.parametrize("full", [False, True])
@pytest.mark.parametrize("grant_field", ["read_by", "shared_with"])
@pytest.mark.parametrize("declarations", [
    {"module": "reader", "access": "read_only"}, "reader", True,
    [None], ["reader"], [{}], [{"module": None}], [{"module": 3}], [{"module": ""}], [{"module": " "}],
])
def test_malformed_grants_fail_load_and_direct_context_before_driver(tmp_path, full, grant_field, declarations):
    value = contract(full=full)
    entry = value["shared_collections"][0]
    entry.pop("read_by")
    entry[grant_field] = declarations
    (tmp_path / "data").mkdir()
    (tmp_path / "data/contract.json").write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(DataContractLoadError):
        load_data_contract(tmp_path)
    client = Client()
    with pytest.raises(DataContractLoadError):
        MongoPersistenceContext(app_id="app-a", module_id="reader", client=client, data_contract=value)
    assert client.lookups == client.driver.calls == []


@pytest.mark.parametrize("grant_field", ["read_by", "shared_with"])
@pytest.mark.parametrize("declarations", [None, []])
async def test_absent_or_empty_grant_list_keeps_legacy_runtime_behavior(grant_field, declarations):
    value = contract()
    entry = value["shared_collections"][0]
    entry.pop("read_by")
    entry[grant_field] = declarations
    ctx, client = context(value=value)
    await handle(ctx, "logical").insert_one({})
    assert client.driver.calls[0][0] == "insert_one"


def test_mounted_namespace_fences_remain():
    ctx, client = context(module="outsider", mounted=True)
    with pytest.raises(PersistenceScopeError):
        handle(ctx, "logical")
    with pytest.raises(PersistenceScopeError):
        handle(ctx, "alias")
    assert client.lookups == []


def test_literal_only_share_does_not_add_a_logical_grant():
    value = contract()
    value["shared_collections"][0].pop("name")
    ctx, client = context(value=value, mounted=True)
    with pytest.raises(PersistenceScopeError, match="Undeclared collection"):
        handle(ctx, "logical")
    assert client.lookups == []


@pytest.mark.parametrize("kind", ["logical", "alias"])
async def test_ordinary_module_dispatch_receives_read_only_identity(kind):
    client = Client()
    seen = []

    async def attempt(ctx):
        seen.append(ctx.module_id)
        with pytest.raises(PersistenceScopeError, match="read_only"):
            await handle(ctx.persistence, kind).update_one({}, {"$set": {"x": 1}})
        return {"blocked": True}

    executor = ModuleExecutor(data_contract=contract(), persistence_client=client)
    executor.register("reader", SimpleNamespace(attempt=attempt), action_method_map={"attempt": "attempt"})
    result = await executor.execute(ModuleRequest(
        module="reader", action="attempt", app_id="app-a", authority=trusted_framework_authority(),
    ))
    assert result.success
    assert seen == ["reader"]
    assert client.driver.calls == []


async def test_reaction_identity_is_already_bound_and_cannot_write():
    from tests.test_module_reaction_persistence import event, router

    value = contract()
    value["shared_collections"][0]["read_by"][0]["module"] = "preferences"
    client = Client()
    seen = []

    async def on_event(ctx, **_payload):
        seen.append(ctx.module_id)
        with pytest.raises(PersistenceScopeError, match="read_only"):
            await handle(ctx.persistence, "alias", value).insert_one({})

    runtime = router(SimpleNamespace(on_event=on_event), data_contract=value, persistence_client=client)
    result = await runtime.handle_event("domain.preferences.requested", event())
    assert result.success
    assert seen == ["preferences"]
    assert client.driver.calls == []


@pytest.mark.parametrize("mounted", [False, True])
async def test_reader_preserves_authenticated_owner_scope(mounted):
    value = contract()
    row = value["surfaces"][0]["collections"][0]
    row.update(tenancy="per_user", owner_field="created_by")
    ctx, client = context(value=value, mounted=mounted, principal=PersistencePrincipal("alice"))
    collection = handle(ctx, "logical", value)
    await collection.find_one({"created_by": "bob"})
    query = client.driver.calls[0][1][0]
    assert query["$and"][0]["created_by"] == "alice"
    assert query["$and"][1] == {"created_by": "bob"}
    with pytest.raises(PersistenceScopeError, match="read_only"):
        await collection.insert_one({"created_by": "alice"})
    assert len(client.driver.calls) == 1
    with pytest.raises(PersistenceScopeError, match="owned collections"):
        handle(ctx, "alias", value)


@pytest.mark.parametrize("kind", ["logical", "alias"])
async def test_shared_with_read_only_uses_the_same_operation_fence(kind):
    value = contract()
    entry = value["shared_collections"][0]
    entry["shared_with"] = entry.pop("read_by")
    ctx, client = context(value=value)
    with pytest.raises(PersistenceScopeError, match="read_only"):
        await handle(ctx, kind, value).delete_one({})
    assert client.driver.calls == []


@pytest.mark.parametrize("selector", ["logical", "literal"])
def test_conflicting_physical_owners_fail_at_contract_load(tmp_path, selector):
    value = contract()
    duplicate = deepcopy(value["surfaces"][0])
    duplicate["surface_id"] = "Writer" if selector == "logical" else "another_writer"
    row = duplicate["collections"][0]
    row["ownership"]["surface_id"] = duplicate["surface_id"]
    if selector == "logical":
        row["mongo_collection"] = "different_literal"
    value["surfaces"].append(duplicate)
    (tmp_path / "data").mkdir()
    (tmp_path / "data/contract.json").write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(DataContractLoadError, match="conflicting physical collection owners"):
        load_data_contract(tmp_path)


async def test_factory_support_purpose_labels_preserve_existing_writes():
    root = Path(__file__).resolve().parents[1] / "factory_app/app"
    value = load_data_contract(root)
    client = Client()
    ctx = MongoPersistenceContext(
        app_id="app-a", module_id="workspace_support", client=client,
        platform_modules=PlatformModuleDeclarations(value, ["messages", "workspace_support"]),
    )
    assert len(value["shared_collections"]) == 3
    for entry in value["shared_collections"]:
        grant = entry["shared_with"][0]
        assert "access" not in grant
        assert grant["purpose"].startswith("support_thread_lifecycle:")
        await ctx.collection("messages", entry["name"]).insert_one({})
    assert [call[0] for call in client.driver.calls] == ["insert_one"] * 3
