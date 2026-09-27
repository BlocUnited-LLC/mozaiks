"""Execute shipped alias-based modules beside owner-scoped app collections."""
from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import shutil
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from motor.motor_asyncio import AsyncIOMotorClient
from pymongo import MongoClient, ReturnDocument

from mozaiksai.core.runtime.app.entitlements import ConfiguredEntitlementAdapter
from mozaiksai.core.runtime.app.loader import AppLoader
from mozaiksai.core.runtime.app.subscriptions_loader import SubscriptionsConfig
from mozaiksai.core.runtime.composition.module_executor import ModuleExecutor, ModuleRequest
from mozaiksai.core.runtime.persistence import (
    MongoPersistenceContext,
    PersistenceScopeError,
    app_data_from_context,
)
from mozaiksai.core.runtime.persistence.alias_collection import GuardedAliasCollection
from tests import test_runtime_owner_scoping_http as ownership_http
from tests.module_authority_test_helpers import trusted_framework_authority

http_runtime = ownership_http.http_runtime
_PACK = Path(__file__).resolve().parents[1] / "factory_app/build_context/entitlement_dispatch/templates"


class _AssignmentCollection:
    def __init__(self):
        self.rows = []

    async def find_one(self, query, projection=None):
        return next((deepcopy(row) for row in self.rows if all(row.get(k) == v for k, v in query.items())), None)

    async def update_one(self, query, update, *, upsert=False):
        row = next((row for row in self.rows if all(row.get(k) == v for k, v in query.items())), None)
        if row is None and upsert:
            row = dict(query)
            self.rows.append(row)
        if row is None:
            return SimpleNamespace(modified_count=0)
        changed = any(row.get(key) != value for key, value in update["$set"].items())
        row.update(deepcopy(update["$set"]))
        return SimpleNamespace(modified_count=int(changed))


class _AliasCursor:
    def __init__(self, collection):
        self.collection = collection
        self.rows = [{"rank": 2}, {"rank": 1}]
        self.offset = 0

    def sort(self, field, direction):
        self.rows.sort(key=lambda row: row[field], reverse=direction < 0)
        return self

    def limit(self, limit):
        self.rows = self.rows[:limit]
        return self

    def skip(self, count):
        self.rows = self.rows[count:]
        return self

    async def to_list(self, length=None):
        return self.rows[:length]

    async def __anext__(self):
        if self.offset == len(self.rows):
            raise StopAsyncIteration
        row = self.rows[self.offset]
        self.offset += 1
        return row


class _AliasSource:
    def __init__(self):
        self.database = object()
        self.pipelines = []

    def aggregate(self, pipeline):
        self.pipelines.append(pipeline)
        return _AliasCursor(self)

    def find(self, *args, **kwargs):
        return _AliasCursor(self)


def _mixed_contract(subscription_tenancy):
    migration = json.loads((_PACK / "data/migrations/001_entitlement_dispatch_collections.json").read_text())
    subscriptions = migration["surfaces"][0]["collections"][0]
    subscriptions.update({
        "name": "subscriptions", "scope": "app",
        "fields": [{"name": field, "type": "string"} for field in ("app_id", "user_id", "plan_id", "status")],
    })
    if subscription_tenancy is not None:
        subscriptions.update({
            "entity": "Subscription", "tenancy": subscription_tenancy,
            "owner_field": None if subscription_tenancy == "app_wide" else "user_id",
        })
    contract = ownership_http._contract("per_user")
    contract["app_id"] = "mixed-ownership-app"
    contract["surfaces"].append({
        "surface_id": "entitlement_dispatch", "surface_kind": "module", "collections": [subscriptions],
    })
    return contract


