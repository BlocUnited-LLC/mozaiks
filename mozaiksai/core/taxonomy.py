"""Versioned semantic identifier registry for ADR 0007 Slice 1.

The taxonomy owns names and reference grammar only.  In particular, artifact
entries identify ``layout_registry`` rows; they intentionally cannot carry
paths, renderers, validators, or any other layout metadata.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

SCHEMA_VERSION: Literal["mozaiks.taxonomy.v1"] = "mozaiks.taxonomy.v1"


class TaxonomyError(ValueError):
    """Base error for an invalid registry or unresolved semantic name."""


class UnknownTaxonomyIdentifier(TaxonomyError):
    """Raised when advisory validation cannot resolve an identifier."""


class SemanticCategory(StrEnum):
    EVENT = "event"
    CAPABILITY = "capability"
    ARTIFACT_FAMILY = "artifact_family"


class NamespaceKind(StrEnum):
    CORE = "core"
    EXTENSION = "extension"


_GRAMMARS: dict[SemanticCategory, re.Pattern[str]] = {
    SemanticCategory.EVENT: re.compile(r"^[a-z0-9_]+(?:\.[a-z0-9_]+)+$"),
    SemanticCategory.CAPABILITY: re.compile(r"^[a-z0-9_]+(?:\.[a-z0-9_]+)*$"),
    SemanticCategory.ARTIFACT_FAMILY: re.compile(r"^[a-z][a-z0-9_]*$"),
}

_GRANDFATHERED_EVENT_IDENTIFIERS = frozenset({"error"})


def validate_identifier_grammar(category: SemanticCategory | str, identifier: str) -> str:
    """Validate one category's canonical grammar and return a trimmed value."""
    resolved_category = SemanticCategory(category)
    value = str(identifier or "").strip()
    if (
        not value
        or (
            _GRAMMARS[resolved_category].fullmatch(value) is None
            and not (
                resolved_category is SemanticCategory.EVENT
                and value in _GRANDFATHERED_EVENT_IDENTIFIERS
            )
        )
    ):
        if resolved_category is SemanticCategory.CAPABILITY:
            expected = "[a-z0-9_.]+"
        elif resolved_category is SemanticCategory.EVENT:
            expected = "a dot-namespaced lowercase event identifier"
        else:
            expected = "a lowercase underscore artifact-family identifier"
        raise TaxonomyError(
            f"{resolved_category.value} identifier must match {expected}, got {value!r}"
        )
    return value


class TaxonomyModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class TaxonomyEntry(TaxonomyModel):
    category: SemanticCategory
    identifier: str

    @field_validator("identifier", mode="before")
    @classmethod
    def _normalize_identifier(cls, value: str) -> str:
        return str(value or "").strip()

    @model_validator(mode="after")
    def _validate_identifier(self) -> TaxonomyEntry:
        validate_identifier_grammar(self.category, self.identifier)
        return self

    @property
    def identity_payload(self) -> dict[str, str]:
        return {"category": self.category.value, "identifier": self.identifier}


