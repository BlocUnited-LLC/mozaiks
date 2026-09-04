"""Module↔workflow capability binding semantics: contract, closure, mutation matrix.

The typed vocabulary under test replaces the stringly seam where module and
workflow linkage lived in seven mutually-unaware string namespaces (module
capability rows, reaction targets, orchestrator trigger ids, kebab slugs,
plan lists, synthesized envelope ids, page workflow_ids).  These tests prove:

- typed semantic payloads own application meaning; generic graph edges are
  derived projections that can never invent ownership, event production,
  binding meaning, or result identity;
- module actions used through :class:`ModuleActionRef` have exactly one
  canonical module owner;
- event production authority comes from typed ``ActionPayload.emits`` joined
  to the EVENT node's canonical taxonomy identity, never from a bare EMITS
  edge;
- each binding/capability/result node participates in EXACTLY its derived
  edge set (full edge identity, discriminator included);
- workflow results are typed, capability-owned WORKFLOW_RESULT nodes; fan-out
  is explicit (several commit bindings referencing the same result node);
- the canonical fixture (documents / AnalyzeDocument) validates and is
  byte-deterministic across processes, and unrelated additions never move a
  binding or result identity.
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
    TaxonomyReference,
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
    WorkflowResultPayload,
    build_semantic_payload,
    derive_workflow_capability_binding_edges,
    derive_workflow_result_edges,
    parse_semantic_payload,
    semantic_payload_ref,
    validate_semantic_graph_v2_payload_closure,
)
from mozaiksai.core.semantics.refs import ExecutionAccessScopeRef
from mozaiksai.core.taxonomy import SemanticCategory

ROOT = Path(__file__).resolve().parents[1]

_SCOPE = ExecutionAccessScopeRef(tenant_id="tenant-docs", workspace_id="ws-docs")
_FOREIGN_SCOPE = ExecutionAccessScopeRef(tenant_id="tenant-other")

_MODULE = "mozaiks.module.documents"
_ACTION_CREATE = "mozaiks.action.documents_create"
_ACTION_GET = "mozaiks.action.documents_get_content"
_ACTION_STORE = "mozaiks.action.documents_store_analysis"
_EVENT = "mozaiks.event.documents_created"
_EVENT_ID = "domain.documents.created"
_WORKFLOW = "mozaiks.workflow.analyze_document"
_CAPABILITY = "mozaiks.workflow_capability.documents_analysis"
_RESULT = "mozaiks.workflow_result.analysis_result"
_RESULT_ALT = "mozaiks.workflow_result.analysis_summary"
_BINDING_TRIGGER = "mozaiks.workflow_capability_binding.analysis_on_created"
_BINDING_READ = "mozaiks.workflow_capability_binding.analysis_reads_content"
_BINDING_RESULT = "mozaiks.workflow_capability_binding.analysis_stores_result"

_RIVAL_WORKFLOW = "mozaiks.workflow.rival"
_RIVAL_CAPABILITY = "mozaiks.workflow_capability.rival_analysis"


def _action(node_id: str, *, description: str, emits: tuple[str, ...] = ()) -> ActionPayload:
    return build_semantic_payload(
        ActionPayload,
        node_id=node_id,
        payload_version=1,
        scope=_SCOPE,
        description=description,
        emits=emits,
    )


def _result(
    node_id: str,
    *,
    result_id: str,
    capability_node_id: str = _CAPABILITY,
    description: str | None = "One analysis of one document",
) -> WorkflowResultPayload:
    return build_semantic_payload(
        WorkflowResultPayload,
        node_id=node_id,
        payload_version=1,
        scope=_SCOPE,
        result_id=result_id,
        description=description,
        workflow_capability_node_id=capability_node_id,
    )


def _rival_workflow_and_capability() -> tuple[WorkflowPayload, WorkflowCapabilityPayload]:
    workflow = build_semantic_payload(
        WorkflowPayload,
        node_id=_RIVAL_WORKFLOW,
        payload_version=1,
        scope=_SCOPE,
        workflow_id="rival",
        description="Competing workflow",
        startup_mode=None,
        topology=None,
    )
    capability = build_semantic_payload(
        WorkflowCapabilityPayload,
        node_id=_RIVAL_CAPABILITY,
        payload_version=1,
        scope=_SCOPE,
        capability_id="documents.rival_analysis",
        description="A different capability",
        workflow_node_id=_RIVAL_WORKFLOW,
    )
    return workflow, capability


def _fixture_payloads() -> dict[str, SemanticPayloadBase]:
    """The canonical fixture: documents module + AnalyzeDocument workflow.

    module documents { create, get_content, store_analysis }; create truthfully
    emits domain.documents.created; workflow AnalyzeDocument declares
    capability documents.analysis, which declares workflow result
    analysis_result and three bindings: triggered by the event, consumes
    get_content, commits the declared result through store_analysis.
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
            emits=(_EVENT_ID,),
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
    _add(_result(_RESULT, result_id="analysis_result"))
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
            workflow_result_node_id=_RESULT,
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
    for payload in payloads.values():
        if isinstance(payload, WorkflowCapabilityPayload):
            edges.append(
                SemanticEdge(
                    kind=SemanticEdgeKind.DECLARES,
                    source_node_id=payload.workflow_node_id,
                    target_node_id=payload.node_id,
                )
            )
        elif isinstance(payload, WorkflowResultPayload):
            edges.extend(derive_workflow_result_edges(payload))
        elif isinstance(payload, WorkflowCapabilityBindingPayload):
            edges.extend(derive_workflow_capability_binding_edges(payload))
    return edges


