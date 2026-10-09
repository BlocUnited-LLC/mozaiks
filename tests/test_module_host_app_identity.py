"""Signed HTTP proof that module execution belongs to the loaded app bundle."""

from __future__ import annotations

import json
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import FastAPI
from fastapi.testclient import TestClient

from mozaiksai.core.auth.adapters import registry as auth_registry
from mozaiksai.core.auth.adapters.jwt_adapter import GenericJWTAdapter, JWTAdapterConfig
from mozaiksai.core.auth.config import clear_auth_config_cache
from mozaiksai.core.runtime.composition.executor_registry import ExecutorRegistry
from mozaiksai.core.runtime.composition.module_executor import ModuleExecutor
from mozaiksai.core.runtime.composition.platform_hooks import PlatformHookRegistry
from mozaiksai.core.runtime.persistence import PersistencePrincipal
from mozaiksai.hosts.routers import modules as module_router


class _ProbeHandler:
    async def scope(self, ctx):
        return {"execution_app_id": ctx.app_id}

    async def inspect_target(self, ctx, *, app_id: str):
        return {"execution_app_id": ctx.app_id, "target_app_id": app_id}


@pytest.fixture
def signed_module_host(monkeypatch):
    for name in auth_registry._ALL_AUTH_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    for name, value in {
        "ENV": "test", "AUTH_ENABLED": "true", "AUTH_PROVIDER": "jwt",
        "AUTH_ISSUER": "https://auth.test", "AUTH_AUDIENCE": "module-host-test",
        "AUTH_JWKS_URL": "https://auth.test/jwks",
    }.items():
        monkeypatch.setenv(name, value)
    clear_auth_config_cache()
    auth_registry.reset_auth_adapter()

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key()))
    jwk.update({"kid": "module-host", "alg": "RS256", "use": "sig"})
    adapter = GenericJWTAdapter(config=JWTAdapterConfig(
        jwks_url="https://auth.test/jwks", issuer="https://auth.test", audience="module-host-test",
    ))
    monkeypatch.setattr(adapter, "_get_jwks_client_async", AsyncMock(return_value=SimpleNamespace(
        get_signing_key=AsyncMock(return_value=jwk),
    )))
    monkeypatch.setattr("mozaiksai.core.auth.dependencies.get_auth_adapter", lambda: adapter)

    def token(app_id: str | None = None) -> dict[str, str]:
        claims = {
            "sub": "signed-user", "iss": "https://auth.test", "aud": "module-host-test",
            "exp": int(time.time()) + 300,
        }
        if app_id is not None:
            claims["app_id"] = app_id
        return {"Authorization": "Bearer " + jwt.encode(
            claims, key, algorithm="RS256", headers={"kid": "module-host", "typ": "at+jwt"},
        )}

    executor = ModuleExecutor()
    executor.register(
        "probe", _ProbeHandler(),
        action_method_map={"scope": "scope", "inspect_target": "inspect_target"},
        action_schemas={
            "inspect_target": {
                "input": {
                    "type": "object", "required": ["app_id"],
                    "properties": {"app_id": {"type": "string"}},
                },
            },
        },
    )
    registry = ExecutorRegistry()
    registry.register(executor)
    hooks = PlatformHookRegistry()
    app = FastAPI()
    app.state.loaded_app_id = "host-app"
    app.state.executor_registry = registry
    app.state.failed_module_names = []
    app.state.module_action_surfaces = {"probe": {"scope": "public", "inspect_target": "public"}}
    app.include_router(module_router.router)
    app.dependency_overrides[module_router.module_dispatch_environment] = lambda: (
        module_router.ModuleDispatchEnvironment(
            authentication_enabled=True,
            platform_hooks=hooks,
            record_invocation=lambda **_kwargs: None,
            persistence_principal=PersistencePrincipal.from_authenticated_user,
        )
    )
    try:
        with TestClient(app) as client:
            yield SimpleNamespace(client=client, token=token, app=app, hooks=hooks)
    finally:
        clear_auth_config_cache()
        auth_registry.reset_auth_adapter()


def test_claimless_access_token_uses_loaded_host_and_cannot_select_foreign_app(signed_module_host):
    client = signed_module_host.client
    claimless = signed_module_host.token()
    assert client.get("/api/modules/probe/scope", headers=claimless).json() == {
        "execution_app_id": "host-app",
    }
    assert client.get("/api/modules/probe/scope?app_id=foreign-app", headers=claimless).status_code == 403
    assert client.post(
        "/api/modules/probe/scope", headers=claimless,
        json={"params": {}, "context": {"app_id": "foreign-app"}},
    ).status_code == 403
    assert client.post(
        "/api/modules/probe/scope?app_id=foreign-app", headers=claimless,
        json={"params": {}, "context": {"app_id": "host-app"}},
    ).status_code == 403


def test_business_target_app_id_does_not_replace_execution_app(signed_module_host):
    response = signed_module_host.client.post(
        "/api/modules/probe/inspect_target", headers=signed_module_host.token(),
        json={"app_id": "target-app"},
    )
    assert response.status_code == 200, response.text
    assert response.json() == {"execution_app_id": "host-app", "target_app_id": "target-app"}


def test_token_or_host_hook_cannot_retarget_loaded_app(signed_module_host):
    client = signed_module_host.client
    assert client.get(
        "/api/modules/probe/scope", headers=signed_module_host.token("foreign-app"),
    ).status_code == 403

    signed_module_host.hooks.register_bundle({
        "module_scope_resolver": lambda **_kwargs: {"app_id": "foreign-app"},
    }, source="foreign-scope")
    assert client.get(
        "/api/modules/probe/scope", headers=signed_module_host.token(),
    ).status_code == 403


def test_missing_loaded_app_identity_refuses_module_dispatch(signed_module_host):
    signed_module_host.app.state.loaded_app_id = None
    assert signed_module_host.client.get(
        "/api/modules/probe/scope", headers=signed_module_host.token(),
    ).status_code == 503
