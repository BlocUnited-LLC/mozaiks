from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from mozaiksai.core.runtime.app.layout_registry import ValidatorIdentifier
from mozaiksai.core.semantics.compilation_plan import PlanDisposition, derive_compilation_plan
from mozaiksai.core.semantics.plan_authority import (
    build_compilation_plan_authority_inputs,
)
from mozaiksai.core.semantics.refs import CompilationPlanRef, PlanUnitRef, SemanticPayloadRef
from mozaiksai.core.semantics.resolver import ReferenceResolutionError, SemanticReferenceResolver
from mozaiksai.core.workflow.plan_assignment_compiler import (
    ApprovedAssignmentSpec,
    ApprovedPlan,
    compile_approved_plan,
)
from mozaiksai.core.workflow.structured_output_contracts import StructuredOutputContractRef
from tests.test_compilation_plan import _corpus_graph, _registry

_AUTHORITY: dict = {}


@lru_cache(maxsize=1)
def _fixture():
    config = yaml.safe_load(
        Path("factory_app/workflows/AppGenerator/structured_outputs.yaml").read_text(
            encoding="utf-8"
        )
    )
    graph, payloads = _corpus_graph()
    plan = derive_compilation_plan(
        graph=graph,
        payloads=payloads,
        registry=_registry(),
        structured_output_configs={"AppGenerator": config},
    )
    _AUTHORITY["inputs"] = build_compilation_plan_authority_inputs(
        graph=graph,
        payloads=payloads,
        registry=_registry(),
        structured_output_configs={"AppGenerator": config},
    )
    unit = next(
        item
        for item in plan.units
        if item.disposition is PlanDisposition.AGENT_AUTHOR
    )
    resolver = SemanticReferenceResolver()
    for payload in payloads:
        resolver.register_semantic_payload(payload)
    resolver.register_semantic_graph_v2(graph)
    resolver.register_compilation_plan(plan)
    plan_ref = CompilationPlanRef(
        subject_id=plan.graph_id,
        subject_version=plan.graph_version,
        content_digest=plan.plan_digest,
        scope=plan.scope,
    )
    unit_ref = PlanUnitRef(
        compilation_plan_ref=plan_ref,
        unit_id=unit.unit_id,
        unit_digest=unit.unit_digest,
    )
    payload_by_id = {payload.node_id: payload for payload in payloads}
    source_refs = tuple(
        SemanticPayloadRef(
            node_id=source.node_id,
            payload_kind=payload_by_id[source.node_id].payload_kind.value,
            payload_version=payload_by_id[source.node_id].payload_version,
            content_digest=source.payload_digest,
            scope=plan.scope,
        )
        for source in unit.sources
    )
    dependency_refs = tuple(
        PlanUnitRef(
            compilation_plan_ref=plan_ref,
            unit_id=dependency_id,
            unit_digest=plan.unit(dependency_id).unit_digest,
        )
        for dependency_id in unit.depends_on_units
    )
    spec = ApprovedAssignmentSpec(
        plan_unit_ref=unit_ref,
        assignment_kind=unit.assignment_kind,
        dependency_context_refs=(*source_refs, *dependency_refs),
        required_structured_output_ref=unit.required_structured_output_ref,
        required_validators=(unit.validator,),
        assignment_retry_limit=0,
        base_revision_digest=None,
    )
    return config, plan, unit, resolver, spec


def test_closed_spec_compiles_paths_and_identity_from_plan_unit() -> None:
    config, _plan, unit, resolver, spec = _fixture()
    result = compile_approved_plan(
        ApprovedPlan(assignments=(spec,)),
        resolver=resolver,
        authority_inputs=_AUTHORITY["inputs"],
        structured_output_configs={"AppGenerator": config},
    )
    assignment = result.ordered_assignments[0]
    assert assignment.owned_paths == tuple(output.path for output in unit.outputs)
    assert assignment.assignment_kind is unit.assignment_kind
    assert assignment.base_revision_digest is None
    assert assignment.assignment_id.startswith("wa_")

    forged = assignment.model_dump(mode="json")
    forged["assignment_id"] = "wa_000000000000000000000000"
    with pytest.raises(ValidationError, match="canonical assignment identity"):
        type(assignment).model_validate(forged)


@pytest.mark.parametrize(
    "retired",
    [
        "logical_id",
        "owned_paths",
        "depends_on",
        "allowed_agent_ids",
        "required_structured_output_id",
        "baseline_sha",
    ],
)
def test_retired_prototype_fields_are_forbidden(retired: str) -> None:
    _config, _plan, _unit, _resolver, spec = _fixture()
    document = spec.model_dump(mode="json")
    document[retired] = "runtime-id"
    with pytest.raises(ValidationError, match="extra"):
        ApprovedAssignmentSpec.model_validate(document)


def test_arbitrary_context_strings_and_runtime_ids_fail() -> None:
    _config, _plan, _unit, _resolver, spec = _fixture()
    document = spec.model_dump(mode="json")
    document["dependency_context_refs"] = ["channel://agent/session"]
    with pytest.raises(ValidationError):
        ApprovedAssignmentSpec.model_validate(document)


