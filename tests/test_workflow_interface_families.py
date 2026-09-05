"""Workflow-interface compilation families: module_interface.yaml + workflow_registry.json.

Proofs that the two new families are exact deterministic projections of the
#478 module-workflow capability semantics: payload-driven source locality,
node-level canonical event identity pinned through ``taxonomy_sources``,
binding-authorized deterministic rendering, canonical plan-authority
integration, and byte-for-byte reuse across refinement when nothing a unit
depends on changed.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from mozaiksai.core.runtime.app.layout_registry import (
    MaterializerIdentifier,
    PathScope,
    build_app_layout_registry,
)
from mozaiksai.core.semantics.binding import (
    RendererSelection,
    build_implementation_binding,
)
from mozaiksai.core.semantics.compilation_plan import (
    CompilationPlan,
    CompilationScopeSelection,
    derive_compilation_plan,
    plan_regeneration_closure,
)
from mozaiksai.core.semantics.materialization import (
    PAGE_SCHEMA_RENDERER_IMPLEMENTATION_ID,
    PAGE_SCHEMA_RENDERER_IMPLEMENTATION_VERSION,
    materialize_plan,
    rematerialize_plan,
)
from mozaiksai.core.semantics.payloads import (
    ModuleActionRef,
    SemanticPayloadBase,
    build_semantic_payload,
)
from mozaiksai.core.semantics.plan_authority import (
    PlanAuthorityError,
    build_compilation_plan_authority_inputs,
    validate_compilation_plan_against_authority,
)
from mozaiksai.core.semantics.refs import SemanticGraphRef
from mozaiksai.core.semantics.workflow_interface_materialization import (
    WORKFLOW_INTERFACE_FAMILIES,
    WORKFLOW_INTERFACE_RENDERER_IMPLEMENTATION_ID,
    WORKFLOW_INTERFACE_RENDERER_IMPLEMENTATION_VERSION,
    WorkflowInterfaceMaterializationError,
)
from tests.test_workflow_capability_semantics import (
    _ACTION_CREATE,
    _ACTION_GET,
    _BINDING_RESULT,
    _CAPABILITY,
    _EVENT,
    _MODULE,
    _RESULT,
    _RESULT_ALT,
    _WORKFLOW,
    _build_graph,
    _fixture_payloads,
    _rebuilt_action,
    _result,
    _rival_workflow_and_capability,
)

ROOT = Path(__file__).resolve().parents[1]

_INTERFACE_FAMILY = "workflow_module_interface"
_REGISTRY_FAMILY = "app_workflow_registry"

# Golden re-pinned once in correction round 1: capability-owned results were
# previously omitted unless a commit binding referenced them — the interface
# now projects EVERY capability-owned WORKFLOW_RESULT (committed or advisory)
# in an explicit `results` block. This re-pin fixes missing semantic output
# projection; no unrelated plan/unit identity was re-pinned.
_GOLDEN_MODULE_INTERFACE = """schema_version: mozaiks.module_interface.v2
workflow_id: analyze_document
capabilities:
- capability_id: documents.analysis
  description: Analyze a created document
  results:
  - result_id: analysis_result
    description: One analysis of one document
  bindings:
  - role: commits_result_through_action
    module_id: documents
    action_node_id: mozaiks.action.documents_store_analysis
    workflow_result_id: analysis_result
  - role: consumes_action
    module_id: documents
    action_node_id: mozaiks.action.documents_get_content
  - role: triggered_by_event
    event_type: domain.documents.created
"""

_GOLDEN_WORKFLOW_REGISTRY = """{
  "schema_version": "mozaiks.app_workflow_registry.v1",
  "workflows": [
    {
      "workflow_id": "analyze_document",
      "startup_mode": null,
      "capabilities": [
        {
          "capability_id": "documents.analysis",
          "event_triggers": [
            "domain.documents.created"
          ]
        }
      ]
    }
  ]
}
"""


def _scope_selection() -> CompilationScopeSelection:
    return CompilationScopeSelection(workflow_manifest_scope=PathScope.WORKSPACE_ROOT)


def _derive(payloads: dict[str, SemanticPayloadBase], *, graph=None):
    resolved_graph = _build_graph(payloads) if graph is None else graph
    plan = derive_compilation_plan(
        graph=resolved_graph,
        payloads=tuple(payloads.values()),
        registry=build_app_layout_registry(()),
        scope_selection=_scope_selection(),
    )
    return resolved_graph, plan


def _family_units(plan: CompilationPlan) -> dict[str, object]:
    units = {}
    for unit in plan.units:
        if unit.family_kind in WORKFLOW_INTERFACE_FAMILIES and unit.sources:
            units[unit.family_kind] = unit
    return units


def _binding(graph):
    return build_implementation_binding(
        binding_id="wi_families",
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
                implementation_id=PAGE_SCHEMA_RENDERER_IMPLEMENTATION_ID,
                implementation_version=PAGE_SCHEMA_RENDERER_IMPLEMENTATION_VERSION,
                artifact_families=("app_ui_page_schema",),
            ),
            RendererSelection(
                materializer_id=MaterializerIdentifier.WORKFLOW_INTERFACE_EXECUTOR,
                implementation_id=WORKFLOW_INTERFACE_RENDERER_IMPLEMENTATION_ID,
                implementation_version=WORKFLOW_INTERFACE_RENDERER_IMPLEMENTATION_VERSION,
                artifact_families=tuple(sorted(WORKFLOW_INTERFACE_FAMILIES)),
            ),
        ),
    )


def _materialize(payloads: dict[str, SemanticPayloadBase]):
    graph, plan = _derive(payloads)
    authority = build_compilation_plan_authority_inputs(
        graph=graph,
        payloads=tuple(payloads.values()),
        registry=build_app_layout_registry(()),
        scope_selection=_scope_selection(),
    )
    bundle = materialize_plan(
        plan=plan,
        authority_inputs=authority,
        graph=graph,
        payloads=tuple(payloads.values()),
        binding=_binding(graph),
        layout_registry=build_app_layout_registry(()),
    )
    return graph, plan, authority, bundle


def test_capability_closure_derives_exact_payload_driven_footprints() -> None:
    """Interface and registry units pin exactly the payload closure their
    bytes consume — never edge-hop spillover, never ACTION payload bodies —
    plus the bound event's canonical node-level identity."""

    _graph, plan = _derive(_fixture_payloads())
    units = _family_units(plan)
    interface = units[_INTERFACE_FAMILY]
    registry = units[_REGISTRY_FAMILY]

    assert [s.node_id for s in interface.sources] == sorted(
        [
            _MODULE,
            _WORKFLOW,
            _CAPABILITY,
            "mozaiks.workflow_capability_binding.analysis_on_created",
            "mozaiks.workflow_capability_binding.analysis_reads_content",
            _BINDING_RESULT,
            _RESULT,
        ]
    )
    assert [(t.node_id, t.category, t.identifier) for t in interface.taxonomy_sources] == [
        (_EVENT, "event", "domain.documents.created")
    ]
    # The registry excludes module/action/result facts entirely: module churn
    # can never reprint it.
    assert [s.node_id for s in registry.sources] == sorted(
        [
            _WORKFLOW,
            _CAPABILITY,
            "mozaiks.workflow_capability_binding.analysis_on_created",
        ]
    )
    assert [(t.node_id, t.identifier) for t in registry.taxonomy_sources] == [
        (_EVENT, "domain.documents.created")
    ]
    assert not [g for g in plan.gaps if g.family_kind in WORKFLOW_INTERFACE_FAMILIES]
    assert interface.outputs[0].path == "workflows/analyze_document/module_interface.yaml"
    assert registry.outputs[0].path == "workflows/workflow_registry.json"


