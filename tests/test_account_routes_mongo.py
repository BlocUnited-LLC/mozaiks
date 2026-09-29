"""Authenticated account routes use the same database as app-owned modules."""
from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import httpx
import jwt
import pytest
import pytest_asyncio
from fastapi import FastAPI

import mozaiksai.core.account as account
from mozaiksai.core import core_config
from mozaiksai.core.runtime.app.module_loader import ModuleLoader
from mozaiksai.core.runtime.composition.platform_hooks import PlatformHookRegistry
from mozaiksai.core.runtime.persistence import MongoPersistenceContext
from mozaiksai.hosts.routers import account as account_routes
from mozaiksai.resources import resolve_factory_app_root


@pytest_asyncio.fixture
async def account_host(monkeypatch, request):
    factory_root = resolve_factory_app_root()
    database_name = f"alignment_account_{uuid4().hex}"
    monkeypatch.setenv("PLATFORM_PATH", str(factory_root))
    monkeypatch.setenv("MONGO_URI", os.getenv("MONGO_URI", "mongodb://127.0.0.1:27017"))
    for name in ("MOZAIKS_APP_DATA_DATABASE_NAME", "MOZAIKS_APP_DATABASE_NAME", "MOZAIKS_APPS_DATABASE"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv(request.param, database_name)
    monkeypatch.setenv("ENV", "test")
    monkeypatch.setenv("ENVIRONMENT", "test")
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setenv("AUTH_PROVIDER", "supabase")
    monkeypatch.setenv("SUPABASE_URL", "http://127.0.0.1")
    signing_key = uuid4().hex + uuid4().hex
    monkeypatch.setenv("SUPABASE_JWT_SECRET", signing_key)
    monkeypatch.setattr(core_config, "_mongo_client", None)
    monkeypatch.setattr(core_config, "_mongo_client_conn_str", None)
    client = core_config.get_mongo_client()
    try:
        await client.admin.command("ping", maxTimeMS=1500)
    except Exception:
        client.close()
        if os.getenv("MOZAIKS_REQUIRE_REAL_MONGO"):
            pytest.fail("Mongo is required for account route acceptance")
        pytest.skip("Mongo is unavailable")

    registry = account.AccountDataRegistry()
    monkeypatch.setattr(account, "account_data_registry", registry)
    monkeypatch.setattr(account_routes, "account_data_registry", registry)
    monkeypatch.setattr(PlatformHookRegistry, "_instance", PlatformHookRegistry())
    loader = ModuleLoader(str(factory_root / "app"))
    loader.load("user_onboarding")
    assert registry.registered_module_ids() == ["user_onboarding"]
    host = FastAPI()
    host.include_router(account_routes.router)

    def authorization(app_id, user_id):
        now = datetime.now(UTC)
        token = jwt.encode(
            {"sub": user_id, "aud": "authenticated", "role": "authenticated",
             "iat": now, "exp": now + timedelta(minutes=5), "app_metadata": {"app_id": app_id}},
            signing_key, algorithm="HS256",
        )
        return {"Authorization": f"Bearer {token}"}

    def persistence(app_id, user_id):
        return MongoPersistenceContext(app_id=app_id, user_id=user_id, database_name=database_name)

    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=host), base_url="http://test") as http:
            yield SimpleNamespace(http=http, persistence=persistence, authorization=authorization)
    finally:
        assert database_name.startswith("alignment_account_")
        await client.drop_database(database_name)
        client.close()


@pytest.mark.parametrize(
    "account_host", ["MOZAIKS_APP_DATA_DATABASE_NAME", "MOZAIKS_APP_DATABASE_NAME"], indirect=True,
)
async def test_account_routes_export_delete_and_retry_preserve_app_and_owner_scope(account_host):
    host = account_host
    owned = host.persistence("app-one", "alice").collection("user_onboarding", "status")
    other_owner = host.persistence("app-one", "bob").collection("user_onboarding", "status")
    other_app = host.persistence("app-two", "alice").collection("user_onboarding", "status")
    for collection in (owned, other_owner, other_app):
        await collection.insert_one({"seen_welcome": True})

    assert (await host.http.get("/api/account/export")).status_code == 401
    assert (await host.http.delete("/api/account")).status_code == 401
    headers = host.authorization("app-one", "alice")
    exported = await host.http.get("/api/account/export?app_id=app-two", headers=headers)
    assert exported.status_code == 200
    assert exported.json()["_meta"]["app_id"] == "app-one"
    assert exported.json()["user_onboarding_status"] == [
        {"app_id": "app-one", "user_id": "alice", "seen_welcome": True},
    ]
    deleted = await host.http.request(
        "DELETE", "/api/account?app_id=app-two", headers=headers,
        json={"app_id": "app-two", "user_id": "bob"},
    )
    assert deleted.status_code == 200
    assert deleted.json()["results"] == {"user_onboarding": {"deleted_count": 1}}
    assert await owned.find_one({"user_id": "alice"}) is None
    assert await other_owner.find_one({"user_id": "bob"}) is not None
    assert await other_app.find_one({"user_id": "alice"}) is not None
    again = await host.http.delete("/api/account", headers=headers)
    assert again.json()["results"] == {"user_onboarding": {"deleted_count": 0}}
    assert (await host.http.get("/api/account/export", headers=headers)).json()["user_onboarding_status"] == []


