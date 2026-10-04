"""Entitlement lookups are keyed only by the verified identity.

Signed tokens through the real JWT adapter, module router, executor and
ConfiguredEntitlementAdapter. Only the JWKS transport and the assignment store
are replaced. A tenant or workspace named by the request never selects a plan;
the tenant or workspace a validated token is bound to does.
"""
from __future__ import annotations

import json
import time
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import FastAPI
from fastapi.testclient import TestClient

from mozaiksai.core.auth.adapters.jwt_adapter import GenericJWTAdapter, JWTAdapterConfig
from mozaiksai.core.auth.dependencies import UserPrincipal
from mozaiksai.core.auth.websocket_auth import WebSocketUser
from mozaiksai.core.runtime.app.entitlements import ConfiguredEntitlementAdapter
from mozaiksai.core.runtime.app.subscriptions_loader import SubscriptionsConfig
from mozaiksai.core.runtime.composition.module_executor import ModuleExecutor, ModuleRequest
from mozaiksai.core.runtime.composition.platform_hooks import PlatformHookRegistry
from mozaiksai.core.runtime.persistence.adapter import PersistencePrincipal
from mozaiksai.hosts.routers import modules as module_router
from tests.module_authority_test_helpers import enforce_authority, trusted_framework_authority

APP_ID = "entitlement-identity-app"
GATE = "reports.export"
URL = "/api/modules/reports/export_report"


class _Assignments:
    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []

    def add(self, *, user_id=None, tenant_id=None, workspace_id=None, plan_id="pro") -> None:
        self.rows.append({
            "app_id": APP_ID, "user_id": user_id, "tenant_id": tenant_id,
            "workspace_id": workspace_id, "plan_id": plan_id, "status": "active",
        })

    async def find_one(self, query, projection=None, **kwargs):
        for row in self.rows:
            if all(row.get(key) == value for key, value in query.items()):
                return dict(row)
        return None


class _Reports:
    def __init__(self) -> None:
        self.calls = 0

    async def export_report(self, ctx, **_params):
        self.calls += 1
        return {"exported": True}


def _config(*, workspace_store: bool) -> SubscriptionsConfig:
    store: dict[str, Any] = {"data_alias": "billing.subscriptions", "user_id_field": "user_id"}
    if workspace_store:
        store["workspace_id_field"] = "workspace_id"
    return SubscriptionsConfig.model_validate({
        "schema_version": "mozaiks.subscriptions.v1",
        "label": "Identity test",
        "default_plan_id": "free",
        "assignment_store": store,
        "plans": [
            {"plan_id": "free", "label": "Free", "capabilities": ["reports.view"]},
            {"plan_id": "pro", "label": "Pro", "capabilities": ["reports.view", GATE]},
        ],
    })