async def _template_executor(tmp_path, monkeypatch, client, subscription_tenancy="app_wide"):
    app_root = tmp_path / "app"
    shutil.copytree(_PACK / "modules", app_root / "modules")
    (app_root / "data").mkdir()
    contract = _mixed_contract(subscription_tenancy)
    (app_root / "app.json").write_text(json.dumps({"appName": "Mixed Ownership", "appId": contract["app_id"]}))
    (app_root / "data/contract.json").write_text(json.dumps(contract))
    monkeypatch.setenv("PLATFORM_PATH", str(app_root))
    monkeypatch.setattr("mozaiksai.core.runtime.persistence.mongo.get_mongo_client", lambda: client)
    monkeypatch.setattr(ModuleExecutor, "_emit_dispatch_audit", AsyncMock())
    loaded = await AppLoader.load(str(app_root))
    assert loaded.failed_module_names == []
    module = next(module for module in loaded.modules if module.name == "entitlement_dispatch")
    executor = ModuleExecutor(data_contract=loaded.data_contract)
    executor.register(
        module.name, module.handler, action_method_map=module.action_method_map,
        action_permissions=module.action_permissions_map, action_schemas=module.action_schemas_map,
    )
    return executor


async def _dispatch(executor, action, **params):
    # Billing fulfillment is a framework-owned action. It has no row owner.
    return await executor.execute(ModuleRequest(
        module="entitlement_dispatch", action=action, app_id="mixed-ownership-app",
        user_id="billing-worker", params=params,
        authority=trusted_framework_authority("verified billing fulfillment"),
    ))


def _configured_entitlements(client, subscription_tenancy="app_wide"):
    context = MongoPersistenceContext(
        app_id="mixed-ownership-app", data_contract=_mixed_contract(subscription_tenancy), client=client,
    )
    resolver = app_data_from_context(SimpleNamespace(persistence=context)).collection
    config = SubscriptionsConfig.model_validate({
        "schema_version": "mozaiks.subscriptions.v1", "label": "Compatibility", "default_plan_id": "free",
        "assignment_store": {"data_alias": "billing.subscriptions", "active_statuses": ["active"]},
        "plans": [
            {"plan_id": "free", "label": "Free", "capabilities": []},
            {"plan_id": "pro", "label": "Pro", "capabilities": ["reports.export"]},
        ],
    })
    return ConfiguredEntitlementAdapter(config=config, collection_resolver=resolver)


async def _assignment_round_trip(executor, collection, entitlements):
    before = await entitlements.check("reports.export", app_id="mixed-ownership-app", user_id="recipient-a")
    assert (before.granted, before.reason) == (False, "no_grant")
    activated = await _dispatch(
        executor, "activate_subscription", user_id="recipient-a", plan_id="pro",
        metadata=[{"key": "source", "value": "verified-payment"}],
    )
    assert activated.success is True, activated
    assert activated.data == {"activated": True, "plan_id": "pro"}
    row = await collection.find_one({"app_id": "mixed-ownership-app", "user_id": "recipient-a"})
    assert row is not None
    assert row["status"] == "active"
    assert row["plan_id"] == "pro"
    assert row["metadata"] == {"source": "verified-payment"}
    active = await entitlements.check("reports.export", app_id="mixed-ownership-app", user_id="recipient-a")
    assert (active.granted, active.reason) == (True, "active_subscription")
    deactivated = await _dispatch(executor, "deactivate_subscription", user_id="recipient-a", plan_id="pro")
    assert deactivated.success is True, deactivated
    assert deactivated.data == {"deactivated": True}
    row = await collection.find_one({"app_id": "mixed-ownership-app", "user_id": "recipient-a"})
    assert row["status"] == "cancelled"
    assert row["deactivated_at"]
    cancelled = await entitlements.check("reports.export", app_id="mixed-ownership-app", user_id="recipient-a")
    assert (cancelled.granted, cancelled.reason) == (False, "inactive_subscription")


@pytest.mark.asyncio
@pytest.mark.parametrize("subscription_tenancy", ["app_wide", None])
async def test_real_entitlement_template_alias_operates_beside_owned_collection(
    tmp_path, monkeypatch, subscription_tenancy,
):
    database = defaultdict(_AssignmentCollection)
    client = defaultdict(lambda: database)
    executor = await _template_executor(tmp_path, monkeypatch, client, subscription_tenancy)
    await _assignment_round_trip(
        executor, database["entitlement_dispatch_subscriptions"], _configured_entitlements(client, subscription_tenancy),
    )


