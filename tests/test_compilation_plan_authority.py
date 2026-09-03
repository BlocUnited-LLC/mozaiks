"""Canonical-authority validation proofs for CompilationPlans.

A plan self-digest proves body integrity, not truthful derivation: a caller
can mutate any derived plan fact, recompute the self-digest, and obtain a
structurally cold-valid plan. These proofs pin the closing rule — before a
plan authorizes materialization, rematerialization, or historical reuse, it
must equal its exact canonical rederivation from immutable authorities — and
attack every execution-authorizing field class.

Codex 2's original generic reproduction is preserved first: an unrelated but
valid same-graph payload source added to one unit, digests recomputed, cold
model validation accepting the forgery.
"""

from __future__ import annotations

import copy
import time
from collections.abc import Callable

import pytest

from mozaiksai.core.semantics.canonical import canonical_digest
from mozaiksai.core.semantics.compilation_plan import (
    CompilationPlan,
    derive_compilation_plan,
)
from mozaiksai.core.semantics.materialization import (
    MaterializationError,
    materialize_plan,
    rematerialize_plan,
)
from mozaiksai.core.semantics.plan_authority import (
    PlanAuthorityError,
    PlanAuthorityMismatch,
    PlanAuthorityProof,
    validate_compilation_plan_against_authority,
)
from tests.test_deterministic_page_materialization import (
    _binding,
    _build,
    _registry,
    _source,
)


def _authority():
    result, plan = _build(_source())
    return result.graph, result.payloads, plan


def _forge(plan: CompilationPlan, mutate: Callable[[dict], None]) -> CompilationPlan:
    """Apply one mutation and recompute the outer self-digest.

    The result passes cold model validation whenever the mutation is
    structurally expressible — exactly the forgery class the authority
    verifier must reject.
    """
    document = plan.model_dump(mode="json")
    payload = plan.canonical_payload(include_digest=False)
    mutate(document)
    mutate(payload)
    document["plan_digest"] = canonical_digest(payload)
    return CompilationPlan.model_validate(document)


def _render_unit_index(document: dict) -> int:
    return next(
        index
        for index, unit in enumerate(document["units"])
        if unit["disposition"] == "render" and unit["sources"]
    )


def _extra_source(graph, payloads, plan):
    unit = next(u for u in plan.units if u.disposition.value == "render" and u.sources)
    present = {s.node_id for s in unit.sources}
    extra = next(p for p in payloads if p.node_id not in present)

    def mutate(document: dict) -> None:
        index = _render_unit_index(document)
        document["units"][index]["sources"] = sorted(
            list(document["units"][index]["sources"])
            + [{"node_id": extra.node_id, "payload_digest": extra.payload_digest}],
            key=lambda s: s["node_id"],
        )

    return mutate


def test_codex2_generic_extra_source_forgery_is_preserved_and_rejected() -> None:
    """The exact reproduction: cold model validation accepts the forgery;
    canonical-authority validation rejects it before any consumer trusts the
    forged footprint."""
    graph, payloads, plan = _authority()
    forged = _forge(plan, _extra_source(graph, payloads, plan))
    # cold model path accepts the recomputed self-digest — body integrity only
    assert (
        CompilationPlan.model_validate(forged.model_dump(mode="json")).plan_digest
        == forged.plan_digest
    )
    with pytest.raises(PlanAuthorityError) as excinfo:
        validate_compilation_plan_against_authority(
            forged, graph=graph, payloads=payloads, registry=_registry()
        )
    assert (
        excinfo.value.category is PlanAuthorityMismatch.CANONICAL_DERIVATION_MISMATCH
    )
    assert excinfo.value.unit_id is not None


