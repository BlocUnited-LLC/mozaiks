"""Module↔workflow capability binding semantics: contract, closure, mutation matrix.

The typed vocabulary under test replaces the stringly seam where module and
workflow linkage lived in seven mutually-unaware string namespaces (module
capability rows, reaction targets, orchestrator trigger ids, kebab slugs,
plan lists, synthesized envelope ids, page workflow_ids).  These tests prove:

- the canonical fixture (documents / AnalyzeDocument) validates and is
  byte-deterministic across processes;
- every single-axis mutation either changes canonical semantic identity or is
  rejected by validation;
- unrelated module/action/workflow additions never move a binding's identity;
- referential integrity fails closed on every dangling or foreign reference;
- the three App Zero patterns (user-launched, event-triggered, no-result
  answer) are expressible with zero hosted/provider policy.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

from mozaiksai.core.semantics.graph import (
    SemanticEdge,
    SemanticEdgeKind,
    SemanticGraphV2,
    SemanticNodeV2,
    build_semantic_graph_v2,
)
from mozaiksai.core.semantics.payloads import (
    ActionPayload,
    EventPayload,
    ModuleActionRef,
    ModulePayload,
    SemanticPayloadBase,
    SemanticPayloadError,
    WorkflowCapabilityBindingPayload,
    WorkflowCapabilityBindingRole,
    WorkflowCapabilityPayload,
    WorkflowPayload,
    build_semantic_payload,
    derive_workflow_capability_binding_edges,
    parse_semantic_payload,
    semantic_payload_ref,
    validate_semantic_graph_v2_payload_closure,
)
from mozaiksai.core.semantics.refs import ExecutionAccessScopeRef

ROOT = Path(__file__).resolve().parents[1]

_SCOPE = ExecutionAccessScopeRef(tenant_id="tenant-docs", workspace_id="ws-docs")
_FOREIGN_SCOPE = ExecutionAccessScopeRef(tenant_id="tenant-other")

_MODULE = "mozaiks.module.documents"
_ACTION_CREATE = "mozaiks.action.documents_create"
_ACTION_GET = "mozaiks.action.documents_get_content"
_ACTION_STORE = "mozaiks.action.documents_store_analysis"
_EVENT = "mozaiks.event.documents_created"
_WORKFLOW = "mozaiks.workflow.analyze_document"
_CAPABILITY = "mozaiks.workflow_capability.documents_analysis"
_BINDING_TRIGGER = "mozaiks.workflow_capability_binding.analysis_on_created"
_BINDING_READ = "mozaiks.workflow_capability_binding.analysis_reads_content"
_BINDING_RESULT = "mozaiks.workflow_capability_binding.analysis_stores_result"


def _action(node_id: str, *, description: str, emits: tuple[str, ...] = ()) -> ActionPayload:
    return build_semantic_payload(
        ActionPayload,
        node_id=node_id,
        payload_version=1,
        scope=_SCOPE,
        description=description,
        emits=emits,
    )


def _fixture_payloads() -> dict[str, SemanticPayloadBase]:
    """The canonical fixture: documents module + AnalyzeDocument workflow.

    module documents { create, get_content, store_analysis } emits
    domain.documents.created; workflow AnalyzeDocument declares capability
    documents.analysis with three bindings: triggered by the event, consumes
    get_content, commits result analysis_result through store_analysis.
    """
    payloads: dict[str, SemanticPayloadBase] = {}

    def _add(payload: SemanticPayloadBase) -> None:
        payloads[payload.node_id] = payload

    _add(
        build_semantic_payload(
            ModulePayload,
            node_id=_MODULE,
            payload_version=1,
            scope=_SCOPE,
            module_id="documents",
            description="Durable document facts",
        )
    )
    _add(
        _action(
            _ACTION_CREATE,
            description="Create one document",
            emits=("domain.documents.created",),
        )
    )
    _add(_action(_ACTION_GET, description="Read one document's content"))
    _add(_action(_ACTION_STORE, description="Persist one analysis result"))
    _add(
        build_semantic_payload(
            EventPayload,
            node_id=_EVENT,
            payload_version=1,
            scope=_SCOPE,
            description="A document was created",
        )
    )
    _add(
        build_semantic_payload(
            WorkflowPayload,
            node_id=_WORKFLOW,
            payload_version=1,
            scope=_SCOPE,
            workflow_id="analyze_document",
            description="Reason over a new document",
            startup_mode=None,
            topology=None,
        )
    )
    _add(
        build_semantic_payload(
            WorkflowCapabilityPayload,
            node_id=_CAPABILITY,
            payload_version=1,
            scope=_SCOPE,
            capability_id="documents.analysis",
            description="Analyze a created document",
            workflow_node_id=_WORKFLOW,
        )
    )
    _add(
        build_semantic_payload(
            WorkflowCapabilityBindingPayload,
            node_id=_BINDING_TRIGGER,
            payload_version=1,
            scope=_SCOPE,
            binding_role=WorkflowCapabilityBindingRole.TRIGGERED_BY_EVENT,
            workflow_capability_node_id=_CAPABILITY,
            event_node_id=_EVENT,
        )
    )
    _add(
        build_semantic_payload(
            WorkflowCapabilityBindingPayload,
            node_id=_BINDING_READ,
            payload_version=1,
            scope=_SCOPE,
            binding_role=WorkflowCapabilityBindingRole.CONSUMES_ACTION,
            workflow_capability_node_id=_CAPABILITY,
            module_action=ModuleActionRef(
                module_node_id=_MODULE, action_node_id=_ACTION_GET
            ),
        )
    )
    _add(
        build_semantic_payload(
            WorkflowCapabilityBindingPayload,
            node_id=_BINDING_RESULT,
            payload_version=1,
            scope=_SCOPE,
            binding_role=WorkflowCapabilityBindingRole.COMMITS_RESULT_THROUGH_ACTION,
            workflow_capability_node_id=_CAPABILITY,
            module_action=ModuleActionRef(
                module_node_id=_MODULE, action_node_id=_ACTION_STORE
            ),
            workflow_result_id="analysis_result",
        )
    )
    return payloads


def _fixture_edges(payloads: dict[str, SemanticPayloadBase]) -> list[SemanticEdge]:
    edges = [
        SemanticEdge(
            kind=SemanticEdgeKind.DECLARES, source_node_id=_MODULE, target_node_id=action
        )
        for action in (_ACTION_CREATE, _ACTION_GET, _ACTION_STORE)
    ]
    edges.append(
        SemanticEdge(
            kind=SemanticEdgeKind.EMITS,
            source_node_id=_ACTION_CREATE,
            target_node_id=_EVENT,
        )
    )
    edges.append(
        SemanticEdge(
            kind=SemanticEdgeKind.DECLARES,
            source_node_id=_WORKFLOW,
            target_node_id=_CAPABILITY,
        )
    )
    for payload in payloads.values():
        if isinstance(payload, WorkflowCapabilityBindingPayload):
            edges.extend(derive_workflow_capability_binding_edges(payload))
    return edges


def _build_graph(
    payloads: dict[str, SemanticPayloadBase],
    *,
    edges: list[SemanticEdge] | None = None,
    graph_id: str = "documents-analysis",
) -> SemanticGraphV2:
    return build_semantic_graph_v2(
        graph_id=graph_id,
        version=1,
        scope=_SCOPE,
        nodes=[
            SemanticNodeV2(
                node_id=payload.node_id,
                kind=payload.payload_kind,
                payload_ref=semantic_payload_ref(payload),
            )
            for payload in payloads.values()
        ],
        edges=_fixture_edges(payloads) if edges is None else edges,
    )


def _validate(payloads: dict[str, SemanticPayloadBase], **kwargs) -> SemanticGraphV2:
    graph = _build_graph(payloads, **kwargs)
    validate_semantic_graph_v2_payload_closure(graph, list(payloads.values()))
    return graph


# ---------------------------------------------------------------------------
# Canonical fixture: valid, deterministic across processes
# ---------------------------------------------------------------------------


def test_canonical_fixture_validates_and_round_trips() -> None:
    payloads = _fixture_payloads()
    graph = _validate(payloads)
    for payload in payloads.values():
        parsed = parse_semantic_payload(payload.model_dump(mode="json"))
        assert parsed == payload
    assert graph.graph_digest == _build_graph(_fixture_payloads()).graph_digest


def test_fixture_digest_is_identical_in_a_fresh_process() -> None:
    expected = _build_graph(_fixture_payloads()).graph_digest
    probe = (
        "from tests.test_workflow_capability_semantics import (\n"
        "    _build_graph, _fixture_payloads)\n"
        "print(_build_graph(_fixture_payloads()).graph_digest)\n"
    )
    completed = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == expected


# ---------------------------------------------------------------------------
# Mutation matrix: one axis at a time
# ---------------------------------------------------------------------------


def _rebuilt_binding(payloads: dict[str, SemanticPayloadBase], source_node_id: str, **overrides):
    base = payloads[source_node_id]
    fields = {
        "node_id": base.node_id,
        "payload_version": base.payload_version,
        "scope": base.scope,
        "binding_role": base.binding_role,
        "workflow_capability_node_id": base.workflow_capability_node_id,
        "module_action": base.module_action,
        "event_node_id": base.event_node_id,
        "workflow_result_id": base.workflow_result_id,
    }
    fields.update(overrides)
    return build_semantic_payload(WorkflowCapabilityBindingPayload, **fields)


def test_each_bound_axis_mutation_changes_canonical_identity() -> None:
    baseline = _fixture_payloads()
    baseline_graph = _validate(baseline)
    read_binding_digest = baseline[_BINDING_READ].payload_digest

    # Input action mutated: get_content -> create.
    mutated = _fixture_payloads()
    mutated[_BINDING_READ] = _rebuilt_binding(
        mutated,
        _BINDING_READ,
        module_action=ModuleActionRef(
            module_node_id=_MODULE, action_node_id=_ACTION_CREATE
        ),
    )
    graph = _validate(mutated)
    assert mutated[_BINDING_READ].payload_digest != read_binding_digest
    assert graph.graph_digest != baseline_graph.graph_digest

    # Result action mutated: store_analysis -> create.
    mutated = _fixture_payloads()
    mutated[_BINDING_RESULT] = _rebuilt_binding(
        mutated,
        _BINDING_RESULT,
        module_action=ModuleActionRef(
            module_node_id=_MODULE, action_node_id=_ACTION_CREATE
        ),
    )
    assert _validate(mutated).graph_digest != baseline_graph.graph_digest

    # Binding role/direction mutated on the same endpoints.
    mutated = _fixture_payloads()
    mutated[_BINDING_READ] = _rebuilt_binding(
        mutated,
        _BINDING_READ,
        binding_role=WorkflowCapabilityBindingRole.COMMITS_RESULT_THROUGH_ACTION,
        workflow_result_id="content_projection",
    )
    assert _validate(mutated).graph_digest != baseline_graph.graph_digest

    # Capability id mutated.
    mutated = _fixture_payloads()
    base_cap = mutated[_CAPABILITY]
    mutated[_CAPABILITY] = build_semantic_payload(
        WorkflowCapabilityPayload,
        node_id=base_cap.node_id,
        payload_version=1,
        scope=_SCOPE,
        capability_id="documents.deep_analysis",
        description=base_cap.description,
        workflow_node_id=base_cap.workflow_node_id,
    )
    assert _validate(mutated).graph_digest != baseline_graph.graph_digest

    # Workflow result id mutated.
    mutated = _fixture_payloads()
    mutated[_BINDING_RESULT] = _rebuilt_binding(
        mutated, _BINDING_RESULT, workflow_result_id="analysis_summary"
    )
    assert _validate(mutated).graph_digest != baseline_graph.graph_digest


def test_unrelated_additions_never_move_a_binding_identity() -> None:
    baseline = _fixture_payloads()
    binding_digests = {
        node_id: payload.payload_digest
        for node_id, payload in baseline.items()
        if isinstance(payload, WorkflowCapabilityBindingPayload)
    }

    extended = _fixture_payloads()
    extended["mozaiks.module.archive"] = build_semantic_payload(
        ModulePayload,
        node_id="mozaiks.module.archive",
        payload_version=1,
        scope=_SCOPE,
        module_id="archive",
        description="Unrelated module",
    )
    extended["mozaiks.action.archive_store"] = _action(
        "mozaiks.action.archive_store", description="Unrelated action"
    )
    extended["mozaiks.workflow.unrelated"] = build_semantic_payload(
        WorkflowPayload,
        node_id="mozaiks.workflow.unrelated",
        payload_version=1,
        scope=_SCOPE,
        workflow_id="unrelated",
        description="Unrelated workflow",
        startup_mode=None,
        topology=None,
    )
    edges = _fixture_edges(extended)
    edges.append(
        SemanticEdge(
            kind=SemanticEdgeKind.DECLARES,
            source_node_id="mozaiks.module.archive",
            target_node_id="mozaiks.action.archive_store",
        )
    )
    graph = _build_graph(extended, edges=edges)
    validate_semantic_graph_v2_payload_closure(graph, list(extended.values()))

    for node_id, digest in binding_digests.items():
        assert extended[node_id].payload_digest == digest, (
            f"unrelated mutation moved binding identity {node_id}"
        )


# ---------------------------------------------------------------------------
# Referential integrity: every dangling/foreign reference fails closed
# ---------------------------------------------------------------------------


def _expect_rejection(payloads: dict[str, SemanticPayloadBase], match: str, **kwargs) -> None:
    graph = _build_graph(payloads, **kwargs)
    with pytest.raises(SemanticPayloadError, match=match):
        validate_semantic_graph_v2_payload_closure(graph, list(payloads.values()))


def test_removed_action_rejects() -> None:
    payloads = _fixture_payloads()
    del payloads[_ACTION_GET]
    edges = [
        edge
        for edge in _fixture_edges(payloads)
        if _ACTION_GET not in (edge.source_node_id, edge.target_node_id)
    ]
    _expect_rejection(payloads, "missing or non-action", edges=edges)


def test_removed_workflow_rejects() -> None:
    payloads = _fixture_payloads()
    del payloads[_WORKFLOW]
    edges = [
        edge
        for edge in _fixture_edges(payloads)
        if _WORKFLOW not in (edge.source_node_id, edge.target_node_id)
    ]
    _expect_rejection(payloads, "missing or non-workflow", edges=edges)


def test_removed_event_rejects() -> None:
    payloads = _fixture_payloads()
    del payloads[_EVENT]
    edges = [
        edge
        for edge in _fixture_edges(payloads)
        if _EVENT not in (edge.source_node_id, edge.target_node_id)
    ]
    _expect_rejection(payloads, "missing or non-event", edges=edges)


def test_action_under_wrong_module_rejects() -> None:
    """A real action referenced through a module that does not declare it."""
    payloads = _fixture_payloads()
    payloads["mozaiks.module.other"] = build_semantic_payload(
        ModulePayload,
        node_id="mozaiks.module.other",
        payload_version=1,
        scope=_SCOPE,
        module_id="other",
        description="Another module",
    )
    payloads[_BINDING_READ] = _rebuilt_binding(
        payloads,
        _BINDING_READ,
        module_action=ModuleActionRef(
            module_node_id="mozaiks.module.other", action_node_id=_ACTION_GET
        ),
    )
    _expect_rejection(payloads, "does not declare")


def test_event_without_module_producer_rejects() -> None:
    payloads = _fixture_payloads()
    edges = [
        edge
        for edge in _fixture_edges(payloads)
        if edge.kind is not SemanticEdgeKind.EMITS
    ]
    _expect_rejection(payloads, "no module or action produces", edges=edges)


def test_duplicate_binding_identity_rejects() -> None:
    payloads = _fixture_payloads()
    duplicate = _rebuilt_binding(
        payloads,
        _BINDING_READ,
        node_id="mozaiks.workflow_capability_binding.analysis_reads_content_again",
    )
    payloads[duplicate.node_id] = duplicate
    _expect_rejection(payloads, "duplicate workflow capability binding identity")


def test_ambiguous_capability_ownership_rejects() -> None:
    payloads = _fixture_payloads()
    payloads["mozaiks.workflow.rival"] = build_semantic_payload(
        WorkflowPayload,
        node_id="mozaiks.workflow.rival",
        payload_version=1,
        scope=_SCOPE,
        workflow_id="rival",
        description="Competing workflow",
        startup_mode=None,
        topology=None,
    )
    payloads["mozaiks.workflow_capability.rival_analysis"] = build_semantic_payload(
        WorkflowCapabilityPayload,
        node_id="mozaiks.workflow_capability.rival_analysis",
        payload_version=1,
        scope=_SCOPE,
        capability_id="documents.analysis",
        description="Same capability id, different workflow",
        workflow_node_id="mozaiks.workflow.rival",
    )
    edges = _fixture_edges(payloads)
    edges.append(
        SemanticEdge(
            kind=SemanticEdgeKind.DECLARES,
            source_node_id="mozaiks.workflow.rival",
            target_node_id="mozaiks.workflow_capability.rival_analysis",
        )
    )
    _expect_rejection(payloads, "ambiguously owned", edges=edges)


def test_capability_owner_and_declarer_must_agree() -> None:
    payloads = _fixture_payloads()
    payloads["mozaiks.workflow.rival"] = build_semantic_payload(
        WorkflowPayload,
        node_id="mozaiks.workflow.rival",
        payload_version=1,
        scope=_SCOPE,
        workflow_id="rival",
        description="Competing workflow",
        startup_mode=None,
        topology=None,
    )
    edges = [
        SemanticEdge(
            kind=SemanticEdgeKind.DECLARES,
            source_node_id="mozaiks.workflow.rival",
            target_node_id=_CAPABILITY,
        )
        if (
            edge.kind is SemanticEdgeKind.DECLARES
            and edge.source_node_id == _WORKFLOW
            and edge.target_node_id == _CAPABILITY
        )
        else edge
        for edge in _fixture_edges(payloads)
    ]
    _expect_rejection(payloads, "declared by", edges=edges)


def test_missing_derived_edge_rejects() -> None:
    payloads = _fixture_payloads()
    edges = [
        edge
        for edge in _fixture_edges(payloads)
        if not (
            edge.kind is SemanticEdgeKind.CONSUMES
            and edge.source_node_id == _BINDING_READ
        )
    ]
    _expect_rejection(payloads, "lacks its derived", edges=edges)


def test_foreign_scope_binding_rejects() -> None:
    foreign = build_semantic_payload(
        WorkflowCapabilityBindingPayload,
        node_id=_BINDING_READ,
        payload_version=1,
        scope=_FOREIGN_SCOPE,
        binding_role=WorkflowCapabilityBindingRole.CONSUMES_ACTION,
        workflow_capability_node_id=_CAPABILITY,
        module_action=ModuleActionRef(
            module_node_id=_MODULE, action_node_id=_ACTION_GET
        ),
    )
    graph = _build_graph(_fixture_payloads())
    supplied = [
        foreign if payload.node_id == _BINDING_READ else payload
        for payload in _fixture_payloads().values()
    ]
    with pytest.raises(SemanticPayloadError):
        validate_semantic_graph_v2_payload_closure(graph, supplied)


# ---------------------------------------------------------------------------
# Role shape and provider neutrality
# ---------------------------------------------------------------------------


def test_role_shapes_are_closed() -> None:
    common = {
        "node_id": "mozaiks.workflow_capability_binding.shape_probe",
        "payload_version": 1,
        "scope": _SCOPE,
        "workflow_capability_node_id": _CAPABILITY,
    }
    action = ModuleActionRef(module_node_id=_MODULE, action_node_id=_ACTION_GET)

    with pytest.raises(ValidationError, match="requires module_action"):
        build_semantic_payload(
            WorkflowCapabilityBindingPayload,
            binding_role=WorkflowCapabilityBindingRole.CONSUMES_ACTION,
            **common,
        )
    with pytest.raises(ValidationError, match="must not set event_node_id"):
        build_semantic_payload(
            WorkflowCapabilityBindingPayload,
            binding_role=WorkflowCapabilityBindingRole.CONSUMES_ACTION,
            module_action=action,
            event_node_id=_EVENT,
            **common,
        )
    with pytest.raises(ValidationError, match="requires workflow_result_id"):
        build_semantic_payload(
            WorkflowCapabilityBindingPayload,
            binding_role=WorkflowCapabilityBindingRole.COMMITS_RESULT_THROUGH_ACTION,
            module_action=action,
            **common,
        )
    with pytest.raises(ValidationError, match="must not set module_action"):
        build_semantic_payload(
            WorkflowCapabilityBindingPayload,
            binding_role=WorkflowCapabilityBindingRole.TRIGGERED_BY_EVENT,
            module_action=action,
            event_node_id=_EVENT,
            **common,
        )
    with pytest.raises(ValidationError, match="must not set workflow_result_id"):
        build_semantic_payload(
            WorkflowCapabilityBindingPayload,
            binding_role=WorkflowCapabilityBindingRole.CONSUMES_ACTION,
            module_action=action,
            workflow_result_id="analysis_result",
            **common,
        )


def test_workflow_result_identity_is_provider_neutral() -> None:
    """A provider schema / model-class name is not durable application meaning."""
    for provider_shaped in ("PlanCatalogProposal", "AnalyzeDocumentOutput", "gpt-result"):
        with pytest.raises(ValidationError, match="workflow_result_id"):
            build_semantic_payload(
                WorkflowCapabilityBindingPayload,
                node_id="mozaiks.workflow_capability_binding.neutrality_probe",
                payload_version=1,
                scope=_SCOPE,
                binding_role=WorkflowCapabilityBindingRole.COMMITS_RESULT_THROUGH_ACTION,
                workflow_capability_node_id=_CAPABILITY,
                module_action=ModuleActionRef(
                    module_node_id=_MODULE, action_node_id=_ACTION_STORE
                ),
                workflow_result_id=provider_shaped,
            )


def test_module_action_ref_is_exact() -> None:
    with pytest.raises(ValidationError, match="must differ"):
        ModuleActionRef(module_node_id=_MODULE, action_node_id=_MODULE)
    with pytest.raises(ValidationError):
        ModuleActionRef(module_node_id="documents", action_node_id=_ACTION_GET)


# ---------------------------------------------------------------------------
# App Zero proofs (genericized; zero hosted/provider policy)
# ---------------------------------------------------------------------------


def test_app_zero_pattern_a_user_launched_report_workflow() -> None:
    """UI -> research.generate_report -> reads source data -> stores report."""
    payloads: dict[str, SemanticPayloadBase] = {}

    def _add(payload: SemanticPayloadBase) -> None:
        payloads[payload.node_id] = payload

    _add(
        build_semantic_payload(
            ModulePayload,
            node_id="mozaiks.module.reports",
            payload_version=1,
            scope=_SCOPE,
            module_id="reports",
            description="Durable report facts",
        )
    )
    _add(
        build_semantic_payload(
            ActionPayload,
            node_id="mozaiks.action.reports_get_source_data",
            payload_version=1,
            scope=_SCOPE,
            description="Read report source data",
        )
    )
    _add(
        build_semantic_payload(
            ActionPayload,
            node_id="mozaiks.action.reports_store_report",
            payload_version=1,
            scope=_SCOPE,
            description="Persist a generated report",
        )
    )
    _add(
        build_semantic_payload(
            WorkflowPayload,
            node_id="mozaiks.workflow.report_generator",
            payload_version=1,
            scope=_SCOPE,
            workflow_id="report_generator",
            description="Generate a research report",
            startup_mode=None,
            topology=None,
        )
    )
    _add(
        build_semantic_payload(
            WorkflowCapabilityPayload,
            node_id="mozaiks.workflow_capability.generate_report",
            payload_version=1,
            scope=_SCOPE,
            capability_id="research.generate_report",
            description="User-launched report generation",
            workflow_node_id="mozaiks.workflow.report_generator",
        )
    )
    _add(
        build_semantic_payload(
            WorkflowCapabilityBindingPayload,
            node_id="mozaiks.workflow_capability_binding.report_reads_sources",
            payload_version=1,
            scope=_SCOPE,
            binding_role=WorkflowCapabilityBindingRole.CONSUMES_ACTION,
            workflow_capability_node_id="mozaiks.workflow_capability.generate_report",
            module_action=ModuleActionRef(
                module_node_id="mozaiks.module.reports",
                action_node_id="mozaiks.action.reports_get_source_data",
            ),
        )
    )
    _add(
        build_semantic_payload(
            WorkflowCapabilityBindingPayload,
            node_id="mozaiks.workflow_capability_binding.report_commits_report",
            payload_version=1,
            scope=_SCOPE,
            binding_role=WorkflowCapabilityBindingRole.COMMITS_RESULT_THROUGH_ACTION,
            workflow_capability_node_id="mozaiks.workflow_capability.generate_report",
            module_action=ModuleActionRef(
                module_node_id="mozaiks.module.reports",
                action_node_id="mozaiks.action.reports_store_report",
            ),
            workflow_result_id="generated_report",
        )
    )
    edges = [
        SemanticEdge(
            kind=SemanticEdgeKind.DECLARES,
            source_node_id="mozaiks.module.reports",
            target_node_id=action,
        )
        for action in (
            "mozaiks.action.reports_get_source_data",
            "mozaiks.action.reports_store_report",
        )
    ]
    edges.append(
        SemanticEdge(
            kind=SemanticEdgeKind.DECLARES,
            source_node_id="mozaiks.workflow.report_generator",
            target_node_id="mozaiks.workflow_capability.generate_report",
        )
    )
    for payload in list(payloads.values()):
        if isinstance(payload, WorkflowCapabilityBindingPayload):
            edges.extend(derive_workflow_capability_binding_edges(payload))
    graph = build_semantic_graph_v2(
        graph_id="pattern-a",
        version=1,
        scope=_SCOPE,
        nodes=[
            SemanticNodeV2(
                node_id=payload.node_id,
                kind=payload.payload_kind,
                payload_ref=semantic_payload_ref(payload),
            )
            for payload in payloads.values()
        ],
        edges=edges,
    )
    validate_semantic_graph_v2_payload_closure(graph, list(payloads.values()))


def test_app_zero_pattern_b_event_triggered_analysis() -> None:
    """documents.create -> domain.documents.created -> analysis -> store.

    The canonical fixture IS Pattern B; assert the semantic facts read back.
    """
    payloads = _fixture_payloads()
    _validate(payloads)

    trigger = payloads[_BINDING_TRIGGER]
    assert trigger.binding_role is WorkflowCapabilityBindingRole.TRIGGERED_BY_EVENT
    assert trigger.event_node_id == _EVENT
    result = payloads[_BINDING_RESULT]
    assert result.module_action.action_node_id == _ACTION_STORE
    assert result.workflow_result_id == "analysis_result"
    # Directional authority: the workflow capability owns neither the module
    # actions nor the event — its bindings reference them, and the module
    # remains the sole declarer of both.
    capability = payloads[_CAPABILITY]
    assert capability.workflow_node_id == _WORKFLOW


def test_app_zero_pattern_c_no_module_result_write() -> None:
    """Event-triggered advisory workflow with a user-facing answer only.

    Genericized proposal/brief pattern: the workflow proposes; a human later
    invokes the module commit action.  No commits_result_through_action
    binding exists and none is fabricated — truthful absence validates.
    """
    payloads = _fixture_payloads()
    del payloads[_BINDING_RESULT]
    del payloads[_BINDING_READ]
    edges = [
        edge
        for edge in _fixture_edges(payloads)
        if edge.source_node_id not in (_BINDING_RESULT, _BINDING_READ)
        and edge.target_node_id not in (_BINDING_RESULT, _BINDING_READ)
    ]
    graph = _build_graph(payloads, edges=edges)
    validate_semantic_graph_v2_payload_closure(graph, list(payloads.values()))
    remaining_roles = {
        payload.binding_role
        for payload in payloads.values()
        if isinstance(payload, WorkflowCapabilityBindingPayload)
    }
    assert remaining_roles == {WorkflowCapabilityBindingRole.TRIGGERED_BY_EVENT}
