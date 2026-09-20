"""5B composition-ledger proofs over canonically derived, authority-validated
plans.

Composition now requires the exact CompilationPlanAuthorityInputs and uses the
canonical rederived plan; the outcome coverage below is exactly what honest
derivation can produce today (rendered, agent-authored, and reused-through-
closure). PRESERVED / EXTERNAL_HANDOFF / INAPPLICABLE / INPUT_ONLY /
REMOVED outcome coverage moved with the brownfield prerequisite: fabricating
those dispositions requires plans canonical derivation cannot produce, which
is precisely what the authority rule rejects (typed, tested in
test_compilation_plan_authority and below).
"""

from __future__ import annotations

import dataclasses

import pytest
from pydantic import ValidationError

from mozaiksai.core.semantics.composition_ledger import (
    CompositionLedger,
    CompositionOutcome,
    compose_plan_artifacts,
)
from mozaiksai.core.semantics.plan_authority import (
    PlanAuthorityError,
    PlanAuthorityMismatch,
)
from tests.slice_5b_composition_helpers import composition_fixture


def _compose(**updates):
    fixture = composition_fixture()
    arguments = {
        "plan": fixture["successor"],
        "authority_inputs": fixture["authority_inputs"],
        "resolver": fixture["resolver"],
        "assignments": fixture["assignments"],
        "assignment_results": (fixture["result"],),
        "materialized_bundle": fixture["materialized"],
        "base_revision_digest": None,
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
    }
    assert not ledger.removed_base_artifacts
    assert len(composed.artifacts) == len(ledger.final_bundle_manifest)
    assert dataclasses.is_dataclass(composed)
    assert CompositionLedger.model_validate(ledger.model_dump(mode="json")) == ledger


def test_composition_requires_canonical_authority() -> None:
    fixture = composition_fixture()
    with pytest.raises(TypeError):
        compose_plan_artifacts(  # authority_inputs is not optional
            plan=fixture["successor"],
            resolver=fixture["resolver"],
            assignments=fixture["assignments"],
            assignment_results=(fixture["result"],),
            materialized_bundle=fixture["materialized"],
            base_revision_digest=None,
        )
    with pytest.raises(PlanAuthorityError) as excinfo:
        _compose(authority_inputs=None)
    assert excinfo.value.category is PlanAuthorityMismatch.REQUIRED_AUTHORITY_MISSING
    # authority for another plan (the base) does not cover the successor
    with pytest.raises(PlanAuthorityError):
        _compose(authority_inputs=fixture["base_authority_inputs"])


def test_refinement_composition_requires_base_authority() -> None:
    from tests.slice_5b_composition_helpers import refinement_execution

    fixture = composition_fixture()
    assignments, result = refinement_execution(fixture, "b" * 64)
    with pytest.raises(PlanAuthorityError) as excinfo:
        _compose(
            assignments=assignments,
            assignment_results=(result,),
            base_plan=fixture["base"],
            base_outputs=fixture["base_outputs"],
            regeneration_closure=fixture["closure"],
            base_revision_digest="b" * 64,
        )
    assert excinfo.value.category is PlanAuthorityMismatch.REQUIRED_AUTHORITY_MISSING


def test_refinement_composition_with_reuse_through_closure() -> None:
    """The v1->v2 closure over two canonically derived plans yields REUSED
    outcomes for the unaffected units — refinement composition works without
    any brownfield fabrication."""

    from tests.slice_5b_composition_helpers import empty_assignment_set

    fixture = composition_fixture()
    # the unchanged agent unit is REUSED from base bytes: no fresh
    # assignment or result may accompany it
    reusable = set(fixture["closure"].reusable)
    affected_bundle = dataclasses.replace(
        fixture["materialized"],
        outputs=tuple(
            output
            for output in fixture["materialized"].outputs
            if output.unit_id not in reusable
        ),
    )
    composed = _compose(
        assignments=empty_assignment_set(),
        assignment_results=(),
        materialized_bundle=affected_bundle,
        base_plan=fixture["base"],
        base_authority_inputs=fixture["base_authority_inputs"],
        base_outputs=fixture["base_outputs"],
        regeneration_closure=fixture["closure"],
        base_revision_digest="b" * 64,
    )
    outcomes = {entry.outcome for entry in composed.ledger.unit_entries}
    assert CompositionOutcome.REUSED in outcomes
    assert CompositionOutcome.RENDERED in outcomes


