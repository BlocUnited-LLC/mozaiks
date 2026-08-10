"""Tests for platform module dispatch endpoint behavior.

Covers the HTTP dispatch layer in mozaiksai/hosts/platform.py:
- startup-failed module returns 503 not 404
- module not in failed list falls through to executor (503 when executor absent)
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from mozaiksai.core.runtime.composition.executor_registry import ExecutorRegistry
from mozaiksai.core.runtime.composition.module_executor import ModuleExecutor
from mozaiksai.hosts import platform as platform_host


class _OrdersHandler:
    async def list(self, ctx):
        return {"permissions": ctx.permissions}

    async def whoami(self, ctx):
        return {"user_id": ctx.user_id}

    async def scope(self, ctx):
        return {
            "app_id": ctx.app_id,
            "user_id": ctx.user_id,
            "tenant_id": ctx.tenant_id,
            "workspace_id": ctx.workspace_id,
            "permissions": ctx.permissions,
        }

    async def authority(self, ctx):
        authority = ctx.dispatch_authority
        provenance = ctx.dispatch_provenance
        return {
            "authority_kind": authority.kind if authority else None,
            "permission_mode": authority.permission_mode if authority else None,
            "legacy_granted_permissions_none": (
                authority.legacy_granted_permissions_none if authority else None
            ),
            "actor_id": authority.actor_id if authority else None,
            "permissions": list(authority.permissions) if authority else None,
            "surface": provenance.surface if provenance else None,
        }

    async def inspect_app_input(self, ctx, *, app_id: str):
        return {
            "ctx_app_id": ctx.app_id,
            "param_app_id": app_id,
        }

    async def inspect_payload(self, ctx, **params):
        return {
            "ctx_app_id": ctx.app_id,
            "ctx_user_id": ctx.user_id,
            "params": params,
        }


def _client(
    *,
    failed_module_names: list[str] | None = None,
    action_surfaces: dict[str, dict[str, str | None]] | None = None,
) -> TestClient:
    platform_host.app.state.failed_module_names = failed_module_names or []
    platform_host.app.state.module_action_surfaces = action_surfaces or {}
    # Mirror the module-level registry (which tests may have monkeypatched) into
    # app.state so the modules router can retrieve it via request.app.state.
    platform_host.app.state.executor_registry = platform_host.executor_registry
    return TestClient(platform_host.app, raise_server_exceptions=False)


def test_dispatch_to_startup_failed_module_returns_503() -> None:
    client = _client(failed_module_names=["tasks"])
    resp = client.get("/api/modules/tasks/list")
    assert resp.status_code == 503
    assert "failed to load at startup" in resp.json().get("detail", "")


def test_dispatch_to_different_module_not_in_failed_list_does_not_return_startup_503() -> None:
    # "tasks" failed but "contacts" did not — contacts should get a different error
    # (executor absent → 503 with different message, or 404/500 if executor has no entry)
    client = _client(failed_module_names=["tasks"])
    resp = client.get("/api/modules/contacts/list")
    # Must NOT be the "failed to load at startup" 503
    assert "failed to load at startup" not in resp.json().get("detail", "")


def test_dispatch_with_empty_failed_list_does_not_block() -> None:
    client = _client(failed_module_names=[])
    resp = client.get("/api/modules/tasks/list")
    # No startup-failure message — falls through to executor (which returns its own error)
    assert "failed to load at startup" not in resp.json().get("detail", "")


def test_invalid_module_name_returns_400_regardless_of_failed_list() -> None:
    client = _client(failed_module_names=["../evil"])
    # The name regex check fires before the failed_module_names check
    resp = client.get("/api/modules/../evil/action")
    assert resp.status_code in {400, 404, 422}
    assert "failed to load at startup" not in str(resp.json())


def test_post_dispatch_to_startup_failed_module_returns_503() -> None:
    client = _client(failed_module_names=["tasks"])
    resp = client.post("/api/modules/tasks/create", json={"params": {}})
    assert resp.status_code == 503
    assert "failed to load at startup" in resp.json().get("detail", "")


def test_post_params_envelope_preserves_reserved_action_input(monkeypatch) -> None:
    executor = ModuleExecutor()
    executor.register(
        "orders",
        _OrdersHandler(),
        action_schemas={
            "inspect_app_input": {
                "input": {
                    "type": "object",
                    "properties": {"app_id": {"type": "string"}},
                    "required": ["app_id"],
                },
            },
        },
    )
    registry = ExecutorRegistry()
    registry.register(executor)
    monkeypatch.setattr(platform_host, "executor_registry", registry)
    monkeypatch.setenv("AUTH_ENABLED", "false")

    client = _client(failed_module_names=[])
    resp = client.post(
        "/api/modules/orders/inspect_app_input",
        json={
            "params": {"app_id": "app-resource"},
            "context": {"app_id": "app-resource"},
        },
    )

    assert resp.status_code == 200
    assert resp.json() == {
        "ctx_app_id": "app-resource",
        "param_app_id": "app-resource",
    }


def test_post_raw_body_keeps_reserved_fields_as_legacy_context_only(monkeypatch) -> None:
    executor = ModuleExecutor()
    executor.register("orders", _OrdersHandler())
    registry = ExecutorRegistry()
    registry.register(executor)
    monkeypatch.setattr(platform_host, "executor_registry", registry)
    monkeypatch.setenv("AUTH_ENABLED", "false")

    client = _client(failed_module_names=[])
    resp = client.post(
        "/api/modules/orders/inspect_payload",
        json={
            "app_id": "app-context",
            "user_id": "user-context",
            "label": "kept",
        },
    )

    assert resp.status_code == 200
    assert resp.json() == {
        "ctx_app_id": "app-context",
        "ctx_user_id": "user-context",
        "params": {"label": "kept"},
    }


# ---------------------------------------------------------------------------
# IDOR protection: explicit app_id must match principal's app_id
# ---------------------------------------------------------------------------


def test_module_dispatch_idor_guard_fires_on_mismatched_app_id(monkeypatch) -> None:
    """When AUTH_ENABLED and ?app_id doesn't match the principal's app_id, return 403.

    This test patches the auth layer to inject a known principal and enables auth
    to exercise the IDOR guard added to _execute_module_action.
    """
    from mozaiksai.core.auth.adapters.base import UserClaims
    class _MockAdapter:
        name = "mock"

        async def validate_token(self, token: str):
            return UserClaims(
                user_id="u1",
                email=None,
                name=None,
                roles=[],
                scopes=[],
                raw_claims={},
                provider="mock",
                app_id="app-1",
            )

    monkeypatch.setenv("AUTH_ENABLED", "true")
    # AUTH_PROVIDER must be set to a non-empty value so _auto_detect_provider()
    # doesn't fall through to the "none" default and is_auth_enabled() returns True.
    monkeypatch.setenv("AUTH_PROVIDER", "jwt")
    monkeypatch.setattr(
        "mozaiksai.core.auth.dependencies.get_auth_adapter",
        lambda: _MockAdapter(),
    )

    client = _client(failed_module_names=[])
    # Principal has app_id="app-1" but request targets ?app_id=app-2 → 403
    resp = client.get(
        "/api/modules/contacts/list?app_id=app-2",
        headers={"Authorization": "Bearer fake-token"},
    )
    assert resp.status_code == 403


def test_auth_disabled_module_dispatch_bypasses_action_permissions_for_local_studio(monkeypatch) -> None:
    executor = ModuleExecutor()
    executor.register(
        "orders",
        _OrdersHandler(),
        action_permissions={"list": ["orders.read"]},
    )
    registry = ExecutorRegistry()
    registry.register(executor)
    monkeypatch.setattr(platform_host, "executor_registry", registry)
    monkeypatch.setenv("AUTH_ENABLED", "false")

    client = _client(failed_module_names=[])
    resp = client.get("/api/modules/orders/list")

    assert resp.status_code == 200
    assert resp.json() == {"permissions": None}


def test_auth_disabled_module_dispatch_uses_local_development_authority(monkeypatch) -> None:
    executor = ModuleExecutor()
    executor.register(
        "orders",
        _OrdersHandler(),
        action_permissions={"authority": ["orders.read"]},
    )
    registry = ExecutorRegistry()
    registry.register(executor)
    monkeypatch.setattr(platform_host, "executor_registry", registry)
    monkeypatch.setenv("AUTH_ENABLED", "false")

    client = _client(failed_module_names=[])
    resp = client.get("/api/modules/orders/authority")

    assert resp.status_code == 200
    assert resp.json() == {
        "authority_kind": "local_development",
        "permission_mode": "trusted_bypass",
        "legacy_granted_permissions_none": True,
        "actor_id": "anonymous",
        "permissions": [],
        "surface": "http_module_dispatch",
    }


def test_auth_enabled_module_dispatch_requires_token_by_default(monkeypatch) -> None:
    executor = ModuleExecutor()
    executor.register("orders", _OrdersHandler())
    registry = ExecutorRegistry()
    registry.register(executor)
    monkeypatch.setattr(platform_host, "executor_registry", registry)
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setenv("AUTH_PROVIDER", "jwt")

    client = _client(failed_module_names=[])
    resp = client.get("/api/modules/orders/whoami")

    assert resp.status_code == 401
    assert resp.json()["detail"] == "Missing authorization token"


def test_auth_enabled_public_module_dispatch_allows_anonymous_call(monkeypatch) -> None:
    executor = ModuleExecutor()
    executor.register("orders", _OrdersHandler())
    registry = ExecutorRegistry()
    registry.register(executor)
    monkeypatch.setattr(platform_host, "executor_registry", registry)
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setenv("AUTH_PROVIDER", "jwt")

    client = _client(
        failed_module_names=[],
        action_surfaces={"orders": {"whoami": "public"}},
    )
    resp = client.get("/api/modules/orders/whoami")

    assert resp.status_code == 200
    assert resp.json() == {"user_id": None}


def test_authenticated_module_dispatch_uses_scope_hook_result(monkeypatch) -> None:
    from mozaiksai.core.auth.adapters.base import UserClaims

    class _MockAdapter:
        name = "mock"

        async def validate_token(self, token: str):
            return UserClaims(
                user_id="u1",
                email=None,
                name=None,
                roles=[],
                scopes=["access_as_user"],
                raw_claims={},
                provider="mock",
                app_id="app-token",
                tenant_id="tenant-token",
                workspace_id="workspace-token",
            )

    class _ScopeHooks:
        def __init__(self):
            self.called_with = None

        async def call_module_scope(self, **kwargs):
            self.called_with = kwargs
            return {
                "app_id": "app-resolved",
                "user_id": "u1",
                "tenant_id": "tenant-resolved",
                "workspace_id": "workspace-resolved",
                "permissions": ["orders.scope"],
            }

    executor = ModuleExecutor()
    executor.register(
        "orders",
        _OrdersHandler(),
        action_permissions={"scope": ["orders.scope"]},
    )
    registry = ExecutorRegistry()
    registry.register(executor)
    hooks = _ScopeHooks()
    monkeypatch.setattr(platform_host, "executor_registry", registry)
    # The scope hook is called from the modules router, not platform.py directly.
    monkeypatch.setattr("mozaiksai.hosts.routers.modules.get_platform_hooks", lambda: hooks)
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setenv("AUTH_PROVIDER", "jwt")
    monkeypatch.setattr(
        "mozaiksai.core.auth.dependencies.get_auth_adapter",
        lambda: _MockAdapter(),
    )

    client = _client(failed_module_names=[])
    resp = client.get(
        "/api/modules/orders/scope",
        headers={"Authorization": "Bearer fake-token"},
    )

    assert resp.status_code == 200
    assert hooks.called_with["requested_scope"] == {
        "app_id": "app-token",
        "tenant_id": "tenant-token",
        "workspace_id": "workspace-token",
        "user_id": "u1",
    }
    assert hooks.called_with["default_permissions"] == ["access_as_user"]
    assert resp.json() == {
        "app_id": "app-resolved",
        "user_id": "u1",
        "tenant_id": "tenant-resolved",
        "workspace_id": "workspace-resolved",
        "permissions": ["orders.scope"],
    }


def test_authenticated_module_dispatch_uses_authenticated_user_authority(monkeypatch) -> None:
    from mozaiksai.core.auth.adapters.base import UserClaims

    class _MockAdapter:
        name = "mock"

        async def validate_token(self, token: str):
            return UserClaims(
                user_id="u1",
                email=None,
                name=None,
                roles=[],
                scopes=["orders.read"],
                raw_claims={},
                provider="mock",
                app_id="app-token",
            )

    executor = ModuleExecutor()
    executor.register(
        "orders",
        _OrdersHandler(),
        action_permissions={"authority": ["orders.read"]},
    )
    registry = ExecutorRegistry()
    registry.register(executor)
    monkeypatch.setattr(platform_host, "executor_registry", registry)
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setenv("AUTH_PROVIDER", "jwt")
    monkeypatch.setattr(
        "mozaiksai.core.auth.dependencies.get_auth_adapter",
        lambda: _MockAdapter(),
    )

    client = _client(failed_module_names=[])
    resp = client.get(
        "/api/modules/orders/authority",
        headers={"Authorization": "Bearer fake-token"},
    )

    assert resp.status_code == 200
    assert resp.json() == {
        "authority_kind": "authenticated_user",
        "permission_mode": "enforce",
        "legacy_granted_permissions_none": False,
        "actor_id": "u1",
        "permissions": ["orders.read"],
        "surface": "http_module_dispatch",
    }


def test_unauthenticated_module_dispatch_ignores_user_id_override(monkeypatch) -> None:
    executor = ModuleExecutor()
    executor.register("orders", _OrdersHandler())
    registry = ExecutorRegistry()
    registry.register(executor)
    monkeypatch.setattr(platform_host, "executor_registry", registry)

    client = _client(failed_module_names=[])
    resp = client.get("/api/modules/orders/whoami?user_id=attacker")

    assert resp.status_code == 200
    assert resp.json() == {"user_id": "anonymous"}


def test_module_dispatch_rejects_authenticated_user_id_override(monkeypatch) -> None:
    from mozaiksai.core.auth.adapters.base import UserClaims

    class _MockAdapter:
        name = "mock"

        async def validate_token(self, token: str):
            return UserClaims(
                user_id="u1",
                email=None,
                name=None,
                roles=[],
                scopes=[],
                raw_claims={},
                provider="mock",
            )

    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setenv("AUTH_PROVIDER", "jwt")
    monkeypatch.setattr(
        "mozaiksai.core.auth.dependencies.get_auth_adapter",
        lambda: _MockAdapter(),
    )

    client = _client(failed_module_names=[])
    resp = client.post(
        "/api/modules/contacts/list",
        headers={"Authorization": "Bearer fake-token"},
        json={"context": {"user_id": "u2"}},
    )

    assert resp.status_code == 403
    assert resp.json()["detail"] == "Token user_id does not match request user_id"


def test_module_dispatch_rejects_token_bound_tenant_workspace_override(monkeypatch) -> None:
    from mozaiksai.core.auth.adapters.base import UserClaims

    class _MockAdapter:
        name = "mock"

        async def validate_token(self, token: str):
            return UserClaims(
                user_id="u1",
                email=None,
                name=None,
                roles=[],
                scopes=[],
                raw_claims={},
                provider="mock",
                tenant_id="tenant-1",
                workspace_id="workspace-1",
            )

    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setenv("AUTH_PROVIDER", "jwt")
    monkeypatch.setattr(
        "mozaiksai.core.auth.dependencies.get_auth_adapter",
        lambda: _MockAdapter(),
    )

    client = _client(failed_module_names=[])
    resp = client.get(
        "/api/modules/contacts/list?tenant_id=tenant-1&workspace_id=workspace-2",
        headers={"Authorization": "Bearer fake-token"},
    )

    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Internal-surface HTTP guard
# ---------------------------------------------------------------------------


def _registered_client(
    handler,
    *,
    action_surfaces: dict[str, dict[str, str | None]],
    module_name: str = "orders",
) -> TestClient:
    """Build a test client with a registered executor for the given handler."""
    executor = ModuleExecutor()
    executor.register(module_name, handler)
    registry = ExecutorRegistry()
    registry.register(executor)
    import mozaiksai.hosts.platform as _ph
    _ph.executor_registry = registry
    return _client(failed_module_names=[], action_surfaces=action_surfaces)


@pytest.mark.parametrize("surface", ["internal", "admin_internal"])
def test_internal_surface_action_is_rejected_via_http_get(monkeypatch, surface: str) -> None:
    """Actions with internal/admin_internal api_surface must return 404 from HTTP dispatch.

    These actions are only reachable through the event bus or direct
    ModuleExecutor calls; external callers must not be able to trigger them.
    """
    client = _client(
        failed_module_names=[],
        action_surfaces={"orders": {"process_settlement": surface}},
    )
    resp = client.get("/api/modules/orders/process_settlement")
    assert resp.status_code == 404, (
        f"Expected 404 for {surface} action via HTTP GET, got {resp.status_code}"
    )


@pytest.mark.parametrize("surface", ["internal", "admin_internal"])
def test_internal_surface_action_is_rejected_via_http_post(monkeypatch, surface: str) -> None:
    """Internal-surface actions must also be blocked on POST."""
    client = _client(
        failed_module_names=[],
        action_surfaces={"orders": {"process_settlement": surface}},
    )
    resp = client.post("/api/modules/orders/process_settlement", json={})
    assert resp.status_code == 404, (
        f"Expected 404 for {surface} action via HTTP POST, got {resp.status_code}"
    )


def test_internal_surface_block_applies_regardless_of_auth_enabled(monkeypatch) -> None:
    """The internal-surface guard fires before auth checks — it is independent of AUTH_ENABLED."""
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setenv("AUTH_PROVIDER", "jwt")

    client = _client(
        failed_module_names=[],
        action_surfaces={"orders": {"process_settlement": "internal"}},
    )
    # Even with a valid-looking bearer token the action should be unreachable.
    resp = client.get(
        "/api/modules/orders/process_settlement",
        headers={"Authorization": "Bearer any-token"},
    )
    assert resp.status_code == 404


def test_non_internal_surface_action_is_not_blocked_by_internal_guard(monkeypatch) -> None:
    """Actions without an internal surface must not be affected by the guard."""
    executor = ModuleExecutor()
    executor.register("orders", _OrdersHandler())
    registry = ExecutorRegistry()
    registry.register(executor)
    monkeypatch.setattr(platform_host, "executor_registry", registry)

    client = _client(
        failed_module_names=[],
        action_surfaces={"orders": {"list": "public_readonly"}},
    )
    resp = client.get("/api/modules/orders/list")
    # Should reach the executor (200 or 4xx from permission/action checks — NOT 404 from guard)
    assert resp.status_code != 404 or "Action not found" not in resp.json().get("detail", "")
