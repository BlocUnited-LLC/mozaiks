"""Deterministic, offline-only projection of current build artifacts.

This module is ADR 0007 Slice 3 comparison infrastructure.  Production
generators, hosts, loaders, workflows, Studio, and control-plane code must not
import it.  It projects only facts representable by ``SemanticGraph`` v1 and
reports every other source leaf through a typed coverage/gap record.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from enum import StrEnum
from typing import Any, Literal

from pydantic import Field

from mozaiksai.core.semantics.canonical import canonical_digest
from mozaiksai.core.semantics.graph import (
    SemanticEdge,
    SemanticEdgeKind,
    SemanticGraph,
    SemanticNode,
    SemanticNodeKind,
    TaxonomyReference,
    build_semantic_graph,
    validate_semantic_graph_taxonomy_closure,
)
from mozaiksai.core.semantics.refs import ExecutionAccessScopeRef, SemanticsModel
from mozaiksai.core.taxonomy import SemanticCategory, TaxonomyRegistry, default_taxonomy_registry

PROJECTION_SCHEMA_VERSION: Literal["mozaiks.semantic_projection.v1"] = (
    "mozaiks.semantic_projection.v1"
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
    nodes: tuple[tuple[str, str], ...]
    edges: tuple[tuple[str, str, str, str | None], ...]


class ProjectionResult(SemanticsModel):
    schema_version: Literal["mozaiks.semantic_projection.v1"] = PROJECTION_SCHEMA_VERSION
    source_digest: str
    graph: SemanticGraph
    represented_facts: SemanticFactSet
    gaps: tuple[ProjectionGap, ...]
    coverage: tuple[ProjectionCoverage, ...]


class ProjectionError(ValueError):
    """Fail-closed projection error retaining deterministic typed gaps."""

    def __init__(self, gaps: Iterable[ProjectionGap]):
        self.gaps = tuple(sorted(gaps, key=lambda gap: (gap.source_path, gap.kind.value, gap.reason)))
        super().__init__("; ".join(f"{gap.source_path}: {gap.reason}" for gap in self.gaps))


_SOURCE_AUTHORITIES: dict[str, tuple[str, str, str]] = {
    "app_build_plan": (
        "factory_app/workflows/AppGenerator/structured_outputs.yaml",
        "AppBuildPlan",
        "AppGenerator agent-authored operational plan",
    ),
    "app_schema": (
        "factory_app/workflows/AppGenerator/structured_outputs.yaml",
        "AppSchemaOutput",
        "AppGenerator schema-stage output",
    ),
    "design_docs": (
        "factory_app/workflows/DesignDocs/structured_outputs.yaml",
        "DesignDocsBundle",
        "DesignDocs stage output",
    ),
    "subscription_contract": (
        "factory_app/workflows/SubscriptionContractDesigner/structured_outputs.yaml",
        "SubscriptionContractOutput",
        "SubscriptionContractDesigner stage output",
    ),
    "modules": (
        "mozaiksai/core/runtime/app/module_loader.py",
        "ModuleLoader",
        "runtime module child contracts",
    ),
    "subscriptions": (
        "mozaiksai/core/runtime/app/subscriptions_loader.py",
        "SubscriptionsConfig",
        "runtime subscription child contract",
    ),
    "agent_workflows": (
        "factory_app/workflows/AgentGenerator/structured_outputs.yaml",
        "WorkflowBundleBuilderOutput",
        "AgentGenerator workflow bundle output",
    ),
    "recorded_artifacts": (
        "mozaiksai/core/runtime/app/loader.py",
        "AppLoader",
        "recorded generated-app artifact bundle",
    ),
    "ownership_evidence": (
        "mozaiksai/core/app_context/models.py",
        "OwnershipBoundary",
        "observed brownfield/hybrid ownership evidence",
    ),
    "build_context": (
        "mozaiksai/core/session/build_context_schema.py",
        "BuildContextRegistry",
        "declared build-context provenance",
    ),
    "source_scopes": (
        "mozaiksai/core/semantics/offline_projection.py",
        "project_semantic_graph",
        "offline projection composition envelope",
    ),
}

_NON_SEMANTIC_NAMES = frozenset(
    {
        "agent_message",
        "description",
        "label",
        "notes",
        "purpose",
        "rationale",
        "summary",
        "title",
        "frontend_markdown",
        "backend_markdown",
        "database_markdown",
        "acceptance_criteria",
        "initial_message",
        "generation_order",
    }
)
_SLUG = re.compile(r"[^a-z0-9_]+")


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
            [ProjectionGap(kind=ProjectionGapKind.MISSING, source_path="identity", reason="stable identity is empty")]
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
    root = {
        "AppBuildPlan": "app_build_plan",
        "AppSchemaOutput": "app_schema",
        "DesignDocsBundle": "design_docs",
        "SubscriptionContractOutput": "subscription_contract",
    }.get(root, root)
    return _SOURCE_AUTHORITIES.get(root, ("unknown", "unknown", "unclassified input"))


class _Builder:
    def __init__(self, source: dict[str, Any], scope: ExecutionAccessScopeRef, registry: TaxonomyRegistry):
        self.source = source
        self.scope = scope
        self.registry = registry
        self.nodes: dict[str, SemanticNode] = {}
        self.edges: dict[tuple[str, str, str, str | None], SemanticEdge] = {}
        self.projected_paths: dict[str, tuple[SemanticNodeKind | None, SemanticEdgeKind | None, SemanticCategory | None, str]] = {}
        self.gaps: list[ProjectionGap] = []

    def mark(
        self,
        path: str,
        *,
        node: SemanticNodeKind | None = None,
        edge: SemanticEdgeKind | None = None,
        taxonomy: SemanticCategory | None = None,
        identity: str,
    ) -> None:
        self.projected_paths[path] = (node, edge, taxonomy, identity)

    def add_node(
        self,
        kind: SemanticNodeKind,
        identity: Any,
        *,
        path: str,
        taxonomy: tuple[SemanticCategory, str] | None = None,
    ) -> str:
        node_id = _node_id(kind, identity)
        tax_refs: tuple[TaxonomyReference, ...] = ()
        if taxonomy is not None:
            category, identifier = taxonomy
            try:
                self.registry.resolve(category, identifier)
            except Exception as exc:
                raise ProjectionError(
                    [ProjectionGap(kind=ProjectionGapKind.UNSUPPORTED, source_path=path, reason=f"unknown {category.value} taxonomy identifier {identifier!r}: {exc}")]
                ) from exc
            tax_refs = (TaxonomyReference(category=category, identifier=identifier),)
        candidate = SemanticNode(node_id=node_id, kind=kind, taxonomy_references=tax_refs)
        existing = self.nodes.get(node_id)
        if existing is not None and existing != candidate:
            raise ProjectionError(
                [ProjectionGap(kind=ProjectionGapKind.CONTRADICTORY, source_path=path, reason=f"conflicting facts reuse node identity {node_id!r}")]
            )
        self.nodes[node_id] = candidate
        self.mark(path, node=kind, taxonomy=taxonomy[0] if taxonomy else None, identity="kind + canonical source identifier")
        return node_id

    def add_edge(self, kind: SemanticEdgeKind, source: str, target: str, *, path: str, discriminator: str | None = None) -> None:
        key = (kind.value, source, target, discriminator)
        self.edges[key] = SemanticEdge(kind=kind, source_node_id=source, target_node_id=target, discriminator=discriminator)
        self.mark(path, edge=kind, identity="edge kind + stable source/target identities + discriminator")

    def module(self, module_id: Any, path: str) -> str:
        return self.add_node(SemanticNodeKind.MODULE, module_id, path=path)

    def project_plan(self, plan: dict[str, Any], root: str) -> None:
        surfaces = _as_list(_mapping(plan.get("surface_map")).get("surfaces"))
        for i, surface in enumerate(surfaces):
            item = _mapping(surface)
            base = f"{root}.surface_map.surfaces[{i}]"
            sid = item.get("surface_id")
            if not sid:
                raise ProjectionError([ProjectionGap(kind=ProjectionGapKind.MISSING, source_path=f"{base}.surface_id", reason="surface identity is required")])
            module = self.module(sid, f"{base}.surface_id")
            self.mark(f"{base}.surface_kind", node=SemanticNodeKind.MODULE, identity="surface kind selects graph node kind")
            self.mark(f"{base}.owner", node=SemanticNodeKind.MODULE, identity="ownership is preserved by scoped module identity")
            for j, action in enumerate(_as_list(item.get("owned_mutations"))):
                action_node = self.add_node(SemanticNodeKind.ACTION, f"{sid}_{action}", path=f"{base}.owned_mutations[{j}]")
                self.add_edge(SemanticEdgeKind.DECLARES, module, action_node, path=f"{base}.owned_mutations[{j}]")
            for j, event in enumerate(_as_list(item.get("events_emitted"))):
                event_node = self.add_node(SemanticNodeKind.EVENT, event, path=f"{base}.events_emitted[{j}]", taxonomy=(SemanticCategory.EVENT, str(event)))
                self.add_edge(SemanticEdgeKind.EMITS, module, event_node, path=f"{base}.events_emitted[{j}]")
            for j, dependency in enumerate(_as_list(item.get("dependencies"))):
                target = self.module(dependency, f"{base}.dependencies[{j}]")
                self.add_edge(SemanticEdgeKind.DEPENDS_ON, module, target, path=f"{base}.dependencies[{j}]")

        for i, page in enumerate(_as_list(plan.get("pages"))):
            item = _mapping(page)
            identity = item.get("name") or item.get("route")
            if identity:
                self.add_node(SemanticNodeKind.PAGE, identity, path=f"{root}.pages[{i}].name")
                if "route" in item:
                    self.mark(f"{root}.pages[{i}].route", node=SemanticNodeKind.PAGE, identity="page name is stable; route is deferred payload")

        for i, entity in enumerate(_as_list(plan.get("entities"))):
            item = _mapping(entity)
            identity = item.get("name")
            if identity:
                self.add_node(SemanticNodeKind.DATA_COLLECTION, identity, path=f"{root}.entities[{i}].name")

        for i, pack in enumerate(_as_list(plan.get("capability_packs"))):
            item = _mapping(pack)
            pack_id = item.get("capability_pack_id")
            if pack_id:
                cap = self.add_node(SemanticNodeKind.CAPABILITY, pack_id, path=f"{root}.capability_packs[{i}].capability_pack_id")
                surface_id = item.get("surface_id")
                if surface_id:
                    module = self.module(surface_id, f"{root}.capability_packs[{i}].surface_id")
                    self.add_edge(SemanticEdgeKind.DEPENDS_ON, module, cap, path=f"{root}.capability_packs[{i}].capability_pack_id")

        for i, flow in enumerate(_as_list(plan.get("event_flows"))):
            item = _mapping(flow)
            event = item.get("event_type")
            producer = item.get("producer_pack_id")
            if event and producer:
                event_node = self.add_node(SemanticNodeKind.EVENT, event, path=f"{root}.event_flows[{i}].event_type", taxonomy=(SemanticCategory.EVENT, str(event)))
                producer_node = self.module(producer, f"{root}.event_flows[{i}].producer_pack_id")
                self.add_edge(SemanticEdgeKind.EMITS, producer_node, event_node, path=f"{root}.event_flows[{i}].event_type")

        for i, touchpoint in enumerate(_as_list(plan.get("workflow_touchpoints"))):
            item = _mapping(touchpoint)
            workflow_id = item.get("workflow_id")
            if workflow_id:
                workflow = self.add_node(SemanticNodeKind.WORKFLOW, workflow_id, path=f"{root}.workflow_touchpoints[{i}].workflow_id")
                page_name = item.get("page_name")
                if page_name:
                    page = self.add_node(SemanticNodeKind.PAGE, page_name, path=f"{root}.workflow_touchpoints[{i}].page_name")
                    self.add_edge(SemanticEdgeKind.BINDS, page, workflow, path=f"{root}.workflow_touchpoints[{i}].workflow_id")

        self.project_data_contract(_mapping(plan.get("data_contract")), f"{root}.data_contract")
        for i, target in enumerate(_as_list(plan.get("deployment_targets"))):
            item = _mapping(target)
            identity = item.get("target_id") or item.get("deployment_profile")
            if identity:
                self.add_node(SemanticNodeKind.DEPLOYMENT_TARGET, identity, path=f"{root}.deployment_targets[{i}].target_id")

    def project_schema(self, schema: dict[str, Any], root: str) -> None:
        for i, page in enumerate(_as_list(schema.get("pages"))):
            item = _mapping(page)
            identity = item.get("page_id") or item.get("name") or item.get("route")
            if not identity:
                raise ProjectionError([ProjectionGap(kind=ProjectionGapKind.MISSING, source_path=f"{root}.pages[{i}]", reason="page identity is required")])
            page_node = self.add_node(SemanticNodeKind.PAGE, identity, path=f"{root}.pages[{i}].name")
            for j, section in enumerate(_as_list(item.get("sections"))):
                section_item = _mapping(section)
                section_id = section_item.get("id") or section_item.get("section_id")
                if section_id:
                    section_node = self.add_node(SemanticNodeKind.SECTION, f"{identity}_{section_id}", path=f"{root}.pages[{i}].sections[{j}].id")
                    self.add_edge(SemanticEdgeKind.RENDERS, page_node, section_node, path=f"{root}.pages[{i}].sections[{j}].id")
                self._project_page_actions(section_item, page_node, f"{root}.pages[{i}].sections[{j}]")
        self.project_data_contract(_mapping(schema.get("data_contract")), f"{root}.data_contract")

    def _project_page_actions(self, value: Any, page_node: str, path: str) -> None:
        if isinstance(value, Mapping):
            for key, child in value.items():
                child_path = f"{path}.{key}"
                if key in {"api_endpoint", "endpoint"} and isinstance(child, str) and "/" in child:
                    parts = [part for part in child.strip("/").split("/") if part]
                    if len(parts) >= 2:
                        module_id, action_id = parts[-2], parts[-1]
                        module = self.module(module_id, child_path)
                        action = self.add_node(SemanticNodeKind.ACTION, f"{module_id}_{action_id}", path=child_path)
                        self.add_edge(SemanticEdgeKind.DECLARES, module, action, path=child_path)
                        self.add_edge(SemanticEdgeKind.BINDS, page_node, action, path=child_path)
                else:
                    self._project_page_actions(child, page_node, child_path)
        elif isinstance(value, list):
            for index, child in enumerate(value):
                self._project_page_actions(child, page_node, f"{path}[{index}]")

    def project_data_contract(self, contract: dict[str, Any], root: str) -> None:
        for i, surface in enumerate(_as_list(contract.get("surfaces"))):
            item = _mapping(surface)
            owner_id = item.get("surface_id")
            owner = self.module(owner_id, f"{root}.surfaces[{i}].surface_id") if owner_id else None
            for j, collection in enumerate(_as_list(item.get("collections"))):
                collection_item = _mapping(collection)
                name = collection_item.get("name") or collection_item.get("entity_name")
                if name:
                    collection_node = self.add_node(SemanticNodeKind.DATA_COLLECTION, f"{owner_id}_{name}", path=f"{root}.surfaces[{i}].collections[{j}].name")
                    if owner:
                        self.add_edge(SemanticEdgeKind.OWNS, owner, collection_node, path=f"{root}.surfaces[{i}].collections[{j}].name")
        for i, alias in enumerate(_as_list(contract.get("aliases"))):
            item = _mapping(alias)
            alias_id = item.get("alias")
            collection = item.get("collection")
            if alias_id:
                alias_node = self.add_node(SemanticNodeKind.DATA_ALIAS, alias_id, path=f"{root}.aliases[{i}].alias")
                if collection:
                    target = self.add_node(SemanticNodeKind.DATA_COLLECTION, f"{item.get('owner_module', 'app')}_{collection}", path=f"{root}.aliases[{i}].collection")
                    self.add_edge(SemanticEdgeKind.BINDS, alias_node, target, path=f"{root}.aliases[{i}].collection")

    def project_modules(self, modules: Any, root: str) -> None:
        values = list(modules.values()) if isinstance(modules, Mapping) else _as_list(modules)
        for i, raw in enumerate(values):
            bundle = _mapping(raw)
            manifest = _mapping(bundle.get("manifest") or bundle.get("module_manifest") or bundle)
            identity = _mapping(manifest.get("module"))
            module_id = identity.get("id") or manifest.get("module_id")
            if not module_id:
                raise ProjectionError([ProjectionGap(kind=ProjectionGapKind.MISSING, source_path=f"{root}[{i}]", reason="module id is required")])
            module = self.module(module_id, f"{root}[{i}].manifest.module.id")
            for j, raw_action in enumerate(_as_list(manifest.get("actions"))):
                action_item = _mapping(raw_action)
                action_id = action_item.get("id")
                if action_id:
                    action = self.add_node(SemanticNodeKind.ACTION, f"{module_id}_{action_id}", path=f"{root}[{i}].manifest.actions[{j}].id")
                    self.add_edge(SemanticEdgeKind.DECLARES, module, action, path=f"{root}[{i}].manifest.actions[{j}].id")
                    for k, event in enumerate(_as_list(action_item.get("emits"))):
                        event_node = self.add_node(SemanticNodeKind.EVENT, event, path=f"{root}[{i}].manifest.actions[{j}].emits[{k}]", taxonomy=(SemanticCategory.EVENT, str(event)))
                        self.add_edge(SemanticEdgeKind.EMITS, action, event_node, path=f"{root}[{i}].manifest.actions[{j}].emits[{k}]")
                    gate = action_item.get("entitlement_gate")
                    if gate:
                        cap = self.add_node(SemanticNodeKind.CAPABILITY, gate, path=f"{root}[{i}].manifest.actions[{j}].entitlement_gate", taxonomy=(SemanticCategory.CAPABILITY, str(gate)))
                        self.add_edge(SemanticEdgeKind.GATES, cap, action, path=f"{root}[{i}].manifest.actions[{j}].entitlement_gate")
            for j, raw_cap in enumerate(_as_list(manifest.get("capabilities"))):
                cap_item = _mapping(raw_cap)
                cap_id = cap_item.get("capability_id")
                if cap_id:
                    cap = self.add_node(SemanticNodeKind.CAPABILITY, cap_id, path=f"{root}[{i}].manifest.capabilities[{j}].capability_id", taxonomy=(SemanticCategory.CAPABILITY, str(cap_id)))
                    self.add_edge(SemanticEdgeKind.DECLARES, module, cap, path=f"{root}[{i}].manifest.capabilities[{j}].capability_id")
            events = _mapping(bundle.get("events"))
            for j, raw_event in enumerate(_as_list(events.get("events"))):
                event = _mapping(raw_event).get("type")
                if event:
                    event_node = self.add_node(SemanticNodeKind.EVENT, event, path=f"{root}[{i}].events.events[{j}].type", taxonomy=(SemanticCategory.EVENT, str(event)))
                    self.add_edge(SemanticEdgeKind.EMITS, module, event_node, path=f"{root}[{i}].events.events[{j}].type")
            reactions = _mapping(bundle.get("reactions"))
            for j, raw_reaction in enumerate(_as_list(reactions.get("reactions"))):
                reaction_item = _mapping(raw_reaction)
                reaction_id = reaction_item.get("id")
                event = reaction_item.get("event_type")
                if reaction_id and event:
                    reaction = self.add_node(SemanticNodeKind.REACTION, f"{module_id}_{reaction_id}", path=f"{root}[{i}].reactions.reactions[{j}].id")
                    event_node = self.add_node(SemanticNodeKind.EVENT, event, path=f"{root}[{i}].reactions.reactions[{j}].event_type", taxonomy=(SemanticCategory.EVENT, str(event)))
                    self.add_edge(SemanticEdgeKind.CONSUMES, reaction, event_node, path=f"{root}[{i}].reactions.reactions[{j}].event_type")
                    self.add_edge(SemanticEdgeKind.DECLARES, module, reaction, path=f"{root}[{i}].reactions.reactions[{j}].id")

    def project_subscriptions(self, config: dict[str, Any], root: str) -> None:
        for i, raw_plan in enumerate(_as_list(config.get("plans"))):
            item = _mapping(raw_plan)
            plan_id = item.get("plan_id")
            if not plan_id:
                raise ProjectionError([ProjectionGap(kind=ProjectionGapKind.MISSING, source_path=f"{root}.plans[{i}].plan_id", reason="subscription plan id is required")])
            plan = self.add_node(SemanticNodeKind.PLAN, plan_id, path=f"{root}.plans[{i}].plan_id")
            for j, cap_id in enumerate(_as_list(item.get("capabilities"))):
                cap = self.add_node(SemanticNodeKind.CAPABILITY, cap_id, path=f"{root}.plans[{i}].capabilities[{j}]", taxonomy=(SemanticCategory.CAPABILITY, str(cap_id)))
                self.add_edge(SemanticEdgeKind.GATES, plan, cap, path=f"{root}.plans[{i}].capabilities[{j}]")
            for j, raw_limit in enumerate(_as_list(item.get("usage_limits"))):
                limit_item = _mapping(raw_limit)
                meter_id = limit_item.get("meter_id")
                if meter_id:
                    meter = self.add_node(SemanticNodeKind.METER, meter_id, path=f"{root}.plans[{i}].usage_limits[{j}].meter_id")
                    limit = self.add_node(SemanticNodeKind.LIMIT, f"{plan_id}_{meter_id}", path=f"{root}.plans[{i}].usage_limits[{j}].monthly_limit")
                    self.add_edge(SemanticEdgeKind.GATES, plan, limit, path=f"{root}.plans[{i}].usage_limits[{j}].monthly_limit")
                    self.add_edge(SemanticEdgeKind.BINDS, limit, meter, path=f"{root}.plans[{i}].usage_limits[{j}].meter_id")
        for key, id_key in (("top_up_products", "product_id"), ("add_on_products", "add_on_id")):
            for i, raw_product in enumerate(_as_list(config.get(key))):
                product_id = _mapping(raw_product).get(id_key)
                if product_id:
                    self.add_node(SemanticNodeKind.PRODUCT, product_id, path=f"{root}.{key}[{i}].{id_key}")

    def project_workflows(self, workflows: Any, root: str) -> None:
        values = list(workflows.values()) if isinstance(workflows, Mapping) else _as_list(workflows)
        for i, raw in enumerate(values):
            item = _mapping(raw)
            name = item.get("workflow_name") or item.get("name")
            if not name:
                continue
            workflow = self.add_node(SemanticNodeKind.WORKFLOW, name, path=f"{root}[{i}].workflow_name")
            for j, raw_trigger in enumerate(_as_list(item.get("triggers"))):
                trigger_item = _mapping(raw_trigger)
                identity = trigger_item.get("event") or trigger_item.get("endpoint") or f"{name}_{j}"
                trigger = self.add_node(SemanticNodeKind.TRIGGER, identity, path=f"{root}[{i}].triggers[{j}]")
                self.add_edge(SemanticEdgeKind.BINDS, trigger, workflow, path=f"{root}[{i}].triggers[{j}]")
                event = trigger_item.get("event")
                if event:
                    event_node = self.add_node(SemanticNodeKind.EVENT, event, path=f"{root}[{i}].triggers[{j}].event", taxonomy=(SemanticCategory.EVENT, str(event)))
                    self.add_edge(SemanticEdgeKind.CONSUMES, trigger, event_node, path=f"{root}[{i}].triggers[{j}].event")

    def coverage(self) -> tuple[ProjectionCoverage, ...]:
        rows: list[ProjectionCoverage] = []
        for path, value in _iter_leaves(self.source, ""):
            source_file, source_symbol, authority = _source_metadata(path)
            projected = self.projected_paths.get(path)
            leaf_name = re.split(r"[.\[]", path)[-1].rstrip("]0123456789")
            if projected:
                node, edge, taxonomy, identity = projected
                disposition = ProjectionDisposition.PROJECTED
                fully = leaf_name in {"id", "name", "type", "event", "event_type", "capability_id", "plan_id", "target_id", "workflow_id", "surface_id", "monthly_limit", "api_endpoint", "endpoint"}
                reason = "represented in graph identity, taxonomy reference, or edge closure"
                adr_slice = None if fully else 5
            elif leaf_name in _NON_SEMANTIC_NAMES:
                node = edge = taxonomy = None
                identity = "excluded from SemanticGraph v1 identity by declared non-semantic classification"
                disposition = ProjectionDisposition.DELIBERATELY_NON_SEMANTIC
                fully = True
                reason = "human-readable or execution-planning metadata is not graph semantics"
                adr_slice = None
            else:
                node = edge = taxonomy = None
                identity = "none; SemanticGraph v1 has no payload field for this fact"
                disposition = ProjectionDisposition.DEFERRED
                fully = False
                reason = "source fact is not representable by Slice 2 identity-only nodes"
                adr_slice = 5
                self.gaps.append(ProjectionGap(kind=ProjectionGapKind.UNSUPPORTED, source_path=path, reason=reason, adr_slice=5))
            rows.append(
                ProjectionCoverage(
                    source_path=path,
                    source_file=source_file,
                    source_symbol=source_symbol,
                    current_authority=authority,
                    disposition=disposition,
                    target_node_kind=node,
                    target_edge_kind=edge,
                    taxonomy_category=taxonomy,
                    stable_identity_derivation=identity,
                    scope_source="project_semantic_graph(scope=ExecutionAccessScopeRef)",
                    fully_representable=fully,
                    absence_valid=value in (None, [], {}),
                    failure_policy="fail closed when required/contradictory/ambiguous; report typed gap when unsupported",
                    adr_slice=adr_slice,
                    reason=reason,
                )
            )
        return tuple(sorted(rows, key=lambda row: row.source_path))


def extract_semantic_facts(graph: SemanticGraph) -> SemanticFactSet:
    """Return exactly the graph-v1 facts used by Slice 3 equivalence."""
    return SemanticFactSet(
        nodes=tuple((node.node_id, node.kind.value) for node in graph.nodes),
        edges=tuple(
            (edge.kind.value, edge.source_node_id, edge.target_node_id, edge.discriminator)
            for edge in graph.edges
        ),
    )


def project_semantic_graph(
    source: Mapping[str, Any],
    *,
    graph_id: str,
    version: int,
    scope: ExecutionAccessScopeRef,
    taxonomy_registry: TaxonomyRegistry | None = None,
) -> ProjectionResult:
    """Project current outputs without I/O, mutation, defaults, or side effects."""
    plain = _plain(source)
    if not isinstance(plain, dict) or not plain:
        raise ProjectionError([ProjectionGap(kind=ProjectionGapKind.MISSING, source_path="$", reason="projection source must be a non-empty mapping")])
    registry = taxonomy_registry or default_taxonomy_registry()
    builder = _Builder(plain, scope, registry)

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
        for leaf_path, _value in _iter_leaves(raw_scope, path):
            builder.mark(
                leaf_path,
                identity="exact ExecutionAccessScopeRef equality across every source",
            )

    plan = _mapping(plain.get("app_build_plan") or plain.get("AppBuildPlan"))
    if plan:
        root = "app_build_plan" if "app_build_plan" in plain else "AppBuildPlan"
        builder.project_plan(plan, root)
    schema = _mapping(plain.get("app_schema") or plain.get("AppSchemaOutput"))
    if schema:
        root = "app_schema" if "app_schema" in plain else "AppSchemaOutput"
        builder.project_schema(schema, root)
    design = _mapping(plain.get("design_docs") or plain.get("DesignDocsBundle"))
    if design:
        root = "design_docs" if "design_docs" in plain else "DesignDocsBundle"
        builder.project_plan({"surface_map": design.get("surface_map"), "data_contract": design.get("data_contract"), "pages": _mapping(design.get("experience_spec")).get("pages", [])}, root)
    subscription_output = _mapping(plain.get("subscription_contract") or plain.get("SubscriptionContractOutput"))
    if subscription_output:
        root = "subscription_contract" if "subscription_contract" in plain else "SubscriptionContractOutput"
        builder.project_subscriptions(_mapping(subscription_output.get("subscription_config_file")), f"{root}.subscription_config_file")
    builder.project_modules(plain.get("modules", []), "modules")
    builder.project_subscriptions(_mapping(plain.get("subscriptions")), "subscriptions")
    builder.project_workflows(plain.get("agent_workflows", []), "agent_workflows")
    recorded = _mapping(plain.get("recorded_artifacts"))
    if recorded:
        builder.project_modules(recorded.get("modules", []), "recorded_artifacts.modules")
        builder.project_schema(
            {"pages": recorded.get("pages", []), "data_contract": recorded.get("data_contract")},
            "recorded_artifacts",
        )
        builder.project_subscriptions(
            _mapping(recorded.get("subscriptions")), "recorded_artifacts.subscriptions"
        )
        builder.project_workflows(
            recorded.get("workflows", []), "recorded_artifacts.workflows"
        )

    ownership = _mapping(plain.get("ownership_evidence"))
    mode = str(ownership.get("mode") or "greenfield")
    if mode in {"brownfield", "hybrid"} and not _as_list(ownership.get("owned_surfaces")):
        raise ProjectionError([ProjectionGap(kind=ProjectionGapKind.MISSING, source_path="ownership_evidence.owned_surfaces", reason=f"{mode} projection requires explicit owned surfaces")])
    for i, surface_id in enumerate(_as_list(ownership.get("owned_surfaces"))):
        builder.module(surface_id, f"ownership_evidence.owned_surfaces[{i}]")

    if not builder.nodes:
        raise ProjectionError([ProjectionGap(kind=ProjectionGapKind.MISSING, source_path="$", reason="source contains no representable semantic identity")])
    graph = build_semantic_graph(
        graph_id=graph_id,
        version=version,
        scope=scope,
        nodes=list(builder.nodes.values()),
        edges=list(builder.edges.values()),
    )
    validate_semantic_graph_taxonomy_closure(graph, registry)
    coverage = builder.coverage()
    gaps = tuple(sorted(set(builder.gaps), key=lambda gap: (gap.source_path, gap.reason)))
    return ProjectionResult(
        source_digest=canonical_digest(plain),
        graph=graph,
        represented_facts=extract_semantic_facts(graph),
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