@pytest_asyncio.fixture
async def rendered_account_host(monkeypatch, tmp_path):
    """The code-rendered handler of a per_user module, loaded and served by the real routes."""
    from mozaiksai.core.runtime.persistence import PersistencePrincipal
    from mozaiksai.core.workflow.generator_support.module_account_data import (
        materialize_module_account_handlers,
    )
    from tests.test_appgenerator_module_write_actions import MODULE, _contract, _files, _plan

    database_name = f"alignment_account_{uuid4().hex}"
    monkeypatch.setenv("MONGO_URI", os.getenv("MONGO_URI", "mongodb://127.0.0.1:27017"))
    for name in ("MOZAIKS_APP_DATA_DATABASE_NAME", "MOZAIKS_APP_DATABASE_NAME", "MOZAIKS_APPS_DATABASE"):
        monkeypatch.delenv(name, raising=False)
    # Module persistence (and so the account routes' scoped persistence) uses the app database.
    monkeypatch.setenv("MOZAIKS_APP_DATABASE_NAME", database_name)
    monkeypatch.setenv("ENV", "test")
    monkeypatch.setenv("ENVIRONMENT", "test")
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setenv("AUTH_PROVIDER", "supabase")
    monkeypatch.setenv("SUPABASE_URL", "http://127.0.0.1")
    signing_key = uuid4().hex + uuid4().hex
    monkeypatch.setenv("SUPABASE_JWT_SECRET", signing_key)
    monkeypatch.setattr(core_config, "_mongo_client", None)
    monkeypatch.setattr(core_config, "_mongo_client_conn_str", None)
    client = core_config.get_mongo_client()
    try:
        await client.admin.command("ping", maxTimeMS=1500)
    except Exception:
        client.close()
        if os.getenv("MOZAIKS_REQUIRE_REAL_MONGO"):
            pytest.fail("Mongo is required for account route acceptance")
        pytest.skip("Mongo is unavailable")

    contract = _contract()
    files = _files()
    files.update(materialize_module_account_handlers(files, app_build_plan=_plan(), data_contract=contract))
    for path, source in files.items():
        target = tmp_path / "app" / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(source, encoding="utf-8")
    registry = account.AccountDataRegistry()
    monkeypatch.setattr(account, "account_data_registry", registry)
    monkeypatch.setattr(account_routes, "account_data_registry", registry)
    monkeypatch.setattr(PlatformHookRegistry, "_instance", PlatformHookRegistry())
    ModuleLoader(str(tmp_path / "app")).load(MODULE)
    assert registry.registered_module_ids() == [MODULE]
    host = FastAPI()
    host.state.data_contract = contract
    host.include_router(account_routes.router)

    def authorization(app_id, user_id):
        now = datetime.now(UTC)
        token = jwt.encode(
            {"sub": user_id, "aud": "authenticated", "role": "authenticated",
             "iat": now, "exp": now + timedelta(minutes=5), "app_metadata": {"app_id": app_id}},
            signing_key, algorithm="HS256",
        )
        return {"Authorization": f"Bearer {token}"}

    def tasks(app_id, user_id):
        return MongoPersistenceContext(
            app_id=app_id, user_id=user_id, database_name=database_name, data_contract=contract,
            principal=PersistencePrincipal(user_id=user_id),
        ).collection(MODULE, "tasks")

    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=host), base_url="http://test") as http:
            yield SimpleNamespace(http=http, tasks=tasks, authorization=authorization, module=MODULE)
    finally:
        assert database_name.startswith("alignment_account_")
        await client.drop_database(database_name)
        client.close()


async def test_rendered_account_handler_exports_and_deletes_only_the_owners_rows(rendered_account_host):
    host = rendered_account_host
    created = datetime(2026, 9, 1, tzinfo=UTC)
    alice, bob, other_app = host.tasks("app-one", "alice"), host.tasks("app-one", "bob"), host.tasks("app-two", "alice")
    # More rows than one persistence page, so the export pages through them.
    for index in range(105):
        await alice.insert_one({"task_id": f"a-{index:03d}", "title": f"Task {index}", "created_at": created,
                                "updated_at": created})
    await bob.insert_one({"task_id": "b-1", "title": "Bob's", "created_at": created, "updated_at": created})
    await other_app.insert_one({"task_id": "x-1", "title": "Other app", "created_at": created, "updated_at": created})

    headers = host.authorization("app-one", "alice")
    exported = await host.http.get("/api/account/export", headers=headers)
    assert exported.status_code == 200
    rows = exported.json()[f"{host.module}_tasks"]
    assert len(rows) == 105 and {row["user_id"] for row in rows} == {"alice"}
    assert rows[0] == {"task_id": "a-000", "user_id": "alice", "title": "Task 0",
                       "created_at": "2026-09-01T00:00:00", "updated_at": "2026-09-01T00:00:00"}
    deleted = await host.http.request("DELETE", "/api/account", headers=headers, json={"user_id": "bob"})
    assert deleted.status_code == 200
    assert deleted.json()["results"] == {host.module: {"deleted_count": 105}}
    assert await alice.count({}) == 0
    assert await bob.count({}) == 1 and await other_app.count({}) == 1
    again = await host.http.delete("/api/account", headers=headers)
    assert again.json()["results"] == {host.module: {"deleted_count": 0}}
