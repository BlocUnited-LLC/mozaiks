"""Direct renderer shape authority, closed inputs, and canonical interface bytes."""

from __future__ import annotations

import json
import subprocess
import sys

import pytest
import yaml
from pydantic import ValidationError

from mozaiksai.core.runtime.app.layout_registry import (
    ValidatorIdentifier,
    build_app_layout_registry,
)
from mozaiksai.core.semantics.canonical import canonical_digest
from mozaiksai.core.semantics.compilation_plan import (
    FamilyInstancePlan,
    PlanDisposition,
    PlanEdgeSource,
    PlanOutput,
    PlanSource,
    PlanSourceScope,
    PlanTaxonomySource,
    canonical_instance_unit_id,
    snapshot_layout_registry,
)
from mozaiksai.core.semantics.workflow_interface_materialization import (
    WORKFLOW_MODULE_INTERFACE,
    RenderInputCommitsResultBinding,
    RenderInputConsumesActionBinding,
    RenderInputTriggeredByEventBinding,
    RenderInputWorkflowCapability,
    RenderInputWorkflowResult,
    WorkflowInterfaceMaterializationError,
    WorkflowInterfaceRenderInput,
    render_workflow_module_interface_unit,
)


def _rows():
    return {
        row.path_scope: row
        for row in snapshot_layout_registry(build_app_layout_registry(())).rows
        if row.kind == WORKFLOW_MODULE_INTERFACE
    }


def _renderer_case(path_scope="workspace_root"):
    row = _rows()[path_scope]
    sources = (
        PlanSource(node_id="workflow.orders", payload_digest="1" * 64),
        PlanSource(node_id="capability.orders", payload_digest="2" * 64),
    )
    edge_facts = {
        "kind": "declares",
        "source_node_id": "workflow.orders",
        "target_node_id": "capability.orders",
        "discriminator": None,
    }
    edges = (PlanEdgeSource(**edge_facts, edge_identity=canonical_digest(edge_facts)),)
    taxonomy = (
        PlanTaxonomySource(
            node_id="event.order_created", category="event", identifier="domain.order.created"
        ),
    )
    unit = FamilyInstancePlan(
        unit_id=canonical_instance_unit_id(row.kind, "orders", row.row_digest),
        family_kind=row.kind,
        family_identity_digest=row.row_digest,
        disposition=PlanDisposition.RENDER,
        source_scope=PlanSourceScope.DECLARED,
        placeholder_values=(("workflow_id", "orders"),),
        outputs=(
            PlanOutput(
                path_scope=row.path_scope,
                path=row.path_template.replace("{workflow_id}", "orders"),
            ),
        ),
        sources=sources,
        edge_sources=edges,
        taxonomy_sources=taxonomy,
        materializer=row.materializer,
        validator=row.validator,
    )
    render_input = WorkflowInterfaceRenderInput(
        workflow_id="orders",
        sources=sources,
        edge_sources=edges,
        taxonomy_sources=taxonomy,
        capabilities=(
            RenderInputWorkflowCapability(
                capability_id="orders.analysis",
                description="Analyze orders",
                results=(
                    RenderInputWorkflowResult(result_id="summary", description="Order summary"),
                    RenderInputWorkflowResult(result_id="advice"),
                ),
                bindings=(
                    RenderInputTriggeredByEventBinding(event_type="domain.order.created"),
                    RenderInputConsumesActionBinding(module_id="orders", action_node_id="action.list"),
                    RenderInputCommitsResultBinding(
                        module_id="orders",
                        action_node_id="action.save",
                        workflow_result_id="summary",
                    ),
                    RenderInputCommitsResultBinding(
                        module_id="orders",
                        action_node_id="action.archive",
                        workflow_result_id="summary",
                    ),
                ),
            ),
        ),
    )
    return unit, render_input


@pytest.mark.parametrize("scope", ["workspace_root", "workflow_relative"])
def test_exact_document_keeps_advisory_results_and_action_node_identity(scope):
    unit, render_input = _renderer_case(scope)
    rendered = render_workflow_module_interface_unit(unit=unit, render_input=render_input)
    document = yaml.safe_load(rendered)
    assert document == {
        "schema_version": "mozaiks.module_interface.v2",
        "workflow_id": "orders",
        "capabilities": [
            {
                "capability_id": "orders.analysis",
                "description": "Analyze orders",
                "results": [
                    {"result_id": "advice"},
                    {"result_id": "summary", "description": "Order summary"},
                ],
                "bindings": [
                    {
                        "role": "commits_result_through_action",
                        "module_id": "orders",
                        "action_node_id": "action.archive",
                        "workflow_result_id": "summary",
                    },
                    {
                        "role": "commits_result_through_action",
                        "module_id": "orders",
                        "action_node_id": "action.save",
                        "workflow_result_id": "summary",
                    },
                    {
                        "role": "consumes_action",
                        "module_id": "orders",
                        "action_node_id": "action.list",
                    },
                    {"role": "triggered_by_event", "event_type": "domain.order.created"},
                ],
            },
        ],
    }
    assert rendered.endswith(b"\n")
    assert b"\r" not in rendered


