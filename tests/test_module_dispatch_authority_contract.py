"""Contract proofs for required explicit ModuleDispatchAuthority.

Every ModuleRequest carries a constructor-validated authority. Trusted bypass
is a closed, server-owned construction property — never an inference from a
missing principal, an empty permission list, or caller/model-supplied state.
ModuleExecutor remains the sole evaluator of module action permissions and
entitlement gates.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from mozaiksai.core.ports.entitlement import EntitlementPort, EntitlementResult
from mozaiksai.core.runtime.composition.module_authority import (
    TRUSTED_BYPASS_KINDS,
    ModuleDispatchAuthority,
    event_reaction_authority,
    workflow_user_authority,
)
from mozaiksai.core.runtime.composition.module_executor import ModuleExecutor, ModuleRequest
from tests.module_authority_test_helpers import enforce_authority, trusted_framework_authority


class _OrdersHandler:
    async def run(self, ctx):
        return {"ran": True}

    async def public_ping(self, ctx):
        return {"pong": True}


def _executor(entitlement_checker: EntitlementPort | None = None) -> ModuleExecutor:
    executor = ModuleExecutor(entitlement_checker=entitlement_checker)
    executor.register(
        "orders",
        _OrdersHandler(),
        action_method_map={"run": "run", "public_ping": "public_ping"},
        action_permissions={"run": ["orders.run"], "public_ping": []},
        action_entitlements={"run": "orders.premium", "public_ping": None},
    )
    return executor


# ---------------------------------------------------------------------------
# Construction invariants
# ---------------------------------------------------------------------------


def test_module_request_requires_explicit_authority() -> None:
    with pytest.raises(TypeError):
        ModuleRequest(module="orders", action="run", app_id="app-1")  # type: ignore[call-arg]


@pytest.mark.parametrize(
    "kind",
    ["authenticated_user", "public_http", "workflow", "app_internal"],
)
def test_trusted_bypass_rejected_for_non_server_owned_kinds(kind) -> None:
    with pytest.raises(ValueError, match="trusted_bypass"):
        ModuleDispatchAuthority(kind=kind, permission_mode="trusted_bypass", reason="nope")


def test_trusted_bypass_kind_set_is_closed() -> None:
    assert TRUSTED_BYPASS_KINDS == frozenset(
        {"framework_internal", "operator_internal", "event_reaction", "local_development"}
    )


def test_workflow_authority_always_enforces() -> None:
    authority = workflow_user_authority(
        actor_id="user-1", permissions=("orders.run",), workflow_name="OrderFlow"
    )
    assert authority.kind == "workflow"
    assert authority.permission_mode == "enforce"
    assert authority.permissions == ("orders.run",)


def test_local_development_rejected_when_auth_enabled(monkeypatch) -> None:
    monkeypatch.setenv("AUTH_PROVIDER", "jwt")
    with pytest.raises(ValueError, match="local_development"):
        ModuleDispatchAuthority(
            kind="local_development",
            permission_mode="trusted_bypass",
            reason="dev dispatch",
        )


def test_local_development_available_when_auth_disabled(monkeypatch) -> None:
    monkeypatch.setenv("AUTH_ENABLED", "false")
    monkeypatch.delenv("AUTH_PROVIDER", raising=False)
    authority = ModuleDispatchAuthority(
        kind="local_development",
        permission_mode="trusted_bypass",
        reason="dev dispatch",
    )
    assert authority.permission_mode == "trusted_bypass"


def test_event_reaction_enforces_by_default_and_requires_provenance() -> None:
    authority, provenance = event_reaction_authority(
        event_id="evt-1",
        event_type="domain.orders.created",
        event_producer="orders",
    )
    assert authority.kind == "event_reaction"
    assert authority.permission_mode == "enforce"
    assert provenance.event_id == "evt-1"
    assert provenance.event_type == "domain.orders.created"
    assert provenance.event_producer == "orders"

    with pytest.raises(ValueError, match="provenance"):
        event_reaction_authority(event_id="", event_type="x", event_producer="y")


def test_event_reaction_trust_requires_contract_declaration() -> None:
    authority, _ = event_reaction_authority(
        event_id="evt-1",
        event_type="domain.orders.created",
        event_producer="orders",
        contract_declares_trusted=True,
    )
    assert authority.permission_mode == "trusted_bypass"
    # Without the contract declaration, internal origin alone never grants trust.
    default_authority, _ = event_reaction_authority(
        event_id="evt-1",
        event_type="domain.orders.created",
        event_producer="orders",
    )
    assert default_authority.permission_mode == "enforce"


# ---------------------------------------------------------------------------
# Executor enforcement — ModuleExecutor is the only evaluator
# ---------------------------------------------------------------------------


@dataclass
class _RecordingEntitlements(EntitlementPort):
    granted: bool = True
    checks: int = 0

    async def check(self, capability_id, *, app_id, user_id=None, tenant_id=None, workspace_id=None):
        self.checks += 1
        return EntitlementResult(
            granted=self.granted,
            reason="active_grant" if self.granted else "no_grant",
        )


@pytest.mark.asyncio
async def test_enforce_mode_uses_authority_permissions_only() -> None:
    entitlements = _RecordingEntitlements(granted=True)
    executor = _executor(entitlements)

    denied = await executor.execute(
        ModuleRequest(module="orders", action="run", app_id="app-1", authority=enforce_authority())
    )
    assert denied.success is False
    assert denied.error_code == "PERMISSION_DENIED"
    assert entitlements.checks == 0

    allowed = await executor.execute(
        ModuleRequest(
            module="orders",
            action="run",
            app_id="app-1",
            authority=enforce_authority("orders.run"),
        )
    )
    assert allowed.success is True
    assert entitlements.checks == 1


@pytest.mark.asyncio
async def test_public_http_empty_permissions_only_runs_permission_free_actions() -> None:
    executor = _executor()
    public_authority = enforce_authority(kind="public_http")

    ping = await executor.execute(
        ModuleRequest(module="orders", action="public_ping", app_id="app-1", authority=public_authority)
    )
    assert ping.success is True

    run = await executor.execute(
        ModuleRequest(module="orders", action="run", app_id="app-1", authority=public_authority)
    )
    assert run.success is False
    assert run.error_code == "PERMISSION_DENIED"


@pytest.mark.asyncio
async def test_entitlement_gate_enforced_in_enforce_mode_and_skipped_for_trusted() -> None:
    entitlements = _RecordingEntitlements(granted=False)
    executor = _executor(entitlements)

    denied = await executor.execute(
        ModuleRequest(
            module="orders",
            action="run",
            app_id="app-1",
            authority=enforce_authority("orders.run"),
        )
    )
    assert denied.success is False
    assert denied.error_code == "ENTITLEMENT_REQUIRED"
    assert entitlements.checks == 1

    trusted = await executor.execute(
        ModuleRequest(
            module="orders",
            action="run",
            app_id="app-1",
            authority=trusted_framework_authority(),
        )
    )
    assert trusted.success is True
    assert entitlements.checks == 1  # no additional check for trusted dispatch
