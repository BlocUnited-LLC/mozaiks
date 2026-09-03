"""ADR 0007 Slice 4C prerequisite: renderer-input closure proofs.

This suite proves the bounded page-declarative corpus and exact preservation
contract only.  It deliberately proves that module, data, subscription,
workflow, app-manifest, and deployment rendering remain deferred rather than
pretending their historical bytes are already semantic renderer inputs.
"""

from __future__ import annotations

import hashlib
import subprocess
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

from mozaiksai.core.runtime.app.layout_registry import (
    ArtifactKind,
    MaterializerIdentifier,
    build_app_layout_registry,
)
from mozaiksai.core.runtime.app.page_schema import AppPageSchema
from mozaiksai.core.semantics.binding import (
    ImplementationBindingError,
    RendererSelection,
    build_implementation_binding,
    validate_implementation_binding_against_graph,
)
from mozaiksai.core.semantics.compilation_plan import (
    PlanDisposition,
    PlanGapCode,
    derive_compilation_plan,
    plan_regeneration_closure,
)
from mozaiksai.core.semantics.graph import (
    SemanticEdge,
    SemanticEdgeKind,
    SemanticNodeV2,
    build_semantic_graph_v2,
)
from mozaiksai.core.semantics.offline_projection import (
    ProjectionError,
    project_semantic_graph,
)
from mozaiksai.core.semantics.opaque_artifact import PreservedOpaqueArtifact
from mozaiksai.core.semantics.payloads import (
    PagePayload,
    SectionPayload,
    WorkflowPayload,
    build_semantic_payload,
    semantic_payload_ref,
)
from mozaiksai.core.semantics.refs import ChildContractRef, SemanticGraphRef
from tests.test_semantic_manifest_binding_contracts import _graph as _v1_graph
from tests.test_semantic_offline_projection import _pinned_registry
from tests.test_semantic_payload_graph_v2 import _corpus_graph

ROOT = Path(__file__).resolve().parents[1]


def _registry():
    return build_app_layout_registry(())


def _plan():
    graph, payloads = _corpus_graph()
    return derive_compilation_plan(graph=graph, payloads=payloads, registry=_registry())


def _replace_payload(graph, payloads, replacement):
    updated = tuple(
        replacement if payload.node_id == replacement.node_id else payload
        for payload in payloads
    )
    nodes = tuple(
        SemanticNodeV2(
            node_id=payload.node_id,
            kind=payload.payload_kind,
            payload_ref=semantic_payload_ref(payload),
        )
        for payload in updated
    )
    return (
        build_semantic_graph_v2(
            graph_id=graph.graph_id,
            version=graph.version,
            scope=graph.scope,
            nodes=nodes,
            edges=graph.edges,
            namespace_grants=graph.namespace_grants,
        ),
        updated,
    )


def _renderer_binding():
    graph, _payloads = _corpus_graph()
    return build_implementation_binding(
        binding_id="page-renderer-binding",
        version=1,
        scope=graph.scope,
        semantic_graph_ref=SemanticGraphRef(
            subject_id=graph.graph_id,
            subject_version=graph.version,
            content_digest=graph.graph_digest,
            scope=graph.scope,
        ),
        renderer_selections=(
            RendererSelection(
                materializer_id=MaterializerIdentifier.PAGE_SCHEMA_EXECUTOR,
                implementation_id="canonical-page-schema",
                implementation_version="1.0.0",
                artifact_families=(ArtifactKind.APP_UI_PAGE_SCHEMA.value,),
            ),
        ),
    )