@pytest.mark.parametrize("scope", ["workspace_root", "workflow_relative"])
@pytest.mark.parametrize(
    "attack",
    [
        "app_bundle_root",
        "generated_staging",
        "module_relative",
        "other_twin_output",
        "extra_placeholder",
        "missing_placeholder",
        "substituted_placeholder",
        "wrong_digest_suffix",
        "other_row_digest",
        "other_unit_id",
        "correct_path_wrong_scope",
        "correct_scope_foreign_path",
        "input_workflow_mismatch",
        "surplus_output",
        "missing_output",
        "wrong_family",
        "wrong_materializer",
        "wrong_disposition",
        "wrong_validator",
        "unknown_row_digest",
        "invalid_workflow_grammar",
    ],
)
def test_hostile_output_identity_matrix_rejects(scope, attack):
    unit, render_input = _renderer_case(scope)
    other_scope = "workflow_relative" if scope == "workspace_root" else "workspace_root"
    other_unit, _ = _renderer_case(other_scope)
    output = unit.outputs[0]
    updates = {}
    if attack in {"app_bundle_root", "generated_staging", "module_relative"}:
        updates["outputs"] = (PlanOutput(path_scope=attack, path=output.path),)
    elif attack == "other_twin_output":
        # Coordinated scope AND path substitution retains the original row identity.
        updates["outputs"] = other_unit.outputs
    elif attack == "extra_placeholder":
        updates["placeholder_values"] = (*unit.placeholder_values, ("module_id", "orders"))
    elif attack == "missing_placeholder":
        updates["placeholder_values"] = ()
    elif attack == "substituted_placeholder":
        updates["placeholder_values"] = (("workflow_id", "foreign"),)
    elif attack == "wrong_digest_suffix":
        updates["unit_id"] = f"workflow_module_interface/orders/{'0' * 12}"
    elif attack == "other_row_digest":
        updates["family_identity_digest"] = other_unit.family_identity_digest
    elif attack == "other_unit_id":
        updates["unit_id"] = other_unit.unit_id
    elif attack == "correct_path_wrong_scope":
        updates["outputs"] = (PlanOutput(path_scope=other_scope, path=output.path),)
    elif attack == "correct_scope_foreign_path":
        updates["outputs"] = (
            PlanOutput(path_scope=scope, path="workflows/foreign/module_interface.yaml"),
        )
    elif attack == "input_workflow_mismatch":
        render_input = render_input.model_copy(update={"workflow_id": "foreign"})
    elif attack == "surplus_output":
        updates["outputs"] = (*unit.outputs, PlanOutput(path_scope=scope, path="extra.yaml"))
    elif attack == "missing_output":
        updates["outputs"] = ()
    elif attack == "wrong_family":
        updates["family_kind"] = "workflow_manifest"
    elif attack == "wrong_materializer":
        updates["materializer"] = "workflow_generator"
    elif attack == "wrong_disposition":
        updates["disposition"] = PlanDisposition.INPUT_ONLY
    elif attack == "wrong_validator":
        updates["validator"] = ValidatorIdentifier.WORKFLOW_MANAGER
    elif attack == "unknown_row_digest":
        updates["family_identity_digest"] = "0" * 64
    elif attack == "invalid_workflow_grammar":
        updates["placeholder_values"] = (("workflow_id", "../Orders"),)
    else:
        raise AssertionError(attack)
    forged = unit.model_copy(update=updates)
    with pytest.raises(WorkflowInterfaceMaterializationError):
        render_workflow_module_interface_unit(unit=forged, render_input=render_input)


def test_complete_alternate_canonical_unit_is_recognized_as_that_shape():
    workspace_unit, workspace_input = _renderer_case("workspace_root")
    relative_unit, relative_input = _renderer_case("workflow_relative")
    assert workspace_unit.unit_id != relative_unit.unit_id
    assert render_workflow_module_interface_unit(
        unit=workspace_unit, render_input=workspace_input
    ) == render_workflow_module_interface_unit(unit=relative_unit, render_input=relative_input)


@pytest.mark.parametrize("field", ["sources", "edge_sources", "taxonomy_sources"])
@pytest.mark.parametrize("attack", ["missing", "surplus", "stale", "substituted"])
def test_exact_pinned_footprint_mismatch_rejects(field, attack):
    unit, render_input = _renderer_case()
    values = getattr(unit, field)
    first = values[0]
    if field == "sources":
        changed = first.model_copy(
            update={"payload_digest": "f" * 64}
            if attack == "stale"
            else {"node_id": "workflow.foreign"}
        )
    elif field == "taxonomy_sources":
        changed = first.model_copy(
            update={"identifier": "domain.order.updated"}
            if attack == "stale"
            else {"node_id": "event.foreign"}
        )
    else:
        facts = first.model_dump(mode="json", exclude={"edge_identity"})
        facts["target_node_id"] = "capability.foreign"
        if attack == "stale":
            facts["discriminator"] = "result.foreign"
        changed = PlanEdgeSource(**facts, edge_identity=canonical_digest(facts))
    forged_values = (
        values[1:]
        if attack == "missing"
        else (*values, changed)
        if attack == "surplus"
        else (changed, *values[1:])
    )
    forged = unit.model_copy(update={field: forged_values})
    with pytest.raises(WorkflowInterfaceMaterializationError):
        render_workflow_module_interface_unit(unit=forged, render_input=render_input)