@pytest.mark.asyncio
async def test_real_entitlement_template_cannot_use_alias_for_owned_collection(tmp_path, monkeypatch):
    database = defaultdict(_AssignmentCollection)
    executor = await _template_executor(tmp_path, monkeypatch, defaultdict(lambda: database), "per_user")
    result = await _dispatch(executor, "activate_subscription", user_id="recipient-a", plan_id="pro")
    assert result.success is False
    assert result.error_code == "PERMISSION_DENIED"
    assert database["entitlement_dispatch_subscriptions"].rows == []


@pytest.mark.parametrize("stage", [
    {"$lookup": {"from": "owned_tasks", "localField": "user_id", "foreignField": "user_id", "as": "tasks"}},
    {"$unionWith": "owned_tasks"}, {"$out": "owned_tasks"},
    {"$facet": {"bypass": [{"$unionWith": "owned_tasks"}]}},
])
def test_mixed_app_alias_cannot_aggregate_other_owners(stage):
    raw = _AliasSource()
    context = MongoPersistenceContext(
        app_id="mixed-ownership-app", data_contract=_mixed_contract("app_wide"),
        client=defaultdict(lambda: defaultdict(lambda: raw)),
    )
    alias = context.literal_collection("entitlement_dispatch_subscriptions")
    with pytest.raises(PersistenceScopeError):
        alias.aggregate([stage])
    assert raw.pipelines == []


@pytest.mark.parametrize("data_alias", ["tasks.records", "disguised.shared_records"])
def test_different_alias_names_cannot_expose_declared_owned_collection(data_alias):
    contract = _mixed_contract("app_wide")
    context = MongoPersistenceContext(
        app_id="mixed-ownership-app", data_contract=contract,
        client=defaultdict(lambda: defaultdict(_AssignmentCollection)),
    )
    owned_name = context.collection_name("tasks", "tasks")
    aliases = {"aliases": [{"alias": data_alias, "collection": owned_name}]}
    app_data = app_data_from_context(SimpleNamespace(persistence=context), contract=aliases)
    with pytest.raises(PersistenceScopeError):
        app_data.collection(data_alias)


@pytest.mark.asyncio
async def test_existing_unowned_literal_alias_retains_same_collection_crud():
    database = defaultdict(_AssignmentCollection)
    context = MongoPersistenceContext(
        app_id="mixed-ownership-app", data_contract=_mixed_contract("app_wide"),
        client=defaultdict(lambda: database),
    )
    app_data = app_data_from_context(SimpleNamespace(persistence=context), contract={
        "aliases": [{"alias": "ownerless.assignments", "collection": "ownerless_assignments"}],
    })
    await app_data.collection("ownerless.assignments").update_one(
        {"app_id": "mixed-ownership-app", "user_id": "recipient"},
        {"$set": {"status": "active"}}, upsert=True,
    )
    assert database["ownerless_assignments"].rows == [{
        "app_id": "mixed-ownership-app", "user_id": "recipient", "status": "active",
    }]


@pytest.mark.asyncio
async def test_guarded_alias_preserves_cursor_operations_and_detaches_lazy_pipeline():
    raw = _AliasSource()
    alias = GuardedAliasCollection(raw)
    assert await alias.find({}, {"_id": 0}).sort("rank", 1).skip(1).limit(1).to_list(length=1) == [{"rank": 2}]
    assert [row async for row in alias.find({}).sort("rank", 1)] == [{"rank": 1}, {"rank": 2}]
    pipeline = [{"$facet": {"safe": [{"$match": {"rank": 1}}]}}]
    cursor = alias.aggregate(pipeline)
    pipeline[0]["$facet"]["safe"].append({"$unionWith": "owned_tasks"})
    pipeline.append({"$out": "owned_tasks"})
    await cursor.to_list()
    assert raw.pipelines == [[{"$facet": {"safe": [{"$match": {"rank": 1}}]}}]]