def _mutations(graph, payloads, plan):
    unit = next(u for u in plan.units if u.disposition.value == "render" and u.sources)
    other_kind = next(
        p for p in payloads if p.node_id not in {s.node_id for s in unit.sources}
    )

    def drop_source(document: dict) -> None:
        index = _render_unit_index(document)
        document["units"][index]["sources"] = list(
            document["units"][index]["sources"]
        )[:-1]

    def substitute_source(document: dict) -> None:
        index = _render_unit_index(document)
        sources = list(document["units"][index]["sources"])
        sources[0] = {
            "node_id": other_kind.node_id,
            "payload_digest": other_kind.payload_digest,
        }
        document["units"][index]["sources"] = sorted(
            sources, key=lambda s: s["node_id"]
        )

    def stale_digest(document: dict) -> None:
        index = _render_unit_index(document)
        sources = [dict(s) for s in document["units"][index]["sources"]]
        sources[0]["payload_digest"] = "f" * 64
        document["units"][index]["sources"] = sources

    def extra_edge(document: dict) -> None:
        index = _render_unit_index(document)
        edges = [dict(e) for e in document["units"][index]["edge_sources"]]
        forged_edge = dict(edges[0])
        forged_edge["target_node_id"] = other_kind.node_id
        forged_edge["edge_identity"] = "a" * 64
        document["units"][index]["edge_sources"] = edges + [forged_edge]

    def missing_edge(document: dict) -> None:
        index = _render_unit_index(document)
        document["units"][index]["edge_sources"] = list(
            document["units"][index]["edge_sources"]
        )[:-1]

    def changed_output_path(document: dict) -> None:
        index = _render_unit_index(document)
        outputs = [dict(o) for o in document["units"][index]["outputs"]]
        outputs[0]["path"] = "ui/pages/hijacked.yaml"
        document["units"][index]["outputs"] = outputs

    def changed_path_scope(document: dict) -> None:
        index = _render_unit_index(document)
        outputs = [dict(o) for o in document["units"][index]["outputs"]]
        outputs[0]["path_scope"] = "workspace_root"
        document["units"][index]["outputs"] = outputs

    def changed_disposition(document: dict) -> None:
        index = _render_unit_index(document)
        document["units"][index]["disposition"] = "external_handoff"

    def disposition_inapplicable(document: dict) -> None:
        index = _render_unit_index(document)
        document["units"][index]["disposition"] = "inapplicable"

    def changed_materializer(document: dict) -> None:
        index = _render_unit_index(document)
        document["units"][index]["materializer"] = "app_generator"

    def changed_validator(document: dict) -> None:
        index = _render_unit_index(document)
        document["units"][index]["validator"] = "app_paths"

    def changed_assignment(document: dict) -> None:
        index = _render_unit_index(document)
        document["units"][index]["assignment_kind"] = "page_bundle"

    def changed_dependency(document: dict) -> None:
        index = _render_unit_index(document)
        other = next(
            u["unit_id"]
            for i, u in enumerate(document["units"])
            if i != index
        )
        document["units"][index]["depends_on_units"] = [other]

    def removed_unit(document: dict) -> None:
        index = _render_unit_index(document)
        document["units"] = [
            u for i, u in enumerate(document["units"]) if i != index
        ]

    def added_unit(document: dict) -> None:
        index = _render_unit_index(document)
        clone = copy.deepcopy(document["units"][index])
        clone["unit_id"] = clone["unit_id"] + "_forged"
        document["units"] = list(document["units"]) + [clone]

    def removed_gap(document: dict) -> None:
        document["gaps"] = list(document["gaps"])[:-1]

    def fake_gap(document: dict) -> None:
        fake = copy.deepcopy(document["gaps"][0])
        fake["family_kind"] = "app_dashboard"
        fake["path_template"] = "dashboard/dashboard.yaml"
        document["gaps"] = list(document["gaps"]) + [fake]

    return {
        "missing-source": drop_source,
        "substituted-source": substitute_source,
        "stale-source-digest": stale_digest,
        "extra-edge": extra_edge,
        "missing-edge": missing_edge,
        "changed-output-path": changed_output_path,
        "changed-path-scope": changed_path_scope,
        "disposition-external-handoff": changed_disposition,
        "disposition-inapplicable": disposition_inapplicable,
        "changed-materializer": changed_materializer,
        "changed-validator": changed_validator,
        "changed-assignment-kind": changed_assignment,
        "changed-dependency": changed_dependency,
        "removed-unit": removed_unit,
        "added-unit": added_unit,
        "removed-emitted-gap": removed_gap,
        "fake-emitted-gap": fake_gap,
    }


