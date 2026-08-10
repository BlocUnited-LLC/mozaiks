from __future__ import annotations

"""Event/reaction provenance for canonical module reactions.

These types explain where an event reaction came from. They are evidence for
application/operator policy, not production authority.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Literal
from uuid import uuid4

EventEnvelopeShape = Literal["structured", "legacy_flat", "unknown"]
EventTrustShape = Literal["module_envelope", "platform_envelope", "legacy_flat", "unknown"]
ReactionTargetKind = Literal["handler", "capability", "notification", "service_adapter", "unknown"]
ReactionAuditOutcome = Literal["ok", "skipped", "failed"]


@dataclass(frozen=True)
class ModuleEventProvenance:
    """Structured evidence about the source event for one reaction."""

    event_id: str | None
    event_type: str
    producer_layer: str | None = None
    producer_app_id: str | None = None
    producer_module_id: str | None = None
    producer_action_id: str | None = None
    capability_id: str | None = None
    tenant_id: str | None = None
    app_id: str | None = None
    workspace_id: str | None = None
    actor_type: str | None = None
    actor_id: str | None = None
    correlation_id: str | None = None
    causation_id: str | None = None
    envelope_shape: EventEnvelopeShape = "unknown"
    trust_shape: EventTrustShape = "unknown"

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "event_type": self.event_type,
            "producer_layer": self.producer_layer,
            "producer_app_id": self.producer_app_id,
            "producer_module_id": self.producer_module_id,
            "producer_action_id": self.producer_action_id,
            "capability_id": self.capability_id,
            "tenant_id": self.tenant_id,
            "app_id": self.app_id,
            "workspace_id": self.workspace_id,
            "actor_type": self.actor_type,
            "actor_id": self.actor_id,
            "correlation_id": self.correlation_id,
            "causation_id": self.causation_id,
            "envelope_shape": self.envelope_shape,
            "trust_shape": self.trust_shape,
        }


@dataclass(frozen=True)
class ModuleReactionProvenance:
    """Structured evidence about the reaction selected by reactions.yaml."""

    reaction_id: str
    source_module_id: str
    target_kind: ReactionTargetKind
    target_ref: str | None = None
    idempotency_key: str | None = None
    declared_permissions: tuple[str, ...] = ()
    permissions_enforced: bool = False
    idempotency_enforced: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "reaction_id": self.reaction_id,
            "source_module_id": self.source_module_id,
            "target_kind": self.target_kind,
            "target_ref": self.target_ref,
            "idempotency_key": self.idempotency_key,
            "declared_permissions": list(self.declared_permissions),
            "permissions_enforced": self.permissions_enforced,
            "idempotency_enforced": self.idempotency_enforced,
        }


@dataclass(frozen=True)
class ModuleReactionAudit:
    """Payload-free structured audit metadata for one reaction dispatch."""

    reaction_dispatch_id: str = field(default_factory=lambda: f"modreact_{uuid4().hex}")
    event: ModuleEventProvenance = field(
        default_factory=lambda: ModuleEventProvenance(event_id=None, event_type="")
    )
    reaction: ModuleReactionProvenance = field(
        default_factory=lambda: ModuleReactionProvenance(
            reaction_id="",
            source_module_id="",
            target_kind="unknown",
        )
    )
    app_id: str | None = None
    tenant_id: str | None = None
    workspace_id: str | None = None
    actor_id: str | None = None
    correlation_id: str | None = None
    causation_id: str | None = None
    outcome: ReactionAuditOutcome = "ok"
    reason: str | None = None
    audit_tags: Mapping[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "reaction_dispatch_id": self.reaction_dispatch_id,
            "event": self.event.to_dict(),
            "reaction": self.reaction.to_dict(),
            "app_id": self.app_id,
            "tenant_id": self.tenant_id,
            "workspace_id": self.workspace_id,
            "actor_id": self.actor_id,
            "correlation_id": self.correlation_id,
            "causation_id": self.causation_id,
            "outcome": self.outcome,
            "reason": self.reason,
            "audit_tags": dict(self.audit_tags),
        }


def normalize_module_event_provenance(
    event_type: str,
    envelope: dict[str, Any],
) -> ModuleEventProvenance:
    """Normalize structured and flat historical event envelopes into provenance."""

    envelope = envelope if isinstance(envelope, dict) else {}
    source = envelope.get("source") if isinstance(envelope.get("source"), dict) else {}
    tenant = envelope.get("tenant") if isinstance(envelope.get("tenant"), dict) else {}
    actor = envelope.get("actor") if isinstance(envelope.get("actor"), dict) else {}
    correlation = envelope.get("correlation") if isinstance(envelope.get("correlation"), dict) else {}

    is_structured = any(
        isinstance(envelope.get(key), dict)
        for key in ("source", "tenant", "actor", "correlation", "payload")
    ) or str(envelope.get("type") or "").strip() == event_type
    envelope_shape: EventEnvelopeShape = "structured" if is_structured else "legacy_flat"

    producer_layer = _clean(source.get("layer"))
    if envelope_shape == "legacy_flat":
        trust_shape: EventTrustShape = "legacy_flat"
    elif producer_layer == "module":
        trust_shape = "module_envelope"
    elif producer_layer in {"platform", "runtime"}:
        trust_shape = "platform_envelope"
    else:
        trust_shape = "unknown"

    app_id = _clean(tenant.get("app_id")) or _clean(envelope.get("app_id"))
    tenant_id = _clean(tenant.get("tenant_id")) or _clean(envelope.get("tenant_id"))
    workspace_id = _clean(tenant.get("workspace_id")) or _clean(envelope.get("workspace_id"))
    correlation_id = (
        _clean(correlation.get("correlation_id"))
        or _clean(envelope.get("correlation_id"))
        or _clean(envelope.get("corr"))
    )
    causation_id = _clean(correlation.get("causation_id")) or _clean(envelope.get("causation_id"))
    capability_id = _clean(source.get("capability_id"))
    producer_module_id = _clean(source.get("module_id"))
    producer_action_id = _clean(source.get("action_id"))
    if not producer_action_id and capability_id and "." in capability_id:
        producer_module_id = producer_module_id or capability_id.split(".", 1)[0]
        producer_action_id = capability_id.split(".", 1)[1]

    return ModuleEventProvenance(
        event_id=_clean(envelope.get("id")) or _clean(envelope.get("event_id")),
        event_type=_clean(envelope.get("type")) or event_type,
        producer_layer=producer_layer,
        producer_app_id=_clean(source.get("app_id")),
        producer_module_id=producer_module_id,
        producer_action_id=producer_action_id,
        capability_id=capability_id,
        tenant_id=tenant_id,
        app_id=app_id,
        workspace_id=workspace_id,
        actor_type=_clean(actor.get("type")),
        actor_id=_clean(actor.get("id")) or _clean(envelope.get("user_id")),
        correlation_id=correlation_id,
        causation_id=causation_id,
        envelope_shape=envelope_shape,
        trust_shape=trust_shape,
    )


def normalize_module_reaction_provenance(reaction: dict[str, Any]) -> ModuleReactionProvenance:
    """Normalize one reactions.yaml reaction dict into provenance."""

    target = reaction.get("target") if isinstance(reaction.get("target"), dict) else {}
    target_kind = _clean(target.get("kind")) or "unknown"
    if target_kind not in {"handler", "capability", "notification", "service_adapter"}:
        target_kind = "unknown"

    target_ref: str | None = None
    if target_kind == "handler":
        target_ref = _clean(target.get("handler_method"))
    elif target_kind == "capability":
        target_ref = _clean(target.get("capability_id"))
    elif target_kind == "notification":
        target_ref = _clean(target.get("notification_id"))
    elif target_kind == "service_adapter":
        adapter = _clean(target.get("adapter"))
        adapter_method = _clean(target.get("adapter_method"))
        target_ref = f"{adapter}.{adapter_method}" if adapter and adapter_method else adapter or adapter_method

    permissions = reaction.get("permissions")
    declared_permissions = (
        tuple(str(item).strip() for item in permissions if str(item).strip())
        if isinstance(permissions, list)
        else ()
    )

    return ModuleReactionProvenance(
        reaction_id=_clean(reaction.get("id")) or "",
        source_module_id=_clean(reaction.get("module_id")) or "",
        target_kind=target_kind,  # type: ignore[arg-type]
        target_ref=target_ref,
        idempotency_key=_clean(reaction.get("idempotency_key")),
        declared_permissions=declared_permissions,
        permissions_enforced=False,
        idempotency_enforced=False,
    )


def build_module_reaction_audit(
    *,
    event: ModuleEventProvenance,
    reaction: ModuleReactionProvenance,
    outcome: ReactionAuditOutcome,
    reason: str | None = None,
    audit_tags: Mapping[str, str] | None = None,
) -> ModuleReactionAudit:
    """Build payload-free reaction audit metadata."""

    return ModuleReactionAudit(
        event=event,
        reaction=reaction,
        app_id=event.app_id,
        tenant_id=event.tenant_id,
        workspace_id=event.workspace_id,
        actor_id=event.actor_id,
        correlation_id=event.correlation_id,
        causation_id=event.causation_id,
        outcome=outcome,
        reason=reason,
        audit_tags=dict(audit_tags or {}),
    )


def _clean(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None