class TaxonomyNamespace(TaxonomyModel):
    namespace_id: str
    version: int = Field(ge=1)
    kind: NamespaceKind
    grants: tuple[str, ...] = Field(default_factory=tuple)
    entries: tuple[TaxonomyEntry, ...] = Field(default_factory=tuple)

    @field_validator("namespace_id")
    @classmethod
    def _namespace_id(cls, value: str) -> str:
        text = str(value or "").strip()
        if re.fullmatch(r"[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)*", text) is None:
            raise ValueError("namespace_id must be a lowercase dotted identifier")
        return text

    @field_validator("grants")
    @classmethod
    def _grants(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(sorted({str(item or "").strip().rstrip(".") for item in value}))
        if any(re.fullmatch(r"[a-z][a-z0-9_]*", item) is None for item in normalized):
            raise ValueError("namespace grants must be lowercase identifier prefixes")
        return normalized

    @field_validator("entries")
    @classmethod
    def _entries(cls, value: tuple[TaxonomyEntry, ...]) -> tuple[TaxonomyEntry, ...]:
        return tuple(sorted(value, key=lambda item: (item.category.value, item.identifier)))

    @model_validator(mode="after")
    def _validate_extension_grants(self) -> TaxonomyNamespace:
        identities = [(entry.category, entry.identifier) for entry in self.entries]
        if len(identities) != len(set(identities)):
            raise ValueError(f"duplicate identifiers in namespace {self.namespace_id!r}")
        if self.kind is NamespaceKind.EXTENSION:
            if not self.grants:
                raise ValueError("extension namespaces require at least one granted prefix")
            for entry in self.entries:
                if entry.category is SemanticCategory.ARTIFACT_FAMILY:
                    raise ValueError(
                        "Slice 1 extensions cannot introduce artifact-family identifiers"
                    )
                if not any(
                    entry.identifier == grant or entry.identifier.startswith(f"{grant}.")
                    for grant in self.grants
                ):
                    raise ValueError(
                        f"extension entry {entry.identifier!r} is outside granted prefixes {self.grants!r}"
                    )
        return self

    @property
    def identity_payload(self) -> dict[str, Any]:
        return {
            "namespace_id": self.namespace_id,
            "version": self.version,
            "kind": self.kind.value,
            "grants": list(self.grants),
            "entries": [entry.identity_payload for entry in self.entries],
        }


class TaxonomyRegistry(TaxonomyModel):
    schema_version: Literal["mozaiks.taxonomy.v1"] = SCHEMA_VERSION
    namespaces: tuple[TaxonomyNamespace, ...] = Field(min_length=1)
    registry_digest: str = Field(min_length=64, max_length=64)

    @field_validator("namespaces")
    @classmethod
    def _namespaces(cls, value: tuple[TaxonomyNamespace, ...]) -> tuple[TaxonomyNamespace, ...]:
        return tuple(sorted(value, key=lambda item: (item.namespace_id, item.version)))

    @model_validator(mode="after")
    def _validate_registry(self) -> TaxonomyRegistry:
        identities = [(item.namespace_id, item.version) for item in self.namespaces]
        if len(identities) != len(set(identities)):
            raise ValueError("duplicate namespace/version identity")

        namespace_ids: dict[str, TaxonomyNamespace] = {}
        for namespace in self.namespaces:
            previous = namespace_ids.get(namespace.namespace_id)
            if previous is not None:
                raise ValueError(
                    f"namespace id {namespace.namespace_id!r} is already owned by "
                    f"{previous.kind.value} namespace version {previous.version}"
                )
            namespace_ids[namespace.namespace_id] = namespace

        protected_core_roots = {
            (entry.category, entry.identifier.split(".", 1)[0])
            for namespace in self.namespaces
            if namespace.kind is NamespaceKind.CORE
            for entry in namespace.entries
        }

        seen_entries: dict[tuple[SemanticCategory, str], TaxonomyNamespace] = {}
        grants: list[tuple[str, NamespaceKind, str]] = []
        for namespace in self.namespaces:
            for grant in namespace.grants:
                for prior_grant, prior_kind, prior_namespace in grants:
                    if (
                        grant == prior_grant
                        or grant.startswith(f"{prior_grant}.")
                        or prior_grant.startswith(f"{grant}.")
                    ):
                        if namespace.namespace_id != prior_namespace:
                            raise ValueError(
                                f"namespace grant {grant!r} conflicts with {prior_kind.value} namespace {prior_namespace!r}"
                            )
                grants.append((grant, namespace.kind, namespace.namespace_id))
            for entry in namespace.entries:
                if (
                    namespace.kind is NamespaceKind.EXTENSION
                    and (entry.category, entry.identifier.split(".", 1)[0])
                    in protected_core_roots
                ):
                    raise ValueError(
                        f"extension namespace {namespace.namespace_id!r} cannot occupy protected "
                        f"core {entry.category.value} root {entry.identifier.split('.', 1)[0]!r}"
                    )
                key = (entry.category, entry.identifier)
                previous = seen_entries.get(key)
                if previous is not None:
                    raise ValueError(
                        f"duplicate or conflicting {entry.category.value} identifier {entry.identifier!r} "
                        f"in {previous.namespace_id!r} and {namespace.namespace_id!r}"
                    )
                seen_entries[key] = namespace

        expected = _stable_digest(self.canonical_payload(include_digest=False))
        if self.registry_digest != expected:
            raise ValueError("registry_digest does not match taxonomy content")
        return self

    def canonical_payload(self, *, include_digest: bool = True) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "schema_version": self.schema_version,
            "namespaces": [namespace.identity_payload for namespace in self.namespaces],
        }
        if include_digest:
            payload["registry_digest"] = self.registry_digest
        return payload

    def canonical_json(self) -> str:
        return json.dumps(
            self.canonical_payload(), sort_keys=True, separators=(",", ":"), ensure_ascii=True
        )

    def resolve(self, category: SemanticCategory | str, identifier: str) -> TaxonomyEntry:
        resolved_category = SemanticCategory(category)
        value = validate_identifier_grammar(resolved_category, identifier)
        for namespace in self.namespaces:
            for entry in namespace.entries:
                if entry.category is resolved_category and entry.identifier == value:
                    return entry
        raise UnknownTaxonomyIdentifier(f"unknown {resolved_category.value} identifier {value!r}")

    def validate_closure(self, references: Iterable[tuple[SemanticCategory | str, str]]) -> None:
        for category, identifier in references:
            self.resolve(category, identifier)


