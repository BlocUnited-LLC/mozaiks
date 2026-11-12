"""Account handlers keep per-request module identity through real account routes."""
from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace

import httpx
from fastapi import FastAPI

from factory_app.build_context.notifications.templates.modules.notification_settings.backend.account_data_handler import (
    AccountDataHandler,
)
from mozaiksai.core.account import AccountDataRegistry
from mozaiksai.core.auth import UserPrincipal, require_user_scope
from mozaiksai.core.auth.adapters import registry as auth_registry
from mozaiksai.core.runtime.composition.platform_hooks import PlatformHookRegistry
from mozaiksai.core.runtime.persistence import MongoPersistenceContext, PersistencePrincipal
from mozaiksai.core.runtime.persistence import app_data as data_module
from mozaiksai.hosts.routers import account as account_routes
from tests.test_runtime_persistence_ownership import RecordingCollection

OWNER = "notification_settings"
READER = "account_audit"


def contract():
    return {
        "version": "1", "surfaces": [{
            "surface_id": OWNER, "surface_kind": "module", "collections": [{
                "name": "preferences", "entity": "Preference", "scope": "app",
                "tenancy": "per_user", "owner_field": "user_id",
                "fields": [{"name": "user_id", "type": "string", "required": True}],
                "ownership": {"surface_id": OWNER, "surface_kind": "module"},
            }],
        }],
        "shared_collections": [{
            "owner_module": OWNER, "name": "preferences",
            "read_by": [{"module": READER, "access": "read_only"}],
        }],
    }


class Driver(RecordingCollection):
    async def find_one(self, query, projection=None, **options):
        await super().find_one(query, projection, **options)
        return {"email_enabled": False}

    async def delete_many(self, query, **options):
        await super().delete_many(query, **options)
        return SimpleNamespace(deleted_count=1)


class Database:
    def __init__(self):
        self.collections = {}

    def __getitem__(self, name):
        return self.collections.setdefault(name, Driver())


class Client:
    def __init__(self):
        self.databases = {}

    def __getitem__(self, name):
        return self.databases.setdefault(name, Database())

    def calls(self):
        return [call for database in self.databases.values()
                for driver in database.collections.values() for call in driver.calls]


class ReadOnlyPeer:
    def __init__(self, persistence):
        self.persistence = persistence

    async def export_user_data(self, *, app_id, user_id):
        await self.persistence.collection(OWNER, "preferences").find_one({"user_id": user_id})
        return {"audit_reader_export": True}

    async def delete_user_data(self, *, app_id, user_id):
        await self.persistence.collection(OWNER, "preferences").delete_many({"user_id": user_id})
        return {"unexpected_write": True}


async def test_registry_factory_preserves_owner_export_and_refuses_reader_delete():
    data = contract()
    client = Client()
    registry = AccountDataRegistry()
    registry.register(OWNER, AccountDataHandler)
    registry.register(READER, ReadOnlyPeer)
    seen = []

    def factory(module_id):
        context = MongoPersistenceContext(
            app_id="app-a", module_id=module_id, client=client, data_contract=data,
            principal=PersistencePrincipal("alice", "workspace-a", tenant_id="tenant-a"),
        )
        seen.append((module_id, context))
        return context

    exported = await registry.export_all(
        app_id="app-a", user_id="alice", db=None, persistence_factory=factory,
    )
    assert exported["preferences"]["email_enabled"] is False
    assert exported["audit_reader_export"] is True
    deleted = await registry.delete_all(
        app_id="app-a", user_id="alice", db=None, persistence_factory=factory,
    )
    assert deleted[OWNER]["preferences_deleted"] == 1
    assert "read_only" in deleted[READER]["error"]
    assert [module for module, _ in seen] == [OWNER, READER, OWNER, READER]
    assert len({id(context) for _, context in seen}) == 4
    assert [call[0] for call in client.calls()] == ["find_one", "find_one", "delete_many"]
    assert set(vars(registry)) == {"_handlers"}


async def test_account_routes_use_registered_identity_and_keep_request_scopes(monkeypatch):
    data = contract()
    before = deepcopy(data)
    client = Client()
    registry = AccountDataRegistry()
    registry.register(OWNER, AccountDataHandler)
    registry.register(READER, ReadOnlyPeer)
    monkeypatch.setattr(account_routes, "account_data_registry", registry)
    monkeypatch.setattr(account_routes, "get_platform_hooks", PlatformHookRegistry)
    monkeypatch.setattr(data_module, "get_mongo_client", lambda: client)
    monkeypatch.setattr(auth_registry, "is_auth_enabled", lambda: True)
    created = []

    def context(**kwargs):
        instance = MongoPersistenceContext(**kwargs, client=client)
        created.append((kwargs, instance))
        return instance

    monkeypatch.setattr(account_routes, "MongoPersistenceContext", context)
    authenticated = {"user": "alice", "app": "app-a"}

    def principal():
        return UserPrincipal(
            user_id=authenticated["user"], app_id=authenticated["app"], email=None,
            name=None, roles=[], scopes=[], raw_claims={}, tenant_id="tenant-a",
            workspace_id="workspace-a", auth_provenance="token_validated",
        )

    app = FastAPI()
    app.state.data_contract = data
    app.include_router(account_routes.router)
    app.dependency_overrides[require_user_scope] = principal
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as http:
        first = await http.get("/api/account/export?app_id=foreign&user_id=mallory&module_id=other")
        assert first.status_code == 200, first.text
        assert first.json()["_meta"]["app_id"] == "app-a"
        assert first.json()["preferences"]["email_enabled"] is False
        deleted = await http.request(
            "DELETE", "/api/account?app_id=foreign&module_id=other",
            json={"user_id": "mallory", "module_id": OWNER},
        )
        assert deleted.status_code == 200, deleted.text
        assert deleted.json()["results"][OWNER]["preferences_deleted"] == 1
        assert "read_only" in deleted.json()["results"][READER]["error"]
        authenticated.update(user="bob", app="app-b")
        second = await http.get("/api/account/export")
        assert second.status_code == 200, second.text
        assert second.json()["_meta"]["user_id"] == "bob"

    assert [arguments["module_id"] for arguments, _ in created] == [OWNER, READER] * 3
    assert [arguments["app_id"] for arguments, _ in created] == ["app-a"] * 4 + ["app-b"] * 2
    assert [instance.principal.user_id for _, instance in created] == ["alice"] * 4 + ["bob"] * 2
    assert all(instance.principal.workspace_id == "workspace-a" for _, instance in created)
    assert all(instance.principal.tenant_id == "tenant-a" for _, instance in created)
    assert len({id(instance) for _, instance in created}) == 6
    for call in client.calls():
        scope = call[1].get("$and", [call[1]])[0]
        assert (scope["app_id"], scope["user_id"]) in {("app-a", "alice"), ("app-b", "bob")}
    assert sum(call[0] == "delete_many" for call in client.calls()) == 1
    assert data == before
    assert set(vars(registry)) == {"_handlers"}
