"""Workspace connector actions act only on the caller's verified workspace.

Signed tokens through the real JWT adapter, module router and executor, with
the Studio ``workspace_integrations`` module loaded from its own module.yaml.
Only the JWKS transport, the connector store, the vault backend and the app
declarations repository are replaced, so every assertion reads the workspace
that reached the connector layer.

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
MODULE = "workspace_integrations"
SCOPES = "workspace_integrations.read workspace_integrations.manage"
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

    def _record(self, scope_id: str, scope: str, *, secret: bool = False, **extra: Any) -> dict[str, Any]:
        return {
            "scope": scope, "scope_id": scope_id, "service": "openai", "display_name": "OpenAI",
            "status": "active", "secret_available": secret, "public_config": {}, "required_fields": [],
            **extra,
        }

    async def list(self, *, scope, scope_id):
        self.calls.append(("list", scope, scope_id))
        return [self._record(scope_id, scope)]

    async def get(self, *, scope, scope_id, service):
        self.calls.append(("get", scope, scope_id))
        return self._record(scope_id, scope, secret=True)

    async def upsert(self, *, scope, scope_id, service, **fields):
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

    async def store_secret(self, *, scope_id, service, secret_value, display_name=None, ttl_days=30):
        self.scope_ids.append(scope_id)
        return {"success": True, "provider": "test-vault"}

    async def delete_secret(self, *, scope_id, service):
        self.scope_ids.append(scope_id)
        return {"success": True}


class _Declarations:
    async def get_for_app(self, *, app_id):
        return [{"app_id": app_id, "service": "openai", "display_name": "OpenAI", "kind": "api_key",
                 "connector_status": "not_configured", "required_at": "runtime", "optional": False}]


@pytest.fixture
def studio(monkeypatch):
    """A Studio module router serving the real workspace_integrations module."""
    monkeypatch.setenv("ENV", "test")
    monkeypatch.setenv("ENVIRONMENT", "test")
    store = _ConnectorStore()
    vault = _Vault()
    monkeypatch.setattr(connector_service, "_get_store", lambda store_arg=None: store)
    monkeypatch.setattr(connector_service, "get_connector_vault_backend", lambda: vault)
    monkeypatch.setattr(connector_health, "ConnectorStore", _StoreClass(store))
    monkeypatch.setattr(module_router, "record_action_invocation", lambda **_: None)
    hooks = PlatformHookRegistry()
    monkeypatch.setattr(module_router, "get_platform_hooks", lambda: hooks)
    monkeypatch.setattr("mozaiksai.core.runtime.composition.module_executor.get_platform_hooks", lambda: hooks)
    monkeypatch.setattr(ModuleExecutor, "_emit_dispatch_audit", AsyncMock())

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key()))
    jwk.update({"kid": "connector-scope", "alg": "RS256", "use": "sig"})

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

    def token(*, workspace: str | None = None, tenant: str | None = None, scopes: str = SCOPES) -> dict[str, str]:
        claims: dict[str, Any] = {"sub": "studio-user", "iss": "https://auth.test", "aud": "studio-api",
                                  "exp": int(time.time()) + 300, "scp": scopes}
        if workspace is not None:
            claims["workspace_id"] = workspace
        if tenant is not None:
            claims["tid"] = tenant
        return {"Authorization": "Bearer " + jwt.encode(
            claims, key, algorithm="RS256", headers={"kid": "connector-scope", "typ": "at+jwt"},
        )}

    loaded = ModuleLoader(str(STUDIO_APP)).load(MODULE)
    loaded.handler.service.declarations_repo = _Declarations()
    executor = ModuleExecutor()
    executor.register_loaded_module(loaded)
    app = FastAPI()
    app.state.module_action_surfaces = {MODULE: loaded.action_api_surface_map}
    app.state.executor_registry = SimpleNamespace(module_executor=executor)
    app.include_router(module_router.router)

    def client(peer: tuple[str, int] = REMOTE) -> TestClient:
        return TestClient(app, raise_server_exceptions=False, client=peer, base_url="http://localhost:8000")

    return SimpleNamespace(
        store=store, vault=vault, hooks=hooks, token=token, client=client,
        authenticated=authenticated, development=development,
    )


def _post(client: TestClient, action: str, headers: dict[str, str] | None = None, *,
          params: dict[str, Any] | None = None, context: dict[str, Any] | None = None):
    body: dict[str, Any] = {"params": params or {}}
    if context is not None:
        body["context"] = context
    return client.post(f"/api/modules/{MODULE}/{action}", json=body, headers=headers or {})


# One request per connector action: what each needs besides a workspace.
CONNECTOR_ACTIONS = {
    "list_workspace_connectors": {},
    "save_workspace_connector": {"service": "openai", "secret_value": "placeholder-value"},
    "check_workspace_connector_health": {"service": "openai"},
    "delete_workspace_connector": {"service": "openai"},
}


@pytest.mark.parametrize("action", sorted(CONNECTOR_ACTIONS))
def test_a_matching_context_does_not_let_the_action_target_another_workspace(studio, action) -> None:
    studio.authenticated()
    response = _post(
        studio.client(), action, studio.token(workspace=OWN, tenant="tenant-own"),
        params={**CONNECTOR_ACTIONS[action], "workspace_id": FOREIGN},
        context={"workspace_id": OWN, "tenant_id": "tenant-own"},
    )

    assert response.status_code == 403
    assert response.json()["detail"]["error_code"] == "PERMISSION_DENIED"
    assert studio.store.calls == []
    assert studio.vault.scope_ids == []


@pytest.mark.parametrize("action", sorted(CONNECTOR_ACTIONS))
def test_a_conflicting_target_alone_is_refused_before_dispatch(studio, action) -> None:
    studio.authenticated()
    response = _post(
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
        ({"query": f"workspace_id={FOREIGN}"}, "tenant-own"),
        ({"query": f"tenant_id={FOREIGN}"}, None),
    ],
    ids=["nothing", "param", "context_workspace", "context_tenant", "query_workspace", "query_tenant"],
)
@pytest.mark.parametrize("action", sorted(CONNECTOR_ACTIONS))
def test_an_identity_without_a_verified_workspace_is_refused(studio, action, requested, token_tenant) -> None:
    studio.authenticated()
    headers = studio.token(tenant=token_tenant)
    params = {**CONNECTOR_ACTIONS[action], **requested.get("params", {})}
    client = studio.client()
    if "query" in requested:
        response = client.post(
            f"/api/modules/{MODULE}/{action}?{requested['query']}", json={"params": params}, headers=headers,
        )
    else:
        response = _post(client, action, headers, params=params, context=requested.get("context"))

    assert response.status_code == 403
    assert studio.store.calls == []
    assert studio.vault.scope_ids == []


def test_an_unverified_get_query_cannot_choose_a_workspace(studio) -> None:
    studio.authenticated()
    client = studio.client()
    for query in (f"workspace_id={FOREIGN}", f"tenant_id={FOREIGN}"):
        response = client.get(f"/api/modules/{MODULE}/list_workspace_connectors?{query}", headers=studio.token())
        assert response.status_code == 403
    assert studio.store.calls == []


@pytest.mark.parametrize("action", sorted(CONNECTOR_ACTIONS))
@pytest.mark.parametrize("name_it", [False, True], ids=["default", "named"])
def test_the_callers_own_workspace_is_served(studio, action, name_it) -> None:
    studio.authenticated()
    params = {**CONNECTOR_ACTIONS[action], **({"workspace_id": OWN} if name_it else {})}
    response = _post(studio.client(), action, studio.token(workspace=OWN, tenant="tenant-own"), params=params)

    assert response.status_code == 200, response.text
    assert studio.store.calls
    assert studio.store.scope_ids() == {OWN}
    assert set(studio.vault.scope_ids) <= {OWN}
    if action == "save_workspace_connector":
        assert studio.vault.scope_ids == [OWN]


def test_listing_returns_only_the_callers_workspace_connectors(studio) -> None:
    studio.authenticated()
    response = _post(
        studio.client(), "list_workspace_connectors", studio.token(workspace=OWN),
        context={"tenant_id": FOREIGN},
    )

    assert response.status_code == 200
    assert [connector["scope_id"] for connector in response.json()["connectors"]] == [OWN]


def test_a_host_verified_membership_is_the_workspace(studio) -> None:
    studio.authenticated()
    studio.hooks.register_bundle(
        {"module_scope_resolver": lambda **_scope: {"verified_workspace_id": "workspace-member"}}, source="test",
    )
    client = studio.client()

    assert _post(client, "list_workspace_connectors", studio.token()).status_code == 200
    assert _post(client, "list_workspace_connectors", studio.token(workspace=OWN)).status_code == 200
    assert studio.store.scope_ids() == {"workspace-member"}

    refused = _post(client, "list_workspace_connectors", studio.token(), params={"workspace_id": OWN})
    assert refused.status_code == 403
    assert studio.store.scope_ids() == {"workspace-member"}


def test_a_host_revoking_the_workspace_leaves_none(studio) -> None:
    studio.authenticated()
    studio.hooks.register_bundle(
        {"module_scope_resolver": lambda **_scope: {"verified_workspace_id": None}}, source="test",
    )

    response = _post(studio.client(), "list_workspace_connectors", studio.token(workspace=OWN))

    assert response.status_code == 403
    assert studio.store.calls == []


def test_an_unverified_host_scope_does_not_select_the_workspace(studio) -> None:
    studio.authenticated()
    studio.hooks.register_bundle(
        {"module_scope_resolver": lambda **_scope: {"workspace_id": FOREIGN, "tenant_id": FOREIGN}}, source="test",
    )
    client = studio.client()

    assert _post(client, "list_workspace_connectors", studio.token(workspace=OWN)).status_code == 200
    assert studio.store.scope_ids() == {OWN}
    assert _post(client, "list_workspace_connectors", studio.token()).status_code == 403
    assert studio.store.scope_ids() == {OWN}


def test_app_integration_needs_overlay_only_the_verified_workspace(studio) -> None:
    studio.authenticated()
    client = studio.client()

    unbound = _post(
        client, "list_app_integration_needs", studio.token(), params={"app_id": "built-app"},
        context={"workspace_id": FOREIGN, "tenant_id": FOREIGN},
    )
    assert unbound.status_code == 200, unbound.text
    assert unbound.json()["declarations"][0]["connector_status"] == "not_configured"
    assert studio.store.calls == []

    bound = _post(client, "list_app_integration_needs", studio.token(workspace=OWN), params={"app_id": "built-app"})
    assert bound.status_code == 200
    assert bound.json()["declarations"][0]["workspace_connector_status"] == "partial"
    assert studio.store.calls == [("list", ConnectorStore.SCOPE_WORKSPACE, OWN)]


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
def test_local_development_keeps_its_explicit_selection(studio, request_scope, expected) -> None:
    studio.development()
    for action, params in CONNECTOR_ACTIONS.items():
        response = _post(
            studio.client(LOCAL), action, params={**params, **request_scope.get("params", {})},
            context=request_scope.get("context"),
        )
        assert response.status_code == 200, (action, response.text)
    assert studio.store.scope_ids() == {expected}
    assert set(studio.vault.scope_ids) == {expected}

    studio.store.calls.clear()
    overlay = _post(studio.client(LOCAL), "list_app_integration_needs", params={"app_id": "built-app"},
                    context=request_scope.get("context"))
    assert overlay.status_code == 200
    overlay_expected = expected if "params" not in request_scope else "demo-workspace"
    assert studio.store.scope_ids() == {overlay_expected}


def test_a_remote_visitor_without_development_access_gets_nothing(studio) -> None:
    studio.development()
    response = _post(studio.client(REMOTE), "list_workspace_connectors", params={"workspace_id": FOREIGN})

    assert response.status_code == 403
    assert studio.store.calls == []