def build_taxonomy_registry(namespaces: Iterable[TaxonomyNamespace]) -> TaxonomyRegistry:
    ordered = tuple(sorted(tuple(namespaces), key=lambda item: (item.namespace_id, item.version)))
    payload = {
        "schema_version": SCHEMA_VERSION,
        "namespaces": [namespace.identity_payload for namespace in ordered],
    }
    return TaxonomyRegistry(
        namespaces=ordered,
        registry_digest=_stable_digest(payload),
    )


_CORE_EVENTS = (
    "error",
    "ack.artifact_action",
    "ack.tool_call_response",
    "artifact.action.completed",
    "artifact.action.failed",
    "artifact.action.started",
    "artifact.action",
    "artifact.created",
    "artifact.deleted",
    "artifact.ready",
    "artifact.state.updated",
    "artifact.updated",
    "build.completed",
    "build.failed",
    "build.started",
    "client.resume",
    "runtime.agent_output_validated",
    "runtime.handoff",
    "runtime.process_completed",
    "runtime.reaction.dead_lettered",
    "runtime.workflow_paused",
    "notification.count_changed",
    "notification.created",
    "platform.workflow_capability_started",
    "user.input.submit",
    "workflow.completed",
    "chat.tool_call_complete",
    "chat.transition_requested",
    "chat.revision_requested",
    "chat.deployment_started",
    "chat.deployment_progress",
    "chat.deployment_completed",
    "chat.deployment_failed",
    "ui.dismiss",
    "ui.render",
    "ui.update",
    "domain.app_registry.app_created",
    "domain.app_registry.app_deleted",
    "domain.app_registry.app_promoted",
    "domain.app_registry.status_changed",
    "domain.commerce.cart.updated",
    "domain.commerce.checkout.failed",
    "domain.commerce.checkout.requested",
    "domain.commerce.inventory.adjusted",
    "domain.commerce.order.paid",
    "domain.commerce.order.updated",
    "domain.commerce.product.archived",
    "domain.commerce.product.created",
    "domain.commerce.product.updated",
    "domain.files.file_deleted",
    "domain.files.file_uploaded",
    "domain.messages.message_sent",
    "domain.messages.thread_created",
    "domain.messages.thread_read",
    "domain.messaging_core.message_sent",
    "domain.onboarding.dismissed",
    "domain.onboarding.step_completed",
    "domain.reports.generated",
    "domain.social.activity.recorded",
    "domain.social.friend_request.declined",
    "domain.social.friend_request.sent",
    "domain.social.friend_request.withdrawn",
    "domain.social.friendship.created",
    "domain.social.friendship.removed",
    "domain.social.post.comment_deleted",
    "domain.social.post.commented",
    "domain.social.post.deleted",
    "domain.social.post.published",
    "domain.social.post.reacted",
    "domain.support.request_created",
    "domain.support.status_changed",
    "domain.support_ticket.batch_requested",
    "domain.task_manager.task_created",
    "domain.users.user_created",
    "domain.workspace_integrations.declaration_removed",
    "domain.workspace_integrations.declarations_saved",
    "domain.workspace_integrations.note_updated",
    "domain.workspace_support.message_added",
    "domain.workspace_support.negative_feedback",
    "domain.workspace_support.request_created",
    "domain.workspace_support.request_deleted",
    "domain.workspace_support.request_status_changed",
    "hosted.billing.subscription.activated",
    "hosted.billing.subscription.cancelled",
    "hosted.billing.subscription.updated",
    "hosted.onboarding.step_completed",
)

