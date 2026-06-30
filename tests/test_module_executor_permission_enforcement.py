"""ModuleExecutor permission enforcement tests.

Verifies that granted_permissions is correctly enforced so that HTTP module
dispatch (via platform.py) can wire principal.scopes as granted permissions
and have action-level declarations respected.
"""

from __future__ import annotations

import pytest

from mozaiksai.core.runtime.composition.module_executor import (
    ModuleExecutor,
    ModuleRequest,
)

# ── minimal handler ────────────────────────────────────────────────────────────


class _Handler:
    async def do_list(self, ctx, **kwargs):
        return {"items": []}

    async def do_read(self, ctx, **kwargs):
        return {"item": None}

    async def show_permissions(self, ctx, **kwargs):
        return {"permissions": ctx.permissions}


def _executor_with_permissions(action_permissions: dict) -> ModuleExecutor:
    """Build an executor with a module registered with the given action_permissions."""
    executor = ModuleExecutor()
    executor.register(
        "orders",
        _Handler(),
        action_method_map={
            "list": "do_list",
            "read": "do_read",
            "permissions": "show_permissions",
        },
        action_permissions=action_permissions,
    )
    return executor


def _request(*, action: str, granted_permissions) -> ModuleRequest:
    return ModuleRequest(
        module="orders",
        action=action,
        params={},
        app_id="app_1",
        granted_permissions=granted_permissions,
    )


# ── tests ──────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_granted_permissions_none_bypasses_enforcement():
    """granted_permissions=None is the trusted-internal bypass; always allowed."""
    executor = _executor_with_permissions({"list": ["orders:read"]})

    result = await executor.execute(_request(action="list", granted_permissions=None))

    assert result.success is True


@pytest.mark.asyncio
async def test_granted_permissions_containing_required_scope_allows_action():
    """When the granted set includes the required permission, the action proceeds."""
    executor = _executor_with_permissions({"list": ["orders:read"]})

    result = await executor.execute(
        _request(action="list", granted_permissions=["orders:read", "orders:write"])
    )

    assert result.success is True


@pytest.mark.asyncio
async def test_granted_permissions_missing_required_scope_denies_action():
    """When the required permission is absent from the granted set, PERMISSION_DENIED."""
    executor = _executor_with_permissions({"list": ["orders:read"]})

    result = await executor.execute(_request(action="list", granted_permissions=[]))

    assert result.success is False
    assert result.error_code == "PERMISSION_DENIED"
    assert "orders:read" in (result.error or "")


@pytest.mark.asyncio
async def test_empty_action_permissions_allows_any_granted_set():
    """Actions with no declared permissions are always allowed regardless of scopes."""
    executor = _executor_with_permissions({})  # no permissions declared

    result = await executor.execute(_request(action="list", granted_permissions=[]))

    assert result.success is True


@pytest.mark.asyncio
async def test_partial_scope_match_still_denies():
    """Having some but not all required permissions must deny access."""
    executor = _executor_with_permissions({"read": ["orders:read", "orders:admin"]})

    result = await executor.execute(
        _request(action="read", granted_permissions=["orders:read"])  # missing orders:admin
    )

    assert result.success is False
    assert result.error_code == "PERMISSION_DENIED"
    assert "orders:admin" in (result.error or "")


@pytest.mark.asyncio
async def test_granted_permissions_are_injected_into_module_context():
    """Module policies can inspect ctx.permissions for resource-level checks."""
    executor = _executor_with_permissions({})

    result = await executor.execute(
        _request(action="permissions", granted_permissions=["orders:read"])
    )

    assert result.success is True
    assert result.data == {"permissions": ["orders:read"]}