def test_page_payload_reconstructs_normative_runtime_model_without_invention() -> None:
    source_page = {
        "schema_version": "mozaiks.app_page.v1",
        "name": "home",
        "route": "/home",
        "title": "Home",
        "page_type": "landing",
        "layout": "full-width",
        "shell_mode": "public",
        "roles": ["admin"],
        "navigation": {"label": "Home", "scope": "global"},
        "meta": {"requiresAuth": False, "shellMode": "public"},
        "sections": [
            {
                "id": "hero",
                "primitive": "PageHeader",
                "config": {"title": "Welcome"},
            }
        ],
    }
    result = project_semantic_graph(
        {"pages": [source_page]},
        graph_id="page-closure",
        version=1,
        scope=_corpus_graph()[0].scope,
        taxonomy_registry=_pinned_registry(),
    )
    page = next(payload for payload in result.payloads if payload.payload_kind.value == "page")
    sections = {
        payload.node_id: payload
        for payload in result.payloads
        if payload.payload_kind.value == "section"
    }
    reconstructed = AppPageSchema(
        schema_version="mozaiks.app_page.v1",
        name=page.page_id,
        route=page.route,
        title=page.title,
        page_type=page.page_type,
        layout=page.layout,
        shell_mode=page.shell_mode,
        roles=None if page.roles is None else list(page.roles),
        navigation=page.navigation,
        meta=page.meta,
        sections=[sections[item.section_node_id].declarative for item in page.sections],
    )
    assert reconstructed == AppPageSchema.model_validate(source_page)

    absent_source = dict(source_page)
    for field in ("shell_mode", "roles", "navigation", "meta"):
        absent_source.pop(field)
    absent_result = project_semantic_graph(
        {"pages": [absent_source]},
        graph_id="page-absence",
        version=1,
        scope=result.graph.scope,
        taxonomy_registry=_pinned_registry(),
    )
    absent_page = next(
        payload
        for payload in absent_result.payloads
        if payload.payload_kind.value == "page"
    )
    assert (
        absent_page.shell_mode,
        absent_page.roles,
        absent_page.navigation,
        absent_page.meta,
    ) == (None, None, None, None)


def test_malformed_declared_runtime_section_fails_instead_of_becoming_absent() -> None:
    page = {
        "schema_version": "mozaiks.app_page.v1",
        "name": "broken",
        "route": "/broken",
        "title": "Broken",
        "page_type": "landing",
        "layout": "full-width",
        "sections": [{"id": "hero", "primitive": "PageHeader", "config": {}}],
    }
    with pytest.raises(ProjectionError, match="AppPageSection contract"):
        project_semantic_graph(
            {"pages": [page]},
            graph_id="bad-page",
            version=1,
            scope=_corpus_graph()[0].scope,
            taxonomy_registry=_pinned_registry(),
        )


def test_pricing_catalog_runtime_state_cannot_enter_semantic_closure() -> None:
    page = {
        "schema_version": "mozaiks.app_page.v1",
        "name": "pricing",
        "route": "/pricing",
        "title": "Pricing",
        "page_type": "landing",
        "layout": "full-width",
        "sections": [
            {
                "id": "catalog",
                "primitive": "PricingCatalog",
                "config": {
                    "plans": [
                        {
                            "plan_id": "pro",
                            "label": "Pro",
                            "managed_ai": {"display": "100K AI tokens"},
                            "usage_limits": [
                                {
                                    "label": "AI tokens",
                                    "monthly_limit_display": "100K/month",
                                }
                            ],
                            "pricing": {"display": "$29", "interval": "month"},
                        }
                    ],
                    "groups": [
                        {
                            "group_id": "platform",
                            "label": "Platform",
                            "kind": "subscription",
                            "plan_ids": ["pro"],
                        }
                    ],
                    "add_ons": [
                        {
                            "add_on_id": "priority_review",
                            "label": "Priority review",
                            "price": {"display": "$25", "interval": "one_time"},
                        }
                    ],
                },
            }
        ],
    }
    result = project_semantic_graph(
        {"pages": [page]},
        graph_id="pricing-closure",
        version=1,
        scope=_corpus_graph()[0].scope,
        taxonomy_registry=_pinned_registry(),
    )
    section = next(
        payload for payload in result.payloads if payload.payload_kind.value == "section"
    )
    node = next(node for node in result.graph.nodes if node.node_id == section.node_id)
    assert node.payload_ref.content_digest == section.payload_digest
    plan = derive_compilation_plan(
        graph=result.graph,
        payloads=result.payloads,
        registry=_registry(),
    )
    page_unit = next(unit for unit in plan.units if unit.family_kind == "app_ui_page_schema")
    assert any(source.node_id == section.node_id for source in page_unit.sources)

    attacked = page.copy()
    attacked["sections"] = [
        {
            **page["sections"][0],
            "config": {
                "plans": [
                    {
                        "plan_id": "pro",
                        "label": "Pro",
                        "managed_ai": {
                            "display": "100K AI tokens",
                            "agent_id": "live-agent",
                            "channel": {
                                "websocket": {"connected": True},
                                "wal": [{"envelope_id": "env-1"}],
                            },
                            "model": {"identifier": "live-model"},
                            "checkpoint_path": "/tmp/ag2.chk",
                            "passport": {"tenant_id": "foreign-tenant"},
                        },
                    }
                ]
            },
        }
    ]
    with pytest.raises(ProjectionError, match="AppPageSection contract"):
        project_semantic_graph(
            {"pages": [attacked]},
            graph_id="pricing-smuggling",
            version=1,
            scope=result.graph.scope,
            taxonomy_registry=_pinned_registry(),
        )


