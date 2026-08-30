"""Shared explicit-dispatch-authority builders for module executor tests.

Every ModuleRequest requires an explicit ModuleDispatchAuthority. These
helpers keep test intent obvious: enforce-mode dispatch with concrete
permissions, or a server-owned trusted authority. There is deliberately no
permissive default that hides the enforcement decision.
"""

from __future__ import annotations

from mozaiksai.core.runtime.composition.module_authority import (
    ModuleDispatchAuthority,
    ModuleDispatchAuthorityKind,
)


def enforce_authority(
    *permissions: str,
    kind: ModuleDispatchAuthorityKind = "authenticated_user",
    actor_id: str | None = None,
) -> ModuleDispatchAuthority:
    """Enforce-mode authority carrying the given concrete permission ids."""

    return ModuleDispatchAuthority(
        kind=kind,
        permission_mode="enforce",
        reason="test enforce dispatch",
        actor_id=actor_id,
        permissions=tuple(permissions),
    )


def trusted_framework_authority(reason: str = "test trusted framework dispatch") -> ModuleDispatchAuthority:
    """Server-owned trusted authority for tests exercising internal dispatch."""

    return ModuleDispatchAuthority(
        kind="framework_internal",
        permission_mode="trusted_bypass",
        reason=reason,
    )