def test_rendered_bytes_are_the_exact_golden_projections() -> None:
    _graph, _plan, _authority, bundle = _materialize(_fixture_payloads())
    by_path = {output.path: output for output in bundle.outputs}
    interface = by_path["workflows/analyze_document/module_interface.yaml"]
    registry = by_path["workflows/workflow_registry.json"]
    assert interface.content.decode("utf-8") == _GOLDEN_MODULE_INTERFACE
    assert registry.content.decode("utf-8") == _GOLDEN_WORKFLOW_REGISTRY
    assert interface.origin == "rendered"
    assert registry.origin == "rendered"


def test_unrelated_payload_mutations_never_move_unit_digests() -> None:
    """Action bodies and producer descriptions are NOT interface authority:
    the binding payload pins action identity as node ids, so unrelated
    action/producer changes leave both units' reuse signatures untouched."""

    base_payloads = _fixture_payloads()
    _graph, base_plan = _derive(base_payloads)
    base_units = _family_units(base_plan)

    for mutated_action, overrides in (
        (_ACTION_GET, {"description": "Read one document's content, verbatim"}),
        (_ACTION_CREATE, {"description": "Create exactly one document"}),
    ):
        payloads = dict(base_payloads)
        payloads[mutated_action] = _rebuilt_action(payloads, mutated_action, **overrides)
        _mutated_graph, plan = _derive(payloads)
        units = _family_units(plan)
        for family in (_INTERFACE_FAMILY, _REGISTRY_FAMILY):
            assert units[family].unit_digest == base_units[family].unit_digest, (
                mutated_action,
                family,
            )


def test_meaningful_mutations_move_exactly_the_dependent_units() -> None:
    """Capability facts move both units; module identity and committed
    results move only the interface; the registry never sees them."""

    base_payloads = _fixture_payloads()
    _graph, base_plan = _derive(base_payloads)
    base_units = _family_units(base_plan)

    # Capability description is pinned by both families.
    payloads = dict(base_payloads)
    capability = payloads[_CAPABILITY]
    payloads[_CAPABILITY] = build_semantic_payload(
        type(capability),
        node_id=capability.node_id,
        payload_version=capability.payload_version,
        scope=capability.scope,
        capability_id=capability.capability_id,
        description="Analyze a created document, thoroughly",
        workflow_node_id=capability.workflow_node_id,
    )
    _mutated, plan = _derive(payloads)
    units = _family_units(plan)
    assert units[_INTERFACE_FAMILY].unit_digest != base_units[_INTERFACE_FAMILY].unit_digest
    assert units[_REGISTRY_FAMILY].unit_digest != base_units[_REGISTRY_FAMILY].unit_digest

    # The committed result is interface authority only.
    payloads = dict(base_payloads)
    result = payloads[_RESULT]
    payloads[_RESULT] = build_semantic_payload(
        type(result),
        node_id=result.node_id,
        payload_version=result.payload_version,
        scope=result.scope,
        result_id="analysis_verdict",
        description=result.description,
        workflow_capability_node_id=result.workflow_capability_node_id,
    )
    _mutated, plan = _derive(payloads)
    units = _family_units(plan)
    assert units[_INTERFACE_FAMILY].unit_digest != base_units[_INTERFACE_FAMILY].unit_digest
    assert units[_REGISTRY_FAMILY].unit_digest == base_units[_REGISTRY_FAMILY].unit_digest

    # The module's typed identity is interface authority only.
    payloads = dict(base_payloads)
    module = payloads[_MODULE]
    payloads[_MODULE] = build_semantic_payload(
        type(module),
        node_id=module.node_id,
        payload_version=module.payload_version,
        scope=module.scope,
        module_id="document_store",
        description=module.description,
    )
    _mutated, plan = _derive(payloads)
    units = _family_units(plan)
    assert units[_INTERFACE_FAMILY].unit_digest != base_units[_INTERFACE_FAMILY].unit_digest
    assert units[_REGISTRY_FAMILY].unit_digest == base_units[_REGISTRY_FAMILY].unit_digest


def test_canonical_event_identity_change_moves_both_units() -> None:
    """The trigger event's node-level canonical identity is pinned unit
    identity: renaming it moves both reuse signatures even though no payload
    digest changed."""

    payloads = _fixture_payloads()
    base_graph = _build_graph(payloads)
    renamed_payloads = dict(payloads)
    renamed_payloads[_ACTION_CREATE] = _rebuilt_action(
        renamed_payloads, _ACTION_CREATE, emits=("domain.documents.registered",)
    )
    renamed_graph = _build_graph(
        renamed_payloads, event_identity="domain.documents.registered"
    )
    _same, base_plan = _derive(payloads, graph=base_graph)
    _same2, renamed_plan = _derive(renamed_payloads, graph=renamed_graph)
    base_units = _family_units(base_plan)
    renamed_units = _family_units(renamed_plan)
    for family in (_INTERFACE_FAMILY, _REGISTRY_FAMILY):
        assert renamed_units[family].unit_digest != base_units[family].unit_digest
        assert renamed_units[family].taxonomy_sources[0].identifier == (
            "domain.documents.registered"
        )


