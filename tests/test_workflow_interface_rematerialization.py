"""Canonical interface ownership, selective reuse, and taxonomy rematerialization."""

from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
import yaml

from mozaiksai.core.runtime.app.layout_registry import (
    MaterializerIdentifier,
    PathScope,
    build_app_layout_registry,
)
from mozaiksai.core.semantics import materialization
from mozaiksai.core.semantics.binding import RendererSelection, build_implementation_binding
from mozaiksai.core.semantics.canonical import canonical_digest
from mozaiksai.core.semantics.compilation_plan import (
    CompilationPlan,
    CompilationScopeSelection,
    PlanDisposition,
    PlanEdgeSource,
    PlanSource,
    PlanTaxonomySource,
    derive_compilation_plan,
    plan_regeneration_closure,
)
from mozaiksai.core.semantics.graph import (
    SemanticEdge,
    SemanticEdgeKind,
    SemanticNodeV2,
    TaxonomyReference,
    build_semantic_graph_v2,
)
from mozaiksai.core.semantics.payloads import (
    ActionPayload,
    ModuleActionRef,
    SemanticPayloadBase,
    WorkflowCapabilityBindingPayload,
    WorkflowCapabilityPayload,
    WorkflowResultPayload,
    build_semantic_payload,
    derive_workflow_capability_binding_edges,
    derive_workflow_result_edges,
    semantic_payload_ref,
)
from mozaiksai.core.semantics.plan_authority import (
    CompilationPlanAuthorityInputs,
    PlanAuthorityError,
    build_compilation_plan_authority_inputs,
    validate_compilation_plan_against_authority,
)
from mozaiksai.core.semantics.refs import SemanticGraphRef
from mozaiksai.core.semantics.workflow_interface_materialization import (
    WORKFLOW_INTERFACE_RENDERER_IMPLEMENTATION_ID,
    WORKFLOW_INTERFACE_RENDERER_IMPLEMENTATION_VERSION,
    WorkflowInterfaceRenderInput,
    render_workflow_module_interface_unit,
)
from tests import test_workflow_capability_semantics as capability_fixture

ROOT = Path(__file__).resolve().parents[1]
_WORKFLOW_ID = "analyze_document"
_OTHER_WORKFLOW_ID = "archive_document"
_ADVISORY = "mozaiks.workflow_result.advisory_summary"
_SECOND_CAPABILITY = "mozaiks.workflow_capability.document_notes"
_SECOND_RESULT = "mozaiks.workflow_result.document_notes"
_NEW_EVENT_ID = "domain.documents.imported"


@dataclass
class _Fixture:
    payloads: dict[str, SemanticPayloadBase]
    action_owners: dict[str, str]
    event_identities: dict[str, str]


def _other(node_id: str) -> str:
    namespace, local = node_id.rsplit(".", 1)
    return f"{namespace}.other_{local}"


def _rebuilt(payload: SemanticPayloadBase, **changes: Any) -> SemanticPayloadBase:
    return build_semantic_payload(type(payload), **{**payload.model_dump(mode="json"), **changes})


def _replace(fixture: _Fixture, node_id: str, **changes: Any) -> None:
    fixture.payloads[node_id] = _rebuilt(fixture.payloads[node_id], **changes)


def _result(node_id: str, result_id: str, capability: str) -> SemanticPayloadBase:
    return build_semantic_payload(
        WorkflowResultPayload,
        node_id=node_id,
        payload_version=1,
        scope=capability_fixture._SCOPE,
        result_id=result_id,
        description="An advisory result with no commit binding",
        workflow_capability_node_id=capability,
    )


