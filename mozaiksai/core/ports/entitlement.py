# ==============================================================================
# FILE: mozaiksai/core/ports/entitlement.py
# DESCRIPTION: EntitlementPort — engine-agnostic contract for capability
#              entitlement checks at module action dispatch time.
#
#              This port gives the platform a consistent enforcement hook
#              ("is this capability granted for this scope?") without coupling
#              the runtime to any billing system or plan catalog.
#
#              Default implementation: NoOpEntitlementAdapter — grants every
#              capability. Non-SaaS apps are completely unaffected.
#
#              SaaS apps with app/config/subscriptions.yaml use the OSS
#              ConfiguredEntitlementAdapter, which resolves assignments from an
#              app data-contract alias.
#
#              The module.yaml ActionDef.entitlement_gate field names the
#              capability_id to check. Actions without entitlement_gate are
#              never checked, regardless of which adapter is wired.
# ==============================================================================
"""EntitlementPort — generic contract for module action entitlement checks."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class EntitlementResult:
    """Result of a capability entitlement check.

    Fields:
        granted:      True when the capability is currently active for this scope.
        reason:       Short machine-readable reason string.  Examples:
                        "active_grant"   — a valid grant exists
                        "no_grant"       — no matching grant found
                        "expired"        — grant exists but has expired
                        "not_configured" — no entitlement adapter is wired (no-op)
        expires_at:   ISO-8601 datetime string when the grant expires, or None.
    """

    granted: bool
    reason: str = "not_configured"
    expires_at: str | None = field(default=None)


# Canonical denial result — modules can compare result.reason against this.
ENTITLEMENT_REQUIRED = EntitlementResult(granted=False, reason="no_grant")


# ---------------------------------------------------------------------------
# Port protocol
# ---------------------------------------------------------------------------


@runtime_checkable
class EntitlementPort(Protocol):
    """Engine-agnostic contract for capability entitlement checks.

    Implementors:
        NoOpEntitlementAdapter          — grants everything (default; non-SaaS apps)
        ConfiguredEntitlementAdapter    — reads config/subscriptions.yaml and its
                                          assignment_store data-contract alias

    Consumers:
        ModuleExecutor          — checks entitlement_gate before dispatching
                                  actions that declare one

    The port is deliberately thin: one check method.  All plan/grant resolution
    logic lives in the adapter, not in the port or the executor.
    """

    async def check(
        self,
        capability_id: str,
        *,
        app_id: str,
        user_id: str | None = None,
        tenant_id: str | None = None,
        workspace_id: str | None = None,
    ) -> EntitlementResult:
        """Check whether capability_id is currently granted for the given scope.

        Args:
            capability_id: The capability identifier declared in module.yaml
                           ActionDef.entitlement_gate (e.g. "wallet.view").
            app_id:        Application scope — required, must be non-empty.
            user_id:       Optional authenticated user for user-scoped grants.
            tenant_id:     Optional tenant for tenant-scoped grants.
            workspace_id:  Optional workspace for workspace-scoped grants.

        Returns:
            EntitlementResult with granted=True when the capability is active.

        Note:
            Fail-closed convention: on internal error, adapters should return
            EntitlementResult(granted=False, reason="error") rather than raising,
            so the executor can surface a clean ENTITLEMENT_REQUIRED denial.
        """
        ...


# ---------------------------------------------------------------------------
# Default no-op adapter
# ---------------------------------------------------------------------------


class NoOpEntitlementAdapter:
    """Default entitlement adapter — grants every capability.

    Used when no entitlement adapter is wired (non-SaaS apps, local dev, tests).
    Actions declaring entitlement_gate still pass through without a DB check.

    SaaS apps with config/subscriptions.yaml are wired to the configured
    subscription adapter at platform startup.
    """

    async def check(
        self,
        capability_id: str,
        *,
        app_id: str,
        user_id: str | None = None,
        tenant_id: str | None = None,
        workspace_id: str | None = None,
    ) -> EntitlementResult:
        return EntitlementResult(granted=True, reason="not_configured")


__all__ = [
    "EntitlementPort",
    "EntitlementResult",
    "NoOpEntitlementAdapter",
    "ENTITLEMENT_REQUIRED",
]