@pytest.mark.parametrize("path", [(), ("capabilities", 0), ("capabilities", 0, "results", 0), ("capabilities", 0, "bindings", 0)])
def test_closed_input_rejects_unowned_fields_at_every_document_level(path):
    _, render_input = _renderer_case()
    raw = render_input.model_dump(mode="json")
    target = raw
    for part in path:
        target = target[part]
    target["runtime_identity"] = "unowned"
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        WorkflowInterfaceRenderInput.model_validate(raw)


@pytest.mark.parametrize(
    "binding",
    [
        {"role": "unknown_role", "event_type": "domain.order.created"},
        {"role": "consumes_action", "module_id": "orders"},
        {"role": "consumes_action", "module_id": "orders", "action_node_id": "action.list", "event_type": "domain.order.created"},
        {"role": "triggered_by_event", "event_type": "bad event"},
        {"role": "commits_result_through_action", "module_id": "orders", "action_node_id": "action.save", "workflow_result_id": "foreign"},
    ],
)
def test_binding_roles_are_closed_and_commits_cannot_reference_foreign_results(binding):
    _, render_input = _renderer_case()
    raw = render_input.model_dump(mode="json")
    raw["capabilities"][0]["bindings"] = [binding]
    with pytest.raises(ValidationError):
        WorkflowInterfaceRenderInput.model_validate(raw)


@pytest.mark.parametrize("field", ["capabilities", "sources", "edge_sources", "taxonomy_sources", "results", "bindings"])
def test_duplicate_identities_reject(field):
    _, render_input = _renderer_case()
    raw = render_input.model_dump(mode="json")
    target = raw["capabilities"][0] if field in {"results", "bindings"} else raw
    target[field].append(target[field][0])
    with pytest.raises(ValidationError, match="duplicate"):
        WorkflowInterfaceRenderInput.model_validate(raw)


def test_direct_entry_cold_validates_frozen_objects_and_nested_inputs():
    unit, render_input = _renderer_case()
    with pytest.raises(ValidationError, match="frozen"):
        render_input.workflow_id = "foreign"
    with pytest.raises(ValidationError, match="frozen"):
        render_input.capabilities[0].description = "Changed"
    forged_binding = render_input.capabilities[0].bindings[0].model_copy(
        update={"action_node_id": "not a node"}
    )
    forged_capability = render_input.capabilities[0].model_copy(update={"bindings": (forged_binding,)})
    forged_input = render_input.model_copy(update={"capabilities": (forged_capability,)})
    with pytest.raises(WorkflowInterfaceMaterializationError, match="cold validation"):
        render_workflow_module_interface_unit(unit=unit, render_input=forged_input)


@pytest.mark.parametrize("scope", ["workspace_root", "workflow_relative"])
def test_ordering_repeated_render_and_serialized_round_trip_are_byte_identical(scope):
    unit, render_input = _renderer_case(scope)
    baseline = render_workflow_module_interface_unit(unit=unit, render_input=render_input)
    raw = render_input.model_dump(mode="json")
    for field in ("sources", "edge_sources", "taxonomy_sources", "capabilities"):
        raw[field].reverse()
    for capability in raw["capabilities"]:
        capability["results"].reverse()
        capability["bindings"].reverse()
    shuffled_input = WorkflowInterfaceRenderInput.model_validate(raw)
    assert shuffled_input == render_input
    for _ in range(3):
        assert render_workflow_module_interface_unit(unit=unit, render_input=shuffled_input) == baseline
    assert render_workflow_module_interface_unit(
        unit=FamilyInstancePlan.model_validate_json(unit.model_dump_json()),
        render_input=WorkflowInterfaceRenderInput.model_validate_json(shuffled_input.model_dump_json()),
    ) == baseline


def test_repeated_fresh_processes_produce_identical_interface_bytes():
    script = """
import json
from tests.test_workflow_interface_renderer import _renderer_case
from mozaiksai.core.semantics.workflow_interface_materialization import render_workflow_module_interface_unit
values = []
for scope in ('workspace_root', 'workflow_relative'):
    unit, render_input = _renderer_case(scope)
    values.append(render_workflow_module_interface_unit(unit=unit, render_input=render_input).decode())
print(json.dumps(values))
"""
    first = subprocess.check_output([sys.executable, "-c", script], text=True)
    second = subprocess.check_output([sys.executable, "-c", script], text=True)
    assert first == second
    workspace, relative = json.loads(first)
    assert workspace == relative