def _fixture() -> _Fixture:
    payloads = capability_fixture._fixture_payloads()
    payloads[_ADVISORY] = _result(_ADVISORY, "advisory_summary", capability_fixture._CAPABILITY)
    payloads[_SECOND_CAPABILITY] = _rebuilt(
        payloads[capability_fixture._CAPABILITY],
        node_id=_SECOND_CAPABILITY,
        capability_id="documents.notes",
        description="Independent notes capability",
    )
    payloads[_SECOND_RESULT] = _result(_SECOND_RESULT, "notes", _SECOND_CAPABILITY)
    replacements = {
        **{node_id: _other(node_id) for node_id in payloads},
        "documents": "archives",
        _WORKFLOW_ID: _OTHER_WORKFLOW_ID,
        capability_fixture._EVENT_ID: "domain.archives.created",
        "documents.analysis": "archives.analysis",
        "documents.notes": "archives.notes",
    }

    def clone(value: Any) -> Any:
        if isinstance(value, str):
            return replacements.get(value, value)
        if isinstance(value, list):
            return [clone(item) for item in value]
        if isinstance(value, dict):
            return {key: clone(item) for key, item in value.items()}
        return value

    for payload in list(payloads.values()):
        copied = build_semantic_payload(type(payload), **clone(payload.model_dump(mode="json")))
        payloads[copied.node_id] = copied
    action_owners = {
        action: capability_fixture._MODULE
        for action in (
            capability_fixture._ACTION_CREATE,
            capability_fixture._ACTION_GET,
            capability_fixture._ACTION_STORE,
        )
    }
    action_owners.update({_other(action): _other(owner) for action, owner in list(action_owners.items())})
    return _Fixture(
        payloads,
        action_owners,
        {
            capability_fixture._EVENT: capability_fixture._EVENT_ID,
            _other(capability_fixture._EVENT): "domain.archives.created",
        },
    )


def _edges(fixture: _Fixture) -> list[SemanticEdge]:
    edges = [
        SemanticEdge(kind=SemanticEdgeKind.DECLARES, source_node_id=owner, target_node_id=action)
        for action, owner in fixture.action_owners.items()
    ]
    for payload in fixture.payloads.values():
        if isinstance(payload, ActionPayload):
            for node_id, event_id in fixture.event_identities.items():
                if event_id in payload.emits:
                    edges.append(SemanticEdge(
                        kind=SemanticEdgeKind.EMITS,
                        source_node_id=payload.node_id,
                        target_node_id=node_id,
                    ))
        elif isinstance(payload, WorkflowCapabilityPayload):
            edges.append(SemanticEdge(
                kind=SemanticEdgeKind.DECLARES,
                source_node_id=payload.workflow_node_id,
                target_node_id=payload.node_id,
            ))
        elif isinstance(payload, WorkflowResultPayload):
            edges.extend(derive_workflow_result_edges(payload))
        elif isinstance(payload, WorkflowCapabilityBindingPayload):
            edges.extend(derive_workflow_capability_binding_edges(payload))
    return edges


def _state(
    fixture: _Fixture | None = None,
    *,
    scope: PathScope = PathScope.WORKSPACE_ROOT,
    version: int = 1,
    reverse: bool = False,
    reload: bool = False,
) -> dict[str, Any]:
    fixture = fixture or _fixture()
    payloads = list(fixture.payloads.values())
    nodes = [
        SemanticNodeV2(
            node_id=payload.node_id,
            kind=payload.payload_kind,
            payload_ref=semantic_payload_ref(payload),
            taxonomy_references=(
                (TaxonomyReference(category="event", identifier=fixture.event_identities[payload.node_id]),)
                if payload.node_id in fixture.event_identities else ()
            ),
        )
        for payload in payloads
    ]
    edges = _edges(fixture)
    graph = build_semantic_graph_v2(
        graph_id="workflow-interface-proof",
        version=version,
        scope=capability_fixture._SCOPE,
        nodes=list(reversed(nodes)) if reverse else nodes,
        edges=list(reversed(edges)) if reverse else edges,
    )
    if reverse:
        payloads.reverse()
    registry = build_app_layout_registry(())
    selection = CompilationScopeSelection(workflow_manifest_scope=scope)
    plan = derive_compilation_plan(graph=graph, payloads=payloads, registry=registry, scope_selection=selection)
    authority = build_compilation_plan_authority_inputs(
        graph=graph, payloads=payloads, registry=registry, scope_selection=selection,
    )
    if reload:
        plan = CompilationPlan.model_validate_json(plan.model_dump_json())
        authority = CompilationPlanAuthorityInputs.model_validate_json(authority.model_dump_json())
        graph = authority.graph
        payloads = list(authority.payloads)
    binding = build_implementation_binding(
        binding_id="workflow_interface_proof",
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
                artifact_families=("app_ui_page_schema",),
                implementation_id=materialization.PAGE_SCHEMA_RENDERER_IMPLEMENTATION_ID,
                implementation_version=materialization.PAGE_SCHEMA_RENDERER_IMPLEMENTATION_VERSION,
            ),
            RendererSelection(
                materializer_id=MaterializerIdentifier.WORKFLOW_INTERFACE_EXECUTOR,
                artifact_families=("workflow_module_interface",),
                implementation_id=WORKFLOW_INTERFACE_RENDERER_IMPLEMENTATION_ID,
                implementation_version=WORKFLOW_INTERFACE_RENDERER_IMPLEMENTATION_VERSION,
            ),
        ),
    )
    return {
        "plan": plan, "authority_inputs": authority, "graph": graph,
        "payloads": payloads, "binding": binding, "layout_registry": registry,
    }


