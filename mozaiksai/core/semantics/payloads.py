"""ADR 0007 Slice 2E typed semantic payload documents (``mozaiks.semantic_payload.v1``).

Exactly one strict payload variant exists per :class:`SemanticNodeKind`; a
graph-v2 node pins its payload by full identity through
:class:`~mozaiksai.core.semantics.refs.SemanticPayloadRef`.  Payloads carry the
content a node's identity cannot — titles, intent text, typed field shapes,
prices, ordered entries — never a second copy of identity facts the node
already owns, and never untyped ``dict[str, Any]`` escape hatches.

Ordering rule: order-bearing collections (page sections, section entries) use
explicit dense ``position`` integers, so canonical sorting cannot destroy
source order; identity collections (fields, emits, hints) are sorted and
deduplicated by key.  Prices are integer minor units — floats never carry
money.  Event and capability identifiers validate through the Slice 1
taxonomy grammar; portable paths validate through the Slice 4A profile.

``payload_digest`` is the canonical digest (Slice 2 serializer — the only
serializer) over the payload with the digest field excluded; every parse
re-verifies it, so a tampered field or digest fails closed at validation.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import Field, TypeAdapter, ValidationInfo, field_validator, model_validator

from mozaiksai.core.semantics.canonical import canonical_digest
from mozaiksai.core.semantics.graph import SemanticGraphV2, SemanticNodeKind
from mozaiksai.core.semantics.portable_path import validate_portable_path
from mozaiksai.core.semantics.refs import (
    ExecutionAccessScopeRef,
    SemanticPayloadRef,
    SemanticsModel,
    _validate_digest,
    validate_node_id_grammar,
)
from mozaiksai.core.stub_kinds import StubKind
from mozaiksai.core.taxonomy import SemanticCategory, validate_identifier_grammar

SEMANTIC_PAYLOAD_SCHEMA_VERSION: Literal["mozaiks.semantic_payload.v1"] = (
    "mozaiks.semantic_payload.v1"
)

_MAX_TEXT_CHARS = 4000
_FIELD_NAME = re.compile(r"^[a-z][a-z0-9_]*$")
_ENTRYPOINT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
# ISO 4217 Maintenance Agency List One (current currencies and funds),
# captured for this schema version on 2026-08-29.  Shape validation alone is
# insufficient: unassigned uppercase triples such as ``ZZZ`` fail closed.
_ISO_4217_LIST_ONE_CODES = frozenset(
    """
    AED AFN ALL AMD AOA ARS AUD AWG AZN BAM BBD BDT BHD BIF BMD BND BOB BOV BRL
    BSD BTN BWP BYN BZD CAD CDF CHE CHF CHW CLF CLP CNY COP COU CRC CUP CVE CZK
    DJF DKK DOP DZD EGP ERN ETB EUR FJD FKP GBP GEL GHS GIP GMD GNF GTQ GYD HKD
    HNL HTG HUF IDR ILS INR IQD IRR ISK JMD JOD JPY KES KGS KHR KMF KPW KRW KWD
    KYD KZT LAK LBP LKR LRD LSL LYD MAD MDL MGA MKD MMK MNT MOP MRU MUR MVR MWK
    MXN MXV MYR MZN NAD NGN NIO NOK NPR NZD OMR PAB PEN PGK PHP PKR PLN PYG QAR
    RON RSD RUB RWF SAR SBD SCR SDG SEK SGD SHP SLE SOS SRD SSP STN SVC SYP SZL
    THB TJS TMT TND TOP TRY TTD TWD TZS UAH UGX USD USN UYI UYU UYW UZS VED VES
    VND VUV WST XAD XAF XAG XAU XBA XBB XBC XBD XCD XCG XDR XOF XPD XPF XPT
    XSU XTS XUA XXX YER ZAR ZMW ZWG
    """.split()
)

#: Validation-context key used exclusively by :func:`build_semantic_payload`
#: to defer the digest check while computing the digest.  It is a validation
#: parameter, not document data — serialized payloads cannot smuggle it.
_BUILDER_CONTEXT_KEY = "semantic_payload_builder"
_PLACEHOLDER_DIGEST = "0" * 64


class SemanticPayloadError(ValueError):
    """Raised when a payload document violates the contract."""


def _text(value: str, *, field_name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{field_name} must be non-empty text")
    if len(text) > _MAX_TEXT_CHARS:
        raise ValueError(f"{field_name} exceeds {_MAX_TEXT_CHARS} characters")
    return text


def _field_name(value: str, *, field_name: str) -> str:
    text = str(value or "").strip()
    if _FIELD_NAME.fullmatch(text) is None:
        raise ValueError(f"{field_name} must be a lowercase snake_case name, got {value!r}")
    return text


# ---------------------------------------------------------------------------
# Closed vocabulary enums (structured-output-first: no freeform strings)
# ---------------------------------------------------------------------------


class FieldType(StrEnum):
    """Closed field-type set for typed request/response/data shapes."""

    STRING = "string"
    TEXT = "text"
    INTEGER = "integer"
    NUMBER = "number"
    BOOLEAN = "boolean"
    DATETIME = "datetime"
    REFERENCE = "reference"


class SectionEntryKind(StrEnum):
    TEXT = "text"
    MODULE_BINDING = "module_binding"
    ACTION_BINDING = "action_binding"
    API_BINDING = "api_binding"


class HttpMethod(StrEnum):
    GET = "GET"
    POST = "POST"
    PUT = "PUT"
    PATCH = "PATCH"
    DELETE = "DELETE"


class NotificationChannel(StrEnum):
    IN_APP = "in_app"
    EMAIL = "email"
    SMS = "sms"
    WEBHOOK = "webhook"


class WorkflowStartupMode(StrEnum):
    ON_DEMAND = "on_demand"
    EVENT_DRIVEN = "event_driven"
    SCHEDULED = "scheduled"


class TriggerKind(StrEnum):
    EVENT = "event"
    ENDPOINT = "endpoint"
    CAPABILITY = "capability"


class BillingPeriod(StrEnum):
    ONE_TIME = "one_time"
    MONTHLY = "monthly"
    YEARLY = "yearly"


class DeploymentTargetKind(StrEnum):
    CONTAINER = "container"
    STATIC_SITE = "static_site"
    SERVERLESS = "serverless"


# ---------------------------------------------------------------------------
# Typed sub-shapes
# ---------------------------------------------------------------------------


class TypedFieldSpec(SemanticsModel):
    """One typed field in a request/response/data/event shape."""

    name: str
    field_type: FieldType
    required: bool

    @field_validator("name")
    @classmethod
    def _name(cls, value: str) -> str:
        return _field_name(value, field_name="name")


class IndexSpec(SemanticsModel):
    """One declared index over a data collection's fields."""

    name: str
    field_names: tuple[str, ...] = Field(min_length=1)
    unique: bool

    @field_validator("name")
    @classmethod
    def _name(cls, value: str) -> str:
        return _field_name(value, field_name="name")

    @field_validator("field_names")
    @classmethod
    def _field_names(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(_field_name(item, field_name="field_names") for item in value)
        if len(normalized) != len(set(normalized)):
            raise ValueError("duplicate field names in one index")
        return normalized


class PageSectionEntry(SemanticsModel):
    """Ordered reference from a page to a section node."""

    position: int = Field(ge=0, strict=True)
    section_node_id: str

    @field_validator("section_node_id")
    @classmethod
    def _section(cls, value: str) -> str:
        return validate_node_id_grammar(value)


class SectionContentEntry(SemanticsModel):
    """One ordered entry inside a section: text or a typed binding."""

    position: int = Field(ge=0, strict=True)
    entry_kind: SectionEntryKind
    text: str | None = None
    target_node_id: str | None = None
    api_method: HttpMethod | None = None
    api_path: str | None = None

    @field_validator("text")
    @classmethod
    def _text_value(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _text(value, field_name="text")

    @field_validator("target_node_id")
    @classmethod
    def _target(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return validate_node_id_grammar(value)

    @field_validator("api_path")
    @classmethod
    def _api_path(cls, value: str | None) -> str | None:
        if value is None:
            return None
        text = str(value or "").strip()
        if not text.startswith("/") or " " in text:
            raise ValueError(f"api_path must be an absolute route path, got {value!r}")
        return text

    @model_validator(mode="after")
    def _shape(self) -> SectionContentEntry:
        if self.entry_kind is SectionEntryKind.TEXT:
            if self.text is None or self.target_node_id or self.api_method or self.api_path:
                raise ValueError("text entries carry text and no binding fields")
        elif self.entry_kind in (SectionEntryKind.MODULE_BINDING, SectionEntryKind.ACTION_BINDING):
            if self.target_node_id is None or self.text or self.api_method or self.api_path:
                raise ValueError(
                    f"{self.entry_kind.value} entries carry target_node_id and nothing else"
                )
        else:  # API_BINDING
            if self.api_method is None or self.api_path is None or self.text or self.target_node_id:
                raise ValueError("api_binding entries carry api_method and api_path only")
        return self


class PriceSpec(SemanticsModel):
    """One price point: integer minor units, ISO-4217 currency, billing period."""

    amount_minor_units: int = Field(ge=0, strict=True)
    currency: str
    period: BillingPeriod

    @field_validator("currency")
    @classmethod
    def _currency(cls, value: str) -> str:
        text = str(value or "").strip()
        if text not in _ISO_4217_LIST_ONE_CODES:
            raise ValueError(f"currency must be a current ISO-4217 code, got {value!r}")
        return text


def _ordered_dense(entries: tuple, *, field_name: str) -> tuple:
    """Enforce dense 0..n-1 explicit positions and order by them."""
    positions = sorted(entry.position for entry in entries)
    if positions != list(range(len(entries))):
        raise ValueError(
            f"{field_name} positions must be dense 0..{max(len(entries) - 1, 0)}, got {positions}"
        )
    return tuple(sorted(entries, key=lambda entry: entry.position))


def _sorted_prices(value: tuple[PriceSpec, ...]) -> tuple[PriceSpec, ...]:
    ordered = tuple(sorted(value, key=lambda item: (item.currency, item.period.value)))
    keys = [(item.currency, item.period) for item in ordered]
    if len(keys) != len(set(keys)):
        raise ValueError("duplicate price for one (currency, period)")
    return ordered


def _sorted_event_ids(value: tuple[str, ...], *, field_name: str) -> tuple[str, ...]:
    normalized = tuple(
        sorted({validate_identifier_grammar(SemanticCategory.EVENT, item) for item in value})
    )
    if len(normalized) != len(value):
        raise ValueError(f"duplicate event identifiers in {field_name}")
    return normalized


# ---------------------------------------------------------------------------
# Payload base + one strict variant per node kind
# ---------------------------------------------------------------------------


class SemanticPayloadBase(SemanticsModel):
    """Shared identity/digest shape of every payload variant."""

    payload_schema_version: Literal["mozaiks.semantic_payload.v1"] = (
        SEMANTIC_PAYLOAD_SCHEMA_VERSION
    )
    node_id: str
    payload_version: int = Field(ge=1, strict=True)
    scope: ExecutionAccessScopeRef
    payload_digest: str

    @field_validator("node_id")
    @classmethod
    def _node_id(cls, value: str) -> str:
        return validate_node_id_grammar(value)

    @field_validator("payload_digest")
    @classmethod
    def _digest_field(cls, value: str) -> str:
        return _validate_digest(value, field_name="payload_digest")

    @model_validator(mode="after")
    def _verify_digest(self, info: ValidationInfo) -> SemanticPayloadBase:
        if info.context is not None and info.context.get(_BUILDER_CONTEXT_KEY):
            return self
        expected = canonical_digest(self.canonical_payload(include_digest=False))
        if self.payload_digest != expected:
            raise ValueError("payload_digest does not match payload content")
        return self

    def canonical_payload(self, *, include_digest: bool = True) -> dict[str, Any]:
        """Canonical digest payload: every field participates except the digest."""
        payload = self.model_dump(mode="json", exclude={"payload_digest"})
        if include_digest:
            payload["payload_digest"] = self.payload_digest
        return payload


class SurfacePayload(SemanticPayloadBase):
    payload_kind: Literal[SemanticNodeKind.SURFACE] = SemanticNodeKind.SURFACE
    description: str | None

    @field_validator("description")
    @classmethod
    def _description(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _text(value, field_name="description")


class PagePayload(SemanticPayloadBase):
    payload_kind: Literal[SemanticNodeKind.PAGE] = SemanticNodeKind.PAGE
    title: str | None
    intent: str | None
    layout_id: str | None = None
    sections: tuple[PageSectionEntry, ...] = Field(default_factory=tuple)

    @field_validator("title", "intent")
    @classmethod
    def _texts(cls, value: str | None, info: ValidationInfo) -> str | None:
        if value is None:
            return None
        return _text(value, field_name=str(info.field_name))

    @field_validator("layout_id")
    @classmethod
    def _layout(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _field_name(value, field_name="layout_id")

    @field_validator("sections")
    @classmethod
    def _sections(cls, value: tuple[PageSectionEntry, ...]) -> tuple[PageSectionEntry, ...]:
        ordered = _ordered_dense(value, field_name="sections")
        section_ids = [entry.section_node_id for entry in ordered]
        if len(section_ids) != len(set(section_ids)):
            raise ValueError("duplicate section references on one page")
        return ordered


class SectionPayload(SemanticPayloadBase):
    payload_kind: Literal[SemanticNodeKind.SECTION] = SemanticNodeKind.SECTION
    title: str | None
    intent: str | None
    entries: tuple[SectionContentEntry, ...] = Field(default_factory=tuple)

    @field_validator("title", "intent")
    @classmethod
    def _texts(cls, value: str | None, info: ValidationInfo) -> str | None:
        if value is None:
            return None
        return _text(value, field_name=str(info.field_name))

    @field_validator("entries")
    @classmethod
    def _entries(cls, value: tuple[SectionContentEntry, ...]) -> tuple[SectionContentEntry, ...]:
        return _ordered_dense(value, field_name="entries")


class ModulePayload(SemanticPayloadBase):
    payload_kind: Literal[SemanticNodeKind.MODULE] = SemanticNodeKind.MODULE
    description: str | None

    @field_validator("description")
    @classmethod
    def _description(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _text(value, field_name="description")


class ActionPayload(SemanticPayloadBase):
    payload_kind: Literal[SemanticNodeKind.ACTION] = SemanticNodeKind.ACTION
    description: str | None
    request_fields: tuple[TypedFieldSpec, ...] = Field(default_factory=tuple)
    response_fields: tuple[TypedFieldSpec, ...] = Field(default_factory=tuple)
    emits: tuple[str, ...] = Field(default_factory=tuple)
    entitlement_gate: str | None = None

    @field_validator("description")
    @classmethod
    def _description(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _text(value, field_name="description")

    @field_validator("request_fields", "response_fields")
    @classmethod
    def _fields(
        cls, value: tuple[TypedFieldSpec, ...], info: ValidationInfo
    ) -> tuple[TypedFieldSpec, ...]:
        ordered = tuple(sorted(value, key=lambda item: item.name))
        names = [item.name for item in ordered]
        if len(names) != len(set(names)):
            raise ValueError(f"duplicate field names in {info.field_name}")
        return ordered

    @field_validator("emits")
    @classmethod
    def _emits(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _sorted_event_ids(value, field_name="emits")

    @field_validator("entitlement_gate")
    @classmethod
    def _gate(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return validate_identifier_grammar(SemanticCategory.CAPABILITY, value)


class CapabilityPayload(SemanticPayloadBase):
    payload_kind: Literal[SemanticNodeKind.CAPABILITY] = SemanticNodeKind.CAPABILITY
    description: str | None

    @field_validator("description")
    @classmethod
    def _description(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _text(value, field_name="description")


class PermissionPayload(SemanticPayloadBase):
    payload_kind: Literal[SemanticNodeKind.PERMISSION] = SemanticNodeKind.PERMISSION
    description: str | None

    @field_validator("description")
    @classmethod
    def _description(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _text(value, field_name="description")


class EventPayload(SemanticPayloadBase):
    payload_kind: Literal[SemanticNodeKind.EVENT] = SemanticNodeKind.EVENT
    description: str | None
    payload_fields: tuple[TypedFieldSpec, ...] = Field(default_factory=tuple)

    @field_validator("description")
    @classmethod
    def _description(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _text(value, field_name="description")

    @field_validator("payload_fields")
    @classmethod
    def _fields(cls, value: tuple[TypedFieldSpec, ...]) -> tuple[TypedFieldSpec, ...]:
        ordered = tuple(sorted(value, key=lambda item: item.name))
        names = [item.name for item in ordered]
        if len(names) != len(set(names)):
            raise ValueError("duplicate field names in payload_fields")
        return ordered


class ReactionPayload(SemanticPayloadBase):
    payload_kind: Literal[SemanticNodeKind.REACTION] = SemanticNodeKind.REACTION
    description: str | None
    consumed_event: str | None

    @field_validator("description")
    @classmethod
    def _description(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _text(value, field_name="description")

    @field_validator("consumed_event")
    @classmethod
    def _event(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return validate_identifier_grammar(SemanticCategory.EVENT, value)


class NotificationPayload(SemanticPayloadBase):
    payload_kind: Literal[SemanticNodeKind.NOTIFICATION] = SemanticNodeKind.NOTIFICATION
    template_text: str | None
    channel: NotificationChannel | None

    @field_validator("template_text")
    @classmethod
    def _template(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _text(value, field_name="template_text")


class DataCollectionPayload(SemanticPayloadBase):
    payload_kind: Literal[SemanticNodeKind.DATA_COLLECTION] = SemanticNodeKind.DATA_COLLECTION
    description: str | None
    fields: tuple[TypedFieldSpec, ...] | None
    indexes: tuple[IndexSpec, ...] = Field(default_factory=tuple)

    @field_validator("description")
    @classmethod
    def _description(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _text(value, field_name="description")

    @field_validator("fields")
    @classmethod
    def _fields(
        cls, value: tuple[TypedFieldSpec, ...] | None
    ) -> tuple[TypedFieldSpec, ...] | None:
        if value is None:
            return None
        ordered = tuple(sorted(value, key=lambda item: item.name))
        names = [item.name for item in ordered]
        if len(names) != len(set(names)):
            raise ValueError("duplicate field names in fields")
        return ordered

    @field_validator("indexes")
    @classmethod
    def _indexes(cls, value: tuple[IndexSpec, ...]) -> tuple[IndexSpec, ...]:
        ordered = tuple(sorted(value, key=lambda item: item.name))
        names = [item.name for item in ordered]
        if len(names) != len(set(names)):
            raise ValueError("duplicate index names")
        return ordered

    @model_validator(mode="after")
    def _index_closure(self) -> DataCollectionPayload:
        declared = {field.name for field in self.fields or ()}
        for index in self.indexes:
            for name in index.field_names:
                if name not in declared:
                    raise ValueError(f"index {index.name!r} references undeclared field {name!r}")
        return self


class DataAliasPayload(SemanticPayloadBase):
    payload_kind: Literal[SemanticNodeKind.DATA_ALIAS] = SemanticNodeKind.DATA_ALIAS
    alias: str | None
    collection: str | None
    owner_node_id: str | None

    @field_validator("alias", "collection")
    @classmethod
    def _names(cls, value: str | None, info: ValidationInfo) -> str | None:
        if value is None:
            return None
        return _field_name(value, field_name=str(info.field_name))

    @field_validator("owner_node_id")
    @classmethod
    def _owner(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return validate_node_id_grammar(value)


class WorkflowPayload(SemanticPayloadBase):
    payload_kind: Literal[SemanticNodeKind.WORKFLOW] = SemanticNodeKind.WORKFLOW
    description: str | None
    startup_mode: WorkflowStartupMode | None

    @field_validator("description")
    @classmethod
    def _description(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _text(value, field_name="description")


class TriggerPayload(SemanticPayloadBase):
    payload_kind: Literal[SemanticNodeKind.TRIGGER] = SemanticNodeKind.TRIGGER
    description: str | None
    trigger_kind: TriggerKind | None
    event_id: str | None = None
    endpoint_path: str | None = None
    capability_id: str | None = None

    @field_validator("description")
    @classmethod
    def _description(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _text(value, field_name="description")

    @field_validator("event_id")
    @classmethod
    def _event(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return validate_identifier_grammar(SemanticCategory.EVENT, value)

    @field_validator("endpoint_path")
    @classmethod
    def _endpoint(cls, value: str | None) -> str | None:
        if value is None:
            return None
        text = str(value or "").strip()
        if not text.startswith("/") or " " in text:
            raise ValueError(f"endpoint_path must be an absolute route path, got {value!r}")
        return text

    @field_validator("capability_id")
    @classmethod
    def _capability(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return validate_identifier_grammar(SemanticCategory.CAPABILITY, value)

    @model_validator(mode="after")
    def _binding(self) -> TriggerPayload:
        bindings = {
            TriggerKind.EVENT: (self.event_id, "event_id"),
            TriggerKind.ENDPOINT: (self.endpoint_path, "endpoint_path"),
            TriggerKind.CAPABILITY: (self.capability_id, "capability_id"),
        }
        if self.trigger_kind is None:
            set_names = [name for value, name in bindings.values() if value is not None]
            if set_names:
                raise ValueError(
                    f"trigger bindings {set_names} require an explicit trigger_kind"
                )
            return self
        required_value, required_name = bindings[self.trigger_kind]
        if required_value is None:
            raise ValueError(
                f"trigger_kind {self.trigger_kind.value!r} requires {required_name}"
            )
        for kind, (value, name) in bindings.items():
            if kind is not self.trigger_kind and value is not None:
                raise ValueError(
                    f"trigger_kind {self.trigger_kind.value!r} must not set {name}"
                )
        return self


class PlanPayload(SemanticPayloadBase):
    payload_kind: Literal[SemanticNodeKind.PLAN] = SemanticNodeKind.PLAN
    title: str | None
    prices: tuple[PriceSpec, ...] | None
    granted_capabilities: tuple[str, ...] = Field(default_factory=tuple)

    @field_validator("title")
    @classmethod
    def _title(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _text(value, field_name="title")

    @field_validator("prices")
    @classmethod
    def _prices(cls, value: tuple[PriceSpec, ...] | None) -> tuple[PriceSpec, ...] | None:
        if value is None:
            return None
        return _sorted_prices(value)

    @field_validator("granted_capabilities")
    @classmethod
    def _grants(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(
            sorted(
                {
                    validate_identifier_grammar(SemanticCategory.CAPABILITY, item)
                    for item in value
                }
            )
        )
        if len(normalized) != len(value):
            raise ValueError("duplicate capability identifiers in granted_capabilities")
        return normalized


class ProductPayload(SemanticPayloadBase):
    payload_kind: Literal[SemanticNodeKind.PRODUCT] = SemanticNodeKind.PRODUCT
    title: str | None
    description: str | None
    prices: tuple[PriceSpec, ...] | None

    @field_validator("title", "description")
    @classmethod
    def _texts(cls, value: str | None, info: ValidationInfo) -> str | None:
        if value is None:
            return None
        return _text(value, field_name=str(info.field_name))

    @field_validator("prices")
    @classmethod
    def _prices(cls, value: tuple[PriceSpec, ...] | None) -> tuple[PriceSpec, ...] | None:
        if value is None:
            return None
        return _sorted_prices(value)


class MeterPayload(SemanticPayloadBase):
    payload_kind: Literal[SemanticNodeKind.METER] = SemanticNodeKind.METER
    description: str | None
    unit: str | None

    @field_validator("description")
    @classmethod
    def _description(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _text(value, field_name="description")

    @field_validator("unit")
    @classmethod
    def _unit(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _field_name(value, field_name="unit")


class LimitPayload(SemanticPayloadBase):
    payload_kind: Literal[SemanticNodeKind.LIMIT] = SemanticNodeKind.LIMIT
    description: str | None
    limit_value: int | None = Field(ge=0, strict=True)
    period: BillingPeriod | None = None

    @field_validator("description")
    @classmethod
    def _description(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _text(value, field_name="description")


class DeploymentTargetPayload(SemanticPayloadBase):
    payload_kind: Literal[SemanticNodeKind.DEPLOYMENT_TARGET] = SemanticNodeKind.DEPLOYMENT_TARGET
    target_kind: DeploymentTargetKind | None
    profile_id: str | None
    output_hints: tuple[str, ...] = Field(default_factory=tuple)

    @field_validator("profile_id")
    @classmethod
    def _profile(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _field_name(value, field_name="profile_id")

    @field_validator("output_hints")
    @classmethod
    def _hints(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(sorted({validate_portable_path(item).text for item in value}))
        if len(normalized) != len(value):
            raise ValueError("duplicate portable output hints")
        return normalized


class StubDeclarationPayload(SemanticPayloadBase):
    payload_kind: Literal[SemanticNodeKind.STUB_DECLARATION] = SemanticNodeKind.STUB_DECLARATION
    stub_kind: StubKind
    path: str
    entrypoint: str

    @field_validator("path")
    @classmethod
    def _path(cls, value: str) -> str:
        return validate_portable_path(value).text

    @field_validator("entrypoint")
    @classmethod
    def _entrypoint(cls, value: str) -> str:
        text = str(value or "").strip()
        if _ENTRYPOINT.fullmatch(text) is None:
            raise ValueError(f"entrypoint must be a bare symbol name, got {value!r}")
        return text


SemanticPayload = Annotated[
    SurfacePayload | PagePayload | SectionPayload | ModulePayload | ActionPayload | CapabilityPayload | PermissionPayload | EventPayload | ReactionPayload | NotificationPayload | DataCollectionPayload | DataAliasPayload | WorkflowPayload | TriggerPayload | PlanPayload | ProductPayload | MeterPayload | LimitPayload | DeploymentTargetPayload | StubDeclarationPayload,
    Field(discriminator="payload_kind"),
]

#: Exactly one variant per node kind; enum-completeness is test-enforced.
PAYLOAD_MODEL_BY_KIND: dict[SemanticNodeKind, type[SemanticPayloadBase]] = {
    SemanticNodeKind.SURFACE: SurfacePayload,
    SemanticNodeKind.PAGE: PagePayload,
    SemanticNodeKind.SECTION: SectionPayload,
    SemanticNodeKind.MODULE: ModulePayload,
    SemanticNodeKind.ACTION: ActionPayload,
    SemanticNodeKind.CAPABILITY: CapabilityPayload,
    SemanticNodeKind.PERMISSION: PermissionPayload,
    SemanticNodeKind.EVENT: EventPayload,
    SemanticNodeKind.REACTION: ReactionPayload,
    SemanticNodeKind.NOTIFICATION: NotificationPayload,
    SemanticNodeKind.DATA_COLLECTION: DataCollectionPayload,
    SemanticNodeKind.DATA_ALIAS: DataAliasPayload,
    SemanticNodeKind.WORKFLOW: WorkflowPayload,
    SemanticNodeKind.TRIGGER: TriggerPayload,
    SemanticNodeKind.PLAN: PlanPayload,
    SemanticNodeKind.PRODUCT: ProductPayload,
    SemanticNodeKind.METER: MeterPayload,
    SemanticNodeKind.LIMIT: LimitPayload,
    SemanticNodeKind.DEPLOYMENT_TARGET: DeploymentTargetPayload,
    SemanticNodeKind.STUB_DECLARATION: StubDeclarationPayload,
}

_PAYLOAD_ADAPTER: TypeAdapter[SemanticPayloadBase] = TypeAdapter(SemanticPayload)


def parse_semantic_payload(data: Any) -> SemanticPayloadBase:
    """Parse and fully re-verify one payload document (discriminated union)."""
    return _PAYLOAD_ADAPTER.validate_python(data)


def build_semantic_payload(
    model_cls: type[SemanticPayloadBase], /, **fields: Any
) -> SemanticPayloadBase:
    """Construct a payload with its canonical self-digest computed.

    Validation runs twice: a builder-context pass normalizes every field, then
    the definitive pass re-validates with the computed digest so the returned
    document is exactly what a cold parse would accept.
    """
    probe = model_cls.model_validate(
        {**fields, "payload_digest": _PLACEHOLDER_DIGEST},
        context={_BUILDER_CONTEXT_KEY: True},
    )
    digest = canonical_digest(probe.canonical_payload(include_digest=False))
    return model_cls.model_validate({**fields, "payload_digest": digest})


def semantic_payload_ref(payload: SemanticPayloadBase) -> SemanticPayloadRef:
    """The full-identity reference pinning ``payload`` into a graph-v2 node."""
    kind = getattr(payload, "payload_kind", None)
    if not isinstance(kind, SemanticNodeKind):
        raise SemanticPayloadError("payload has no discriminant kind")
    return SemanticPayloadRef(
        node_id=payload.node_id,
        payload_kind=kind.value,
        payload_version=payload.payload_version,
        content_digest=payload.payload_digest,
        scope=payload.scope,
    )


def validate_semantic_graph_v2_payload_closure(
    graph: SemanticGraphV2, payloads: Iterable[SemanticPayloadBase]
) -> None:
    """Bijective closure between a v2 graph's pins and the supplied payloads.

    Every pinned reference must match exactly one supplied payload on
    (node_id, kind, version, digest, scope); every supplied payload must be
    pinned.  Missing, mismatched, duplicate, and extra payloads fail closed.
    """
    try:
        verified_graph = SemanticGraphV2.model_validate(graph.model_dump(mode="json"))
    except (TypeError, ValueError) as exc:
        raise SemanticPayloadError(f"semantic graph v2 failed cold validation: {exc}") from exc

    supplied: dict[tuple[str, int], SemanticPayloadBase] = {}
    for payload in payloads:
        try:
            verified_payload = parse_semantic_payload(payload.model_dump(mode="json"))
        except (TypeError, ValueError) as exc:
            raise SemanticPayloadError(
                f"semantic payload failed cold validation: {exc}"
            ) from exc
        payload = verified_payload
        key = (payload.node_id, payload.payload_version)
        if key in supplied:
            raise SemanticPayloadError(
                f"duplicate payload supplied for node {payload.node_id!r} "
                f"version {payload.payload_version}"
            )
        supplied[key] = payload

    pinned: set[tuple[str, int]] = set()
    for node in verified_graph.nodes:
        ref = node.payload_ref
        key = (ref.node_id, ref.payload_version)
        pinned.add(key)
        candidate = supplied.get(key)
        if candidate is None:
            raise SemanticPayloadError(
                f"graph pins payload for node {ref.node_id!r} version "
                f"{ref.payload_version} but it was not supplied"
            )
        kind = getattr(candidate, "payload_kind", None)
        if not isinstance(kind, SemanticNodeKind) or kind.value != ref.payload_kind:
            raise SemanticPayloadError(
                f"payload kind mismatch for node {ref.node_id!r}"
            )
        if candidate.payload_digest != ref.content_digest:
            raise SemanticPayloadError(
                f"payload digest mismatch for node {ref.node_id!r}"
            )
        if candidate.scope != ref.scope:
            raise SemanticPayloadError(
                f"payload scope mismatch for node {ref.node_id!r}"
            )

    extras = sorted(set(supplied) - pinned)
    if extras:
        raise SemanticPayloadError(
            f"supplied payloads are not pinned by the graph: {extras}"
        )


__all__ = [
    "PAYLOAD_MODEL_BY_KIND",
    "SEMANTIC_PAYLOAD_SCHEMA_VERSION",
    "ActionPayload",
    "BillingPeriod",
    "CapabilityPayload",
    "DataAliasPayload",
    "DataCollectionPayload",
    "DeploymentTargetKind",
    "DeploymentTargetPayload",
    "EventPayload",
    "FieldType",
    "HttpMethod",
    "IndexSpec",
    "LimitPayload",
    "MeterPayload",
    "ModulePayload",
    "NotificationChannel",
    "NotificationPayload",
    "PagePayload",
    "PageSectionEntry",
    "PermissionPayload",
    "PlanPayload",
    "PriceSpec",
    "ProductPayload",
    "ReactionPayload",
    "SectionContentEntry",
    "SectionEntryKind",
    "SectionPayload",
    "SemanticPayload",
    "SemanticPayloadBase",
    "SemanticPayloadError",
    "StubDeclarationPayload",
    "SurfacePayload",
    "TriggerKind",
    "TriggerPayload",
    "TypedFieldSpec",
    "WorkflowPayload",
    "WorkflowStartupMode",
    "build_semantic_payload",
    "parse_semantic_payload",
    "semantic_payload_ref",
    "validate_semantic_graph_v2_payload_closure",
]