def _build_graph(
    payloads: dict[str, SemanticPayloadBase],
    *,
    edges: list[SemanticEdge] | None = None,
    graph_id: str = "documents-analysis",
    event_identity: str | None = _EVENT_ID,
) -> SemanticGraphV2:
    nodes = []
    for payload in payloads.values():
        taxonomy_references: tuple[TaxonomyReference, ...] = ()
        if payload.node_id == _EVENT and event_identity is not None:
            taxonomy_references = (
                TaxonomyReference(
                    category=SemanticCategory.EVENT, identifier=event_identity
                ),
            )
        nodes.append(
            SemanticNodeV2(
                node_id=payload.node_id,
                kind=payload.payload_kind,
                taxonomy_references=taxonomy_references,
                payload_ref=semantic_payload_ref(payload),
            )
        )
    return build_semantic_graph_v2(
        graph_id=graph_id,
        version=1,
        scope=_SCOPE,
        nodes=nodes,
        edges=_fixture_edges(payloads) if edges is None else edges,
    )


def _validate(payloads: dict[str, SemanticPayloadBase], **kwargs) -> SemanticGraphV2:
    graph = _build_graph(payloads, **kwargs)
    validate_semantic_graph_v2_payload_closure(graph, list(payloads.values()))
    return graph


def _expect_rejection(payloads: dict[str, SemanticPayloadBase], match: str, **kwargs) -> None:
    graph = _build_graph(payloads, **kwargs)
    with pytest.raises(SemanticPayloadError, match=match):
        validate_semantic_graph_v2_payload_closure(graph, list(payloads.values()))


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
        "workflow_result_node_id": base.workflow_result_node_id,
    }
    fields.update(overrides)
    return build_semantic_payload(WorkflowCapabilityBindingPayload, **fields)


def _rebuilt_action(payloads: dict[str, SemanticPayloadBase], node_id: str, **overrides):
    base = payloads[node_id]
    fields = {
        "node_id": base.node_id,
        "payload_version": base.payload_version,
        "scope": base.scope,
        "description": base.description,
        "emits": base.emits,
    }
    fields.update(overrides)
    return build_semantic_payload(ActionPayload, **fields)


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