def _unit(state: dict[str, Any], workflow_id: str = _WORKFLOW_ID):
    return next(
        unit for unit in state["plan"].units
        if unit.family_kind == "workflow_module_interface"
        and unit.disposition is PlanDisposition.RENDER
        and unit.placeholder_values == (("workflow_id", workflow_id),)
    )


def _direct_bytes(state: dict[str, Any], workflow_id: str = _WORKFLOW_ID) -> bytes:
    unit = _unit(state, workflow_id)
    render_input = materialization.project_workflow_interface_render_input(
        unit=unit, payload_by_node={payload.node_id: payload for payload in state["payloads"]},
    )
    return render_workflow_module_interface_unit(unit=unit, render_input=render_input)


def _rematerialize(base: dict[str, Any], successor: dict[str, Any], bundle=None):
    return materialization.rematerialize_plan(
        base_bundle=bundle or materialization.materialize_plan(**base),
        base_plan=base["plan"],
        base_authority_inputs=base["authority_inputs"],
        successor_plan=successor["plan"],
        successor_authority_inputs=successor["authority_inputs"],
        **{key: value for key, value in successor.items() if key not in {"plan", "authority_inputs"}},
    )


def _event_identity_change(fixture: _Fixture, *, other: bool = False) -> None:
    event = _other(capability_fixture._EVENT) if other else capability_fixture._EVENT
    producer = _other(capability_fixture._ACTION_CREATE) if other else capability_fixture._ACTION_CREATE
    identity = "domain.archives.imported" if other else _NEW_EVENT_ID
    fixture.event_identities[event] = identity
    _replace(fixture, producer, emits=(identity,))


def test_exact_payload_edge_and_taxonomy_footprints_and_all_owned_results() -> None:
    fixture = _fixture()
    state = _state(fixture)
    unit = _unit(state)
    owned = {
        capability_fixture._WORKFLOW,
        capability_fixture._CAPABILITY,
        _SECOND_CAPABILITY,
        capability_fixture._RESULT,
        _ADVISORY,
        _SECOND_RESULT,
        capability_fixture._BINDING_TRIGGER,
        capability_fixture._BINDING_READ,
        capability_fixture._BINDING_RESULT,
        capability_fixture._MODULE,
    }
    assert unit.sources == tuple(sorted(
        (PlanSource(node_id=node, payload_digest=fixture.payloads[node].payload_digest) for node in owned),
        key=lambda item: item.node_id,
    ))
    meaning_edges = []
    for node in owned:
        payload = fixture.payloads[node]
        if isinstance(payload, WorkflowCapabilityPayload):
            meaning_edges.append(SemanticEdge(
                kind="declares", source_node_id=capability_fixture._WORKFLOW, target_node_id=node,
            ))
        elif isinstance(payload, WorkflowResultPayload):
            meaning_edges.extend(derive_workflow_result_edges(payload))
        elif isinstance(payload, WorkflowCapabilityBindingPayload):
            meaning_edges.extend(derive_workflow_capability_binding_edges(payload))
    assert unit.edge_sources == tuple(sorted(
        (PlanEdgeSource(edge_identity=edge.edge_identity, **edge.model_dump(mode="json")) for edge in meaning_edges),
        key=lambda item: item.edge_identity,
    ))
    assert unit.taxonomy_sources == (PlanTaxonomySource(
        node_id=capability_fixture._EVENT, category="event", identifier=capability_fixture._EVENT_ID,
    ),)
    document = yaml.safe_load(_direct_bytes(state))
    assert set(document) == {"schema_version", "workflow_id", "capabilities"}
    assert document["schema_version"] == "mozaiks.module_interface.v2"
    capabilities = {item["capability_id"]: item for item in document["capabilities"]}
    assert set(capabilities) == {"documents.analysis", "documents.notes"}
    assert {result["result_id"] for result in capabilities["documents.analysis"]["results"]} == {
        "analysis_result", "advisory_summary",
    }
    assert [result["result_id"] for result in capabilities["documents.notes"]["results"]] == ["notes"]
    assert capabilities["documents.notes"]["bindings"] == []


