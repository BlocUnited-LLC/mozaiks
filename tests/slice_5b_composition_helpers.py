from __future__ import annotations

import hashlib
from functools import lru_cache
from pathlib import Path

import yaml

from mozaiksai.core.semantics.canonical import canonical_digest
from mozaiksai.core.semantics.compilation_plan import (
    CompilationPlan,
    FamilyInstancePlan,
    PlanDisposition,
    PlanOutput,
    derive_compilation_plan,
    plan_regeneration_closure,
)
from mozaiksai.core.semantics.materialization import (
    MaterializedBundle,
    MaterializedOutput,
    _materialize_unit,
)
from mozaiksai.core.semantics.refs import (
    CompilationPlanRef,
    PlanUnitRef,
    SemanticPayloadRef,
)
from mozaiksai.core.semantics.resolver import SemanticReferenceResolver
from mozaiksai.core.workflow.assignment_artifacts import build_assignment_artifact_result
from mozaiksai.core.workflow.plan_assignment_compiler import (
    ApprovedAssignmentSpec,
    ApprovedPlan,
    CompiledAssignmentSet,
    compile_approved_plan,
)
from tests.test_compilation_plan import _corpus_graph, _registry

ROOT = Path(__file__).resolve().parents[1]


def _plan(
    source: CompilationPlan,
    units: tuple[FamilyInstancePlan, ...],
    *,
    graph_version: int,
) -> CompilationPlan:
    candidate = CompilationPlan.model_construct(
        schema_version=source.schema_version,
        graph_id=source.graph_id,
        graph_version=graph_version,
        scope=source.scope,
        graph_digest=source.graph_digest,
        scope_selection=source.scope_selection,
        registry_schema_version=source.registry_schema_version,
        registry_digest=source.registry_digest,
        assignment_contracts_digest=source.assignment_contracts_digest,
        units=units,
        gaps=(),
        plan_digest="0" * 64,
    )
    document = candidate.canonical_payload(include_digest=False)
    document["plan_digest"] = canonical_digest(document)
    return CompilationPlan.model_validate(document)


def _output(unit: FamilyInstancePlan, content: bytes, *, origin: str) -> MaterializedOutput:
    target = unit.outputs[0]
    return MaterializedOutput(
        unit_id=unit.unit_id,
        path_scope=target.path_scope,
        path=target.path,
        content=content,
        origin=origin,
        content_digest=hashlib.sha256(content).hexdigest(),
    )