def test_missing_or_extra_source_ref_fails_exact_closure() -> None:
    config, _plan, _unit, resolver, spec = _fixture()
    missing = spec.model_copy(update={"dependency_context_refs": ()})
    with pytest.raises(ValueError, match="exactly match"):
        compile_approved_plan(
            ApprovedPlan(assignments=(missing,)),
            resolver=resolver,
            authority_inputs=_AUTHORITY["inputs"],
            structured_output_configs={"AppGenerator": config},
        )


def test_source_and_unit_dependency_refs_are_exact_and_same_plan() -> None:
    config, plan, _unit, resolver, _spec = _fixture()
    _graph, payloads = _corpus_graph()
    payload_by_id = {payload.node_id: payload for payload in payloads}
    unit = next(
        item
        for item in plan.units
        if item.disposition is PlanDisposition.AGENT_AUTHOR and item.depends_on_units
    )
    plan_ref = CompilationPlanRef(
        subject_id=plan.graph_id,
        subject_version=plan.graph_version,
        content_digest=plan.plan_digest,
        scope=plan.scope,
    )
    source_refs = tuple(
        SemanticPayloadRef(
            node_id=source.node_id,
            payload_kind=payload_by_id[source.node_id].payload_kind.value,
            payload_version=payload_by_id[source.node_id].payload_version,
            content_digest=source.payload_digest,
            scope=plan.scope,
        )
        for source in unit.sources
    )
    dependency_refs = tuple(
        PlanUnitRef(
            compilation_plan_ref=plan_ref,
            unit_id=dependency_id,
            unit_digest=plan.unit(dependency_id).unit_digest,
        )
        for dependency_id in unit.depends_on_units
    )
    spec = ApprovedAssignmentSpec(
        plan_unit_ref=PlanUnitRef(
            compilation_plan_ref=plan_ref,
            unit_id=unit.unit_id,
            unit_digest=unit.unit_digest,
        ),
        assignment_kind=unit.assignment_kind,
        dependency_context_refs=source_refs + dependency_refs,
        required_structured_output_ref=unit.required_structured_output_ref,
        required_validators=(unit.validator,),
        assignment_retry_limit=0,
        base_revision_digest=None,
    )
    compiled = compile_approved_plan(
        ApprovedPlan(assignments=(spec,)),
        resolver=resolver,
        authority_inputs=_AUTHORITY["inputs"],
        structured_output_configs={"AppGenerator": config},
    ).ordered_assignments[0]
    assert {ref.unit_id for ref in compiled.depends_on_unit_refs} == set(
        unit.depends_on_units
    )

    missing = spec.model_copy(
        update={"dependency_context_refs": source_refs + dependency_refs[1:]}
    )
    with pytest.raises(ValueError, match="exactly match unit dependencies"):
        compile_approved_plan(
            ApprovedPlan(assignments=(missing,)),
            resolver=resolver,
            authority_inputs=_AUTHORITY["inputs"],
            structured_output_configs={"AppGenerator": config},
        )

    foreign_plan_ref = plan_ref.model_copy(update={"subject_id": "foreign_graph"})
    foreign_dependency = dependency_refs[0].model_copy(
        update={"compilation_plan_ref": foreign_plan_ref}
    )
    foreign = spec.model_copy(
        update={
            "dependency_context_refs": source_refs
            + (foreign_dependency,)
            + dependency_refs[1:]
        }
    )
    with pytest.raises(ValueError, match="foreign plan"):
        compile_approved_plan(
            ApprovedPlan(assignments=(foreign,)),
            resolver=resolver,
            authority_inputs=_AUTHORITY["inputs"],
            structured_output_configs={"AppGenerator": config},
        )


def test_extra_semantic_source_ref_fails_exact_closure() -> None:
    config, _plan, unit, resolver, spec = _fixture()
    _graph, payloads = _corpus_graph()
    source_ids = {source.node_id for source in unit.sources}
    extra_payload = next(payload for payload in payloads if payload.node_id not in source_ids)
    extra_ref = SemanticPayloadRef(
        node_id=extra_payload.node_id,
        payload_kind=extra_payload.payload_kind.value,
        payload_version=extra_payload.payload_version,
        content_digest=extra_payload.payload_digest,
        scope=extra_payload.scope,
    )
    extra = spec.model_copy(
        update={"dependency_context_refs": spec.dependency_context_refs + (extra_ref,)}
    )
    with pytest.raises(ValueError, match="exactly match the source footprint"):
        compile_approved_plan(
            ApprovedPlan(assignments=(extra,)),
            resolver=resolver,
            authority_inputs=_AUTHORITY["inputs"],
            structured_output_configs={"AppGenerator": config},
        )