def test_workflow_without_capabilities_renders_truthful_empty_projections() -> None:
    payloads = {
        node_id: payload
        for node_id, payload in _fixture_payloads().items()
        if not node_id.startswith(
            ("mozaiks.workflow_capability", "mozaiks.workflow_result")
        )
    }
    _graph, _plan, _authority, bundle = _materialize(payloads)
    by_path = {output.path: output for output in bundle.outputs}
    interface = by_path["workflows/analyze_document/module_interface.yaml"].content
    registry = by_path["workflows/workflow_registry.json"].content
    assert b"capabilities: []" in interface
    assert b'"capabilities": []' in registry


def test_materialization_requires_the_exact_renderer_selection() -> None:
    payloads = _fixture_payloads()
    graph, plan = _derive(payloads)
    authority = build_compilation_plan_authority_inputs(
        graph=graph,
        payloads=tuple(payloads.values()),
        registry=build_app_layout_registry(()),
        scope_selection=_scope_selection(),
    )
    without_selection = build_implementation_binding(
        binding_id="wi_missing",
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
                implementation_id=PAGE_SCHEMA_RENDERER_IMPLEMENTATION_ID,
                implementation_version=PAGE_SCHEMA_RENDERER_IMPLEMENTATION_VERSION,
                artifact_families=("app_ui_page_schema",),
            ),
        ),
    )
    with pytest.raises(
        WorkflowInterfaceMaterializationError, match="no renderer selection"
    ):
        materialize_plan(
            plan=plan,
            authority_inputs=authority,
            graph=graph,
            payloads=tuple(payloads.values()),
            binding=without_selection,
            layout_registry=build_app_layout_registry(()),
        )

    partial = build_implementation_binding(
        binding_id="wi_partial",
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
                implementation_id=PAGE_SCHEMA_RENDERER_IMPLEMENTATION_ID,
                implementation_version=PAGE_SCHEMA_RENDERER_IMPLEMENTATION_VERSION,
                artifact_families=("app_ui_page_schema",),
            ),
            RendererSelection(
                materializer_id=MaterializerIdentifier.WORKFLOW_INTERFACE_EXECUTOR,
                implementation_id=WORKFLOW_INTERFACE_RENDERER_IMPLEMENTATION_ID,
                implementation_version=WORKFLOW_INTERFACE_RENDERER_IMPLEMENTATION_VERSION,
                artifact_families=("app_workflow_registry",),
            ),
        ),
    )
    with pytest.raises(
        WorkflowInterfaceMaterializationError, match="exactly its supported family set"
    ):
        materialize_plan(
            plan=plan,
            authority_inputs=authority,
            graph=graph,
            payloads=tuple(payloads.values()),
            binding=partial,
            layout_registry=build_app_layout_registry(()),
        )


def test_rematerialize_reuses_untouched_interface_bytes() -> None:
    """An unrelated action-body change between revisions leaves both units
    reusable: refinement copies the exact prior bytes instead of re-rendering."""

    base_payloads = _fixture_payloads()
    base_graph, base_plan, base_authority, base_bundle = _materialize(base_payloads)

    successor_payloads = dict(base_payloads)
    successor_payloads[_ACTION_GET] = _rebuilt_action(
        successor_payloads, _ACTION_GET, description="Read one document, cold"
    )
    successor_graph = _build_graph(successor_payloads)
    successor_plan = derive_compilation_plan(
        graph=successor_graph,
        payloads=tuple(successor_payloads.values()),
        registry=build_app_layout_registry(()),
        scope_selection=_scope_selection(),
    )
    successor_authority = build_compilation_plan_authority_inputs(
        graph=successor_graph,
        payloads=tuple(successor_payloads.values()),
        registry=build_app_layout_registry(()),
        scope_selection=_scope_selection(),
    )
    closure = plan_regeneration_closure(base_plan, successor_plan)
    interface_unit = _family_units(successor_plan)[_INTERFACE_FAMILY]
    registry_unit = _family_units(successor_plan)[_REGISTRY_FAMILY]
    assert interface_unit.unit_id in closure.reusable
    assert registry_unit.unit_id in closure.reusable

    bundle = rematerialize_plan(
        base_plan=base_plan,
        base_authority_inputs=base_authority,
        successor_plan=successor_plan,
        successor_authority_inputs=successor_authority,
        graph=successor_graph,
        payloads=tuple(successor_payloads.values()),
        binding=_binding(successor_graph),
        layout_registry=build_app_layout_registry(()),
        base_bundle=base_bundle,
    )
    by_path = {output.path: output for output in bundle.outputs}
    for path in (
        "workflows/analyze_document/module_interface.yaml",
        "workflows/workflow_registry.json",
    ):
        assert by_path[path].origin == "reused"
        base_output = next(o for o in base_bundle.outputs if o.path == path)
        assert by_path[path].content == base_output.content


def test_forged_taxonomy_identity_rejects_against_canonical_authority() -> None:
    """A plan whose pinned canonical event identity was rewritten (and
    re-digested) is not the canonical derivation of its authorities."""

    payloads = _fixture_payloads()
    graph, plan = _derive(payloads)
    authority = build_compilation_plan_authority_inputs(
        graph=graph,
        payloads=tuple(payloads.values()),
        registry=build_app_layout_registry(()),
        scope_selection=_scope_selection(),
    )
    validate_compilation_plan_against_authority(plan, authority)

    from mozaiksai.core.semantics.canonical import canonical_digest

    document = plan.model_dump(mode="json")
    payload = plan.canonical_payload(include_digest=False)
    for doc in (document, payload):
        for unit in doc["units"]:
            for taxonomy in unit.get("taxonomy_sources", ()):
                taxonomy["identifier"] = "domain.documents.hijacked"
    document["plan_digest"] = canonical_digest(payload)
    forged = CompilationPlan.model_validate(document)
    with pytest.raises(PlanAuthorityError):
        validate_compilation_plan_against_authority(forged, authority)


def test_cross_process_bytes_and_digests_are_identical() -> None:
    probe = (
        "from tests.test_workflow_interface_families import (\n"
        "    _fixture_payloads, _materialize)\n"
        "import hashlib\n"
        "_g, plan, _a, bundle = _materialize(_fixture_payloads())\n"
        "digest = hashlib.sha256()\n"
        "for output in bundle.outputs:\n"
        "    if 'workflow' in output.path:\n"
        "        digest.update(output.content)\n"
        "print(plan.plan_digest, digest.hexdigest())\n"
    )
    completed = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        timeout=600,
    )
    assert completed.returncode == 0, completed.stderr
    import hashlib

    _graph, plan, _authority, bundle = _materialize(_fixture_payloads())
    digest = hashlib.sha256()
    for output in bundle.outputs:
        if "workflow" in output.path:
            digest.update(output.content)
    assert completed.stdout.split() == [plan.plan_digest, digest.hexdigest()]


