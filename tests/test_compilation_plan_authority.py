"""Canonical CompilationPlan authority-contract proofs (ADR 0007 lane).

A plan self-digest proves body integrity, not truthful derivation: a caller
can mutate any derived plan fact, recompute the self-digests, and obtain a
structurally cold-valid plan. These proofs pin the authority contract that
closes the gap — one immutable authority-input document and one canonical
validator that re-derives the plan through the single existing derivation
implementation and returns the canonical rederived plan. No proof object, no
token, no bearer capability: possession of nothing establishes trust.

PR A defines the contract and validator only; consumer wiring is a separate
enforcement change.
"""

from __future__ import annotations

import copy
import json
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path

import pytest
from pydantic import ValidationError

from mozaiksai.core.semantics.canonical import canonical_digest
from mozaiksai.core.semantics.compilation_plan import (
    CompilationPlan,
    derive_compilation_plan,
)
from mozaiksai.core.semantics.plan_authority import (
    CompilationPlanAuthorityInputs,
    PlanAuthorityError,
    PlanAuthorityMismatch,
    build_compilation_plan_authority_inputs,
    validate_compilation_plan_against_authority,
)
from tests.test_deterministic_page_materialization import (
    _build,
    _registry,
    _source,
)

ROOT = Path(__file__).resolve().parents[1]


def _authority():
    result, plan = _build(_source())
    inputs = build_compilation_plan_authority_inputs(
        graph=result.graph,
        payloads=result.payloads,
        registry=_registry(),
    )
    return inputs, plan


def _forge(plan: CompilationPlan, mutate: Callable[[dict], None]) -> CompilationPlan:
    """Apply one mutation and recompute the outer self-digest.

    The result passes cold model validation whenever the mutation is
    structurally expressible — exactly the forgery class the canonical
    validator must reject.
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


def _extra_source(inputs, plan):
    unit = next(u for u in plan.units if u.disposition.value == "render" and u.sources)
    present = {s.node_id for s in unit.sources}
    extra = next(p for p in inputs.payloads if p.node_id not in present)

    def mutate(document: dict) -> None:
        index = _render_unit_index(document)
        document["units"][index]["sources"] = sorted(
            list(document["units"][index]["sources"])
            + [{"node_id": extra.node_id, "payload_digest": extra.payload_digest}],
            key=lambda s: s["node_id"],
        )

    return mutate


# ---------------------------------------------------------------------------
# Contract shape: frozen, extra-forbid, closed, serializable-but-untrusted
# ---------------------------------------------------------------------------


def test_authority_inputs_are_frozen_closed_and_versioned() -> None:
    inputs, _plan_obj = _authority()
    with pytest.raises(ValidationError):
        inputs.graph = inputs.graph  # type: ignore[misc]
    document = inputs.model_dump(mode="json")
    document["arbitrary_metadata"] = {"campaign": "x"}
    with pytest.raises(ValidationError):
        CompilationPlanAuthorityInputs.model_validate(document)
    document = inputs.model_dump(mode="json")
    document["authority_schema_version"] = "mozaiks.other.v9"
    with pytest.raises(ValidationError, match="schema version"):
        CompilationPlanAuthorityInputs.model_validate(document)
    document = inputs.model_dump(mode="json")
    document["payloads"] = []
    with pytest.raises(ValidationError, match="complete payload closure"):
        CompilationPlanAuthorityInputs.model_validate(document)


def test_authority_inputs_serialization_is_not_trust() -> None:
    """The contract round-trips losslessly, and a deserialized copy still
    yields validation only through full rederivation — the same canonical
    digest, not a remembered claim."""
    inputs, plan = _authority()
    revived = CompilationPlanAuthorityInputs.model_validate(
        json.loads(json.dumps(inputs.model_dump(mode="json")))
    )
    canonical = validate_compilation_plan_against_authority(plan, revived)
    assert canonical.plan_digest == plan.plan_digest
    # subclass payloads survive the round-trip exactly
    assert [p.payload_kind for p in revived.payloads] == [
        p.payload_kind for p in inputs.payloads
    ]
    assert [p.payload_digest for p in revived.payloads] == [
        p.payload_digest for p in inputs.payloads
    ]


# ---------------------------------------------------------------------------
# The canonical validator: exact acceptance and the forged-candidate matrix
# ---------------------------------------------------------------------------


def test_exact_candidate_returns_the_canonical_rederived_plan() -> None:
    inputs, plan = _authority()
    canonical = validate_compilation_plan_against_authority(plan, inputs)
    assert canonical is not plan  # rederived, never the caller object
    assert canonical.plan_digest == plan.plan_digest
    assert canonical.canonical_payload() == plan.canonical_payload()


def test_generic_extra_source_forgery_is_rejected() -> None:
    """The preserved generic reproduction: an unrelated valid same-graph
    payload source added to one unit, digests recomputed, cold model
    validation accepting the forgery — the canonical validator rejects it."""
    inputs, plan = _authority()
    forged = _forge(plan, _extra_source(inputs, plan))
    assert (
        CompilationPlan.model_validate(forged.model_dump(mode="json")).plan_digest
        == forged.plan_digest
    )
    with pytest.raises(PlanAuthorityError) as excinfo:
        validate_compilation_plan_against_authority(forged, inputs)
    assert (
        excinfo.value.category is PlanAuthorityMismatch.CANONICAL_DERIVATION_MISMATCH
    )
    assert excinfo.value.unit_id is not None


def _mutations(inputs, plan):
    unit = next(u for u in plan.units if u.disposition.value == "render" and u.sources)
    other_kind = next(
        p for p in inputs.payloads if p.node_id not in {s.node_id for s in unit.sources}
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
            u["unit_id"] for i, u in enumerate(document["units"]) if i != index
        )
        document["units"][index]["depends_on_units"] = [other]

    def removed_unit(document: dict) -> None:
        index = _render_unit_index(document)
        document["units"] = [u for i, u in enumerate(document["units"]) if i != index]

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
    and prove the validator rejects the candidate — or the body contract
    itself refuses to express the forgery (also fail-closed)."""
    inputs, plan = _authority()
    mutate = _mutations(inputs, plan)[mutation_id]
    try:
        forged = _forge(plan, mutate)
    except (ValueError, TypeError):
        return  # structurally inexpressible: the body contract already refuses
    assert forged.plan_digest != plan.plan_digest
    with pytest.raises(PlanAuthorityError):
        validate_compilation_plan_against_authority(forged, inputs)


