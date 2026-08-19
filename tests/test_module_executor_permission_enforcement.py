"""ModuleExecutor permission enforcement tests.

Verifies that granted_permissions is correctly enforced so that HTTP module
dispatch (via platform.py) can wire principal.scopes as granted permissions
and have action-level declarations respected.
"""

from __future__ import annotations

import pytest

from mozaiksai.core.runtime.composition.module_authority import ModuleDispatchAuthority
from mozaiksai.core.runtime.composition.module_executor import (
    ModuleExecutor,
    ModuleRequest,
)
from tests.module_authority_test_helpers import enforce_authority, trusted_framework_authority

# ── minimal handler ────────────────────────────────────────────────────────────


class _Handler:
    async def do_list(self, ctx, **kwargs):
        return {"items": []}

    async def do_read(self, ctx, **kwargs):
        return {"item": None}

    async def show_permissions(self, ctx, **kwargs):
        return {"permissions": ctx.permissions}

    async def show_authority(self, ctx, **kwargs):
        authority = ctx.dispatch_authority
        provenance = ctx.dispatch_provenance
        return {
            "authority_kind": authority.kind if authority else None,
            "permission_mode": authority.permission_mode if authority else None,
            "authority_permissions": list(authority.permissions) if authority else None,
            "provenance_correlation_id": provenance.correlation_id if provenance else None,
        }


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
            "authority": "show_authority",
        },
        action_permissions=action_permissions,
    )
    return executor


def _request(*, action: str, authority: ModuleDispatchAuthority) -> ModuleRequest:
    return ModuleRequest(
        module="orders",
        action=action,
        params={},
        app_id="app_1",
        authority=authority,
    )


# ── tests ──────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_trusted_authority_bypasses_enforcement():
    """authority=trusted_framework_authority() is the trusted-internal bypass; always allowed."""
    executor = _executor_with_permissions({"list": ["orders:read"]})

    result = await executor.execute(_request(action="list", authority=trusted_framework_authority()))

    assert result.success is True


@pytest.mark.asyncio
async def test_trusted_authority_is_observable_in_module_context():
    executor = _executor_with_permissions({"authority": ["orders:read"]})

    result = await executor.execute(_request(action="authority", authority=trusted_framework_authority()))

    assert result.success is True
    assert result.data == {
        "authority_kind": "framework_internal",
        "permission_mode": "trusted_bypass",
        "authority_permissions": [],
        "provenance_correlation_id": None,
    }


@pytest.mark.asyncio
async def test_authority_containing_required_scope_allows_action():
    """When the granted set includes the required permission, the action proceeds."""
    executor = _executor_with_permissions({"list": ["orders:read"]})

    result = await executor.execute(
        _request(action="list", authority=enforce_authority("orders:read", "orders:write"))
    )

    assert result.success is True


@pytest.mark.asyncio
async def test_enforced_authority_is_observable_in_module_context():
    executor = _executor_with_permissions({"authority": ["orders:read"]})

    result = await executor.execute(
        _request(action="authority", authority=enforce_authority("orders:read"))
    )

    assert result.success is True
    assert result.data["authority_kind"] == "authenticated_user"
    assert result.data["permission_mode"] == "enforce"
    assert result.data["authority_permissions"] == ["orders:read"]


@pytest.mark.asyncio
async def test_authority_missing_required_scope_denies_action():
    """When the required permission is absent from the granted set, PERMISSION_DENIED."""
    executor = _executor_with_permissions({"list": ["orders:read"]})

    result = await executor.execute(_request(action="list", authority=enforce_authority()))

    assert result.success is False
    assert result.error_code == "PERMISSION_DENIED"
    assert "orders:read" in (result.error or "")


@pytest.mark.asyncio
async def test_empty_action_permissions_allows_any_authority():
    """Actions with no declared permissions are always allowed regardless of scopes."""
    executor = _executor_with_permissions({})  # no permissions declared

    result = await executor.execute(_request(action="list", authority=enforce_authority()))

    assert result.success is True


@pytest.mark.asyncio
async def test_partial_scope_match_still_denies():
    """Having some but not all required permissions must deny access."""
    executor = _executor_with_permissions({"read": ["orders:read", "orders:admin"]})

    result = await executor.execute(
        _request(action="read", authority=enforce_authority("orders:read"))  # missing orders:admin
    )

    assert result.success is False
    assert result.error_code == "PERMISSION_DENIED"
    assert "orders:admin" in (result.error or "")


@pytest.mark.asyncio
async def test_authority_permissions_are_injected_into_module_context():
    """Module policies can inspect ctx.permissions for resource-level checks."""
    executor = _executor_with_permissions({})

    result = await executor.execute(
        _request(action="permissions", authority=enforce_authority("orders:read"))
    )

    assert result.success is True
    assert result.data == {"permissions": ["orders:read"]}


class _BadParamsHandler:
    async def do_action(self, ctx, *, required_kwarg):
        return {}  # will raise TypeError when called without required_kwarg


@pytest.mark.asyncio
async def test_type_error_does_not_leak_exception_message():
    """TypeError from a handler must not expose raw exception text to callers."""
    executor = ModuleExecutor()
    executor.register(
        "bad",
        _BadParamsHandler(),
        action_method_map={"act": "do_action"},
        action_permissions={},
    )
    req = ModuleRequest(module="bad", action="act", params={}, app_id="app_1", authority=trusted_framework_authority())
    result = await executor.execute(req)

    assert result.success is False
    assert result.error_code == "INVALID_PARAMS"
    # Raw TypeError text must not appear — it can expose parameter names/types
    assert "required_kwarg" not in (result.error or "")
    assert "missing" not in (result.error or "").lower()



# ── error suppression ──────────────────────────────────────────────────────────


class _RaisingHandler:
    """Handler that raises a raw exception with internal details."""

    async def risky(self, ctx, **kwargs):
        raise RuntimeError("mongodb://user:secret@host/db connection refused")


@pytest.mark.asyncio
async def test_execution_error_does_not_leak_exception_message():
    """EXECUTION_ERROR responses must suppress the raw exception message.

    Internal exception text (e.g. DB connection strings, stack frames) must
    not reach callers — only a generic 'action failed' message is returned.
    The full error is still logged (verified by exc_info=True in the handler).
    """
    executor = ModuleExecutor()
    executor.register(
        "risky_module",
        _RaisingHandler(),
        action_method_map={"risky": "risky"},
    )
    req = ModuleRequest(
        module="risky_module",
        action="risky",
        params={},
        app_id="app_test",
        authority=trusted_framework_authority(),
    )

    result = await executor.execute(req)

    assert result.success is False
    assert result.error_code == "EXECUTION_ERROR"
    # Raw exception text must not be in the response.
    assert "mongodb" not in (result.error or "")
    assert "secret" not in (result.error or "")
    assert "connection refused" not in (result.error or "")
    # Must contain a generic, user-facing message.
    assert "failed" in (result.error or "").lower()