# ---------------------------------------------------------------------------
# Correction round 1 — defect 1: ALL capability-owned results are interface
# semantics, whether or not a commit binding delivers them.
# ---------------------------------------------------------------------------


def _rebuilt_result(payloads, node_id, **overrides):
    base = payloads[node_id]
    fields = {
        "node_id": base.node_id,
        "payload_version": base.payload_version,
        "scope": base.scope,
        "result_id": base.result_id,
        "description": base.description,
        "workflow_capability_node_id": base.workflow_capability_node_id,
    }
    fields.update(overrides)
    return build_semantic_payload(type(base), **fields)


def _advisory_result_payloads() -> dict[str, SemanticPayloadBase]:
    """Canonical fixture plus one advisory (uncommitted) result R2."""
    payloads = dict(_fixture_payloads())
    payloads[_RESULT_ALT] = _result(_RESULT_ALT, result_id="analysis_summary")
    return payloads


def _interface_state(payloads):
    graph, plan = _derive(payloads)
    units = _family_units(plan)
    authority = build_compilation_plan_authority_inputs(
        graph=graph,
        payloads=tuple(payloads.values()),
        registry=build_app_layout_registry(()),
        scope_selection=_scope_selection(),
    )
    bundle = materialize_plan(
        plan=plan,
        authority_inputs=authority,
        graph=graph,
        payloads=tuple(payloads.values()),
        binding=_binding(graph),
        layout_registry=build_app_layout_registry(()),
    )
    interface_bytes = next(
        o.content
        for o in bundle.outputs
        if o.path == "workflows/analyze_document/module_interface.yaml"
    )
    return units, interface_bytes


def test_advisory_result_is_projected_without_any_commit_binding() -> None:
    """An uncommitted capability-owned result appears in the interface, is
    pinned in the unit footprint, and stays absent from the registry."""

    payloads = _advisory_result_payloads()
    _graph, plan = _derive(payloads)
    units = _family_units(plan)
    interface = units[_INTERFACE_FAMILY]
    registry = units[_REGISTRY_FAMILY]
    assert _RESULT_ALT in {s.node_id for s in interface.sources}
    assert _RESULT_ALT not in {s.node_id for s in registry.sources}

    _units, interface_bytes = _interface_state(payloads)
    text = interface_bytes.decode("utf-8")
    assert "result_id: analysis_summary" in text
    assert text.index("results:") < text.index("bindings:")


def test_result_locality_matrix() -> None:
    """The complete advisory-result mutation matrix: every owned-result fact
    moves the interface (digest AND bytes); nothing else does, and the
    registry never moves for any result-only change."""

    base_payloads = _advisory_result_payloads()
    base_units, base_bytes = _interface_state(base_payloads)

    def _changed(payloads, *, registry_must_hold: bool = True):
        units, rendered = _interface_state(payloads)
        assert (
            units[_INTERFACE_FAMILY].unit_digest
            != base_units[_INTERFACE_FAMILY].unit_digest
        )
        assert rendered != base_bytes
        if registry_must_hold:
            assert (
                units[_REGISTRY_FAMILY].unit_digest
                == base_units[_REGISTRY_FAMILY].unit_digest
            )
        return rendered

    # 2. advisory result_id rename
    payloads = dict(base_payloads)
    payloads[_RESULT_ALT] = _rebuilt_result(
        payloads, _RESULT_ALT, result_id="analysis_digest"
    )
    rendered = _changed(payloads)
    assert b"analysis_digest" in rendered

    # 3. advisory description change (description is rendered)
    payloads = dict(base_payloads)
    payloads[_RESULT_ALT] = _rebuilt_result(
        payloads, _RESULT_ALT, description="One short summary"
    )
    rendered = _changed(payloads)
    assert b"One short summary" in rendered

    # 4. advisory result deletion
    payloads = {k: v for k, v in base_payloads.items() if k != _RESULT_ALT}
    rendered = _changed(payloads)
    assert b"analysis_summary" not in rendered

    # 5. add another uncommitted result R3
    payloads = dict(base_payloads)
    payloads["mozaiks.workflow_result.analysis_notes"] = _result(
        "mozaiks.workflow_result.analysis_notes", result_id="analysis_notes"
    )
    rendered = _changed(payloads)
    assert b"analysis_notes" in rendered

    # 6. a result owned by an unrelated workflow's capability leaves this
    # workflow's interface untouched
    rival_workflow, rival_capability = _rival_workflow_and_capability()
    payloads = dict(base_payloads)
    payloads[rival_workflow.node_id] = rival_workflow
    payloads[rival_capability.node_id] = rival_capability
    payloads["mozaiks.workflow_result.rival_verdict"] = _result(
        "mozaiks.workflow_result.rival_verdict",
        result_id="rival_verdict",
        capability_node_id=rival_capability.node_id,
    )
    _graph, rival_plan = _derive(payloads)
    analyze_unit = next(
        u
        for u in rival_plan.units
        if u.family_kind == _INTERFACE_FAMILY
        and ("workflow_id", "analyze_document") in u.placeholder_values
    )
    assert analyze_unit.unit_digest == base_units[_INTERFACE_FAMILY].unit_digest

    # 7. removing R1's commit binding keeps R1 listed; only bindings change
    payloads = {k: v for k, v in base_payloads.items() if k != _BINDING_RESULT}
    rendered = _changed(payloads)
    text = rendered.decode("utf-8")
    assert "result_id: analysis_result" in text
    assert "commits_result_through_action" not in text

    # 8. committing the advisory result changes bindings, not result identity
    payloads = dict(base_payloads)
    payloads["mozaiks.workflow_capability_binding.analysis_stores_summary"] = (
        build_semantic_payload(
            type(payloads[_BINDING_RESULT]),
            node_id="mozaiks.workflow_capability_binding.analysis_stores_summary",
            payload_version=1,
            scope=payloads[_BINDING_RESULT].scope,
            binding_role=payloads[_BINDING_RESULT].binding_role,
            workflow_capability_node_id=_CAPABILITY,
            module_action=payloads[_BINDING_RESULT].module_action,
            workflow_result_node_id=_RESULT_ALT,
        )
    )
    rendered = _changed(payloads)
    text = rendered.decode("utf-8")
    assert text.count("- result_id: analysis_summary") == 1
    assert "workflow_result_id: analysis_summary" in text

    # 9. fan-out: one result committed through two actions appears once in
    # results with two explicit commit bindings
    payloads = dict(base_payloads)
    payloads["mozaiks.workflow_capability_binding.analysis_stores_twice"] = (
        build_semantic_payload(
            type(payloads[_BINDING_RESULT]),
            node_id="mozaiks.workflow_capability_binding.analysis_stores_twice",
            payload_version=1,
            scope=payloads[_BINDING_RESULT].scope,
            binding_role=payloads[_BINDING_RESULT].binding_role,
            workflow_capability_node_id=_CAPABILITY,
            module_action=ModuleActionRef(
                module_node_id=_MODULE, action_node_id=_ACTION_CREATE
            ),
            workflow_result_node_id=_RESULT,
        )
    )
    rendered = _changed(payloads)
    text = rendered.decode("utf-8")
    assert text.count("- result_id: analysis_result") == 1
    assert text.count("workflow_result_id: analysis_result") == 2