@pytest.fixture
def runtime(monkeypatch):
    monkeypatch.setenv("ENV", "test")
    monkeypatch.setenv("ENVIRONMENT", "test")
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setenv("AUTH_PROVIDER", "jwt")
    monkeypatch.setenv("AUTH_AUDIENCE", "entitlement-test")
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key()))
    jwk.update({"kid": "entitlement-test", "alg": "RS256", "use": "sig"})
    adapter = GenericJWTAdapter(config=JWTAdapterConfig(
        jwks_url="https://auth.test/jwks", issuer="https://auth.test", audience="entitlement-test",
    ))
    monkeypatch.setattr(adapter, "_get_jwks_client_async", AsyncMock(return_value=SimpleNamespace(
        get_signing_key=AsyncMock(return_value=jwk),
    )))
    monkeypatch.setattr("mozaiksai.core.auth.dependencies.get_auth_adapter", lambda: adapter)
    monkeypatch.setattr(module_router, "record_action_invocation", lambda **kwargs: None)
    hooks = PlatformHookRegistry()
    monkeypatch.setattr(module_router, "get_platform_hooks", lambda: hooks)
    monkeypatch.setattr("mozaiksai.core.runtime.composition.module_executor.get_platform_hooks", lambda: hooks)
    monkeypatch.setattr(ModuleExecutor, "_emit_dispatch_audit", AsyncMock())
    assignments = _Assignments()
    handler = _Reports()

    def token(user, *, tenant=None, workspace=None):
        claims = {"sub": user, "iss": "https://auth.test", "aud": "entitlement-test",
                  "app_id": APP_ID, "exp": int(time.time()) + 300}
        if tenant is not None:
            claims["tid"] = tenant
        if workspace is not None:
            claims["workspace_id"] = workspace
        return {"Authorization": "Bearer " + jwt.encode(
            claims, key, algorithm="RS256", headers={"kid": "entitlement-test", "typ": "at+jwt"},
        )}

    def executor(*, workspace_store=False):
        checker = ConfiguredEntitlementAdapter(
            config=_config(workspace_store=workspace_store),
            collection_resolver=lambda alias: assignments,
        )
        module_executor = ModuleExecutor(entitlement_checker=checker)
        module_executor.register(
            "reports", handler,
            action_method_map={"export_report": "export_report"},
            action_entitlements={"export_report": GATE},
        )
        return module_executor

    def client(*, workspace_store=False):
        app = FastAPI()
        app.state.module_action_surfaces = {"reports": {"export_report": None}}
        app.state.executor_registry = SimpleNamespace(module_executor=executor(workspace_store=workspace_store))
        app.include_router(module_router.router)
        return TestClient(app)

    return SimpleNamespace(
        client=client, executor=executor, token=token, assignments=assignments, handler=handler,
    )


def _export(client, headers, *, context=None, query=None, **params):
    body: dict[str, Any] = {"params": params}
    if context:
        body["context"] = context
    return client.post(URL, params=query or None, json=body, headers=headers)


def _refused(response) -> bool:
    return response.status_code == 402 and response.json()["detail"]["error_code"] == "ENTITLEMENT_REQUIRED"


@pytest.mark.parametrize("form", ["context", "query", "params"])
def test_requested_tenant_does_not_select_a_tenant_plan(runtime, form):
    client = runtime.client()
    runtime.assignments.add(tenant_id="t-paid")
    unbound = runtime.token("dave")
    named = {
        "context": {"context": {"tenant_id": "t-paid"}},
        "query": {"query": {"tenant_id": "t-paid"}},
        "params": {"tenant_id": "t-paid"},
    }[form]

    assert _refused(_export(client, unbound))
    assert _refused(_export(client, unbound, **named))
    assert runtime.handler.calls == 0


def test_token_bound_to_the_paying_tenant_holds_its_plan(runtime):
    client = runtime.client()
    runtime.assignments.add(tenant_id="t-paid")
    member = runtime.token("mia", tenant="t-paid")

    plain = _export(client, member)
    named = _export(client, member, context={"tenant_id": "t-paid"})

    assert plain.status_code == 200, plain.text
    assert named.status_code == 200, named.text
    assert runtime.handler.calls == 2


def test_token_bound_to_another_tenant_is_refused(runtime):
    client = runtime.client()
    runtime.assignments.add(tenant_id="t-paid")
    outsider = runtime.token("erin", tenant="t-other")

    mismatch = _export(client, outsider, context={"tenant_id": "t-paid"})

    assert mismatch.status_code == 403
    assert _refused(_export(client, outsider))
    assert runtime.handler.calls == 0


@pytest.mark.parametrize("form", ["context", "query", "params"])
def test_requested_workspace_does_not_select_a_workspace_plan(runtime, form):
    client = runtime.client(workspace_store=True)
    runtime.assignments.add(workspace_id="ws-paid")
    unbound = runtime.token("oscar")
    named = {
        "context": {"context": {"workspace_id": "ws-paid"}},
        "query": {"query": {"workspace_id": "ws-paid"}},
        "params": {"workspace_id": "ws-paid"},
    }[form]

    assert _refused(_export(client, unbound, **named))
    assert runtime.handler.calls == 0


