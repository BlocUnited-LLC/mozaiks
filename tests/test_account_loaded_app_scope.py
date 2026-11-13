"""Account routes bind real signed identities to the loaded app before dispatch."""
from __future__ import annotations

import asyncio
import json
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import FastAPI

from mozaiksai.core.account import AccountDataRegistry
from mozaiksai.core.auth.adapters.jwt_adapter import GenericJWTAdapter, JWTAdapterConfig
from mozaiksai.core.runtime.persistence import MongoPersistenceContext
from mozaiksai.hosts.routers import account as account_routes

APP = "loaded-account-app"
METHODS = [("GET", "/api/account/export"), ("DELETE", "/api/account")]
MISSING = object()


@pytest.fixture
def account_host(monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setenv("AUTH_PROVIDER", "jwt")
    monkeypatch.setenv("AUTH_AUDIENCE", APP)
    monkeypatch.setenv("AUTH_ISSUER", "https://account-scope.invalid")
    monkeypatch.setenv("AUTH_JWKS_URL", "https://account-scope.invalid/keys")
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key()))
    public.update(kid="account-scope-key", use="sig", alg="RS256")
    adapter = GenericJWTAdapter(JWTAdapterConfig(
        issuer="https://account-scope.invalid", jwks_url="https://account-scope.invalid/keys",
        audience=APP, user_id_claim="oid", required_scope="access_as_user", algorithms=["RS256"],
        clock_skew_seconds=0,
    ))
    # Supply only the external issuer's key retrieval; validation and principal
    # construction remain the actual JWT adapter and HTTP auth dependency.
    adapter._jwks_client = SimpleNamespace(get_signing_key=AsyncMock(return_value=public))
    monkeypatch.setattr("mozaiksai.core.auth.dependencies.get_auth_adapter", lambda: adapter)

    def headers(user="alice", **claims):
        now = datetime.now(UTC)
        token = jwt.encode({
            "iss": "https://account-scope.invalid", "aud": APP,
            "oid": user, "sub": f"pairwise-{user}", "scp": "access_as_user",
            "iat": now - timedelta(seconds=1), "nbf": now - timedelta(seconds=1),
            "exp": now + timedelta(minutes=5), **claims,
        }, key, algorithm="RS256", headers={"kid": "account-scope-key", "typ": "at+jwt"})
        return {"Authorization": f"Bearer {token}"}

    calls = []
    resources, principals = [], []
    database = object()

    def resolve_database(*args, **kwargs):
        calls.append("database")
        return SimpleNamespace(db=database)

    original = account_routes._account_persistence

    def resolve_persistence(request, principal, **kwargs):
        calls.append("persistence")
        before = deepcopy(vars(principal))
        resource = original(request, principal, **kwargs)
        assert isinstance(resource, MongoPersistenceContext)
        principals.append((principal, before))
        resources.append(resource)
        return resource

    class Handler:
        def __init__(self, db, persistence):
            calls.append("constructor")
            assert db is database and persistence is resources[-1]
            self.persistence = persistence

        def scope(self, app_id, user_id):
            return {
                "app_id": app_id, "user_id": user_id,
                "resource_app_id": self.persistence.app_id,
                "resource_user_id": self.persistence.principal.user_id,
                "resource_source": self.persistence.principal.source,
            }

        async def export_user_data(self, *, app_id, user_id):
            calls.append("export")
            return {"observed_scope": self.scope(app_id, user_id)}

        async def delete_user_data(self, *, app_id, user_id):
            calls.append("delete")
            return {"deleted_count": 0, **self.scope(app_id, user_id)}

    class Hooks:
        async def call_on_account_export_ready(self, **kwargs):
            calls.append("export_hook")
            return kwargs["export_payload"]

        async def call_on_account_delete_complete(self, **kwargs):
            calls.append("delete_hook")

    registry = AccountDataRegistry()
    registry.register("scope_probe", Handler)
    monkeypatch.setattr(account_routes, "account_data_registry", registry)
    monkeypatch.setattr(account_routes, "app_data_from_context", resolve_database)
    monkeypatch.setattr(account_routes, "_account_persistence", resolve_persistence)
    monkeypatch.setattr(account_routes, "get_platform_hooks", Hooks)
    app = FastAPI()
    app.state.loaded_app_id = APP
    app.state.data_contract = {"version": "1", "surfaces": []}
    app.include_router(account_routes.router)
    return SimpleNamespace(app=app, headers=headers, calls=calls, resources=resources, principals=principals)


