"""Exact pre-family corpus proof captured from authoritative main #481/#482."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from mozaiksai.core.runtime.app.layout_registry import build_app_layout_registry
from mozaiksai.core.semantics.canonical import canonical_digest
from mozaiksai.core.semantics.compilation_plan import (
    CompilationPlan,
    FamilyInstancePlan,
    LayoutRegistrySnapshot,
    PlanDisposition,
    RegistryFamilyRow,
    derive_compilation_plan,
    snapshot_layout_registry,
)
from mozaiksai.core.semantics.payloads import WorkflowPayload
from tests.test_semantic_payload_graph_v2 import _corpus_graph

_FIXTURE = Path(__file__).parent / "fixtures/workflow_interface_pre_family_corpus.json"
_BASE_COMMIT = "71c355a728a1470286d02f23eba666bfe93a5ff9"
_BASE_UNIT_COUNT = 59
_BASE_PLAN_DIGEST = "4e3a809b0982e18fa24f85da753a1df9734ae38132199c0be5931263fbd202f2"
_BASE_UNIT_PROOF_DIGEST = "c27885233d404cf253c80ad6e4fb4f44309b444a4efe41eb80461e11c601d5ea"
_INTERFACE_FAMILY = "workflow_module_interface"


def _baseline() -> dict[str, Any]:
    return json.loads(_FIXTURE.read_text(encoding="utf-8"))


def _unit_proof(unit: FamilyInstancePlan, row: RegistryFamilyRow) -> dict[str, Any]:
    """Capture every requested representation independently, including omissions."""
    return {
        "unit_id": unit.unit_id,
        "canonical_layout_row": row.model_dump(mode="json"),
        "family_identity_digest": unit.family_identity_digest,
        "identity_payload": unit.identity_payload,
        "model_dump_json_mode": unit.model_dump(mode="json"),
        "serialized_json": unit.model_dump_json(),
        "unit_digest": unit.unit_digest,
        "placeholder_values": [list(pair) for pair in unit.placeholder_values],
        "depends_on_units": list(unit.depends_on_units),
        "outputs": [item.model_dump(mode="json") for item in unit.outputs],
        "sources": [item.model_dump(mode="json") for item in unit.sources],
        "edge_sources": [item.model_dump(mode="json") for item in unit.edge_sources],
        "taxonomy_sources": [item.model_dump(mode="json") for item in unit.taxonomy_sources],
    }


def test_pre_family_capture_is_complete_and_self_consistent() -> None:
    baseline = _baseline()
    assert baseline["base_commit"] == _BASE_COMMIT
    assert baseline["unit_count"] == _BASE_UNIT_COUNT
    assert baseline["plan_digest"] == _BASE_PLAN_DIGEST
    assert baseline["unit_proof_digest"] == _BASE_UNIT_PROOF_DIGEST
    assert canonical_digest(baseline["units"]) == _BASE_UNIT_PROOF_DIGEST
    plan = CompilationPlan.model_validate(baseline["plan"])
    snapshot = LayoutRegistrySnapshot.model_validate(baseline["registry_snapshot"])
    assert plan.plan_digest == baseline["plan_digest"]
    assert plan.registry_digest == snapshot.snapshot_digest
    assert len(plan.units) == _BASE_UNIT_COUNT
    assert all(row.kind != _INTERFACE_FAMILY for row in snapshot.rows)
    row_by_digest = {row.row_digest: row for row in snapshot.rows}
    assert [
        _unit_proof(unit, row_by_digest[unit.family_identity_digest])
        for unit in plan.units
    ] == baseline["units"]


def test_every_pre_existing_corpus_unit_retains_exact_identity_and_bytes() -> None:
    baseline = _baseline()
    graph, payloads = _corpus_graph()
    registry = build_app_layout_registry(())
    plan = derive_compilation_plan(graph=graph, payloads=payloads, registry=registry)
    snapshot = snapshot_layout_registry(registry)
    row_by_digest = {row.row_digest: row for row in snapshot.rows}
    old_ids = {unit["unit_id"] for unit in baseline["units"]}
    current_by_id = {unit.unit_id: unit for unit in plan.units}
    assert old_ids <= current_by_id.keys(), "A pre-existing unit was deleted or moved"
    existing = [unit for unit in plan.units if unit.unit_id in old_ids]
    proof = [_unit_proof(unit, row_by_digest[unit.family_identity_digest]) for unit in existing]
    assert len(proof) == _BASE_UNIT_COUNT
    # Complete record equality, not merely a digest comparison: this checks the
    # canonical row, both identity representations, exact serialized JSON, all
    # placeholders, dependencies, outputs, and all three source footprints.
    assert proof == baseline["units"]
    assert canonical_digest(proof) == _BASE_UNIT_PROOF_DIGEST
    for before in baseline["units"]:
        after = current_by_id[before["unit_id"]]
        assert after.model_dump_json().encode("utf-8") == before["serialized_json"].encode("utf-8")
        assert after.taxonomy_sources == ()
        assert "taxonomy_sources" not in after.model_dump(mode="json")


def test_only_canonical_interface_rows_and_their_units_extend_the_corpus() -> None:
    baseline = _baseline()
    graph, payloads = _corpus_graph()
    registry = build_app_layout_registry(())
    plan = derive_compilation_plan(graph=graph, payloads=payloads, registry=registry)
    snapshot = snapshot_layout_registry(registry)
    old_snapshot = LayoutRegistrySnapshot.model_validate(baseline["registry_snapshot"])
    old_row_digests = {row.row_digest for row in old_snapshot.rows}
    assert [row for row in snapshot.rows if row.row_digest in old_row_digests] == list(old_snapshot.rows)
    new_rows = [row for row in snapshot.rows if row.row_digest not in old_row_digests]
    assert len(new_rows) == 2
    assert {row.kind for row in new_rows} == {_INTERFACE_FAMILY}
    assert {(row.path_scope, row.path_template) for row in new_rows} == {
        ("workspace_root", "workflows/{workflow_id}/module_interface.yaml"),
        ("workflow_relative", "module_interface.yaml"),
    }
    workflow_ids = {payload.workflow_id for payload in payloads if isinstance(payload, WorkflowPayload)}
    assert workflow_ids == {"digest"}
    old_unit_ids = {unit["unit_id"] for unit in baseline["units"]}
    new_units = {unit.unit_id: unit for unit in plan.units if unit.unit_id not in old_unit_ids}
    expected_ids = set()
    for row in new_rows:
        active = row.path_scope == plan.scope_selection.workflow_manifest_scope.value
        if not active:
            unit_id = f"{_INTERFACE_FAMILY}/scope_inactive/{row.row_digest[:12]}"
            expected_ids.add(unit_id)
            unit = new_units[unit_id]
            assert unit.family_kind == _INTERFACE_FAMILY
            assert unit.family_identity_digest == row.row_digest
            assert unit.disposition is PlanDisposition.INAPPLICABLE
            assert unit.outputs == ()
            assert unit.placeholder_values == ()
            continue
        for workflow_id in workflow_ids:
            unit_id = f"{_INTERFACE_FAMILY}/{workflow_id}/{row.row_digest[:12]}"
            expected_ids.add(unit_id)
            unit = new_units[unit_id]
            assert unit.family_kind == _INTERFACE_FAMILY
            assert unit.family_identity_digest == row.row_digest
            assert unit.placeholder_values == (("workflow_id", workflow_id),)
            assert len(unit.outputs) == 1
            assert unit.outputs[0].path_scope == row.path_scope
            assert unit.outputs[0].path == row.path_template.format(workflow_id=workflow_id)
    assert new_units.keys() == expected_ids
    assert len(plan.units) == _BASE_UNIT_COUNT + len(expected_ids)