@pytest.mark.parametrize("attribute", ["database", "client", "rename", "with_options", "_collection"])
def test_guarded_alias_exposes_no_other_collection_handles(attribute):
    alias = GuardedAliasCollection(_AliasSource())
    with pytest.raises(AttributeError):
        getattr(alias, attribute)


@pytest.mark.parametrize("attribute", ["collection", "delegate", "clone", "_cursor"])
def test_guarded_alias_cursor_exposes_no_raw_collection(attribute):
    cursor = GuardedAliasCollection(_AliasSource()).find({})
    with pytest.raises(AttributeError):
        getattr(cursor, attribute)


@pytest.mark.asyncio
@pytest.mark.skipif(os.getenv("MOZAIKS_RUN_REAL_MONGO_TESTS") != "1", reason="requires opt-in local Mongo")
async def test_real_entitlement_template_alias_with_owned_sibling_on_real_mongo(tmp_path, monkeypatch):
    database_name = f"mozaiks_owner_entitlement_test_{uuid4().hex}"
    client = AsyncIOMotorClient(os.getenv("MONGO_URI", "mongodb://127.0.0.1:27017"), serverSelectionTimeoutMS=3000)
    monkeypatch.setenv("MOZAIKS_APP_DATABASE_NAME", database_name)
    try:
        executor = await _template_executor(tmp_path, monkeypatch, client)
        await _assignment_round_trip(
            executor, client[database_name]["entitlement_dispatch_subscriptions"], _configured_entitlements(client),
        )
        context = MongoPersistenceContext(
            app_id="mixed-ownership-app", data_contract=_mixed_contract("app_wide"),
            client=client, database_name=database_name,
        )
        alias = context.literal_collection("entitlement_dispatch_subscriptions")
        await alias.insert_many([{"probe": 1, "state": "new"}, {"probe": 2, "state": "new"}])
        assert await alias.count_documents({"state": "new"}) == 2
        assert (await alias.update_many({"state": "new"}, {"$set": {"state": "active"}})).modified_count == 2
        assert await alias.distinct("probe", {"state": "active"}) == [1, 2]
        updated = await alias.find_one_and_update({"probe": 1}, {"$set": {"state": "updated"}}, return_document=ReturnDocument.AFTER)
        assert updated["state"] == "updated"
        replaced = await alias.find_one_and_replace({"probe": 1}, {"probe": 1, "state": "replaced"}, return_document=ReturnDocument.AFTER)
        assert replaced["state"] == "replaced"
        assert (await alias.replace_one({"probe": 2}, {"probe": 2, "state": "replaced"})).modified_count == 1
        deleted = await alias.find_one_and_delete({"probe": 1})
        assert deleted["probe"] == 1
        assert (await alias.delete_one({"probe": 2})).deleted_count == 1
        assert (await alias.delete_many({"app_id": "mixed-ownership-app"})).deleted_count == 1
        assert await alias.count_documents({}) == 0
    finally:
        assert database_name.startswith("mozaiks_owner_entitlement_test_")
        await client.drop_database(database_name)
        client.close()