def test_result_footprint_forgeries_reject_against_authority() -> None:
    """Removing, adding, or substituting a result source (re-digested) is not
    the canonical derivation; removing the advisory result from the graph
    truthfully produces a NEW canonical interface identity."""

    from mozaiksai.core.semantics.canonical import canonical_digest

    payloads = _advisory_result_payloads()
    graph, plan = _derive(payloads)
    authority = build_compilation_plan_authority_inputs(
        graph=graph,
        payloads=tuple(payloads.values()),
        registry=build_app_layout_registry(()),
        scope_selection=_scope_selection(),
    )
    validate_compilation_plan_against_authority(plan, authority)
    interface_unit_id = _family_units(plan)[_INTERFACE_FAMILY].unit_id
    rival_digest = payloads[_RESULT].payload_digest

    def _forge(edit) -> CompilationPlan:
        document = plan.model_dump(mode="json")
        payload = plan.canonical_payload(include_digest=False)
        for doc in (document, payload):
            unit = next(u for u in doc["units"] if u["unit_id"] == interface_unit_id)
            edit(unit)
        document["plan_digest"] = canonical_digest(payload)
        return CompilationPlan.model_validate(document)

    def _drop_advisory(unit) -> None:
        unit["sources"] = [s for s in unit["sources"] if s["node_id"] != _RESULT_ALT]

    def _add_unrelated(unit) -> None:
        unit["sources"] = sorted(
            [*unit["sources"], {"node_id": "mozaiks.event.documents_created",
                                "payload_digest": rival_digest}],
            key=lambda s: s["node_id"],
        )

    def _substitute(unit) -> None:
        for source in unit["sources"]:
            if source["node_id"] == _RESULT_ALT:
                source["payload_digest"] = rival_digest

    for edit in (_drop_advisory, _add_unrelated, _substitute):
        with pytest.raises(PlanAuthorityError):
            validate_compilation_plan_against_authority(_forge(edit), authority)

    # Truthful removal: the canonical rederivation without the advisory
    # result is a different interface identity, not a rejection.
    without = {k: v for k, v in payloads.items() if k != _RESULT_ALT}
    _g2, plan_without = _derive(without)
    assert (
        _family_units(plan_without)[_INTERFACE_FAMILY].unit_digest
        != _family_units(plan)[_INTERFACE_FAMILY].unit_digest
    )


def test_advisory_result_changes_rematerialize_interface_and_reuse_registry() -> None:
    """Advisory-result mutation, deletion, and addition each re-render the
    interface while the registry (and an unrelated workflow change keeps the
    interface) reuse exact prior bytes."""

    base_payloads = _advisory_result_payloads()
    base_graph, base_plan, base_authority, base_bundle = _materialize(base_payloads)

    def _successors():
        mutated = dict(base_payloads)
        mutated[_RESULT_ALT] = _rebuilt_result(
            mutated, _RESULT_ALT, description="One revised summary"
        )
        deleted = {k: v for k, v in base_payloads.items() if k != _RESULT_ALT}
        added = dict(base_payloads)
        added["mozaiks.workflow_result.analysis_notes"] = _result(
            "mozaiks.workflow_result.analysis_notes", result_id="analysis_notes"
        )
        return {"mutated": mutated, "deleted": deleted, "added": added}

    for label, successor_payloads in _successors().items():
        successor_graph = _build_graph(successor_payloads)
        successor_plan = derive_compilation_plan(
            graph=successor_graph,
            payloads=tuple(successor_payloads.values()),
            registry=build_app_layout_registry(()),
            scope_selection=_scope_selection(),
        )
        successor_authority = build_compilation_plan_authority_inputs(
            graph=successor_graph,
            payloads=tuple(successor_payloads.values()),
            registry=build_app_layout_registry(()),
            scope_selection=_scope_selection(),
        )
        bundle = rematerialize_plan(
            base_plan=base_plan,
            base_authority_inputs=base_authority,
            successor_plan=successor_plan,
            successor_authority_inputs=successor_authority,
            graph=successor_graph,
            payloads=tuple(successor_payloads.values()),
            binding=_binding(successor_graph),
            layout_registry=build_app_layout_registry(()),
            base_bundle=base_bundle,
        )
        by_path = {o.path: o for o in bundle.outputs}
        interface = by_path["workflows/analyze_document/module_interface.yaml"]
        registry = by_path["workflows/workflow_registry.json"]
        assert interface.origin == "rendered", label
        base_interface = next(
            o
            for o in base_bundle.outputs
            if o.path == "workflows/analyze_document/module_interface.yaml"
        )
        assert interface.content != base_interface.content, label
        assert registry.origin == "reused", label


# ---------------------------------------------------------------------------
# Correction round 1 — defect 2: exact workflow/path identity binding at the
# renderer itself, independent of canonical plan authority and output closure.
# ---------------------------------------------------------------------------


def _projected_interface_unit_and_input():
    from mozaiksai.core.semantics.materialization import (
        project_workflow_interface_render_input,
    )

    payloads = _fixture_payloads()
    _graph, plan = _derive(payloads)
    unit = _family_units(plan)[_INTERFACE_FAMILY]
    render_input = project_workflow_interface_render_input(
        unit=unit, payload_by_node={p.node_id: p for p in payloads.values()}
    )
    return payloads, plan, unit, render_input


