"""Signed-user Studio requests exercise real registry persistence boundaries."""

from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import httpx
import jwt
import pytest
import pytest_asyncio
from motor.motor_asyncio import AsyncIOMotorClient

from factory_app.app.modules.app_registry.backend.handler import AppRegistryModule
from factory_app.app.modules.app_registry.backend.repo import AppRegistryRepo
from factory_app.app.modules.app_registry.backend.service import AppRegistryService
from mozaiksai.core.auth import reset_auth_adapter


@pytest_asyncio.fixture
async def registry_host(monkeypatch):
    database_name = f"registry_ownership_{uuid4().hex}"
    mongo = AsyncIOMotorClient(os.getenv("MONGO_URI", "mongodb://127.0.0.1:27017"), serverSelectionTimeoutMS=1500)
    try:
        await mongo.admin.command("ping")
    except Exception:
        mongo.close()
        if os.getenv("MOZAIKS_REQUIRE_REAL_MONGO"):
            pytest.fail("Real Mongo is required for app registry ownership acceptance")
        pytest.skip("Mongo is unavailable")
    collection = mongo[database_name]["AppRegistryRecords"]
    repo = AppRegistryRepo.__new__(AppRegistryRepo)

    async def get_collection():
        return collection

    monkeypatch.setattr(repo, "_collection", get_collection)
    service = AppRegistryService(repo=repo)
    monkeypatch.setenv("PLATFORM_PATH", str(Path(__file__).resolve().parents[1] / "factory_app" / "app"))
    monkeypatch.setenv("ENV", "test")
    monkeypatch.setenv("ENVIRONMENT", "test")
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setenv("AUTH_PROVIDER", "supabase")
    monkeypatch.setenv("SUPABASE_URL", "http://127.0.0.1")
    monkeypatch.setenv("RATE_LIMIT_ENABLED", "false")
    signing_key = uuid4().hex + uuid4().hex
    monkeypatch.setenv("SUPABASE_JWT_SECRET", signing_key)
    reset_auth_adapter()
    import mozaiksai.core.usage as usage
    from mozaiksai.hosts import studio

    purged = []

    async def purge(*, app_id):
        purged.append(app_id)

    monkeypatch.setattr(usage, "get_runtime_usage_ledger", lambda: SimpleNamespace(purge_usage_for_app=purge))
    monkeypatch.setattr(studio, "_get_app_registry_service", lambda: service)

    def headers(user):
        now = datetime.now(UTC)
        token = jwt.encode(
            {"sub": user, "aud": "authenticated", "role": "authenticated", "iat": now,
             "exp": now + timedelta(minutes=5)},
            signing_key, algorithm="HS256",
        )
        return {"Authorization": f"Bearer {token}"}

    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=studio.app), base_url="http://test") as http:
            yield SimpleNamespace(http=http, service=service, collection=collection, headers=headers, purged=purged)
    finally:
        reset_auth_adapter()
        assert database_name.startswith("registry_ownership_")
        await mongo.drop_database(database_name)
        mongo.close()


async def _create(host, *, owner="alice"):
    response = await host.http.post(
        "/api/studio/apps", headers=host.headers(owner),
        json={"name": "Owner's app"},
    )
    assert response.status_code == 200, response.text
    return response.json()["app"]


async def test_another_user_cannot_take_over_existing_app_id(registry_host):
    host = registry_host
    record = await _create(host)
    before = await host.collection.find_one({"_id": record["build_registry_id"]})
    response = await host.http.post(
        "/api/studio/apps", headers=host.headers("bob"),
        json={"app_id": "owned-app", "name": "Replacement", "owner_user_id": "alice"},
    )
    assert response.status_code == 422
    assert await host.collection.find_one({"_id": record["build_registry_id"]}) == before


@pytest.mark.parametrize("path", ["/api/studio/overview", "/api/studio/build"])
async def test_studio_reads_hide_other_owners_record(registry_host, path):
    host = registry_host
    record = await _create(host)
    response = await host.http.get(path + "?app_id=" + record["app_id"], headers=host.headers("bob"))
    assert response.status_code == 404, response.text


@pytest.mark.parametrize("method", ["DELETE"])
async def test_other_owner_and_anonymous_cannot_mutate_or_purge(registry_host, method):
    host = registry_host
    record = await _create(host)
    before = await host.collection.find_one({"_id": record["build_registry_id"]})
    path = f"/api/studio/apps/{record['build_registry_id']}" + ("/status" if method == "PUT" else "")
    payload = {"status": "active"} if method == "PUT" else None
    assert (await host.http.request(method, path, json=payload)).status_code == 401
    response = await host.http.request(method, path, json=payload, headers=host.headers("bob"))
    assert response.status_code == 404
    assert await host.collection.find_one({"_id": record["build_registry_id"]}) == before
    assert host.purged == []


async def test_owner_can_create_distinct_apps_and_delete_record(registry_host):
    host = registry_host
    original = await _create(host)
    reopened = await _create(host)
    assert original["build_registry_id"] != reopened["build_registry_id"]
    assert original["app_id"] != reopened["app_id"]
    path = f"/api/studio/apps/{original['build_registry_id']}"
    updated = await host.http.put(path + "/status", headers=host.headers("alice"), json={"status": "active"})
    assert updated.status_code == 404, updated.text
    deleted = await host.http.delete(path, headers=host.headers("alice"))
    assert deleted.status_code == 200, deleted.text
    assert deleted.json()["success"] is True
    assert await host.collection.count_documents({}) == 1
    assert host.purged == []