def test_changed_structured_output_ref_is_rejected_under_configs() -> None:
    import yaml as _yaml

    configs = {
        "AppGenerator": _yaml.safe_load(
            (
                ROOT / "factory_app/workflows/AppGenerator/structured_outputs.yaml"
            ).read_text(encoding="utf-8")
        )
    }
    result, _ = _build(_source())
    plan = derive_compilation_plan(
        graph=result.graph,
        payloads=result.payloads,
        registry=_registry(),
        structured_output_configs=configs,
    )
    inputs = build_compilation_plan_authority_inputs(
        graph=result.graph,
        payloads=result.payloads,
        registry=_registry(),
        structured_output_configs=configs,
    )
    # the honest config-derived plan validates against config-carrying inputs
    canonical = validate_compilation_plan_against_authority(plan, inputs)
    assert canonical.plan_digest == plan.plan_digest

    refs = [
        u.required_structured_output_ref
        for u in plan.units
        if u.required_structured_output_ref is not None
    ]
    if len({r.model_dump_json() for r in refs}) < 2:
        pytest.skip("corpus produced fewer than two distinct structured refs")

    def swap_ref(document: dict) -> None:
        docs = [
            u["required_structured_output_ref"]
            for u in document["units"]
            if u.get("required_structured_output_ref") is not None
        ]
        distinct = next(d for d in docs if d != docs[0])
        for u in document["units"]:
            if u.get("required_structured_output_ref") == docs[0]:
                u["required_structured_output_ref"] = distinct
                return

    try:
        forged = _forge(plan, swap_ref)
    except (ValueError, TypeError):
        return  # body contract refuses the substitution: fail-closed
    with pytest.raises(PlanAuthorityError):
        validate_compilation_plan_against_authority(forged, inputs)


# ---------------------------------------------------------------------------
# Authority mismatches: wrong graph, wrong registry, missing authority,
# brownfield (no base authority exists), shuffled equivalence, fresh process
# ---------------------------------------------------------------------------