def test_page_payload_rejects_custom_route_only_page_type_and_unsafe_route() -> None:
    _graph, payloads = _corpus_graph()
    page = next(payload for payload in payloads if payload.payload_kind.value == "page")
    common = page.model_dump(
        exclude={"payload_schema_version", "payload_kind", "payload_digest"}
    )
    with pytest.raises(ValidationError, match="page_type"):
        build_semantic_payload(PagePayload, **{**common, "page_type": "checkout_success"})
    with pytest.raises(ValidationError, match="safe absolute app route"):
        build_semantic_payload(PagePayload, **{**common, "route": "/bad|route"})


def test_page_unit_has_complete_linked_node_and_edge_footprint() -> None:
    unit = next(unit for unit in _plan().units if unit.family_kind == "app_ui_page_schema")
    assert unit.disposition is PlanDisposition.RENDER
    assert {source.node_id for source in unit.sources} == {
        "mozaiks.page.home",
        "mozaiks.section.hero",
        "mozaiks.section.pricing",
    }
    assert len(unit.edge_sources) == 1
    edge = unit.edge_sources[0]
    assert edge.kind is SemanticEdgeKind.RENDERS
    assert edge.source_node_id == "mozaiks.page.home"
    assert edge.target_node_id == "mozaiks.section.hero"


def test_linked_section_and_internal_edge_changes_selectively_invalidate_page() -> None:
    graph, payloads = _corpus_graph()
    base = derive_compilation_plan(graph=graph, payloads=payloads, registry=_registry())
    section = next(payload for payload in payloads if payload.node_id == "mozaiks.section.hero")
    changed_section = build_semantic_payload(
        SectionPayload,
        node_id=section.node_id,
        payload_version=section.payload_version,
        scope=section.scope,
        section_id=section.section_id,
        title=section.title,
        intent=section.intent,
        declarative=section.declarative.model_copy(
            update={"config": {"title": "Changed welcome"}}
        ),
        entries=section.entries,
    )
    changed_graph, changed_payloads = _replace_payload(graph, payloads, changed_section)
    changed = derive_compilation_plan(
        graph=changed_graph, payloads=changed_payloads, registry=_registry()
    )
    closure = plan_regeneration_closure(base, changed)
    page_unit = next(unit.unit_id for unit in changed.units if unit.family_kind == "app_ui_page_schema")
    assert page_unit in closure.affected

    pricing_edge = SemanticEdge(
        kind=SemanticEdgeKind.RENDERS,
        source_node_id="mozaiks.page.home",
        target_node_id="mozaiks.section.pricing",
    )
    edge_graph = build_semantic_graph_v2(
        graph_id=graph.graph_id,
        version=graph.version,
        scope=graph.scope,
        nodes=graph.nodes,
        edges=(*graph.edges, pricing_edge),
        namespace_grants=graph.namespace_grants,
    )
    edge_plan = derive_compilation_plan(
        graph=edge_graph, payloads=payloads, registry=_registry()
    )
    assert page_unit in plan_regeneration_closure(base, edge_plan).affected


