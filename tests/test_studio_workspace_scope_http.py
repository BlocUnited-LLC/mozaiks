"""Studio workspace-scoped module actions act only on the caller's verified workspace.

Signed tokens through the real JWT adapter, module router and executor, with
the Studio ``workspace_integrations`` and ``messages`` modules loaded from
their own module.yaml. Only the JWKS transport, the connector store, the vault
backend, the app declarations repository and the Mongo driver are replaced, so
every assertion reads the workspace that reached storage.

The verified workspace is the one the validated token is bound to, or the
membership a host scope hook verified (``verified_workspace_id``). A workspace
named by the request, the dispatch tenant and the demo workspace never select
one. Local development (authentication off, development access) keeps its
explicit selection.
"""
from __future__ import annotations

import json
import sys
import time
from collections import defaultdict
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import FastAPI
from fastapi.testclient import TestClient

from mozaiksai.core.auth.adapters import registry as auth_registry
from mozaiksai.core.auth.adapters.jwt_adapter import GenericJWTAdapter, JWTAdapterConfig
from mozaiksai.core.auth.config import clear_auth_config_cache
from mozaiksai.core.data.persistence import ConnectorStore
from mozaiksai.core.runtime.app.module_loader import ModuleLoader
from mozaiksai.core.runtime.composition.module_executor import ModuleExecutor
from mozaiksai.core.runtime.composition.platform_hooks import PlatformHookRegistry
from mozaiksai.core.workflow.generator_support import connector_health, connector_service
from mozaiksai.hosts.routers import modules as module_router

STUDIO_APP = Path(__file__).resolve().parents[1] / "factory_app" / "app"
SCOPES = " ".join((
    "workspace_integrations.read", "workspace_integrations.manage", "messages.read", "messages.write",
))
OWN = "workspace-own"
FOREIGN = "workspace-foreign"
LOCAL = ("127.0.0.1", 50000)
REMOTE = ("198.51.100.9", 50000)


@pytest.fixture(autouse=True)
def _isolated_state(monkeypatch):
    for name in auth_registry._ALL_AUTH_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    clear_auth_config_cache()
    auth_registry.reset_auth_adapter()
    paths = list(sys.path)
    modules = dict(sys.modules)
    yield
    sys.path[:] = paths
    for key in list(sys.modules):
        if key in {"services", "modules"} or key.startswith(("services.", "modules.", "mozaiks_runtime_module_")):
            if key in modules:
                sys.modules[key] = modules[key]
            else:
                sys.modules.pop(key, None)
    clear_auth_config_cache()
    auth_registry.reset_auth_adapter()