def test_foreign_graph_authorities_reject_the_plan() -> None:
    inputs, plan = _authority()
    other_result, other_plan = _build(_source(column_label="Order Number"))
    other_inputs = build_compilation_plan_authority_inputs(
        graph=other_result.graph,
        payloads=other_result.payloads,
        registry=_registry(),
    )
    with pytest.raises(PlanAuthorityError):
        validate_compilation_plan_against_authority(plan, other_inputs)
    with pytest.raises(PlanAuthorityError):
        validate_compilation_plan_against_authority(other_plan, inputs)


def test_foreign_registry_authority_rejects_the_plan() -> None:
    from mozaiksai.core.runtime.app.layout_registry import build_app_layout_registry

    _inputs, plan = _authority()
    source = build_app_layout_registry(())

    class _MutatedRegistry:
        def __init__(self) -> None:
            self.schema_version = source.schema_version
            families = []
            for family in source.ordered_families():
                if family.path_template == "app.json":
                    family = family.model_copy(
                        update={"path_template": "app_renamed.json"}
                    )
                families.append(family)
            self._families = tuple(families)

        def ordered_families(self):
            return self._families

    result, _ = _build(_source())
    mutated_inputs = build_compilation_plan_authority_inputs(
        graph=result.graph,
        payloads=result.payloads,
        registry=_MutatedRegistry(),
    )
    with pytest.raises(PlanAuthorityError):
        validate_compilation_plan_against_authority(plan, mutated_inputs)


def test_missing_authority_fails_explicitly_typed() -> None:
    inputs, plan = _authority()
    with pytest.raises(PlanAuthorityError) as excinfo:
        validate_compilation_plan_against_authority(plan, None)
    assert excinfo.value.category is PlanAuthorityMismatch.REQUIRED_AUTHORITY_MISSING
    with pytest.raises(PlanAuthorityError) as excinfo:
        validate_compilation_plan_against_authority(None, inputs)
    assert excinfo.value.category is PlanAuthorityMismatch.REQUIRED_AUTHORITY_MISSING
    with pytest.raises(PlanAuthorityError) as excinfo:
        validate_compilation_plan_against_authority(plan, {"graph": None})
    assert excinfo.value.category is PlanAuthorityMismatch.REQUIRED_AUTHORITY_MISSING


def test_brownfield_plan_without_base_authority_is_rejected() -> None:
    """Canonical derivation consumes no base/brownfield input, so a plan
    carrying preserve_unowned content cannot be truthfully rederived: the
    validator rejects it fail-closed rather than accepting it unverified.
    The immutable base-input contract is the identified prerequisite."""
    from tests.slice_5b_composition_helpers import composition_fixture

    fixture = composition_fixture()
    inputs = build_compilation_plan_authority_inputs(
        graph=fixture["graph"],
        payloads=fixture["payloads"],
        registry=_registry(),
    )
    with pytest.raises(PlanAuthorityError):
        validate_compilation_plan_against_authority(fixture["successor"], inputs)


def test_shuffled_raw_authority_inputs_derive_the_same_canonical_plan() -> None:
    inputs, plan = _authority()
    shuffled = CompilationPlanAuthorityInputs(
        graph=inputs.graph,
        payloads=tuple(reversed(inputs.payloads)),
        registry_snapshot=inputs.registry_snapshot,
        scope_selection=inputs.scope_selection,
        structured_output_configs=inputs.structured_output_configs,
        assignment_contract_registry=inputs.assignment_contract_registry,
    )
    canonical = validate_compilation_plan_against_authority(plan, shuffled)
    assert canonical.plan_digest == plan.plan_digest


def test_fresh_process_validation_succeeds() -> None:
    """Durable callers rederive after restart: a fresh interpreter, fed only
    the serialized authority inputs and candidate plan, reaches the same
    canonical acceptance."""
    inputs, plan = _authority()
    probe = (
        "import json, sys\n"
        "from mozaiksai.core.semantics.compilation_plan import CompilationPlan\n"
        "from mozaiksai.core.semantics.plan_authority import (\n"
        "    CompilationPlanAuthorityInputs,\n"
        "    validate_compilation_plan_against_authority,\n"
        ")\n"
        "payload = json.loads(sys.stdin.read())\n"
        "inputs = CompilationPlanAuthorityInputs.model_validate(payload['inputs'])\n"
        "plan = CompilationPlan.model_validate(payload['plan'])\n"
        "canonical = validate_compilation_plan_against_authority(plan, inputs)\n"
        "print(canonical.plan_digest)\n"
    )
    completed = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=str(ROOT),
        input=json.dumps(
            {
                "inputs": inputs.model_dump(mode="json"),
                "plan": plan.model_dump(mode="json"),
            }
        ),
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == plan.plan_digest


