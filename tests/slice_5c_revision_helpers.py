from __future__ import annotations

from pathlib import Path

import yaml

from mozaiksai.core.runtime.app.layout_registry import ValidatorIdentifier
from mozaiksai.core.semantics.artifact_revision import (
    build_artifact_revision,
    build_artifact_revision_validation_evidence,
)
from mozaiksai.core.semantics.binding import build_implementation_binding
from mozaiksai.core.semantics.canonical import canonical_digest
from mozaiksai.core.semantics.compilation_plan import CompilationPlan, PlanDisposition
from mozaiksai.core.semantics.composition_ledger import compose_plan_artifacts
from mozaiksai.core.semantics.graph import build_semantic_graph_v2
from mozaiksai.core.semantics.materialization import MaterializedBundle
from mozaiksai.core.semantics.refs import (
    CompilationPlanRef,
    ImplementationBindingRef,
    PlanUnitRef,
    SemanticGraphRef,
    SemanticPayloadRef,
)
from mozaiksai.core.semantics.resolver import SemanticReferenceResolver
from mozaiksai.core.workflow.assignment_artifacts import (
    ValidatorReceipt,
    build_assignment_artifact_result,
)
from mozaiksai.core.workflow.plan_assignment_compiler import (
    ApprovedAssignmentSpec,
    ApprovedPlan,
    compile_approved_plan,
)
from mozaiksai.core.workflow.structured_output_contracts import stable_digest
from tests.slice_5b_composition_helpers import (
    composition_fixture,
    empty_assignment_set,
    issue_test_authority_proof,
)

ROOT = Path(__file__).resolve().parents[1]


def revision_fixture() -> dict[str, object]:
    source = composition_fixture()
    graph = source["graph"]
    payloads = source["payloads"]
    plan = source["base"]
    resolver = SemanticReferenceResolver()
    for payload in payloads:
        resolver.register_semantic_payload(payload)
    resolver.register_semantic_graph_v2(graph)
    resolver.register_compilation_plan(plan)

    graph_ref = SemanticGraphRef(
        subject_id=graph.graph_id,
        subject_version=graph.version,
        content_digest=graph.graph_digest,
        scope=graph.scope,
    )
    binding = build_implementation_binding(
        binding_id="slice-5c-binding",
        version=1,
        scope=graph.scope,
        semantic_graph_ref=graph_ref,
        capability_pack_selections=(),
        renderer_selections=(),
        deployment_profile_selections=(),
    )
    resolver.register_implementation_binding(binding)
    binding_ref = ImplementationBindingRef(
        subject_id=binding.binding_id,
        subject_version=binding.version,
        content_digest=binding.binding_digest,
        scope=binding.scope,
    )
    plan_ref = CompilationPlanRef(
        subject_id=plan.graph_id,
        subject_version=plan.graph_version,
        content_digest=plan.plan_digest,
        scope=plan.scope,
    )
    materialized = MaterializedBundle(
        plan_digest=plan.plan_digest,
        outputs=source["base_outputs"],
        external_handoff_units=(),
        inapplicable_units=(),
        unsupplied_preserved_units=(),
        instance_scope_deferred_units=(),
        gap_count=0,
    )
    bundle = compose_plan_artifacts(
        plan=plan,
        resolver=resolver,
        assignments=empty_assignment_set(),
        assignment_results=(),
        materialized_bundle=materialized,
        plan_authority_proof=issue_test_authority_proof(plan),
        base_revision_digest=None,
    )
    validators = tuple(
        sorted(
            {
                unit.validator
                for unit in plan.units
                if unit.validator is not ValidatorIdentifier.NONE
            },
            key=lambda item: item.value,
        )
    )
    receipts = tuple(
        ValidatorReceipt(
            validator=validator,
            subject_digest=bundle.ledger.bundle_digest,
            passed=True,
            evidence_digest=stable_digest(
                {
                    "validator": validator.value,
                    "subject_digest": bundle.ledger.bundle_digest,
                    "passed": True,
                }
            ),
        )
        for validator in validators
    )
    app_id = "corpus-app"
    evidence = build_artifact_revision_validation_evidence(
        scope=graph.scope,
        app_id=app_id,
        plan=plan,
        ledger=bundle.ledger,
        assignment_results=(),
        bundle_validator_receipts=receipts,
    )
    revision = build_artifact_revision(
        scope=graph.scope,
        app_id=app_id,
        parent_revision_ref=None,
        semantic_graph_ref=graph_ref,
        implementation_binding_ref=binding_ref,
        compilation_plan_ref=plan_ref,
        composition_ledger_digest=bundle.ledger.ledger_digest,
        bundle_digest=bundle.ledger.bundle_digest,
        validation_evidence_digest=evidence.evidence_digest,
    )
    return {
        "app_id": app_id,
        "graph": graph,
        "plan": plan,
        "binding": binding,
        "resolver": resolver,
        "bundle": bundle,
        "evidence": evidence,
        "revision": revision,
        "receipts": receipts,
    }


