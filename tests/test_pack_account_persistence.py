"""Pack account handlers address the same declared storage as module actions."""
from __future__ import annotations

import importlib.util
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import httpx
import jwt
import pytest
import pytest_asyncio
from bson import ObjectId
from fastapi import FastAPI

from mozaiksai.core import core_config
from mozaiksai.core.account import AccountDataRegistry
from mozaiksai.core.runtime.composition.platform_hooks import PlatformHookRegistry
from mozaiksai.core.runtime.persistence import MongoPersistenceContext, PersistencePrincipal
from mozaiksai.hosts.routers import account as account_routes

ROOT = Path(__file__).resolve().parents[1]
MODULES = {
    "files": ("files", ("records",)),
    "commerce": ("commerce", ("carts", "orders", "checkout_requests", "products")),
    "friends": ("social", ("friendships", "requests")),
    "activity_feed": ("social", ("events",)),
    "notification_settings": ("notifications", ("preferences",)),
    "support": ("support", ("requests",)),
}


def handler_class(module):
    pack = MODULES[module][0]
    path = ROOT / f"factory_app/build_context/{pack}/templates/modules/{module}/backend/account_data_handler.py"
    spec = importlib.util.spec_from_file_location(f"pack_account_{module}", path)
    assert spec and spec.loader
    loaded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loaded)
    return loaded.AccountDataHandler


@pytest_asyncio.fixture
async def host(monkeypatch):
    uri = os.getenv("MONGO_URI")
    required = os.getenv("MOZAIKS_REQUIRE_REAL_MONGO") == "1"
    if not uri:
        if required:
            pytest.fail("MONGO_URI is required for pack account persistence acceptance")
        pytest.skip("Mongo is not configured")
    database_name = f"pack_account_test_{uuid4().hex}"
    monkeypatch.setenv("MOZAIKS_APP_DATABASE_NAME", database_name)
    for name in ("MOZAIKS_APP_DATA_DATABASE_NAME", "MOZAIKS_APPS_DATABASE"):
        monkeypatch.delenv(name, raising=False)
    for name, value in {"ENV": "test", "ENVIRONMENT": "test", "AUTH_ENABLED": "true",
                        "AUTH_PROVIDER": "supabase", "SUPABASE_URL": "http://127.0.0.1"}.items():
        monkeypatch.setenv(name, value)
    signing_key = uuid4().hex + uuid4().hex
    monkeypatch.setenv("SUPABASE_JWT_SECRET", signing_key)
    monkeypatch.setattr(core_config, "_mongo_client", None)
    monkeypatch.setattr(core_config, "_mongo_client_conn_str", None)
    client = core_config.get_mongo_client()
    try:
        await client.admin.command("ping", maxTimeMS=1500)
    except Exception:
        client.close()
        if required:
            pytest.fail("Mongo is required for pack account persistence acceptance")
        pytest.skip("Mongo is unavailable")
    contract = {"version": "1", "surfaces": [
        {"surface_id": module, "surface_kind": "module", "collections": [
            {"name": name, "scope": "app", "entity": name, "tenancy": "app_wide",
             "owner_field": None, "fields": [], "indexes": []}
            for name in names
        ]} for module, (_, names) in MODULES.items()
    ], "shared_collections": []}
    registry = AccountDataRegistry()
    monkeypatch.setattr(account_routes, "account_data_registry", registry)
    monkeypatch.setattr(PlatformHookRegistry, "_instance", PlatformHookRegistry())
    app = FastAPI()
    app.state.data_contract = contract
    app.include_router(account_routes.router)

    def persistence(app_id="app-one", user_id="alice", principal=True):
        return MongoPersistenceContext(
            app_id=app_id, user_id=user_id, database_name=database_name, client=client,
            data_contract=contract, principal=PersistencePrincipal(user_id=user_id) if principal else None,
        )

    def headers(app_id="app-one", user_id="alice"):
        now = datetime.now(UTC)
        token = jwt.encode({"sub": user_id, "aud": "authenticated", "role": "authenticated",
                            "iat": now, "exp": now + timedelta(minutes=5), "app_metadata": {"app_id": app_id}},
                           signing_key, algorithm="HS256")
        return {"Authorization": f"Bearer {token}"}

    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as http:
            yield SimpleNamespace(http=http, registry=registry, persistence=persistence,
                                  headers=headers, db=client[database_name])
    finally:
        assert database_name.startswith("pack_account_test_")
        await client.drop_database(database_name)
        client.close()


