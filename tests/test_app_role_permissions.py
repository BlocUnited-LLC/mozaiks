"""Loaded app grants reach module dispatch without granting every caller a role."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketState

from mozaiksai.core.auth import UserPrincipal, optional_user
from mozaiksai.core.auth.websocket_auth import WebSocketUser
from mozaiksai.core.runtime.app.auth_contract import (
    AppAuthContractError,
    validate_app_auth_contract,
)
from mozaiksai.core.runtime.app.module_permissions import resolve_module_permissions
from mozaiksai.core.runtime.composition import (
    ExecutorRegistry,
    ModuleExecutor,
    PlatformHookRegistry,
)
from mozaiksai.core.runtime.persistence import PersistencePrincipal
from mozaiksai.hosts.routers import modules

PERMISSION = "tools.create_tool"


@pytest.fixture
def contract_document():
    path = Path(__file__).resolve().parents[1] / "factory_app/app/config/auth.yaml"
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    document["schema_version"] = "mozaiks.auth.v2"
    document["authorization"] = {"roles": [
        {"id": "coordinator", "permissions": [PERMISSION]},
        {"id": "admin", "permissions": [PERMISSION]},
        {"id": "auditor", "permissions": ["tools.audit"]},
    ]}
    return document


@pytest.fixture
def app(contract_document):
    app = FastAPI()
    app.state.auth_contract = validate_app_auth_contract(contract_document)
    app.state.app_admin_emails = (" Owner@Example.com ",)
    app.state.loaded_app_id = "library"
    return app


def _principal(**changes):
    return UserPrincipal(**{
        "user_id": "member-1", "email": None, "name": None,
        "roles": [], "scopes": ["openid"], "raw_claims": {},
        "auth_provenance": "token_validated", "provider": "jwt",
        **changes,
    })


@pytest.mark.parametrize("roles,expected", [
    ([], ["openid"]),
    (["unknown"], ["openid"]),
    (["coordinator"], ["openid", PERMISSION]),
    (["coordinator", "admin", "auditor"], ["openid", PERMISSION, "tools.audit"]),
])
def test_only_held_declared_roles_grant_permissions(app, roles, expected):
    principal = _principal(roles=roles)
    assert resolve_module_permissions(principal, app=app) == expected
    assert principal.roles == roles and principal.scopes == ["openid"]


@pytest.mark.parametrize("provenance,allowed", [
    ("anonymous", False), ("token_validated", True), ("local_development", True),
])
def test_roles_require_verified_or_explicit_development_identity(app, provenance, allowed):
    principal = _principal(roles=["coordinator"], auth_provenance=provenance)
    assert (PERMISSION in resolve_module_permissions(principal, app=app)) is allowed


@pytest.mark.parametrize("email,verified,provenance,allowed", [
    ("owner@example.com", False, "token_validated", False),
    ("OWNER@example.com", True, "token_validated", True),
    ("outsider@example.com", True, "token_validated", False),
    ("owner@example.com", True, "anonymous", False),
    ("owner@example.com", False, "local_development", True),
    ("\u212Aeeper@example.com", True, "token_validated", False),
    (" ", True, "token_validated", False),
])
def test_admin_allowlist_requires_verified_ascii_email(app, email, verified, provenance, allowed):
    app.state.app_admin_emails += ("keeper@example.com", " ")
    principal = _principal(email=email, email_verified=verified, auth_provenance=provenance)
    granted = resolve_module_permissions(principal, app=app)
    assert (PERMISSION in granted) is allowed
    assert "tools.audit" not in granted
    assert principal.roles == []


def test_grants_and_admin_allowlist_are_bound_to_the_loaded_app(app, contract_document):
    other = FastAPI()
    contract_document["authorization"]["roles"] = []
    other.state.auth_contract = validate_app_auth_contract(contract_document)
    other.state.app_admin_emails = ()
    staff = _principal(roles=["coordinator"])
    assert PERMISSION in resolve_module_permissions(staff, app=app)
    assert resolve_module_permissions(staff, app=other) == ["openid"]
    assert resolve_module_permissions(staff, app=FastAPI()) == ["openid"]
    assert resolve_module_permissions(None, app=app) == []


def test_duck_typed_provenance_cannot_claim_role_grants(app):
    claimant = SimpleNamespace(scopes=["openid"], roles=["coordinator"],
                               has_local_development_access=True, is_authenticated=True)
    assert resolve_module_permissions(claimant, app=app) == ["openid"]


@pytest.mark.parametrize("authorization", [
    {"roles": [{"id": "coordinator", "permissions": [PERMISSION, PERMISSION]}]},
    {"roles": [{"id": "admin", "permissions": []}, {"id": "admin", "permissions": []}]},
    {"roles": [{"id": "Admin Role", "permissions": [PERMISSION]}]},
    {"roles": [{"id": "admin", "permissions": ["*"]}]},
    {"roles": [{"id": "admin", "permissions": [" tools.create_tool"]}]},
    {"roles": [{"id": "admin", "permissions": [PERMISSION], "bypass": True}]},
    {"roles": [], "default_role": "admin"},
])
def test_authorization_contract_rejects_ambiguous_or_unbounded_grants(contract_document, authorization):
    contract_document["authorization"] = authorization
    with pytest.raises(AppAuthContractError):
        validate_app_auth_contract(contract_document)


def test_auth_v2_replaces_v1_and_empty_policy_grants_nothing(contract_document):
    contract_document.pop("authorization")
    assert validate_app_auth_contract(contract_document).authorization.roles == []
    contract_document["schema_version"] = "mozaiks.auth.v1"
    with pytest.raises(AppAuthContractError):
        validate_app_auth_contract(contract_document)


class _Audit:
    async def log_module_action(self, **kwargs):
        pass


class _Handler:
    def __init__(self):
        self.calls = []

    async def create_tool(self, ctx):
        self.calls.append(ctx.user_id)
        return {"created": True, "permission_mode": ctx.dispatch_audit.permission_check.checked}


def _runtime(app, hooks):
    handler = _Handler()
    executor = ModuleExecutor(platform_hooks=hooks, audit_logger=_Audit())
    executor.register("tools", handler, action_method_map={"create_tool": "create_tool"},
                      action_permissions={"create_tool": [PERMISSION]})
    registry = ExecutorRegistry()
    registry.register(executor)
    app.state.executor_registry = registry
    app.state.module_action_surfaces = {"tools": {"create_tool": None}}
    app.include_router(modules.router)
    app.dependency_overrides[modules.module_dispatch_environment] = lambda: modules.ModuleDispatchEnvironment(
        authentication_enabled=True, platform_hooks=hooks, record_invocation=lambda **kwargs: None,
        persistence_principal=lambda principal: None,
    )
    return handler


def test_http_dispatch_allows_role_holder_and_denies_member_before_execution(app):
    handler = _runtime(app, PlatformHookRegistry())
    principal = _principal()
    app.dependency_overrides[optional_user] = lambda: principal
    with TestClient(app) as client:
        denied = client.post("/api/modules/tools/create_tool", json={})
        assert denied.status_code == 403 and denied.json()["detail"]["error_code"] == "PERMISSION_DENIED"
        assert handler.calls == []
        principal.roles = ["coordinator"]
        allowed = client.post("/api/modules/tools/create_tool", json={})
        assert allowed.status_code == 200, allowed.text
        assert allowed.json() == {"created": True, "permission_mode": True}
        assert handler.calls == ["member-1"]
        principal.roles = []
        assert client.post("/api/modules/tools/create_tool", json={}).status_code == 403
        assert handler.calls == ["member-1"]


def test_deployer_hook_can_remove_role_permission_before_http_dispatch(app):
    hooks = PlatformHookRegistry()
    observed = []

    def narrow(**kwargs):
        observed.append(kwargs["default_permissions"])
        return {"permissions": []}

    hooks.register_bundle({"module_scope_resolver": narrow}, source="test")
    handler = _runtime(app, hooks)
    app.dependency_overrides[optional_user] = lambda: _principal(roles=["coordinator"])
    with TestClient(app) as client:
        response = client.post("/api/modules/tools/create_tool", json={})
    assert response.status_code == 403
    assert observed == [["openid", PERMISSION]]
    assert handler.calls == []


def test_failed_deployer_hook_never_restores_role_permissions(app):
    hooks = PlatformHookRegistry()

    def unavailable(**kwargs):
        assert PERMISSION in kwargs["default_permissions"]
        raise RuntimeError("membership service unavailable")

    hooks.register_bundle({"module_scope_resolver": unavailable}, source="test")
    handler = _runtime(app, hooks)
    app.dependency_overrides[optional_user] = lambda: _principal(roles=["coordinator"])
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.post("/api/modules/tools/create_tool", json={})
    assert response.status_code == 403
    assert response.json()["detail"] == "Module scope resolution failed"
    assert handler.calls == []


@pytest.mark.asyncio
async def test_workflow_session_uses_same_role_grants_and_revocation(app):
    from mozaiksai.core.workflow.module_tools import session_permissions

    principal = WebSocketUser(user_id="staff", email=None, name=None, roles=["coordinator"],
                              scopes=["openid"], raw_claims={}, provider="jwt", auth_provenance="token_validated")
    assert await session_permissions(principal, app=app) == ["openid", PERMISSION]
    assert await session_permissions(replace(principal, roles=[]), app=app) == ["openid"]
    assert await session_permissions(replace(principal, auth_provenance="anonymous"), app=app) == ["openid"]


@pytest.mark.asyncio
async def test_profile_hydration_retains_hook_narrowing_and_ownership(app, monkeypatch):
    from mozaiksai.hosts import platform

    hooks = PlatformHookRegistry()

    def narrow(**kwargs):
        assert PERMISSION in kwargs["default_permissions"]
        return {"permissions": [], "workspace_id": "unverified-request"}

    hooks.register_bundle({"module_scope_resolver": narrow}, source="test")
    monkeypatch.setattr(platform, "app", app)
    monkeypatch.setattr(platform, "get_platform_hooks", lambda: hooks)
    owner = PersistencePrincipal(user_id="member-1", workspace_id="verified-workspace")
    scope = await platform._profile_dispatch_scope(
        _principal(roles=["coordinator"]), owner, app_id="library", user_id="member-1",
        module_name="tools", action="create_tool", params={},
    )
    assert scope["permissions"] == []
    assert owner.with_host_scope(scope) == owner


@pytest.mark.asyncio
@pytest.mark.parametrize("surface", ["panels", "tabs", "pages", "relationships"])
@pytest.mark.parametrize("narrowed", [False, True])
async def test_profile_dispatch_sites_enforce_roles_before_hooks(app, monkeypatch, surface, narrowed):
    from mozaiksai.hosts import platform

    hooks = PlatformHookRegistry()
    if narrowed:
        hooks.register_bundle({"module_scope_resolver": lambda **kwargs: {"permissions": []}}, source="test")
    handler = _runtime(app, hooks)
    monkeypatch.setattr(platform, "app", app)
    monkeypatch.setattr(platform, "executor_registry", app.state.executor_registry)
    monkeypatch.setattr(platform, "get_platform_hooks", lambda: hooks)
    monkeypatch.setattr(platform, "_resolve_profile_scope", lambda *args, **kwargs: ("library", "member-1"))
    monkeypatch.setattr(platform, "_resolve_profile_subject_user_id", AsyncMock(return_value="member-1"))
    loader = "load_relationship_providers" if surface == "relationships" else f"load_profile_{surface}"
    monkeypatch.setattr(platform, loader, lambda *args: [{"id": "tools", "module_id": "tools", "action": "create_tool"}])
    endpoint = platform.get_current_user_relationships if surface == "relationships" else getattr(platform, f"get_profile_{surface}")
    kwargs = {"principal": _principal(roles=["coordinator"])}
    if surface == "pages":
        kwargs["request"] = SimpleNamespace(app=app)
    await endpoint(**kwargs)
    assert handler.calls == ([] if narrowed else ["member-1"])
    kwargs["principal"].roles = []
    await endpoint(**kwargs)
    assert handler.calls == ([] if narrowed else ["member-1"])


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["roles", "email", "email_verified", "auth_provenance", "hook_failure"])
async def test_workflow_role_authority_is_rechecked_after_awaited_hook(app, monkeypatch, change):
    from mozaiksai.core.runtime.composition.platform_hooks import ModuleScopeResolutionError
    from mozaiksai.core.workflow import module_tools

    principal = WebSocketUser(user_id="member-1", email="owner@example.com", name=None, roles=["coordinator"],
                              scopes=["openid"], raw_claims={}, provider="jwt", auth_provenance="token_validated",
                              email_verified=True)
    socket = SimpleNamespace(app=app, state=SimpleNamespace(user=principal),
                             client_state=WebSocketState.CONNECTED, application_state=WebSocketState.CONNECTED)
    transport = SimpleNamespace(connections={"chat-1": {"websocket": socket, "active": True,
                                                       "app_id": "library", "user_id": "member-1"}})
    app.state.module_action_surfaces = {"tools": {"create_tool": None}}
    hooks = PlatformHookRegistry()

    async def mutate(**kwargs):
        assert PERMISSION in kwargs["default_permissions"]
        if change == "hook_failure":
            raise RuntimeError("membership service unavailable")
        setattr(principal, change, {"roles": [], "email": None, "email_verified": False,
                                    "auth_provenance": "anonymous"}[change])
        return {"permissions": kwargs["default_permissions"]}

    hooks.register_bundle({"module_scope_resolver": mutate}, source="test")
    run = ("Workflow", "library", "chat-1", "member-1")
    monkeypatch.setattr(module_tools, "get_platform_hooks", lambda: hooks)
    monkeypatch.setattr(module_tools, "active_workflow_tool_run", lambda: run)
    with pytest.raises(ModuleScopeResolutionError if change == "hook_failure" else PermissionError):
        await module_tools._prepare_live_dispatch("tools", "create_tool", {}, transport=transport, run_identity=run)


@pytest.mark.asyncio
async def test_notification_membership_hook_receives_roles_without_notification_grants(app, monkeypatch):
    from mozaiksai.hosts.routers import notifications

    hooks = PlatformHookRegistry()
    observed = []

    def member(**kwargs):
        observed.append(kwargs["default_permissions"])
        return {"verified_workspace_id": "workspace-1"}

    hooks.register_bundle({"module_scope_resolver": member}, source="test")
    monkeypatch.setattr(notifications, "get_platform_hooks", lambda: hooks)
    scope = await notifications._verified_notification_scope(
        SimpleNamespace(app=app), _principal(roles=["coordinator"], workspace_id="workspace-1"), "library",
    )
    assert observed == [["openid", PERMISSION]]
    assert scope["permissions"] == scope["roles"] == []