@pytest.mark.asyncio
@pytest.mark.skipif(os.getenv("MOZAIKS_RUN_REAL_MONGO_TESTS") != "1", reason="requires opt-in local Mongo")
async def test_real_commerce_product_repo_alias_with_owned_sibling_on_real_mongo(tmp_path, monkeypatch):
    pack = _PACK.parents[1] / "commerce/templates"
    migration = json.loads((pack / "data/migrations/001_commerce_collections.json").read_text())
    for surface in migration["surfaces"]:
        surface["surface_kind"] = "module"
        for collection in surface["collections"]:
            collection.update({"name": collection["data_alias"].split(".")[-1], "scope": "app", "fields": []})
    contract = _mixed_contract("app_wide")
    contract["surfaces"].extend(migration["surfaces"])
    app_root = tmp_path / "app"
    (app_root / "data").mkdir(parents=True)
    (app_root / "app.json").write_text(json.dumps({"appName": "Mixed Commerce", "appId": contract["app_id"]}))
    (app_root / "data/contract.json").write_text(json.dumps(contract))
    monkeypatch.setenv("PLATFORM_PATH", str(app_root))
    spec = importlib.util.spec_from_file_location("commerce_compatibility_repo", pack / "modules/commerce/backend/repo.py")
    assert spec is not None and spec.loader is not None
    loaded_repo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loaded_repo)
    repo = loaded_repo.ProductRepo()
    database_name = f"mozaiks_owner_commerce_test_{uuid4().hex}"
    client = AsyncIOMotorClient(os.getenv("MONGO_URI", "mongodb://127.0.0.1:27017"), serverSelectionTimeoutMS=3000)
    context = MongoPersistenceContext(
        app_id="mixed-ownership-app", data_contract=contract, client=client, database_name=database_name,
    )
    ctx = SimpleNamespace(persistence=context)
    try:
        assert isinstance(app_data_from_context(ctx).collection("commerce.products"), GuardedAliasCollection)
        for product_id in ("one", "two"):
            await repo.insert(ctx, doc={
                "product_id": product_id, "slug": product_id, "title": product_id,
                "status": "published", "updated_at": "2026-01-01" if product_id == "one" else "2026-01-02",
            })
        by_id = await repo.get(ctx, product_id="one")
        assert by_id["title"] == "one" and "_id" not in by_id
        assert await repo.get(ctx, slug="one") == by_id
        assert [row["product_id"] for row in await repo.list(ctx, query={"status": "published"}, limit=1)] == ["two"]
        assert [row["product_id"] for row in await repo.list(ctx, query={}, limit=2, before="2026-01-02")] == ["one"]
        updated = await repo.update(ctx, product_id="one", updates={"title": "Updated"})
        assert updated["title"] == "Updated"
        assert await client[database_name]["commerce_products"].count_documents({}) == 2
        with pytest.raises(PersistenceScopeError):
            context.literal_collection(context.collection_name("tasks", "tasks"))
    finally:
        assert database_name.startswith("mozaiks_owner_commerce_test_")
        await client.drop_database(database_name)
        client.close()


class _CachingHandler:
    """A singleton repo retaining the first collection handle it is given."""

    def __init__(self):
        self.cached = None
        self.arrivals = 0
        self.ready = asyncio.Event()

    def collection(self, ctx):
        if self.cached is None:
            self.cached = ctx.persistence.collection("tasks", "tasks")
        return self.cached

    async def create(self, ctx, *, task_id):
        await self.collection(ctx).insert_one({"task_id": task_id, "title": task_id})
        return {"created": task_id}

    async def list(self, ctx):
        return {"items": await self.collection(ctx).find_many({})}

    async def update(self, ctx, *, task_id):
        result = await self.collection(ctx).update_one({"task_id": task_id}, {"$set": {"title": "updated"}})
        return {"matched": result.matched_count}

    async def delete(self, ctx, *, task_id):
        result = await self.collection(ctx).delete_one({"task_id": task_id})
        return {"deleted": result.deleted_count}

    async def concurrent(self, ctx, *, task_id):
        collection = self.collection(ctx)
        self.arrivals += 1
        if self.arrivals == 2:
            self.ready.set()
        await asyncio.wait_for(self.ready.wait(), timeout=5)
        await collection.insert_one({"task_id": task_id, "title": task_id})
        await asyncio.sleep(0)
        return {"items": await collection.find_many({})}


def _caching_client(http_runtime, tenancy):
    client = http_runtime.client(tenancy)
    executor = client.app.state.executor_registry.module_executor
    handler = _CachingHandler()
    actions = {name: name for name in ("create", "list", "update", "delete", "concurrent")}
    executor.register("tasks", handler, action_method_map=actions)
    client.app.state.module_action_surfaces = {"tasks": dict.fromkeys(actions)}
    return client


