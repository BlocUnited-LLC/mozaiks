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


# ---------------------------------------------------------------------------
# Public composition boundary: plan authority is mandatory. Codex 3's exact
# reproduction is preserved — an internally consistent forged plan (rejected
# by the canonical validator) with the entire supporting chain publicly
# recomputed (closure, resolver, assignments, result, bundle) must not be
# composable, and no None/omitted/forged-proof path may exist.
# ---------------------------------------------------------------------------


def _forged_composition_scenario():
    import dataclasses
    from pathlib import Path as _Path

    import yaml as _yaml

    from mozaiksai.core.semantics.compilation_plan import (
        plan_regeneration_closure,
    )
    from mozaiksai.core.semantics.refs import (
        CompilationPlanRef,
        PlanUnitRef,
        SemanticPayloadRef,
    )
    from mozaiksai.core.semantics.resolver import SemanticReferenceResolver
    from mozaiksai.core.workflow.assignment_artifacts import (
        build_assignment_artifact_result,
    )
    from mozaiksai.core.workflow.plan_assignment_compiler import (
        ApprovedAssignmentSpec,
        ApprovedPlan,
        compile_approved_plan,
    )
    from tests.slice_5b_composition_helpers import composition_fixture

    root = _Path(__file__).resolve().parents[1]
    structured = _yaml.safe_load(
        (
            root / "factory_app/workflows/AppGenerator/structured_outputs.yaml"
        ).read_text(encoding="utf-8")
    )
    fixture = composition_fixture()
    successor = fixture["successor"]
    base = fixture["base"]
    base_ids = {u.unit_id for u in base.units}
    unit = next(
        u for u in successor.units if u.sources and u.unit_id not in base_ids
    )
    present = {s.node_id for s in unit.sources}
    extra = next(p for p in fixture["payloads"] if p.node_id not in present)
    document = successor.model_dump(mode="json")
    payload = successor.canonical_payload(include_digest=False)
    for doc in (document, payload):
        for u in doc["units"]:
            if u["unit_id"] == unit.unit_id:
                u["sources"] = sorted(
                    list(u["sources"])
                    + [
                        {
                            "node_id": extra.node_id,
                            "payload_digest": extra.payload_digest,
                        }
                    ],
                    key=lambda s: s["node_id"],
                )
    document["plan_digest"] = canonical_digest(payload)
    forged = CompilationPlan.model_validate(document)

    resolver = SemanticReferenceResolver()
    for item in fixture["payloads"]:
        resolver.register_semantic_payload(item)
    resolver.register_semantic_graph_v2(fixture["graph"])
    resolver.register_compilation_plan(base)
    resolver.register_compilation_plan(forged)

    agent = next(
        u for u in forged.units if u.disposition.value == "agent_author"
    )
    plan_ref = CompilationPlanRef(
        subject_id=forged.graph_id,
        subject_version=forged.graph_version,
        content_digest=forged.plan_digest,
        scope=forged.scope,
    )
    unit_ref = PlanUnitRef(
        compilation_plan_ref=plan_ref,
        unit_id=agent.unit_id,
        unit_digest=agent.unit_digest,
    )
    payload_by_id = {p.node_id: p for p in fixture["payloads"]}
    source_refs = tuple(
        SemanticPayloadRef(
            node_id=s.node_id,
            payload_kind=payload_by_id[s.node_id].payload_kind.value,
            payload_version=payload_by_id[s.node_id].payload_version,
            content_digest=s.payload_digest,
            scope=forged.scope,
        )
        for s in agent.sources
    )
    assignments = compile_approved_plan(
        ApprovedPlan(
            assignments=(
                ApprovedAssignmentSpec(
                    plan_unit_ref=unit_ref,
                    assignment_kind=agent.assignment_kind,
                    dependency_context_refs=source_refs,
                    required_structured_output_ref=(
                        agent.required_structured_output_ref
                    ),
                    required_validators=(agent.validator,),
                    assignment_retry_limit=2,
                    base_revision_digest=fixture["base_revision_digest"],
                ),
            )
        ),
        resolver=resolver,
        structured_output_configs={"AppGenerator": structured},
    )
    helper_source = "def report_hook():\n    return None\n"
    result = build_assignment_artifact_result(
        assignment=assignments.ordered_assignments[0],
        structured_output={
            "assignment_kind": "module_helper_implementation",
            "module_id": "reports",
            "helper_id": "report_hook",
            "helper_source": helper_source,
        },
        artifacts={"modules/reports/backend/report_hook.py": helper_source},
        structured_output_configs={"AppGenerator": structured},
        validator_runner=lambda _validator, files: bool(files),
    )
    bundle = dataclasses.replace(
        fixture["materialized"], plan_digest=forged.plan_digest
    )
    return {
        "fixture": fixture,
        "forged": forged,
        "resolver": resolver,
        "assignments": assignments,
        "result": result,
        "bundle": bundle,
        "closure": plan_regeneration_closure(base, forged),
    }


