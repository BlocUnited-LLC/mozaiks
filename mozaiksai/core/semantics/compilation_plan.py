"""ADR 0007 Slice 4B aggregate ``CompilationPlan`` derivation (offline-only).

Exactly one aggregate plan is derived per immutable graph identity from three
inputs and nothing else: a validated ``SemanticGraphV2``, its typed payload
closure, and the sole ``layout_registry``. The plan pins the complete input
identity (graph quartet + registry digest) and embeds one non-authoritative
``FamilyInstancePlan`` subdocument per applicable artifact-family instance.
Embedded family plans have no independent reference type, registration,
resolution, publication, or execution authority — they are valid only under
the aggregate plan's identity and digest.

Completeness rule: every registry family row is either disposed
(render / reuse_from_base / preserve_unowned / input_only / external_handoff /
inapplicable) or carried as an explicit typed ``PlanGap``. Omission is never
an implicit decision. Conditions that graph-v2 semantics cannot decide
(auth, custom routes, refinement harness, managed capabilities, staging,
extensions) and renderer resolution are typed gaps deferred to the
implementation-binding slice — the plan never invents a semantic fact.

This module is deterministic substrate: no filesystem, no network, no AG2
imports, no model calls, no runtime or capability authority. The active
agent-produced ``AppBuildPlan`` remains the sole operational plan until the
Slice 5 cutover; nothing in production imports this module.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from enum import StrEnum
from typing import Any, Literal

from pydantic import Field, field_validator, model_validator

from mozaiksai.core.semantics.canonical import canonical_digest
from mozaiksai.core.semantics.graph import SemanticGraphV2, SemanticNodeKind
from mozaiksai.core.semantics.payloads import (
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

COMPILATION_PLAN_SCHEMA_VERSION: Literal["mozaiks.compilation_plan.v1"] = (
    "mozaiks.compilation_plan.v1"
)

_PLACEHOLDER_RE = re.compile(r"\{([a-z][a-z0-9_]*)\}")
_UNIT_SEGMENT_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]*$")


class CompilationPlanError(ValueError):
    """The plan inputs or document violate the Slice 4B contract."""


class PlanDisposition(StrEnum):
    """Complete disposition vocabulary from the ADR aggregate-authority rule."""

    RENDER = "render"
    REUSE_FROM_BASE = "reuse_from_base"
    PRESERVE_UNOWNED = "preserve_unowned"
    INPUT_ONLY = "input_only"
    EXTERNAL_HANDOFF = "external_handoff"
    INAPPLICABLE = "inapplicable"


class PlanGap(SemanticsModel):
    """One explicit deferred decision — never silent omission."""

    family_kind: str
    path_template: str
    reason: str
    adr_slice: int = Field(ge=4, le=7, strict=True)

    @field_validator("family_kind", "path_template", "reason")
    @classmethod
    def _text(cls, value: str) -> str:
        text = str(value or "").strip()
        if not text:
            raise ValueError("plan gap fields must be non-empty")
        return text


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


class PlanOutput(SemanticsModel):
    """One planned output path inside its registry path scope."""

    path_scope: str
    path: str

    @field_validator("path_scope")
    @classmethod
    def _scope(cls, value: str) -> str:
        text = str(value or "").strip()
        if not text:
            raise ValueError("path_scope must be non-empty")
        return text

    @field_validator("path")
    @classmethod
    def _path(cls, value: str) -> str:
        return validate_portable_path(value).text


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
    placeholder_values: tuple[tuple[str, str], ...] = Field(default_factory=tuple)
    outputs: tuple[PlanOutput, ...] = Field(default_factory=tuple)
    sources: tuple[PlanSource, ...] = Field(default_factory=tuple)
    depends_on_units: tuple[str, ...] = Field(default_factory=tuple)
    materializer: str
    base_plan_digest: str | None = None

    @field_validator("unit_id")
    @classmethod
    def _unit_id(cls, value: str) -> str:
        text = str(value or "").strip()
        if not all(_UNIT_SEGMENT_RE.fullmatch(part) for part in text.split("/") if True):
            raise ValueError(f"unit_id must be a normalized identifier path, got {value!r}")
        return text

    @field_validator("family_kind", "materializer")
    @classmethod
    def _identifier(cls, value: str) -> str:
        text = str(value or "").strip()
        if not text:
            raise ValueError("identifier fields must be non-empty")
        return text

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
        ordered = tuple(sorted(value))
        names = [name for name, _v in ordered]
        if len(names) != len(set(names)):
            raise ValueError("duplicate placeholder names in one unit")
        return ordered

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

    @field_validator("depends_on_units")
    @classmethod
    def _deps(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        ordered = tuple(sorted(str(item).strip() for item in value))
        if len(ordered) != len(set(ordered)):
            raise ValueError("duplicate unit dependencies")
        return ordered

    @model_validator(mode="after")
    def _reuse_shape(self) -> FamilyInstancePlan:
        if (self.disposition is PlanDisposition.REUSE_FROM_BASE) != (
            self.base_plan_digest is not None
        ):
            raise ValueError(
                "base_plan_digest is required exactly when disposition is reuse_from_base"
            )
        return self

    @property
    def identity_payload(self) -> dict[str, Any]:
        return {
            "unit_id": self.unit_id,
            "family_kind": self.family_kind,
            "family_identity_digest": self.family_identity_digest,
            "disposition": self.disposition.value,
            "placeholder_values": [list(pair) for pair in self.placeholder_values],
            "outputs": [
                {"path_scope": output.path_scope, "path": output.path}
                for output in self.outputs
            ],
            "sources": [
                {"node_id": source.node_id, "payload_digest": source.payload_digest}
                for source in self.sources
            ],
            "depends_on_units": list(self.depends_on_units),
            "materializer": self.materializer,
            "base_plan_digest": self.base_plan_digest,
        }


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

    @field_validator("graph_digest")
    @classmethod
    def _graph_digest(cls, value: str) -> str:
        return _validate_digest(value, field_name="graph_digest")

    @field_validator("plan_digest")
    @classmethod
    def _plan_digest_field(cls, value: str) -> str:
        return _validate_digest(value, field_name="plan_digest")

    @field_validator("registry_schema_version", "registry_digest", "graph_id")
    @classmethod
    def _nonempty(cls, value: str) -> str:
        text = str(value or "").strip()
        if not text:
            raise ValueError("plan identity fields must be non-empty")
        return text

    @field_validator("gaps")
    @classmethod
    def _gaps(cls, value: tuple[PlanGap, ...]) -> tuple[PlanGap, ...]:
        return tuple(
            sorted(value, key=lambda gap: (gap.family_kind, gap.path_template, gap.reason))
        )

    @model_validator(mode="after")
    def _validate_plan(self) -> CompilationPlan:
        unit_ids = [unit.unit_id for unit in self.units]
        if len(unit_ids) != len(set(unit_ids)):
            raise ValueError("duplicate unit identities in one plan")
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
        # Iterative DFS cycle check: a forged document must not smuggle a
        # dependency cycle past the registry's own acyclicity guarantee.
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
                        raise ValueError(
                            f"dependency cycle through unit {dependency!r}"
                        )
                    if state.get(dependency, 0) == 0:
                        stack.append((current, position + 1))
                        stack.append((dependency, 0))
                        advanced = True
                        break
                if not advanced:
                    state[current] = 2
        # Global path ownership: one owner per path, and the whole output set
        # must be representable on every host (case-fold and file/dir-prefix
        # collisions fail closed), per path scope root.
        by_scope: dict[tuple[str, tuple[tuple[str, str], ...]], list[str]] = {}
        for unit in self.units:
            for output in unit.outputs:
                key = (output.path_scope, unit.placeholder_values)
                by_scope.setdefault(key, []).append(output.path)
        for scope_root, paths in sorted(by_scope.items()):
            if len(paths) != len(set(paths)):
                duplicates = sorted({p for p in paths if paths.count(p) > 1})
                raise ValueError(
                    f"duplicate output ownership in scope {scope_root!r}: {duplicates}"
                )
            try:
                detect_collisions(paths)
            except ValueError as exc:
                raise ValueError(f"output collision in scope {scope_root!r}: {exc}") from exc
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

#: Registry conditions decidable from graph-v2 semantics alone, mapped to the
#: node kinds whose presence makes the condition true (empty tuple = always).
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

#: Conditions that are implementation-binding or operator facts, not graph
#: semantics. Deciding them here would invent meaning; each becomes a typed
#: gap deferred to the binding slice.
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

#: Placeholder names that instantiate one unit per semantic node of a kind.
_INSTANCE_PLACEHOLDERS: dict[str, SemanticNodeKind] = {
    "module_id": SemanticNodeKind.MODULE,
    "page_id": SemanticNodeKind.PAGE,
    "workflow_id": SemanticNodeKind.WORKFLOW,
}

#: App-scoped single-instance families trace to the node kinds that carry the
#: relevant semantics; empty means the whole graph (pinned by graph_digest).
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


def _node_local_identity(node_id: str) -> str:
    """Deterministic path-safe unit identity from a node id.

    ``mozaiks.module.reports_ab12`` -> ``reports_ab12``; dots in the local part
    normalize to underscores so the value satisfies every path template
    placeholder. Collision safety comes from the global output collision
    check, never from this normalization.
    """
    parts = validate_node_id_grammar(node_id).split(".")
    local = "_".join(parts[2:]) if len(parts) > 2 else parts[-1]
    return local.replace(".", "_")


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
) -> CompilationPlan:
    """Derive the single aggregate plan for one immutable graph identity.

    ``registry`` is the sole ``AppLayoutRegistry`` (duck-typed here so the
    semantics contract layer does not import the runtime registry module; the
    registry pins itself into the plan through its schema version and
    self-verified digest, and each family row through its identity digest).
    """
    verified_graph, payload_by_node = _cold_validate_inputs(graph, payloads)

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

    ordered = list(registry.ordered_families())
    units: dict[str, FamilyInstancePlan] = {}
    gaps: list[PlanGap] = []
    unit_ids_by_kind: dict[str, list[str]] = {}
    unit_instance_index: dict[tuple[str, tuple[tuple[str, str], ...]], str] = {}

    def _gap(family: Any, reason: str, *, adr_slice: int = 4) -> None:
        gaps.append(
            PlanGap(
                family_kind=family.kind.value,
                path_template=family.path_template,
                reason=reason,
                adr_slice=adr_slice,
            )
        )

    for family in ordered:
        kind_value = family.kind.value
        requirement = family.requirement.value
        condition = family.condition.value
        family_digest = canonical_digest(family.identity_payload)
        placeholders = tuple(_PLACEHOLDER_RE.findall(family.path_template))

        if requirement == "prohibited":
            # Prohibited rows join the plan as explicit inapplicable units:
            # the disposition set stays complete and the prohibition is a
            # stated decision rather than a silent skip.
            unit_id = f"{kind_value}/prohibited/{family_digest[:12]}"
            units[unit_id] = FamilyInstancePlan(
                unit_id=unit_id,
                family_kind=kind_value,
                family_identity_digest=family_digest,
                disposition=PlanDisposition.INAPPLICABLE,
                materializer=family.materializer.value,
            )
            unit_ids_by_kind.setdefault(kind_value, []).append(unit_id)
            continue

        if condition in _BINDING_CONDITIONS:
            _gap(
                family,
                f"condition {condition!r} is an implementation-binding or operator "
                "fact that graph-v2 semantics cannot decide; disposition is "
                "deferred to the binding slice",
            )
            continue

        if family.path_scope.value == "generated_staging":
            _gap(
                family,
                "generated-staging rows are factory transport locations, not "
                "compiled application artifacts; they never join the plan",
            )
            continue

        trigger_kinds = _DERIVABLE_CONDITIONS.get(condition)
        if trigger_kinds is None:
            _gap(
                family,
                f"condition {condition!r} has no graph-v2 derivation rule; "
                "disposition is deferred rather than guessed",
            )
            continue

        condition_met = not trigger_kinds or any(
            nodes_by_kind.get(kind) for kind in trigger_kinds
        )
        instance_placeholder = next(
            (name for name in placeholders if name in _INSTANCE_PLACEHOLDERS), None
        )
        unknown_placeholders = [
            name
            for name in placeholders
            if name not in _INSTANCE_PLACEHOLDERS
        ]
        if unknown_placeholders:
            _gap(
                family,
                f"path placeholders {unknown_placeholders!r} are not derivable "
                "from graph-v2 node identities; instantiation is deferred",
            )
            continue

        disposition = (
            PlanDisposition.EXTERNAL_HANDOFF
            if family.path_scope.value == "deployment_derived"
            or family.owner.value == "download_renderer"
            else PlanDisposition.RENDER
        )

        if not condition_met:
            unit_id = f"{kind_value}/{family_digest[:12]}"
            unit = FamilyInstancePlan(
                unit_id=unit_id,
                family_kind=kind_value,
                family_identity_digest=family_digest,
                disposition=PlanDisposition.INAPPLICABLE,
                materializer=family.materializer.value,
            )
            units[unit_id] = unit
            unit_ids_by_kind.setdefault(kind_value, []).append(unit_id)
            continue

        if instance_placeholder is not None:
            node_kind = _INSTANCE_PLACEHOLDERS[instance_placeholder]
            for node in nodes_by_kind.get(node_kind, ()):
                local = _node_local_identity(node.node_id)
                values = ((instance_placeholder, local),)
                path = family.path_template
                for name, value in values:
                    path = path.replace("{" + name + "}", value)
                unit_id = f"{kind_value}/{local}/{family_digest[:12]}"
                unit = FamilyInstancePlan(
                    unit_id=unit_id,
                    family_kind=kind_value,
                    family_identity_digest=family_digest,
                    disposition=disposition,
                    placeholder_values=values,
                    outputs=(PlanOutput(path_scope=family.path_scope.value, path=path),),
                    sources=(
                        PlanSource(
                            node_id=node.node_id,
                            payload_digest=payload_by_node[node.node_id].payload_digest,
                        ),
                    ),
                    materializer=family.materializer.value,
                )
                units[unit_id] = unit
                unit_ids_by_kind.setdefault(kind_value, []).append(unit_id)
                unit_instance_index[(kind_value, values)] = unit_id
        else:
            unit_id = f"{kind_value}/{family_digest[:12]}"
            unit = FamilyInstancePlan(
                unit_id=unit_id,
                family_kind=kind_value,
                family_identity_digest=family_digest,
                disposition=disposition,
                outputs=(
                    PlanOutput(
                        path_scope=family.path_scope.value, path=family.path_template
                    ),
                ),
                sources=_sources_for(_FAMILY_SOURCE_KINDS.get(condition, ())),
                materializer=family.materializer.value,
            )
            units[unit_id] = unit
            unit_ids_by_kind.setdefault(kind_value, []).append(unit_id)
            unit_instance_index[(kind_value, ())] = unit_id

    # Renderer resolution stays a per-plan typed gap: the registry declares
    # materializer identifiers, but resolving renderer versions is
    # implementation-binding work owned by the next slice.
    gaps.append(
        PlanGap(
            family_kind="*",
            path_template="*",
            reason=(
                "renderer resolution and implementation-binding pinning are "
                "deferred; registry materializer identifiers are declarations, "
                "not resolved renderers"
            ),
            adr_slice=4,
        )
    )

    # Dependency edges from the registry: same-instance link when the
    # dependency family shares the instance placeholder, else every unit of
    # the dependency kind.
    dependency_map: dict[str, tuple[str, ...]] = {}
    family_by_kind: dict[str, list[Any]] = {}
    for family in ordered:
        family_by_kind.setdefault(family.kind.value, []).append(family)
    for unit in list(units.values()):
        dep_ids: set[str] = set()
        for family in family_by_kind.get(unit.family_kind, ()):
            if canonical_digest(family.identity_payload) != unit.family_identity_digest:
                continue
            for dependency_kind in family.dependency_families:
                dep_kind_value = dependency_kind.value
                same_instance = unit_instance_index.get(
                    (dep_kind_value, unit.placeholder_values)
                )
                if same_instance is not None:
                    dep_ids.add(same_instance)
                else:
                    dep_ids.update(unit_ids_by_kind.get(dep_kind_value, ()))
        dependency_map[unit.unit_id] = tuple(sorted(dep_ids))

    finished_units = tuple(
        unit.model_copy(update={"depends_on_units": dependency_map[unit.unit_id]})
        for unit in units.values()
    )
    # model_copy skips validators; rebuild through validation so nothing
    # forged or unnormalized can survive into the plan document.
    rebuilt_units = tuple(
        FamilyInstancePlan.model_validate(unit.model_dump(mode="json"))
        for unit in finished_units
    )
    ordered_kind_index = {
        family.kind.value: index for index, family in enumerate(ordered)
    }
    sorted_units = tuple(
        sorted(
            rebuilt_units,
            key=lambda unit: (ordered_kind_index.get(unit.family_kind, 1_000_000), unit.unit_id),
        )
    )
    sorted_gaps = tuple(
        sorted(set(gaps), key=lambda gap: (gap.family_kind, gap.path_template, gap.reason))
    )

    payload: dict[str, Any] = {
        "schema_version": COMPILATION_PLAN_SCHEMA_VERSION,
        "graph_id": verified_graph.graph_id,
        "graph_version": verified_graph.version,
        "scope": verified_graph.scope.model_dump(mode="json"),
        "graph_digest": verified_graph.graph_digest,
        "registry_schema_version": str(registry.schema_version),
        "registry_digest": str(registry.registry_digest),
        "units": [unit.identity_payload for unit in sorted_units],
        "gaps": [gap.model_dump(mode="json") for gap in sorted_gaps],
    }
    return CompilationPlan(
        graph_id=verified_graph.graph_id,
        graph_version=verified_graph.version,
        scope=verified_graph.scope,
        graph_digest=verified_graph.graph_digest,
        registry_schema_version=str(registry.schema_version),
        registry_digest=str(registry.registry_digest),
        units=sorted_units,
        gaps=sorted_gaps,
        plan_digest=canonical_digest(payload),
    )


# ---------------------------------------------------------------------------
# Refinement impact (pure comparison; mutates no production authority)
# ---------------------------------------------------------------------------


class RegenerationClosure(SemanticsModel):
    """Complete unit partition between a base plan and a successor plan.

    Every successor unit lands in exactly one of ``affected`` (a source
    identity changed — must render), ``reusable`` (identical sources — may
    reuse from base, pinned to the base plan digest), or ``added``; every
    base-only unit lands in ``removed``. Nothing is omitted, so omission can
    never masquerade as preservation.
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

    def _signature(unit: FamilyInstancePlan) -> Any:
        return (
            unit.family_identity_digest,
            tuple((s.node_id, s.payload_digest) for s in unit.sources),
            tuple((o.path_scope, o.path) for o in unit.outputs),
            unit.disposition.value,
        )

    affected: list[str] = []
    reusable: list[str] = []
    added: list[str] = []
    for unit_id, unit in successor_units.items():
        if unit_id not in base_units:
            added.append(unit_id)
        elif _signature(unit) != _signature(base_units[unit_id]):
            affected.append(unit_id)
        else:
            reusable.append(unit_id)
    removed = [unit_id for unit_id in base_units if unit_id not in successor_units]

    closure = RegenerationClosure(
        base_plan_digest=base.plan_digest,
        successor_plan_digest=successor.plan_digest,
        affected=tuple(sorted(affected)),
        reusable=tuple(sorted(reusable)),
        added=tuple(sorted(added)),
        removed=tuple(sorted(removed)),
    )
    partition = set(closure.affected) | set(closure.reusable) | set(closure.added)
    if partition != set(successor_units) or (
        set(closure.removed) != set(base_units) - set(successor_units)
    ):
        raise CompilationPlanError("regeneration closure failed to partition all units")
    return closure


__all__ = [
    "COMPILATION_PLAN_SCHEMA_VERSION",
    "CompilationPlan",
    "CompilationPlanError",
    "FamilyInstancePlan",
    "PlanDisposition",
    "PlanGap",
    "PlanOutput",
    "PlanSource",
    "RegenerationClosure",
    "derive_compilation_plan",
    "plan_regeneration_closure",
]