def test_renderer_binds_unit_input_and_path_to_one_exact_workflow() -> None:
    """Direct-renderer attack matrix: only the exact canonical path of the
    unit's exact workflow renders; every contradictory identity fails closed."""

    from mozaiksai.core.semantics.compilation_plan import PlanOutput
    from mozaiksai.core.semantics.workflow_interface_materialization import (
        render_workflow_interface_unit,
    )

    _payloads, _plan, unit, render_input = _projected_interface_unit_and_input()
    assert render_workflow_interface_unit(unit=unit, render_input=render_input)

    hostile_paths = (
        "workflows/other_workflow/module_interface.yaml",
        "workflows/analyze_document/../other_workflow/module_interface.yaml",
        "workflows//analyze_document/module_interface.yaml",
        "workflows/analyze_document/workflow_registry.json",
        "workflows/Analyze_Document/module_interface.yaml",
        "module_interface.yaml",  # workspace-scoped unit, relative path
    )
    for path in hostile_paths:
        forged = unit.model_copy(
            update={
                "outputs": (
                    PlanOutput.model_construct(
                        path_scope=unit.outputs[0].path_scope, path=path
                    ),
                )
            }
        )
        with pytest.raises(
            WorkflowInterfaceMaterializationError, match="exact canonical path"
        ):
            render_workflow_interface_unit(unit=forged, render_input=render_input)

    # A render input naming a different workflow than the unit's exact
    # identity fails before any path comparison.
    renamed_input = render_input.model_copy(update={"workflow_id": "other_workflow"})
    with pytest.raises(
        WorkflowInterfaceMaterializationError, match="exact workflow identity"
    ):
        render_workflow_interface_unit(unit=unit, render_input=renamed_input)

    # A unit stripped of its workflow identity cannot render at all.
    anonymous = unit.model_copy(update={"placeholder_values": ()})
    with pytest.raises(
        WorkflowInterfaceMaterializationError,
        match="canonical workflow instance identity",
    ):
        render_workflow_interface_unit(unit=anonymous, render_input=render_input)


def test_renderer_rejects_foreign_workflow_sources_and_keeps_twin_row() -> None:
    """Workflow B's projected facts cannot enter workflow A's unit, and the
    legitimate workflow-relative twin row still renders."""

    from mozaiksai.core.semantics.materialization import (
        project_workflow_interface_render_input,
    )
    from mozaiksai.core.semantics.workflow_interface_materialization import (
        render_workflow_interface_unit,
    )

    payloads, _plan, unit, _render_input = _projected_interface_unit_and_input()

    # Workflow B: rename the workflow and re-derive; its projected input does
    # not bind workflow A's pinned sources.
    foreign_payloads = dict(payloads)
    workflow = foreign_payloads[_WORKFLOW]
    foreign_payloads[_WORKFLOW] = build_semantic_payload(
        type(workflow),
        node_id=workflow.node_id,
        payload_version=workflow.payload_version,
        scope=workflow.scope,
        workflow_id="other_workflow",
        description=workflow.description,
        startup_mode=workflow.startup_mode,
        topology=workflow.topology,
    )
    _foreign_graph, foreign_plan = _derive(foreign_payloads)
    foreign_unit = _family_units(foreign_plan)[_INTERFACE_FAMILY]
    foreign_input = project_workflow_interface_render_input(
        unit=foreign_unit,
        payload_by_node={p.node_id: p for p in foreign_payloads.values()},
    )
    with pytest.raises(WorkflowInterfaceMaterializationError):
        render_workflow_interface_unit(unit=unit, render_input=foreign_input)

    # The workflow-relative twin row remains a legitimate rendering target.
    relative_plan = derive_compilation_plan(
        graph=_build_graph(payloads),
        payloads=tuple(payloads.values()),
        registry=build_app_layout_registry(()),
        scope_selection=CompilationScopeSelection(
            workflow_manifest_scope=PathScope.WORKFLOW_RELATIVE
        ),
    )
    relative_unit = next(
        u
        for u in relative_plan.units
        if u.family_kind == _INTERFACE_FAMILY and u.sources
    )
    assert relative_unit.outputs[0].path == "module_interface.yaml"
    relative_input = project_workflow_interface_render_input(
        unit=relative_unit,
        payload_by_node={p.node_id: p for p in payloads.values()},
    )
    content = render_workflow_interface_unit(
        unit=relative_unit, render_input=relative_input
    )
    assert b"workflow_id: analyze_document" in content


def test_output_closure_and_authority_protect_paths_independently() -> None:
    """Three distinct protections: the renderer's exact-path contract (tested
    above), canonical plan authority on a path-forged plan, and the output
    closure on missing/extra/duplicate/foreign outputs."""

    from mozaiksai.core.semantics.canonical import canonical_digest
    from mozaiksai.core.semantics.materialization import (
        MaterializationError,
        MaterializedOutput,
        _assert_workflow_interface_output_closure,
    )

    payloads = _fixture_payloads()
    graph, plan = _derive(payloads)
    authority = build_compilation_plan_authority_inputs(
        graph=graph,
        payloads=tuple(payloads.values()),
        registry=build_app_layout_registry(()),
        scope_selection=_scope_selection(),
    )

    # Canonical authority rejects a plan whose interface output path was
    # rewritten (and re-digested) before materialization even begins.
    interface_unit_id = _family_units(plan)[_INTERFACE_FAMILY].unit_id
    document = plan.model_dump(mode="json")
    payload = plan.canonical_payload(include_digest=False)
    for doc in (document, payload):
        unit = next(u for u in doc["units"] if u["unit_id"] == interface_unit_id)
        unit["outputs"][0]["path"] = "workflows/other_workflow/module_interface.yaml"
    document["plan_digest"] = canonical_digest(payload)
    forged_plan = CompilationPlan.model_validate(document)
    with pytest.raises(MaterializationError, match="canonical authority"):
        materialize_plan(
            plan=forged_plan,
            authority_inputs=authority,
            graph=graph,
            payloads=tuple(payloads.values()),
            binding=_binding(graph),
            layout_registry=build_app_layout_registry(()),
        )

    # Output closure: missing, duplicate, and foreign-family outputs each
    # fail independently of the renderer.
    _g, _p, _a, bundle = _materialize(payloads)
    selection = next(
        s
        for s in _binding(graph).renderer_selections
        if "app_workflow_registry" in s.artifact_families
    )
    good = [
        o
        for o in bundle.outputs
        if o.path
        in (
            "workflows/analyze_document/module_interface.yaml",
            "workflows/workflow_registry.json",
        )
    ]
    with pytest.raises(WorkflowInterfaceMaterializationError, match="exactly one"):
        _assert_workflow_interface_output_closure(plan, good[:1], selection)
    with pytest.raises(WorkflowInterfaceMaterializationError, match="exactly one"):
        _assert_workflow_interface_output_closure(plan, [*good, good[0]], selection)
    foreign = MaterializedOutput(
        unit_id=good[0].unit_id,
        path_scope=good[0].path_scope,
        path="workflows/other/module_interface.yaml",
        content=good[0].content,
        origin="rendered",
        content_digest=good[0].content_digest,
    )
    with pytest.raises(
        WorkflowInterfaceMaterializationError, match="plan-owned path"
    ):
        _assert_workflow_interface_output_closure(plan, [*good, foreign], selection)
    with pytest.raises(WorkflowInterfaceMaterializationError, match="unauthorized"):
        _assert_workflow_interface_output_closure(plan, good, None)