async def request(host, method, path, *, headers=None):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=host.app), base_url="http://test") as client:
        options = {"headers": headers or {}, "params": {"app_id": "foreign", "user_id": "mallory", "module_id": "foreign"}}
        if method == "DELETE":
            options["json"] = {"app_id": "foreign", "user_id": "mallory", "module_id": "foreign"}
        return await client.request(method, path, **options)


@pytest.mark.parametrize("method,path", METHODS)
async def test_no_app_claim_uses_loaded_scope_without_changing_authenticated_identity(account_host, method, path):
    host = account_host
    response = await request(host, method, path, headers=host.headers())
    assert response.status_code == 200
    payload = response.json()
    scope = payload["observed_scope"] if method == "GET" else payload["results"]["scope_probe"]
    assert scope == {**({"deleted_count": 0} if method == "DELETE" else {}),
                     "app_id": APP, "user_id": "alice", "resource_app_id": APP,
                     "resource_user_id": "alice", "resource_source": "authenticated"}
    assert host.calls == ["database", "persistence", "constructor", "export" if method == "GET" else "delete",
                          "export_hook" if method == "GET" else "delete_hook"]
    (principal, before), = host.principals
    assert vars(principal) == before and principal.app_id is None
    assert "app_id" not in principal.raw_claims and "mozaiks_app_id" not in principal.raw_claims


@pytest.mark.parametrize("method,path", METHODS)
@pytest.mark.parametrize("identity", [MISSING, None, "", "  ", 123, True, {}, [], " padded "])
async def test_missing_or_malformed_loaded_identity_refuses_before_resources_and_hooks(account_host, method, path, identity):
    host = account_host
    if identity is MISSING:
        del host.app.state.loaded_app_id
    else:
        host.app.state.loaded_app_id = identity
    response = await request(host, method, path, headers=host.headers())
    assert response.status_code == 503
    assert host.calls == [] and host.resources == [] and host.principals == []


@pytest.mark.parametrize("method,path", METHODS)
@pytest.mark.parametrize("claim", ["app_id", "mozaiks_app_id"])
async def test_foreign_token_app_cannot_select_another_app_before_registry_dispatch(account_host, method, path, claim):
    host = account_host
    response = await request(host, method, path, headers=host.headers(**{claim: "another-app"}))
    assert response.status_code == 403
    assert host.calls == [] and host.resources == [] and host.principals == []


@pytest.mark.parametrize("method,path", METHODS)
async def test_unauthenticated_account_requests_do_not_resolve_resources(account_host, method, path):
    host = account_host
    assert (await request(host, method, path)).status_code == 401
    assert host.calls == []


async def test_concurrent_verified_users_keep_distinct_account_resources(account_host):
    host = account_host
    responses = await asyncio.gather(*(request(host, "GET", "/api/account/export", headers=host.headers(user))
                                      for user in ("alice", "bob")))
    assert all(response.status_code == 200 for response in responses)
    scopes = [response.json()["observed_scope"] for response in responses]
    assert [(scope["app_id"], scope["user_id"], scope["resource_app_id"], scope["resource_user_id"])
            for scope in scopes] == [(APP, user, APP, user) for user in ("alice", "bob")]
    assert len({id(resource) for resource in host.resources}) == 2
    assert all(vars(principal) == before and principal.app_id is None for principal, before in host.principals)