def test_missing_extra_duplicate_and_stale_inputs_fail_closed() -> None:
    fixture = composition_fixture()
    with pytest.raises(ValueError, match="lacks an artifact result"):
        _compose(assignment_results=())

    duplicate_outputs = (
        *fixture["materialized"].outputs,
        fixture["materialized"].outputs[0],
    )
    duplicate_bundle = dataclasses.replace(
        fixture["materialized"], outputs=duplicate_outputs
    )
    with pytest.raises(ValueError, match="duplicate materialized"):
        _compose(materialized_bundle=duplicate_bundle)

    with pytest.raises(ValueError, match="stale base revision"):
        _compose(base_revision_digest="c" * 64)


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


def test_forged_plan_and_gap_bearing_plan_reject_before_composition() -> None:
    """A modified internally consistent plan fails canonical rederivation
    before any assignment, byte, or path is inspected; a genuinely derived
    but gap-bearing plan (the full canonical registry) fails the zero-gap
    contract — never composed silently."""
    from mozaiksai.core.runtime.app.layout_registry import build_app_layout_registry
    from mozaiksai.core.semantics.canonical import canonical_digest
    from mozaiksai.core.semantics.compilation_plan import (
        CompilationPlan,
        derive_compilation_plan,
    )
    from mozaiksai.core.semantics.plan_authority import (
        build_compilation_plan_authority_inputs,
    )

    fixture = composition_fixture()
    plan = fixture["successor"]
    document = plan.model_dump(mode="json")
    payload = plan.canonical_payload(include_digest=False)
    for doc in (document, payload):
        target = next(u for u in doc["units"] if u["sources"])
        target["sources"] = list(target["sources"])[:-1]
    document["plan_digest"] = canonical_digest(payload)
    forged = CompilationPlan.model_validate(document)
    with pytest.raises(PlanAuthorityError):
        _compose(plan=forged)

    full_registry = build_app_layout_registry(())
    gappy_plan = derive_compilation_plan(
        graph=fixture["graph"],
        payloads=fixture["payloads"],
        registry=full_registry,
        structured_output_configs=fixture["configs"],
    )
    gappy_authority = build_compilation_plan_authority_inputs(
        graph=fixture["graph"],
        payloads=fixture["payloads"],
        registry=full_registry,
        structured_output_configs=fixture["configs"],
    )
    assert gappy_plan.gaps
    with pytest.raises(ValueError, match="gaps prevent"):
        _compose(plan=gappy_plan, authority_inputs=gappy_authority)


def test_brownfield_plan_rejects_typed_at_composition() -> None:
    """A plan carrying preserve_unowned content cannot compose: canonical
    validation rejects it with the typed base_authority_missing category —
    the immutable base-input authority is the identified prerequisite."""
    from mozaiksai.core.semantics.canonical import canonical_digest
    from mozaiksai.core.semantics.compilation_plan import CompilationPlan

    fixture = composition_fixture()
    plan = fixture["successor"]
    document = plan.model_dump(mode="json")
    payload = plan.canonical_payload(include_digest=False)
    for doc in (document, payload):
        target = next(u for u in doc["units"] if u["disposition"] == "render")
        target["disposition"] = "preserve_unowned"
        target["materializer"] = "preserved_opaque"
    document["plan_digest"] = canonical_digest(payload)
    try:
        brownfield = CompilationPlan.model_validate(document)
    except ValidationError:
        pytest.skip("body contract refuses the disposition rewrite outright")
    with pytest.raises(PlanAuthorityError) as excinfo:
        _compose(plan=brownfield)
    assert excinfo.value.category is PlanAuthorityMismatch.BASE_AUTHORITY_MISSING


def test_hostile_unit_resolution_cannot_alter_composition(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """After canonical validation, plan-unit facts come only from the
    canonical rederived plan: a resolver returning mutated units must not
    change one composed byte or ledger fact."""

    fixture = composition_fixture()
    arguments = {
        "plan": fixture["successor"],
        "authority_inputs": fixture["authority_inputs"],
        "resolver": fixture["resolver"],
        "assignments": fixture["assignments"],
        "assignment_results": (fixture["result"],),
        "materialized_bundle": fixture["materialized"],
        "base_revision_digest": None,
    }
    baseline = compose_plan_artifacts(**arguments)
    original = fixture["resolver"].resolve_plan_unit

    def hostile(ref, *, requesting_scope):
        honest = original(ref, requesting_scope=requesting_scope)
        return honest.model_copy(
            update={"outputs": (), "placeholder_values": (("helper_id", "evil"),)}
        )

    monkeypatch.setattr(fixture["resolver"], "resolve_plan_unit", hostile)
    composed = compose_plan_artifacts(**arguments)
    assert composed.ledger == baseline.ledger
    assert composed.artifacts == baseline.artifacts
