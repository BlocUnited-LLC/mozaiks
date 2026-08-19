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
]

ModuleDispatchPermissionMode = Literal["enforce", "trusted_bypass"]

# Only server-owned dispatch reasons may ever bypass permission and
# entitlement enforcement. Bypass is a property of how the authority was
# constructed, never of a missing principal or an empty permission list.
TRUSTED_BYPASS_KINDS: frozenset[str] = frozenset(
    {
        "framework_internal",
        "operator_internal",
        "event_reaction",
        "local_development",
    }
)


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

    def __post_init__(self) -> None:
        if self.permission_mode == "trusted_bypass" and self.kind not in TRUSTED_BYPASS_KINDS:
            raise ValueError(
                f"Authority kind {self.kind!r} may not use trusted_bypass; "
                f"only {sorted(TRUSTED_BYPASS_KINDS)} qualify."
            )
        if self.kind == "workflow" and self.permission_mode != "enforce":
            raise ValueError("workflow dispatch authority must always enforce permissions")
        if self.kind == "local_development" and not _local_development_allowed():
            raise ValueError(
                "local_development dispatch authority is not available when "
                "authentication is enabled"
            )


def _local_development_allowed() -> bool:
    """local_development authority exists only while runtime auth is disabled."""

    from mozaiksai.core.auth.adapters.registry import is_auth_enabled

    return not is_auth_enabled()


def workflow_user_authority(
    *,
    actor_id: str | None,
    permissions: tuple[str, ...] = (),
    workflow_name: str | None = None,
) -> ModuleDispatchAuthority:
    """Authority for a user-facing workflow tool dispatching a module action.

    Identity and scopes must come from the server-side session principal,
    never from workflow context_variables or model output. Workflow dispatch
    always enforces the action's declared permissions and entitlement gate.
    """

    return ModuleDispatchAuthority(
        kind="workflow",
        permission_mode="enforce",
        reason=f"workflow tool dispatch: {workflow_name or 'unknown'}",
        actor_id=actor_id,
        permissions=tuple(permissions),
    )


def event_reaction_authority(
    *,
    event_id: str,
    event_type: str,
    event_producer: str,
    contract_declares_trusted: bool = False,
    actor_id: str | None = None,
) -> tuple[ModuleDispatchAuthority, ModuleDispatchProvenance]:
    """Authority for an event reaction dispatching a module action.

    Reactions enforce by default. A reaction may run trusted only when the
    consuming module contract explicitly declares it trusted AND full event
    provenance is supplied. Being an internal call is never sufficient.
    """

    if not (event_id and event_type and event_producer):
        raise ValueError(
            "event_reaction dispatch requires event_id, event_type, and event_producer provenance"
        )
    mode: ModuleDispatchPermissionMode = (
        "trusted_bypass" if contract_declares_trusted else "enforce"
    )
    authority = ModuleDispatchAuthority(
        kind="event_reaction",
        permission_mode=mode,
        reason=(
            "contract-declared trusted event reaction"
            if contract_declares_trusted
            else "event reaction dispatch"
        ),
        actor_id=actor_id,
    )
    provenance = ModuleDispatchProvenance(
        surface="event_reaction",
        event_id=event_id,
        event_type=event_type,
        event_producer=event_producer,
    )
    return authority, provenance


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
    granted: tuple[str, ...] = ()
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
                "granted": list(self.permission_check.granted),
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
