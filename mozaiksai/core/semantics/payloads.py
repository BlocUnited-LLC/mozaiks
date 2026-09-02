"""ADR 0007 Slice 2E typed semantic payload documents (``mozaiks.semantic_payload.v1``).

Exactly one strict payload variant exists per :class:`SemanticNodeKind`; a
graph-v2 node pins its payload by full identity through
:class:`~mozaiksai.core.semantics.refs.SemanticPayloadRef`.  Payloads carry the
content a node's graph identity cannot — canonical artifact identifiers,
titles, intent text, typed field shapes, prices, ordered entries, and selected
normative runtime declaratives.  Runtime declaratives retain their existing
typed model validation; authoritative payload closure never admits an untyped
``dict[str, Any]`` escape hatch.

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

from mozaiksai.core.runtime.app.page_schema import (
    AppPageMeta,
    AppPageNavigation,
    AppPageSection,
)
from mozaiksai.core.semantics.canonical import canonical_digest
from mozaiksai.core.semantics.graph import (
    SemanticEdgeKind,
    SemanticGraphV2,
    SemanticNodeKind,
)
from mozaiksai.core.semantics.portable_path import validate_portable_path
from mozaiksai.core.semantics.refs import (
    ExecutionAccessScopeRef,
    SemanticPayloadRef,
    SemanticsModel,
    _validate_digest,
    validate_node_id_grammar,
)
from mozaiksai.core.taxonomy import SemanticCategory, validate_identifier_grammar

SEMANTIC_PAYLOAD_SCHEMA_VERSION: Literal["mozaiks.semantic_payload.v1"] = (
    "mozaiks.semantic_payload.v1"
)

_MAX_TEXT_CHARS = 4000
_FIELD_NAME = re.compile(r"^[a-z][a-z0-9_]*$")
_PAGE_ROUTE = re.compile(r"^/[A-Za-z0-9_./:{}?-]*$")
_CANONICAL_ID = re.compile(r"^[a-z0-9][a-z0-9._-]*$")
_ENV_HANDLE = re.compile(r"^[A-Z][A-Z0-9_]*$")
_SEMVER = re.compile(
    r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)(?:-[0-9A-Za-z.-]+)?$"
)
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


def _canonical_id(value: str, *, field_name: str) -> str:
    text = str(value or "").strip()
    if _CANONICAL_ID.fullmatch(text) is None:
        raise ValueError(f"{field_name} must be a lowercase canonical identifier, got {value!r}")
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
    AGENT_DRIVEN = "agent_driven"
    USER_DRIVEN = "user_driven"
    BACKEND_ONLY = "backend_only"


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


class OptionalFamilyKind(StrEnum):
    AUTH = "auth"
    INTEGRATIONS = "integrations"
    CUSTOM_ROUTES = "custom_routes"
    THEME = "theme"
    SHELL = "shell"
    ASSETS = "assets"
    DATA = "data"
    WORKFLOWS = "workflows"


class OptionalFamilySelectionStatus(StrEnum):
    SELECTED = "selected"
    ABSENT_BY_DECLARATION = "absent_by_declaration"
    NOT_APPLICABLE = "not_applicable"


class AuthStrategyKind(StrEnum):
    PUBLIC = "public"
    BASIC_LOGIN = "basic_login"
    ROLE_BASED = "role_based"
    FEDERATED = "federated"


class IntegrationKind(StrEnum):
    API_KEY = "api_key"
    OAUTH = "oauth"
    OIDC = "oidc"
    WEBHOOK = "webhook"
    SERVICE = "service"
    DATABASE = "database"


class IntegrationRequirementPhase(StrEnum):
    BUILD = "build"
    RUNTIME = "runtime"
    DEPLOYMENT = "deployment"


class IntegrationConfigValueKind(StrEnum):
    TEXT = "text"
    URL = "url"
    SECRET = "secret"


class IntegrationImplementationKind(StrEnum):
    CONFIG_ONLY = "config_only"
    APP_OWNED_ADAPTER = "app_owned_adapter"
    MANAGED_CAPABILITY = "managed_capability"


class AdapterAreaKind(StrEnum):
    AUTH = "auth"
    SOURCE_CONTROL = "source_control"
    DEPLOYMENT = "deployment"
    DNS = "dns"
    REGISTRAR = "registrar"
    CLOUD = "cloud"
    STORAGE = "storage"
    SEARCH = "search"
    EMAIL = "email"
    DATABASE = "database"
    SECRETS = "secrets"
    PAYMENTS = "payments"


class PackageManagerKind(StrEnum):
    NPM = "npm"
    PNPM = "pnpm"
    YARN = "yarn"


class LockfileKind(StrEnum):
    PACKAGE_LOCK = "package_lock"
    PNPM_LOCK = "pnpm_lock"
    YARN_LOCK = "yarn_lock"


class ArtifactDeclarationRole(StrEnum):
    DATA_MIGRATION = "data_migration"
    APP_ROUTE_EXTENSION = "app_route_extension"
    CUSTOM_PAGE = "custom_page"
    MODULE_HELPER = "module_helper"
    WORKFLOW_TOOL = "workflow_tool"
    WORKFLOW_COMPONENT = "workflow_component"
    MODULE_ADMIN_PAGE = "module_admin_page"
    REFINEMENT_PROMPT_PACK = "refinement_prompt_pack"


class WorkflowTransitionKind(StrEnum):
    AFTER_TURN = "after_turn"
    CONTEXT_EQUALS = "context_equals"
    TOOL_CALLED = "tool_called"


class WorkflowTransitionTargetKind(StrEnum):
    PARTICIPANT = "participant"
    HUMAN = "human"
    TERMINATE = "terminate"


# ---------------------------------------------------------------------------
# Typed sub-shapes
# ---------------------------------------------------------------------------


class OptionalFamilySelection(SemanticsModel):
    family: OptionalFamilyKind
    status: OptionalFamilySelectionStatus


class IntegrationConfigRequirement(SemanticsModel):
    name: str
    value_kind: IntegrationConfigValueKind
    required: bool

    @field_validator("name")
    @classmethod
    def _name(cls, value: str) -> str:
        text = str(value or "").strip()
        if _FIELD_NAME.fullmatch(text) is None and _ENV_HANDLE.fullmatch(text) is None:
            raise ValueError("integration configuration names must be snake_case or ENV_STYLE")
        return text


class IntegrationImplementationBinding(SemanticsModel):
    implementation_kind: IntegrationImplementationKind
    adapter_area: AdapterAreaKind | None = None

    @model_validator(mode="after")
    def _coherence(self) -> IntegrationImplementationBinding:
        owns_adapter = (
            self.implementation_kind is IntegrationImplementationKind.APP_OWNED_ADAPTER
        )
        if owns_adapter != (self.adapter_area is not None):
            raise ValueError("only app_owned_adapter integrations declare adapter_area")
        return self


class RuntimeSupportSelection(SemanticsModel):
    python_runtime: bool
    requirements: bool
    pyproject: bool
    node_frontend: bool
    package_manager: PackageManagerKind | None
    lockfile: LockfileKind | None
    typescript: bool
    vite: bool

    @model_validator(mode="after")
    def _coherence(self) -> RuntimeSupportSelection:
        if not self.python_runtime and (self.requirements or self.pyproject):
            raise ValueError("Python support files require python_runtime")
        node_fields = (
            self.package_manager is not None,
            self.lockfile is not None,
            self.typescript,
            self.vite,
        )
        if not self.node_frontend and any(node_fields):
            raise ValueError("Node support files require node_frontend")
        if self.node_frontend and self.package_manager is None:
            raise ValueError("node_frontend requires one package_manager")
        expected_lock = (
            {
                PackageManagerKind.NPM: LockfileKind.PACKAGE_LOCK,
                PackageManagerKind.PNPM: LockfileKind.PNPM_LOCK,
                PackageManagerKind.YARN: LockfileKind.YARN_LOCK,
            }[self.package_manager]
            if self.package_manager is not None
            else None
        )
        if self.lockfile is not None and self.lockfile is not expected_lock:
            raise ValueError("lockfile must match the selected package_manager")
        return self


class RefinementHarnessSelection(SemanticsModel):
    status: OptionalFamilySelectionStatus
    harness_id: str | None = None
    prompt_pack_ids: tuple[str, ...] = Field(default_factory=tuple)

    @field_validator("harness_id")
    @classmethod
    def _harness_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _canonical_id(value, field_name="harness_id")

    @field_validator("prompt_pack_ids")
    @classmethod
    def _prompt_pack_ids(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        ordered = tuple(
            sorted(_canonical_id(item, field_name="prompt_pack_id") for item in value)
        )
        if len(ordered) != len(set(ordered)):
            raise ValueError("duplicate refinement prompt-pack identities")
        return ordered

    @model_validator(mode="after")
    def _coherence(self) -> RefinementHarnessSelection:
        selected = self.status is OptionalFamilySelectionStatus.SELECTED
        if selected != (self.harness_id is not None):
            raise ValueError("selected refinement harness requires one harness_id")
        if not selected and self.prompt_pack_ids:
            raise ValueError("an unselected refinement harness cannot declare prompt packs")
        return self


class WorkflowParticipant(SemanticsModel):
    participant_id: str

    @field_validator("participant_id")
    @classmethod
    def _participant_id(cls, value: str) -> str:
        return _field_name(value, field_name="participant_id")


class WorkflowTransition(SemanticsModel):
    source_participant_id: str
    target_kind: WorkflowTransitionTargetKind
    target_participant_id: str | None
    transition_kind: WorkflowTransitionKind
    condition_key: str | None
    condition_value: str | int | bool | None
    tool_name: str | None

    @field_validator("source_participant_id")
    @classmethod
    def _source(cls, value: str) -> str:
        return _field_name(value, field_name="source_participant_id")

    @field_validator("target_participant_id")
    @classmethod
    def _target(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _field_name(value, field_name="target_participant_id")

    @field_validator("condition_key", "tool_name")
    @classmethod
    def _optional_names(cls, value: str | None, info: ValidationInfo) -> str | None:
        if value is None:
            return None
        return _field_name(value, field_name=str(info.field_name))

    @field_validator("condition_value")
    @classmethod
    def _condition_value(cls, value: str | int | bool | None) -> str | int | bool | None:
        if isinstance(value, str):
            return _text(value, field_name="condition_value")
        return value

    @model_validator(mode="after")
    def _shape(self) -> WorkflowTransition:
        if (self.target_kind is WorkflowTransitionTargetKind.PARTICIPANT) != (
            self.target_participant_id is not None
        ):
            raise ValueError("only participant transition targets carry target_participant_id")
        if self.transition_kind is WorkflowTransitionKind.AFTER_TURN:
            if self.condition_key is not None or self.condition_value is not None or self.tool_name is not None:
                raise ValueError("after_turn transitions cannot declare condition fields")
        elif self.transition_kind is WorkflowTransitionKind.CONTEXT_EQUALS:
            if self.condition_key is None or self.condition_value is None or self.tool_name is not None:
                raise ValueError("context_equals requires condition_key/value only")
        elif self.transition_kind is WorkflowTransitionKind.TOOL_CALLED:
            if self.tool_name is None or self.condition_key is not None or self.condition_value is not None:
                raise ValueError("tool_called requires tool_name only")
        return self

    @property
    def routing_decision_identity(self) -> tuple[object, ...]:
        """Canonical authored decision key, excluding its target outcome."""
        identity: tuple[object, ...] = (
            self.source_participant_id,
            self.transition_kind.value,
        )
        if self.transition_kind is WorkflowTransitionKind.CONTEXT_EQUALS:
            condition_value = self.condition_value
            identity += (
                self.condition_key,
                type(condition_value).__name__,
                condition_value,
            )
        elif self.transition_kind is WorkflowTransitionKind.TOOL_CALLED:
            identity += (self.tool_name,)
        return identity

    @property
    def target_outcome(self) -> tuple[str, str | None]:
        return self.target_kind.value, self.target_participant_id


class WorkflowTopology(SemanticsModel):
    max_turns: int = Field(ge=1, le=500, strict=True)
    human_input_required: bool
    initial_participant_id: str | None
    participants: tuple[WorkflowParticipant, ...]
    transitions: tuple[WorkflowTransition, ...]

    @field_validator("initial_participant_id")
    @classmethod
    def _initial(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _field_name(value, field_name="initial_participant_id")

    @field_validator("participants")
    @classmethod
    def _participants(
        cls, value: tuple[WorkflowParticipant, ...]
    ) -> tuple[WorkflowParticipant, ...]:
        ordered = tuple(sorted(value, key=lambda item: item.participant_id))
        identities = [item.participant_id for item in ordered]
        if len(identities) != len(set(identities)):
            raise ValueError("duplicate workflow participants")
        return ordered

    @field_validator("transitions")
    @classmethod
    def _transitions(
        cls, value: tuple[WorkflowTransition, ...]
    ) -> tuple[WorkflowTransition, ...]:
        ordered = tuple(
            sorted(
                value,
                key=lambda item: (
                    item.source_participant_id,
                    item.target_participant_id or "",
                    item.transition_kind.value,
                    item.condition_key or "",
                    str(item.condition_value),
                    item.tool_name or "",
                ),
            )
        )
        if len(ordered) != len(set(ordered)):
            raise ValueError("duplicate workflow transitions")
        outcomes_by_decision: dict[tuple[object, ...], tuple[str, str | None]] = {}
        for transition in ordered:
            decision = transition.routing_decision_identity
            outcome = transition.target_outcome
            existing = outcomes_by_decision.get(decision)
            if existing is not None and existing != outcome:
                raise ValueError(
                    "conflicting workflow transitions for one routing decision"
                )
            outcomes_by_decision[decision] = outcome
        return ordered

    @model_validator(mode="after")
    def _closure(self) -> WorkflowTopology:
        participants = {item.participant_id for item in self.participants}
        if self.initial_participant_id is not None and self.initial_participant_id not in participants:
            raise ValueError("initial_participant_id must reference a declared participant")
        for transition in self.transitions:
            if transition.source_participant_id not in participants:
                raise ValueError("transition source must reference a declared participant")
            if (
                transition.target_participant_id is not None
                and transition.target_participant_id not in participants
            ):
                raise ValueError("transition target must reference a declared participant")
        return self


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


class ApplicationPayload(SemanticPayloadBase):
    payload_kind: Literal[SemanticNodeKind.APPLICATION] = SemanticNodeKind.APPLICATION
    application_id: str
    display_name: str
    description: str | None
    tagline: str | None
    value_proposition: str | None
    version: str
    default_route: str
    optional_families: tuple[OptionalFamilySelection, ...]
    runtime_support: RuntimeSupportSelection | None = None
    refinement_harness: RefinementHarnessSelection | None = None
    closed_artifact_roles: tuple[ArtifactDeclarationRole, ...] = Field(
        default_factory=tuple
    )

    @field_validator("application_id")
    @classmethod
    def _application_id(cls, value: str) -> str:
        return _canonical_id(value, field_name="application_id")

    @field_validator("display_name")
    @classmethod
    def _display_name(cls, value: str) -> str:
        return _text(value, field_name="display_name")

    @field_validator("description", "tagline", "value_proposition")
    @classmethod
    def _optional_text(cls, value: str | None, info: ValidationInfo) -> str | None:
        if value is None:
            return None
        return _text(value, field_name=str(info.field_name))

    @field_validator("version")
    @classmethod
    def _version(cls, value: str) -> str:
        text = str(value or "").strip()
        if _SEMVER.fullmatch(text) is None:
            raise ValueError("version must be a semantic version")
        return text

    @field_validator("default_route")
    @classmethod
    def _default_route(cls, value: str) -> str:
        text = str(value or "").strip()
        if _PAGE_ROUTE.fullmatch(text) is None:
            raise ValueError("default_route must be an app-local route")
        return text

    @field_validator("optional_families")
    @classmethod
    def _optional_families(
        cls, value: tuple[OptionalFamilySelection, ...]
    ) -> tuple[OptionalFamilySelection, ...]:
        ordered = tuple(sorted(value, key=lambda item: item.family.value))
        families = [item.family for item in ordered]
        if len(families) != len(set(families)):
            raise ValueError("duplicate optional family selections")
        if set(families) != set(OptionalFamilyKind):
            raise ValueError("optional_families must state every closed optional family")
        return ordered

    @field_validator("closed_artifact_roles")
    @classmethod
    def _closed_artifact_roles(
        cls, value: tuple[ArtifactDeclarationRole, ...]
    ) -> tuple[ArtifactDeclarationRole, ...]:
        allowed = {
            ArtifactDeclarationRole.DATA_MIGRATION,
            ArtifactDeclarationRole.APP_ROUTE_EXTENSION,
            ArtifactDeclarationRole.CUSTOM_PAGE,
            ArtifactDeclarationRole.REFINEMENT_PROMPT_PACK,
        }
        if not set(value) <= allowed:
            raise ValueError("application carries only application-owned artifact roles")
        if len(value) != len(set(value)):
            raise ValueError("duplicate closed application artifact roles")
        return tuple(sorted(value, key=lambda item: item.value))


class AuthPayload(SemanticPayloadBase):
    payload_kind: Literal[SemanticNodeKind.AUTH] = SemanticNodeKind.AUTH
    auth_required: bool
    strategy: AuthStrategyKind
    roles: tuple[str, ...]

    @field_validator("roles")
    @classmethod
    def _roles(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(sorted(_field_name(item, field_name="role") for item in value))
        if len(normalized) != len(set(normalized)):
            raise ValueError("duplicate auth roles")
        return normalized

    @model_validator(mode="after")
    def _coherence(self) -> AuthPayload:
        if self.strategy is AuthStrategyKind.PUBLIC:
            if self.auth_required or self.roles:
                raise ValueError("public auth cannot require auth or logical roles")
        elif not self.auth_required:
            raise ValueError("non-public auth strategy requires auth_required=true")
        if self.strategy is AuthStrategyKind.ROLE_BASED and not self.roles:
            raise ValueError("role_based auth requires at least one logical role")
        return self


class IntegrationPayload(SemanticPayloadBase):
    payload_kind: Literal[SemanticNodeKind.INTEGRATION] = SemanticNodeKind.INTEGRATION
    integration_id: str
    integration_kind: IntegrationKind
    purpose: str | None
    required_at: IntegrationRequirementPhase
    optional: bool
    config_requirements: tuple[IntegrationConfigRequirement, ...]
    implementation: IntegrationImplementationBinding | None = None

    @field_validator("integration_id")
    @classmethod
    def _integration_id(cls, value: str) -> str:
        return _canonical_id(value, field_name="integration_id")

    @field_validator("purpose")
    @classmethod
    def _purpose(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _text(value, field_name="purpose")

    @field_validator("config_requirements")
    @classmethod
    def _config_requirements(
        cls, value: tuple[IntegrationConfigRequirement, ...]
    ) -> tuple[IntegrationConfigRequirement, ...]:
        ordered = tuple(sorted(value, key=lambda item: item.name))
        names = [item.name for item in ordered]
        if len(names) != len(set(names)):
            raise ValueError("duplicate integration configuration requirements")
        return ordered


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
    page_id: str
    route: str | None
    title: str | None
    intent: str | None
    page_type: Literal[
        "record_list",
        "record_detail",
        "analytics_dashboard",
        "workflow_board",
        "activity_feed",
        "gallery",
        "wizard",
        "split_view",
        "settings",
        "landing",
    ] | None
    layout: Literal["grid", "sidebar", "full-width", "split"] | None
    shell_mode: Literal[
        "standard", "workspace", "conversation", "focused", "immersive", "public"
    ] | None
    roles: tuple[str, ...] | None
    navigation: AppPageNavigation | None
    meta: AppPageMeta | None
    layout_id: str | None = None
    sections: tuple[PageSectionEntry, ...] = Field(default_factory=tuple)

    @field_validator("page_id")
    @classmethod
    def _page_id(cls, value: str) -> str:
        return _field_name(value, field_name="page_id")

    @field_validator("route")
    @classmethod
    def _route(cls, value: str | None) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        if ".." in text or _PAGE_ROUTE.fullmatch(text) is None:
            raise ValueError(f"route must be a safe absolute app route, got {value!r}")
        return text

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

    @field_validator("roles")
    @classmethod
    def _roles(cls, value: tuple[str, ...] | None) -> tuple[str, ...] | None:
        if value is None:
            return None
        normalized = tuple(sorted({_field_name(item, field_name="role") for item in value}))
        if len(normalized) != len(value):
            raise ValueError("roles must be unique")
        return normalized

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
    section_id: str
    title: str | None
    intent: str | None
    declarative: AppPageSection | None
    entries: tuple[SectionContentEntry, ...] = Field(default_factory=tuple)

    @field_validator("section_id")
    @classmethod
    def _section_id(cls, value: str) -> str:
        text = str(value or "").strip()
        if re.fullmatch(r"[a-z][a-z0-9_-]*", text) is None:
            raise ValueError("section_id must be a lowercase declarative identifier")
        return text

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

    @model_validator(mode="after")
    def _declarative_identity(self) -> SectionPayload:
        if self.declarative is not None and self.declarative.id != self.section_id:
            raise ValueError("declarative section id must match section_id")
        return self


class ModulePayload(SemanticPayloadBase):
    payload_kind: Literal[SemanticNodeKind.MODULE] = SemanticNodeKind.MODULE
    module_id: str
    description: str | None
    closed_artifact_roles: tuple[ArtifactDeclarationRole, ...] = Field(
        default_factory=tuple
    )

    @field_validator("module_id")
    @classmethod
    def _module_id(cls, value: str) -> str:
        return _field_name(value, field_name="module_id")

    @field_validator("description")
    @classmethod
    def _description(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _text(value, field_name="description")

    @field_validator("closed_artifact_roles")
    @classmethod
    def _closed_artifact_roles(
        cls, value: tuple[ArtifactDeclarationRole, ...]
    ) -> tuple[ArtifactDeclarationRole, ...]:
        allowed = {
            ArtifactDeclarationRole.MODULE_HELPER,
            ArtifactDeclarationRole.MODULE_ADMIN_PAGE,
        }
        if not set(value) <= allowed:
            raise ValueError("module carries only module-owned artifact roles")
        if len(value) != len(set(value)):
            raise ValueError("duplicate closed module artifact roles")
        return tuple(sorted(value, key=lambda item: item.value))


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
    workflow_id: str
    description: str | None
    startup_mode: WorkflowStartupMode | None
    topology: WorkflowTopology | None
    closed_artifact_roles: tuple[ArtifactDeclarationRole, ...] = Field(
        default_factory=tuple
    )

    @field_validator("workflow_id")
    @classmethod
    def _workflow_id(cls, value: str) -> str:
        return _field_name(value, field_name="workflow_id")

    @field_validator("description")
    @classmethod
    def _description(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _text(value, field_name="description")

    @field_validator("closed_artifact_roles")
    @classmethod
    def _closed_artifact_roles(
        cls, value: tuple[ArtifactDeclarationRole, ...]
    ) -> tuple[ArtifactDeclarationRole, ...]:
        allowed = {
            ArtifactDeclarationRole.WORKFLOW_TOOL,
            ArtifactDeclarationRole.WORKFLOW_COMPONENT,
        }
        if not set(value) <= allowed:
            raise ValueError("workflow carries only workflow-owned artifact roles")
        if len(value) != len(set(value)):
            raise ValueError("duplicate closed workflow artifact roles")
        return tuple(sorted(value, key=lambda item: item.value))


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
    period: BillingPeriod | None

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


class ArtifactDeclarationPayload(SemanticPayloadBase):
    payload_kind: Literal[SemanticNodeKind.ARTIFACT_DECLARATION] = (
        SemanticNodeKind.ARTIFACT_DECLARATION
    )
    declaration_id: str
    artifact_role: ArtifactDeclarationRole
    owner_node_id: str
    related_node_id: str | None = None

    @field_validator("declaration_id")
    @classmethod
    def _declaration_id(cls, value: str) -> str:
        return _canonical_id(value, field_name="declaration_id")

    @field_validator("owner_node_id", "related_node_id")
    @classmethod
    def _node_refs(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return validate_node_id_grammar(value)

    @model_validator(mode="after")
    def _relationship_shape(self) -> ArtifactDeclarationPayload:
        requires_related = self.artifact_role in {
            ArtifactDeclarationRole.CUSTOM_PAGE,
            ArtifactDeclarationRole.MODULE_ADMIN_PAGE,
        }
        if requires_related != (self.related_node_id is not None):
            raise ValueError(
                "custom-page and module-admin declarations require one related page node"
            )
        return self


SemanticPayload = Annotated[
    ApplicationPayload | AuthPayload | IntegrationPayload | SurfacePayload | PagePayload | SectionPayload | ModulePayload | ActionPayload | CapabilityPayload | PermissionPayload | EventPayload | ReactionPayload | NotificationPayload | DataCollectionPayload | DataAliasPayload | WorkflowPayload | TriggerPayload | PlanPayload | ProductPayload | MeterPayload | LimitPayload | DeploymentTargetPayload | ArtifactDeclarationPayload,
    Field(discriminator="payload_kind"),
]

#: Exactly one variant per node kind; enum-completeness is test-enforced.
PAYLOAD_MODEL_BY_KIND: dict[SemanticNodeKind, type[SemanticPayloadBase]] = {
    SemanticNodeKind.APPLICATION: ApplicationPayload,
    SemanticNodeKind.AUTH: AuthPayload,
    SemanticNodeKind.INTEGRATION: IntegrationPayload,
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
    SemanticNodeKind.ARTIFACT_DECLARATION: ArtifactDeclarationPayload,
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

    payload_by_node = {payload.node_id: payload for payload in supplied.values()}
    edge_keys = {
        (edge.kind, edge.source_node_id, edge.target_node_id)
        for edge in verified_graph.edges
    }
    owner_kind_by_role = {
        ArtifactDeclarationRole.DATA_MIGRATION: SemanticNodeKind.APPLICATION,
        ArtifactDeclarationRole.APP_ROUTE_EXTENSION: SemanticNodeKind.APPLICATION,
        ArtifactDeclarationRole.CUSTOM_PAGE: SemanticNodeKind.APPLICATION,
        ArtifactDeclarationRole.MODULE_HELPER: SemanticNodeKind.MODULE,
        ArtifactDeclarationRole.WORKFLOW_TOOL: SemanticNodeKind.WORKFLOW,
        ArtifactDeclarationRole.WORKFLOW_COMPONENT: SemanticNodeKind.WORKFLOW,
        ArtifactDeclarationRole.MODULE_ADMIN_PAGE: SemanticNodeKind.MODULE,
        ArtifactDeclarationRole.REFINEMENT_PROMPT_PACK: SemanticNodeKind.APPLICATION,
    }
    seen_declarations: set[tuple[ArtifactDeclarationRole, str, str]] = set()
    for payload in payload_by_node.values():
        if not isinstance(payload, ArtifactDeclarationPayload):
            continue
        owner = payload_by_node.get(payload.owner_node_id)
        expected_owner_kind = owner_kind_by_role[payload.artifact_role]
        if owner is None or getattr(owner, "payload_kind", None) is not expected_owner_kind:
            raise SemanticPayloadError(
                f"artifact declaration {payload.node_id!r} has a foreign or missing owner"
            )
        if payload.artifact_role not in getattr(owner, "closed_artifact_roles", ()):
            raise SemanticPayloadError(
                f"artifact declaration {payload.node_id!r} uses an owner-unclosed role"
            )
        identity = (
            payload.artifact_role,
            payload.owner_node_id,
            payload.declaration_id,
        )
        if identity in seen_declarations:
            raise SemanticPayloadError(f"duplicate artifact declaration identity {identity!r}")
        seen_declarations.add(identity)
        if (
            SemanticEdgeKind.OWNS,
            payload.owner_node_id,
            payload.node_id,
        ) not in edge_keys:
            raise SemanticPayloadError(
                f"artifact declaration {payload.node_id!r} lacks its typed owner edge"
            )
        if payload.related_node_id is not None:
            related = payload_by_node.get(payload.related_node_id)
            if related is None or getattr(related, "payload_kind", None) is not SemanticNodeKind.PAGE:
                raise SemanticPayloadError(
                    f"artifact declaration {payload.node_id!r} has a foreign related page"
                )
            if (
                SemanticEdgeKind.BINDS,
                payload.node_id,
                payload.related_node_id,
            ) not in edge_keys:
                raise SemanticPayloadError(
                    f"artifact declaration {payload.node_id!r} lacks its typed page edge"
                )

    applications = [
        payload
        for payload in payload_by_node.values()
        if isinstance(payload, ApplicationPayload)
    ]
    integrations = [
        payload
        for payload in payload_by_node.values()
        if isinstance(payload, IntegrationPayload)
    ]
    if integrations:
        application = applications[0] if applications else None
        for integration in integrations:
            if integration.implementation is None:
                continue
            if application is None or (
                SemanticEdgeKind.DECLARES,
                application.node_id,
                integration.node_id,
            ) not in edge_keys:
                raise SemanticPayloadError(
                    f"integration {integration.node_id!r} lacks application ownership"
                )

    for application in applications:
        selection = application.refinement_harness
        if selection is None:
            continue
        declared_packs = {
            payload.declaration_id
            for payload in payload_by_node.values()
            if isinstance(payload, ArtifactDeclarationPayload)
            and payload.artifact_role is ArtifactDeclarationRole.REFINEMENT_PROMPT_PACK
            and payload.owner_node_id == application.node_id
        }
        if declared_packs != set(selection.prompt_pack_ids):
            raise SemanticPayloadError(
                "refinement prompt-pack declarations do not match application selection"
            )


__all__ = [
    "PAYLOAD_MODEL_BY_KIND",
    "SEMANTIC_PAYLOAD_SCHEMA_VERSION",
    "AdapterAreaKind",
    "ActionPayload",
    "ApplicationPayload",
    "ArtifactDeclarationPayload",
    "ArtifactDeclarationRole",
    "AuthPayload",
    "AuthStrategyKind",
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
    "IntegrationConfigRequirement",
    "IntegrationConfigValueKind",
    "IntegrationImplementationBinding",
    "IntegrationImplementationKind",
    "IntegrationKind",
    "IntegrationPayload",
    "IntegrationRequirementPhase",
    "LimitPayload",
    "LockfileKind",
    "MeterPayload",
    "ModulePayload",
    "NotificationChannel",
    "NotificationPayload",
    "OptionalFamilyKind",
    "OptionalFamilySelection",
    "OptionalFamilySelectionStatus",
    "PackageManagerKind",
    "PagePayload",
    "PageSectionEntry",
    "PermissionPayload",
    "PlanPayload",
    "PriceSpec",
    "ProductPayload",
    "ReactionPayload",
    "RefinementHarnessSelection",
    "RuntimeSupportSelection",
    "SectionContentEntry",
    "SectionEntryKind",
    "SectionPayload",
    "SemanticPayload",
    "SemanticPayloadBase",
    "SemanticPayloadError",
    "SurfacePayload",
    "TriggerKind",
    "TriggerPayload",
    "TypedFieldSpec",
    "WorkflowPayload",
    "WorkflowParticipant",
    "WorkflowStartupMode",
    "WorkflowTopology",
    "WorkflowTransition",
    "WorkflowTransitionKind",
    "WorkflowTransitionTargetKind",
    "build_semantic_payload",
    "parse_semantic_payload",
    "semantic_payload_ref",
    "validate_semantic_graph_v2_payload_closure",
]
