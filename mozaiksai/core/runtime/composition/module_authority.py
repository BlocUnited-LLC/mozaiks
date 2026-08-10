from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Literal
from uuid import uuid4

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


@dataclass(frozen=True)
class ModulePermissionCheck:
    """Result of framework module permission enforcement."""

    checked: bool
    granted_permissions: tuple[str, ...] = ()
    required_permissions: tuple[str, ...] = ()
    missing_permissions: tuple[str, ...] = ()

    @property
    def allowed(self) -> bool:
        return not self.checked or not self.missing_permissions


ModuleEntitlementStatus = Literal["not_applicable", "granted", "denied", "skipped"]


@dataclass(frozen=True)
class ModuleEntitlementCheck:
    """Result of framework module entitlement enforcement."""

    checked: bool
    status: ModuleEntitlementStatus
    capability_id: str | None = None
    reason: str | None = None

    @property
    def allowed(self) -> bool:
        return self.status in {"not_applicable", "granted", "skipped"}


@dataclass(frozen=True)
class ModuleExecutionPolicyDecision:
    """Allow/deny response from an application module-execution policy hook."""

    allowed: bool
    reason: str | None = None
    audit_tags: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class ModuleExecutionPolicyInput:
    """Input passed to before-module-execution application policy hooks."""

    request: Any
    authority: ModuleDispatchAuthority
    provenance: ModuleDispatchProvenance
    permission_check: ModulePermissionCheck
    entitlement_check: ModuleEntitlementCheck


ModuleDispatchOutcome = Literal["allowed", "denied", "failed", "ok"]


@dataclass(frozen=True)
class ModuleDispatchAudit:
    """Structured framework audit metadata for one module dispatch."""

    dispatch_id: str = field(default_factory=lambda: f"moddisp_{uuid4().hex}")
    app_id: str | None = None
    tenant_id: str | None = None
    workspace_id: str | None = None
    actor_id: str | None = None
    module: str = ""
    action: str = ""
    authority_kind: str = ""
    permission_mode: str = ""
    permission_check: ModulePermissionCheck = field(
        default_factory=lambda: ModulePermissionCheck(checked=False)
    )
    entitlement_check: ModuleEntitlementCheck = field(
        default_factory=lambda: ModuleEntitlementCheck(checked=False, status="skipped")
    )
    correlation_id: str | None = None
    causation_id: str | None = None
    outcome: ModuleDispatchOutcome = "allowed"
    reason: str | None = None
    audit_tags: Mapping[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "dispatch_id": self.dispatch_id,
            "app_id": self.app_id,
            "tenant_id": self.tenant_id,
            "workspace_id": self.workspace_id,
            "actor_id": self.actor_id,
            "module": self.module,
            "action": self.action,
            "authority_kind": self.authority_kind,
            "permission_mode": self.permission_mode,
            "permission_check": {
                "checked": self.permission_check.checked,
                "granted_permissions": list(self.permission_check.granted_permissions),
                "required_permissions": list(self.permission_check.required_permissions),
                "missing_permissions": list(self.permission_check.missing_permissions),
                "allowed": self.permission_check.allowed,
            },
            "entitlement_check": {
                "checked": self.entitlement_check.checked,
                "status": self.entitlement_check.status,
                "capability_id": self.entitlement_check.capability_id,
                "reason": self.entitlement_check.reason,
                "allowed": self.entitlement_check.allowed,
            },
            "correlation_id": self.correlation_id,
            "causation_id": self.causation_id,
            "outcome": self.outcome,
            "reason": self.reason,
            "audit_tags": dict(self.audit_tags),
        }