# ---------------------------------------------------------------------------
# Correction round 2 — complete canonical layout-row / output / instance
# identity binding at the direct renderer. Resolution direction is the
# invariant: family_identity_digest -> exact canonical row -> exact
# placeholder set, unit id, scope, and path -> exact equality. The renderer
# never selects a profile from caller-supplied path_scope or path.
# ---------------------------------------------------------------------------


def _forged_output(unit, *, path_scope=None, path=None):
    from mozaiksai.core.semantics.compilation_plan import PlanOutput

    target = unit.outputs[0]
    return unit.model_copy(
        update={
            "outputs": (
                PlanOutput.model_construct(
                    path_scope=path_scope if path_scope is not None else target.path_scope,
                    path=path if path is not None else target.path,
                ),
            )
        }
    )


def _reject(unit, render_input) -> None:
    from mozaiksai.core.semantics.workflow_interface_materialization import (
        render_workflow_interface_unit,
    )

    with pytest.raises(WorkflowInterfaceMaterializationError):
        render_workflow_interface_unit(unit=unit, render_input=render_input)


def _relative_interface_unit_and_input():
    from mozaiksai.core.semantics.materialization import (
        project_workflow_interface_render_input,
    )

    payloads = _fixture_payloads()
    plan = derive_compilation_plan(
        graph=_build_graph(payloads),
        payloads=tuple(payloads.values()),
        registry=build_app_layout_registry(()),
        scope_selection=CompilationScopeSelection(
            workflow_manifest_scope=PathScope.WORKFLOW_RELATIVE
        ),
    )
    unit = next(
        u
        for u in plan.units
        if u.family_kind == _INTERFACE_FAMILY and u.sources
    )
    render_input = project_workflow_interface_render_input(
        unit=unit, payload_by_node={p.node_id: p for p in payloads.values()}
    )
    return unit, render_input


def _registry_unit_and_input():
    from mozaiksai.core.semantics.materialization import (
        project_workflow_interface_render_input,
    )

    payloads = _fixture_payloads()
    _graph, plan = _derive(payloads)
    unit = _family_units(plan)[_REGISTRY_FAMILY]
    render_input = project_workflow_interface_render_input(
        unit=unit, payload_by_node={p.node_id: p for p in payloads.values()}
    )
    return unit, render_input


def test_workspace_interface_row_identity_attack_matrix() -> None:
    """Hostile matrix A-O for the canonical workspace interface unit: every
    scope, placeholder, row-digest, unit-id, path, and render-input identity
    contradiction rejects before bytes."""

    from mozaiksai.core.semantics.workflow_interface_materialization import (
        _canonical_rows_by_digest,
        render_workflow_interface_unit,
    )

    _payloads, _plan, unit, render_input = _projected_interface_unit_and_input()
    assert render_workflow_interface_unit(unit=unit, render_input=render_input)
    relative_row_digest = next(
        digest
        for digest, row in _canonical_rows_by_digest().items()
        if row.kind == _INTERFACE_FAMILY and row.path_scope == "workflow_relative"
    )

    # A-C: scope substitution with the canonical path kept
    for scope in ("app_bundle_root", "generated_staging", "module_relative"):
        _reject(_forged_output(unit, path_scope=scope), render_input)
    # D: coordinated scope+path substitution toward the twin representation
    # while keeping the workspace row identity — Codex 3's key reproducer.
    _reject(
        _forged_output(
            unit, path_scope="workflow_relative", path="module_interface.yaml"
        ),
        render_input,
    )
    # E: surplus placeholder axis
    _reject(
        unit.model_copy(
            update={
                "placeholder_values": (
                    ("module_id", "documents"),
                    ("workflow_id", "analyze_document"),
                )
            }
        ),
        render_input,
    )
    # F: substituted placeholder axis
    _reject(
        unit.model_copy(update={"placeholder_values": (("module_id", "documents"),)}),
        render_input,
    )
    # G: removed placeholder
    _reject(unit.model_copy(update={"placeholder_values": ()}), render_input)
    # H: relative-row digest with the workspace unit id and output kept
    _reject(
        unit.model_copy(update={"family_identity_digest": relative_row_digest}),
        render_input,
    )
    # I: relative-shaped unit id with the workspace digest kept
    _reject(
        unit.model_copy(
            update={
                "unit_id": (
                    f"{_INTERFACE_FAMILY}/analyze_document/{relative_row_digest[:12]}"
                )
            }
        ),
        render_input,
    )
    # J: unknown row digest entirely
    _reject(
        unit.model_copy(update={"family_identity_digest": "0" * 64}), render_input
    )
    # K: wrong row-digest prefix inside the unit id
    _reject(
        unit.model_copy(
            update={"unit_id": f"{_INTERFACE_FAMILY}/analyze_document/000000000000"}
        ),
        render_input,
    )
    # L: correct scope/path but an extra placeholder rejects (same as E but
    # asserting the output identity was untouched)
    forged = unit.model_copy(
        update={
            "placeholder_values": (
                ("page_id", "home"),
                ("workflow_id", "analyze_document"),
            )
        }
    )
    assert forged.outputs == unit.outputs
    _reject(forged, render_input)
    # M: correct placeholders/path but wrong scope
    _reject(_forged_output(unit, path_scope="deployment_derived"), render_input)
    # N: correct scope/placeholders but another workflow's path
    _reject(
        _forged_output(unit, path="workflows/other_workflow/module_interface.yaml"),
        render_input,
    )
    # O: correct unit identity but a render input naming another workflow
    _reject(unit, render_input.model_copy(update={"workflow_id": "other_workflow"}))