@lru_cache(maxsize=1)
def composition_fixture() -> dict[str, object]:
    structured = yaml.safe_load(
        (ROOT / "factory_app/workflows/AppGenerator/structured_outputs.yaml").read_text(
            encoding="utf-8"
        )
    )
    graph, payloads = _corpus_graph()
    complete = derive_compilation_plan(
        graph=graph,
        payloads=payloads,
        registry=_registry(),
        structured_output_configs={"AppGenerator": structured},
    )

    def one(disposition: PlanDisposition, prefix: str) -> FamilyInstancePlan:
        return next(
            unit
            for unit in complete.units
            if unit.disposition is disposition and unit.unit_id.startswith(prefix)
        )

    handoff = one(PlanDisposition.EXTERNAL_HANDOFF, "app_deployment_artifact/")

    def preserved_unit(unit_id: str, family: str, path: str) -> FamilyInstancePlan:
        return handoff.model_copy(
            update={
                "unit_id": unit_id,
                "family_kind": family,
                "disposition": PlanDisposition.PRESERVE_UNOWNED,
                "outputs": (PlanOutput(path_scope="app_bundle_root", path=path),),
                "depends_on_units": (),
                "materializer": "preserved_opaque",
            }
        )

    reusable = preserved_unit(
        "app_service_support/preserved", "app_service_support", "services/config.py"
    )
    removed = preserved_unit(
        "app_backend_support/preserved", "app_backend_support", "backend/admin_config.py"
    )
    agent = one(PlanDisposition.AGENT_AUTHOR, "module_backend_helper/").model_copy(
        update={"depends_on_units": ()}
    )
    rendered = one(PlanDisposition.RENDER, "app_ui_page_schema/")
    preserved = preserved_unit(
        "workflow_config/preserved", "workflow_config", "workflows/demo/agents.yaml"
    )
    inapplicable = one(PlanDisposition.INAPPLICABLE, "prohibited_legacy/")
    input_only_source = next(
        unit
        for unit in complete.units
        if unit.disposition is PlanDisposition.INAPPLICABLE
        and unit.unit_id != inapplicable.unit_id
    )
    input_only = input_only_source.model_copy(
        update={"disposition": PlanDisposition.INPUT_ONLY}
    )

    base = _plan(complete, (reusable, removed), graph_version=1)
    successor = _plan(
        complete,
        (reusable, agent, rendered, preserved, handoff, inapplicable, input_only),
        graph_version=2,
    )
    closure = plan_regeneration_closure(base, successor)
    resolver = SemanticReferenceResolver()
    for payload in payloads:
        resolver.register_semantic_payload(payload)
    resolver.register_semantic_graph_v2(graph)
    resolver.register_compilation_plan(base)
    resolver.register_compilation_plan(successor)

    plan_ref = CompilationPlanRef(
        subject_id=successor.graph_id,
        subject_version=successor.graph_version,
        content_digest=successor.plan_digest,
        scope=successor.scope,
    )
    unit_ref = PlanUnitRef(
        compilation_plan_ref=plan_ref,
        unit_id=agent.unit_id,
        unit_digest=agent.unit_digest,
    )
    payload_by_id = {payload.node_id: payload for payload in payloads}
    source_refs = tuple(
        SemanticPayloadRef(
            node_id=source.node_id,
            payload_kind=payload_by_id[source.node_id].payload_kind.value,
            payload_version=payload_by_id[source.node_id].payload_version,
            content_digest=source.payload_digest,
            scope=successor.scope,
        )
        for source in agent.sources
    )
    base_revision_digest = "b" * 64
    approved = ApprovedPlan(
        assignments=(
            ApprovedAssignmentSpec(
                plan_unit_ref=unit_ref,
                assignment_kind=agent.assignment_kind,
                dependency_context_refs=source_refs,
                required_structured_output_ref=agent.required_structured_output_ref,
                required_validators=(agent.validator,),
                assignment_retry_limit=2,
                base_revision_digest=base_revision_digest,
            ),
        )
    )
    assignments = compile_approved_plan(
        approved,
        resolver=resolver,
        structured_output_configs={"AppGenerator": structured},
    )
    assignment = assignments.ordered_assignments[0]
    helper_source = "def report_hook():\n    return None\n"
    result = build_assignment_artifact_result(
        assignment=assignment,
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

    rendered_outputs: list[MaterializedOutput] = []
    _materialize_unit(
        rendered,
        payload_by_node=payload_by_id,
        app_config_render_input=None,
        app_config_selection=None,
        preserved_by_unit={},
        bundle_outputs=rendered_outputs,
        external=[],
        inapplicable=[],
        unsupplied=[],
        input_only=[],
        deferred=[],
    )
    preserved_output = _output(preserved, b"agents: []\n", origin="preserved")
    materialized = MaterializedBundle(
        plan_digest=successor.plan_digest,
        outputs=(*rendered_outputs, preserved_output),
        external_handoff_units=(handoff.unit_id,),
        inapplicable_units=(inapplicable.unit_id,),
        unsupplied_preserved_units=(),
        input_only_units=(),
        instance_scope_deferred_units=(preserved.unit_id,),
        gap_count=0,
        closure=closure,
    )
    base_outputs = (
        _output(reusable, b"# reusable service config\n", origin="preserved"),
        _output(removed, b"# removed backend config\n", origin="preserved"),
    )
    return {
        "graph": graph,
        "payloads": payloads,
        "base": base,
        "successor": successor,
        "closure": closure,
        "resolver": resolver,
        "assignments": assignments,
        "result": result,
        "materialized": materialized,
        "base_outputs": base_outputs,
        "base_revision_digest": base_revision_digest,
    }


def empty_assignment_set() -> CompiledAssignmentSet:
    from mozaiksai.core.workflow.structured_output_contracts import stable_digest

    return CompiledAssignmentSet(
        ordered_assignments=(), assignment_set_digest=stable_digest([])
    )