def test_workspace_bound_tokens_hold_only_their_workspace_plan(runtime):
    client = runtime.client(workspace_store=True)
    runtime.assignments.add(workspace_id="ws-paid")

    member = _export(client, runtime.token("quin", workspace="ws-paid"))
    mismatch = _export(client, runtime.token("pat", workspace="ws-other"), context={"workspace_id": "ws-paid"})

    assert member.status_code == 200, member.text
    assert mismatch.status_code == 403
    assert runtime.handler.calls == 1


def test_user_level_assignments_are_unchanged(runtime):
    client = runtime.client()
    runtime.assignments.add(user_id="bob")

    payer = _export(client, runtime.token("bob"))
    payer_in_a_tenant = _export(client, runtime.token("bob", tenant="t-any"))
    other = _export(client, runtime.token("carl"))
    named_payer = _export(client, runtime.token("carl"), context={"user_id": "bob"})

    assert payer.status_code == 200, payer.text
    assert payer_in_a_tenant.status_code == 200, payer_in_a_tenant.text
    assert _refused(other)
    assert named_payer.status_code == 403
    assert runtime.handler.calls == 2


@pytest.mark.asyncio
async def test_trusted_server_owned_dispatch_is_unchanged(runtime):
    executor = runtime.executor()

    result = await executor.execute(ModuleRequest(
        module="reports", action="export_report", app_id=APP_ID, user_id="nobody",
        authority=trusted_framework_authority(),
    ))

    assert result.success is True
    assert runtime.handler.calls == 1


@pytest.mark.asyncio
async def test_dispatch_without_a_verified_identity_holds_only_app_wide_grants(runtime):
    executor = runtime.executor()
    runtime.assignments.add(user_id="bob")
    runtime.assignments.add(tenant_id="t-paid")

    stated = await executor.execute(ModuleRequest(
        module="reports", action="export_report", app_id=APP_ID, user_id="bob", tenant_id="t-paid",
        authority=enforce_authority(kind="app_internal"),
    ))
    verified = await executor.execute(ModuleRequest(
        module="reports", action="export_report", app_id=APP_ID, user_id="bob",
        authority=enforce_authority(kind="app_internal"),
        persistence_principal=PersistencePrincipal(user_id="bob"),
    ))

    assert stated.error_code == "ENTITLEMENT_REQUIRED"
    assert verified.success is True
    assert runtime.handler.calls == 1


def test_principal_captures_the_tenant_its_credential_is_bound_to(monkeypatch):
    monkeypatch.setenv("ENV", "test")
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setenv("AUTH_PROVIDER", "jwt")
    monkeypatch.setenv("AUTH_JWKS_URL", "https://auth.test/jwks")
    monkeypatch.setenv("AUTH_ISSUER", "https://auth.test")
    monkeypatch.setenv("AUTH_AUDIENCE", "entitlement-test")
    http_user = UserPrincipal(
        user_id="mia", email=None, name=None, roles=[], scopes=[], raw_claims={},
        tenant_id="t-paid", workspace_id="ws-paid", auth_provenance="token_validated",
    )
    socket_user = WebSocketUser(
        user_id="mia", email=None, name=None, roles=[], scopes=[],
        raw_claims={"exp": int(time.time()) + 300}, provider="jwt", tenant_id="t-paid",
    )
    unbound = UserPrincipal(
        user_id="dave", email=None, name=None, roles=[], scopes=[], raw_claims={},
        tenant_id="", auth_provenance="token_validated",
    )

    http_principal = PersistencePrincipal.from_authenticated_user(http_user)
    socket_principal = PersistencePrincipal.from_websocket_user(socket_user)

    assert http_principal == PersistencePrincipal(user_id="mia", workspace_id="ws-paid", tenant_id="t-paid")
    assert socket_principal == PersistencePrincipal(user_id="mia", tenant_id="t-paid")
    assert PersistencePrincipal.from_authenticated_user(unbound) == PersistencePrincipal(user_id="dave")
    assert http_principal.with_host_scope({"_verified_workspace_id": "ws-host"}) == PersistencePrincipal(
        user_id="mia", workspace_id="ws-host", tenant_id="t-paid",
    )