@pytest.mark.parametrize("reload", [False, True], ids=["live-authorities", "serialized-authorities"])
def test_real_taxonomy_identity_change_rerenders_and_never_copies_old_bytes(reload: bool) -> None:
    base = _state(reload=reload)
    original_bundle = materialization.materialize_plan(**base)
    changed = _fixture()
    _event_identity_change(changed)
    successor = _state(changed, version=2, reload=reload)
    affected = _unit(successor)
    unrelated = _unit(successor, _OTHER_WORKFLOW_ID)
    assert affected.taxonomy_sources
    closure = plan_regeneration_closure(base["plan"], successor["plan"])
    assert affected.unit_id in closure.affected
    assert affected.unit_id not in closure.reusable
    assert unrelated.unit_id in closure.reusable
    refreshed = _rematerialize(base, successor, original_bundle)
    outputs = {output.unit_id: output for output in refreshed.outputs}
    original_outputs = {output.unit_id: output for output in original_bundle.outputs}
    assert outputs[affected.unit_id].origin == "rendered"
    assert outputs[affected.unit_id].content != original_outputs[affected.unit_id].content
    assert _NEW_EVENT_ID.encode() in outputs[affected.unit_id].content
    assert capability_fixture._EVENT_ID.encode() not in outputs[affected.unit_id].content
    assert outputs[unrelated.unit_id].origin == "reused"
    assert outputs[unrelated.unit_id].content == original_outputs[unrelated.unit_id].content
    assert refreshed.files() == materialization.materialize_plan(**successor).files()