# ---------------------------------------------------------------------------
# No bearer authority, no second planner, determinism
# ---------------------------------------------------------------------------


def test_no_bearer_proof_or_token_surface_exists() -> None:
    source = (ROOT / "mozaiksai/core/semantics/plan_authority.py").read_text(
        encoding="utf-8"
    )
    for forbidden in (
        "PlanAuthorityProof",
        "issued_token",
        "IssuanceToken",
        "trusted=True",
        "skip_validation",
    ):
        assert forbidden not in source, forbidden


def test_no_second_planner_exists() -> None:
    """The validator calls the one canonical derive_compilation_plan and
    re-implements none of its derivation vocabulary."""
    source = (ROOT / "mozaiksai/core/semantics/plan_authority.py").read_text(
        encoding="utf-8"
    )
    assert "derive_compilation_plan(" in source
    for derivation_internal in (
        "_condition_state",
        "_sources_for",
        "_renderer_footprint",
        "ConditionIdentifier",
        "_FAMILY_SOURCE_KINDS",
        "PathScope",
        "AssignmentKind",
    ):
        assert derivation_internal not in source, derivation_internal


def test_validation_is_deterministic_and_bounded() -> None:
    inputs, plan = _authority()
    start = time.perf_counter()
    first = validate_compilation_plan_against_authority(plan, inputs)
    second = validate_compilation_plan_against_authority(plan, inputs)
    elapsed = time.perf_counter() - start
    assert first.plan_digest == second.plan_digest
    assert first.canonical_payload() == second.canonical_payload()
    assert elapsed < 60  # offline compiler boundary; generous ceiling only


def test_unreferenced_extra_payload_in_authority_inputs_fails_closed() -> None:
    """Padding the authority inputs with a valid payload no graph node
    references is rejected by the derivation's own payload-closure
    validation — extra authority can never ride along unnoticed."""
    from mozaiksai.core.semantics.payloads import (
        CapabilityPayload,
        build_semantic_payload,
    )

    inputs, plan = _authority()
    orphan = build_semantic_payload(
        CapabilityPayload,
        node_id="mozaiks.capability.orphan",
        payload_version=1,
        scope=plan.scope,
        description="orphan capability payload",
    )
    padded = CompilationPlanAuthorityInputs(
        graph=inputs.graph,
        payloads=inputs.payloads + (orphan,),
        registry_snapshot=inputs.registry_snapshot,
        scope_selection=inputs.scope_selection,
        structured_output_configs=inputs.structured_output_configs,
        assignment_contract_registry=inputs.assignment_contract_registry,
    )
    with pytest.raises(PlanAuthorityError) as excinfo:
        validate_compilation_plan_against_authority(plan, padded)
    assert excinfo.value.category is PlanAuthorityMismatch.REQUIRED_AUTHORITY_MISSING


def test_scope_selection_is_never_silently_reconstructed() -> None:
    """A plan derived under a non-default scope selection validates only
    against inputs carrying that exact selection — no derivation input is
    silently rebuilt with a different default at validation time."""
    from mozaiksai.core.runtime.app.layout_registry import PathScope
    from mozaiksai.core.semantics.compilation_plan import (
        CompilationScopeSelection,
    )

    result, _ = _build(_source())
    selection = CompilationScopeSelection(
        app_manifest_scope=PathScope.WORKSPACE_ROOT
    )
    plan = derive_compilation_plan(
        graph=result.graph,
        payloads=result.payloads,
        registry=_registry(),
        scope_selection=selection,
    )
    matching = build_compilation_plan_authority_inputs(
        graph=result.graph,
        payloads=result.payloads,
        registry=_registry(),
        scope_selection=selection,
    )
    canonical = validate_compilation_plan_against_authority(plan, matching)
    assert canonical.plan_digest == plan.plan_digest
    default_inputs = build_compilation_plan_authority_inputs(
        graph=result.graph,
        payloads=result.payloads,
        registry=_registry(),
    )
    with pytest.raises(PlanAuthorityError):
        validate_compilation_plan_against_authority(plan, default_inputs)