def test_unrelated_workflow_change_leaves_page_and_opaque_module_units_reusable() -> None:
    graph, payloads = _corpus_graph()
    base = derive_compilation_plan(graph=graph, payloads=payloads, registry=_registry())
    workflow = next(payload for payload in payloads if payload.payload_kind.value == "workflow")
    replacement = build_semantic_payload(
        WorkflowPayload,
        node_id=workflow.node_id,
        payload_version=workflow.payload_version,
        scope=workflow.scope,
        workflow_id=workflow.workflow_id,
        description="Changed workflow description",
        startup_mode=workflow.startup_mode,
        topology=workflow.topology,
    )
    changed_graph, changed_payloads = _replace_payload(graph, payloads, replacement)
    changed = derive_compilation_plan(
        graph=changed_graph, payloads=changed_payloads, registry=_registry()
    )
    reusable = set(plan_regeneration_closure(base, changed).reusable)
    page_unit = next(unit.unit_id for unit in changed.units if unit.family_kind == "app_ui_page_schema")
    handler_units = {
        unit.unit_id
        for unit in changed.units
        if unit.family_kind == "module_backend_handler"
    }
    assert page_unit in reusable
    assert handler_units and handler_units <= reusable


def test_layout_registry_declares_truthful_materializer_categories() -> None:
    rows = _registry().families
    expected = {
        ArtifactKind.APP_UI_PAGE_SCHEMA: MaterializerIdentifier.PAGE_SCHEMA_EXECUTOR,
        ArtifactKind.MODULE_MANIFEST: MaterializerIdentifier.MODULE_CONTRACT_EXECUTOR,
        ArtifactKind.WORKFLOW_MANIFEST: MaterializerIdentifier.WORKFLOW_GENERATOR,
        ArtifactKind.APP_DEPLOYMENT_ARTIFACT: MaterializerIdentifier.DOWNLOAD_DEPLOYMENT_RENDERER,
        ArtifactKind.MODULE_BACKEND_HANDLER: MaterializerIdentifier.PRESERVED_OPAQUE,
        ArtifactKind.WORKFLOW_CONFIG: MaterializerIdentifier.PRESERVED_OPAQUE,
        ArtifactKind.WORKFLOW_TOOL: MaterializerIdentifier.PRESERVED_OPAQUE,
    }
    for family, materializer in expected.items():
        matching = [row for row in rows if row.kind is family]
        assert matching
        assert {row.materializer for row in matching} == {materializer}


