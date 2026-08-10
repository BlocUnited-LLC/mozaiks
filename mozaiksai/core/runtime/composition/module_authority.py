from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Literal

ModuleDispatchAuthorityKind = Literal[
    "authenticated_user",
    "public_http",
    "local_development",
    "framework_internal",
    "workflow",
    "event_reaction",
    "app_internal",
    "operator_internal",
    "legacy_permissions",
    "legacy_trusted",
]

ModuleDispatchPermissionMode = Literal["enforce", "trusted_bypass"]


@dataclass(frozen=True)
class ModuleDispatchAuthority:
    """Framework-level reason a module action is allowed to dispatch.

    This is dispatch authority only. It does not authorize production
    infrastructure, DNS, money movement, secret mutation, or hosted operations.
    """

    kind: ModuleDispatchAuthorityKind
    permission_mode: ModuleDispatchPermissionMode
    reason: str
    actor_id: str | None = None
    permissions: tuple[str, ...] = ()
    legacy_granted_permissions_none: bool = False

    @classmethod
    def from_granted_permissions(
        cls,
        granted_permissions: list[str] | None,
        *,
        actor_id: str | None = None,
    ) -> ModuleDispatchAuthority:
        """Translate existing ModuleRequest permission semantics into metadata."""

        if granted_permissions is None:
            return cls(
                kind="legacy_trusted",
                permission_mode="trusted_bypass",
                reason="compatibility trusted dispatch",
                actor_id=actor_id,
                permissions=(),
                legacy_granted_permissions_none=True,
            )
        return cls(
            kind="legacy_permissions",
            permission_mode="enforce",
            reason="compatibility concrete permissions dispatch",
            actor_id=actor_id,
            permissions=tuple(granted_permissions),
            legacy_granted_permissions_none=False,
        )


@dataclass(frozen=True)
class ModuleDispatchProvenance:
    """Source metadata for a module action dispatch."""

    surface: str | None = None
    workflow_name: str | None = None
    workflow_run_id: str | None = None
    event_id: str | None = None
    event_type: str | None = None
    event_producer: str | None = None
    correlation_id: str | None = None
    causation_id: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)