# ---------------------------------------------------------------------------
# Same document / same result: no canonical planner input may remain ambient.
# Codex 3's exact assignment-registry reproduction is preserved here.
# ---------------------------------------------------------------------------


def test_ambient_assignment_registry_mutation_cannot_change_validation() -> None:
    """The same serialized authority document yields the same canonical
    rederived plan even after ambient assignment-descriptor registry state
    changes — derivation consumes the descriptors resolved from the exact
    supplied snapshot, never module globals."""
    import mozaiksai.core.workflow.assignment_kinds as ak

    inputs, plan = _authority()
    document = json.dumps(
        {"inputs": inputs.model_dump(mode="json"), "plan": plan.model_dump(mode="json")}
    )
    revived = CompilationPlanAuthorityInputs.model_validate(
        json.loads(document)["inputs"]
    )
    before = validate_compilation_plan_against_authority(plan, revived)

    original = ak.ASSIGNMENT_CONTRACT_DESCRIPTORS
    try:
        # ambient mutation: rebind the module registry to an empty mapping
        ak.ASSIGNMENT_CONTRACT_DESCRIPTORS = type(original)({})
        revived_again = CompilationPlanAuthorityInputs.model_validate(
            json.loads(document)["inputs"]
        )
        after = validate_compilation_plan_against_authority(plan, revived_again)
    finally:
        ak.ASSIGNMENT_CONTRACT_DESCRIPTORS = original
    assert after.plan_digest == before.plan_digest == plan.plan_digest
    assert after.canonical_payload() == before.canonical_payload()


def test_authority_snapshot_pins_descriptor_content() -> None:
    """A forged descriptor snapshot inside the authority document changes the
    canonical derivation result — descriptor authority is document state, so
    tampering yields a typed mismatch rather than silent divergence."""
    import yaml as _yaml

    configs = {
        "AppGenerator": _yaml.safe_load(
            (
                ROOT / "factory_app/workflows/AppGenerator/structured_outputs.yaml"
            ).read_text(encoding="utf-8")
        )
    }
    result, _ = _build(_source())
    plan = derive_compilation_plan(
        graph=result.graph,
        payloads=result.payloads,
        registry=_registry(),
        structured_output_configs=configs,
    )
    inputs = build_compilation_plan_authority_inputs(
        graph=result.graph,
        payloads=result.payloads,
        registry=_registry(),
        structured_output_configs=configs,
    )
    assert (
        validate_compilation_plan_against_authority(plan, inputs).plan_digest
        == plan.plan_digest
    )
    document = inputs.model_dump(mode="json")
    document["assignment_contract_registry"]["descriptors"] = []
    tampered = CompilationPlanAuthorityInputs.model_validate(document)
    with pytest.raises(PlanAuthorityError):
        validate_compilation_plan_against_authority(plan, tampered)


def test_fresh_process_ambient_registry_independence() -> None:
    """A fresh interpreter validating the same serialized document reaches
    the same canonical digest even with the ambient registry emptied first."""
    inputs, plan = _authority()
    probe = (
        "import json, sys\n"
        "import mozaiksai.core.workflow.assignment_kinds as ak\n"
        "ak.ASSIGNMENT_CONTRACT_DESCRIPTORS = type(ak.ASSIGNMENT_CONTRACT_DESCRIPTORS)({})\n"
        "from mozaiksai.core.semantics.compilation_plan import CompilationPlan\n"
        "from mozaiksai.core.semantics.plan_authority import (\n"
        "    CompilationPlanAuthorityInputs,\n"
        "    validate_compilation_plan_against_authority,\n"
        ")\n"
        "payload = json.loads(sys.stdin.read())\n"
        "inputs = CompilationPlanAuthorityInputs.model_validate(payload['inputs'])\n"
        "plan = CompilationPlan.model_validate(payload['plan'])\n"
        "print(validate_compilation_plan_against_authority(plan, inputs).plan_digest)\n"
    )
    completed = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=str(ROOT),
        input=json.dumps(
            {
                "inputs": inputs.model_dump(mode="json"),
                "plan": plan.model_dump(mode="json"),
            }
        ),
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == plan.plan_digest