class _ConnectorStore:
    """Records the scope every connector read or write was keyed by."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str]] = []

    def _record(self, scope_id: str, scope: str, *, secret: bool = False) -> dict[str, Any]:
        return {
            "scope": scope, "scope_id": scope_id, "service": "openai", "display_name": "OpenAI",
            "status": "active", "secret_available": secret, "public_config": {}, "required_fields": [],
        }

    async def list(self, *, scope, scope_id):
        self.calls.append(("list", scope, scope_id))
        return [self._record(scope_id, scope)]

    async def get(self, *, scope, scope_id, service):
        self.calls.append(("get", scope, scope_id))
        return self._record(scope_id, scope, secret=True)

    async def upsert(self, *, scope, scope_id, service, **_fields):
        self.calls.append(("upsert", scope, scope_id))
        return self._record(scope_id, scope)

    async def delete(self, *, scope, scope_id, service):
        self.calls.append(("delete", scope, scope_id))
        return True

    def scope_ids(self) -> set[str]:
        return {scope_id for _operation, _scope, scope_id in self.calls}


class _StoreClass:
    """Stands in for the ConnectorStore class: constructing it returns the shared fake."""

    SCOPE_APP = ConnectorStore.SCOPE_APP
    SCOPE_WORKSPACE = ConnectorStore.SCOPE_WORKSPACE

    def __init__(self, store: _ConnectorStore) -> None:
        self._store = store

    def __call__(self) -> _ConnectorStore:
        return self._store


class _Vault:
    def __init__(self) -> None:
        self.scope_ids: list[str] = []

    async def store_secret(self, *, scope, scope_id, service, secret_value, display_name=None, ttl_days=30):
        assert scope == "workspace"
        self.scope_ids.append(scope_id)
        return {"success": True, "provider": "test-vault"}

    async def delete_secret(self, *, scope, scope_id, service):
        assert scope == "workspace"
        self.scope_ids.append(scope_id)
        return {"success": True}


class _Declarations:
    def __init__(self) -> None:
        self.calls: list[tuple[str, Any]] = []
        self.rows = [
            {"app_id": "built-app", "service": "openai", "catalog_id": "openai", "display_name": "OpenAI",
             "kind": "api_key", "connector_status": "not_configured", "required_at": "runtime", "optional": False},
            {"app_id": "foreign-app", "service": "resend", "catalog_id": "resend", "display_name": "Resend",
             "kind": "api_key", "connector_status": "not_configured", "required_at": "runtime", "optional": False},
        ]

    async def get_for_app(self, *, app_id):
        self.calls.append(("get", app_id))
        return [dict(row) for row in self.rows if row["app_id"] == app_id]

    async def upsert_declarations(self, *, app_id, declarations):
        self.calls.append(("upsert", app_id))
        self.rows.extend({**row, "app_id": app_id} for row in declarations)
        return declarations

    async def soft_delete_declaration(self, *, app_id, service, removed_by=None):
        self.calls.append(("delete", app_id))
        for row in self.rows:
            if row["app_id"] == app_id and row["service"] == service:
                row["removed"] = True
                return True
        return False

    async def get_catalog_usage_counts(self, *, app_ids, catalog_ids=None):
        self.calls.append(("usage", tuple(app_ids)))
        return {
            catalog_id: len({row["app_id"] for row in self.rows if row.get("catalog_id") == catalog_id
                             and row["app_id"] in app_ids and not row.get("removed")})
            for catalog_id in (catalog_ids or [])
        }


class _AppRegistry:
    def __init__(self) -> None:
        self.owned = {"studio-user": {"built-app"}, "other-user": {"foreign-app"}, "anonymous": {"built-app"}}
        self.calls: list[tuple[str, str, str | None]] = []

    async def get_app_record(self, *, app_id, owner_user_id):
        self.calls.append(("get", owner_user_id, app_id))
        return {"app": {"app_id": app_id} if app_id in self.owned.get(owner_user_id, set()) else None}

    async def list_apps(self, *, owner_user_id):
        self.calls.append(("list", owner_user_id, None))
        return {"apps": [{"app_id": app_id} for app_id in sorted(self.owned.get(owner_user_id, set()))]}


def _matches(row: dict[str, Any], query: dict[str, Any]) -> bool:
    for key, value in query.items():
        if isinstance(value, dict) and "$ne" in value:
            if row.get(key) == value["$ne"]:
                return False
        elif isinstance(row.get(key), list):
            if value not in row[key]:
                return False
        elif row.get(key) != value:
            return False
    return True


class _Cursor:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def sort(self, spec):
        for key, direction in reversed(list(spec)):
            self._rows.sort(key=lambda row: str(row.get(key) or ""), reverse=int(direction) < 0)
        return self

    def limit(self, count):
        self._rows = self._rows[:count]
        return self

    async def to_list(self, length=None):
        return [dict(row) for row in self._rows]


class _Collection:
    """The driver calls the module persistence layer makes, kept in memory."""

    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []

    async def insert_one(self, document, **_options):
        self.rows.append(dict(document))
        return SimpleNamespace(inserted_id=len(self.rows))

    async def find_one(self, query, projection=None, **_options):
        return next((dict(row) for row in self.rows if _matches(row, query)), None)

    def find(self, query, projection=None, **_options):
        return _Cursor([row for row in self.rows if _matches(row, query)])

    async def update_one(self, query, update, *, upsert=False, **_options):
        for row in self.rows:
            if _matches(row, query):
                row.update(update.get("$set") or {})
                return SimpleNamespace(matched_count=1, upserted_id=None)
        if upsert:
            self.rows.append({**{k: v for k, v in query.items() if not isinstance(v, dict)}, **(update.get("$set") or {})})
            return SimpleNamespace(matched_count=0, upserted_id=len(self.rows))
        return SimpleNamespace(matched_count=0, upserted_id=None)


@pytest.fixture
def studio(monkeypatch):
    """A Studio module router serving the real workspace_integrations and messages modules."""
    monkeypatch.setenv("ENV", "test")
    monkeypatch.setenv("ENVIRONMENT", "test")
    store = _ConnectorStore()
    vault = _Vault()
    monkeypatch.setattr(connector_service, "_get_store", lambda store_arg=None: store)
    monkeypatch.setattr(connector_service, "get_connector_vault_backend", lambda: vault)
    monkeypatch.setattr(connector_health, "ConnectorStore", _StoreClass(store))
    # Hosts register live provider checks process-wide; a health check here
    # reports configuration health instead of calling a provider.
    monkeypatch.setattr(connector_health, "_PROVIDERS", {})
    monkeypatch.setattr(module_router, "record_action_invocation", lambda **_: None)
    hooks = PlatformHookRegistry()
    monkeypatch.setattr(module_router, "get_platform_hooks", lambda: hooks)
    monkeypatch.setattr("mozaiksai.core.runtime.composition.module_executor.get_platform_hooks", lambda: hooks)
    monkeypatch.setattr(ModuleExecutor, "_emit_dispatch_audit", AsyncMock())

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key()))
    jwk.update({"kid": "studio-scope", "alg": "RS256", "use": "sig"})

    def authenticated() -> None:
        for name, value in {"AUTH_ENABLED": "true", "AUTH_PROVIDER": "jwt", "AUTH_ISSUER": "https://auth.test",
                            "AUTH_AUDIENCE": "studio-api", "AUTH_JWKS_URL": "https://auth.test/jwks"}.items():
            monkeypatch.setenv(name, value)
        clear_auth_config_cache()
        auth_registry.reset_auth_adapter()
        adapter = GenericJWTAdapter(config=JWTAdapterConfig(
            jwks_url="https://auth.test/jwks", issuer="https://auth.test", audience="studio-api",
        ))
        monkeypatch.setattr(adapter, "_get_jwks_client_async", AsyncMock(return_value=SimpleNamespace(
            get_signing_key=AsyncMock(return_value=jwk),
        )))
        monkeypatch.setattr("mozaiksai.core.auth.dependencies.get_auth_adapter", lambda: adapter)

    def development() -> None:
        monkeypatch.setenv("AUTH_ENABLED", "false")
        clear_auth_config_cache()
        auth_registry.reset_auth_adapter()

    def token(*, workspace: str | None = None, tenant: str | None = None,
              user: str = "studio-user", app_id: str | None = None) -> dict[str, str]:
        claims: dict[str, Any] = {"sub": user, "iss": "https://auth.test", "aud": "studio-api",
                                  "exp": int(time.time()) + 300, "scp": SCOPES}
        if app_id is not None:
            claims["app_id"] = app_id
        if workspace is not None:
            claims["workspace_id"] = workspace
        if tenant is not None:
            claims["tid"] = tenant
        return {"Authorization": "Bearer " + jwt.encode(
            claims, key, algorithm="RS256", headers={"kid": "studio-scope", "typ": "at+jwt"},
        )}

    database: defaultdict[str, defaultdict[str, _Collection]] = defaultdict(lambda: defaultdict(_Collection))
    loader = ModuleLoader(str(STUDIO_APP))
    loaded = [loader.load("workspace_integrations"), loader.load("messages")]
    declarations = _Declarations()
    registry = _AppRegistry()
    loaded[0].handler.service.declarations_repo = declarations
    loaded[0].handler.service.app_registry = registry
    executor = ModuleExecutor(persistence_client=database, persistence_database="studio_scope")
    for module in loaded:
        executor.register_loaded_module(module)
    app = FastAPI()
    app.state.loaded_app_id = json.loads((STUDIO_APP / "app.json").read_text(encoding="utf-8"))["appId"]
    app.state.module_action_surfaces = {module.name: module.action_api_surface_map for module in loaded}
    app.state.executor_registry = SimpleNamespace(module_executor=executor)
    app.include_router(module_router.router)

    def client(peer: tuple[str, int] = REMOTE) -> TestClient:
        return TestClient(app, raise_server_exceptions=False, client=peer, base_url="http://localhost:8000")

    def threads() -> list[dict[str, Any]]:
        return [row for name, rows in database["studio_scope"].items() if name.endswith("threads") and
                not name.endswith("thread_reads") for row in rows.rows]

    return SimpleNamespace(
        store=store, vault=vault, hooks=hooks, token=token, client=client, threads=threads,
        declarations=declarations, registry=registry, executor=executor,
        authenticated=authenticated, development=development,
    )


def _post(client: TestClient, module: str, action: str, headers: dict[str, str] | None = None, *,
          params: dict[str, Any] | None = None, context: dict[str, Any] | None = None, query: str = ""):
    body: dict[str, Any] = {"params": params or {}}
    if context is not None:
        body["context"] = context
    return client.post(f"/api/modules/{module}/{action}{query}", json=body, headers=headers or {})


def _connector(client: TestClient, action: str, headers: dict[str, str] | None = None, **request: Any):
    return _post(client, "workspace_integrations", action, headers, **request)


def _message(client: TestClient, action: str, headers: dict[str, str] | None = None, **request: Any):
    return _post(client, "messages", action, headers, **request)


# --------------------------------------------------------------------------- workspace connectors

# One request per connector action: what each needs besides a workspace.
CONNECTOR_ACTIONS = {
    "list_workspace_connectors": {},
    "save_workspace_connector": {"service": "openai", "secret_value": "placeholder-value"},
    "check_workspace_connector_health": {"service": "openai"},
    "delete_workspace_connector": {"service": "openai"},
}


@pytest.mark.parametrize("action", sorted(CONNECTOR_ACTIONS))
def test_a_matching_context_does_not_let_a_connector_action_target_another_workspace(studio, action) -> None:
    studio.authenticated()
    response = _connector(
        studio.client(), action, studio.token(workspace=OWN, tenant="tenant-own"),
        params={**CONNECTOR_ACTIONS[action], "workspace_id": FOREIGN},
        context={"workspace_id": OWN, "tenant_id": "tenant-own"},
    )

    assert response.status_code == 403
    assert response.json()["detail"]["error_code"] == "PERMISSION_DENIED"
    assert studio.store.calls == []
    assert studio.vault.scope_ids == []


@pytest.mark.parametrize("action", sorted(CONNECTOR_ACTIONS))
def test_a_conflicting_connector_target_alone_is_refused(studio, action) -> None:
    studio.authenticated()
    response = _connector(
        studio.client(), action, studio.token(workspace=OWN),
        params={**CONNECTOR_ACTIONS[action], "workspace_id": FOREIGN},
    )

    assert response.status_code == 403
    assert studio.store.calls == []
    assert studio.vault.scope_ids == []


@pytest.mark.parametrize(
    ("requested", "token_tenant"),
    [
        ({}, "tenant-own"),
        ({"params": {"workspace_id": FOREIGN}}, "tenant-own"),
        ({"context": {"workspace_id": FOREIGN}}, "tenant-own"),
        ({"context": {"tenant_id": FOREIGN}}, None),
        ({"query": f"?workspace_id={FOREIGN}"}, "tenant-own"),
        ({"query": f"?tenant_id={FOREIGN}"}, None),
    ],
    ids=["nothing", "param", "context_workspace", "context_tenant", "query_workspace", "query_tenant"],
)
@pytest.mark.parametrize("action", sorted(CONNECTOR_ACTIONS))
def test_an_identity_without_a_verified_workspace_gets_no_connectors(studio, action, requested, token_tenant) -> None:
    studio.authenticated()
    response = _connector(
        studio.client(), action, studio.token(tenant=token_tenant),
        params={**CONNECTOR_ACTIONS[action], **requested.get("params", {})},
        context=requested.get("context"), query=requested.get("query", ""),
    )

    assert response.status_code == 403
    assert studio.store.calls == []
    assert studio.vault.scope_ids == []


def test_an_unverified_get_query_cannot_choose_a_connector_workspace(studio) -> None:
    studio.authenticated()
    client = studio.client()
    for query in (f"workspace_id={FOREIGN}", f"tenant_id={FOREIGN}"):
        response = client.get(
            f"/api/modules/workspace_integrations/list_workspace_connectors?{query}", headers=studio.token(),
        )
        assert response.status_code == 403
    assert studio.store.calls == []


def test_a_dispatch_without_persistence_fails_closed(studio) -> None:
    # A blank request app id cannot override the host's loaded app identity.
    studio.authenticated()
    response = _connector(
        studio.client(), "list_workspace_connectors", studio.token(workspace=OWN), context={"app_id": " "},
    )

    assert response.status_code == 403
    assert studio.store.calls == []


@pytest.mark.parametrize("action", sorted(CONNECTOR_ACTIONS))
@pytest.mark.parametrize("name_it", [False, True], ids=["default", "named"])
def test_the_callers_own_connector_workspace_is_served(studio, action, name_it) -> None:
    studio.authenticated()
    params = {**CONNECTOR_ACTIONS[action], **({"workspace_id": OWN} if name_it else {})}
    response = _connector(studio.client(), action, studio.token(workspace=OWN, tenant="tenant-own"), params=params)

    assert response.status_code == 200, response.text
    assert studio.store.calls
    assert studio.store.scope_ids() == {OWN}
    assert set(studio.vault.scope_ids) <= {OWN}
    if action == "save_workspace_connector":
        assert studio.vault.scope_ids == [OWN]


def test_a_foreign_dispatch_tenant_is_refused_before_connector_access(studio) -> None:
    studio.authenticated()
    response = _connector(
        studio.client(), "list_workspace_connectors", studio.token(workspace=OWN), context={"tenant_id": FOREIGN},
    )

    assert response.status_code == 403
    assert studio.store.calls == []


def test_a_host_verified_membership_is_the_connector_workspace(studio) -> None:
    studio.authenticated()
    studio.hooks.register_bundle(
        {"module_scope_resolver": lambda **_scope: {"verified_workspace_id": "workspace-member"}}, source="test",
    )
    client = studio.client()

    assert _connector(client, "list_workspace_connectors", studio.token()).status_code == 200
    assert _connector(client, "list_workspace_connectors", studio.token(workspace=OWN)).status_code == 403
    assert studio.store.scope_ids() == {"workspace-member"}

    refused = _connector(client, "list_workspace_connectors", studio.token(), params={"workspace_id": OWN})
    assert refused.status_code == 403
    assert studio.store.scope_ids() == {"workspace-member"}


@pytest.mark.parametrize("action", sorted(CONNECTOR_ACTIONS))
@pytest.mark.parametrize("request_scope", [
    {"context": {"workspace_id": FOREIGN}},
    {"query": f"?workspace_id={FOREIGN}"},
], ids=["context", "query"])
def test_a_verified_member_cannot_name_a_different_dispatch_workspace(studio, action, request_scope) -> None:
    studio.authenticated()
    studio.hooks.register_bundle(
        {"module_scope_resolver": lambda **_scope: {"verified_workspace_id": OWN}}, source="test",
    )

    response = _connector(studio.client(), action, studio.token(), params=CONNECTOR_ACTIONS[action], **request_scope)

    assert response.status_code == 403
    assert studio.store.calls == []
    assert studio.vault.scope_ids == []


@pytest.mark.parametrize("request_scope", [
    {"context": {"tenant_id": FOREIGN}},
    {"query": f"?tenant_id={FOREIGN}"},
], ids=["context", "query"])
def test_a_verified_member_cannot_name_a_different_dispatch_tenant(studio, request_scope) -> None:
    studio.authenticated()
    studio.hooks.register_bundle(
        {"module_scope_resolver": lambda **_scope: {
            "verified_workspace_id": OWN, "verified_tenant_id": "tenant-own",
        }}, source="test",
    )

    response = _connector(studio.client(), "list_workspace_connectors", studio.token(), **request_scope)

    assert response.status_code == 403
    assert studio.store.calls == []


def test_a_host_revoking_the_workspace_leaves_no_connector_workspace(studio) -> None:
    studio.authenticated()
    studio.hooks.register_bundle(
        {"module_scope_resolver": lambda **_scope: {"verified_workspace_id": None}}, source="test",
    )

    response = _connector(studio.client(), "list_workspace_connectors", studio.token(workspace=OWN))

    assert response.status_code == 403
    assert studio.store.calls == []


def test_an_unverified_host_scope_does_not_select_the_connector_workspace(studio) -> None:
    studio.authenticated()
    studio.hooks.register_bundle(
        {"module_scope_resolver": lambda **_scope: {"workspace_id": FOREIGN, "tenant_id": FOREIGN}}, source="test",
    )
    client = studio.client()

    assert _connector(client, "list_workspace_connectors", studio.token(workspace=OWN)).status_code == 403
    assert studio.store.calls == []
    assert _connector(client, "list_workspace_connectors", studio.token()).status_code == 403
    assert studio.store.calls == []


def test_app_integration_needs_overlay_only_the_verified_workspace(studio) -> None:
    studio.authenticated()
    client = studio.client()

    spoofed = _connector(
        client, "list_app_integration_needs", studio.token(), params={"app_id": "built-app"},
        context={"workspace_id": FOREIGN, "tenant_id": FOREIGN},
    )
    assert spoofed.status_code == 403
    assert studio.declarations.calls == []
    unbound = _connector(
        client, "list_app_integration_needs", studio.token(), params={"app_id": "built-app"},
    )
    assert unbound.status_code == 200, unbound.text
    assert unbound.json()["declarations"][0]["connector_status"] == "not_configured"
    assert studio.store.calls == []

    bound = _connector(client, "list_app_integration_needs", studio.token(workspace=OWN), params={"app_id": "built-app"})
    assert bound.status_code == 200
    assert bound.json()["declarations"][0]["workspace_connector_status"] == "partial"
    assert studio.store.calls == [("list", ConnectorStore.SCOPE_WORKSPACE, OWN)]


DECLARATION_ACTIONS = {
    "list_app_integration_needs": {},
    "declare_app_integration_needs": {"needs": [{"service": "sendgrid"}]},
    "upsert_app_integration_need": {"need": {"service": "twilio"}},
    "delete_app_integration_need": {"service": "resend"},
}


@pytest.mark.parametrize("action", sorted(DECLARATION_ACTIONS))
@pytest.mark.parametrize("verified", [False, True], ids=["unbound", "bound_elsewhere"])
@pytest.mark.parametrize("request_scope", [
    {"workspace_id": FOREIGN},
    {"tenant_id": FOREIGN},
    {"workspace_id": FOREIGN, "tenant_id": FOREIGN},
], ids=["workspace", "tenant", "both"])
def test_app_declarations_reject_unverified_dispatch_scope(studio, action, verified, request_scope) -> None:
    studio.authenticated()
    headers = studio.token(workspace=OWN, tenant="tenant-own") if verified else studio.token()
    response = _connector(
        studio.client(), action, headers,
        params={"app_id": "built-app", **DECLARATION_ACTIONS[action]},
        context=request_scope,
    )

    assert response.status_code == 403
    assert studio.declarations.calls == []
    assert studio.store.calls == []


@pytest.mark.parametrize("action,params", [
    ("list_integrations", {}),
    ("get_integration", {"integration_id": "openai"}),
    ("set_integration_note", {"integration_id": "openai", "note": "test"}),
])
@pytest.mark.parametrize("request_scope", [
    {"workspace_id": FOREIGN},
    {"tenant_id": FOREIGN},
    {"workspace_id": FOREIGN, "tenant_id": FOREIGN},
], ids=["workspace", "tenant", "both"])
def test_app_catalog_rejects_unverified_dispatch_scope(studio, action, params, request_scope) -> None:
    studio.authenticated()
    response = _connector(
        studio.client(), action, studio.token(), params=params,
        context=request_scope,
    )

    assert response.status_code == 403
    assert studio.declarations.calls == []
    assert studio.store.calls == []


@pytest.mark.parametrize("action", sorted(DECLARATION_ACTIONS))
def test_owned_app_declarations_accept_matching_verified_dispatch_scope(studio, action) -> None:
    studio.authenticated()
    response = _connector(
        studio.client(), action, studio.token(workspace=OWN, tenant="tenant-own"),
        params={"app_id": "built-app", **DECLARATION_ACTIONS[action]},
        context={"workspace_id": OWN, "tenant_id": "tenant-own"},
    )

    assert response.status_code == 200, response.text
    assert len(studio.declarations.calls) == 1


@pytest.mark.parametrize("action", sorted(DECLARATION_ACTIONS))
def test_a_signed_app_unbound_caller_cannot_touch_another_owners_declarations(studio, action) -> None:
    studio.authenticated()
    response = _connector(
        studio.client(), action, studio.token(workspace=OWN),
        params={"app_id": "foreign-app", **DECLARATION_ACTIONS[action]},
    )

    assert response.status_code == 403
    assert studio.declarations.calls == []
    assert studio.store.calls == []
    assert studio.registry.calls == [("get", "studio-user", "foreign-app")]


@pytest.mark.parametrize("action", sorted(DECLARATION_ACTIONS))
def test_owned_app_declarations_remain_accessible_to_an_app_unbound_caller(studio, action) -> None:
    studio.authenticated()
    response = _connector(
        studio.client(), action, studio.token(workspace=OWN),
        params={"app_id": "built-app", **DECLARATION_ACTIONS[action]},
    )

    assert response.status_code == 200, response.text
    assert studio.declarations.calls == [
        ("get" if action == "list_app_integration_needs" else
         "delete" if action == "delete_app_integration_need" else "upsert", "built-app"),
    ]
    if action == "list_app_integration_needs":
        assert [row["service"] for row in response.json()["declarations"]] == ["openai"]


def test_catalog_usage_counts_only_registry_owned_apps(studio) -> None:
    studio.authenticated()
    client = studio.client()
    own = _connector(client, "list_integrations", studio.token(workspace=OWN))
    other = _connector(client, "list_integrations", studio.token(user="other-user", workspace=FOREIGN))
    unowned = _connector(client, "list_integrations", studio.token(user="third-user"))

    assert own.status_code == other.status_code == unowned.status_code == 200
    own_counts = {row["id"]: row["app_usage_count"] for row in own.json()["integrations"]}
    other_counts = {row["id"]: row["app_usage_count"] for row in other.json()["integrations"]}
    unowned_counts = {row["id"]: row["app_usage_count"] for row in unowned.json()["integrations"]}
    assert (own_counts["openai"], own_counts["resend"]) == (1, 0)
    assert (other_counts["openai"], other_counts["resend"]) == (0, 1)
    assert (unowned_counts["openai"], unowned_counts["resend"]) == (0, 0)
    assert ("usage", ("foreign-app",)) in studio.declarations.calls
    assert ("usage", ("built-app",)) in studio.declarations.calls


@pytest.mark.parametrize(
    ("request_scope", "expected"),
    [
        ({"params": {"workspace_id": "workspace-dev"}}, "workspace-dev"),
        ({"context": {"workspace_id": "workspace-dev"}}, "workspace-dev"),
        ({"context": {"tenant_id": "tenant-dev"}}, "tenant-dev"),
        ({}, "demo-workspace"),
    ],
    ids=["param", "context_workspace", "context_tenant", "demo_default"],
)
def test_local_development_keeps_its_connector_workspace_selection(studio, request_scope, expected) -> None:
    studio.development()
    for action, params in CONNECTOR_ACTIONS.items():
        response = _connector(
            studio.client(LOCAL), action, params={**params, **request_scope.get("params", {})},
            context=request_scope.get("context"),
        )
        assert response.status_code == 200, (action, response.text)
    assert studio.store.scope_ids() == {expected}
    assert set(studio.vault.scope_ids) == {expected}

    studio.store.calls.clear()
    overlay = _connector(studio.client(LOCAL), "list_app_integration_needs", params={"app_id": "built-app"},
                         context=request_scope.get("context"))
    assert overlay.status_code == 200
    overlay_expected = expected if "params" not in request_scope else "demo-workspace"
    assert studio.store.scope_ids() == {overlay_expected}


def test_a_remote_visitor_without_development_access_gets_no_connectors(studio) -> None:
    studio.development()
    response = _connector(studio.client(REMOTE), "list_workspace_connectors", params={"workspace_id": FOREIGN})

    assert response.status_code == 403
    assert studio.store.calls == []


# --------------------------------------------------------------------------- workspace message threads

WORKSPACE_THREAD = {"scope_type": "workspace", "participant_ids": ["teammate"], "title": "Workspace thread"}


def test_a_matching_context_does_not_let_a_thread_name_another_workspace(studio) -> None:
    studio.authenticated()
    response = _message(
        studio.client(), "create_thread", studio.token(workspace=OWN),
        params={**WORKSPACE_THREAD, "scope_id": FOREIGN}, context={"workspace_id": OWN},
    )

    assert response.status_code == 403
    assert studio.threads() == []


@pytest.mark.parametrize(
    "requested",
    [
        {},
        {"params": {"scope_id": FOREIGN}},
        {"context": {"workspace_id": FOREIGN}},
        {"query": f"?workspace_id={FOREIGN}"},
    ],
    ids=["nothing", "scope_id", "context_workspace", "query_workspace"],
)
def test_an_identity_without_a_verified_workspace_gets_no_workspace_threads(studio, requested) -> None:
    studio.authenticated()
    client = studio.client()
    headers = studio.token(tenant="tenant-own")
    request = {"context": requested.get("context"), "query": requested.get("query", "")}

    created = _message(
        client, "create_thread", headers, params={**WORKSPACE_THREAD, **requested.get("params", {})}, **request,
    )
    listed = _message(
        client, "list_threads", headers, params={"scope_type": "workspace", **requested.get("params", {})}, **request,
    )

    assert created.status_code == 403
    assert listed.status_code == 403
    assert studio.threads() == []


def test_an_identity_without_a_verified_workspace_keeps_app_threads(studio) -> None:
    studio.authenticated()
    created = _message(studio.client(), "create_thread", studio.token(), params={"participant_ids": ["teammate"]})

    assert created.status_code == 200, created.text
    assert created.json()["thread"]["scope_type"] == "app"


def test_workspace_threads_stay_in_the_verified_workspace(studio) -> None:
    studio.authenticated()
    client = studio.client()
    headers = studio.token(workspace=OWN)

    created = _message(client, "create_thread", headers, params=WORKSPACE_THREAD)
    assert created.status_code == 200, created.text
    thread_id = created.json()["thread"]["thread_id"]
    assert created.json()["thread"]["scope_id"] == OWN

    sent = _message(client, "send_message", headers, params={"thread_id": thread_id, "body": "hello"})
    fetched = _message(client, "get_thread", headers, params={"thread_id": thread_id})
    listed = _message(client, "list_threads", headers, params={"scope_type": "workspace"})
    named = _message(client, "list_threads", headers, params={"scope_type": "workspace", "scope_id": OWN})
    foreign = _message(client, "list_threads", headers, params={"scope_type": "workspace", "scope_id": FOREIGN})

    assert sent.status_code == 200 and sent.json()["success"] is True
    assert fetched.json()["messages"][0]["body"] == "hello"
    assert [thread["thread_id"] for thread in listed.json()["threads"]] == [thread_id]
    assert [thread["thread_id"] for thread in named.json()["threads"]] == [thread_id]
    assert foreign.status_code == 403
    assert {thread["scope_id"] for thread in studio.threads()} == {OWN}


def test_a_host_verified_membership_is_the_thread_workspace(studio) -> None:
    studio.authenticated()
    studio.hooks.register_bundle(
        {"module_scope_resolver": lambda **_scope: {"verified_workspace_id": "workspace-member"}}, source="test",
    )

    created = _message(studio.client(), "create_thread", studio.token(), params=WORKSPACE_THREAD)

    assert created.status_code == 200, created.text
    assert created.json()["thread"]["scope_id"] == "workspace-member"


@pytest.mark.parametrize("request_scope", [
    {"context": {"workspace_id": FOREIGN}},
    {"query": f"?workspace_id={FOREIGN}"},
    {"context": {"tenant_id": FOREIGN}},
    {"query": f"?tenant_id={FOREIGN}"},
], ids=["context_workspace", "query_workspace", "context_tenant", "query_tenant"])
def test_a_mismatched_thread_dispatch_emits_no_foreign_envelope(studio, request_scope) -> None:
    studio.authenticated()
    studio.hooks.register_bundle(
        {"module_scope_resolver": lambda **_scope: {"verified_workspace_id": OWN}}, source="test",
    )
    events: list[dict[str, Any]] = []

    async def capture(_event_type, envelope):
        events.append(envelope)

    studio.executor._event_emitter = capture
    client = studio.client()
    headers = studio.token()
    thread_params = {**WORKSPACE_THREAD, "subject_app_id": "default", "related_type": "test", "related_id": "test"}
    refused = _message(client, "create_thread", headers, params=thread_params, **request_scope)
    listed = _message(client, "list_threads", headers, params={"scope_type": "workspace"}, **request_scope)

    assert refused.status_code == listed.status_code == 403
    assert studio.threads() == []
    assert events == []

    created = _message(client, "create_thread", headers, params=thread_params, context={"workspace_id": OWN})
    assert created.status_code == 200, created.text
    assert len(events) == 1
    assert events[0]["tenant"]["workspace_id"] == OWN
    assert events[0]["payload"]["scope_id"] == OWN

    thread_id = created.json()["thread"]["thread_id"]
    by_id = _message(client, "send_message", headers, params={"thread_id": thread_id, "body": "hello"},
                     **request_scope)
    assert by_id.status_code == 403
    assert len(events) == 1


@pytest.mark.parametrize(
    ("request_scope", "expected"),
    [
        ({"params": {"scope_id": "workspace-dev"}}, "workspace-dev"),
        ({"context": {"workspace_id": "workspace-dev"}}, "workspace-dev"),
        ({}, None),
    ],
    ids=["scope_id", "context_workspace", "none"],
)
def test_local_development_keeps_its_thread_workspace_selection(studio, request_scope, expected) -> None:
    studio.development()
    created = _message(
        studio.client(LOCAL), "create_thread", params={**WORKSPACE_THREAD, **request_scope.get("params", {})},
        context=request_scope.get("context"),
    )

    assert created.status_code == 200, created.text
    assert created.json()["thread"]["scope_type"] == "workspace"
    assert created.json()["thread"].get("scope_id") == expected