def _mutate(fixture: _Fixture, change: str) -> None:
    if change == "capability_added":
        node = "mozaiks.workflow_capability.extra_analysis"
        fixture.payloads[node] = _rebuilt(
            fixture.payloads[capability_fixture._CAPABILITY],
            node_id=node, capability_id="documents.extra_analysis",
        )
    elif change == "capability_removed":
        fixture.payloads.pop(_SECOND_CAPABILITY)
        fixture.payloads.pop(_SECOND_RESULT)
    elif change in {"capability_id", "capability_description"}:
        field = "capability_id" if change == "capability_id" else "description"
        _replace(fixture, capability_fixture._CAPABILITY, **{field: "documents.changed"})
    elif change in {"owned_result_added", "advisory_result_added"}:
        node = "mozaiks.workflow_result.extra_advisory"
        fixture.payloads[node] = _result(node, "extra_advisory", capability_fixture._CAPABILITY)
    elif change == "owned_result_removed":
        fixture.payloads.pop(capability_fixture._RESULT)
        fixture.payloads.pop(capability_fixture._BINDING_RESULT)
    elif change == "advisory_result_removed":
        fixture.payloads.pop(_ADVISORY)
    elif change in {"result_id", "result_description", "advisory_id", "advisory_description"}:
        node = _ADVISORY if change.startswith("advisory") else capability_fixture._RESULT
        field = "result_id" if change.endswith("_id") else "description"
        _replace(fixture, node, **{field: "updated_result"})
    elif change == "commit_added":
        node = "mozaiks.workflow_capability_binding.extra_commit"
        fixture.payloads[node] = _rebuilt(
            fixture.payloads[capability_fixture._BINDING_RESULT],
            node_id=node,
            module_action=ModuleActionRef(
                module_node_id=capability_fixture._MODULE,
                action_node_id=capability_fixture._ACTION_GET,
            ),
        )
    elif change == "commit_removed":
        fixture.payloads.pop(capability_fixture._BINDING_RESULT)
    elif change in {"commit_changed", "consumes_action_changed"}:
        node = (
            capability_fixture._BINDING_RESULT
            if change == "commit_changed" else capability_fixture._BINDING_READ
        )
        _replace(fixture, node, module_action=ModuleActionRef(
            module_node_id=capability_fixture._MODULE,
            action_node_id=capability_fixture._ACTION_CREATE,
        ))
    elif change == "trigger_changed":
        _replace(
            fixture, capability_fixture._BINDING_TRIGGER,
            event_node_id=_other(capability_fixture._EVENT),
        )
    elif change == "module_id":
        _replace(fixture, capability_fixture._MODULE, module_id="files")
    elif change == "action_node_identity":
        old = capability_fixture._ACTION_GET
        new = "mozaiks.action.documents_fetch_content"
        fixture.payloads[new] = _rebuilt(fixture.payloads.pop(old), node_id=new)
        fixture.action_owners[new] = fixture.action_owners.pop(old)
        _replace(fixture, capability_fixture._BINDING_READ, module_action=ModuleActionRef(
            module_node_id=capability_fixture._MODULE, action_node_id=new,
        ))
    elif change == "event_identity":
        _event_identity_change(fixture)
    elif change in {"action_body", "action_request_schema", "action_response_schema"}:
        field = {
            "action_body": "description",
            "action_request_schema": "request_contract",
            "action_response_schema": "response_fields",
        }[change]
        if field == "description":
            value = "Different implementation description"
        elif field == "request_contract":
            value = {
                "kind": "object", "nullable": False, "additional_properties": False,
                "properties": [{
                    "name": "changed_field", "required": True,
                    "contract": {"kind": "array", "nullable": False,
                                 "items": {"kind": "string", "nullable": True}},
                }],
            }
        else:
            value = [{"name": "changed_field", "field_type": "string", "required": False}]
        _replace(fixture, capability_fixture._ACTION_GET, **{field: value})
    elif change in {"event_body", "event_schema"}:
        values = (
            {"description": "Changed event payload body"}
            if change == "event_body" else {
                "payload_fields": [{"name": "extra", "field_type": "string", "required": False}],
            }
        )
        _replace(fixture, capability_fixture._EVENT, **values)
    elif change == "producer_action_body":
        _replace(fixture, capability_fixture._ACTION_CREATE, description="Changed producer implementation")
    elif change.startswith("unrelated_"):
        kind = change.removeprefix("unrelated_")
        if kind == "event_identity":
            _event_identity_change(fixture, other=True)
            return
        node = {
            "workflow": capability_fixture._WORKFLOW,
            "capability": capability_fixture._CAPABILITY,
            "result": capability_fixture._RESULT,
            "module": capability_fixture._MODULE,
            "action": capability_fixture._ACTION_GET,
            "event": capability_fixture._EVENT,
        }[kind]
        _replace(fixture, _other(node), description="Only another workflow's facts changed")
    elif change == "order_only":
        fixture.payloads = dict(reversed(list(fixture.payloads.items())))
        fixture.event_identities = dict(reversed(list(fixture.event_identities.items())))
        fixture.action_owners = dict(reversed(list(fixture.action_owners.items())))
    else:
        raise AssertionError(f"Unknown test mutation {change}")


_AFFECTED_CHANGES = (
    "capability_added", "capability_removed", "capability_id", "capability_description",
    "owned_result_added", "owned_result_removed", "result_id", "result_description",
    "advisory_result_added", "advisory_result_removed", "advisory_id", "advisory_description",
    "commit_added", "commit_removed", "commit_changed", "consumes_action_changed",
    "trigger_changed", "module_id", "action_node_identity", "event_identity",
)
_REUSABLE_CHANGES = (
    "action_body", "action_request_schema", "action_response_schema", "producer_action_body",
    "event_body", "event_schema", "unrelated_workflow", "unrelated_capability",
    "unrelated_result", "unrelated_module", "unrelated_action", "unrelated_event",
    "unrelated_event_identity", "order_only",
)