def _compose_forged(scenario, proof):
    from mozaiksai.core.semantics.composition_ledger import compose_plan_artifacts

    return compose_plan_artifacts(
        plan=scenario["forged"],
        resolver=scenario["resolver"],
        assignments=scenario["assignments"],
        assignment_results=(scenario["result"],),
        materialized_bundle=scenario["bundle"],
        plan_authority_proof=proof,
        base_revision_digest=scenario["fixture"]["base_revision_digest"],
        base_plan=scenario["fixture"]["base"],
        base_outputs=scenario["fixture"]["base_outputs"],
        regeneration_closure=scenario["closure"],
    )


def test_codex3_public_composition_forgery_is_rejected() -> None:
    """Preserved reproduction: the internally consistent forged plan whose
    supporting chain was fully recomputed through public APIs cannot compose —
    proof=None fails typed, and no validator will issue a covering proof."""
    scenario = _forged_composition_scenario()
    with pytest.raises(PlanAuthorityError) as excinfo:
        _compose_forged(scenario, None)
    assert (
        excinfo.value.category
        is PlanAuthorityMismatch.REQUIRED_AUTHORITY_MISSING
    )


def test_composition_requires_the_proof_argument_entirely() -> None:
    from mozaiksai.core.semantics.composition_ledger import compose_plan_artifacts

    scenario = _forged_composition_scenario()
    with pytest.raises(TypeError):
        compose_plan_artifacts(
            plan=scenario["forged"],
            resolver=scenario["resolver"],
            assignments=scenario["assignments"],
            assignment_results=(scenario["result"],),
            materialized_bundle=scenario["bundle"],
            base_revision_digest=scenario["fixture"]["base_revision_digest"],
            base_plan=scenario["fixture"]["base"],
            base_outputs=scenario["fixture"]["base_outputs"],
            regeneration_closure=scenario["closure"],
        )


def test_forged_or_foreign_proofs_cannot_authorize_composition() -> None:
    import dataclasses


    scenario = _forged_composition_scenario()
    forged = scenario["forged"]
    honest = scenario["fixture"]["plan_authority_proof"]
    # proof for another plan (the honest successor) does not cover the forgery
    with pytest.raises(PlanAuthorityError):
        _compose_forged(scenario, honest)
    # dataclasses.replace with substituted digests keeps the original token,
    # whose issuance-bound digest then mismatches — rejected as non-issued
    replaced = dataclasses.replace(
        honest,
        plan_digest=forged.plan_digest,
        graph_digest=forged.graph_digest,
        registry_digest=forged.registry_digest,
    )
    with pytest.raises(PlanAuthorityError) as excinfo:
        _compose_forged(scenario, replaced)
    assert (
        excinfo.value.category
        is PlanAuthorityMismatch.REQUIRED_AUTHORITY_MISSING
    )
    # direct public construction cannot mint a token
    with pytest.raises(TypeError):
        PlanAuthorityProof(
            plan_digest=forged.plan_digest,
            graph_digest=forged.graph_digest,
            registry_digest=forged.registry_digest,
            scope_key="tenant1|ws1|",
        )
    # a caller-created token-shaped object is a foreign object
    class _FakeToken:
        _plan_digest = forged.plan_digest

    fake = PlanAuthorityProof(
        plan_digest=forged.plan_digest,
        graph_digest=forged.graph_digest,
        registry_digest=forged.registry_digest,
        scope_key="tenant1|ws1|",
        issued_token=_FakeToken(),
    )
    with pytest.raises(PlanAuthorityError):
        _compose_forged(scenario, fake)
    # proofs are not serializable documents: no pydantic surface exists
    assert not hasattr(PlanAuthorityProof, "model_validate")
    # a wrong-scope proof for an identical body does not cover the plan
    graph, payloads, plan = _authority()
    issued = validate_compilation_plan_against_authority(
        plan, graph=graph, payloads=payloads, registry=_registry()
    )
    wrong_scope = dataclasses.replace(issued, scope_key="other|scope|")
    from mozaiksai.core.semantics.plan_authority import (
        require_plan_authority_proof,
    )

    with pytest.raises(PlanAuthorityError):
        require_plan_authority_proof(wrong_scope, plan)
    # the untouched validator-issued proof covers its exact plan
    require_plan_authority_proof(issued, plan)


def test_no_production_module_reaches_the_private_issuance_seam() -> None:
    from pathlib import Path as _Path

    root = _Path(__file__).resolve().parents[1]
    offenders = []
    for base_dir in ("mozaiksai", "factory_app"):
        for path in (root / base_dir).rglob("*.py"):
            if path.name == "plan_authority.py":
                continue
            if "_IssuanceToken" in path.read_text(encoding="utf-8"):
                offenders.append(str(path.relative_to(root)))
    assert offenders == []


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
