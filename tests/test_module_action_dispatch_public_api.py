from __future__ import annotations

from types import SimpleNamespace

import pytest

from mozaiksai.core.runtime.composition import (
    ExecutorRegistry,
    ModuleActionDispatchRequest,
    ModuleDispatchMetadata,
    ModuleDispatchScope,
    ModuleExecutor,
    dispatch_module_action,
)


class _OrdersModule:
    async def restricted(self, ctx, **params):  # noqa: ANN001, ANN003
        return {
            "params": params,
            "app_id": ctx.app_id,
            "user_id": ctx.user_id,
            "tenant_id": ctx.tenant_id,
            "workspace_id": ctx.workspace_id,
            "permissions": ctx.permissions,
            "auth_token": ctx.auth_token,
            "correlation_id": ctx.correlation_id,
        }


def _app_with_executor() -> SimpleNamespace:
    executor = ModuleExecutor()
    executor.register(
        "orders",
        _OrdersModule(),
        action_permissions={"restricted": ["orders.read"]},
    )
    registry = ExecutorRegistry()
    registry.register(executor)
    return SimpleNamespace(state=SimpleNamespace(executor_registry=registry))


@pytest.mark.asyncio
async def test_dispatch_module_action_preserves_scope_metadata_and_permissions() -> None:
    result = await dispatch_module_action(
        ModuleActionDispatchRequest(
            module="orders",
            action="restricted",
            params={"limit": 5},
            scope=ModuleDispatchScope(
                app_id="app-1",
                user_id="user-1",
                tenant_id="tenant-1",
                workspace_id="workspace-1",
            ),
            metadata=ModuleDispatchMetadata(
                auth_token="token-1",
                correlation_id="corr-1",
            ),
            granted_permissions=["orders.read"],
        ),
        app=_app_with_executor(),
    )

    assert result.success is True
    assert result.data == {
        "params": {"limit": 5},
        "app_id": "app-1",
        "user_id": "user-1",
        "tenant_id": "tenant-1",
        "workspace_id": "workspace-1",
        "permissions": ["orders.read"],
        "auth_token": "token-1",
        "correlation_id": "corr-1",
    }


@pytest.mark.asyncio
async def test_dispatch_module_action_preserves_permission_enforcement() -> None:
    result = await dispatch_module_action(
        ModuleActionDispatchRequest(
            module="orders",
            action="restricted",
            scope=ModuleDispatchScope(app_id="app-1", user_id="user-1"),
            granted_permissions=[],
        ),
        app=_app_with_executor(),
    )

    assert result.success is False
    assert result.error_code == "PERMISSION_DENIED"


@pytest.mark.asyncio
async def test_dispatch_module_action_does_not_expose_implicit_trusted_bypass() -> None:
    request = ModuleActionDispatchRequest(
        module="orders",
        action="restricted",
        scope=ModuleDispatchScope(app_id="app-1", user_id="user-1"),
        granted_permissions=None,  # type: ignore[arg-type]
    )

    with pytest.raises(ValueError, match="Trusted/internal authority"):
        await dispatch_module_action(request, app=_app_with_executor())
