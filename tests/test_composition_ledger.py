from __future__ import annotations

import dataclasses

import pytest
from pydantic import ValidationError

from mozaiksai.core.semantics.compilation_plan import PlanDisposition
from mozaiksai.core.semantics.composition_ledger import (
    CompositionLedger,
    CompositionOutcome,
    compose_plan_artifacts,
)
from mozaiksai.core.semantics.materialization import MaterializedBundle
from tests.slice_5b_composition_helpers import composition_fixture


def _compose(**updates):
    fixture = composition_fixture()
    arguments = {
        "plan": fixture["successor"],
        "resolver": fixture["resolver"],
        "assignments": fixture["assignments"],
        "assignment_results": (fixture["result"],),
        "materialized_bundle": fixture["materialized"],
        "plan_authority_proof": fixture["plan_authority_proof"],
        "base_revision_digest": fixture["base_revision_digest"],
        "base_plan": fixture["base"],
        "base_outputs": fixture["base_outputs"],
        "regeneration_closure": fixture["closure"],
    }
    arguments.update(updates)
    return compose_plan_artifacts(**arguments)


def test_every_unit_path_and_finite_outcome_is_accounted_once() -> None:
    composed = _compose()
    fixture = composition_fixture()
    ledger = composed.ledger
    assert {entry.plan_unit_ref.unit_id for entry in ledger.unit_entries} == {
        unit.unit_id for unit in fixture["successor"].units
    }
    assert {entry.outcome for entry in ledger.unit_entries} == {
        CompositionOutcome.AGENT_AUTHORED,
        CompositionOutcome.RENDERED,
        CompositionOutcome.PRESERVED,
        CompositionOutcome.REUSED,
        CompositionOutcome.INPUT_ONLY,
        CompositionOutcome.EXTERNAL_HANDOFF,
        CompositionOutcome.INAPPLICABLE,
    }
    assert ledger.removed_base_artifacts
    assert all(item.outcome is CompositionOutcome.REMOVED for item in ledger.removed_base_artifacts)
    assert len(composed.artifacts) == len(ledger.final_bundle_manifest)
    assert not any("content" in item for item in ledger.model_dump(mode="json").keys())
    assert dataclasses.is_dataclass(composed)
    assert CompositionLedger.model_validate(ledger.model_dump(mode="json")) == ledger


def test_missing_extra_duplicate_and_stale_inputs_fail_closed() -> None:
    fixture = composition_fixture()
    with pytest.raises(ValueError, match="lacks an artifact result"):
        _compose(assignment_results=())

    duplicate_outputs = (*fixture["materialized"].outputs, fixture["materialized"].outputs[0])
    duplicate_bundle = dataclasses.replace(
        fixture["materialized"], outputs=duplicate_outputs
    )
    with pytest.raises(ValueError, match="duplicate materialized"):
        _compose(materialized_bundle=duplicate_bundle)

    with pytest.raises(ValueError, match="stale base revision"):
        _compose(base_revision_digest="c" * 64)

    extra_bundle = MaterializedBundle(
        plan_digest=fixture["successor"].plan_digest,
        outputs=(*fixture["materialized"].outputs, fixture["base_outputs"][1]),
        external_handoff_units=(),
        inapplicable_units=(),
        unsupplied_preserved_units=(),
        instance_scope_deferred_units=(),
        gap_count=0,
    )
    with pytest.raises(ValueError, match="extra materialized"):
        _compose(materialized_bundle=extra_bundle)


def test_forged_result_unit_and_ledger_manifest_fail() -> None:
    fixture = composition_fixture()
    forged_result = fixture["result"].model_dump(mode="json")
    forged_result["plan_unit_ref"]["unit_digest"] = "0" * 64
    forged_result["result_digest"] = "0" * 64
    with pytest.raises(ValidationError):
        type(fixture["result"]).model_validate(forged_result)

    ledger = _compose().ledger
    forged_ledger = ledger.model_dump(mode="json")
    forged_ledger["final_bundle_manifest"] = forged_ledger["final_bundle_manifest"][:-1]
    forged_ledger["bundle_digest"] = "0" * 64
    forged_ledger["ledger_digest"] = "0" * 64
    with pytest.raises(ValidationError, match="manifest|bundle_digest"):
        CompositionLedger.model_validate(forged_ledger)


def test_blocking_gap_and_content_on_noncontent_units_reject() -> None:
    fixture = composition_fixture()
    plan = fixture["successor"]
    gap_source = next(
        unit for unit in plan.units if unit.disposition is PlanDisposition.INAPPLICABLE
    )
    # A non-empty gap tuple cannot be invented without changing canonical plan identity;
    # direct mutation is still caught by cold validation before composition.
    broken = plan.model_copy(update={"gaps": (gap_source,)})
    with pytest.raises((ValueError, ValidationError)):
        _compose(plan=broken)

    handoff = next(
        unit for unit in plan.units if unit.disposition is PlanDisposition.EXTERNAL_HANDOFF
    )
    leaked = dataclasses.replace(
        fixture["base_outputs"][0],
        unit_id=handoff.unit_id,
        path_scope=handoff.outputs[0].path_scope,
        path=handoff.outputs[0].path,
    )
    bundle = dataclasses.replace(
        fixture["materialized"], outputs=(*fixture["materialized"].outputs, leaked)
    )
    with pytest.raises(ValueError, match="extra materialized"):
        _compose(materialized_bundle=bundle)
