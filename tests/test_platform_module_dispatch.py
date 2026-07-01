"""Tests for platform module dispatch endpoint behavior.

Covers the HTTP dispatch layer in mozaiksai/hosts/platform.py:
- startup-failed module returns 503 not 404
- module not in failed list falls through to executor (503 when executor absent)
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from mozaiksai.core.runtime.composition.executor_registry import ExecutorRegistry
from mozaiksai.core.runtime.composition.module_executor import ModuleExecutor
from mozaiksai.hosts import platform as platform_host


class _OrdersHandler:
    async def list(self, ctx):
        return {"permissions": ctx.permissions}

    async def whoami(self, ctx):
        return {"user_id": ctx.user_id}


def _client(
    *,
    failed_module_names: list[str] | None = None,
    action_surfaces: dict[str, dict[str, str | None]] | None = None,
) -> TestClient:
    platform_host.app.state.failed_module_names = failed_module_names or []
    platform_host.app.state.module_action_surfaces = action_surfaces or {}
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


def test_unauthenticated_module_dispatch_uses_empty_permissions_not_internal_bypass(monkeypatch) -> None:
    executor = ModuleExecutor()
    executor.register(
        "orders",
        _OrdersHandler(),
        action_permissions={"list": ["orders.read"]},
    )
    registry = ExecutorRegistry()
    registry.register(executor)
    monkeypatch.setattr(platform_host, "executor_registry", registry)

    client = _client(failed_module_names=[])
    resp = client.get("/api/modules/orders/list")

    assert resp.status_code == 403
    assert resp.json()["detail"]["error_code"] == "PERMISSION_DENIED"


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


def test_unauthenticated_module_dispatch_ignores_user_id_override(monkeypatch) -> None:
    executor = ModuleExecutor()
    executor.register("orders", _OrdersHandler())
    registry = ExecutorRegistry()
    registry.register(executor)
    monkeypatch.setattr(platform_host, "executor_registry", registry)

    client = _client(failed_module_names=[])
    resp = client.get("/api/modules/orders/whoami?user_id=attacker")

    assert resp.status_code == 200
    assert resp.json() == {"user_id": None}


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
    assert resp.json()["detail"] == "Token workspace_id does not match request workspace_id"