async def test_concurrent_new_app_requests_allocate_distinct_targets(registry_host):
    host = registry_host
    results = await asyncio.gather(*[_create(host) for _ in range(12)])
    assert len({item["build_registry_id"] for item in results}) == 12
    assert len({item["app_id"] for item in results}) == 12
    assert await host.collection.count_documents({}) == 12


async def test_service_reads_and_promotions_require_owner(registry_host):
    host = registry_host
    record = (await host.service.create_app_record(
        owner_user_id="alice", app_id="owned-app", status="review",
        current_build_run={"build_id": "build_1", "phase": "genesis", "artifact_version_id": "av_1"},
    ))["app"]
    for reference in ({"app_id": "owned-app"}, {"build_registry_id": record["build_registry_id"]}):
        assert (await host.service.get_app_record(owner_user_id="bob", **reference))["app"] is None
        assert (await host.service.get_app_record(owner_user_id="alice", **reference))["app"]["app_id"] == "owned-app"
    with pytest.raises(ValueError, match="not found"):
        await host.service.promote_build(
            build_registry_id=record["build_registry_id"], promoted_by="bob",
            expected_build_id="build_1", expected_artifact_version_id="av_1",
        )
    assert (await host.collection.find_one({"app_id": "owned-app"}))["lifecycle_state"] == "review"
    result = await host.service.promote_build(
        build_registry_id=record["build_registry_id"], promoted_by="alice",
        expected_build_id="build_1", expected_artifact_version_id="av_1",
    )
    assert result["app"]["lifecycle_state"] == "active"


async def test_promotion_cannot_activate_a_superseded_build(registry_host):
    host = registry_host
    record = (await host.service.create_app_record(
        owner_user_id="alice", app_id="owned-app", status="review",
        current_build_run={"build_id": "new-build", "phase": "refinement", "artifact_version_id": "new-artifact"},
    ))["app"]
    before = await host.collection.find_one({"app_id": "owned-app"})
    result = await host.service.promote_build(
        build_registry_id=record["build_registry_id"], promoted_by="alice",
        expected_build_id="old-build", expected_artifact_version_id="old-artifact",
    )
    assert result == {"success": False, "app": None}
    assert await host.collection.find_one({"app_id": "owned-app"}) == before


async def test_ensure_status_cannot_reopen_someone_elses_record(registry_host):
    host = registry_host
    await host.service.create_app_record(owner_user_id="alice", app_id="owned-app")
    before = await host.collection.find_one({"app_id": "owned-app"})
    with pytest.raises(ValueError):
        await host.service.ensure_status_for_app(app_id="owned-app", owner_user_id="bob", status="draft")
    assert await host.collection.find_one({"app_id": "owned-app"}) == before


@pytest.mark.parametrize("owner", [None, "", " "])
async def test_missing_owner_cannot_create_a_record(registry_host, owner):
    with pytest.raises(ValueError, match="owner_user_id"):
        await registry_host.service.create_app_record(owner_user_id=owner, app_id="new-app")
    assert await registry_host.collection.count_documents({}) == 0


async def test_concurrent_different_owners_cannot_share_an_app_id(registry_host):
    host = registry_host
    owners = ["alice", "bob"] * 6
    responses = await asyncio.gather(*[
        host.http.post("/api/studio/apps", headers=host.headers(owner), json={"app_id": "shared-target"})
        for owner in owners
    ])
    assert await host.collection.count_documents({}) == 0
    for response in responses:
        assert response.status_code == 422


async def test_interrupted_creation_keeps_ownership_and_allows_owner_retry(registry_host, monkeypatch):
    host = registry_host
    original = host.collection.find_one_and_update

    async def interrupted(query, update, **kwargs):
        if "$set" in update:
            raise RuntimeError("interrupted metadata write")
        return await original(query, update, **kwargs)

    monkeypatch.setattr(host.collection, "find_one_and_update", interrupted)
    with pytest.raises(RuntimeError, match="interrupted metadata"):
        await host.service.create_app_record(owner_user_id="alice", app_id="owned-app")
    draft = await host.collection.find_one({"app_id": "owned-app"})
    assert draft["owner_user_id"] == "alice"
    assert draft["lifecycle_state"] == "draft"
    monkeypatch.setattr(host.collection, "find_one_and_update", original)
    with pytest.raises(ValueError, match="not available"):
        await host.service.create_app_record(owner_user_id="bob", app_id="owned-app")
    reopened = (await host.service.create_app_record(owner_user_id="alice", app_id="owned-app"))["app"]
    assert reopened["build_registry_id"] == draft["_id"]


async def test_module_actions_use_context_owner_and_do_not_emit_on_denial(registry_host):
    host = registry_host
    record = await _create(host)
    events = []

    async def emit(*args):
        events.append(args)

    module = AppRegistryModule(service=host.service)
    context = SimpleNamespace(user_id="bob", app_id="studio-host", emit=emit)
    reference = {"build_registry_id": record["build_registry_id"]}
    assert (await module.get_app_record(context, **reference))["app"] is None
    assert not hasattr(module, "update_build_status")
    assert (await module.delete_app(context, **reference))["success"] is False
    assert not hasattr(module, "promote_build")
    assert events == []
    created = await module.create_app_record(context, name="Independent app")
    assert created["app"]["app_id"] != "studio-host"
    assert created["app"]["owner_user_id"] == "bob"