def test_each_bound_axis_mutation_changes_canonical_identity() -> None:
    baseline = _fixture_payloads()
    baseline_graph = _validate(baseline)
    read_binding_digest = baseline[_BINDING_READ].payload_digest
    commit_binding_digest = baseline[_BINDING_RESULT].payload_digest
    result_digest = baseline[_RESULT].payload_digest

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

    # Binding role/direction mutated on the same endpoints (explicit fan-out
    # of the declared result through a second action, so it still validates).
    mutated = _fixture_payloads()
    mutated[_BINDING_READ] = _rebuilt_binding(
        mutated,
        _BINDING_READ,
        binding_role=WorkflowCapabilityBindingRole.COMMITS_RESULT_THROUGH_ACTION,
        workflow_result_node_id=_RESULT,
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

    # Committed result reference mutated: point at a second declared result.
    mutated = _fixture_payloads()
    mutated[_RESULT_ALT] = _result(_RESULT_ALT, result_id="analysis_summary")
    mutated[_BINDING_RESULT] = _rebuilt_binding(
        mutated, _BINDING_RESULT, workflow_result_node_id=_RESULT_ALT
    )
    graph = _validate(mutated)
    assert mutated[_BINDING_RESULT].payload_digest != commit_binding_digest
    assert graph.graph_digest != baseline_graph.graph_digest

    # Result node's own result_id mutated: result identity moves, the binding
    # keeps pinning the same node (identity via node + ownership, not string).
    mutated = _fixture_payloads()
    mutated[_RESULT] = _result(_RESULT, result_id="analysis_summary")
    graph = _validate(mutated)
    assert mutated[_RESULT].payload_digest != result_digest
    assert mutated[_BINDING_RESULT].payload_digest == commit_binding_digest
    assert graph.graph_digest != baseline_graph.graph_digest

    # Result description is identity-bearing payload content.
    mutated = _fixture_payloads()
    mutated[_RESULT] = _result(
        _RESULT, result_id="analysis_result", description="Deeper analysis"
    )
    assert mutated[_RESULT].payload_digest != result_digest
    assert _validate(mutated).graph_digest != baseline_graph.graph_digest


def test_unrelated_additions_never_move_binding_or_result_identity() -> None:
    baseline = _fixture_payloads()
    tracked_digests = {
        node_id: payload.payload_digest
        for node_id, payload in baseline.items()
        if isinstance(payload, WorkflowCapabilityBindingPayload | WorkflowResultPayload)
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
    rival_workflow, rival_capability = _rival_workflow_and_capability()
    extended[rival_workflow.node_id] = rival_workflow
    extended[rival_capability.node_id] = rival_capability
    extended["mozaiks.workflow_result.rival_summary"] = _result(
        "mozaiks.workflow_result.rival_summary",
        result_id="summary",
        capability_node_id=_RIVAL_CAPABILITY,
    )
    extended["mozaiks.workflow_capability_binding.rival_reads_archive"] = (
        build_semantic_payload(
            WorkflowCapabilityBindingPayload,
            node_id="mozaiks.workflow_capability_binding.rival_reads_archive",
            payload_version=1,
            scope=_SCOPE,
            binding_role=WorkflowCapabilityBindingRole.CONSUMES_ACTION,
            workflow_capability_node_id=_RIVAL_CAPABILITY,
            module_action=ModuleActionRef(
                module_node_id="mozaiks.module.archive",
                action_node_id="mozaiks.action.archive_store",
            ),
        )
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

    for node_id, digest in tracked_digests.items():
        assert extended[node_id].payload_digest == digest, (
            f"unrelated mutation moved semantic identity {node_id}"
        )


# ---------------------------------------------------------------------------
# Canonical module action ownership
# ---------------------------------------------------------------------------


def test_action_under_wrong_module_rejects() -> None:
    """A real action referenced through a module that is not its canonical owner."""
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
    _expect_rejection(payloads, "canonical module owner is")


def test_action_declared_by_two_modules_rejects() -> None:
    """Codex reproduction: two modules DECLARE the same action."""
    payloads = _fixture_payloads()
    payloads["mozaiks.module.other"] = build_semantic_payload(
        ModulePayload,
        node_id="mozaiks.module.other",
        payload_version=1,
        scope=_SCOPE,
        module_id="other",
        description="Another module",
    )
    edges = _fixture_edges(payloads)
    edges.append(
        SemanticEdge(
            kind=SemanticEdgeKind.DECLARES,
            source_node_id="mozaiks.module.other",
            target_node_id=_ACTION_GET,
        )
    )
    _expect_rejection(payloads, "ambiguously owned by multiple modules", edges=edges)


def test_action_with_no_module_declarer_rejects() -> None:
    payloads = _fixture_payloads()
    edges = [
        edge
        for edge in _fixture_edges(payloads)
        if not (
            edge.kind is SemanticEdgeKind.DECLARES
            and edge.source_node_id == _MODULE
            and edge.target_node_id == _ACTION_GET
        )
    ]
    _expect_rejection(payloads, "that no module\\s+declares", edges=edges)


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


# ---------------------------------------------------------------------------
# Typed event production authority
# ---------------------------------------------------------------------------


def test_forged_emits_edge_without_typed_backing_rejects() -> None:
    """Codex reproduction: ActionPayload.emits == () plus a generic EMITS edge."""
    payloads = _fixture_payloads()
    payloads[_ACTION_CREATE] = _rebuilt_action(payloads, _ACTION_CREATE, emits=())
    _expect_rejection(payloads, "not\\s+backed by typed ActionPayload.emits")


def test_forged_emits_edge_from_unrelated_action_rejects() -> None:
    """A generic EMITS edge from an action that declares no emitted events."""
    payloads = _fixture_payloads()
    edges = _fixture_edges(payloads)
    edges.append(
        SemanticEdge(
            kind=SemanticEdgeKind.EMITS,
            source_node_id=_ACTION_GET,
            target_node_id=_EVENT,
        )
    )
    _expect_rejection(payloads, "not\\s+backed by typed ActionPayload.emits", edges=edges)


def test_typed_emits_without_projection_edge_rejects() -> None:
    payloads = _fixture_payloads()
    edges = [
        edge
        for edge in _fixture_edges(payloads)
        if edge.kind is not SemanticEdgeKind.EMITS
    ]
    _expect_rejection(payloads, "lacks the required EMITS projection", edges=edges)


def test_event_without_canonical_producer_rejects() -> None:
    payloads = _fixture_payloads()
    payloads[_ACTION_CREATE] = _rebuilt_action(payloads, _ACTION_CREATE, emits=())
    edges = [
        edge
        for edge in _fixture_edges(payloads)
        if edge.kind is not SemanticEdgeKind.EMITS
    ]
    _expect_rejection(
        payloads, "no canonical module-owned\\s+action produces", edges=edges
    )


def test_event_producer_without_module_owner_rejects() -> None:
    """Codex reproduction: the only producer action has no module owner."""
    payloads = _fixture_payloads()
    edges = [
        edge
        for edge in _fixture_edges(payloads)
        if not (
            edge.kind is SemanticEdgeKind.DECLARES
            and edge.source_node_id == _MODULE
            and edge.target_node_id == _ACTION_CREATE
        )
    ]
    _expect_rejection(
        payloads, "no canonical module-owned\\s+action produces", edges=edges
    )


def test_event_producer_with_ambiguous_module_ownership_rejects() -> None:
    payloads = _fixture_payloads()
    payloads["mozaiks.module.other"] = build_semantic_payload(
        ModulePayload,
        node_id="mozaiks.module.other",
        payload_version=1,
        scope=_SCOPE,
        module_id="other",
        description="Another module",
    )
    edges = _fixture_edges(payloads)
    edges.append(
        SemanticEdge(
            kind=SemanticEdgeKind.DECLARES,
            source_node_id="mozaiks.module.other",
            target_node_id=_ACTION_CREATE,
        )
    )
    _expect_rejection(payloads, "ambiguous module\\s+ownership", edges=edges)


def test_trigger_event_without_canonical_identity_rejects() -> None:
    payloads = _fixture_payloads()
    _expect_rejection(
        payloads, "exactly one\\s+canonical event identity", event_identity=None
    )


def test_removed_event_rejects() -> None:
    payloads = _fixture_payloads()
    del payloads[_EVENT]
    edges = [
        edge
        for edge in _fixture_edges(payloads)
        if _EVENT not in (edge.source_node_id, edge.target_node_id)
    ]
    _expect_rejection(payloads, "missing or non-event", edges=edges)


# ---------------------------------------------------------------------------
# Typed workflow result identity and ownership
# ---------------------------------------------------------------------------


def test_missing_result_node_rejects() -> None:
    """A free/unanchored workflow result is impossible: the commit binding must
    reference a declared WORKFLOW_RESULT node."""
    payloads = _fixture_payloads()
    del payloads[_RESULT]
    edges = [
        edge
        for edge in _fixture_edges(payloads)
        if _RESULT not in (edge.source_node_id, edge.target_node_id)
    ]
    _expect_rejection(payloads, "missing or\\s+non-workflow-result", edges=edges)


def test_result_node_must_be_workflow_result_kind() -> None:
    payloads = _fixture_payloads()
    payloads[_BINDING_RESULT] = _rebuilt_binding(
        payloads, _BINDING_RESULT, workflow_result_node_id=_EVENT
    )
    _expect_rejection(payloads, "missing or\\s+non-workflow-result")


def test_result_owned_by_foreign_capability_rejects() -> None:
    """A capability cannot commit another capability's result."""
    payloads = _fixture_payloads()
    rival_workflow, rival_capability = _rival_workflow_and_capability()
    payloads[rival_workflow.node_id] = rival_workflow
    payloads[rival_capability.node_id] = rival_capability
    payloads["mozaiks.workflow_result.rival_summary"] = _result(
        "mozaiks.workflow_result.rival_summary",
        result_id="summary",
        capability_node_id=_RIVAL_CAPABILITY,
    )
    payloads[_BINDING_RESULT] = _rebuilt_binding(
        payloads,
        _BINDING_RESULT,
        workflow_result_node_id="mozaiks.workflow_result.rival_summary",
    )
    _expect_rejection(payloads, "cannot commit\\s+another capability's result")


def test_result_owner_and_declarer_must_agree() -> None:
    payloads = _fixture_payloads()
    rival_workflow, rival_capability = _rival_workflow_and_capability()
    payloads[rival_workflow.node_id] = rival_workflow
    payloads[rival_capability.node_id] = rival_capability
    edges = [
        SemanticEdge(
            kind=SemanticEdgeKind.DECLARES,
            source_node_id=_RIVAL_CAPABILITY,
            target_node_id=_RESULT,
        )
        if (
            edge.kind is SemanticEdgeKind.DECLARES
            and edge.source_node_id == _CAPABILITY
            and edge.target_node_id == _RESULT
        )
        else edge
        for edge in _fixture_edges(payloads)
    ]
    _expect_rejection(payloads, "claims owner", edges=edges)


def test_result_declared_by_two_capabilities_rejects() -> None:
    payloads = _fixture_payloads()
    rival_workflow, rival_capability = _rival_workflow_and_capability()
    payloads[rival_workflow.node_id] = rival_workflow
    payloads[rival_capability.node_id] = rival_capability
    edges = _fixture_edges(payloads)
    edges.append(
        SemanticEdge(
            kind=SemanticEdgeKind.DECLARES,
            source_node_id=_RIVAL_CAPABILITY,
            target_node_id=_RESULT,
        )
    )
    _expect_rejection(payloads, "declared by exactly one", edges=edges)


def test_duplicate_result_id_within_one_capability_rejects() -> None:
    payloads = _fixture_payloads()
    payloads[_RESULT_ALT] = _result(_RESULT_ALT, result_id="analysis_result")
    _expect_rejection(payloads, "declared twice by capability")


def test_same_result_id_in_unrelated_capabilities_accepts() -> None:
    payloads = _fixture_payloads()
    rival_workflow, rival_capability = _rival_workflow_and_capability()
    payloads[rival_workflow.node_id] = rival_workflow
    payloads[rival_capability.node_id] = rival_capability
    payloads["mozaiks.workflow_result.rival_analysis_result"] = _result(
        "mozaiks.workflow_result.rival_analysis_result",
        result_id="analysis_result",
        capability_node_id=_RIVAL_CAPABILITY,
    )
    _validate(payloads)


def test_result_identity_is_provider_neutral() -> None:
    """A provider schema / model-class / runtime name is not result identity."""
    for provider_shaped in ("PlanCatalogProposal", "AnalyzeDocumentOutput", "gpt-result"):
        with pytest.raises(ValidationError, match="result_id"):
            _result(_RESULT, result_id=provider_shaped)


# ---------------------------------------------------------------------------
# Explicit result fan-out policy
# ---------------------------------------------------------------------------


def test_explicit_fan_out_of_one_result_through_two_actions_accepts() -> None:
    """Intentional fan-out: multiple commit bindings, one WORKFLOW_RESULT node."""
    payloads = _fixture_payloads()
    payloads["mozaiks.workflow_capability_binding.analysis_also_creates"] = (
        build_semantic_payload(
            WorkflowCapabilityBindingPayload,
            node_id="mozaiks.workflow_capability_binding.analysis_also_creates",
            payload_version=1,
            scope=_SCOPE,
            binding_role=WorkflowCapabilityBindingRole.COMMITS_RESULT_THROUGH_ACTION,
            workflow_capability_node_id=_CAPABILITY,
            module_action=ModuleActionRef(
                module_node_id=_MODULE, action_node_id=_ACTION_CREATE
            ),
            workflow_result_node_id=_RESULT,
        )
    )
    _validate(payloads)


def test_two_results_through_the_same_action_are_distinct_relationships() -> None:
    payloads = _fixture_payloads()
    payloads[_RESULT_ALT] = _result(_RESULT_ALT, result_id="analysis_summary")
    payloads["mozaiks.workflow_capability_binding.analysis_stores_summary"] = (
        build_semantic_payload(
            WorkflowCapabilityBindingPayload,
            node_id="mozaiks.workflow_capability_binding.analysis_stores_summary",
            payload_version=1,
            scope=_SCOPE,
            binding_role=WorkflowCapabilityBindingRole.COMMITS_RESULT_THROUGH_ACTION,
            workflow_capability_node_id=_CAPABILITY,
            module_action=ModuleActionRef(
                module_node_id=_MODULE, action_node_id=_ACTION_STORE
            ),
            workflow_result_node_id=_RESULT_ALT,
        )
    )
    _validate(payloads)


def test_duplicate_binding_identity_rejects() -> None:
    payloads = _fixture_payloads()
    duplicate = _rebuilt_binding(
        payloads,
        _BINDING_RESULT,
        node_id="mozaiks.workflow_capability_binding.analysis_stores_result_again",
    )
    payloads[duplicate.node_id] = duplicate
    _expect_rejection(payloads, "duplicate workflow capability binding identity")


# ---------------------------------------------------------------------------
# Capability ownership (carried forward clean)
# ---------------------------------------------------------------------------


def test_ambiguous_capability_ownership_rejects() -> None:
    payloads = _fixture_payloads()
    rival_workflow, _unused = _rival_workflow_and_capability()
    payloads[rival_workflow.node_id] = rival_workflow
    payloads[_RIVAL_CAPABILITY] = build_semantic_payload(
        WorkflowCapabilityPayload,
        node_id=_RIVAL_CAPABILITY,
        payload_version=1,
        scope=_SCOPE,
        capability_id="documents.analysis",
        description="Same capability id, different workflow",
        workflow_node_id=_RIVAL_WORKFLOW,
    )
    _expect_rejection(payloads, "ambiguously owned")


def test_capability_owner_and_declarer_must_agree() -> None:
    payloads = _fixture_payloads()
    rival_workflow, _unused = _rival_workflow_and_capability()
    payloads[rival_workflow.node_id] = rival_workflow
    edges = [
        SemanticEdge(
            kind=SemanticEdgeKind.DECLARES,
            source_node_id=_RIVAL_WORKFLOW,
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


# ---------------------------------------------------------------------------
# Exact derived-edge projection (full identity, discriminator included)
# ---------------------------------------------------------------------------


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


def test_forged_extra_edges_on_binding_nodes_reject() -> None:
    """Codex reproduction: extra meaning-bearing edges beyond the derived set."""
    payloads = _fixture_payloads()
    forged_edges = (
        # Extra BINDS from the consumes binding to another action.
        SemanticEdge(
            kind=SemanticEdgeKind.BINDS,
            source_node_id=_BINDING_READ,
            target_node_id=_ACTION_STORE,
        ),
        # Extra CONSUMES from the commit binding to another action.
        SemanticEdge(
            kind=SemanticEdgeKind.CONSUMES,
            source_node_id=_BINDING_RESULT,
            target_node_id=_ACTION_GET,
        ),
        # Extra edge from the trigger binding to an action.
        SemanticEdge(
            kind=SemanticEdgeKind.BINDS,
            source_node_id=_BINDING_TRIGGER,
            target_node_id=_ACTION_STORE,
        ),
        # Forged inbound DECLARES onto a binding from a module.
        SemanticEdge(
            kind=SemanticEdgeKind.DECLARES,
            source_node_id=_MODULE,
            target_node_id=_BINDING_READ,
        ),
        # Forged inbound edge onto the declared result node.
        SemanticEdge(
            kind=SemanticEdgeKind.BINDS,
            source_node_id=_WORKFLOW,
            target_node_id=_RESULT,
        ),
    )
    for forged in forged_edges:
        edges = _fixture_edges(payloads)
        edges.append(forged)
        # A forged inbound DECLARES is caught by the sole-declarer rule before
        # the exact-projection scan; every other forgery falls through to it.
        _expect_rejection(
            payloads,
            "not part of the canonical\\s+derived projection"
            "|declared by exactly one",
            edges=edges,
        )


def test_discriminator_mutations_on_derived_edges_reject() -> None:
    """Codex reproduction: edge identity must include the discriminator."""
    payloads = _fixture_payloads()

    def _mutate(match_kind: SemanticEdgeKind, source: str, replacement: SemanticEdge) -> list[SemanticEdge]:
        return [
            replacement
            if (edge.kind is match_kind and edge.source_node_id == source)
            else edge
            for edge in _fixture_edges(payloads)
        ]

    # Wrong result discriminator on the commit projection.
    edges = _mutate(
        SemanticEdgeKind.BINDS,
        _BINDING_RESULT,
        SemanticEdge(
            kind=SemanticEdgeKind.BINDS,
            source_node_id=_BINDING_RESULT,
            target_node_id=_ACTION_STORE,
            discriminator=_RESULT_ALT,
        ),
    )
    _expect_rejection(
        payloads, "not part of the canonical\\s+derived projection", edges=edges
    )

    # Missing result discriminator on the commit projection.
    edges = _mutate(
        SemanticEdgeKind.BINDS,
        _BINDING_RESULT,
        SemanticEdge(
            kind=SemanticEdgeKind.BINDS,
            source_node_id=_BINDING_RESULT,
            target_node_id=_ACTION_STORE,
        ),
    )
    _expect_rejection(
        payloads, "not part of the canonical\\s+derived projection", edges=edges
    )

    # Extra discriminator on the consumes projection.
    edges = _mutate(
        SemanticEdgeKind.CONSUMES,
        _BINDING_READ,
        SemanticEdge(
            kind=SemanticEdgeKind.CONSUMES,
            source_node_id=_BINDING_READ,
            target_node_id=_ACTION_GET,
            discriminator="uninvited",
        ),
    )
    _expect_rejection(
        payloads, "not part of the canonical\\s+derived projection", edges=edges
    )


def test_edge_only_mutation_never_changes_meaning_silently() -> None:
    """Payloads unchanged + a meaning-bearing edge mutation must reject."""
    payloads = _fixture_payloads()
    baseline_digests = {
        node_id: payload.payload_digest for node_id, payload in payloads.items()
    }
    edges = _fixture_edges(payloads)
    edges.append(
        SemanticEdge(
            kind=SemanticEdgeKind.CONSUMES,
            source_node_id=_BINDING_READ,
            target_node_id=_ACTION_STORE,
        )
    )
    graph = _build_graph(payloads, edges=edges)
    with pytest.raises(SemanticPayloadError):
        validate_semantic_graph_v2_payload_closure(graph, list(payloads.values()))
    assert baseline_digests == {
        node_id: payload.payload_digest for node_id, payload in payloads.items()
    }


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
# Role shape closure
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
    with pytest.raises(ValidationError, match="requires workflow_result_node_id"):
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
    with pytest.raises(ValidationError, match="must not set workflow_result_node_id"):
        build_semantic_payload(
            WorkflowCapabilityBindingPayload,
            binding_role=WorkflowCapabilityBindingRole.CONSUMES_ACTION,
            module_action=action,
            workflow_result_node_id=_RESULT,
            **common,
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
    """UI -> research.generate_report -> reads sources -> declared result -> store."""
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
        _action(
            "mozaiks.action.reports_get_source_data",
            description="Read report source data",
        )
    )
    _add(
        _action(
            "mozaiks.action.reports_store_report",
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
        _result(
            "mozaiks.workflow_result.generated_report",
            result_id="generated_report",
            capability_node_id="mozaiks.workflow_capability.generate_report",
            description="The finished report",
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
            workflow_result_node_id="mozaiks.workflow_result.generated_report",
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
        elif isinstance(payload, WorkflowResultPayload):
            edges.extend(derive_workflow_result_edges(payload))
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
    # Typed production authority: the trigger event is produced by the
    # module-owned create action's own typed emits.
    assert payloads[_ACTION_CREATE].emits == (_EVENT_ID,)
    commit = payloads[_BINDING_RESULT]
    assert commit.module_action.action_node_id == _ACTION_STORE
    assert commit.workflow_result_node_id == _RESULT
    declared_result = payloads[_RESULT]
    assert declared_result.result_id == "analysis_result"
    assert declared_result.workflow_capability_node_id == _CAPABILITY
    # Directional authority: the workflow capability owns neither the module
    # actions nor the event — its bindings reference them, and the module
    # remains the sole declarer of both.
    capability = payloads[_CAPABILITY]
    assert capability.workflow_node_id == _WORKFLOW


def test_app_zero_pattern_c_no_module_result_write() -> None:
    """Event-triggered advisory capability whose proposal is its only output.

    Genericized proposal/brief pattern: the declared workflow result IS the
    semantic output, and a human later invokes the module commit action, so
    there is no commits_result_through_action binding and none is fabricated
    — truthful absence validates.
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
    # The declared result stays: the proposal itself is the semantic output.
    assert isinstance(payloads[_RESULT], WorkflowResultPayload)
