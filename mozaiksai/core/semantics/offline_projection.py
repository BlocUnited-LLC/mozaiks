"""ADR 0007 Slice 3E deterministic, offline-only source projection.

Production generators, runtimes, hosts, workflows, Studio, and control-plane
code must not import this module. It accepts current contract shapes, projects
graph-v2 identity plus typed payload content, and reports every other source
fact as typed coverage. Content is never invented: a payload field is set only
when the source carries that fact, and facts that edges or taxonomy already
own are never duplicated into payloads.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from enum import StrEnum
from types import NoneType
from typing import Any, Literal, get_args

import yaml
from pydantic import Field

from mozaiksai.core.runtime.app.page_schema import AppPageSection
from mozaiksai.core.semantics.canonical import canonical_digest
from mozaiksai.core.semantics.graph import (
    SemanticEdge,
    SemanticEdgeKind,
    SemanticGraphV2,
    SemanticNode,
    SemanticNodeKind,
    SemanticNodeV2,
    TaxonomyReference,
    build_semantic_graph_v2,
    validate_semantic_graph_v2_taxonomy_closure,
)
from mozaiksai.core.semantics.payloads import (
    PAYLOAD_MODEL_BY_KIND,
    BillingPeriod,
    PageSectionEntry,
    PriceSpec,
    SemanticPayloadBase,
    TriggerKind,
    build_semantic_payload,
    semantic_payload_ref,
    validate_semantic_graph_v2_payload_closure,
)
from mozaiksai.core.semantics.refs import ExecutionAccessScopeRef, SemanticsModel
from mozaiksai.core.taxonomy import (
    SemanticCategory,
    TaxonomyRegistry,
    validate_identifier_grammar,
)

PROJECTION_SCHEMA_VERSION: Literal["mozaiks.semantic_projection.v2"] = (
    "mozaiks.semantic_projection.v2"
)


class ProjectionGapKind(StrEnum):
    MISSING = "missing"
    CONTRADICTORY = "contradictory"
    UNSUPPORTED = "unsupported"
    AMBIGUOUS = "ambiguous"


class ProjectionDisposition(StrEnum):
    PROJECTED = "projected"
    DELIBERATELY_NON_SEMANTIC = "deliberately_non_semantic"
    DEFERRED = "deferred"


class ProjectionGap(SemanticsModel):
    kind: ProjectionGapKind
    source_path: str
    reason: str
    adr_slice: int | None = Field(default=None, ge=4, le=7)


class ProjectionCoverage(SemanticsModel):
    source_path: str
    source_file: str
    source_symbol: str
    current_authority: str
    disposition: ProjectionDisposition
    target_node_kind: SemanticNodeKind | None = None
    target_edge_kind: SemanticEdgeKind | None = None
    taxonomy_category: SemanticCategory | None = None
    stable_identity_derivation: str
    scope_source: str
    fully_representable: bool
    absence_valid: bool
    failure_policy: str
    adr_slice: int | None = Field(default=None, ge=4, le=7)
    reason: str


class SemanticFactSet(SemanticsModel):
    """Node facts carry (node_id, kind, taxonomy refs, payload content digest)."""

    nodes: tuple[tuple[str, str, tuple[tuple[str, str], ...], str], ...]
    edges: tuple[tuple[str, str, str, str | None], ...]


class ProjectionResult(SemanticsModel):
    schema_version: Literal["mozaiks.semantic_projection.v2"] = PROJECTION_SCHEMA_VERSION
    source_digest: str
    graph: SemanticGraphV2
    payloads: tuple[SemanticPayloadBase, ...]
    source_facts: SemanticFactSet
    represented_facts: SemanticFactSet
    gaps: tuple[ProjectionGap, ...]
    coverage: tuple[ProjectionCoverage, ...]


class ProjectionError(ValueError):
    """Fail-closed projection error retaining deterministic typed gaps."""

    def __init__(self, gaps: Iterable[ProjectionGap]):
        self.gaps = tuple(
            sorted(gaps, key=lambda gap: (gap.source_path, gap.kind.value, gap.reason))
        )
        super().__init__("; ".join(f"{gap.source_path}: {gap.reason}" for gap in self.gaps))


_SOURCE_AUTHORITIES: dict[str, tuple[str, str, str]] = {
    "app_build_plan": (
        "factory_app/workflows/AppGenerator/structured_outputs.yaml",
        "AppBuildPlan",
        "AppGenerator operational plan",
    ),
    "app_schema": (
        "factory_app/workflows/AppGenerator/structured_outputs.yaml",
        "AppSchemaOutput",
        "AppGenerator schema output",
    ),
    "design_docs": (
        "factory_app/workflows/DesignDocs/structured_outputs.yaml",
        "DesignDocsBundle",
        "DesignDocs output",
    ),
    "subscription_contract": (
        "factory_app/workflows/SubscriptionContractDesigner/structured_outputs.yaml",
        "SubscriptionContractOutput",
        "subscription design output",
    ),
    "modules": (
        "mozaiksai/core/runtime/app/module_loader.py",
        "ModuleLoader",
        "runtime module contracts",
    ),
    "pages": (
        "mozaiksai/core/runtime/app/page_schema.py",
        "AppPageSchema",
        "runtime page contract",
    ),
    "route_manifest": (
        "mozaiksai/core/runtime/app/loader.py",
        "AppLoader",
        "runtime route manifest",
    ),
    "subscriptions": (
        "mozaiksai/core/runtime/app/subscriptions_loader.py",
        "SubscriptionsConfig",
        "runtime subscription contract",
    ),
    "agent_workflows": (
        "factory_app/workflows/AgentGenerator/structured_outputs.yaml",
        "WorkflowBundleBuilderOutput",
        "AgentGenerator bundle output",
    ),
    "app_context": (
        "mozaiksai/core/app_context/models.py",
        "AppContextVersion",
        "observed ownership evidence",
    ),
    "ownership_evidence": (
        "mozaiksai/core/app_context/models.py",
        "AppContextVersion.ownership_boundaries",
        "observed ownership evidence",
    ),
    "build_context": (
        "mozaiksai/core/session/build_context_schema.py",
        "validate_pack_context",
        "declared build-context provenance",
    ),
    "workflows": (
        "tests/fixtures/appplan_persistent_projects_output.json",
        "recorded fixture envelope",
        "recorded AppBuildPlan execution metadata; not an AppBuildPlan field",
    ),
    "source_scopes": (
        "mozaiksai/core/semantics/offline_projection.py",
        "project_semantic_graph",
        "offline composition envelope",
    ),
}
_ROOT_ALIASES = {
    "AppBuildPlan": "app_build_plan",
    "AppSchemaOutput": "app_schema",
    "DesignDocsBundle": "design_docs",
    "SubscriptionContractOutput": "subscription_contract",
}
_SUPPORTED_ROOTS = frozenset(_SOURCE_AUTHORITIES) | frozenset(_ROOT_ALIASES)
_NON_SEMANTIC = frozenset({"agent_message"})
_KNOWN_DEFERRED = frozenset(
    {
        "acceptance_criteria",
        "access",
        "action_id",
        "active",
        "add_on_id",
        "agent_backend_required",
        "allowed_operations",
        "amount",
        "amount_cents",
        "app_id",
        "app_kind",
        "app_name",
        "artifact_version_id",
        "assets",
        "auth_strategy",
        "billing_mode",
        "brand_direction",
        "brand_intent",
        "capability_groups",
        "capability_pack_id",
        "cadence",
        "collection",
        "component",
        "config",
        "config_hint",
        "content",
        "context_id",
        "context_variables",
        "contract_required",
        "currency",
        "dependencies",
        "description",
        "display",
        "emits",
        "endpoint",
        "entities",
        "fields",
        "filename",
        "frontend_markdown",
        "frontend_scope",
        "generation_order",
        "handler",
        "handler_method",
        "href",
        "initial_agent",
        "initial_message",
        "indexes",
        "intent",
        "kind",
        "label",
        "layout",
        "lifecycle",
        "max_turns",
        "metering_declarations",
        "method",
        "mode",
        "module_contract_updates",
        "monthly_limit",
        "navigation_model",
        "notes",
        "order",
        "orchestration_pattern",
        "owner",
        "owner_module",
        "ownership",
        "ownership_boundaries",
        "page_surface_requirements",
        "pages",
        "path",
        "path_or_artifact",
        "pattern_id",
        "pattern_name",
        "placement",
        "plan_design_rationale",
        "policies",
        "price",
        "pricing_catalog",
        "primitive",
        "profile_layout",
        "projections",
        "purpose",
        "rationale",
        "readiness_profile",
        "ref_schema_version",
        "required",
        "revenue_model",
        "roles",
        "route",
        "schema_version",
        "scope",
        "search_by",
        "section_id_hint",
        "sections",
        "service_scope",
        "shell_preset_hint",
        "source",
        "source_capability_packs",
        "source_ref",
        "subscriber_intents",
        "summary",
        "surface_kind",
        "target",
        "target_kind",
        "theme_config_patch",
        "theme_preferences",
        "title",
        "token_amount",
        "trigger",
        "triggers",
        "type",
        "unit",
        "value",
        "value_type",
        "version",
        "workflow_capability_ids",
        "workflow_contract_updates",
        "workflow_startup_mode",
        "workflow_triggers",
        "write_mode",
        "appearance_hint",
        "brand_keywords",
        "depends_on",
        "entity_name",
        "execution_target",
        "experience_goals",
        "field",
        "implementation_mode",
        "keys",
        "migration_id",
        "module_id",
        "monetization_provider",
        "name",
        "operations",
        "owned_paths",
        "pack_type",
        "page_type_hint",
        "primary_actions",
        "primary_entities",
        "primary_pages",
        "sections_hint",
        "style_summary",
        "surface_id",
        "task_id",
        "task_type",
        "title_hint",
        "ui_layout",
    }
)
_PROVENANCE_ROOTS = frozenset({"build_context", "workflows"})
_SURFACE_KINDS = frozenset(
    {
        "module",
        "workflow",
        "app_policy",
        "refinement",
        "external_integration",
        "ui_only",
    }
)
_GRAPH_V1_SURFACE_KINDS = frozenset({"module", "workflow"})
_SLUG = re.compile(r"[^a-z0-9_]+")
_MODULE_ENDPOINT = re.compile(r"^/api/modules/([^/]+)/([^/]+)$")
# Mirrors AppPageSchema's _API_PATH_RE (mozaiksai/core/runtime/app/page_schema.py).
_PAGE_API_PATH = re.compile(r"^/api/[A-Za-z0-9_./-]+$")
_PATH_TOKEN = re.compile(r"([^.\[\]]+)|\[(\d+)\]")


def _plain(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return _plain(value.model_dump(mode="json"))
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_plain(item) for item in value]
    return value


def _slug(value: Any) -> str:
    text = _SLUG.sub("_", str(value or "").strip().lower()).strip("_")
    if not text:
        raise ProjectionError(
            [
                ProjectionGap(
                    kind=ProjectionGapKind.MISSING,
                    source_path="identity",
                    reason="stable identity is empty",
                )
            ]
        )
    return text


def _node_id(kind: SemanticNodeKind, identity: Any) -> str:
    text = str(identity or "").strip()
    return f"mozaiks.{kind.value}.{_slug(text)}_{canonical_digest(text)[:12]}"


def _as_list(value: Any) -> list[Any]:
    return list(value) if isinstance(value, list) else []


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _iter_leaves(value: Any, path: str) -> Iterable[tuple[str, Any]]:
    if isinstance(value, Mapping):
        if not value:
            yield path, value
        for key in sorted(value):
            yield from _iter_leaves(value[key], f"{path}.{key}" if path else str(key))
    elif isinstance(value, list):
        if not value:
            yield path, value
        for index, item in enumerate(value):
            yield from _iter_leaves(item, f"{path}[{index}]")
    else:
        yield path, value


def _source_metadata(path: str) -> tuple[str, str, str]:
    root = path.split(".", 1)[0].split("[", 1)[0]
    return _SOURCE_AUTHORITIES[_ROOT_ALIASES.get(root, root)]


def _value_at_path(source: Any, path: str) -> Any:
    value = source
    for name, index in _PATH_TOKEN.findall(path):
        value = value[int(index)] if index else value[name]
    return value


def _canonicalize_unordered(source: dict[str, Any]) -> None:
    """Normalize collections whose current contracts define identity, not order."""

    for root in ("app_build_plan", "AppBuildPlan", "design_docs", "DesignDocsBundle"):
        value = _mapping(source.get(root))
        surfaces = _mapping(value.get("surface_map")).get("surfaces")
        if isinstance(surfaces, list):
            surfaces.sort(key=lambda item: str(_mapping(item).get("surface_id") or ""))
    modules = source.get("modules")
    if not isinstance(modules, list):
        return
    modules.sort(
        key=lambda raw: str(
            _mapping(
                _mapping(raw).get("manifest")
                or _mapping(raw).get("module_manifest")
                or _mapping(raw)
            )
            .get("module", {})
            .get("id", "")
        )
    )
    for raw in modules:
        bundle = _mapping(raw)
        manifest = _mapping(bundle.get("manifest") or bundle.get("module_manifest") or bundle)
        for field, key in (
            ("actions", "id"),
            ("capabilities", "capability_id"),
            ("permissions", "id"),
        ):
            values = manifest.get(field)
            if isinstance(values, list):
                values.sort(key=lambda item: str(_mapping(item).get(key) or ""))


class _Builder:
    def __init__(
        self, source: dict[str, Any], scope: ExecutionAccessScopeRef, registry: TaxonomyRegistry
    ):
        self.source = source
        self.scope = scope
        self.registry = registry
        self.nodes: dict[str, SemanticNode] = {}
        self.edges: dict[tuple[str, str, str, str | None], SemanticEdge] = {}
        self.node_groups: set[tuple[str, str]] = set()
        self.edge_groups: set[tuple[str, tuple[str, str, str, str | None]]] = set()
        self.pending: list[tuple[SemanticEdgeKind, str, str, str, str | None, str]] = []
        self.projected: dict[
            str,
            tuple[SemanticNodeKind | None, SemanticEdgeKind | None, SemanticCategory | None, str],
        ] = {}
        self.gaps: list[ProjectionGap] = []
        self.observations: dict[tuple[str, str, str], str] = {}
        self.content: dict[str, dict[str, Any]] = {}

    def mark(
        self,
        path: str,
        *,
        node: SemanticNodeKind | None = None,
        edge: SemanticEdgeKind | None = None,
        taxonomy: SemanticCategory | None = None,
        identity: str,
    ) -> None:
        prior = self.projected.get(path)
        if prior is None:
            self.projected[path] = (node, edge, taxonomy, identity)
        else:
            self.projected[path] = (
                prior[0] or node,
                prior[1] or edge,
                prior[2] or taxonomy,
                prior[3] if identity in prior[3] else f"{prior[3]}; {identity}",
            )

    def gap(
        self, kind: ProjectionGapKind, path: str, reason: str, *, adr_slice: int | None = None
    ) -> None:
        self.gaps.append(
            ProjectionGap(kind=kind, source_path=path, reason=reason, adr_slice=adr_slice)
        )

    def content_field(self, node_id: str, field: str, value: Any, path: str) -> None:
        """Record one typed payload content fact for a node, never inventing.

        ``None`` records nothing (absent source content stays absent). The same
        field stated twice with different values is a contradiction, not a
        merge.
        """
        if value is None:
            return
        existing = self.content.setdefault(node_id, {})
        if field in existing and existing[field] != value:
            raise ProjectionError(
                [
                    ProjectionGap(
                        kind=ProjectionGapKind.CONTRADICTORY,
                        source_path=path,
                        reason=f"conflicting payload content {field!r} for {node_id!r}",
                    )
                ]
            )
        existing[field] = value
        self.mark(path, identity="typed semantic payload content field")

    def build_payloads(self, version: int) -> dict[str, SemanticPayloadBase]:
        """Build the typed payload for every projected node.

        Every node gets exactly one payload of its kind; content fields hold
        only what the source stated. Content that fails the payload contract is
        a projection failure, not a silent drop.
        """
        payloads: dict[str, SemanticPayloadBase] = {}
        for node_id, node in self.nodes.items():
            model = PAYLOAD_MODEL_BY_KIND[node.kind]
            fields = {
                field_name: None
                for field_name, field in model.model_fields.items()
                if field.is_required() and NoneType in get_args(field.annotation)
            }
            fields.update(self.content.get(node_id, {}))
            try:
                payloads[node_id] = build_semantic_payload(
                    model,
                    node_id=node_id,
                    payload_version=version,
                    scope=self.scope,
                    **fields,
                )
            except (TypeError, ValueError) as exc:
                raise ProjectionError(
                    [
                        ProjectionGap(
                            kind=ProjectionGapKind.AMBIGUOUS,
                            source_path="$",
                            reason=(
                                f"payload content for node {node_id!r} failed the "
                                f"{model.__name__} contract: {exc}"
                            ),
                        )
                    ]
                ) from exc
        return payloads

    def observe(self, concept: str, identity: Any, field: str, value: Any, path: str) -> None:
        if value is None:
            return
        key = (concept, str(identity), field)
        digest = canonical_digest(value)
        prior = self.observations.get(key)
        if prior is not None and prior != digest:
            raise ProjectionError(
                [
                    ProjectionGap(
                        kind=ProjectionGapKind.CONTRADICTORY,
                        source_path=path,
                        reason=f"conflicting {concept} {field} facts for {identity!r}",
                    )
                ]
            )
        self.observations[key] = digest

    def node(
        self,
        kind: SemanticNodeKind,
        identity: Any,
        *,
        path: str,
        group: str,
        taxonomy: tuple[SemanticCategory, str] | None = None,
        renderer_identity: Any | None = None,
    ) -> str:
        node_id = _node_id(kind, identity)
        if (group, node_id) in self.node_groups:
            raise ProjectionError(
                [
                    ProjectionGap(
                        kind=ProjectionGapKind.CONTRADICTORY,
                        source_path=path,
                        reason=f"duplicate semantic identity {node_id!r} in {group}",
                    )
                ]
            )
        self.node_groups.add((group, node_id))
        refs: tuple[TaxonomyReference, ...] = ()
        if taxonomy:
            category, identifier = taxonomy
            try:
                validate_identifier_grammar(category, identifier)
            except Exception as exc:
                raise ProjectionError(
                    [
                        ProjectionGap(
                            kind=ProjectionGapKind.UNSUPPORTED,
                            source_path=path,
                            reason=f"invalid {category.value} taxonomy identifier {identifier!r}: {exc}",
                        )
                    ]
                ) from exc
            try:
                self.registry.resolve(category, identifier)
            except Exception as exc:
                raise ProjectionError(
                    [
                        ProjectionGap(
                            kind=ProjectionGapKind.MISSING,
                            source_path=path,
                            reason=(
                                f"valid {category.value} identifier {identifier!r} is absent "
                                "from the pinned taxonomy registry; a declared namespace entry is required"
                            ),
                        )
                    ]
                ) from exc
            refs = (TaxonomyReference(category=category, identifier=identifier),)
        candidate = SemanticNode(node_id=node_id, kind=kind, taxonomy_references=refs)
        if node_id in self.nodes and self.nodes[node_id] != candidate:
            raise ProjectionError(
                [
                    ProjectionGap(
                        kind=ProjectionGapKind.CONTRADICTORY,
                        source_path=path,
                        reason=f"conflicting facts reuse node identity {node_id!r}",
                    )
                ]
            )
        self.nodes[node_id] = candidate
        renderer_identity_fields = {
            SemanticNodeKind.PAGE: "page_id",
            SemanticNodeKind.SECTION: "section_id",
            SemanticNodeKind.MODULE: "module_id",
            SemanticNodeKind.WORKFLOW: "workflow_id",
        }
        renderer_identity_field = renderer_identity_fields.get(kind)
        if renderer_identity_field is not None:
            source_identity = (
                _slug(identity)
                if renderer_identity is None
                else str(renderer_identity).strip()
            )
            self.content_field(node_id, renderer_identity_field, source_identity, path)
        self.mark(
            path,
            node=kind,
            taxonomy=taxonomy[0] if taxonomy else None,
            identity="node kind + canonical source identifier",
        )
        return node_id

    def edge(
        self,
        kind: SemanticEdgeKind,
        source: str,
        target: str,
        *,
        path: str,
        group: str,
        discriminator: str | None = None,
    ) -> None:
        self.pending.append((kind, source, target, path, discriminator, group))
        self.mark(path, edge=kind, identity="edge kind + stable declared endpoints + discriminator")

    def resolve_edges(self) -> None:
        for kind, source, target, path, discriminator, group in self.pending:
            missing = [node_id for node_id in (source, target) if node_id not in self.nodes]
            if missing:
                raise ProjectionError(
                    [
                        ProjectionGap(
                            kind=ProjectionGapKind.MISSING,
                            source_path=path,
                            reason=f"reference does not resolve to a declared semantic node: {missing}",
                        )
                    ]
                )
            key = (kind.value, source, target, discriminator)
            if (group, key) in self.edge_groups:
                raise ProjectionError(
                    [
                        ProjectionGap(
                            kind=ProjectionGapKind.CONTRADICTORY,
                            source_path=path,
                            reason=f"duplicate semantic relationship {key!r} in {group}",
                        )
                    ]
                )
            self.edge_groups.add((group, key))
            self.edges[key] = SemanticEdge(
                kind=kind, source_node_id=source, target_node_id=target, discriminator=discriminator
            )

    def project_plan(self, plan: dict[str, Any], root: str) -> None:
        surfaces = _as_list(_mapping(plan.get("surface_map")).get("surfaces"))
        for i, raw in enumerate(surfaces):
            item, base = _mapping(raw), f"{root}.surface_map.surfaces[{i}]"
            sid = item.get("surface_id")
            if not sid:
                raise ProjectionError(
                    [
                        ProjectionGap(
                            kind=ProjectionGapKind.MISSING,
                            source_path=f"{base}.surface_id",
                            reason="surface identity is required",
                        )
                    ]
                )
            surface = self.node(
                SemanticNodeKind.SURFACE,
                sid,
                path=f"{base}.surface_id",
                group=f"{root}.surface_map.surfaces",
            )
            kind = str(item.get("surface_kind") or "")
            if not kind:
                raise ProjectionError(
                    [
                        ProjectionGap(
                            kind=ProjectionGapKind.MISSING,
                            source_path=f"{base}.surface_kind",
                            reason="surface_kind is required by the current producer contract",
                        )
                    ]
                )
            if kind not in _SURFACE_KINDS:
                raise ProjectionError(
                    [
                        ProjectionGap(
                            kind=ProjectionGapKind.UNSUPPORTED,
                            source_path=f"{base}.surface_kind",
                            reason=(
                                f"surface_kind {kind!r} is not in the current producer "
                                f"taxonomy {sorted(_SURFACE_KINDS)!r}"
                            ),
                        )
                    ]
                )
            self.observe(
                "surface", sid, "surface_kind", item.get("surface_kind"), f"{base}.surface_kind"
            )
            self.observe("surface", sid, "owner", item.get("owner"), f"{base}.owner")
            owner: str | None = None
            if kind == "module":
                owner = self.node(
                    SemanticNodeKind.MODULE,
                    sid,
                    path=f"{base}.surface_kind",
                    group=f"{root}.surface_modules",
                )
            elif kind == "workflow":
                owner = self.node(
                    SemanticNodeKind.WORKFLOW,
                    sid,
                    path=f"{base}.surface_kind",
                    group=f"{root}.surface_workflows",
                )
            elif kind not in _GRAPH_V1_SURFACE_KINDS:
                self.gap(
                    ProjectionGapKind.UNSUPPORTED,
                    f"{base}.surface_kind",
                    (
                        f"surface_kind {kind!r} is current application semantics but no typed "
                        "payload field retains that realization classification"
                    ),
                    adr_slice=5,
                )
            if owner:
                self.edge(
                    SemanticEdgeKind.OWNS,
                    surface,
                    owner,
                    path=f"{base}.surface_kind",
                    group=f"{root}.surface_ownership",
                )
            for j, action_id in enumerate(_as_list(item.get("owned_mutations"))):
                if owner is None or kind != "module":
                    raise ProjectionError(
                        [
                            ProjectionGap(
                                kind=ProjectionGapKind.CONTRADICTORY,
                                source_path=f"{base}.owned_mutations[{j}]",
                                reason="only module surfaces can declare module actions",
                            )
                        ]
                    )
                action = self.node(
                    SemanticNodeKind.ACTION,
                    f"{sid}_{action_id}",
                    path=f"{base}.owned_mutations[{j}]",
                    group=f"{base}.owned_mutations",
                )
                self.edge(
                    SemanticEdgeKind.DECLARES,
                    owner,
                    action,
                    path=f"{base}.owned_mutations[{j}]",
                    group=f"{base}.owned_mutations",
                )
            for j, event_type in enumerate(_as_list(item.get("events_emitted"))):
                event = self.node(
                    SemanticNodeKind.EVENT,
                    event_type,
                    path=f"{base}.events_emitted[{j}]",
                    group=f"{base}.events_emitted",
                    taxonomy=(SemanticCategory.EVENT, str(event_type)),
                )
                self.edge(
                    SemanticEdgeKind.EMITS,
                    owner or surface,
                    event,
                    path=f"{base}.events_emitted[{j}]",
                    group=f"{base}.events_emitted",
                )
        for i, raw in enumerate(surfaces):
            item, base = _mapping(raw), f"{root}.surface_map.surfaces[{i}]"
            for j, dependency in enumerate(_as_list(item.get("dependencies"))):
                self.edge(
                    SemanticEdgeKind.DEPENDS_ON,
                    _node_id(SemanticNodeKind.SURFACE, item.get("surface_id")),
                    _node_id(SemanticNodeKind.SURFACE, dependency),
                    path=f"{base}.dependencies[{j}]",
                    group=f"{base}.dependencies",
                )
        pages = _as_list(plan.get("pages"))
        if len(pages) > 1:
            self.gap(
                ProjectionGapKind.UNSUPPORTED,
                f"{root}.pages",
                "page navigation ordering has no typed semantic payload field; "
                "navigation modeling remains deferred",
                adr_slice=5,
            )
        for i, raw in enumerate(pages):
            item = _mapping(raw)
            identity = item.get("name") or item.get("route")
            if not identity:
                raise ProjectionError(
                    [
                        ProjectionGap(
                            kind=ProjectionGapKind.MISSING,
                            source_path=f"{root}.pages[{i}]",
                            reason="page identity is required",
                        )
                    ]
                )
            path = f"{root}.pages[{i}].name" if item.get("name") else f"{root}.pages[{i}].route"
            self.observe("page", identity, "route", item.get("route"), f"{root}.pages[{i}].route")
            page = self.node(SemanticNodeKind.PAGE, identity, path=path, group=f"{root}.pages")
            self.content_field(page, "route", item.get("route"), f"{root}.pages[{i}].route")
            self.content_field(page, "title", item.get("title"), f"{root}.pages[{i}].title")
            self.content_field(page, "intent", item.get("purpose"), f"{root}.pages[{i}].purpose")
        for i, raw in enumerate(_as_list(plan.get("entities"))):
            item = _mapping(raw)
            if item.get("name"):
                self.node(
                    SemanticNodeKind.DATA_COLLECTION,
                    item["name"],
                    path=f"{root}.entities[{i}].name",
                    group=f"{root}.entities",
                )
        for i, raw in enumerate(_as_list(plan.get("event_flows"))):
            item = _mapping(raw)
            if item.get("event_type") and item.get("producer_pack_id"):
                event = self.node(
                    SemanticNodeKind.EVENT,
                    item["event_type"],
                    path=f"{root}.event_flows[{i}].event_type",
                    group=f"{root}.event_flows",
                    taxonomy=(SemanticCategory.EVENT, str(item["event_type"])),
                )
                self.edge(
                    SemanticEdgeKind.EMITS,
                    _node_id(SemanticNodeKind.MODULE, item["producer_pack_id"]),
                    event,
                    path=f"{root}.event_flows[{i}].producer_pack_id",
                    group=f"{root}.event_flows",
                )
        for i, raw in enumerate(_as_list(plan.get("workflow_touchpoints"))):
            item = _mapping(raw)
            if item.get("workflow_id") and item.get("page_name"):
                self.edge(
                    SemanticEdgeKind.BINDS,
                    _node_id(SemanticNodeKind.PAGE, item["page_name"]),
                    _node_id(SemanticNodeKind.WORKFLOW, item["workflow_id"]),
                    path=f"{root}.workflow_touchpoints[{i}].workflow_id",
                    group=f"{root}.workflow_touchpoints",
                )
        self.project_data_contract(_mapping(plan.get("data_contract")), f"{root}.data_contract")
        for i, raw in enumerate(_as_list(plan.get("deployment_targets"))):
            item = _mapping(raw)
            identity = item.get("target_id") or item.get("deployment_profile")
            if identity:
                path = (
                    f"{root}.deployment_targets[{i}].target_id"
                    if item.get("target_id")
                    else f"{root}.deployment_targets[{i}].deployment_profile"
                )
                target = self.node(
                    SemanticNodeKind.DEPLOYMENT_TARGET,
                    identity,
                    path=path,
                    group=f"{root}.deployment_targets",
                )
                profile = item.get("deployment_profile")
                if isinstance(profile, str) and re.fullmatch(r"[a-z][a-z0-9_]*", profile):
                    self.content_field(
                        target,
                        "profile_id",
                        profile,
                        f"{root}.deployment_targets[{i}].deployment_profile",
                    )

    def project_pages(self, pages: Any, root: str) -> None:
        for i, raw in enumerate(_as_list(pages)):
            item = _mapping(raw)
            identity = item.get("page_id") or item.get("name") or item.get("route")
            if not identity:
                raise ProjectionError(
                    [
                        ProjectionGap(
                            kind=ProjectionGapKind.MISSING,
                            source_path=f"{root}[{i}]",
                            reason="page identity is required",
                        )
                    ]
                )
            key = next(key for key in ("page_id", "name", "route") if item.get(key))
            self.observe("page", identity, "route", item.get("route"), f"{root}[{i}].route")
            page = self.node(SemanticNodeKind.PAGE, identity, path=f"{root}[{i}].{key}", group=root)
            self.content_field(page, "route", item.get("route"), f"{root}[{i}].route")
            self.content_field(page, "title", item.get("title"), f"{root}[{i}].title")
            self.content_field(page, "intent", item.get("description"), f"{root}[{i}].description")
            self.content_field(page, "page_type", item.get("page_type"), f"{root}[{i}].page_type")
            self.content_field(page, "layout", item.get("layout"), f"{root}[{i}].layout")
            self.content_field(
                page, "shell_mode", item.get("shell_mode"), f"{root}[{i}].shell_mode"
            )
            self.content_field(page, "roles", item.get("roles"), f"{root}[{i}].roles")
            self.content_field(
                page, "navigation", item.get("navigation"), f"{root}[{i}].navigation"
            )
            self.content_field(page, "meta", item.get("meta"), f"{root}[{i}].meta")
            sections = _as_list(item.get("sections"))
            if item.get("schema_version") == "mozaiks.app_page.v1" and not sections:
                raise ProjectionError(
                    [
                        ProjectionGap(
                            kind=ProjectionGapKind.MISSING,
                            source_path=f"{root}[{i}].sections",
                            reason="runtime AppPageSchema requires at least one section",
                        )
                    ]
                )
            section_entries: list[PageSectionEntry] = []
            for j, raw_section in enumerate(sections):
                section = _mapping(raw_section)
                section_id = section.get("id") or section.get("section_id")
                if section_id:
                    key = "id" if section.get("id") else "section_id"
                    child = self.node(
                        SemanticNodeKind.SECTION,
                        f"{identity}_{section_id}",
                        path=f"{root}[{i}].sections[{j}].{key}",
                        group=f"{root}[{i}].sections",
                        renderer_identity=section_id,
                    )
                    self.edge(
                        SemanticEdgeKind.RENDERS,
                        page,
                        child,
                        path=f"{root}[{i}].sections[{j}].{key}",
                        group=f"{root}[{i}].sections",
                    )
                    section_entries.append(
                        PageSectionEntry(position=len(section_entries), section_node_id=child)
                    )
                    self.content_field(
                        child,
                        "title",
                        section.get("title"),
                        f"{root}[{i}].sections[{j}].title",
                    )
                    self.content_field(
                        child,
                        "intent",
                        section.get("description"),
                        f"{root}[{i}].sections[{j}].description",
                    )
                    try:
                        declarative = AppPageSection.model_validate(section)
                    except ValueError as exc:
                        if item.get("schema_version") == "mozaiks.app_page.v1":
                            raise ProjectionError(
                                [
                                    ProjectionGap(
                                        kind=ProjectionGapKind.AMBIGUOUS,
                                        source_path=f"{root}[{i}].sections[{j}]",
                                        reason=(
                                            "declared runtime page section failed the "
                                            f"AppPageSection contract: {exc}"
                                        ),
                                    )
                                ]
                            ) from exc
                        declarative = None
                    if declarative is not None:
                        self.content_field(
                            child,
                            "declarative",
                            declarative,
                            f"{root}[{i}].sections[{j}]",
                        )
                self._page_bindings(section, page, f"{root}[{i}].sections[{j}]")
            if section_entries:
                # The declared source order is the semantic order: it survives
                # as explicit dense positions in the page payload.
                self.content_field(
                    page, "sections", tuple(section_entries), f"{root}[{i}].sections"
                )
            auth = _mapping(_mapping(item.get("meta")).get("routeAuth"))
            if auth:
                self._bind_action(
                    page, auth.get("module"), auth.get("action"), f"{root}[{i}].meta.routeAuth"
                )

    def _page_bindings(self, value: Any, page: str, path: str) -> None:
        if isinstance(value, Mapping):
            for key, child in value.items():
                child_path = f"{path}.{key}"
                if (
                    key in {"api_endpoint", "href"}
                    and isinstance(child, str)
                    and child.startswith("/api/")
                ):
                    match = _MODULE_ENDPOINT.fullmatch(child)
                    if match is not None:
                        self._bind_action(page, match.group(1), match.group(2), child_path)
                    elif _PAGE_API_PATH.fullmatch(child):
                        # AppPageSchema permits any /api/... path (page_schema.py
                        # _API_PATH_RE); only /api/modules/{module}/{action} names a
                        # declared module action. Other valid paths — the committed
                        # /api/notifications route, for one — are real bindings that
                        # no projected payload entry yet exists for, so they are typed gaps
                        # rather than a hard failure or an invented action target.
                        self.gap(
                            ProjectionGapKind.UNSUPPORTED,
                            child_path,
                            (
                                "valid non-module page API binding is not yet projected into "
                                "section payload entries; only /api/modules/{module}/{action} "
                                "names a declared action"
                            ),
                            adr_slice=5,
                        )
                    else:
                        raise ProjectionError(
                            [
                                ProjectionGap(
                                    kind=ProjectionGapKind.AMBIGUOUS,
                                    source_path=child_path,
                                    reason=(
                                        "API binding is not a valid AppPageSchema api path "
                                        "(^/api/[A-Za-z0-9_./-]+$)"
                                    ),
                                )
                            ]
                        )
                else:
                    self._page_bindings(child, page, child_path)
        elif isinstance(value, list):
            for index, child in enumerate(value):
                self._page_bindings(child, page, f"{path}[{index}]")

    def _bind_action(self, page: str, module_id: Any, action_id: Any, path: str) -> None:
        if not module_id or not action_id:
            raise ProjectionError(
                [
                    ProjectionGap(
                        kind=ProjectionGapKind.MISSING,
                        source_path=path,
                        reason="page binding requires module and action",
                    )
                ]
            )
        self.edge(
            SemanticEdgeKind.BINDS,
            page,
            _node_id(SemanticNodeKind.ACTION, f"{module_id}_{action_id}"),
            path=path,
            group=path,
        )

    def project_route_manifest(self, manifest: dict[str, Any], root: str) -> None:
        present_keys = [key for key in ("pages", "route_manifest") if key in manifest]
        if len(present_keys) > 1:
            raise ProjectionError(
                [
                    ProjectionGap(
                        kind=ProjectionGapKind.CONTRADICTORY,
                        source_path=root,
                        reason=(
                            "route manifest contains both runtime 'pages' and AppSchema "
                            "'route_manifest' entry collections"
                        ),
                    )
                ]
            )
        entries_key = present_keys[0] if present_keys else "pages"
        entries = manifest.get(entries_key, [])
        for i, raw in enumerate(_as_list(entries)):
            item = _mapping(raw)
            base = f"{root}.{entries_key}[{i}]"
            identity = item.get("id") or item.get("path")
            if not identity:
                raise ProjectionError(
                    [
                        ProjectionGap(
                            kind=ProjectionGapKind.MISSING,
                            source_path=base,
                            reason="route identity is required",
                        )
                    ]
                )
            key = "id" if item.get("id") else "path"
            page = self.node(
                SemanticNodeKind.PAGE,
                identity,
                path=f"{base}.{key}",
                group=f"{root}.{entries_key}",
            )
            auth = _mapping(_mapping(item.get("meta")).get("routeAuth"))
            if auth:
                self._bind_action(
                    page,
                    auth.get("module"),
                    auth.get("action"),
                    f"{base}.meta.routeAuth",
                )

    def project_data_contract(self, contract: dict[str, Any], root: str) -> None:
        for i, raw_surface in enumerate(_as_list(contract.get("surfaces"))):
            surface = _mapping(raw_surface)
            owner_id = surface.get("surface_id")
            owner = None
            if owner_id and surface.get("surface_kind") == "module":
                owner = self.node(
                    SemanticNodeKind.MODULE,
                    owner_id,
                    path=f"{root}.surfaces[{i}].surface_id",
                    group=f"{root}.surface_owners",
                )
            for j, raw_collection in enumerate(_as_list(surface.get("collections"))):
                item = _mapping(raw_collection)
                name = item.get("name") or item.get("entity_name")
                if name:
                    collection = self.node(
                        SemanticNodeKind.DATA_COLLECTION,
                        f"{owner_id}_{name}" if owner_id else name,
                        path=f"{root}.surfaces[{i}].collections[{j}].name",
                        group=f"{root}.surfaces[{i}].collections",
                    )
                    if owner:
                        self.edge(
                            SemanticEdgeKind.OWNS,
                            owner,
                            collection,
                            path=f"{root}.surfaces[{i}].collections[{j}].name",
                            group=f"{root}.surfaces[{i}].collections",
                        )
        for i, raw_alias in enumerate(_as_list(contract.get("aliases"))):
            item = _mapping(raw_alias)
            if item.get("alias"):
                alias = self.node(
                    SemanticNodeKind.DATA_ALIAS,
                    item["alias"],
                    path=f"{root}.aliases[{i}].alias",
                    group=f"{root}.aliases",
                )
                if item.get("collection"):
                    identity = (
                        f"{item.get('owner_module')}_{item['collection']}"
                        if item.get("owner_module")
                        else item["collection"]
                    )
                    self.edge(
                        SemanticEdgeKind.BINDS,
                        alias,
                        _node_id(SemanticNodeKind.DATA_COLLECTION, identity),
                        path=f"{root}.aliases[{i}].collection",
                        group=f"{root}.aliases",
                    )

    def project_modules(self, modules: Any, root: str) -> None:
        items = (
            [(f"{root}.{key}", modules[key]) for key in sorted(modules)]
            if isinstance(modules, Mapping)
            else [(f"{root}[{index}]", raw) for index, raw in enumerate(_as_list(modules))]
        )
        bundles: list[tuple[str, dict[str, Any], dict[str, Any], str]] = []
        for base, raw in items:
            bundle = _mapping(raw)
            manifest = _mapping(bundle.get("manifest") or bundle.get("module_manifest") or bundle)
            module_id = _mapping(manifest.get("module")).get("id") or manifest.get("module_id")
            if not module_id:
                raise ProjectionError(
                    [
                        ProjectionGap(
                            kind=ProjectionGapKind.MISSING,
                            source_path=base,
                            reason="module id is required",
                        )
                    ]
                )
            module = self.node(
                SemanticNodeKind.MODULE,
                module_id,
                path=f"{base}.manifest.module.id"
                if bundle.get("manifest")
                else f"{base}.module.id",
                group=root,
            )
            module_description = _mapping(manifest.get("module")).get("description")
            self.content_field(
                module,
                "description",
                module_description,
                f"{base}.manifest.module.description"
                if bundle.get("manifest")
                else f"{base}.module.description",
            )
            bundles.append((str(module_id), bundle, manifest, base))
            declarations = (
                ("actions", "id", SemanticNodeKind.ACTION, True),
                ("capabilities", "capability_id", SemanticNodeKind.CAPABILITY, False),
                ("permissions", "id", SemanticNodeKind.PERMISSION, True),
            )
            for field, key, kind, module_qualified in declarations:
                for j, raw_item in enumerate(_as_list(manifest.get(field))):
                    value = _mapping(raw_item).get(key)
                    if value:
                        taxonomy = (
                            (SemanticCategory.CAPABILITY, str(value))
                            if kind is SemanticNodeKind.CAPABILITY
                            else None
                        )
                        identity = f"{module_id}_{value}" if module_qualified else str(value)
                        child = self.node(
                            kind,
                            identity,
                            path=f"{base}.manifest.{field}[{j}].{key}",
                            group=f"{base}.{field}",
                            taxonomy=taxonomy,
                        )
                        self.content_field(
                            child,
                            "description",
                            _mapping(raw_item).get("description"),
                            f"{base}.manifest.{field}[{j}].description",
                        )
                        self.edge(
                            SemanticEdgeKind.DECLARES,
                            module,
                            child,
                            path=f"{base}.manifest.{field}[{j}].{key}",
                            group=f"{base}.{field}",
                        )
            companions = (
                ("events", "events", "type", SemanticNodeKind.EVENT),
                ("reactions", "reactions", "id", SemanticNodeKind.REACTION),
                ("notifications", "notifications", "id", SemanticNodeKind.NOTIFICATION),
            )
            for companion, list_key, id_key, kind in companions:
                for j, raw_item in enumerate(
                    _as_list(_mapping(bundle.get(companion)).get(list_key))
                ):
                    value = _mapping(raw_item).get(id_key)
                    if value:
                        identity = (
                            value if kind is SemanticNodeKind.EVENT else f"{module_id}_{value}"
                        )
                        taxonomy = (
                            (SemanticCategory.EVENT, str(value))
                            if kind is SemanticNodeKind.EVENT
                            else None
                        )
                        child = self.node(
                            kind,
                            identity,
                            path=f"{base}.{companion}.{list_key}[{j}].{id_key}",
                            group=f"{base}.{companion}",
                            taxonomy=taxonomy,
                        )
                        if "description" in PAYLOAD_MODEL_BY_KIND[kind].model_fields:
                            self.content_field(
                                child,
                                "description",
                                _mapping(raw_item).get("description"),
                                f"{base}.{companion}.{list_key}[{j}].description",
                            )
                        edge_kind = (
                            SemanticEdgeKind.EMITS
                            if kind is SemanticNodeKind.EVENT
                            else SemanticEdgeKind.DECLARES
                        )
                        self.edge(
                            edge_kind,
                            module,
                            child,
                            path=f"{base}.{companion}.{list_key}[{j}].{id_key}",
                            group=f"{base}.{companion}",
                        )
        for module_id, bundle, manifest, base in bundles:
            for j, raw_action in enumerate(_as_list(manifest.get("actions"))):
                item = _mapping(raw_action)
                action = _node_id(SemanticNodeKind.ACTION, f"{module_id}_{item.get('id')}")
                for k, event_type in enumerate(_as_list(item.get("emits"))):
                    self.edge(
                        SemanticEdgeKind.EMITS,
                        action,
                        _node_id(SemanticNodeKind.EVENT, event_type),
                        path=f"{base}.manifest.actions[{j}].emits[{k}]",
                        group=f"{base}.actions[{j}].emits",
                    )
                if item.get("entitlement_gate"):
                    self.edge(
                        SemanticEdgeKind.GATES,
                        _node_id(SemanticNodeKind.CAPABILITY, item["entitlement_gate"]),
                        action,
                        path=f"{base}.manifest.actions[{j}].entitlement_gate",
                        group=f"{base}.actions[{j}].gate",
                    )
            for companion, kind in (
                ("reactions", SemanticNodeKind.REACTION),
                ("notifications", SemanticNodeKind.NOTIFICATION),
            ):
                for j, raw_item in enumerate(
                    _as_list(_mapping(bundle.get(companion)).get(companion))
                ):
                    item = _mapping(raw_item)
                    event_type = item.get("event_type") or item.get("on")
                    if item.get("id") and event_type:
                        source = _node_id(kind, f"{module_id}_{item['id']}")
                        self.edge(
                            SemanticEdgeKind.CONSUMES,
                            source,
                            _node_id(SemanticNodeKind.EVENT, event_type),
                            path=f"{base}.{companion}.{companion}[{j}].event_type",
                            group=f"{base}.{companion}",
                        )

    def _price_spec(self, item: dict[str, Any], period: BillingPeriod | None) -> PriceSpec | None:
        """Map a source price mapping to a typed spec, never guessing.

        Only integer minor units, a resolvable ISO-4217 code, and an explicit
        billing period produce a spec; anything else stays deferred coverage.
        """
        price = _mapping(item.get("price"))
        amount = price.get("amount_cents")
        currency = str(price.get("currency") or "").strip().upper()
        if not isinstance(amount, int) or isinstance(amount, bool) or not currency or period is None:
            return None
        try:
            return PriceSpec(amount_minor_units=amount, currency=currency, period=period)
        except ValueError:
            return None

    _CADENCE_PERIODS = {
        "monthly": BillingPeriod.MONTHLY,
        "yearly": BillingPeriod.YEARLY,
        "one_time": BillingPeriod.ONE_TIME,
    }

    def project_subscriptions(self, config: dict[str, Any], root: str) -> None:
        plans = _as_list(config.get("plans"))
        if len(plans) > 1:
            self.gap(
                ProjectionGapKind.UNSUPPORTED,
                f"{root}.plans",
                "plan presentation ordering has no typed semantic payload field; "
                "catalog ordering remains deferred",
                adr_slice=5,
            )
        for i, raw_plan in enumerate(plans):
            item = _mapping(raw_plan)
            plan_id = item.get("plan_id")
            if not plan_id:
                raise ProjectionError(
                    [
                        ProjectionGap(
                            kind=ProjectionGapKind.MISSING,
                            source_path=f"{root}.plans[{i}].plan_id",
                            reason="subscription plan id is required",
                        )
                    ]
                )
            plan = self.node(
                SemanticNodeKind.PLAN,
                plan_id,
                path=f"{root}.plans[{i}].plan_id",
                group=f"{root}.plans",
            )
            self.content_field(plan, "title", item.get("label"), f"{root}.plans[{i}].label")
            for j, cap_id in enumerate(_as_list(item.get("capabilities"))):
                cap = self.node(
                    SemanticNodeKind.CAPABILITY,
                    cap_id,
                    path=f"{root}.plans[{i}].capabilities[{j}]",
                    group=f"{root}.plans[{i}].capabilities",
                    taxonomy=(SemanticCategory.CAPABILITY, str(cap_id)),
                )
                self.edge(
                    SemanticEdgeKind.GATES,
                    plan,
                    cap,
                    path=f"{root}.plans[{i}].capabilities[{j}]",
                    group=f"{root}.plans[{i}].capabilities",
                )
            for j, raw_limit in enumerate(_as_list(item.get("usage_limits"))):
                limit = _mapping(raw_limit)
                if limit.get("meter_id"):
                    meter = self.node(
                        SemanticNodeKind.METER,
                        limit["meter_id"],
                        path=f"{root}.plans[{i}].usage_limits[{j}].meter_id",
                        group=f"{root}.plans[{i}].usage_limits",
                    )
                    self.content_field(
                        meter,
                        "unit",
                        limit.get("unit"),
                        f"{root}.plans[{i}].usage_limits[{j}].unit",
                    )
                    limit_node = self.node(
                        SemanticNodeKind.LIMIT,
                        f"{plan_id}_{limit['meter_id']}",
                        path=f"{root}.plans[{i}].usage_limits[{j}].monthly_limit",
                        group=f"{root}.plans[{i}].usage_limits",
                    )
                    monthly_limit = limit.get("monthly_limit")
                    if isinstance(monthly_limit, int) and not isinstance(monthly_limit, bool):
                        limit_path = f"{root}.plans[{i}].usage_limits[{j}].monthly_limit"
                        self.content_field(limit_node, "limit_value", monthly_limit, limit_path)
                        # The source field is monthly by declaration, so the
                        # period is stated fact, not inference.
                        self.content_field(
                            limit_node, "period", BillingPeriod.MONTHLY, limit_path
                        )
                    self.edge(
                        SemanticEdgeKind.GATES,
                        plan,
                        limit_node,
                        path=f"{root}.plans[{i}].usage_limits[{j}].monthly_limit",
                        group=f"{root}.plans[{i}].usage_limits",
                    )
                    self.edge(
                        SemanticEdgeKind.BINDS,
                        limit_node,
                        meter,
                        path=f"{root}.plans[{i}].usage_limits[{j}].meter_id",
                        group=f"{root}.plans[{i}].usage_limits",
                    )
        for field, id_key in (
            ("top_up_products", "product_id"),
            ("add_on_products", "add_on_id"),
            ("products", "product_id"),
        ):
            for i, raw_product in enumerate(_as_list(config.get(field))):
                item = _mapping(raw_product)
                identity = item.get(id_key)
                if identity:
                    product = self.node(
                        SemanticNodeKind.PRODUCT,
                        identity,
                        path=f"{root}.{field}[{i}].{id_key}",
                        group=f"{root}.{field}",
                    )
                    self.content_field(
                        product, "title", item.get("label"), f"{root}.{field}[{i}].label"
                    )
                    period: BillingPeriod | None
                    if field == "top_up_products":
                        # A token top-up is a one-time cash purchase by
                        # contract definition (TokenTopUpPriceDef), so the
                        # period is stated by the source contract itself.
                        period = BillingPeriod.ONE_TIME
                    else:
                        cadence = str(
                            item.get("cadence") or item.get("billing_mode") or ""
                        ).strip()
                        period = self._CADENCE_PERIODS.get(cadence)
                    spec = self._price_spec(item, period)
                    if spec is not None:
                        self.content_field(
                            product, "prices", (spec,), f"{root}.{field}[{i}].price"
                        )

    def project_workflows(self, workflows: Any, root: str) -> None:
        items = (
            [(f"{root}.{key}", value) for key, value in sorted(workflows.items()) if key != "_meta"]
            if isinstance(workflows, Mapping)
            else [(f"{root}[{index}]", raw) for index, raw in enumerate(_as_list(workflows))]
        )
        for base, raw in items:
            item = _mapping(raw)
            name = item.get("workflow_name")
            if not name:
                raise ProjectionError(
                    [
                        ProjectionGap(
                            kind=ProjectionGapKind.MISSING,
                            source_path=f"{base}.workflow_name",
                            reason="WorkflowBundleBuilderOutput requires workflow_name",
                        )
                    ]
                )
            workflow = self.node(
                SemanticNodeKind.WORKFLOW, name, path=f"{base}.workflow_name", group=root
            )
            files = _as_list(item.get("files"))
            if len(files) > 1:
                self.gap(
                    ProjectionGapKind.UNSUPPORTED,
                    f"{base}.files",
                    "ordered renderer file list is a Slice 4 execution concern",
                    adr_slice=4,
                )
            orchestrators = [
                (j, _mapping(raw_file))
                for j, raw_file in enumerate(files)
                if _mapping(raw_file).get("filename") == "orchestrator.yaml"
            ]
            if len(orchestrators) != 1:
                raise ProjectionError(
                    [
                        ProjectionGap(
                            kind=ProjectionGapKind.MISSING,
                            source_path=f"{base}.files",
                            reason="WorkflowBundleBuilderOutput must contain exactly one orchestrator.yaml",
                        )
                    ]
                )
            file_index, file = orchestrators[0]
            path = f"{base}.files[{file_index}].content"
            try:
                orchestration = _mapping(yaml.safe_load(str(file.get("content") or "")))
            except yaml.YAMLError as exc:
                raise ProjectionError(
                    [
                        ProjectionGap(
                            kind=ProjectionGapKind.AMBIGUOUS,
                            source_path=path,
                            reason=f"orchestrator.yaml is invalid YAML: {exc}",
                        )
                    ]
                ) from exc
            if orchestration.get("workflow_name") != name:
                raise ProjectionError(
                    [
                        ProjectionGap(
                            kind=ProjectionGapKind.CONTRADICTORY,
                            source_path=path,
                            reason="bundle workflow_name disagrees with orchestrator.yaml",
                        )
                    ]
                )
            self.mark(
                path,
                node=SemanticNodeKind.WORKFLOW,
                identity="parsed current orchestrator.yaml identity and triggers",
            )
            self.gap(
                ProjectionGapKind.UNSUPPORTED,
                path,
                "orchestrator.yaml contains workflow behavior beyond graph identity, "
                "trigger relationships, and typed workflow payload content",
                adr_slice=5,
            )
            description = orchestration.get("description")
            if isinstance(description, str):
                self.content_field(workflow, "description", description, path)
            for raw_trigger in _as_list(orchestration.get("triggers")):
                trigger_item = _mapping(raw_trigger)
                identity = (
                    trigger_item.get("event")
                    or trigger_item.get("endpoint")
                    or f"{name}_{canonical_digest(trigger_item)[:12]}"
                )
                trigger = self.node(
                    SemanticNodeKind.TRIGGER,
                    identity,
                    path=path,
                    group=f"{base}.orchestrator.triggers",
                )
                # Event and capability trigger bindings are owned by CONSUMES/
                # GATES edges; duplicating them into payload fields would give
                # one fact two authorities. Endpoint bindings have no edge
                # representation, so a well-formed route path is payload
                # content.
                endpoint = trigger_item.get("endpoint")
                if (
                    not trigger_item.get("event")
                    and isinstance(endpoint, str)
                    and endpoint.startswith("/")
                    and " " not in endpoint
                ):
                    self.content_field(trigger, "trigger_kind", TriggerKind.ENDPOINT, path)
                    self.content_field(trigger, "endpoint_path", endpoint, path)
                self.edge(
                    SemanticEdgeKind.BINDS,
                    trigger,
                    workflow,
                    path=path,
                    group=f"{base}.orchestrator.triggers",
                )
                if trigger_item.get("event"):
                    self.edge(
                        SemanticEdgeKind.CONSUMES,
                        trigger,
                        _node_id(SemanticNodeKind.EVENT, trigger_item["event"]),
                        path=path,
                        group=f"{base}.orchestrator.triggers",
                    )
                if trigger_item.get("capability_id"):
                    self.edge(
                        SemanticEdgeKind.GATES,
                        _node_id(SemanticNodeKind.CAPABILITY, trigger_item["capability_id"]),
                        trigger,
                        path=path,
                        group=f"{base}.orchestrator.triggers",
                    )

    def project_provenance(self, value: Any, root: str) -> None:
        """Classify accepted provenance roots that carry no semantic-graph identity.

        ``build_context`` is declared pack provenance and ``workflows`` is
        recorded execution metadata on an AppBuildPlan envelope. Both are real
        parts of current recorded sources, so they are accepted rather than
        rejected as unknown roots — but neither declares application semantics
        that the semantic graph can hold, so every leaf becomes an explicit typed
        gap instead of an invented node.
        """
        authority = _SOURCE_AUTHORITIES[root][2]
        for leaf_path, _leaf in _iter_leaves(value, root):
            self.gap(
                ProjectionGapKind.UNSUPPORTED,
                leaf_path,
                f"{authority} is not semantic-graph application semantics",
                adr_slice=5,
            )

    def project_ownership(self, evidence: dict[str, Any], root: str) -> None:
        mode = str(evidence.get("mode") or "greenfield")
        boundaries = _as_list(evidence.get("ownership_boundaries"))
        if mode in {"brownfield", "hybrid"} and not boundaries:
            raise ProjectionError(
                [
                    ProjectionGap(
                        kind=ProjectionGapKind.MISSING,
                        source_path=f"{root}.ownership_boundaries",
                        reason=f"{mode} projection requires AppContextVersion ownership boundaries",
                    )
                ]
            )
        for i, raw in enumerate(boundaries):
            item = _mapping(raw)
            if not item.get("path_or_artifact") or not item.get("ownership"):
                raise ProjectionError(
                    [
                        ProjectionGap(
                            kind=ProjectionGapKind.MISSING,
                            source_path=f"{root}.ownership_boundaries[{i}]",
                            reason="ownership boundary requires path_or_artifact and ownership",
                        )
                    ]
                )

    def coverage(self) -> tuple[ProjectionCoverage, ...]:
        rows: list[ProjectionCoverage] = []
        gap_by_path = {
            gap.source_path: gap
            for gap in sorted(
                self.gaps, key=lambda item: (item.source_path, item.kind.value, item.reason)
            )
        }
        for path, value in _iter_leaves(self.source, ""):
            source_file, symbol, authority = _source_metadata(path)
            projected = self.projected.get(path)
            names = [name for name, _index in _PATH_TOKEN.findall(path) if name]
            leaf = names[-1] if names else ""
            gap = gap_by_path.get(path)
            if gap:
                node, edge, taxonomy, identity = projected or (
                    None,
                    None,
                    None,
                    "none; the graph and payload contracts cannot retain this complete fact",
                )
                disposition, fully, reason, adr_slice = (
                    ProjectionDisposition.DEFERRED,
                    False,
                    gap.reason,
                    gap.adr_slice,
                )
            elif projected:
                node, edge, taxonomy, identity = projected
                disposition, fully, reason, adr_slice = (
                    ProjectionDisposition.PROJECTED,
                    True,
                    "source-derived fact is represented by graph identity, taxonomy, or relationship",
                    None,
                )
            elif leaf in _NON_SEMANTIC:
                node = edge = taxonomy = None
                identity = "excluded status narration"
                disposition, fully, reason, adr_slice = (
                    ProjectionDisposition.DELIBERATELY_NON_SEMANTIC,
                    True,
                    "agent status narration is not application semantics",
                    None,
                )
            elif value in (None, [], {}):
                node = edge = taxonomy = None
                identity = "explicit absence contributes no positive semantic fact"
                disposition, fully, reason, adr_slice = (
                    ProjectionDisposition.DELIBERATELY_NON_SEMANTIC,
                    True,
                    "empty or null optional input is intentionally absent",
                    None,
                )
            else:
                node = edge = taxonomy = None
                identity = "none; no typed payload field is projected for this fact"
                gap_kind = (
                    ProjectionGapKind.UNSUPPORTED
                    if leaf in _KNOWN_DEFERRED
                    else ProjectionGapKind.AMBIGUOUS
                )
                reason = (
                    "source fact is not yet projected into typed payload content"
                    if gap_kind is ProjectionGapKind.UNSUPPORTED
                    else (
                        "field is not in this projection's classified set; it is reported "
                        "rather than projected, and carries no semantic-graph identity "
                        "until it is classified"
                    )
                )
                disposition, fully, adr_slice = ProjectionDisposition.DEFERRED, False, 5
                gap = ProjectionGap(kind=gap_kind, source_path=path, reason=reason, adr_slice=5)
                self.gaps.append(gap)
                gap_by_path[path] = gap
            rows.append(
                ProjectionCoverage(
                    source_path=path,
                    source_file=source_file,
                    source_symbol=symbol,
                    current_authority=authority,
                    disposition=disposition,
                    target_node_kind=node,
                    target_edge_kind=edge,
                    taxonomy_category=taxonomy,
                    stable_identity_derivation=identity,
                    scope_source="project_semantic_graph(scope=ExecutionAccessScopeRef)",
                    fully_representable=fully,
                    absence_valid=value in (None, [], {}),
                    failure_policy="fail closed for missing/contradictory/ambiguous; typed gap for unsupported",
                    adr_slice=adr_slice,
                    reason=reason,
                )
            )
        covered = {row.source_path for row in rows}
        for gap in sorted(
            self.gaps, key=lambda item: (item.source_path, item.kind.value, item.reason)
        ):
            if gap.source_path in covered:
                continue
            value = _value_at_path(self.source, gap.source_path)
            source_file, symbol, authority = _source_metadata(gap.source_path)
            rows.append(
                ProjectionCoverage(
                    source_path=gap.source_path,
                    source_file=source_file,
                    source_symbol=symbol,
                    current_authority=authority,
                    disposition=ProjectionDisposition.DEFERRED,
                    stable_identity_derivation="none; the source container carries ordering or compound semantics without a typed payload home",
                    scope_source="project_semantic_graph(scope=ExecutionAccessScopeRef)",
                    fully_representable=False,
                    absence_valid=value in (None, [], {}),
                    failure_policy="typed deferred gap for unsupported container semantics",
                    adr_slice=gap.adr_slice,
                    reason=gap.reason,
                )
            )
            covered.add(gap.source_path)
        return tuple(sorted(rows, key=lambda row: row.source_path))


def extract_semantic_facts(graph: SemanticGraphV2) -> SemanticFactSet:
    """Extract represented facts independently from the built graph.

    The node fact tuple includes the pinned payload content digest, so the
    source/graph equivalence proof covers projected content, not just
    identity.
    """
    return SemanticFactSet(
        nodes=tuple(
            (
                node.node_id,
                node.kind.value,
                tuple((ref.category.value, ref.identifier) for ref in node.taxonomy_references),
                node.payload_ref.content_digest,
            )
            for node in graph.nodes
        ),
        edges=tuple(
            sorted(
                (edge.kind.value, edge.source_node_id, edge.target_node_id, edge.discriminator)
                for edge in graph.edges
            )
        ),
    )


def _source_facts(
    builder: _Builder, payloads: Mapping[str, SemanticPayloadBase]
) -> SemanticFactSet:
    """Extract source candidates before graph construction (non-circular proof side)."""
    return SemanticFactSet(
        nodes=tuple(
            sorted(
                (
                    node.node_id,
                    node.kind.value,
                    tuple((ref.category.value, ref.identifier) for ref in node.taxonomy_references),
                    payloads[node.node_id].payload_digest,
                )
                for node in builder.nodes.values()
            )
        ),
        edges=tuple(sorted(builder.edges)),
    )


def project_semantic_graph(
    source: Mapping[str, Any],
    *,
    graph_id: str,
    version: int,
    scope: ExecutionAccessScopeRef,
    taxonomy_registry: TaxonomyRegistry,
) -> ProjectionResult:
    """Project current contracts without mutation, writes, network, models, or authority.

    ``taxonomy_registry`` is required and must be a pinned Slice 1 registry.
    Constructing a default here would call ``default_taxonomy_registry()``,
    which lazily imports the runtime layout registry and transport event
    contract at call time — that pulls the workflow manager in, which reads
    the workflow catalog from disk. An offline projection cannot own that side
    effect, and Slice 1 remains the only taxonomy authority, so the caller
    supplies the registry it has pinned.
    """
    plain = _plain(source)
    if not isinstance(plain, dict) or not plain:
        raise ProjectionError(
            [
                ProjectionGap(
                    kind=ProjectionGapKind.MISSING,
                    source_path="$",
                    reason="projection source must be a non-empty mapping",
                )
            ]
        )
    _canonicalize_unordered(plain)
    unknown = sorted(set(plain) - _SUPPORTED_ROOTS)
    if unknown:
        raise ProjectionError(
            [
                ProjectionGap(
                    kind=ProjectionGapKind.UNSUPPORTED,
                    source_path=root,
                    reason="unknown projection source root; current producer names fail closed",
                )
                for root in unknown
            ]
        )
    duplicate_aliases = sorted(
        f"{alias}/{canonical}"
        for alias, canonical in _ROOT_ALIASES.items()
        if alias in plain and canonical in plain
    )
    if duplicate_aliases:
        raise ProjectionError(
            [
                ProjectionGap(
                    kind=ProjectionGapKind.CONTRADICTORY,
                    source_path=pair.split("/", 1)[0],
                    reason=(
                        f"both {pair.split('/')[0]!r} and its canonical name "
                        f"{pair.split('/')[1]!r} are present; one envelope would be "
                        "silently ignored, so duplicate roots fail closed"
                    ),
                )
                for pair in duplicate_aliases
            ]
        )
    builder = _Builder(plain, scope, taxonomy_registry)
    for source_name, raw_scope in sorted(_mapping(plain.get("source_scopes")).items()):
        source_scope = ExecutionAccessScopeRef.model_validate(raw_scope)
        path = f"source_scopes.{source_name}"
        if source_scope != scope:
            raise ProjectionError(
                [
                    ProjectionGap(
                        kind=ProjectionGapKind.CONTRADICTORY,
                        source_path=path,
                        reason="cross-tenant/workspace source composition fails closed",
                    )
                ]
            )
        for leaf_path, _ in _iter_leaves(raw_scope, path):
            builder.mark(leaf_path, identity="exact ExecutionAccessScopeRef equality")
    plan = _mapping(plain.get("app_build_plan") or plain.get("AppBuildPlan"))
    if plan:
        builder.project_plan(
            plan, "app_build_plan" if "app_build_plan" in plain else "AppBuildPlan"
        )
    schema = _mapping(plain.get("app_schema") or plain.get("AppSchemaOutput"))
    if schema:
        root = "app_schema" if "app_schema" in plain else "AppSchemaOutput"
        builder.project_pages(schema.get("pages", []), f"{root}.pages")
        builder.project_data_contract(
            _mapping(schema.get("data_contract")), f"{root}.data_contract"
        )
        if _mapping(schema.get("custom_route_bundle")):
            builder.project_route_manifest(
                _mapping(schema["custom_route_bundle"]), f"{root}.custom_route_bundle"
            )
    design = _mapping(plain.get("design_docs") or plain.get("DesignDocsBundle"))
    if design:
        root = "design_docs" if "design_docs" in plain else "DesignDocsBundle"
        builder.project_plan(
            {
                "surface_map": design.get("surface_map"),
                "data_contract": design.get("data_contract"),
                "pages": _mapping(design.get("experience_spec")).get("pages", []),
            },
            root,
        )
    subscription = _mapping(
        plain.get("subscription_contract") or plain.get("SubscriptionContractOutput")
    )
    if subscription:
        root = (
            "subscription_contract"
            if "subscription_contract" in plain
            else "SubscriptionContractOutput"
        )
        builder.project_subscriptions(
            _mapping(subscription.get("subscription_config_file")),
            f"{root}.subscription_config_file",
        )
    builder.project_modules(plain.get("modules", []), "modules")
    builder.project_pages(plain.get("pages", []), "pages")
    if _mapping(plain.get("route_manifest")):
        builder.project_route_manifest(_mapping(plain["route_manifest"]), "route_manifest")
    builder.project_subscriptions(_mapping(plain.get("subscriptions")), "subscriptions")
    builder.project_workflows(plain.get("agent_workflows", []), "agent_workflows")
    if _mapping(plain.get("app_context")):
        builder.project_ownership(_mapping(plain["app_context"]), "app_context")
    if _mapping(plain.get("ownership_evidence")):
        builder.project_ownership(_mapping(plain["ownership_evidence"]), "ownership_evidence")
    for provenance_root in sorted(_PROVENANCE_ROOTS & set(plain)):
        builder.project_provenance(plain[provenance_root], provenance_root)
    if not builder.nodes:
        # Distinguish "this input carries only unrepresentable
        # provenance" from "this input declares nothing at all". Reporting the
        # former as a missing semantic identity misnames the cause.
        provenance_only = bool(_PROVENANCE_ROOTS & set(plain)) and not (
            set(plain) - _PROVENANCE_ROOTS - {"source_scopes"}
        )
        if provenance_only:
            raise ProjectionError(
                [
                    ProjectionGap(
                        kind=ProjectionGapKind.UNSUPPORTED,
                        source_path=root,
                        reason=(
                            f"{_SOURCE_AUTHORITIES[root][2]} carries no semantic-graph "
                            "identity; it is accepted and classified as typed gaps but "
                            "cannot by itself produce a graph"
                        ),
                        adr_slice=5,
                    )
                    for root in sorted(_PROVENANCE_ROOTS & set(plain))
                ]
            )
        raise ProjectionError(
            [
                ProjectionGap(
                    kind=ProjectionGapKind.MISSING,
                    source_path="$",
                    reason="source contains no representable semantic identity",
                )
            ]
        )
    builder.resolve_edges()
    payloads = builder.build_payloads(version)
    nodes_v2 = [
        SemanticNodeV2(
            node_id=node.node_id,
            kind=node.kind,
            taxonomy_references=node.taxonomy_references,
            node_references=node.node_references,
            payload_ref=semantic_payload_ref(payloads[node.node_id]),
        )
        for node in builder.nodes.values()
    ]
    graph = build_semantic_graph_v2(
        graph_id=graph_id,
        version=version,
        scope=scope,
        nodes=nodes_v2,
        edges=list(builder.edges.values()),
    )
    validate_semantic_graph_v2_taxonomy_closure(graph, builder.registry)
    validate_semantic_graph_v2_payload_closure(graph, payloads.values())
    source_facts, represented_facts = (
        _source_facts(builder, payloads),
        extract_semantic_facts(graph),
    )
    if source_facts != represented_facts:
        raise ProjectionError(
            [
                ProjectionGap(
                    kind=ProjectionGapKind.CONTRADICTORY,
                    source_path="$",
                    reason="source-derived and graph-represented facts diverge",
                )
            ]
        )
    coverage = builder.coverage()
    gaps = tuple(
        sorted(set(builder.gaps), key=lambda gap: (gap.source_path, gap.kind.value, gap.reason))
    )
    return ProjectionResult(
        source_digest=canonical_digest(plain),
        graph=graph,
        payloads=tuple(
            sorted(payloads.values(), key=lambda payload: payload.node_id)
        ),
        source_facts=source_facts,
        represented_facts=represented_facts,
        gaps=gaps,
        coverage=coverage,
    )


__all__ = [
    "PROJECTION_SCHEMA_VERSION",
    "ProjectionCoverage",
    "ProjectionDisposition",
    "ProjectionError",
    "ProjectionGap",
    "ProjectionGapKind",
    "ProjectionResult",
    "SemanticFactSet",
    "extract_semantic_facts",
    "project_semantic_graph",
]