@pytest.mark.parametrize("tenancy", ["per_user", "per_workspace"])
def test_cached_collection_rebinds_between_authenticated_http_requests(http_runtime, tenancy):
    user_a = http_runtime.token("user-a", "workspace-a")
    user_b = http_runtime.token("user-b", "workspace-b")
    owner_field = "created_by" if tenancy == "per_user" else "workspace_owner"
    with _caching_client(http_runtime, tenancy) as client:
        for headers, task_id in ((user_a, "a"), (user_b, "b")):
            created = client.post("/api/modules/tasks/create", headers=headers, json={"task_id": task_id})
            assert created.status_code == 200, created.text
        for headers, task_id in ((user_a, "a"), (user_b, "b")):
            listed = client.get("/api/modules/tasks/list", headers=headers)
            assert listed.status_code == 200, listed.text
            assert [item["task_id"] for item in listed.json()["items"]] == [task_id]
            identity = f"user-{task_id}" if tenancy == "per_user" else f"workspace-{task_id}"
            assert listed.json()["items"][0][owner_field] == identity
        assert client.post("/api/modules/tasks/update", headers=user_b, json={"task_id": "a"}).json() == {"matched": 0}
        assert client.post("/api/modules/tasks/delete", headers=user_b, json={"task_id": "a"}).json() == {"deleted": 0}
        assert client.post("/api/modules/tasks/update", headers=user_b, json={"task_id": "b"}).json() == {"matched": 1}
        assert client.post("/api/modules/tasks/delete", headers=user_b, json={"task_id": "b"}).json() == {"deleted": 1}
        assert [item["task_id"] for item in client.get("/api/modules/tasks/list", headers=user_a).json()["items"]] == ["a"]


@pytest.mark.parametrize("tenancy", ["per_user", "per_workspace"])
def test_cached_collection_keeps_concurrent_http_requests_isolated(http_runtime, tenancy):
    with _caching_client(http_runtime, tenancy) as client, ThreadPoolExecutor(max_workers=2) as pool:
        requests = {
            task_id: pool.submit(
                client.post, "/api/modules/tasks/concurrent",
                headers=http_runtime.token(f"user-{task_id}", f"workspace-{task_id}"),
                json={"task_id": task_id},
            )
            for task_id in ("a", "b")
        }
        for task_id, future in requests.items():
            response = future.result(timeout=10)
            assert response.status_code == 200, response.text
            assert [item["task_id"] for item in response.json()["items"]] == [task_id]


@pytest.mark.skipif(os.getenv("MOZAIKS_RUN_REAL_MONGO_TESTS") != "1", reason="requires opt-in local Mongo")
@pytest.mark.parametrize("tenancy", ["per_user", "per_workspace"])
@pytest.mark.parametrize("concurrent", [False, True])
def test_cached_http_collection_rebinds_with_real_mongo(http_runtime, monkeypatch, tenancy, concurrent):
    database_name = f"mozaiks_owner_cached_test_{uuid4().hex}"
    uri = os.getenv("MONGO_URI", "mongodb://127.0.0.1:27017")
    monkeypatch.setenv("MOZAIKS_APP_DATABASE_NAME", database_name)
    clients = []

    def mongo_client():
        if not clients:
            clients.append(AsyncIOMotorClient(uri, serverSelectionTimeoutMS=3000))
        return clients[0]

    monkeypatch.setattr("mozaiksai.core.runtime.persistence.mongo.get_mongo_client", mongo_client)
    try:
        if concurrent:
            test_cached_collection_keeps_concurrent_http_requests_isolated(http_runtime, tenancy)
        else:
            test_cached_collection_rebinds_between_authenticated_http_requests(http_runtime, tenancy)
    finally:
        for client in clients:
            client.close()
        assert database_name.startswith("mozaiks_owner_cached_test_")
        with MongoClient(uri, serverSelectionTimeoutMS=3000) as cleanup:
            cleanup.drop_database(database_name)