def test_relative_interface_row_identity_attack_matrix() -> None:
    """Hostile matrix A-H for the canonical workflow-relative twin unit."""

    from mozaiksai.core.semantics.workflow_interface_materialization import (
        _canonical_rows_by_digest,
        render_workflow_interface_unit,
    )

    unit, render_input = _relative_interface_unit_and_input()
    assert unit.outputs[0].path == "module_interface.yaml"
    content = render_workflow_interface_unit(unit=unit, render_input=render_input)
    assert b"workflow_id: analyze_document" in content
    workspace_row_digest = next(
        digest
        for digest, row in _canonical_rows_by_digest().items()
        if row.kind == _INTERFACE_FAMILY and row.path_scope == "workspace_root"
    )

    # A: coordinated scope+path substitution toward the workspace
    # representation while keeping the relative row identity (the inverse of
    # Codex 3's reproducer).
    _reject(
        _forged_output(
            unit,
            path_scope="workspace_root",
            path="workflows/analyze_document/module_interface.yaml",
        ),
        render_input,
    )
    # B-D: other scope substitutions
    for scope in ("app_bundle_root", "module_relative", "generated_staging"):
        _reject(_forged_output(unit, path_scope=scope), render_input)
    # E: extra placeholder axis
    _reject(
        unit.model_copy(
            update={
                "placeholder_values": (
                    ("module_id", "documents"),
                    ("workflow_id", "analyze_document"),
                )
            }
        ),
        render_input,
    )
    # F: missing workflow identity
    _reject(unit.model_copy(update={"placeholder_values": ()}), render_input)
    # G: wrong unit-id row-digest suffix
    _reject(
        unit.model_copy(
            update={"unit_id": f"{_INTERFACE_FAMILY}/analyze_document/000000000000"}
        ),
        render_input,
    )
    # H: workspace family_identity_digest with the relative output kept
    _reject(
        unit.model_copy(update={"family_identity_digest": workspace_row_digest}),
        render_input,
    )


def test_app_registry_row_identity_attack_matrix() -> None:
    """Hostile matrix for the canonical registry unit: any scope
    substitution, any placeholder at all, wrong unit id, wrong or
    interface-substituted row digest — all reject."""

    from mozaiksai.core.semantics.workflow_interface_materialization import (
        _canonical_rows_by_digest,
        render_workflow_interface_unit,
    )

    unit, render_input = _registry_unit_and_input()
    assert unit.placeholder_values == ()
    assert render_workflow_interface_unit(unit=unit, render_input=render_input)
    interface_row_digest = next(
        digest
        for digest, row in _canonical_rows_by_digest().items()
        if row.kind == _INTERFACE_FAMILY and row.path_scope == "workspace_root"
    )

    for scope in (
        "app_bundle_root",
        "generated_staging",
        "module_relative",
        "workflow_relative",
        "deployment_derived",
    ):
        _reject(_forged_output(unit, path_scope=scope), render_input)
    for placeholder in (
        (("workflow_id", "analyze_document"),),
        (("module_id", "documents"),),
        (("page_id", "home"),),
    ):
        _reject(
            unit.model_copy(update={"placeholder_values": placeholder}), render_input
        )
    _reject(
        unit.model_copy(update={"unit_id": f"{_REGISTRY_FAMILY}/000000000000"}),
        render_input,
    )
    _reject(
        unit.model_copy(update={"family_identity_digest": "0" * 64}), render_input
    )
    # An interface row digest substituted into the registry unit contradicts
    # family kind through canonical row resolution.
    _reject(
        unit.model_copy(update={"family_identity_digest": interface_row_digest}),
        render_input,
    )
    # Correct path with incorrect scope.
    _reject(_forged_output(unit, path_scope="app_bundle_root"), render_input)


def test_full_canonical_row_substitution_is_the_plan_authoritys_boundary() -> None:
    """A COMPLETE canonical unit of the other twin row is recognized as that
    row's canonical shape — the direct renderer's contract is "exactly one
    canonical unit shape this renderer owns", while membership of that unit
    in the current CompilationPlan is #475/#477 plan authority (proven by
    the plan-authority suites), deliberately not re-implemented here."""

    from mozaiksai.core.semantics.workflow_interface_materialization import (
        render_workflow_interface_unit,
    )

    _payloads, _plan, workspace_unit, workspace_input = (
        _projected_interface_unit_and_input()
    )
    relative_unit, relative_input = _relative_interface_unit_and_input()
    # Each complete canonical shape renders under its own row identity...
    assert render_workflow_interface_unit(
        unit=workspace_unit, render_input=workspace_input
    )
    assert render_workflow_interface_unit(
        unit=relative_unit, render_input=relative_input
    )
    # ...and any PARTIAL cross-substitution of the twin identities rejects
    # (the coordinated matrices above); the two complete canonical units
    # differ in exactly the row-derived facts.
    assert workspace_unit.family_identity_digest != relative_unit.family_identity_digest
    assert workspace_unit.unit_id != relative_unit.unit_id
    assert workspace_unit.outputs != relative_unit.outputs
    assert (
        workspace_unit.placeholder_values == relative_unit.placeholder_values
    )  # same workflow instance identity, different row identity


def test_renderer_row_contract_matches_the_canonical_registry() -> None:
    """Drift guard: the renderer's canonical row contract is exactly the
    workflow-interface rows of the one canonical core registry — same
    digests, kinds, owners, scopes, and templates."""

    from mozaiksai.core.semantics.compilation_plan import snapshot_layout_registry
    from mozaiksai.core.semantics.workflow_interface_materialization import (
        _canonical_rows_by_digest,
    )

    snapshot = snapshot_layout_registry(build_app_layout_registry(()))
    expected = {
        row.row_digest: row
        for row in snapshot.rows
        if row.kind in WORKFLOW_INTERFACE_FAMILIES
    }
    resolved = _canonical_rows_by_digest()
    assert set(resolved) == set(expected)
    profiles = {
        (row.kind, row.owner, row.path_scope, row.path_template)
        for row in resolved.values()
    }
    assert profiles == {
        (
            _INTERFACE_FAMILY,
            "workflow",
            "workspace_root",
            "workflows/{workflow_id}/module_interface.yaml",
        ),
        (_INTERFACE_FAMILY, "workflow", "workflow_relative", "module_interface.yaml"),
        (
            _REGISTRY_FAMILY,
            "app_workspace",
            "workspace_root",
            "workflows/workflow_registry.json",
        ),
    }
