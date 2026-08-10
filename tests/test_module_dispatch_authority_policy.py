from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

from mozaiksai.core.ports.entitlement import EntitlementResult
from mozaiksai.core.runtime.composition import (
    ModuleExecutionPolicyDecision,
    ModuleExecutor,
    ModuleRequest,
    PlatformExtensionBundle,
    PlatformHookRegistry,
)
from mozaiksai.core.runtime.composition import module_executor as module_executor_mod


class _Handler:
    def __init__(self) -> None:
        self.called = False

    async def run(self, ctx, **params):  # noqa: ANN001, ANN003
        self.called = True
        return {
            "ok": True,
            "permission_checked": ctx.dispatch_audit.permission_check.checked,
            "entitlement_status": ctx.dispatch_audit.entitlement_check.status,
            "has_params_in_audit": "params" in ctx.dispatch_audit.to_dict(),
            "secret_in_audit": "sk_test_secret" in str(ctx.dispatch_audit.to_dict()),
        }


class _Hooks:
    def __init__(self, *, decision: Any = None, raises: bool = False) -> None:
        self.decision = ModuleExecutionPolicyDecision(allowed=True) if decision is None else decision
        self.raises = raises
        self.policy_inputs: list[Any] = []
        self.audit_records: list[Any] = []

    async def call_before_module_execution(self, policy_input):
        self.policy_inputs.append(policy_input)
        if self.raises:
            raise RuntimeError("policy unavailable")
        return self.decision

    async def call_module_dispatch_audit(self, audit_record):
        self.audit_records.append(audit_record)


class _AuditLogger:
    def __init__(self) -> None:
        self.records: list[dict[str, Any]] = []

    async def log_module_action(self, **kwargs):
        self.records.append(kwargs)


def _install_hooks(monkeypatch, hooks: Any, audit: _AuditLogger) -> None:
    monkeypatch.setattr(module_executor_mod, "get_platform_hooks", lambda: hooks)
    monkeypatch.setattr(module_executor_mod, "get_audit_logger", lambda: audit)


def _executor(handler: _Handler, *, entitlement_checker: Any = None) -> ModuleExecutor:
    executor = ModuleExecutor(entitlement_checker=entitlement_checker)
    executor.register(
        "orders",
        handler,
        action_permissions={"run": ["orders.run"]},
        action_entitlements={"run": "orders.premium"},
    )
    return executor


@pytest.mark.asyncio
async def test_no_configured_policy_hook_preserves_dispatch_behavior(monkeypatch) -> None:
    hooks = _Hooks()
    audit = _AuditLogger()
    _install_hooks(monkeypatch, hooks, audit)
    handler = _Handler()

    result = await _executor(handler).execute(
        ModuleRequest(
            module="orders",
            action="run",
            params={"secret": "sk_test_secret"},
            app_id="app-1",
            granted_permissions=["orders.run"],
        )
    )
    await asyncio.sleep(0)

    assert result.success is True
    assert handler.called is True
    assert result.data["permission_checked"] is True
    assert result.data["entitlement_status"] == "granted"
    assert result.data["has_params_in_audit"] is False
    assert result.data["secret_in_audit"] is False
    assert hooks.policy_inputs[0].permission_check.granted_permissions == ("orders.run",)
    assert hooks.policy_inputs[0].entitlement_check.status == "granted"


@pytest.mark.asyncio
async def test_policy_allow_hook_permits_dispatch(monkeypatch) -> None:
    hooks = _Hooks(decision=ModuleExecutionPolicyDecision(allowed=True, audit_tags={"policy": "ok"}))
    audit = _AuditLogger()
    _install_hooks(monkeypatch, hooks, audit)
    handler = _Handler()

    result = await _executor(handler).execute(
        ModuleRequest(
            module="orders",
            action="run",
            app_id="app-1",
            granted_permissions=["orders.run"],
        )
    )

    assert result.success is True
    assert handler.called is True