def test_graph_v2_renderer_binding_pins_family_implementation_and_version() -> None:
    graph, _payloads = _corpus_graph()
    binding = _renderer_binding()
    validate_implementation_binding_against_graph(binding, graph, layout_registry=_registry())
    assert binding.renderer_selections[0].graph_schema_versions == (
        "mozaiks.semantic_graph.v2",
    )
    changed = build_implementation_binding(
        binding_id=binding.binding_id,
        version=binding.version,
        scope=binding.scope,
        semantic_graph_ref=binding.semantic_graph_ref,
        renderer_selections=(
            binding.renderer_selections[0].model_copy(
                update={"implementation_version": "1.0.1"}
            ),
        ),
    )
    assert changed.binding_digest != binding.binding_digest

    mismatched = build_implementation_binding(
        binding_id=binding.binding_id,
        version=binding.version,
        scope=binding.scope,
        semantic_graph_ref=binding.semantic_graph_ref,
        renderer_selections=(
            RendererSelection(
                materializer_id=MaterializerIdentifier.APP_GENERATOR,
                implementation_id="wrong-owner",
                implementation_version="1.0.0",
                artifact_families=(ArtifactKind.APP_UI_PAGE_SCHEMA.value,),
            ),
        ),
    )
    with pytest.raises(ImplementationBindingError, match="layout_registry declares"):
        validate_implementation_binding_against_graph(
            mismatched, graph, layout_registry=_registry()
        )

    v1 = _v1_graph()
    v1_binding = build_implementation_binding(
        binding_id="v1-renderer-binding",
        version=1,
        scope=v1.scope,
        semantic_graph_ref=SemanticGraphRef(
            subject_id=v1.graph_id,
            subject_version=v1.version,
            content_digest=v1.graph_digest,
            scope=v1.scope,
        ),
        renderer_selections=binding.renderer_selections,
    )
    with pytest.raises(ImplementationBindingError, match="require a semantic graph v2"):
        validate_implementation_binding_against_graph(v1_binding, v1)


def test_opaque_artifact_bytes_are_exact_but_greenfield_plan_does_not_claim_them() -> None:
    content = b"def handle(ctx):\n    return {'ok': True}\n"
    digest = hashlib.sha256(content).hexdigest()
    graph, _payloads = _corpus_graph()
    ref = ChildContractRef(
        subject_id="reports-handler",
        subject_version=1,
        content_digest=digest,
        scope=graph.scope,
        artifact_family=ArtifactKind.MODULE_BACKEND_HANDLER.value,
        canonical_relative_path="modules/reports/backend/handler.py",
        contract_schema_version="opaque.utf8.v1",
    )
    artifact = PreservedOpaqueArtifact(contract_ref=ref, content=content)
    assert artifact.content == content
    assert not any(
        unit.disposition is PlanDisposition.PRESERVE_UNOWNED for unit in _plan().units
    )
    with pytest.raises(ValidationError, match="do not match"):
        PreservedOpaqueArtifact(contract_ref=ref, content=content + b"# changed\n")

    empty_ref = ref.model_copy(
        update={
            "subject_id": "empty-handler",
            "content_digest": hashlib.sha256(b"").hexdigest(),
        }
    )
    assert PreservedOpaqueArtifact(contract_ref=empty_ref, content=b"").content == b""


def test_unsupported_renderer_families_remain_explicit_typed_gaps() -> None:
    plan = _plan()
    renderer_blocked = {
        gap.family_kind
        for gap in plan.gaps
        if gap.code
        in {
            PlanGapCode.RENDERER_INPUT_UNDECLARED,
            PlanGapCode.RENDERER_INPUT_INCOMPLETE,
        }
    }
    assert {
        "module_manifest",
        "app_subscription_config",
        "workflow_manifest",
    } <= renderer_blocked
    # This corpus declares every optional family absent while pinning auth,
    # integration, and workflow payloads — contradictory selection evidence.
    # Slice 5D-0B2A selection honesty therefore keeps app_manifest a typed
    # incomplete gap here; the honest closure renders only on the
    # selection-consistent B2A fixture.
    assert any(
        gap.family_kind == "app_manifest"
        and gap.code is PlanGapCode.RENDERER_INPUT_INCOMPLETE
        for gap in plan.gaps
    )
    assert not any(
        unit.disposition is PlanDisposition.AGENT_AUTHOR
        and unit.family_kind in renderer_blocked | {"app_manifest"}
        for unit in plan.units
    )


def test_contract_identity_is_stable_across_processes() -> None:
    expected = f"{_plan().plan_digest}\n{_renderer_binding().binding_digest}"
    probe = (
        "from tests.test_renderer_input_closure import _plan, _renderer_binding\n"
        "print(_plan().plan_digest)\n"
        "print(_renderer_binding().binding_digest)\n"
    )
    completed = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == expected