# ---------------------------------------------------------------------------
# Closed structured-output configuration: immediate JSON-safety rejection and
# deep immutability. "Frozen model containing mutable dict" is not enough.
# ---------------------------------------------------------------------------


def test_non_json_values_reject_immediately_at_construction() -> None:
    from mozaiksai.core.semantics.plan_authority import CanonicalJsonObject

    class _Custom:
        pass

    def _fn():
        return None

    rejects = [
        {"a": object()},
        {"a": _Custom()},
        {"a": _fn},
        {"a": b"bytes"},
        {"a": {1, 2}},
        {"a": float("nan")},
        {"a": float("inf")},
        {1: "non-string-key"},
        {"a": {"nested": object()}},
        {"a": [1, [2, {"deep": set()}]]},
    ]
    for bad in rejects:
        with pytest.raises((ValueError, TypeError)):
            CanonicalJsonObject.from_python(bad)
    good = CanonicalJsonObject.from_python(
        {"s": "x", "i": 3, "f": 1.5, "b": True, "n": None, "arr": [1, "two", {"k": []}]}
    )
    assert good.to_python() == {
        "s": "x",
        "i": 3,
        "f": 1.5,
        "b": True,
        "n": None,
        "arr": [1, "two", {"k": []}],
    }
    with pytest.raises((ValueError, TypeError)):
        CanonicalJsonObject.from_python({"file": open(__file__)})  # noqa: SIM115


def test_deep_immutability_and_source_mutation_independence() -> None:
    from mozaiksai.core.semantics.plan_authority import CanonicalJsonObject

    source = {"a": {"b": [1, 2, 3]}, "c": "keep"}
    frozen = CanonicalJsonObject.from_python(source)
    # a source dictionary mutated after construction cannot alter the document
    source["a"]["b"].append(99)
    source["c"] = "changed"
    assert frozen.to_python() == {"a": {"b": [1, 2, 3]}, "c": "keep"}
    # top-level and nested assignment rejected; entries are tuples
    with pytest.raises(ValidationError):
        frozen.entries = ()  # type: ignore[misc]
    entry = frozen.entries[0]
    with pytest.raises(ValidationError):
        entry.value = None  # type: ignore[misc]
    assert isinstance(frozen.entries, tuple)
    nested = frozen.to_python()
    nested["a"]["b"].append(4)  # mutating the EXPORT cannot touch the document
    assert frozen.to_python() == {"a": {"b": [1, 2, 3]}, "c": "keep"}
    # serialization -> deserialization yields the identical canonical contract
    revived = CanonicalJsonObject.model_validate(
        json.loads(json.dumps(frozen.model_dump(mode="json")))
    )
    assert revived == frozen
    assert revived.to_python() == frozen.to_python()


def test_model_copy_cannot_smuggle_unchecked_open_values() -> None:
    """model_copy bypasses validators, but the validator's own cold
    re-validation of the authority document rejects anything that cannot
    round-trip the closed contract."""
    inputs, plan = _authority()
    smuggled = inputs.model_copy(
        update={"structured_output_configs": {"AppGenerator": object()}}
    )
    with pytest.raises(PlanAuthorityError) as excinfo:
        validate_compilation_plan_against_authority(plan, smuggled)
    assert excinfo.value.category is PlanAuthorityMismatch.REQUIRED_AUTHORITY_MISSING


# ---------------------------------------------------------------------------
# Complete derivation-input identity: every canonical derivation parameter is
# represented by the authority contract. A new authority-bearing parameter on
# derive_compilation_plan fails this test until the contract carries it.
# ---------------------------------------------------------------------------


def test_every_derivation_input_is_represented_in_authority_inputs() -> None:
    import inspect

    parameters = set(
        inspect.signature(derive_compilation_plan).parameters
    )
    represented = {
        "graph": "graph",
        "payloads": "payloads",
        "registry": "registry_snapshot",
        "scope_selection": "scope_selection",
        "structured_output_configs": "structured_output_configs",
        "assignment_descriptors": "assignment_contract_registry",
    }
    assert parameters == set(represented), (
        "derive_compilation_plan gained or lost an authority-bearing "
        "parameter; CompilationPlanAuthorityInputs must be updated in the "
        f"same change. parameters={sorted(parameters)}"
    )
    fields = set(CompilationPlanAuthorityInputs.model_fields)
    for parameter, field in represented.items():
        assert field in fields, (parameter, field)