@pytest.mark.parametrize("change", (*_AFFECTED_CHANGES, *_REUSABLE_CHANGES))
def test_selective_interface_rematerialization_matrix(change: str) -> None:
    base = _state()
    base_bundle = materialization.materialize_plan(**base)
    fixture = _fixture()
    _mutate(fixture, change)
    successor = _state(fixture, version=2, reverse=change == "order_only")
    target = _unit(successor)
    unaffected = _unit(successor, _OTHER_WORKFLOW_ID)
    expected_affected = change in _AFFECTED_CHANGES
    closure = plan_regeneration_closure(base["plan"], successor["plan"])
    assert (target.unit_id in closure.affected) is expected_affected
    assert (target.unit_id in closure.reusable) is not expected_affected
    refreshed = _rematerialize(base, successor, base_bundle)
    output_by_id = {output.unit_id: output for output in refreshed.outputs}
    before_by_id = {output.unit_id: output for output in base_bundle.outputs}
    target_output = output_by_id[target.unit_id]
    assert target_output.origin == ("rendered" if expected_affected else "reused")
    if expected_affected:
        assert target_output.content != before_by_id[target.unit_id].content
        assert unaffected.unit_id in closure.reusable
        assert output_by_id[unaffected.unit_id].origin == "reused"
    else:
        assert target_output.content == before_by_id[target.unit_id].content
    assert refreshed.files() == materialization.materialize_plan(**successor).files()


@pytest.mark.parametrize("node_id", [capability_fixture._WORKFLOW, capability_fixture._MODULE])
def test_whole_included_payload_pins_conservatively_invalidate_unused_descriptions(node_id: str) -> None:
    base = _state()
    fixture = _fixture()
    _replace(fixture, node_id, description="An included payload field absent from interface bytes")
    successor = _state(fixture, version=2)
    unit_id = _unit(successor).unit_id
    assert unit_id in plan_regeneration_closure(base["plan"], successor["plan"]).affected
    refreshed = _rematerialize(base, successor)
    assert next(output for output in refreshed.outputs if output.unit_id == unit_id).origin == "rendered"
    assert _direct_bytes(base) == _direct_bytes(successor)


def test_commit_fan_out_keeps_one_result_ownership_row_and_advisory_result() -> None:
    fixture = _fixture()
    _mutate(fixture, "commit_added")
    document = yaml.safe_load(_direct_bytes(_state(fixture)))
    capability = next(item for item in document["capabilities"] if item["capability_id"] == "documents.analysis")
    assert [result["result_id"] for result in capability["results"]] == ["advisory_summary", "analysis_result"]
    commits = [binding for binding in capability["bindings"] if binding["role"] == "commits_result_through_action"]
    assert len(commits) == 2
    assert {binding["workflow_result_id"] for binding in commits} == {"analysis_result"}
    assert {binding["action_node_id"] for binding in commits} == {
        capability_fixture._ACTION_GET, capability_fixture._ACTION_STORE,
    }


def _forged_taxonomy_plan(state: dict[str, Any], change: str) -> CompilationPlan:
    plan = state["plan"]
    target_id = _unit(state).unit_id
    document = plan.model_dump(mode="json")
    unit = next(unit for unit in document["units"] if unit["unit_id"] == target_id)
    original = unit["taxonomy_sources"][0]
    alternate = {
        "node_id": _other(capability_fixture._EVENT),
        "category": "event",
        "identifier": "domain.archives.created",
    }
    if change == "missing":
        unit.pop("taxonomy_sources")
    elif change == "surplus":
        unit["taxonomy_sources"] = sorted([original, alternate], key=lambda item: item["node_id"])
    elif change == "stale":
        unit["taxonomy_sources"] = [{**original, "identifier": _NEW_EVENT_ID}]
    else:
        assert change == "substituted"
        unit["taxonomy_sources"] = [alternate]
    document["plan_digest"] = canonical_digest({key: value for key, value in document.items() if key != "plan_digest"})
    return CompilationPlan.model_validate_json(json.dumps(document))


@pytest.mark.parametrize("change", ["missing", "surplus", "stale", "substituted"])
def test_redigested_taxonomy_forgeries_reject_before_render_or_historical_reuse(change: str, monkeypatch) -> None:
    state = _state(reload=True)
    bundle = materialization.materialize_plan(**state)
    forged = _forged_taxonomy_plan(state, change)
    assert forged.plan_digest != state["plan"].plan_digest
    with pytest.raises(PlanAuthorityError):
        validate_compilation_plan_against_authority(forged, state["authority_inputs"])

    def forbidden_renderer(**_kwargs):
        raise AssertionError("Canonical authority must reject before any renderer runs")

    monkeypatch.setattr(materialization, "render_workflow_module_interface_unit", forbidden_renderer)
    forged_state = {**state, "plan": forged}
    with pytest.raises(materialization.MaterializationError, match="canonical authority"):
        materialization.materialize_plan(**forged_state)
    with pytest.raises(materialization.MaterializationError, match="canonical authority"):
        _rematerialize(state, forged_state, bundle)
    with pytest.raises(materialization.MaterializationError, match="canonical authority"):
        _rematerialize(forged_state, state, bundle)