def executable_revision_fixture() -> dict[str, object]:
    """Genesis closure retaining the accepted 5B agent + renderer loader proof."""

    source = composition_fixture()
    source_graph = source["graph"]
    payloads = source["payloads"]
    graph = build_semantic_graph_v2(
        graph_id=source_graph.graph_id,
        version=2,
        scope=source_graph.scope,
        nodes=source_graph.nodes,
        edges=source_graph.edges,
        namespace_grants=source_graph.namespace_grants,
    )
    source_plan = source["successor"]
    candidate = CompilationPlan.model_construct(
        schema_version=source_plan.schema_version,
        graph_id=graph.graph_id,
        graph_version=graph.version,
        scope=graph.scope,
        graph_digest=graph.graph_digest,
        scope_selection=source_plan.scope_selection,
        registry_schema_version=source_plan.registry_schema_version,
        registry_digest=source_plan.registry_digest,
        units=source_plan.units,
        gaps=(),
        plan_digest="0" * 64,
    )
    plan_payload = candidate.canonical_payload(include_digest=False)
    plan_payload["plan_digest"] = canonical_digest(plan_payload)
    plan = CompilationPlan.model_validate(plan_payload)

    resolver = SemanticReferenceResolver()
    for payload in payloads:
        resolver.register_semantic_payload(payload)
    resolver.register_semantic_graph_v2(graph)
    resolver.register_compilation_plan(plan)
    graph_ref = SemanticGraphRef(
        subject_id=graph.graph_id,
        subject_version=graph.version,
        content_digest=graph.graph_digest,
        scope=graph.scope,
    )
    binding = build_implementation_binding(
        binding_id="slice-5c-executable-binding",
        version=1,
        scope=graph.scope,
        semantic_graph_ref=graph_ref,
        capability_pack_selections=(),
        renderer_selections=(),
        deployment_profile_selections=(),
    )
    resolver.register_implementation_binding(binding)
    binding_ref = ImplementationBindingRef(
        subject_id=binding.binding_id,
        subject_version=binding.version,
        content_digest=binding.binding_digest,
        scope=binding.scope,
    )
    plan_ref = CompilationPlanRef(
        subject_id=plan.graph_id,
        subject_version=plan.graph_version,
        content_digest=plan.plan_digest,
        scope=plan.scope,
    )

    agent = next(unit for unit in plan.units if unit.disposition is PlanDisposition.AGENT_AUTHOR)
    payload_by_id = {payload.node_id: payload for payload in payloads}
    dependency_refs = tuple(
        SemanticPayloadRef(
            node_id=item.node_id,
            payload_kind=payload_by_id[item.node_id].payload_kind.value,
            payload_version=payload_by_id[item.node_id].payload_version,
            content_digest=item.payload_digest,
            scope=plan.scope,
        )
        for item in agent.sources
    )
    approved = ApprovedPlan(
        assignments=(
            ApprovedAssignmentSpec(
                plan_unit_ref=PlanUnitRef(
                    compilation_plan_ref=plan_ref,
                    unit_id=agent.unit_id,
                    unit_digest=agent.unit_digest,
                ),
                assignment_kind=agent.assignment_kind,
                dependency_context_refs=dependency_refs,
                required_structured_output_ref=agent.required_structured_output_ref,
                required_validators=(agent.validator,),
                assignment_retry_limit=2,
                base_revision_digest=None,
            ),
        )
    )
    structured = yaml.safe_load(
        (ROOT / "factory_app/workflows/AppGenerator/structured_outputs.yaml").read_text(
            encoding="utf-8"
        )
    )
    assignments = compile_approved_plan(
        approved,
        resolver=resolver,
        structured_output_configs={"AppGenerator": structured},
    )
    result = build_assignment_artifact_result(
        assignment=assignments.ordered_assignments[0],
        structured_output={
            "assignment_kind": "module_helper_implementation",
            "module_id": "reports",
            "helper_id": "report_hook",
            "helper_source": "def report_hook():\n    return None\n",
        },
        artifacts={
            "modules/reports/backend/report_hook.py": "def report_hook():\n    return None\n"
        },
        structured_output_configs={"AppGenerator": structured},
        validator_runner=lambda _validator, files: bool(files),
    )
    materialized_source = source["materialized"]
    materialized = MaterializedBundle(
        plan_digest=plan.plan_digest,
        outputs=(*materialized_source.outputs, source["base_outputs"][0]),
        external_handoff_units=materialized_source.external_handoff_units,
        inapplicable_units=materialized_source.inapplicable_units,
        unsupplied_preserved_units=(),
        instance_scope_deferred_units=materialized_source.instance_scope_deferred_units,
        gap_count=0,
    )
    bundle = compose_plan_artifacts(
        plan=plan,
        resolver=resolver,
        assignments=assignments,
        assignment_results=(result,),
        materialized_bundle=materialized,
        plan_authority_proof=issue_test_authority_proof(plan),
        base_revision_digest=None,
    )
    validators = tuple(
        sorted(
            {
                unit.validator
                for unit in plan.units
                if unit.validator is not ValidatorIdentifier.NONE
            },
            key=lambda item: item.value,
        )
    )
    receipts = tuple(
        ValidatorReceipt(
            validator=validator,
            subject_digest=bundle.ledger.bundle_digest,
            passed=True,
            evidence_digest=stable_digest(
                {
                    "validator": validator.value,
                    "subject_digest": bundle.ledger.bundle_digest,
                    "passed": True,
                }
            ),
        )
        for validator in validators
    )
    evidence = build_artifact_revision_validation_evidence(
        scope=graph.scope,
        app_id="slice-5c-golden",
        plan=plan,
        ledger=bundle.ledger,
        assignment_results=(result,),
        bundle_validator_receipts=receipts,
    )
    revision = build_artifact_revision(
        scope=graph.scope,
        app_id="slice-5c-golden",
        parent_revision_ref=None,
        semantic_graph_ref=graph_ref,
        implementation_binding_ref=binding_ref,
        compilation_plan_ref=plan_ref,
        composition_ledger_digest=bundle.ledger.ledger_digest,
        bundle_digest=bundle.ledger.bundle_digest,
        validation_evidence_digest=evidence.evidence_digest,
    )
    return {
        "app_id": "slice-5c-golden",
        "graph": graph,
        "payloads": payloads,
        "plan": plan,
        "binding": binding,
        "resolver": resolver,
        "assignments": assignments,
        "assignment_results": (result,),
        "bundle": bundle,
        "evidence": evidence,
        "revision": revision,
        "receipts": receipts,
    }


__all__ = ["executable_revision_fixture", "revision_fixture"]