def row(module, index=0, user="alice"):
    common = {"record_id": f"{user}-{index:03d}", "created_at": datetime(2026, 1, 1, tzinfo=UTC),
              "metadata": {"external_id": ObjectId(), "observed": datetime(2026, 1, 2, tzinfo=UTC)}}
    if module == "files":
        return {**common, "created_by": user, "deleted": False}
    if module == "commerce":
        return {**common, "actor_id": user}
    if module == "friends":
        return {**common, "user_id": user, "friend_user_id": "carol", "requester_id": user, "recipient_id": "carol"}
    if module == "activity_feed":
        return {**common, "actor_id": user, "feed_user_ids": [user, "carol"]}
    if module == "notification_settings":
        return {**common, "user_id": user, "email_enabled": False, "push_enabled": True,
                "sms_enabled": False, "updated_at": datetime(2026, 1, 2, tzinfo=UTC)}
    return {**common, "requester_id": user}


@pytest.mark.parametrize("module", MODULES)
async def test_account_routes_use_pack_storage_and_preserve_other_users_and_apps(host, module):
    host.registry.register(module, handler_class(module))
    names = MODULES[module][1]
    owner = host.persistence()
    peer = host.persistence(user_id="bob")
    foreign = host.persistence(app_id="app-two")
    for name in names:
        count = 1 if name in {"carts", "preferences", "products"} else 105
        for index in range(count):
            await owner.collection(module, name).insert_one(row(module, index))
        await peer.collection(module, name).insert_one(row(module, user="bob"))
        await foreign.collection(module, name).insert_one(row(module))
    # A stale raw-name collection must not be mistaken for a declared binding.
    decoy = host.db[f"{module}.{names[0]}"]
    await decoy.insert_one({"app_id": "app-one", "user_id": "alice", "sentinel": True})
    before_peer = {name: await peer.collection(module, name).find_one({"record_id": "bob-000"}) for name in names}
    before_foreign = {name: await foreign.collection(module, name).find_one({}) for name in names}
    exported = await host.http.get("/api/account/export", headers=host.headers())
    assert exported.status_code == 200
    data = exported.json()
    assert not any(key.endswith(".__error__") for key in data), data
    if module == "files":
        assert len(data["files"]) == 105 and all(r["created_by"] == "alice" for r in data["files"])
        assert all("_id" not in r and "app_id" not in r for r in data["files"])
    elif module == "commerce":
        assert data["cart"]["actor_id"] == "alice"
        assert len(data["orders"]) == len(data["checkout_requests"]) == 105
    elif module == "friends":
        assert len(data["friendships"]) == len(data["friend_requests_sent"]) == 105
        assert data["friend_requests_received"] == []
    elif module == "activity_feed":
        assert len(data["activity_events"]) == 105
    elif module == "notification_settings":
        assert data["preferences"]["email_enabled"] is False
        assert isinstance(data["preferences"]["updated_at"], str)
    else:
        assert len(data["requests"]) == 105
    deleted = await host.http.request("DELETE", "/api/account?app_id=app-two", headers=host.headers(),
                                      json={"user_id": "bob"})
    assert deleted.status_code == 200
    result = deleted.json()["results"][module]
    assert "error" not in result, result
    for name in names:
        collection = owner.collection(module, name)
        if module == "files":
            assert await collection.count({"created_by": "alice", "deleted": True}) == 105
            assert await collection.count({"created_by": "alice", "deleted": False}) == 0
        elif name == "products":
            assert await collection.count({"actor_id": "alice"}) == 1
        else:
            assert await collection.count({"record_id": {"$regex": "^alice-"}}) == 0
        assert await peer.collection(module, name).find_one({"record_id": "bob-000"}) == before_peer[name]
        assert await foreign.collection(module, name).find_one({}) == before_foreign[name]
    assert await decoy.count_documents({"sentinel": True}) == 1
    again = await host.http.delete("/api/account", headers=host.headers())
    assert not any(value for key, value in again.json()["results"][module].items() if key != "module")


@pytest.mark.parametrize("module", MODULES)
@pytest.mark.parametrize("mismatch", ["app", "user", "missing_principal", "empty_user"])
async def test_handler_rejects_mismatched_account_before_access(host, module, mismatch):
    host.registry.register(module, handler_class(module))
    persistence = host.persistence(app_id="other" if mismatch == "app" else "app-one",
                                   user_id="bob" if mismatch == "user" else "alice",
                                   principal=mismatch != "missing_principal")
    user_id = "" if mismatch == "empty_user" else "alice"
    exported = await host.registry.export_all(app_id="app-one", user_id=user_id, db=host.db, persistence=persistence)
    deleted = await host.registry.delete_all(app_id="app-one", user_id=user_id, db=host.db, persistence=persistence)
    assert f"{module}.__error__" in exported
    assert "error" in deleted[module]
    expected = "another app" if mismatch == "app" else "another user"
    assert expected in exported[f"{module}.__error__"]
    assert expected in deleted[module]["error"]


