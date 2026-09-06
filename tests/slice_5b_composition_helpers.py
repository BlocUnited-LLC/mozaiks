"""Shared 5B composition fixtures built on canonically derived plans.

Every plan here is produced by the one canonical ``derive_compilation_plan``
implementation and validates against its exact immutable
``CompilationPlanAuthorityInputs`` â€” no synthetic ``model_construct`` plans,
no re-dispositioned units, no fabricated ``preserve_unowned`` content. The
layout registry is a reduced but internally consistent authority (page schema,
the four renderer-ready application families, and the module backend-helper
agent family) so honest derivation is genuinely gap-free; a reduced registry
is a legitimate authority input, pinned like any other by the authority
document.

Brownfield/preserve_unowned composition is intentionally NOT represented: the
immutable base-input authority contract does not exist yet, and plans carrying
such content fail closed with the typed ``base_authority_missing`` category
(see test_compilation_plan_authority). The former synthetic fixtures that
composed fabricated preserved bytes are exactly the forgeries the canonical
authority rule rejects.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import yaml

from mozaiksai.core.runtime.app.layout_registry import (
    MaterializerIdentifier,
    build_app_layout_registry,
)
from mozaiksai.core.semantics.binding import (
    RendererSelection,
    build_implementation_binding,
)
from mozaiksai.core.semantics.compilation_plan import (
    PlanDisposition,
    derive_compilation_plan,
    plan_regeneration_closure,
)
from mozaiksai.core.semantics.materialization import (
    MaterializedBundle,
)
from mozaiksai.core.semantics.plan_authority import (
    build_compilation_plan_authority_inputs,
)
from mozaiksai.core.semantics.refs import (
    CompilationPlanRef,
    PlanUnitRef,
    SemanticGraphRef,
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
from tests.test_app_family_materialization_b2a import _extended_fixture

_ROOT = Path(__file__).resolve().parents[1]

_KEPT_TEMPLATES = frozenset(
    {
        "ui/pages/{page_id}.yaml",
        "app.json",
        "ui/route_manifest.json",
        "config/integrations.yaml",
        "security/secrets.yaml",
        "modules/{module_id}/backend/{helper_id}.py",
    }
)


class ReducedLayoutRegistry:
    """Internally consistent authority subset of the canonical registry.

    Rows keep their canonical content; dependencies on families outside the
    subset are trimmed so the snapshot's dependency closure holds. Honest
    derivation over this authority is gap-free, which is what the 5B
    zero-gap composition contract requires.
    """

    def __init__(self) -> None:
        source = build_app_layout_registry(())
        self.schema_version = source.schema_version
        kept_kinds = {
            family.kind
            for family in source.ordered_families()
            if family.path_template in _KEPT_TEMPLATES
        }
        rows = []
        for family in source.ordered_families():
            if family.path_template not in _KEPT_TEMPLATES:
                continue
            trimmed = tuple(
                dependency
                for dependency in family.dependency_families
                if dependency in kept_kinds
            )
            rows.append(family.model_copy(update={"dependency_families": trimmed}))
        self._rows = tuple(rows)

    def ordered_families(self):
        return self._rows

    @property
    def families(self):
        return self._rows


def reduced_registry() -> ReducedLayoutRegistry:
    return ReducedLayoutRegistry()


def structured_configs() -> dict:
    return {
        "AppGenerator": yaml.safe_load(
            (
                _ROOT / "factory_app/workflows/AppGenerator/structured_outputs.yaml"
            ).read_text(encoding="utf-8")
        )
    }


def _binding(graph):
    from mozaiksai.core.semantics.app_config_materialization import (
        APP_CONFIG_FAMILIES,
        APP_CONFIG_RENDERER_IMPLEMENTATION_ID,
        APP_CONFIG_RENDERER_IMPLEMENTATION_VERSION,
    )

    return build_implementation_binding(
        binding_id="slice5b_binding",
        version=1,
        scope=graph.scope,
        semantic_graph_ref=SemanticGraphRef(
            subject_id=graph.graph_id,
            subject_version=graph.version,
            content_digest=graph.graph_digest,
            scope=graph.scope,
        ),
        renderer_selections=(
            RendererSelection(
                materializer_id=MaterializerIdentifier.PAGE_SCHEMA_EXECUTOR,
                implementation_id="deterministic_page_schema_renderer",
                implementation_version="1",
                artifact_families=("app_ui_page_schema",),
            ),
            RendererSelection(
                materializer_id=MaterializerIdentifier.APP_CONFIG_EXECUTOR,
                implementation_id=APP_CONFIG_RENDERER_IMPLEMENTATION_ID,
                implementation_version=APP_CONFIG_RENDERER_IMPLEMENTATION_VERSION,
                artifact_families=tuple(sorted(APP_CONFIG_FAMILIES)),
            ),
        ),
    )


def _bundle_for(plan, graph, payloads, _authority_inputs) -> MaterializedBundle:
    """Render the plan's renderer-ready units through the real gated path.

    ``materialize_plan`` has no execution path for AGENT_AUTHOR units (their
    bytes arrive as validated assignment results at composition), so the
    bundle for an agent-bearing plan is assembled from the fully gated render
    of the same canonical plan restricted to its composable render outputs â€”
    which is exactly what materialization itself produces for those units.
    """
    from mozaiksai.core.semantics.materialization import (
        _materialize_unit,
        resolve_app_config_renderer_selection,
    )

    payload_by_node = {p.node_id: p for p in payloads}
    binding = _binding(graph)
    selection = resolve_app_config_renderer_selection(
        binding, graph=graph, layout_registry=reduced_registry()
    )
    outputs = []
    for unit in plan.units:
        if unit.disposition is not PlanDisposition.RENDER:
            continue
        _materialize_unit(
            unit,
            payload_by_node=payload_by_node,
            app_config_selection=selection,
            workflow_interface_selection=None,
            preserved_by_unit={},
            bundle_outputs=outputs,
            external=[],
            inapplicable=[],
            unsupplied=[],
            input_only=[],
            deferred=[],
        )
    return MaterializedBundle(
        plan_digest=plan.plan_digest,
        outputs=tuple(sorted(outputs, key=lambda o: o.path)),
        external_handoff_units=(),
        inapplicable_units=(),
        unsupplied_preserved_units=(),
        input_only_units=(),
        instance_scope_deferred_units=(),
        gap_count=0,
    )


@lru_cache(maxsize=1)
def _derived_products() -> dict[str, object]:
    """Cache only the deterministic derivation products; resolvers are live
    registries and must be built fresh per fixture call so tests can never
    pollute one another through shared registration state."""
    return _build_derived_products()


def composition_fixture() -> dict[str, object]:
    products = _derived_products()
    resolver = SemanticReferenceResolver()
    for payload in products["payloads"]:
        resolver.register_semantic_payload(payload)
    resolver.register_semantic_graph_v2(products["graph"])
    resolver.register_compilation_plan(products["base"])
    resolver.register_compilation_plan(products["successor"])
    resolver.register_compilation_plan_authority_inputs(
        products["base_authority_inputs"]
    )
    resolver.register_compilation_plan_authority_inputs(
        products["authority_inputs"]
    )
    return {**products, "resolver": resolver}


def _build_derived_products() -> dict[str, object]:
    """Canonical genesis + refinement composition scenario.

    ``base`` (v1) and ``successor`` (v2, one integration-purpose change) are
    both genuinely derived and authority-validated; the closure between them
    yields REUSED coverage for the unaffected units and fresh RENDERED /
    AGENT_AUTHORED coverage for the rest.
    """
    registry = reduced_registry()
    configs = structured_configs()

    base_graph, base_payloads = _extended_fixture()
    base_plan = derive_compilation_plan(
        graph=base_graph,
        payloads=base_payloads,
        registry=registry,
        structured_output_configs=configs,
    )
    base_authority = build_compilation_plan_authority_inputs(
        graph=base_graph,
        payloads=base_payloads,
        registry=registry,
        structured_output_configs=configs,
    )

    successor_source_graph, successor_payloads = _extended_fixture(
        home_title="Console"
    )
    from mozaiksai.core.semantics.graph import build_semantic_graph_v2

    successor_graph = build_semantic_graph_v2(
        graph_id=successor_source_graph.graph_id,
        version=2,
        scope=successor_source_graph.scope,
        nodes=successor_source_graph.nodes,
        edges=successor_source_graph.edges,
        namespace_grants=successor_source_graph.namespace_grants,
    )
    successor_plan = derive_compilation_plan(
        graph=successor_graph,
        payloads=successor_payloads,
        registry=registry,
        structured_output_configs=configs,
    )
    successor_authority = build_compilation_plan_authority_inputs(
        graph=successor_graph,
        payloads=successor_payloads,
        registry=registry,
        structured_output_configs=configs,
    )

    assert not base_plan.gaps and not successor_plan.gaps
    closure = plan_regeneration_closure(base_plan, successor_plan)

    resolver = SemanticReferenceResolver()
    for payload in successor_payloads:
        resolver.register_semantic_payload(payload)
    resolver.register_semantic_graph_v2(successor_graph)
    resolver.register_compilation_plan(successor_plan)

    agent = next(
        unit
        for unit in successor_plan.units
        if unit.disposition is PlanDisposition.AGENT_AUTHOR
    )
    plan_ref = CompilationPlanRef(
        subject_id=successor_plan.graph_id,
        subject_version=successor_plan.graph_version,
        content_digest=successor_plan.plan_digest,
        scope=successor_plan.scope,
    )
    unit_ref = PlanUnitRef(
        compilation_plan_ref=plan_ref,
        unit_id=agent.unit_id,
        unit_digest=agent.unit_digest,
    )
    payload_by_id = {p.node_id: p for p in successor_payloads}
    source_refs = tuple(
        SemanticPayloadRef(
            node_id=source.node_id,
            payload_kind=payload_by_id[source.node_id].payload_kind.value,
            payload_version=payload_by_id[source.node_id].payload_version,
            content_digest=source.payload_digest,
            scope=successor_plan.scope,
        )
        for source in agent.sources
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
                    base_revision_digest=None,
                ),
            )
        ),
        resolver=resolver,
        authority_inputs=successor_authority,
    )
    helper_source = "def report_hook():\n    return None\n"
    helper_path = agent.outputs[0].path.replace("{module_id}", "reports").replace(
        "{helper_id}", "report_hook"
    )
    result = build_assignment_artifact_result(
        assignment=assignments.ordered_assignments[0],
        structured_output={
            "assignment_kind": "module_helper_implementation",
            "module_id": "reports",
            "helper_id": "report_hook",
            "helper_source": helper_source,
        },
        artifacts={helper_path: helper_source},
        exact_model_ids=frozenset(row.structured_output_model_id for row in successor_authority.assignment_contract_registry.descriptors), structured_output_configs=configs,
        validator_runner=lambda _validator, files: bool(files),
    )

    materialized = _bundle_for(
        successor_plan, successor_graph, successor_payloads, successor_authority
    )
    base_bundle = _bundle_for(base_plan, base_graph, base_payloads, base_authority)
    import hashlib as _hashlib

    from mozaiksai.core.semantics.materialization import MaterializedOutput

    agent_bytes = helper_source.encode("utf-8")
    base_outputs = tuple(
        output
        for output in base_bundle.outputs
        if output.unit_id in set(closure.reusable)
    ) + (
        # the agent-authored artifact as it existed in the base revision, so
        # a reusable AGENT_AUTHOR unit has its exact base bytes available
        MaterializedOutput(
            unit_id=agent.unit_id,
            path_scope=agent.outputs[0].path_scope,
            path=helper_path,
            content=agent_bytes,
            origin="reused",
            content_digest=_hashlib.sha256(agent_bytes).hexdigest(),
        ),
    )

    return {
        "registry": registry,
        "configs": configs,
        "graph": successor_graph,
        "payloads": successor_payloads,
        "base_graph": base_graph,
        "base_payloads": base_payloads,
        "base": base_plan,
        "base_authority_inputs": base_authority,
        "successor": successor_plan,
        "authority_inputs": successor_authority,
        "closure": closure,
        "assignments": assignments,
        "result": result,
        "materialized": materialized,
        "base_outputs": base_outputs,
        "base_revision_digest": None,
    }


def refinement_execution(fixture, base_revision_digest: str):
    """Recompile the agent assignment and its result pinned to a base
    revision digest â€” the canonical way to enter refinement composition."""
    plan = fixture["successor"]
    resolver = fixture["resolver"]
    configs = fixture["configs"]
    agent = next(
        unit
        for unit in plan.units
        if unit.disposition is PlanDisposition.AGENT_AUTHOR
    )
    plan_ref = CompilationPlanRef(
        subject_id=plan.graph_id,
        subject_version=plan.graph_version,
        content_digest=plan.plan_digest,
        scope=plan.scope,
    )
    unit_ref = PlanUnitRef(
        compilation_plan_ref=plan_ref,
        unit_id=agent.unit_id,
        unit_digest=agent.unit_digest,
    )
    payload_by_id = {p.node_id: p for p in fixture["payloads"]}
    source_refs = tuple(
        SemanticPayloadRef(
            node_id=source.node_id,
            payload_kind=payload_by_id[source.node_id].payload_kind.value,
            payload_version=payload_by_id[source.node_id].payload_version,
            content_digest=source.payload_digest,
            scope=plan.scope,
        )
        for source in agent.sources
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
                    base_revision_digest=base_revision_digest,
                ),
            )
        ),
        resolver=resolver,
        authority_inputs=fixture["authority_inputs"],
    )
    helper_source = "def report_hook():" + chr(10) + "    return None" + chr(10)
    helper_path = agent.outputs[0].path.replace(
        "{module_id}", "reports"
    ).replace("{helper_id}", "report_hook")
    result = build_assignment_artifact_result(
        assignment=assignments.ordered_assignments[0],
        structured_output={
            "assignment_kind": "module_helper_implementation",
            "module_id": "reports",
            "helper_id": "report_hook",
            "helper_source": helper_source,
        },
        artifacts={helper_path: helper_source},
        exact_model_ids=frozenset(row.structured_output_model_id for row in fixture["authority_inputs"].assignment_contract_registry.descriptors), structured_output_configs=configs,
        validator_runner=lambda _validator, files: bool(files),
    )
    return assignments, result


def empty_assignment_set() -> CompiledAssignmentSet:
    from mozaiksai.core.workflow.structured_output_contracts import stable_digest

    return CompiledAssignmentSet(
        ordered_assignments=(), assignment_set_digest=stable_digest([])
    )
