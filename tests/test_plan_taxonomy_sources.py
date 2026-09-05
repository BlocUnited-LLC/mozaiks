"""PlanTaxonomySource: node-level taxonomy identity in plan and reuse authority.

The generic prerequisite extracted from the #479 architecture reset: a
node-level taxonomy identity that can affect rendered bytes must participate
consistently in (1) unit identity, (2) serialization, (3) canonical authority
rederivation, and (4) the regeneration/reuse signature — while every
pre-existing payload-only unit stays byte- and digest-identical. No family
derives taxonomy sources yet; these proofs are deliberately generic and use
the pinned corpus plus synthetic (self-consistent, re-digested) plan
documents to exercise the exact reuse-classification path that failed:
``_reuse_signature`` inside :func:`plan_regeneration_closure`, the same
classification :func:`rematerialize_plan` consumes to decide which prior
bytes may be copied.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from mozaiksai.core.semantics.canonical import canonical_digest
from mozaiksai.core.semantics.compilation_plan import (
    CompilationPlan,
    FamilyInstancePlan,
    PlanTaxonomySource,
    derive_compilation_plan,
    plan_regeneration_closure,
)
from mozaiksai.core.semantics.plan_authority import (
    PlanAuthorityError,
    build_compilation_plan_authority_inputs,
    validate_compilation_plan_against_authority,
)
from tests.test_compilation_plan import _corpus_graph, _registry

ROOT = Path(__file__).resolve().parents[1]

_TAXONOMY = {
    "node_id": "mozaiks.event.order_created",
    "category": "event",
    "identifier": "domain.order.created",
}


def _corpus_plan() -> CompilationPlan:
    graph, payloads = _corpus_graph()
    return derive_compilation_plan(graph=graph, payloads=payloads, registry=_registry())


def _redigested(document: dict) -> CompilationPlan:
    """Recompute the plan self-digest over a mutated document — the
    attacker's move: internally consistent, but not canonically derived."""
    body = {key: value for key, value in document.items() if key != "plan_digest"}
    document["plan_digest"] = canonical_digest(body)
    return CompilationPlan.model_validate(document)


def _with_taxonomy(
    plan: CompilationPlan, unit_id: str, taxonomy: list[dict]
) -> CompilationPlan:
    document = plan.model_dump(mode="json")
    canonical = sorted(
        taxonomy, key=lambda item: (item["node_id"], item["category"], item["identifier"])
    )
    for unit in document["units"]:
        if unit["unit_id"] == unit_id:
            if canonical:
                unit["taxonomy_sources"] = canonical
            else:
                unit.pop("taxonomy_sources", None)
    return _redigested(document)


def _pinned_unit_id(plan: CompilationPlan) -> str:
    return next(
        unit.unit_id
        for unit in plan.units
        if unit.sources and not unit.depends_on_units
    )


# ---------------------------------------------------------------------------
# Model contract
# ---------------------------------------------------------------------------


def test_taxonomy_source_contract_is_closed_and_grammar_validated() -> None:
    source = PlanTaxonomySource(**_TAXONOMY)
    assert set(PlanTaxonomySource.model_fields) == {"node_id", "category", "identifier"}
    assert PlanTaxonomySource.model_validate(source.model_dump(mode="json")) == source

    with pytest.raises(ValidationError):
        PlanTaxonomySource(**{**_TAXONOMY, "category": "provider"})
    with pytest.raises(ValidationError):
        PlanTaxonomySource(**{**_TAXONOMY, "identifier": "NotAnEventId"})
    with pytest.raises(ValidationError):
        PlanTaxonomySource(**{**_TAXONOMY, "node_id": ""})
    with pytest.raises(ValidationError):
        PlanTaxonomySource.model_validate({**_TAXONOMY, "created_at": "now"})


def test_unit_normalizes_orders_and_rejects_duplicate_taxonomy_axes() -> None:
    plan = _corpus_plan()
    base_unit = plan.unit(_pinned_unit_id(plan))
    shuffled = (
        PlanTaxonomySource(
            node_id="mozaiks.event.zeta", category="event", identifier="domain.z.two"
        ),
        PlanTaxonomySource(**_TAXONOMY),
    )
    unit = FamilyInstancePlan.model_validate(
        {**base_unit.model_dump(mode="json"), "taxonomy_sources": [
            item.model_dump(mode="json") for item in shuffled
        ]}
    )
    assert [item.node_id for item in unit.taxonomy_sources] == [
        "mozaiks.event.order_created",
        "mozaiks.event.zeta",
    ]
    with pytest.raises(ValidationError, match="duplicate taxonomy source"):
        FamilyInstancePlan.model_validate(
            {
                **base_unit.model_dump(mode="json"),
                "taxonomy_sources": [
                    _TAXONOMY,
                    {**_TAXONOMY, "identifier": "domain.order.updated"},
                ],
            }
        )


# ---------------------------------------------------------------------------
# Serialization / identity: pre-existing-unit stability and non-empty participation
# ---------------------------------------------------------------------------