@pytest.mark.parametrize("module", MODULES)
async def test_pack_account_handlers_require_runtime_persistence(module):
    registry = AccountDataRegistry()
    registry.register(module, handler_class(module))
    exported = await registry.export_all(app_id="app-one", user_id="alice", db=object())
    deleted = await registry.delete_all(app_id="app-one", user_id="alice", db=object())
    expected = "account handler requires runtime persistence, but none was supplied"
    assert exported == {f"{module}.__error__": expected}
    assert deleted == {module: {"error": expected}}


async def test_activity_export_preserves_existing_newest_500_policy(host):
    host.registry.register("activity_feed", handler_class("activity_feed"))
    events = host.persistence().collection("activity_feed", "events")
    for index in range(505):
        await events.insert_one({**row("activity_feed", index),
                                 "created_at": datetime(2026, 1, 1, tzinfo=UTC) + timedelta(seconds=index)})
    exported = await host.http.get("/api/account/export", headers=host.headers())
    assert exported.status_code == 200
    records = exported.json()["activity_events"]
    assert [record["record_id"] for record in records] == [f"alice-{index:03d}" for index in range(504, 4, -1)]
    assert all("_id" not in record for record in records)
    assert all(isinstance(record["metadata"]["external_id"], str) for record in records)


async def test_file_delete_preserves_prior_tombstone_and_blob_metadata(host):
    host.registry.register("files", handler_class("files"))
    records = host.persistence().collection("files", "records")
    await records.insert_one({**row("files"), "blob_key": "existing-object", "deleted": True,
                              "deleted_at": "2025-01-01T00:00:00Z", "deleted_by": "alice"})
    before = await records.find_one({"record_id": "alice-000"})
    exported = await host.http.get("/api/account/export", headers=host.headers())
    assert exported.json()["files"] == []
    deleted = await host.http.delete("/api/account", headers=host.headers())
    assert deleted.json()["results"]["files"] == {"files_deleted": 0}
    assert await records.find_one({"record_id": "alice-000"}) == before


async def test_shared_social_contributions_are_removed_without_deleting_peer_content(host):
    owner, peer, foreign = host.persistence(), host.persistence(user_id="bob"), host.persistence(app_id="app-two")
    for module in ("friends", "activity_feed"):
        host.registry.register(module, handler_class(module))
    for index in range(105):
        await peer.collection("friends", "friendships").insert_one({"record_id": index, "friend_user_id": "alice"})
        await peer.collection("friends", "requests").insert_one({"record_id": index, "requester_id": "bob", "recipient_id": "alice"})
        await peer.collection("activity_feed", "events").insert_one({
            "record_id": index, "actor_id": "bob", "about_user_id": "alice", "feed_user_ids": ["alice", "bob"],
            "subject_label": "Peer content", "created_at": datetime(2026, 1, 1, tzinfo=UTC),
        })
    unrelated = {"actor_id": "carol", "feed_user_ids": ["bob"], "subject_label": "Unrelated"}
    await peer.collection("activity_feed", "events").insert_one(unrelated)
    await foreign.collection("activity_feed", "events").insert_one({**unrelated, "about_user_id": "alice"})
    response = await host.http.get("/api/account/export", headers=host.headers())
    assert len(response.json()["friend_requests_received"]) == 105
    assert response.json()["activity_events"] == []
    deleted = await host.http.delete("/api/account", headers=host.headers())
    result = deleted.json()["results"]
    assert result["friends"] == {"friendships_deleted": 105, "requests_deleted": 105, "deleted_count": 210}
    assert result["activity_feed"] == {"events_deleted": 0, "events_anonymized": 105, "feed_memberships_removed": 105}
    events = owner.collection("activity_feed", "events")
    assert await events.count({"actor_id": "bob", "feed_user_ids": ["bob"], "about_user_id": {"$exists": False},
                               "subject_label": "Peer content"}) == 105
    assert await events.count({"subject_label": "Unrelated"}) == 1
    assert await foreign.collection("activity_feed", "events").count({"about_user_id": "alice"}) == 1