def test_plan_unit_ref_cold_resolution_rejects_stale_or_forged_digest() -> None:
    _config, _plan, _unit, resolver, spec = _fixture()
    forged = spec.plan_unit_ref.model_copy(update={"unit_digest": "0" * 64})
    with pytest.raises(ReferenceResolutionError, match="digest mismatch"):
        resolver.resolve_plan_unit(
            forged, requesting_scope=forged.compilation_plan_ref.scope
        )


def test_stale_structured_output_schema_digest_fails() -> None:
    config, _plan, _unit, resolver, spec = _fixture()
    stale = StructuredOutputContractRef(
        workflow_name=spec.required_structured_output_ref.workflow_name,
        model_id=spec.required_structured_output_ref.model_id,
        schema_digest="0" * 64,
    )
    tampered = spec.model_copy(update={"required_structured_output_ref": stale})
    with pytest.raises(ValueError, match="does not match plan-unit"):
        compile_approved_plan(
            ApprovedPlan(assignments=(tampered,)),
            resolver=resolver,
            authority_inputs=_AUTHORITY["inputs"],
            structured_output_configs={"AppGenerator": config},
        )


@pytest.mark.parametrize("value", ["b" * 40, "A" * 64, "not-a-digest"])
def test_base_revision_digest_is_required_nullable_sha256(value: str) -> None:
    _config, _plan, _unit, _resolver, spec = _fixture()
    document = spec.model_dump(mode="json")
    document["base_revision_digest"] = value
    with pytest.raises(ValidationError, match="base_revision_digest"):
        ApprovedAssignmentSpec.model_validate(document)


def test_omitted_base_revision_digest_fails() -> None:
    _config, _plan, _unit, _resolver, spec = _fixture()
    document = spec.model_dump(mode="json")
    document.pop("base_revision_digest")
    with pytest.raises(ValidationError, match="base_revision_digest"):
        ApprovedAssignmentSpec.model_validate(document)


def _compile(config, resolver, spec):
    return compile_approved_plan(
        ApprovedPlan(assignments=(spec,)),
        resolver=resolver,
        authority_inputs=_AUTHORITY["inputs"],
        structured_output_configs={"AppGenerator": config},
    )


def test_forged_approved_unit_digest_fails_against_canonical_plan() -> None:
    config, _plan, _unit, resolver, spec = _fixture()
    forged = spec.model_copy(
        update={
            "plan_unit_ref": spec.plan_unit_ref.model_copy(
                update={"unit_digest": "0" * 64}
            )
        }
    )
    with pytest.raises(ValueError, match="canonical unit identity"):
        _compile(config, resolver, forged)


def test_hostile_plan_unit_resolution_is_never_execution_authority(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """After canonical rederivation every execution-authorizing unit fact
    comes from the canonical plan itself; a hostile ``resolve_plan_unit`` is
    never even reached."""

    config, _plan, unit, resolver, spec = _fixture()
    baseline = _compile(config, resolver, spec)
    calls: list[str] = []

    def hostile(ref, *, requesting_scope):
        calls.append(ref.unit_id)
        raise AssertionError("plan-unit resolution must not supply execution facts")

    monkeypatch.setattr(resolver, "resolve_plan_unit", hostile)
    compiled = _compile(config, resolver, spec)
    assert calls == []
    assert compiled == baseline
    assignment = compiled.ordered_assignments[0]
    assert assignment.owned_paths == tuple(output.path for output in unit.outputs)
    assert assignment.required_validators == (unit.validator,)
    assert assignment.assignment_kind is unit.assignment_kind
    assert assignment.required_structured_output_ref == unit.required_structured_output_ref


@pytest.mark.parametrize(
    "substitution",
    [
        "owned_path",
        "validator",
        "assignment_kind",
        "structured_output_ref",
        "dependency",
        "identity_binding",
    ],
)
def test_hostile_unit_substitution_cannot_reach_assignment_facts(
    monkeypatch: pytest.MonkeyPatch, substitution: str
) -> None:
    """A resolver returning a modified but plausible unit must not change one
    emitted assignment fact: the canonical plan is the only unit authority."""

    config, plan, unit, resolver, spec = _fixture()
    baseline = _compile(config, resolver, spec)
    mutations = {
        "owned_path": {
            "outputs": (
                unit.outputs[0].model_copy(update={"path": "evil/injected.py"}),
                *unit.outputs[1:],
            )
        },
        "validator": {"validator": ValidatorIdentifier.NONE},
        "assignment_kind": {"assignment_kind": None},
        "structured_output_ref": {"required_structured_output_ref": None},
        "dependency": {"depends_on_units": ()},
        "identity_binding": {
            "placeholder_values": tuple(
                (name, f"evil-{value}") for name, value in unit.placeholder_values
            )
        },
    }

    def hostile(ref, *, requesting_scope):
        honest = plan.unit(ref.unit_id)
        if ref.unit_id == unit.unit_id:
            return honest.model_copy(update=mutations[substitution])
        return honest

    monkeypatch.setattr(resolver, "resolve_plan_unit", hostile)
    assert _compile(config, resolver, spec) == baseline