def test_empty_taxonomy_is_absent_from_serialization_identity_and_digest() -> None:
    """Old-unit stability: no pre-existing unit serializes, digests, or
    identifies any differently because the field exists."""

    plan = _corpus_plan()
    for unit in plan.units:
        assert unit.taxonomy_sources == ()
        assert "taxonomy_sources" not in unit.model_dump(mode="json")
        assert "taxonomy_sources" not in unit.identity_payload
    # The canonical corpus plan digest is byte-identical to the pinned base
    # golden — the field's existence re-pins nothing.
    from tests.test_compilation_plan import _GOLDEN_PLAN_DIGEST

    assert plan.plan_digest == _GOLDEN_PLAN_DIGEST


def test_nonempty_taxonomy_enters_serialization_identity_and_digest() -> None:
    plan = _corpus_plan()
    unit_id = _pinned_unit_id(plan)
    base_unit = plan.unit(unit_id)
    carrying = FamilyInstancePlan.model_validate(
        {**base_unit.model_dump(mode="json"), "taxonomy_sources": [_TAXONOMY]}
    )
    assert carrying.model_dump(mode="json")["taxonomy_sources"] == [_TAXONOMY]
    assert carrying.identity_payload["taxonomy_sources"] == [_TAXONOMY]
    assert carrying.unit_digest != base_unit.unit_digest
    # Round trip preserves the exact identity.
    assert (
        FamilyInstancePlan.model_validate(carrying.model_dump(mode="json"))
        == carrying
    )


# ---------------------------------------------------------------------------
# Reuse signature: the defect that caused the architecture reset
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "mutation",
    [
        "identifier",
        "category",
        "added",
        "removed",
        "node_substituted",
    ],
)
def test_taxonomy_mutations_are_never_classified_reusable(mutation: str) -> None:
    """The exact reuse-classification path that failed in #479: two plans
    whose only difference is one unit's node-level taxonomy identity must
    classify that unit AFFECTED — reuse of its prior bytes is forbidden."""

    plan = _corpus_plan()
    unit_id = _pinned_unit_id(plan)
    base_taxonomy = [_TAXONOMY]
    successor_taxonomy: list[dict]
    if mutation == "identifier":
        successor_taxonomy = [{**_TAXONOMY, "identifier": "domain.order.updated"}]
    elif mutation == "category":
        successor_taxonomy = [
            {**_TAXONOMY, "category": "capability", "identifier": "orders.analysis"}
        ]
    elif mutation == "added":
        successor_taxonomy = [
            _TAXONOMY,
            {
                "node_id": "mozaiks.event.order_paid",
                "category": "event",
                "identifier": "domain.order.paid",
            },
        ]
    elif mutation == "removed":
        successor_taxonomy = []
    else:
        successor_taxonomy = [
            {**_TAXONOMY, "node_id": "mozaiks.event.order_created_v2"}
        ]

    base = _with_taxonomy(plan, unit_id, base_taxonomy)
    successor = _with_taxonomy(plan, unit_id, successor_taxonomy)
    closure = plan_regeneration_closure(base, successor)
    assert unit_id in closure.affected, mutation
    assert unit_id not in closure.reusable, mutation


def test_identical_and_order_permuted_taxonomy_stays_reusable() -> None:
    plan = _corpus_plan()
    unit_id = _pinned_unit_id(plan)
    second = {
        "node_id": "mozaiks.event.order_paid",
        "category": "event",
        "identifier": "domain.order.paid",
    }
    base = _with_taxonomy(plan, unit_id, [_TAXONOMY, second])
    # The same canonical identities supplied in the opposite order normalize
    # to one canonical tuple: an order-only permutation never regenerates.
    successor = _with_taxonomy(plan, unit_id, [second, _TAXONOMY])
    closure = plan_regeneration_closure(base, successor)
    assert unit_id in closure.reusable
    assert unit_id not in closure.affected
    assert (
        base.unit(unit_id).taxonomy_sources == successor.unit(unit_id).taxonomy_sources
    )


# ---------------------------------------------------------------------------
# Canonical authority: possession of a self-consistent plan proves nothing
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "forged_taxonomy",
    [
        [_TAXONOMY],
        [{**_TAXONOMY, "identifier": "domain.order.updated"}],
        [
            _TAXONOMY,
            {
                "node_id": "mozaiks.event.order_paid",
                "category": "event",
                "identifier": "domain.order.paid",
            },
        ],
    ],
)
def test_forged_taxonomy_sources_fail_canonical_rederivation(
    forged_taxonomy: list[dict],
) -> None:
    """Canonical derivation of current families produces EMPTY taxonomy
    sources, so any caller-forged taxonomy pin — even fully re-digested —
    is not the canonical derivation of its authorities. When a family that
    derives non-empty taxonomy sources exists, removing or changing its
    pinned identity fails the same exact-equality rule in that family's own
    fixtures."""

    graph, payloads = _corpus_graph()
    plan = derive_compilation_plan(graph=graph, payloads=payloads, registry=_registry())
    authority = build_compilation_plan_authority_inputs(
        graph=graph, payloads=payloads, registry=_registry()
    )
    validate_compilation_plan_against_authority(plan, authority)
    forged = _with_taxonomy(plan, _pinned_unit_id(plan), forged_taxonomy)
    with pytest.raises(PlanAuthorityError):
        validate_compilation_plan_against_authority(forged, authority)