_MUTATION_IDS = [
    "missing-source",
    "substituted-source",
    "stale-source-digest",
    "extra-edge",
    "missing-edge",
    "changed-output-path",
    "changed-path-scope",
    "disposition-external-handoff",
    "disposition-inapplicable",
    "changed-materializer",
    "changed-validator",
    "changed-assignment-kind",
    "changed-dependency",
    "removed-unit",
    "added-unit",
    "removed-emitted-gap",
    "fake-emitted-gap",
]


@pytest.mark.parametrize("mutation_id", _MUTATION_IDS)
def test_every_forged_plan_fact_is_rejected(mutation_id) -> None:
    """Every execution-authorizing fact class: forge, recompute self-digests,
    and prove the authority verifier rejects the candidate — or the body
    contract itself refuses to express the forgery (also fail-closed)."""
    graph, payloads, plan = _authority()
    mutate = _mutations(graph, payloads, plan)[mutation_id]
    try:
        forged = _forge(plan, mutate)
    except (ValueError, TypeError):
        return  # structurally inexpressible: the body contract already refuses
    assert forged.plan_digest != plan.plan_digest
    with pytest.raises(PlanAuthorityError):
        validate_compilation_plan_against_authority(
            forged, graph=graph, payloads=payloads, registry=_registry()
        )


def test_changed_structured_output_ref_is_rejected() -> None:
    from pathlib import Path as _Path

    import yaml as _yaml

    root = _Path(__file__).resolve().parents[1]
    configs = {
        "AppGenerator": _yaml.safe_load(
            (
                root / "factory_app/workflows/AppGenerator/structured_outputs.yaml"
            ).read_text(encoding="utf-8")
        )
    }
    from mozaiksai.core.semantics.offline_projection import (  # noqa: F401
        project_semantic_graph,
    )
    from tests.test_deterministic_page_materialization import (
        _build as _build_4c,
    )
    from tests.test_deterministic_page_materialization import (
        _source as _source_4c,
    )

    result, _ = _build_4c(_source_4c())
    plan = derive_compilation_plan(
        graph=result.graph,
        payloads=result.payloads,
        registry=_registry(),
        structured_output_configs=configs,
    )
    if not any(u.required_structured_output_ref is not None for u in plan.units):
        pytest.skip("corpus produced no structured-output refs")

    def swap_ref(document: dict) -> None:
        refs = [
            unit["required_structured_output_ref"]
            for unit in document["units"]
            if unit.get("required_structured_output_ref") is not None
        ]
        distinct = [r for r in refs if r != refs[0]]
        if not distinct:
            pytest.skip("corpus produced a single structured-output ref")
        for unit in document["units"]:
            if unit.get("required_structured_output_ref") == refs[0]:
                unit["required_structured_output_ref"] = distinct[0]
                return

    # Either the body contract refuses the substitution outright, or the
    # authority verifier rejects the cold-valid forgery — both fail closed.
    try:
        forged = _forge(plan, swap_ref)
    except (ValueError, TypeError):
        forged = None
    if forged is not None:
        with pytest.raises(PlanAuthorityError):
            validate_compilation_plan_against_authority(
                forged,
                graph=result.graph,
                payloads=result.payloads,
                registry=_registry(),
                structured_output_configs=configs,
            )
    # the same authorities WITH configs accept the honest plan
    proof = validate_compilation_plan_against_authority(
        plan,
        graph=result.graph,
        payloads=result.payloads,
        registry=_registry(),
        structured_output_configs=configs,
    )
    assert proof.covers(plan)


def test_materialize_plan_rejects_forged_plan_before_bytes() -> None:
    graph, payloads, plan = _authority()
    forged = _forge(plan, _extra_source(graph, payloads, plan))
    with pytest.raises(PlanAuthorityError):
        materialize_plan(
            plan=forged,
            graph=graph,
            payloads=payloads,
            binding=_binding(graph),
            layout_registry=_registry(),
        )