@pytest.mark.parametrize("scope", [PathScope.WORKSPACE_ROOT, PathScope.WORKFLOW_RELATIVE])
def test_interface_bytes_deterministic_across_process_order_and_all_round_trips(scope: PathScope) -> None:
    state = _state(scope=scope)
    expected = _direct_bytes(state)
    again = _state(scope=scope)
    reordered = _state(scope=scope, reverse=True)
    reloaded = _state(scope=scope, reload=True)
    assert _direct_bytes(again) == _direct_bytes(reordered) == _direct_bytes(reloaded) == expected
    assert again["plan"] == reordered["plan"] == reloaded["plan"] == state["plan"]
    unit = _unit(state)
    projected = materialization.project_workflow_interface_render_input(
        unit=unit, payload_by_node={payload.node_id: payload for payload in state["payloads"]},
    )
    raw_input = projected.model_dump(mode="json")
    raw_input["sources"].reverse()
    raw_input["edge_sources"].reverse()
    raw_input["taxonomy_sources"].reverse()
    raw_input["capabilities"].reverse()
    for capability in raw_input["capabilities"]:
        capability["results"].reverse()
        capability["bindings"].reverse()
    reloaded_input = WorkflowInterfaceRenderInput.model_validate_json(json.dumps(raw_input))
    assert reloaded_input == projected
    assert render_workflow_module_interface_unit(unit=unit, render_input=reloaded_input) == expected
    probe = (
        "from tests.test_workflow_interface_rematerialization import _state, _direct_bytes\n"
        "from mozaiksai.core.runtime.app.layout_registry import PathScope\n"
        f"print(_direct_bytes(_state(scope=PathScope({scope.value!r}))).hex())\n"
    )
    completed = subprocess.run([sys.executable, "-c", probe], cwd=ROOT, capture_output=True, text=True, timeout=60)
    assert completed.returncode == 0, completed.stderr
    assert bytes.fromhex(completed.stdout.strip()) == expected


def test_equivalent_canonical_taxonomy_spelling_and_order_reuse_historical_bytes() -> None:
    fixture = _fixture()
    second_trigger = "mozaiks.workflow_capability_binding.analysis_on_archive"
    fixture.payloads[second_trigger] = _rebuilt(
        fixture.payloads[capability_fixture._BINDING_TRIGGER],
        node_id=second_trigger, event_node_id=_other(capability_fixture._EVENT),
    )
    base = _state(fixture)
    assert len(_unit(base).taxonomy_sources) == 2
    successor = _state(fixture, version=2, reverse=True, reload=True)
    document = successor["plan"].model_dump(mode="json")
    for unit in document["units"]:
        if "taxonomy_sources" in unit:
            unit["taxonomy_sources"].reverse()
        for taxonomy in unit.get("taxonomy_sources", []):
            taxonomy["identifier"] = f"  {taxonomy['identifier']}  "
    successor["plan"] = CompilationPlan.model_validate_json(json.dumps(document))
    validate_compilation_plan_against_authority(successor["plan"], successor["authority_inputs"])
    refreshed = _rematerialize(base, successor)
    assert all(output.origin == "reused" for output in refreshed.outputs)
    assert refreshed.files() == materialization.materialize_plan(**base).files()


def test_runtime_registry_changes_cannot_affect_interface_reuse(monkeypatch, tmp_path) -> None:
    from mozaiksai.core.workflow import workflow_manager

    base = _state()
    bundle = materialization.materialize_plan(**base)
    runtime_registry = tmp_path / "extension_registry.json"
    runtime_registry.write_text('{"entrypoints": [{"id": "different_runtime_workflow"}]}', encoding="utf-8")
    monkeypatch.setenv("MOZAIKS_WORKFLOWS_PATH", str(tmp_path))

    def forbidden_registry():
        raise AssertionError("Compiler interface must not consult the runtime workflow registry")

    monkeypatch.setattr(workflow_manager, "get_workflow_manager", forbidden_registry)
    successor = _state(version=2)
    refreshed = _rematerialize(base, successor, bundle)
    assert all(output.origin == "reused" for output in refreshed.outputs)
    assert refreshed.files() == bundle.files()
