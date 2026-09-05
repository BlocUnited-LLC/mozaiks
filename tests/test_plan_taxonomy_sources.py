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

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from mozaiksai.core.semantics.canonical import canonical_digest
from mozaiksai.core.semantics.compilation_plan import (
    CompilationPlan,
    FamilyInstancePlan,
    PlanTaxonomySource,
    _reuse_signature,
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


@pytest.mark.parametrize("category,identifier", [
    ("event", "domain.order.created"),
    ("capability", "orders.analysis"),
    ("artifact_family", "app_manifest"),
])
@pytest.mark.parametrize("padding", [(" ", ""), ("", " "), ("\t ", " \n")])
def test_taxonomy_source_stores_canonical_identity(
    category: str, identifier: str, padding: tuple[str, str],
) -> None:
    canonical = PlanTaxonomySource(**{**_TAXONOMY, "category": category, "identifier": identifier})
    prefix, suffix = padding
    supplied = {
        "node_id": f"{prefix}{canonical.node_id}{suffix}",
        "category": f"{prefix}{category}{suffix}",
        "identifier": f"{prefix}{identifier}{suffix}",
    }
    source = PlanTaxonomySource.model_validate(supplied)
    assert source.identifier == identifier
    assert (source.node_id, source.category, source.identifier) == (
        canonical.node_id, canonical.category, canonical.identifier,
    )
    assert source == canonical
    assert source.model_dump() == canonical.model_dump()
    assert source.model_dump(mode="json") == canonical.model_dump(mode="json")
    assert source.model_dump_json() == canonical.model_dump_json()
    assert PlanTaxonomySource.model_validate_json(json.dumps(supplied)) == canonical
    assert PlanTaxonomySource.model_validate_json(source.model_dump_json()) == canonical
    assert supplied["identifier"] == f"{prefix}{identifier}{suffix}"
    with pytest.raises(ValidationError, match="frozen"):
        source.identifier = "domain.order.updated"


@pytest.mark.parametrize("category,identifier", [
    ("event", " domain order.created "),
    ("event", " Domain.order.created "),
    ("capability", " orders/analysis "),
    ("artifact_family", " app.manifest "),
    ("event", " \t\n "),
])
def test_taxonomy_source_normalization_still_rejects_invalid_grammar(
    category: str, identifier: str,
) -> None:
    with pytest.raises(ValidationError, match="identifier must match"):
        PlanTaxonomySource(**{**_TAXONOMY, "category": category, "identifier": identifier})


def test_whitespace_and_order_are_identical_unit_and_reuse_authority() -> None:
    plan = _corpus_plan()
    unit_id = _pinned_unit_id(plan)
    base_unit = plan.unit(unit_id)
    second = {
        "node_id": "mozaiks.event.order_paid",
        "category": "event",
        "identifier": "domain.order.paid",
    }
    canonical = FamilyInstancePlan.model_validate({
        **base_unit.model_dump(mode="json"), "taxonomy_sources": [_TAXONOMY, second],
    })
    padded = FamilyInstancePlan.model_validate({
        **base_unit.model_dump(mode="json"),
        "taxonomy_sources": [
            {key: f" \t{value}\n " for key, value in source.items()}
            for source in [second, _TAXONOMY]
        ],
    })
    assert padded.taxonomy_sources == canonical.taxonomy_sources
    assert padded == canonical
    assert padded.model_dump() == canonical.model_dump()
    assert padded.model_dump(mode="json") == canonical.model_dump(mode="json")
    assert padded.identity_payload == canonical.identity_payload
    assert padded.model_dump_json() == canonical.model_dump_json()
    assert padded.unit_digest == canonical.unit_digest
    assert _reuse_signature(padded, plan) == _reuse_signature(canonical, plan)
    assert FamilyInstancePlan.model_validate_json(padded.model_dump_json()) == canonical
    base = _with_taxonomy(plan, unit_id, canonical.model_dump(mode="json")["taxonomy_sources"])
    successor = _with_taxonomy(plan, unit_id, padded.model_dump(mode="json")["taxonomy_sources"])
    assert base.model_dump_json() == successor.model_dump_json()
    assert unit_id in plan_regeneration_closure(base, successor).reusable


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


def test_every_preexisting_corpus_unit_keeps_exact_bytes_and_identity() -> None:
    # Independently captured from exact base 36b33e021fb04b68c82a8b144ff4fef608ce9b15.
    # Pin every unit's serialized JSON, dump, identity payload and unit digest,
    # rather than relying solely on the aggregate plan's serialization digest.
    rows = [
        {
            "unit_id": unit.unit_id,
            "model_dump": unit.model_dump(mode="json"),
            "identity_payload": unit.identity_payload,
            "serialized_json": unit.model_dump_json(),
            "unit_digest": unit.unit_digest,
        }
        for unit in _corpus_plan().units
        if unit.family_kind != "workflow_module_interface"
    ]
    assert len(rows) == 59
    assert canonical_digest(rows) == "23a30860c1bad68123df0c3e208fc150e9f0d8aadef8f64d15a993e7333c8e8a"


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
