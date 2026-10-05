"""Entitlement lookups are keyed only by the verified identity.

Signed tokens through the real JWT adapter, module router, executor and
ConfiguredEntitlementAdapter. Only the JWKS transport and the assignment store
are replaced. A tenant or workspace named by the request never selects a plan;
the tenant or workspace a validated token is bound to does, unless a host's
scope hook replaces it with a membership it verified (``verified_tenant_id`` /
``verified_workspace_id``).
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

from mozaiksai.core.auth.adapters.base import UserClaims
from mozaiksai.core.auth.adapters.jwt_adapter import GenericJWTAdapter, JWTAdapterConfig
from mozaiksai.core.auth.dependencies import UserPrincipal
from mozaiksai.core.auth.websocket_auth import WebSocketUser
from mozaiksai.core.runtime.app.entitlements import ConfiguredEntitlementAdapter
from mozaiksai.core.runtime.app.subscriptions_loader import SubscriptionsConfig
from mozaiksai.core.runtime.composition.module_executor import ModuleExecutor, ModuleRequest
from mozaiksai.core.runtime.composition.platform_hooks import PlatformHookRegistry
from mozaiksai.core.runtime.persistence.adapter import PersistencePrincipal
from mozaiksai.hosts.routers import admin_modules as admin_module_router
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

    def token(user, *, tenant=None, workspace=None, roles=None):
        claims = {"sub": user, "iss": "https://auth.test", "aud": "entitlement-test",
                  "app_id": APP_ID, "exp": int(time.time()) + 300}
        if tenant is not None:
            claims["tid"] = tenant
        if workspace is not None:
            claims["workspace_id"] = workspace
        if roles is not None:
            claims["roles"] = roles
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

    def client(*, workspace_store=False, surface=None):
        app = FastAPI()
        app.state.module_action_surfaces = {"reports": {"export_report": surface}}
        app.state.executor_registry = SimpleNamespace(module_executor=executor(workspace_store=workspace_store))
        app.include_router(module_router.router)
        app.include_router(admin_module_router.router)
        return TestClient(app)

    return SimpleNamespace(
        client=client, executor=executor, token=token, assignments=assignments, handler=handler,
        hooks=hooks,
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


def test_host_verified_workspace_does_not_verify_a_requested_tenant(runtime):
    runtime.hooks.register_bundle(
        {"module_scope_resolver": lambda **_scope: {"verified_workspace_id": "ws-own"}}, source="test",
    )
    client = runtime.client()
    runtime.assignments.add(tenant_id="t-paid")
    unbound = runtime.token("nora")

    assert _refused(_export(client, unbound, context={"tenant_id": "t-paid"}))
    assert _refused(_export(client, unbound, query={"tenant_id": "t-paid"}))
    assert runtime.handler.calls == 0
    assert _export(client, runtime.token("mia", tenant="t-paid")).status_code == 200


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
    assert PersistencePrincipal(user_id="dave").with_host_scope(
        {"_verified_workspace_id": "ws-1", "tenant_id": "t-paid"},
    ) == PersistencePrincipal(user_id="dave", workspace_id="ws-1")


# A host-verified tenant: the scope hook returns ``verified_tenant_id``.

# Host-owned memberships. The hooks below look a caller up by the
# authenticated principal only, never by anything the request names.
_HOST_TENANTS = {"maya": "t-paid", "olga": "t-paid"}


def _membership_hook(**scope):
    tenant = _HOST_TENANTS.get(scope["principal"].user_id)
    return {"verified_tenant_id": tenant} if tenant else {}


def _plain_tenant_hook(**scope):
    return {"tenant_id": _HOST_TENANTS.get(scope["principal"].user_id)}


def _audits() -> list[Any]:
    return [call.args[0] for call in ModuleExecutor._emit_dispatch_audit.call_args_list]


def _socket_user(user: str) -> WebSocketUser:
    return WebSocketUser.from_claims(UserClaims(
        user_id=user, app_id=APP_ID, provider="jwt", raw_claims={"exp": int(time.time()) + 300},
    ))


@pytest.mark.parametrize("token_tenant", [None, "idp-directory"])
def test_host_verified_tenant_lets_a_member_pass_a_tenant_gate(runtime, token_tenant):
    runtime.hooks.register_bundle({"module_scope_resolver": _membership_hook}, source="test")
    client = runtime.client()
    runtime.assignments.add(tenant_id="t-paid")

    member = _export(client, runtime.token("maya", tenant=token_tenant))
    outsider = _export(client, runtime.token("nora", tenant=token_tenant))

    assert member.status_code == 200, member.text
    assert _refused(outsider)
    assert runtime.handler.calls == 1
    # The audit records the dispatch tenant, not the verified one.
    granted = next(audit for audit in _audits() if audit.actor_id == "maya")
    assert granted.tenant_id == token_tenant
    assert granted.entitlement_check.status == "granted"


def test_plain_tenant_from_a_host_hook_stays_dispatch_metadata(runtime):
    runtime.hooks.register_bundle({"module_scope_resolver": _plain_tenant_hook}, source="test")
    client = runtime.client()
    runtime.assignments.add(tenant_id="t-paid")
    member = runtime.token("maya")

    assert _refused(_export(client, member))
    assert _refused(_export(client, member, context={"tenant_id": "t-paid"}))
    assert runtime.handler.calls == 0
    assert {audit.tenant_id for audit in _audits()} == {"t-paid"}


_FORGED_TENANT = {"tenant_id": "t-paid", "verified_tenant_id": "t-paid", "_verified_tenant_id": "t-paid"}


@pytest.mark.parametrize("with_hook", [False, True])
@pytest.mark.parametrize("form", ["context", "query", "params", "flat", "headers"])
def test_request_input_cannot_assert_a_verified_tenant(runtime, with_hook, form):
    if with_hook:
        runtime.hooks.register_bundle({"module_scope_resolver": _membership_hook}, source="test")
    client = runtime.client()
    runtime.assignments.add(tenant_id="t-paid")
    headers = runtime.token("nora")

    if form == "flat":
        response = client.post(URL, json=dict(_FORGED_TENANT), headers=headers)
    elif form == "headers":
        headers.update({"X-Tenant-Id": "t-paid", "X-Mozaiks-Tenant-Id": "t-paid", "X-Verified-Tenant-Id": "t-paid"})
        response = _export(client, headers)
    else:
        response = _export(client, headers, **{
            "context": {"context": dict(_FORGED_TENANT)},
            "query": {"query": dict(_FORGED_TENANT)},
            "params": dict(_FORGED_TENANT),
        }[form])

    assert _refused(response), response.text
    assert runtime.handler.calls == 0


@pytest.mark.parametrize(("hook_result", "granted"), [
    ({}, True),
    ({"verified_workspace_id": "ws-own"}, True),
    ({"tenant_id": "t-other"}, True),
    ({"verified_tenant_id": None}, False),
    ({"verified_tenant_id": "t-other"}, False),
])
def test_token_bound_tenant_is_kept_unless_the_host_asserts_one(runtime, hook_result, granted):
    runtime.hooks.register_bundle({"module_scope_resolver": lambda **_scope: dict(hook_result)}, source="test")
    client = runtime.client()
    runtime.assignments.add(tenant_id="t-paid")

    response = _export(client, runtime.token("mia", tenant="t-paid"))

    if granted:
        assert response.status_code == 200, response.text
    else:
        assert _refused(response), response.text
    assert runtime.handler.calls == int(granted)


@pytest.mark.parametrize(("token_workspace", "hook_result", "granted"), [
    ("ws-paid", {"verified_tenant_id": "t-own"}, True),
    (None, {"verified_tenant_id": "t-own", "verified_workspace_id": "ws-paid"}, True),
    (None, {"verified_tenant_id": "t-own"}, False),
    ("ws-paid", {"verified_tenant_id": "t-own", "verified_workspace_id": None}, False),
])
def test_tenant_assertion_leaves_workspace_behaviour_unchanged(runtime, token_workspace, hook_result, granted):
    runtime.hooks.register_bundle({"module_scope_resolver": lambda **_scope: dict(hook_result)}, source="test")
    client = runtime.client(workspace_store=True)
    runtime.assignments.add(workspace_id="ws-paid")

    response = _export(client, runtime.token("quin", workspace=token_workspace))

    if granted:
        assert response.status_code == 200, response.text
    else:
        assert _refused(response), response.text


def test_admin_lane_applies_a_host_verified_tenant(runtime):
    runtime.hooks.register_bundle({"module_scope_resolver": _membership_hook}, source="test")
    client = runtime.client(surface="admin_internal")
    runtime.assignments.add(tenant_id="t-paid")
    url = "/api/admin/modules/reports/export_report"

    member = client.post(url, json={"params": {}}, headers=runtime.token("olga", roles=["platform_operator"]))
    outsider = client.post(
        url, json={"params": {}, "context": {"tenant_id": "t-paid"}},
        headers=runtime.token("otto", roles=["platform_operator"]),
    )

    assert member.status_code == 200, member.text
    assert _refused(outsider)
    assert runtime.handler.calls == 1
    assert [(audit.actor_id, audit.tenant_id, audit.entitlement_check.status) for audit in _audits()] == [
        ("olga", None, "granted"), ("otto", "t-paid", "denied"),
    ]


@pytest.mark.parametrize("surface", ["panels", "tabs", "pages", "relationships"])
def test_profile_hydration_applies_a_host_verified_tenant(runtime, monkeypatch, surface):
    from mozaiksai.hosts import platform

    runtime.hooks.register_bundle({"module_scope_resolver": _membership_hook}, source="test")
    runtime.assignments.add(tenant_id="t-paid")
    relationship = surface == "relationships"
    monkeypatch.setattr(platform, "executor_registry", SimpleNamespace(module_executor=runtime.executor()))
    monkeypatch.setattr(platform, "get_platform_hooks", lambda: runtime.hooks)
    monkeypatch.setattr(platform.app.state, "subscriptions_config", None)
    loader = "load_relationship_providers" if relationship else f"load_profile_{surface}"
    monkeypatch.setattr(platform, loader, lambda *_: [{"id": "gated", "module_id": "reports", "action": "export_report"}])
    handler = platform.get_current_user_relationships if relationship else getattr(platform, f"get_profile_{surface}")
    app = FastAPI()
    app.add_api_route("/profile", handler, methods=["GET"])
    client = TestClient(app)

    def error_for(user):
        response = client.get("/profile", headers=runtime.token(user))
        assert response.status_code == 200, response.text
        rows = response.json()["providers" if relationship else surface]
        return next(row for row in rows if row["id"] == "gated")["error"]

    assert error_for("maya") is None
    assert error_for("nora")
    assert runtime.handler.calls == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(("user", "granted"), [("maya", True), ("nora", False)])
async def test_workflow_tool_dispatch_applies_a_host_verified_tenant(runtime, monkeypatch, user, granted):
    from starlette.websockets import WebSocketState

    from mozaiksai.core.transport.simple_transport import SimpleTransport
    from mozaiksai.core.workflow import module_tools

    runtime.hooks.register_bundle({"module_scope_resolver": _membership_hook}, source="test")
    runtime.assignments.add(tenant_id="t-paid")
    app = SimpleNamespace(state=SimpleNamespace(
        executor_registry=SimpleNamespace(module_executor=runtime.executor()),
        module_action_surfaces={"reports": {"export_report": None}},
    ))
    socket = SimpleNamespace(
        state=SimpleNamespace(user=_socket_user(user)), app=app,
        client_state=WebSocketState.CONNECTED, application_state=WebSocketState.CONNECTED,
    )
    transport = SimpleNamespace(connections={
        "chat-1": {"websocket": socket, "active": True, "app_id": APP_ID, "user_id": user},
    })
    monkeypatch.setattr(SimpleTransport, "get_instance", AsyncMock(return_value=transport))
    monkeypatch.setattr(module_tools, "get_platform_hooks", lambda: runtime.hooks)
    monkeypatch.setattr(module_tools, "active_workflow_tool_run", lambda: ("Reports", APP_ID, "chat-1", user))

    result = await module_tools.dispatch_workflow_module_action("reports", "export_report", {})

    assert result.success is granted
    assert result.error_code == (None if granted else "ENTITLEMENT_REQUIRED")


@pytest.mark.asyncio
@pytest.mark.parametrize(("user", "granted"), [("maya", True), ("nora", False)])
async def test_page_ask_context_applies_a_host_verified_tenant(runtime, monkeypatch, user, granted):
    from mozaiksai.core.runtime import composition
    from mozaiksai.core.runtime.app import ask_page_context

    runtime.hooks.register_bundle({"module_scope_resolver": _membership_hook}, source="test")
    runtime.assignments.add(tenant_id="t-paid")
    monkeypatch.setattr(composition, "get_platform_hooks", lambda: runtime.hooks)
    app = SimpleNamespace(state=SimpleNamespace(
        executor_registry=SimpleNamespace(module_executor=runtime.executor()),
        module_ask_context_actions={"reports": {"export_report": True}},
    ))
    principal = _socket_user(user)

    context = await ask_page_context.resolve_page_ask_context(
        ask_page_context.normalize_ask_context_declarations([{"module": "reports", "action": "export_report"}]),
        app=app, app_id=APP_ID, user_id=user,
        persistence_principal=PersistencePrincipal.from_websocket_user(principal), principal=principal,
    )

    assert context == ({"reports.export_report": json.dumps({"exported": True})} if granted else {})


@pytest.mark.asyncio
async def test_scope_registry_carries_only_a_hook_asserted_tenant():
    async def resolve(*hook_results, requested=None, params=None):
        registry = PlatformHookRegistry()
        for hook_result in hook_results:
            registry.register_bundle(
                {"module_scope_resolver": lambda _result=hook_result, **_scope: dict(_result)}, source="test",
            )
        return await registry.call_module_scope(
            principal=None, module_name="reports", action_name="export_report",
            requested_scope=requested or {}, params=params or {},
        )

    requested_only = await resolve(requested=dict(_FORGED_TENANT), params=dict(_FORGED_TENANT))
    assert requested_only["tenant_id"] == "t-paid"
    assert "_verified_tenant_id" not in requested_only
    assert "_verified_tenant_id" not in await resolve({"tenant_id": "t-paid", "_verified_tenant_id": "t-paid"})
    assert (await resolve({"verified_tenant_id": " t-host "}))["_verified_tenant_id"] == "t-host"
    assert (await resolve({"verified_tenant_id": None}))["_verified_tenant_id"] is None
    assert (await resolve({"verified_tenant_id": "t-host"}, {"verified_tenant_id": ""}))["_verified_tenant_id"] is None


@pytest.mark.asyncio
async def test_scope_registry_keeps_a_verified_tenant_out_of_dispatch_scope_and_past_a_failing_hook():
    permission_tenants: list[Any] = []

    def failing(**_scope):
        raise RuntimeError("membership lookup failed")

    def permissions(**scope):
        permission_tenants.append(scope["tenant_id"])

    registry = PlatformHookRegistry()
    registry.register_bundle(
        {"module_scope_resolver": lambda **_scope: {"verified_tenant_id": "t-host"}}, source="test",
    )
    registry.register_bundle(
        {"module_scope_resolver": failing, "module_permission_resolver": permissions}, source="test",
    )

    resolved = await registry.call_module_scope(
        principal=None, module_name="reports", action_name="export_report",
        requested_scope={"tenant_id": "t-requested"}, params={},
    )

    assert resolved["_verified_tenant_id"] == "t-host"
    assert resolved["tenant_id"] == "t-requested"
    assert permission_tenants == ["t-requested"]


def test_principal_takes_tenant_and_workspace_only_from_verified_keys():
    bound = PersistencePrincipal(user_id="mia", workspace_id="ws-token", tenant_id="t-token")
    development = PersistencePrincipal(user_id="dev", workspace_id="development", source="development")

    assert bound.with_host_scope({"tenant_id": "t-x", "workspace_id": "ws-x"}) is bound
    assert bound.with_host_scope({"_verified_tenant_id": "t-host"}) == PersistencePrincipal(
        user_id="mia", workspace_id="ws-token", tenant_id="t-host",
    )
    assert bound.with_host_scope({"_verified_tenant_id": None}) == PersistencePrincipal(
        user_id="mia", workspace_id="ws-token",
    )
    assert bound.with_host_scope({"_verified_tenant_id": "t-host", "_verified_workspace_id": "ws-host"}) == (
        PersistencePrincipal(user_id="mia", workspace_id="ws-host", tenant_id="t-host")
    )
    assert development.with_host_scope({"_verified_tenant_id": "t-host"}) == PersistencePrincipal(
        user_id="dev", workspace_id="development", source="development", tenant_id="t-host",
    )
    assert bound.with_host_scope({"verified_tenant_id": "t-x", "verified_workspace_id": "ws-x"}) is bound
    assert PersistencePrincipal(user_id="mia").with_host_scope({"_verified_tenant_id": "t-host"}) == (
        PersistencePrincipal(user_id="mia", tenant_id="t-host")
    )
