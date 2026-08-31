"""ADR 0007 Slice 4B aggregate ``CompilationPlan`` derivation (offline-only).

Exactly one aggregate plan is derived per immutable graph identity from three
inputs and nothing else: a validated ``SemanticGraphV2``, its typed payload
closure, and a typed snapshot of the sole ``layout_registry``. The registry
snapshot recomputes its own identity from the canonical row content it
carries — a caller-claimed registry digest is never trusted — so substituted,
forged, or internally inconsistent registries change or fail plan identity
rather than hiding behind a retained digest. The snapshot is a projection of
the one registry, never a parallel authority: it declares no rows of its own.

The plan embeds one non-authoritative ``FamilyInstancePlan`` subdocument per
applicable artifact-family instance. Embedded family plans have no independent
reference type, registration, resolution, publication, or execution authority
— they are valid only under the aggregate plan's identity and digest.

Completeness rule: every registry family row is either disposed
(render / reuse_from_base / preserve_unowned / input_only / external_handoff /
inapplicable) or carried as an explicit typed ``PlanGap`` with a closed reason
code. Omission is never an implicit decision. Conditions that graph-v2
semantics cannot decide, placeholders whose joint binding no typed graph
relationship proves, and incomplete renderer inputs are typed gaps. Renderer
implementation/version selection remains in the separate implementation
binding — the plan never invents a semantic fact, and a validated unit can
never carry an unresolved ``{placeholder}``.

Every authoritative field obeys a closed value domain (enums or canonical
lowercase identifier grammar); the document carries no free-form prose, so
the contract is structurally incapable of smuggling arbitrary live runtime
state. Output ownership is validated per physical destination root: global
roots are one collision domain regardless of semantic instance, and
instance-relative roots require an explicit instance identity.

This module is deterministic substrate: no filesystem, no network, no AG2
imports, no model calls, no runtime or capability authority. The plan carries
no live runtime identifiers of any execution engine: execution needs appear
only as provider-neutral deterministic requirements (registry materializer
declarations, dependency order, dispositions) that the authority-cutover
slice may later bind onto runtime adapters and workflow transitions. The
active agent-produced ``AppBuildPlan`` remains the sole operational plan until
the Slice 5 cutover; nothing in production imports this module.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from enum import StrEnum
from typing import Any, Literal

from pydantic import Field, field_validator, model_validator

from mozaiksai.core.runtime.app.layout_registry import ValidatorIdentifier
from mozaiksai.core.semantics.canonical import canonical_digest
from mozaiksai.core.semantics.graph import (
    SemanticEdgeKind,
    SemanticGraphV2,
    SemanticNodeKind,
)
from mozaiksai.core.semantics.payloads import (
    PagePayload,
    SemanticPayloadBase,
    parse_semantic_payload,
    validate_semantic_graph_v2_payload_closure,
)
from mozaiksai.core.semantics.portable_path import detect_collisions, validate_portable_path
from mozaiksai.core.semantics.refs import (
    ExecutionAccessScopeRef,
    SemanticsModel,
    _validate_digest,
    validate_node_id_grammar,
)
from mozaiksai.core.workflow.assignment_kinds import (
    AssignmentKind,
    assignment_contract_descriptor,
)
from mozaiksai.core.workflow.work_contracts import (
    StructuredOutputContractRef,
    build_structured_output_contract_ref,
)

COMPILATION_PLAN_SCHEMA_VERSION: Literal["mozaiks.compilation_plan.v1"] = (
    "mozaiks.compilation_plan.v1"
)

_PLACEHOLDER_RE = re.compile(r"\{([a-z][a-z0-9_]*)\}")
_IDENTIFIER_RE = re.compile(r"^[a-z][a-z0-9_]*$")
_VALUE_RE = re.compile(r"^[a-z0-9][a-z0-9_]*$")
_UNIT_SEGMENT_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]*$")
_SCHEMA_VERSION_RE = re.compile(r"^[a-z][a-z0-9_.]*$")

#: Path scopes that name one shared physical destination root: ownership is
#: global across every unit regardless of semantic instance identity.
_GLOBAL_PATH_SCOPES = frozenset(
    {"app_bundle_root", "workspace_root", "deployment_derived", "generated_staging"}
)
#: Path scopes rooted at one semantic instance: the instance identity is the
#: explicit physical root discriminator and must be present.
_INSTANCE_PATH_SCOPES = frozenset({"module_relative", "workflow_relative"})


class CompilationPlanError(ValueError):
    """The plan inputs or document violate the Slice 4B contract."""


def _identifier(value: Any, *, field_name: str) -> str:
    text = str(getattr(value, "value", value) or "").strip()
    if _IDENTIFIER_RE.fullmatch(text) is None:
        raise ValueError(f"{field_name} must be a lowercase identifier, got {value!r}")
    return text


def _template_text(value: str, *, field_name: str) -> str:
    """Validate a registry path template: portable once placeholders resolve."""
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{field_name} must be non-empty")
    probe = text
    for name in _PLACEHOLDER_RE.findall(text):
        probe = probe.replace("{" + name + "}", "x0")
    if "{" in probe or "}" in probe:
        raise ValueError(f"{field_name} contains a malformed placeholder: {value!r}")
    validate_portable_path(probe)
    return text


# ---------------------------------------------------------------------------
# Registry snapshot: recomputed identity, no trusted claimed digest
# ---------------------------------------------------------------------------


class RegistryFamilyRow(SemanticsModel):
    """One consumed registry row, typed and closed.

    ``row_digest`` is recomputed from this exact content; a unit's
    ``family_identity_digest`` pins it, so every registry-derived value in the
    plan traces to the recomputed snapshot identity rather than to anything
    the caller claimed.
    """

    kind: str
    owner: str
    requirement: str
    multiplicity: str
    condition: str
    path_scope: str
    path_template: str
    materializer: str
    assignment_kinds: tuple[AssignmentKind, ...]
    validator: ValidatorIdentifier
    dependency_families: tuple[str, ...] = Field(default_factory=tuple)
    semantic_input_kinds: tuple[str, ...] = Field(default_factory=tuple)

    @field_validator(
        "kind", "owner", "requirement", "multiplicity", "condition", "path_scope", "materializer"
    )
    @classmethod
    def _identifiers(cls, value: str, info: Any) -> str:
        return _identifier(value, field_name=str(info.field_name))

    @field_validator("path_template")
    @classmethod
    def _template(cls, value: str) -> str:
        return _template_text(value, field_name="path_template")

    @field_validator("dependency_families")
    @classmethod
    def _deps(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(
            sorted({_identifier(item, field_name="dependency_families") for item in value})
        )

    @field_validator("semantic_input_kinds")
    @classmethod
    def _semantic_inputs(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        try:
            return tuple(sorted({SemanticNodeKind(item).value for item in value}))
        except ValueError as exc:
            raise ValueError("semantic_input_kinds contains an unknown node kind") from exc

    @field_validator("assignment_kinds", mode="before")
    @classmethod
    def _assignment_kinds(cls, value: Any) -> tuple[AssignmentKind, ...]:
        parsed = tuple(AssignmentKind(item) for item in value)
        if len(parsed) != len(set(parsed)):
            raise ValueError("assignment_kinds must be unique")
        return tuple(sorted(parsed, key=lambda item: item.value))

    @property
    def identity_payload(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "owner": self.owner,
            "requirement": self.requirement,
            "multiplicity": self.multiplicity,
            "condition": self.condition,
            "path_scope": self.path_scope,
            "path_template": self.path_template,
            "materializer": self.materializer,
            "assignment_kinds": [kind.value for kind in self.assignment_kinds],
            "validator": self.validator.value,
            "dependency_families": list(self.dependency_families),
            "semantic_input_kinds": list(self.semantic_input_kinds),
        }

    @property
    def row_digest(self) -> str:
        return canonical_digest(self.identity_payload)


class LayoutRegistrySnapshot(SemanticsModel):
    """Typed projection of the sole layout registry with recomputed identity.

    ``snapshot_digest`` is verified against the carried row content, so two
    snapshots with different rows can never share an identity and a forged
    digest fails validation. The snapshot declares nothing of its own — it is
    consumed, not authored, and is not a parallel registry authority.
    """

    registry_schema_version: str
    rows: tuple[RegistryFamilyRow, ...] = Field(min_length=1)
    snapshot_digest: str

    @field_validator("registry_schema_version")
    @classmethod
    def _schema(cls, value: str) -> str:
        text = str(value or "").strip()
        if _SCHEMA_VERSION_RE.fullmatch(text) is None:
            raise ValueError(f"registry_schema_version must be a dotted identifier, got {value!r}")
        return text

    @field_validator("snapshot_digest")
    @classmethod
    def _digest(cls, value: str) -> str:
        return _validate_digest(value, field_name="snapshot_digest")

    @model_validator(mode="after")
    def _validate_snapshot(self) -> LayoutRegistrySnapshot:
        seen: set[tuple[str, str, str]] = set()
        kinds: set[str] = {row.kind for row in self.rows}
        first_position: dict[str, int] = {}
        for index, row in enumerate(self.rows):
            key = (row.kind, row.path_scope, row.path_template)
            if key in seen:
                raise ValueError(f"duplicate registry row {key!r}")
            seen.add(key)
            first_position.setdefault(row.kind, index)
        for row in self.rows:
            for dependency in row.dependency_families:
                if dependency not in kinds:
                    raise ValueError(
                        f"registry row {row.kind!r} depends on unknown family {dependency!r}"
                    )
                if dependency == row.kind:
                    raise ValueError(f"registry row {row.kind!r} depends on itself")
                # Rows arrive in the registry's dependency-respecting total
                # order; a dependency first appearing after its dependent is
                # an inconsistent registry input.
                if first_position[dependency] > first_position[row.kind]:
                    raise ValueError(
                        f"registry order places dependency {dependency!r} after its "
                        f"dependent {row.kind!r}"
                    )
        expected = canonical_digest(
            {
                "registry_schema_version": self.registry_schema_version,
                "rows": [row.identity_payload for row in self.rows],
            }
        )
        if self.snapshot_digest != expected:
            raise ValueError("snapshot_digest does not match snapshot row content")
        return self


def snapshot_layout_registry(registry: Any) -> LayoutRegistrySnapshot:
    """Project the sole layout registry into its typed, self-digesting snapshot.

    Reads only canonical row content from ``registry.ordered_families()`` and
    ``registry.schema_version``; the registry's own claimed digest is never
    consulted. Content that fails the closed row domains fails here.
    """
    try:
        families = tuple(registry.ordered_families())
        schema_version = str(registry.schema_version)
    except (AttributeError, TypeError) as exc:
        raise CompilationPlanError(f"registry input is not a layout registry: {exc}") from exc
    try:
        rows = tuple(
            RegistryFamilyRow(
                kind=getattr(family.kind, "value", family.kind),
                owner=getattr(family.owner, "value", family.owner),
                requirement=getattr(family.requirement, "value", family.requirement),
                multiplicity=getattr(family.multiplicity, "value", family.multiplicity),
                condition=getattr(family.condition, "value", family.condition),
                path_scope=getattr(family.path_scope, "value", family.path_scope),
                path_template=family.path_template,
                materializer=getattr(family.materializer, "value", family.materializer),
                assignment_kinds=tuple(
                    getattr(item, "value", item) for item in family.assignment_kinds
                ),
                validator=getattr(family.validator, "value", family.validator),
                dependency_families=tuple(
                    getattr(item, "value", item) for item in family.dependency_families
                ),
                semantic_input_kinds=tuple(
                    getattr(item, "value", item)
                    for item in getattr(family, "semantic_input_kinds", ())
                ),
            )
            for family in families
        )
    except (AttributeError, TypeError, ValueError) as exc:
        raise CompilationPlanError(
            f"registry rows failed the closed snapshot domains: {exc}"
        ) from exc
    digest = canonical_digest(
        {
            "registry_schema_version": str(schema_version).strip(),
            "rows": [row.identity_payload for row in rows],
        }
    )
    try:
        return LayoutRegistrySnapshot(
            registry_schema_version=schema_version,
            rows=rows,
            snapshot_digest=digest,
        )
    except ValueError as exc:
        raise CompilationPlanError(f"registry snapshot is internally inconsistent: {exc}") from exc


# ---------------------------------------------------------------------------
# Plan documents
# ---------------------------------------------------------------------------


class PlanDisposition(StrEnum):
    """Complete disposition vocabulary from the ADR aggregate-authority rule."""

    RENDER = "render"
    AGENT_AUTHOR = "agent_author"
    REUSE_FROM_BASE = "reuse_from_base"
    PRESERVE_UNOWNED = "preserve_unowned"
    INPUT_ONLY = "input_only"
    EXTERNAL_HANDOFF = "external_handoff"
    INAPPLICABLE = "inapplicable"


class PlanSourceScope(StrEnum):
    """How a unit's reuse signature is sourced.

    ``declared`` units pin an explicit node/payload footprint; ``graph_wide``
    units depend on the whole graph identity, so any graph change affects
    them. The dependency on the entire graph is explicit, never an empty
    source list masquerading as independence.
    """

    DECLARED = "declared"
    GRAPH_WIDE = "graph_wide"


class PlanGapCode(StrEnum):
    """Closed reason codes for explicit deferrals — never free prose."""

    BINDING_CONDITION_DEFERRED = "binding_condition_deferred"
    STAGING_TRANSPORT_EXCLUDED = "staging_transport_excluded"
    CONDITION_UNDERIVABLE = "condition_underivable"
    PLACEHOLDER_UNDERIVABLE = "placeholder_underivable"
    PLACEHOLDER_RELATIONSHIP_UNPROVABLE = "placeholder_relationship_unprovable"
    RENDERER_RESOLUTION_DEFERRED = "renderer_resolution_deferred"
    RENDERER_INPUT_UNDECLARED = "renderer_input_undeclared"
    RENDERER_INPUT_INCOMPLETE = "renderer_input_incomplete"
    ASSIGNMENT_UNDECLARED = "assignment_undeclared"
    ASSIGNMENT_AMBIGUOUS = "assignment_ambiguous"
    VALIDATOR_UNDECLARED = "validator_undeclared"
    OUTPUT_CONTRACT_UNRESOLVED = "output_contract_unresolved"
    SOURCE_FOOTPRINT_INCOMPLETE = "source_footprint_incomplete"


class PlanGap(SemanticsModel):
    """One explicit deferred decision, fully typed.

    ``subject`` names the deferred condition or placeholder set (identifier
    grammar); there is no prose field, so a gap cannot carry arbitrary text.
    """

    code: PlanGapCode
    family_kind: str
    path_template: str
    subject: str | None = None
    adr_slice: int = Field(ge=4, le=7, strict=True)

    @field_validator("family_kind")
    @classmethod
    def _family(cls, value: str) -> str:
        return _identifier(value, field_name="family_kind")

    @field_validator("path_template")
    @classmethod
    def _template(cls, value: str) -> str:
        return _template_text(value, field_name="path_template")

    @field_validator("subject")
    @classmethod
    def _subject(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _identifier(value, field_name="subject")


class PlanSource(SemanticsModel):
    """Traceability leaf: one semantic node/payload identity behind an output."""

    node_id: str
    payload_digest: str

    @field_validator("node_id")
    @classmethod
    def _node(cls, value: str) -> str:
        return validate_node_id_grammar(value)

    @field_validator("payload_digest")
    @classmethod
    def _digest(cls, value: str) -> str:
        return _validate_digest(value, field_name="payload_digest")


class PlanEdgeSource(SemanticsModel):
    """One complete graph relationship whose facts can affect rendered bytes."""

    kind: SemanticEdgeKind
    source_node_id: str
    target_node_id: str
    discriminator: str | None = None
    edge_identity: str

    @field_validator("source_node_id", "target_node_id")
    @classmethod
    def _endpoint(cls, value: str) -> str:
        return validate_node_id_grammar(value)

    @field_validator("discriminator")
    @classmethod
    def _discriminator(cls, value: str | None) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        if not text:
            raise ValueError("discriminator must be non-empty when present")
        return text

    @field_validator("edge_identity")
    @classmethod
    def _identity(cls, value: str) -> str:
        return _validate_digest(value, field_name="edge_identity")

    @model_validator(mode="after")
    def _identity_matches_facts(self) -> PlanEdgeSource:
        expected = canonical_digest(
            {
                "kind": self.kind.value,
                "source_node_id": self.source_node_id,
                "target_node_id": self.target_node_id,
                "discriminator": self.discriminator,
            }
        )
        if self.edge_identity != expected:
            raise ValueError("edge_identity does not match edge source facts")
        return self


class PlanOutput(SemanticsModel):
    """One planned output path inside its registry path scope."""

    path_scope: str
    path: str

    @field_validator("path_scope")
    @classmethod
    def _scope(cls, value: str) -> str:
        return _identifier(value, field_name="path_scope")

    @field_validator("path")
    @classmethod
    def _path(cls, value: str) -> str:
        text = validate_portable_path(value).text
        if "{" in text or "}" in text:
            raise ValueError(f"planned output path carries an unresolved placeholder: {value!r}")
        return text


class FamilyInstancePlan(SemanticsModel):
    """Embedded, non-authoritative per-family-instance subplan.

    Valid only under its aggregate plan's identity and digest: there is no
    reference type, resolver registration, or execution surface for a family
    plan on its own, and this model carries no self-digest to stand on.
    """

    unit_id: str
    family_kind: str
    family_identity_digest: str
    disposition: PlanDisposition
    source_scope: PlanSourceScope
    placeholder_values: tuple[tuple[str, str], ...] = Field(default_factory=tuple)
    outputs: tuple[PlanOutput, ...] = Field(default_factory=tuple)
    sources: tuple[PlanSource, ...] = Field(default_factory=tuple)
    edge_sources: tuple[PlanEdgeSource, ...] = Field(default_factory=tuple)
    depends_on_units: tuple[str, ...] = Field(default_factory=tuple)
    materializer: str
    assignment_kind: AssignmentKind | None = None
    validator: ValidatorIdentifier
    required_structured_output_ref: StructuredOutputContractRef | None = None
    base_plan_digest: str | None = None

    @field_validator("unit_id")
    @classmethod
    def _unit_id(cls, value: str) -> str:
        text = str(value or "").strip()
        parts = text.split("/")
        if not parts or not all(_UNIT_SEGMENT_RE.fullmatch(part) for part in parts):
            raise ValueError(f"unit_id must be a normalized identifier path, got {value!r}")
        return text

    @field_validator("family_kind", "materializer")
    @classmethod
    def _identifiers(cls, value: str, info: Any) -> str:
        return _identifier(value, field_name=str(info.field_name))

    @field_validator("family_identity_digest")
    @classmethod
    def _family_digest(cls, value: str) -> str:
        return _validate_digest(value, field_name="family_identity_digest")

    @field_validator("base_plan_digest")
    @classmethod
    def _base_digest(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _validate_digest(value, field_name="base_plan_digest")

    @field_validator("placeholder_values")
    @classmethod
    def _placeholders(cls, value: tuple[tuple[str, str], ...]) -> tuple[tuple[str, str], ...]:
        normalized: list[tuple[str, str]] = []
        for name, item in sorted(value):
            clean_name = _identifier(name, field_name="placeholder name")
            clean_value = str(item).strip()
            if _VALUE_RE.fullmatch(clean_value) is None:
                raise ValueError(f"placeholder value {item!r} is outside the closed domain")
            normalized.append((clean_name, clean_value))
        names = [name for name, _v in normalized]
        if len(names) != len(set(names)):
            raise ValueError("duplicate placeholder names in one unit")
        return tuple(normalized)

    @field_validator("outputs")
    @classmethod
    def _outputs(cls, value: tuple[PlanOutput, ...]) -> tuple[PlanOutput, ...]:
        ordered = tuple(sorted(value, key=lambda item: (item.path_scope, item.path)))
        keys = [(item.path_scope, item.path) for item in ordered]
        if len(keys) != len(set(keys)):
            raise ValueError("duplicate output paths in one unit")
        return ordered

    @field_validator("sources")
    @classmethod
    def _sources(cls, value: tuple[PlanSource, ...]) -> tuple[PlanSource, ...]:
        ordered = tuple(sorted(value, key=lambda item: item.node_id))
        node_ids = [item.node_id for item in ordered]
        if len(node_ids) != len(set(node_ids)):
            raise ValueError("duplicate source node identities in one unit")
        return ordered

    @field_validator("edge_sources")
    @classmethod
    def _edge_sources(cls, value: tuple[PlanEdgeSource, ...]) -> tuple[PlanEdgeSource, ...]:
        ordered = tuple(sorted(value, key=lambda item: item.edge_identity))
        identities = [item.edge_identity for item in ordered]
        if len(identities) != len(set(identities)):
            raise ValueError("duplicate edge source identities in one unit")
        return ordered

    @field_validator("depends_on_units")
    @classmethod
    def _deps(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        ordered = tuple(sorted(str(item).strip() for item in value))
        if len(ordered) != len(set(ordered)):
            raise ValueError("duplicate unit dependencies")
        return ordered

    @model_validator(mode="after")
    def _shape(self) -> FamilyInstancePlan:
        if (self.disposition is PlanDisposition.REUSE_FROM_BASE) != (
            self.base_plan_digest is not None
        ):
            raise ValueError(
                "base_plan_digest is required exactly when disposition is reuse_from_base"
            )
        if self.source_scope is PlanSourceScope.GRAPH_WIDE and (
            self.sources or self.edge_sources
        ):
            raise ValueError("graph_wide units must not also declare explicit sources")
        executable = self.disposition is PlanDisposition.AGENT_AUTHOR
        if executable != (
            self.assignment_kind is not None
            and self.validator is not ValidatorIdentifier.NONE
            and self.required_structured_output_ref is not None
        ):
            raise ValueError("executable metadata is required exactly for agent_author units")
        if not executable and (
            self.assignment_kind is not None or self.required_structured_output_ref is not None
        ):
            raise ValueError("non-agent units cannot carry executable metadata")
        if executable and (
            self.source_scope is not PlanSourceScope.DECLARED
            or not self.sources
            or not self.outputs
        ):
            raise ValueError("agent_author requires declared non-empty sources and outputs")
        for output in self.outputs:
            if output.path_scope in _INSTANCE_PATH_SCOPES and not self.placeholder_values:
                raise ValueError(
                    f"instance-relative output scope {output.path_scope!r} requires an "
                    "explicit instance identity"
                )
        return self

    @property
    def identity_payload(self) -> dict[str, Any]:
        return {
            "unit_id": self.unit_id,
            "family_kind": self.family_kind,
            "family_identity_digest": self.family_identity_digest,
            "disposition": self.disposition.value,
            "source_scope": self.source_scope.value,
            "placeholder_values": [list(pair) for pair in self.placeholder_values],
            "outputs": [
                {"path_scope": output.path_scope, "path": output.path}
                for output in self.outputs
            ],
            "sources": [
                {"node_id": source.node_id, "payload_digest": source.payload_digest}
                for source in self.sources
            ],
            "edge_sources": [source.model_dump(mode="json") for source in self.edge_sources],
            "depends_on_units": list(self.depends_on_units),
            "materializer": self.materializer,
            "assignment_kind": self.assignment_kind.value if self.assignment_kind else None,
            "validator": self.validator.value,
            "required_structured_output_ref": (
                self.required_structured_output_ref.model_dump(mode="json")
                if self.required_structured_output_ref
                else None
            ),
            "base_plan_digest": self.base_plan_digest,
        }

    @property
    def unit_digest(self) -> str:
        return canonical_digest(self.identity_payload)


class CompilationPlan(SemanticsModel):
    """One aggregate authoritative plan per immutable graph identity."""

    schema_version: Literal["mozaiks.compilation_plan.v1"] = COMPILATION_PLAN_SCHEMA_VERSION
    graph_id: str
    graph_version: int = Field(ge=1, strict=True)
    scope: ExecutionAccessScopeRef
    graph_digest: str
    registry_schema_version: str
    registry_digest: str
    units: tuple[FamilyInstancePlan, ...]
    gaps: tuple[PlanGap, ...] = Field(default_factory=tuple)
    plan_digest: str

    @field_validator("graph_digest", "registry_digest")
    @classmethod
    def _digests(cls, value: str, info: Any) -> str:
        return _validate_digest(value, field_name=str(info.field_name))

    @field_validator("plan_digest")
    @classmethod
    def _plan_digest_field(cls, value: str) -> str:
        return _validate_digest(value, field_name="plan_digest")

    @field_validator("registry_schema_version")
    @classmethod
    def _registry_schema(cls, value: str) -> str:
        text = str(value or "").strip()
        if _SCHEMA_VERSION_RE.fullmatch(text) is None:
            raise ValueError(f"registry_schema_version must be a dotted identifier, got {value!r}")
        return text

    @field_validator("graph_id")
    @classmethod
    def _graph_id(cls, value: str) -> str:
        text = str(value or "").strip()
        if not text:
            raise ValueError("graph_id must be non-empty")
        return text

    @field_validator("gaps")
    @classmethod
    def _gaps(cls, value: tuple[PlanGap, ...]) -> tuple[PlanGap, ...]:
        ordered = tuple(
            sorted(
                value,
                key=lambda gap: (
                    gap.family_kind,
                    gap.path_template,
                    gap.code.value,
                    gap.subject or "",
                ),
            )
        )
        keys = [
            (gap.family_kind, gap.path_template, gap.code, gap.subject) for gap in ordered
        ]
        if len(keys) != len(set(keys)):
            raise ValueError("duplicate plan gaps")
        return ordered

    @model_validator(mode="after")
    def _validate_plan(self) -> CompilationPlan:
        unit_ids = [unit.unit_id for unit in self.units]
        if len(unit_ids) != len(set(unit_ids)):
            duplicates = sorted({item for item in unit_ids if unit_ids.count(item) > 1})
            raise ValueError(f"duplicate unit identities in one plan: {duplicates}")
        known = set(unit_ids)
        edges: dict[str, tuple[str, ...]] = {}
        for unit in self.units:
            for dependency in unit.depends_on_units:
                if dependency not in known:
                    raise ValueError(
                        f"unit {unit.unit_id!r} depends on unknown unit {dependency!r}"
                    )
                if dependency == unit.unit_id:
                    raise ValueError(f"unit {unit.unit_id!r} depends on itself")
            edges[unit.unit_id] = unit.depends_on_units
        state: dict[str, int] = {}
        for start in edges:
            if state.get(start):
                continue
            stack: list[tuple[str, int]] = [(start, 0)]
            while stack:
                current, index = stack.pop()
                if index == 0:
                    state[current] = 1
                deps = edges[current]
                advanced = False
                for position in range(index, len(deps)):
                    dependency = deps[position]
                    if state.get(dependency) == 1:
                        raise ValueError(f"dependency cycle through unit {dependency!r}")
                    if state.get(dependency, 0) == 0:
                        stack.append((current, position + 1))
                        stack.append((dependency, 0))
                        advanced = True
                        break
                if not advanced:
                    state[current] = 2

        # Physical output ownership: global roots form one collision domain
        # regardless of semantic instance identity; instance-relative roots
        # are discriminated by their explicit instance identity only.
        by_domain: dict[tuple[str, tuple[tuple[str, str], ...]], list[str]] = {}
        for unit in self.units:
            for output in unit.outputs:
                if output.path_scope in _INSTANCE_PATH_SCOPES:
                    domain = (output.path_scope, unit.placeholder_values)
                else:
                    domain = (output.path_scope, ())
                by_domain.setdefault(domain, []).append(output.path)
        for domain, paths in sorted(by_domain.items()):
            if len(paths) != len(set(paths)):
                duplicates = sorted({p for p in paths if paths.count(p) > 1})
                raise ValueError(
                    f"duplicate output ownership in domain {domain[0]!r}: {duplicates}"
                )
            try:
                detect_collisions(paths)
            except ValueError as exc:
                raise ValueError(f"output collision in domain {domain[0]!r}: {exc}") from exc
        expected = canonical_digest(self.canonical_payload(include_digest=False))
        if self.plan_digest != expected:
            raise ValueError("plan_digest does not match plan content")
        return self

    def canonical_payload(self, *, include_digest: bool = True) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "schema_version": self.schema_version,
            "graph_id": self.graph_id,
            "graph_version": self.graph_version,
            "scope": self.scope.model_dump(mode="json"),
            "graph_digest": self.graph_digest,
            "registry_schema_version": self.registry_schema_version,
            "registry_digest": self.registry_digest,
            "units": [unit.identity_payload for unit in self.units],
            "gaps": [gap.model_dump(mode="json") for gap in self.gaps],
        }
        if include_digest:
            payload["plan_digest"] = self.plan_digest
        return payload

    def unit(self, unit_id: str) -> FamilyInstancePlan:
        for candidate in self.units:
            if candidate.unit_id == unit_id:
                return candidate
        raise CompilationPlanError(f"unknown plan unit {unit_id!r}")


# ---------------------------------------------------------------------------
# Derivation
# ---------------------------------------------------------------------------

_DERIVABLE_CONDITIONS: dict[str, tuple[SemanticNodeKind, ...]] = {
    "always": (),
    "when_app_declared": (),
    "when_module_declared": (SemanticNodeKind.MODULE,),
    "when_page_declared": (SemanticNodeKind.PAGE,),
    "when_workflow_declared": (SemanticNodeKind.WORKFLOW,),
    "when_data_contract_required": (SemanticNodeKind.DATA_COLLECTION,),
    "when_subscriptions_required": (SemanticNodeKind.PLAN,),
    "when_deployment_export_requested": (SemanticNodeKind.DEPLOYMENT_TARGET,),
}

_BINDING_CONDITIONS: frozenset[str] = frozenset(
    {
        "when_auth_enabled",
        "when_custom_route_declared",
        "when_refinement_harness_required",
        "when_managed_capability_selected",
        "when_generated_staging_selected",
        "when_extension_selected",
    }
)

_INSTANCE_PLACEHOLDERS: dict[str, SemanticNodeKind] = {
    "module_id": SemanticNodeKind.MODULE,
    "page_id": SemanticNodeKind.PAGE,
    "workflow_id": SemanticNodeKind.WORKFLOW,
}

#: Instance-relative path scopes imply an instance placeholder even when the
#: template does not spell it: the scope root itself is the instance.
_SCOPE_IMPLIED_PLACEHOLDER: dict[str, str] = {
    "module_relative": "module_id",
    "workflow_relative": "workflow_id",
}

_FAMILY_SOURCE_KINDS: dict[str, tuple[SemanticNodeKind, ...]] = {
    "when_data_contract_required": (
        SemanticNodeKind.DATA_COLLECTION,
        SemanticNodeKind.DATA_ALIAS,
    ),
    "when_subscriptions_required": (
        SemanticNodeKind.PLAN,
        SemanticNodeKind.PRODUCT,
        SemanticNodeKind.METER,
        SemanticNodeKind.LIMIT,
    ),
    "when_deployment_export_requested": (SemanticNodeKind.DEPLOYMENT_TARGET,),
    "when_page_declared": (SemanticNodeKind.PAGE,),
    "when_workflow_declared": (SemanticNodeKind.WORKFLOW,),
    "when_module_declared": (SemanticNodeKind.MODULE,),
}

_AGENT_AUTHORED_MATERIALIZERS = frozenset(
    {"app_generator", "module_contract_executor", "workflow_generator"}
)


def _cold_validate_inputs(
    graph: SemanticGraphV2, payloads: Iterable[SemanticPayloadBase]
) -> tuple[SemanticGraphV2, dict[str, SemanticPayloadBase]]:
    try:
        verified_graph = SemanticGraphV2.model_validate(graph.model_dump(mode="json"))
    except (TypeError, ValueError) as exc:
        raise CompilationPlanError(f"semantic graph failed cold validation: {exc}") from exc
    verified_payloads: list[SemanticPayloadBase] = []
    for payload in payloads:
        try:
            verified_payloads.append(parse_semantic_payload(payload.model_dump(mode="json")))
        except (TypeError, ValueError) as exc:
            raise CompilationPlanError(f"semantic payload failed cold validation: {exc}") from exc
    try:
        validate_semantic_graph_v2_payload_closure(verified_graph, verified_payloads)
    except ValueError as exc:
        raise CompilationPlanError(f"payload closure failed: {exc}") from exc
    return verified_graph, {payload.node_id: payload for payload in verified_payloads}


def derive_compilation_plan(
    *,
    graph: SemanticGraphV2,
    payloads: Iterable[SemanticPayloadBase],
    registry: Any,
    structured_output_configs: Mapping[str, Any] | None = None,
) -> CompilationPlan:
    """Derive the single aggregate plan for one immutable graph identity.

    ``registry`` is the sole ``AppLayoutRegistry`` (or an already-built
    :class:`LayoutRegistrySnapshot`). Registry identity is always recomputed
    from the canonical row content actually consumed — a claimed digest on the
    input object is never read.
    """
    verified_graph, payload_by_node = _cold_validate_inputs(graph, payloads)
    output_configs = dict(structured_output_configs or {})
    snapshot = (
        registry
        if isinstance(registry, LayoutRegistrySnapshot)
        else snapshot_layout_registry(registry)
    )

    nodes_by_kind: dict[SemanticNodeKind, list[Any]] = {}
    for node in verified_graph.nodes:
        nodes_by_kind.setdefault(node.kind, []).append(node)

    def _sources_for(kinds: tuple[SemanticNodeKind, ...]) -> tuple[PlanSource, ...]:
        collected = []
        for kind in kinds:
            for node in nodes_by_kind.get(kind, ()):
                collected.append(
                    PlanSource(
                        node_id=node.node_id,
                        payload_digest=payload_by_node[node.node_id].payload_digest,
                    )
                )
        return tuple(collected)

    node_by_id = {node.node_id: node for node in verified_graph.nodes}

    def _renderer_footprint(
        row: RegistryFamilyRow, *, root_node_id: str | None = None
    ) -> tuple[tuple[PlanSource, ...], tuple[PlanEdgeSource, ...]]:
        allowed = {SemanticNodeKind(kind) for kind in row.semantic_input_kinds}
        if not allowed:
            return (), ()
        if root_node_id is None:
            selected = {
                node.node_id for node in verified_graph.nodes if node.kind in allowed
            }
        else:
            selected = {root_node_id}
            root_payload = payload_by_node[root_node_id]
            if isinstance(root_payload, PagePayload):
                selected.update(
                    entry.section_node_id
                    for entry in root_payload.sections
                    if node_by_id[entry.section_node_id].kind in allowed
                )
            for edge in verified_graph.edges:
                if edge.source_node_id == root_node_id:
                    other = edge.target_node_id
                elif edge.target_node_id == root_node_id:
                    other = edge.source_node_id
                else:
                    continue
                if node_by_id[other].kind in allowed:
                    selected.add(other)
            expandable = {
                SemanticNodeKind.ACTION,
                SemanticNodeKind.TRIGGER,
                SemanticNodeKind.REACTION,
                SemanticNodeKind.NOTIFICATION,
            }
            for linked_id in tuple(selected):
                if node_by_id[linked_id].kind not in expandable:
                    continue
                for edge in verified_graph.edges:
                    if edge.source_node_id == linked_id:
                        other = edge.target_node_id
                    elif edge.target_node_id == linked_id:
                        other = edge.source_node_id
                    else:
                        continue
                    if node_by_id[other].kind in allowed:
                        selected.add(other)
        sources = tuple(
            PlanSource(
                node_id=node_id,
                payload_digest=payload_by_node[node_id].payload_digest,
            )
            for node_id in sorted(selected)
        )
        edge_sources = tuple(
            PlanEdgeSource(
                kind=edge.kind,
                source_node_id=edge.source_node_id,
                target_node_id=edge.target_node_id,
                discriminator=edge.discriminator,
                edge_identity=edge.edge_identity,
            )
            for edge in verified_graph.edges
            if edge.source_node_id in selected and edge.target_node_id in selected
        )
        return sources, edge_sources

    def _placeholder_value(placeholder: str, node_id: str) -> str:
        identity_fields = {
            "page_id": "page_id",
            "module_id": "module_id",
            "workflow_id": "workflow_id",
        }
        field = identity_fields[placeholder]
        value = str(getattr(payload_by_node[node_id], field, "") or "").strip()
        if _VALUE_RE.fullmatch(value) is None:
            raise CompilationPlanError(
                f"payload {node_id!r} lacks canonical renderer identity {field!r}"
            )
        return value

    def _renderer_inputs_complete(
        row: RegistryFamilyRow, *, root_node_id: str | None
    ) -> bool:
        """Recognize only the bounded corpus whose semantic facts are closed.

        Other declared footprints are useful dependency evidence, but they do
        not imply byte-complete renderer inputs.  Those families remain typed
        gaps until a later prerequisite explicitly closes their normative
        source models.
        """
        if row.kind != "app_ui_page_schema" or root_node_id is None:
            return False
        page = payload_by_node[root_node_id]
        if not isinstance(page, PagePayload):
            return False
        if not all(
            (
                page.page_id,
                page.route,
                page.title,
                page.page_type,
                page.layout,
                page.sections,
            )
        ):
            return False
        return all(
            getattr(payload_by_node.get(entry.section_node_id), "declarative", None)
            is not None
            for entry in page.sections
        )

    units: dict[str, FamilyInstancePlan] = {}
    gaps: list[PlanGap] = []
    unit_ids_by_kind: dict[str, list[str]] = {}
    unit_instance_index: dict[tuple[str, tuple[tuple[str, str], ...]], str] = {}

    def _add_unit(unit: FamilyInstancePlan) -> None:
        if unit.unit_id in units:
            raise CompilationPlanError(
                f"derivation produced duplicate unit identity {unit.unit_id!r}"
            )
        units[unit.unit_id] = unit
        unit_ids_by_kind.setdefault(unit.family_kind, []).append(unit.unit_id)

    def _gap(
        row: RegistryFamilyRow,
        code: PlanGapCode,
        *,
        subject: str | None = None,
        adr_slice: int = 4,
    ) -> None:
        gaps.append(
            PlanGap(
                code=code,
                family_kind=row.kind,
                path_template=row.path_template,
                subject=subject,
                adr_slice=adr_slice,
            )
        )

    for row in snapshot.rows:
        row_digest = row.row_digest
        placeholders = tuple(_PLACEHOLDER_RE.findall(row.path_template))

        if row.requirement == "prohibited":
            _add_unit(
                FamilyInstancePlan(
                    unit_id=f"{row.kind}/prohibited/{row_digest[:12]}",
                    family_kind=row.kind,
                    family_identity_digest=row_digest,
                    disposition=PlanDisposition.INAPPLICABLE,
                    source_scope=PlanSourceScope.DECLARED,
                    materializer=row.materializer,
                    validator=row.validator,
                )
            )
            continue

        if row.condition in _BINDING_CONDITIONS:
            _gap(row, PlanGapCode.BINDING_CONDITION_DEFERRED, subject=row.condition)
            continue

        if row.path_scope == "generated_staging":
            _gap(row, PlanGapCode.STAGING_TRANSPORT_EXCLUDED)
            continue

        trigger_kinds = _DERIVABLE_CONDITIONS.get(row.condition)
        if trigger_kinds is None:
            _gap(row, PlanGapCode.CONDITION_UNDERIVABLE, subject=row.condition)
            continue

        bindable = {name for name in placeholders if name in _INSTANCE_PLACEHOLDERS}
        unbindable = [name for name in placeholders if name not in _INSTANCE_PLACEHOLDERS]
        scope_implied = _SCOPE_IMPLIED_PLACEHOLDER.get(row.path_scope)
        if scope_implied is not None:
            # The scope root is itself an instance: the implied placeholder
            # joins the binding set even when the template does not spell it.
            bindable.add(scope_implied)
        if unbindable:
            _gap(
                row,
                PlanGapCode.PLACEHOLDER_UNDERIVABLE,
                subject="__".join(sorted(set(unbindable))),
            )
            continue
        if len(bindable) > 1:
            # Joint instantiation across two node kinds requires a typed graph
            # relationship this slice cannot prove; never invent one.
            _gap(
                row,
                PlanGapCode.PLACEHOLDER_RELATIONSHIP_UNPROVABLE,
                subject="__".join(sorted(bindable)),
            )
            continue

        condition_met = not trigger_kinds or any(
            nodes_by_kind.get(kind) for kind in trigger_kinds
        )
        if not condition_met:
            _add_unit(
                FamilyInstancePlan(
                    unit_id=f"{row.kind}/{row_digest[:12]}",
                    family_kind=row.kind,
                    family_identity_digest=row_digest,
                    disposition=PlanDisposition.INAPPLICABLE,
                    source_scope=PlanSourceScope.DECLARED,
                    materializer=row.materializer,
                    validator=row.validator,
                )
            )
            continue

        assignment_kind: AssignmentKind | None = None
        output_ref: StructuredOutputContractRef | None = None
        if row.materializer in {"human_authored", "preserved_opaque"}:
            disposition = PlanDisposition.PRESERVE_UNOWNED
        elif row.path_scope == "deployment_derived" or row.owner == "download_renderer":
            disposition = PlanDisposition.EXTERNAL_HANDOFF
        elif row.materializer == "page_schema_executor":
            disposition = PlanDisposition.RENDER
        elif row.materializer in _AGENT_AUTHORED_MATERIALIZERS:
            if not row.assignment_kinds:
                _gap(row, PlanGapCode.ASSIGNMENT_UNDECLARED, adr_slice=5)
                continue
            if len(row.assignment_kinds) != 1:
                _gap(row, PlanGapCode.ASSIGNMENT_AMBIGUOUS, adr_slice=5)
                continue
            if row.validator is ValidatorIdentifier.NONE:
                _gap(row, PlanGapCode.VALIDATOR_UNDECLARED, adr_slice=5)
                continue
            assignment_kind = row.assignment_kinds[0]
            descriptor = assignment_contract_descriptor(assignment_kind)
            if descriptor is None:
                _gap(row, PlanGapCode.OUTPUT_CONTRACT_UNRESOLVED, adr_slice=5)
                continue
            try:
                output_ref = build_structured_output_contract_ref(
                    workflow_name=descriptor.workflow_name,
                    model_id=descriptor.structured_output_model_id,
                    configs=output_configs,
                )
            except (TypeError, ValueError):
                _gap(row, PlanGapCode.OUTPUT_CONTRACT_UNRESOLVED, adr_slice=5)
                continue
            disposition = PlanDisposition.AGENT_AUTHOR
        else:
            disposition = PlanDisposition.RENDER

        if disposition is PlanDisposition.RENDER and not row.semantic_input_kinds:
            _gap(row, PlanGapCode.RENDERER_INPUT_UNDECLARED, adr_slice=4)
            continue

        if bindable:
            placeholder = next(iter(bindable))
            node_kind = _INSTANCE_PLACEHOLDERS[placeholder]
            for node in nodes_by_kind.get(node_kind, ()):
                local = _placeholder_value(placeholder, node.node_id)
                if disposition is PlanDisposition.RENDER and not _renderer_inputs_complete(
                    row, root_node_id=node.node_id
                ):
                    _gap(
                        row,
                        PlanGapCode.RENDERER_INPUT_INCOMPLETE,
                        subject=local,
                        adr_slice=4,
                    )
                    continue
                values = ((placeholder, local),)
                path = row.path_template.replace("{" + placeholder + "}", local)
                # Scope-implied instances keep the template text unchanged;
                # their instance identity lives in placeholder_values and the
                # collision domain, not the relative path.

                unit_sources, unit_edge_sources = _renderer_footprint(
                    row, root_node_id=node.node_id
                )
                resolved_sources = unit_sources or (
                    PlanSource(
                        node_id=node.node_id,
                        payload_digest=payload_by_node[node.node_id].payload_digest,
                    ),
                )
                if disposition is PlanDisposition.AGENT_AUTHOR and not resolved_sources:
                    _gap(
                        row,
                        PlanGapCode.SOURCE_FOOTPRINT_INCOMPLETE,
                        subject=local,
                        adr_slice=5,
                    )
                    continue
                unit = FamilyInstancePlan(
                    unit_id=f"{row.kind}/{local}/{row_digest[:12]}",
                    family_kind=row.kind,
                    family_identity_digest=row_digest,
                    disposition=disposition,
                    source_scope=PlanSourceScope.DECLARED,
                    placeholder_values=values,
                    outputs=(PlanOutput(path_scope=row.path_scope, path=path),),
                    sources=resolved_sources,
                    edge_sources=unit_edge_sources,
                    materializer=row.materializer,
                    validator=row.validator,
                    assignment_kind=assignment_kind,
                    required_structured_output_ref=output_ref,
                )
                _add_unit(unit)
                unit_instance_index[(row.kind, values)] = unit.unit_id
        else:
            if disposition is PlanDisposition.RENDER and not _renderer_inputs_complete(
                row, root_node_id=None
            ):
                _gap(row, PlanGapCode.RENDERER_INPUT_INCOMPLETE, adr_slice=4)
                continue
            source_kinds = _FAMILY_SOURCE_KINDS.get(row.condition, ())
            declared_sources, declared_edge_sources = _renderer_footprint(row)
            graph_wide = not trigger_kinds and not source_kinds and not row.semantic_input_kinds
            resolved_sources = (
                () if graph_wide else declared_sources or _sources_for(source_kinds)
            )
            if disposition is PlanDisposition.AGENT_AUTHOR and (
                graph_wide or not resolved_sources
            ):
                _gap(row, PlanGapCode.SOURCE_FOOTPRINT_INCOMPLETE, adr_slice=5)
                continue
            unit = FamilyInstancePlan(
                unit_id=f"{row.kind}/{row_digest[:12]}",
                family_kind=row.kind,
                family_identity_digest=row_digest,
                disposition=disposition,
                source_scope=(
                    PlanSourceScope.GRAPH_WIDE if graph_wide else PlanSourceScope.DECLARED
                ),
                outputs=(PlanOutput(path_scope=row.path_scope, path=row.path_template),),
                sources=resolved_sources,
                edge_sources=() if graph_wide else declared_edge_sources,
                materializer=row.materializer,
                validator=row.validator,
                assignment_kind=assignment_kind,
                required_structured_output_ref=output_ref,
            )
            _add_unit(unit)
            unit_instance_index[(row.kind, ())] = unit.unit_id

    gaps.append(
        PlanGap(
            code=PlanGapCode.RENDERER_RESOLUTION_DEFERRED,
            family_kind="registry",
            path_template="registry",
            adr_slice=4,
        )
    )

    row_by_digest: dict[str, RegistryFamilyRow] = {row.row_digest: row for row in snapshot.rows}
    dependency_map: dict[str, tuple[str, ...]] = {}
    for unit in units.values():
        dep_ids: set[str] = set()
        row = row_by_digest[unit.family_identity_digest]
        for dependency_kind in row.dependency_families:
            same_instance = unit_instance_index.get((dependency_kind, unit.placeholder_values))
            if same_instance is not None:
                dep_ids.add(same_instance)
            else:
                dep_ids.update(unit_ids_by_kind.get(dependency_kind, ()))
        dependency_map[unit.unit_id] = tuple(sorted(dep_ids))

    rebuilt_units = tuple(
        FamilyInstancePlan.model_validate(
            {
                **unit.model_dump(mode="json"),
                "depends_on_units": list(dependency_map[unit.unit_id]),
            }
        )
        for unit in units.values()
    )
    kind_index = {row.kind: index for index, row in enumerate(snapshot.rows)}
    sorted_units = tuple(
        sorted(
            rebuilt_units,
            key=lambda unit: (kind_index.get(unit.family_kind, 1_000_000), unit.unit_id),
        )
    )
    sorted_gaps = tuple(
        sorted(
            set(gaps),
            key=lambda gap: (gap.family_kind, gap.path_template, gap.code.value, gap.subject or ""),
        )
    )

    payload: dict[str, Any] = {
        "schema_version": COMPILATION_PLAN_SCHEMA_VERSION,
        "graph_id": verified_graph.graph_id,
        "graph_version": verified_graph.version,
        "scope": verified_graph.scope.model_dump(mode="json"),
        "graph_digest": verified_graph.graph_digest,
        "registry_schema_version": snapshot.registry_schema_version,
        "registry_digest": snapshot.snapshot_digest,
        "units": [unit.identity_payload for unit in sorted_units],
        "gaps": [gap.model_dump(mode="json") for gap in sorted_gaps],
    }
    return CompilationPlan(
        graph_id=verified_graph.graph_id,
        graph_version=verified_graph.version,
        scope=verified_graph.scope,
        graph_digest=verified_graph.graph_digest,
        registry_schema_version=snapshot.registry_schema_version,
        registry_digest=snapshot.snapshot_digest,
        units=sorted_units,
        gaps=sorted_gaps,
        plan_digest=canonical_digest(payload),
    )


# ---------------------------------------------------------------------------
# Refinement impact (pure comparison; mutates no production authority)
# ---------------------------------------------------------------------------


class RegenerationClosure(SemanticsModel):
    """Complete unit partition between a base plan and a successor plan.

    Directly changed units are found from complete reuse signatures (family
    row identity, disposition, outputs, placeholders, materializer, declared
    sources, and — for graph-wide units — the graph identity itself), then
    affectedness propagates through the reverse dependency DAG: a dependent of
    an affected unit can never remain reusable, because no contract proves its
    output independent of the changed dependency. Every successor unit lands
    in exactly one of ``affected``/``reusable``/``added``; every base-only
    unit lands in ``removed``. Nothing is omitted and nothing is silently
    preserved.
    """

    base_plan_digest: str
    successor_plan_digest: str
    affected: tuple[str, ...]
    reusable: tuple[str, ...]
    added: tuple[str, ...]
    removed: tuple[str, ...]

    @field_validator("base_plan_digest", "successor_plan_digest")
    @classmethod
    def _digests(cls, value: str) -> str:
        return _validate_digest(value, field_name="plan digest")


def _reuse_signature(unit: FamilyInstancePlan, plan: CompilationPlan) -> Any:
    return (
        unit.family_identity_digest,
        unit.disposition.value,
        unit.source_scope.value,
        unit.placeholder_values,
        tuple((output.path_scope, output.path) for output in unit.outputs),
        tuple((source.node_id, source.payload_digest) for source in unit.sources),
        tuple(source.edge_identity for source in unit.edge_sources),
        unit.depends_on_units,
        unit.materializer,
        plan.graph_digest if unit.source_scope is PlanSourceScope.GRAPH_WIDE else None,
    )


def plan_regeneration_closure(
    base: CompilationPlan, successor: CompilationPlan
) -> RegenerationClosure:
    """Identify affected plan units between two plans of one graph lineage."""
    if (base.graph_id, base.scope) != (successor.graph_id, successor.scope):
        raise CompilationPlanError(
            "regeneration closure requires plans from one graph lineage and scope"
        )
    base_units: Mapping[str, FamilyInstancePlan] = {u.unit_id: u for u in base.units}
    successor_units: Mapping[str, FamilyInstancePlan] = {
        u.unit_id: u for u in successor.units
    }

    added = sorted(unit_id for unit_id in successor_units if unit_id not in base_units)
    removed = sorted(unit_id for unit_id in base_units if unit_id not in successor_units)
    directly_affected = {
        unit_id
        for unit_id, unit in successor_units.items()
        if unit_id in base_units
        and _reuse_signature(unit, successor) != _reuse_signature(base_units[unit_id], base)
    }

    # Reverse-dependency propagation to a fixed point: dependents of any
    # affected or added unit are affected too.
    dependents: dict[str, set[str]] = {unit_id: set() for unit_id in successor_units}
    for unit_id, unit in successor_units.items():
        for dependency in unit.depends_on_units:
            dependents.setdefault(dependency, set()).add(unit_id)
    frontier = set(directly_affected) | set(added)
    affected: set[str] = set(directly_affected)
    while frontier:
        next_frontier: set[str] = set()
        for unit_id in frontier:
            for dependent in dependents.get(unit_id, ()):
                if dependent not in affected and dependent not in added:
                    affected.add(dependent)
                    next_frontier.add(dependent)
        frontier = next_frontier

    reusable = sorted(
        unit_id
        for unit_id in successor_units
        if unit_id not in affected and unit_id not in added
    )

    closure = RegenerationClosure(
        base_plan_digest=base.plan_digest,
        successor_plan_digest=successor.plan_digest,
        affected=tuple(sorted(affected)),
        reusable=tuple(reusable),
        added=tuple(added),
        removed=tuple(removed),
    )
    partition = set(closure.affected) | set(closure.reusable) | set(closure.added)
    overlap = (
        (set(closure.affected) & set(closure.reusable))
        | (set(closure.affected) & set(closure.added))
        | (set(closure.reusable) & set(closure.added))
    )
    if partition != set(successor_units) or overlap or (
        set(closure.removed) != set(base_units) - set(successor_units)
    ):
        raise CompilationPlanError("regeneration closure failed to partition all units")
    return closure


__all__ = [
    "COMPILATION_PLAN_SCHEMA_VERSION",
    "CompilationPlan",
    "CompilationPlanError",
    "FamilyInstancePlan",
    "LayoutRegistrySnapshot",
    "PlanDisposition",
    "PlanGap",
    "PlanGapCode",
    "PlanEdgeSource",
    "PlanOutput",
    "PlanSource",
    "PlanSourceScope",
    "RegenerationClosure",
    "RegistryFamilyRow",
    "derive_compilation_plan",
    "plan_regeneration_closure",
    "snapshot_layout_registry",
]