_CORE_CAPABILITIES = (
    "app_registry.create",
    "app_registry.list",
    "billing_portal",
    "cloud.deployment.health",
    "cloud.deployment.rollback",
    "cloud.deployment.status",
    "cloud.deployment.submit",
    "cloud.domain.connect",
    "cloud.domain.disconnect",
    "cloud.domain.status",
    "cloud.environment.endpoints",
    "cloud.usage.report",
    "commerce.cart.manage",
    "commerce.checkout.start",
    "commerce.inventory.manage",
    "commerce.orders.manage",
    "commerce.products.browse",
    "commerce.products.manage",
    "files.delete",
    "files.read",
    "files.upload",
    "messaging.messages.send",
    "messaging.threads.list",
    "mozaikspay.billing_portal",
    "mozaikspay.checkout.create_session",
    "mozaikspay.subscription_checkout",
    "mozaikspay.subscription_status",
    "mozaikspay.token_status",
    "mozaikspay.token_top_up",
    "mozaikspay.usage_status",
    "notifications.email.send",
    "notifications.preferences.read",
    "notifications.preferences.write",
    "notifications.push.send",
    "onboarding.tour.dismiss",
    "onboarding.tour.progress.read",
    "onboarding.tour.show",
    "operator_readiness.evidence.local",
    "operator_readiness.launch.check",
    "operator_readiness.profile.select",
    "reports.export",
    "reports.view",
    "social.feed.read",
    "social.friends.connect",
    "social.friends.list",
    "social.posts.comment",
    "social.posts.create",
    "social.posts.list",
    "social.posts.react",
    "support.requests.create",
    "support.requests.list",
    "support.requests.update_status",
    "subscriptions",
    "usage_billing",
    "workspace_integrations.get",
    "workspace_integrations.list",
)


def default_taxonomy_registry() -> TaxonomyRegistry:
    from mozaiksai.core.runtime.app.layout_registry import default_app_layout_registry
    from mozaiksai.core.transport.event_contract import MozaiksEventType

    event_names = sorted({*_CORE_EVENTS, *(item.value for item in MozaiksEventType)})
    artifact_kinds = default_app_layout_registry().iter_artifact_kinds()
    namespaces = (
        TaxonomyNamespace(
            namespace_id="mozaiks.events",
            version=1,
            kind=NamespaceKind.CORE,
            grants=(
                "ack",
                "artifact",
                "build",
                "chat",
                "client",
                "domain",
                "hosted",
                "mozaikspay",
                "notification",
                "platform",
                "runtime",
                "ui",
                "user",
                "workflow",
            ),
            entries=tuple(
                TaxonomyEntry(category=SemanticCategory.EVENT, identifier=item)
                for item in event_names
            ),
        ),
        TaxonomyNamespace(
            namespace_id="mozaiks.capabilities",
            version=1,
            kind=NamespaceKind.CORE,
            entries=tuple(
                TaxonomyEntry(category=SemanticCategory.CAPABILITY, identifier=item)
                for item in _CORE_CAPABILITIES
            ),
        ),
        TaxonomyNamespace(
            namespace_id="mozaiks.artifact_families",
            version=1,
            kind=NamespaceKind.CORE,
            entries=tuple(
                TaxonomyEntry(category=SemanticCategory.ARTIFACT_FAMILY, identifier=item.value)
                for item in artifact_kinds
            ),
        ),
    )
    return build_taxonomy_registry(namespaces)


def validate_registered_identifier(
    category: SemanticCategory | str,
    identifier: str,
    *,
    advisory: bool,
    registry: TaxonomyRegistry | None = None,
) -> str:
    """Fail closed only on the explicit Slice 1 advisory path."""
    value = str(identifier or "").strip()
    if advisory:
        (registry or default_taxonomy_registry()).resolve(category, value)
    return value


def _stable_digest(value: Any) -> str:
    canonical = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(canonical.encode("ascii")).hexdigest()


__all__ = [
    "NamespaceKind",
    "SCHEMA_VERSION",
    "SemanticCategory",
    "TaxonomyEntry",
    "TaxonomyError",
    "TaxonomyNamespace",
    "TaxonomyRegistry",
    "UnknownTaxonomyIdentifier",
    "build_taxonomy_registry",
    "default_taxonomy_registry",
    "validate_identifier_grammar",
    "validate_registered_identifier",
]