@pytest.mark.asyncio
async def test_policy_deny_hook_blocks_before_action_execution(monkeypatch) -> None:
    hooks = _Hooks(
        decision=ModuleExecutionPolicyDecision(
            allowed=False,
            reason="blocked by app policy",
            audit_tags={"policy": "blocked"},
        )
    )
    audit = _AuditLogger()
    _install_hooks(monkeypatch, hooks, audit)
    handler = _Handler()

    result = await _executor(handler).execute(
        ModuleRequest(
            module="orders",
            action="run",
            app_id="app-1",
            granted_permissions=["orders.run"],
        )
    )
    await asyncio.sleep(0)

    assert result.success is False
    assert result.error_code == "PERMISSION_DENIED"
    assert result.error == "blocked by app policy"
    assert handler.called is False
    assert hooks.audit_records[0].outcome == "denied"
    assert hooks.audit_records[0].audit_tags == {"policy": "blocked"}


@pytest.mark.asyncio
async def test_policy_hook_exception_fails_closed(monkeypatch) -> None:
    async def broken_hook(_policy_input):
        raise RuntimeError("policy unavailable")

    hooks = PlatformHookRegistry()
    hooks._register_bundle(PlatformExtensionBundle(before_module_execution=broken_hook))
    audit = _AuditLogger()
    _install_hooks(monkeypatch, hooks, audit)
    handler = _Handler()

    result = await _executor(handler).execute(
        ModuleRequest(
            module="orders",
            action="run",
            app_id="app-1",
            granted_permissions=["orders.run"],
        )
    )

    assert result.success is False
    assert result.error_code == "PERMISSION_DENIED"
    assert "policy hook failed" in (result.error or "")
    assert handler.called is False


@pytest.mark.asyncio
async def test_permission_and_entitlement_denials_expose_structured_results(monkeypatch) -> None:
    hooks = _Hooks()
    audit = _AuditLogger()
    _install_hooks(monkeypatch, hooks, audit)
    handler = _Handler()

    permission_result = await _executor(handler).execute(
        ModuleRequest(module="orders", action="run", app_id="app-1", granted_permissions=[])
    )
    await asyncio.sleep(0)

    assert permission_result.success is False
    assert permission_result.error_code == "PERMISSION_DENIED"
    assert hooks.audit_records[-1].permission_check.missing_permissions == ("orders.run",)
    assert hooks.policy_inputs == []

    denied_checker = SimpleNamespace(
        check=lambda *args, **kwargs: EntitlementResult(granted=False, reason="no_plan")
    )
    denied_checker.check = _async_return(EntitlementResult(granted=False, reason="no_plan"))
    entitlement_result = await _executor(handler, entitlement_checker=denied_checker).execute(
        ModuleRequest(
            module="orders",
            action="run",
            app_id="app-1",
            granted_permissions=["orders.run"],
        )
    )
    await asyncio.sleep(0)

    assert entitlement_result.success is False
    assert entitlement_result.error_code == "ENTITLEMENT_REQUIRED"
    assert hooks.audit_records[-1].entitlement_check.status == "denied"
    assert hooks.audit_records[-1].entitlement_check.capability_id == "orders.premium"


@pytest.mark.asyncio
async def test_legacy_trusted_dispatch_remains_explicit_and_skips_checks(monkeypatch) -> None:
    hooks = _Hooks()
    audit = _AuditLogger()
    _install_hooks(monkeypatch, hooks, audit)
    handler = _Handler()

    result = await _executor(handler).execute(
        ModuleRequest(module="orders", action="run", app_id="app-1", granted_permissions=None)
    )

    assert result.success is True
    assert hooks.policy_inputs[0].authority.kind == "legacy_trusted"
    assert hooks.policy_inputs[0].permission_check.checked is False
    assert hooks.policy_inputs[0].entitlement_check.status == "skipped"


def _async_return(value):
    async def _inner(*args, **kwargs):  # noqa: ANN002, ANN003
        return value

    return _inner