def test_rematerialize_plan_rejects_forged_successor_before_reuse() -> None:
    graph, payloads, plan = _authority()
    base_bundle = materialize_plan(
        plan=plan,
        graph=graph,
        payloads=payloads,
        binding=_binding(graph),
        layout_registry=_registry(),
    )
    forged = _forge(plan, _extra_source(graph, payloads, plan))
    with pytest.raises(MaterializationError):
        rematerialize_plan(
            base_bundle=base_bundle,
            base_plan=forged,  # wrong base digest: bundle tie rejects first
            successor_plan=plan,
            graph=graph,
            payloads=payloads,
            binding=_binding(graph),
            layout_registry=_registry(),
        )
    with pytest.raises(PlanAuthorityError):
        rematerialize_plan(
            base_bundle=base_bundle,
            base_plan=plan,
            successor_plan=forged,
            graph=graph,
            payloads=payloads,
            binding=_binding(graph),
            layout_registry=_registry(),
        )


def test_foreign_graph_plan_and_shuffled_authorities() -> None:
    graph, payloads, plan = _authority()
    other_result, other_plan = _build(_source(column_label="Order Number"))
    # foreign plan against these authorities: canonical mismatch
    with pytest.raises(PlanAuthorityError):
        validate_compilation_plan_against_authority(
            other_plan, graph=graph, payloads=payloads, registry=_registry()
        )
    # shuffled raw authority input derives the same canonical plan
    proof = validate_compilation_plan_against_authority(
        plan,
        graph=graph,
        payloads=tuple(reversed(tuple(payloads))),
        registry=_registry(),
    )
    assert proof.covers(plan)


def test_exact_canonical_plan_is_accepted_with_proof() -> None:
    graph, payloads, plan = _authority()
    proof = validate_compilation_plan_against_authority(
        plan, graph=graph, payloads=payloads, registry=_registry()
    )
    assert proof.covers(plan)
    assert proof.plan_digest == plan.plan_digest
    assert not proof.covers(
        _forge(plan, _extra_source(graph, payloads, plan))
    )


def test_missing_authorities_fail_typed() -> None:
    graph, payloads, plan = _authority()
    with pytest.raises(PlanAuthorityError) as excinfo:
        validate_compilation_plan_against_authority(
            plan, graph=None, payloads=payloads, registry=_registry()
        )
    assert excinfo.value.category is PlanAuthorityMismatch.REQUIRED_AUTHORITY_MISSING


def test_composition_rejects_a_non_covering_proof() -> None:
    from mozaiksai.core.semantics.composition_ledger import compose_plan_artifacts

    graph, payloads, plan = _authority()
    wrong = PlanAuthorityProof(
        plan_digest="a" * 64,
        graph_digest=plan.graph_digest,
        registry_digest=plan.registry_digest,
    )
    with pytest.raises(PlanAuthorityError):
        compose_plan_artifacts(
            plan=plan,
            resolver=None,  # rejected before any resolver use
            assignments=None,
            assignment_results=(),
            materialized_bundle=None,
            base_revision_digest=None,
            plan_authority_proof=wrong,
        )


def test_base_plan_foreign_scope_and_graph_are_rejected() -> None:
    graph, payloads, plan = _authority()
    bundle = materialize_plan(
        plan=plan,
        graph=graph,
        payloads=payloads,
        binding=_binding(graph),
        layout_registry=_registry(),
    )
    other_result, other_plan = _build(_source(), graph_id="slice4c_other")
    forged_base = other_plan.model_copy(
        update={"plan_digest": plan.plan_digest}
    )
    with pytest.raises((MaterializationError, ValueError)):
        rematerialize_plan(
            base_bundle=bundle,
            base_plan=forged_base,
            successor_plan=plan,
            graph=graph,
            payloads=payloads,
            binding=_binding(graph),
            layout_registry=_registry(),
        )


def test_verification_is_deterministic_and_bounded() -> None:
    """Determinism and a coarse performance record for the representative
    corpus: repeated verification yields identical proofs; duration is
    recorded, not asserted against a brittle budget."""
    graph, payloads, plan = _authority()
    start = time.perf_counter()
    first = validate_compilation_plan_against_authority(
        plan, graph=graph, payloads=payloads, registry=_registry()
    )
    second = validate_compilation_plan_against_authority(
        plan, graph=graph, payloads=payloads, registry=_registry()
    )
    elapsed = time.perf_counter() - start
    assert first == second
    assert elapsed < 60  # offline compiler boundary; generous ceiling only
