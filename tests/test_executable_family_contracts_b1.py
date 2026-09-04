"""ADR 0007 Slice 5D-0B1 executable-family contract proofs."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from mozaiksai.core.runtime.app.layout_registry import (
    ArtifactDisposition,
    PathScope,
    build_app_layout_registry,
)
from mozaiksai.core.semantics.compilation_plan import (
    CompilationScopeSelection,
    PlanDisposition,
    derive_compilation_plan,
)
from mozaiksai.core.semantics.graph import (
    SemanticEdge,
    SemanticEdgeKind,
    SemanticNodeV2,
    build_semantic_graph_v2,
)
from mozaiksai.core.semantics.payloads import (
    ArtifactDeclarationPayload,
    ArtifactDeclarationRole,
    ModulePayload,
    SemanticPayloadError,
    build_semantic_payload,
    semantic_payload_ref,
    validate_semantic_graph_v2_payload_closure,
)
from mozaiksai.core.semantics.refs import ExecutionAccessScopeRef
from mozaiksai.core.workflow.assignment_artifacts import build_assignment_artifact_result
from mozaiksai.core.workflow.assignment_kinds import (
    APP_BUILD_ASSIGNMENT_KINDS,
    ASSIGNMENT_CONTRACT_DESCRIPTORS,
    COMPILER_ASSIGNMENT_KINDS,
    AssignmentKind,
)
from mozaiksai.core.workflow.plan_assignment_compiler import (
    ApprovedPlan,
    compile_approved_plan,
)
from mozaiksai.core.workflow.structured_output_contracts import (
    build_structured_output_contract_ref,
    resolve_structured_output_contract_ref,
)
from tests.test_compilation_plan import _corpus_graph
from tests.test_plan_assignment_compiler import _AUTHORITY
from tests.test_plan_assignment_compiler import _fixture as assignment_fixture
from tests.test_semantic_payload_graph_v2 import _application_payload

ROOT = Path(__file__).resolve().parents[1]
SCOPE = ExecutionAccessScopeRef(tenant_id="tenant1", workspace_id="ws1")
CONFIGS = {
    workflow: yaml.safe_load(
        (ROOT / f"factory_app/workflows/{workflow}/structured_outputs.yaml").read_text(
            encoding="utf-8"
        )
    )
    for workflow in ("AppGenerator", "AgentGenerator")
}


def _module_declaration_graph(
    *, owner_node_id: str = "mozaiks.module.reports", include_owner_edge: bool = True
):
    application = _application_payload(
        node_id="mozaiks.application.test", application_id="test-app"
    )
    module = build_semantic_payload(
        ModulePayload,
        node_id="mozaiks.module.reports",
        payload_version=1,
        scope=SCOPE,
        module_id="reports",
        description=None,
        closed_artifact_roles=(ArtifactDeclarationRole.MODULE_HELPER,),
    )
    declaration = build_semantic_payload(
        ArtifactDeclarationPayload,
        node_id="mozaiks.artifact.report_hook",
        payload_version=1,
        scope=SCOPE,
        declaration_id="report_hook",
        artifact_role=ArtifactDeclarationRole.MODULE_HELPER,
        owner_node_id=owner_node_id,
    )
    payloads = (application, module, declaration)
    edges = (
        (
            SemanticEdge(
                kind=SemanticEdgeKind.OWNS,
                source_node_id=owner_node_id,
                target_node_id=declaration.node_id,
            ),
        )
        if include_owner_edge
        else ()
    )
    graph = build_semantic_graph_v2(
        graph_id="b1-relations",
        version=1,
        scope=SCOPE,
        nodes=tuple(
            SemanticNodeV2(
                node_id=payload.node_id,
                kind=payload.payload_kind,
                payload_ref=semantic_payload_ref(payload),
            )
            for payload in payloads
        ),
        edges=edges,
    )
    return graph, payloads


def test_compiler_assignment_vocabulary_and_locator_are_exhaustive() -> None:
    assert set(ASSIGNMENT_CONTRACT_DESCRIPTORS) == set(COMPILER_ASSIGNMENT_KINDS)
    assert not COMPILER_ASSIGNMENT_KINDS & APP_BUILD_ASSIGNMENT_KINDS
    assert "integration" not in AssignmentKind._value2member_map_
    assert "validation" not in AssignmentKind._value2member_map_
    for kind, descriptor in ASSIGNMENT_CONTRACT_DESCRIPTORS.items():
        assert descriptor.assignment_kind is kind
        assert descriptor.owned_artifact_families
        assert len(descriptor.validator_ids) == 1
        ref = build_structured_output_contract_ref(
            workflow_name=descriptor.workflow_name,
            model_id=descriptor.structured_output_model_id,
            configs=CONFIGS,
        )
        assert resolve_structured_output_contract_ref(ref, configs=CONFIGS)


def test_exact_output_models_reject_unknown_fields_and_arbitrary_paths() -> None:
    descriptor = ASSIGNMENT_CONTRACT_DESCRIPTORS[
        AssignmentKind.MODULE_HELPER_IMPLEMENTATION
    ]
    ref = build_structured_output_contract_ref(
        workflow_name=descriptor.workflow_name,
        model_id=descriptor.structured_output_model_id,
        configs=CONFIGS,
    )
    model = resolve_structured_output_contract_ref(ref, configs=CONFIGS)
    valid = {
        "assignment_kind": "module_helper_implementation",
        "module_id": "reports",
        "helper_id": "report_hook",
        "helper_source": "def report_hook():\n    return None\n",
    }
    assert model.model_validate(valid)
    with pytest.raises(ValidationError, match="extra"):
        model.model_validate({**valid, "path": "elsewhere.py"})
    assert not {
        "path",
        "paths",
        "generated_files",
        "files",
        "runtime_id",
    } & set(model.model_fields)


def test_artifact_result_rejects_semantic_identity_substitution() -> None:
    config, _plan, _unit, resolver, spec = assignment_fixture()
    assignment = compile_approved_plan(
        ApprovedPlan(assignments=(spec,)),
        resolver=resolver,
        authority_inputs=_AUTHORITY["inputs"],
        structured_output_configs={"AppGenerator": config},
    ).ordered_assignments[0]
    assert dict(assignment.semantic_identity_bindings) == {
        "module_id": "reports",
        "helper_id": "report_hook",
    }
    with pytest.raises(ValueError, match="semantic assignment identity"):
        build_assignment_artifact_result(
            assignment=assignment,
            structured_output={
                "assignment_kind": "module_helper_implementation",
                "module_id": "foreign_module",
                "helper_id": "report_hook",
                "helper_source": "def report_hook():\n    return None\n",
            },
            artifacts={
                assignment.owned_paths[0]: "def report_hook():\n    return None\n"
            },
            structured_output_configs={"AppGenerator": config},
            validator_runner=lambda _validator, _files: True,
        )


def test_scope_variants_and_integration_aliases_are_exclusive() -> None:
    registry = build_app_layout_registry()
    templates = {family.path_template for family in registry.families}
    assert "config/integrations.yaml" in templates
    assert "config/integrations.json" not in templates
    assert "config/integrations.yml" not in templates

    graph, payloads = _corpus_graph()
    app_root = derive_compilation_plan(
        graph=graph,
        payloads=payloads,
        registry=registry,
        scope_selection=CompilationScopeSelection(
            app_manifest_scope=PathScope.APP_BUNDLE_ROOT,
            module_scope=PathScope.APP_BUNDLE_ROOT,
            workflow_manifest_scope=PathScope.WORKFLOW_RELATIVE,
        ),
    )
    workspace = derive_compilation_plan(
        graph=graph,
        payloads=payloads,
        registry=registry,
        scope_selection=CompilationScopeSelection(
            app_manifest_scope=PathScope.WORKSPACE_ROOT,
            module_scope=PathScope.MODULE_RELATIVE,
            workflow_manifest_scope=PathScope.WORKSPACE_ROOT,
        ),
    )
    assert sum(
        unit.family_kind == "app_manifest" and "/scope_inactive/" in unit.unit_id
        for unit in app_root.units
    ) == 1
    assert sum(
        unit.family_kind == "app_manifest" and "/scope_inactive/" in unit.unit_id
        for unit in workspace.units
    ) == 1
    assert app_root.plan_digest != workspace.plan_digest


def test_gap_report_is_literal_and_latent_diagnostics_are_separate() -> None:
    graph, payloads = _corpus_graph()
    plan = derive_compilation_plan(
        graph=graph,
        payloads=payloads,
        registry=build_app_layout_registry(),
        structured_output_configs=CONFIGS,
    )
    report = plan.gap_report()
    assert report.emitted_gaps == plan.gaps
    assert report.latent_gaps == ()
    assert report.composite_diagnostics == plan.gaps


def test_greenfield_dispositions_are_declared_without_false_preservation() -> None:
    registry = build_app_layout_registry()
    dispositions = {family.disposition for family in registry.families}
    assert dispositions == {
        ArtifactDisposition.RENDER,
        ArtifactDisposition.AGENT_AUTHOR,
        ArtifactDisposition.INPUT_ONLY,
        ArtifactDisposition.EXTERNAL_HANDOFF,
        ArtifactDisposition.INAPPLICABLE,
    }
    graph, payloads = _corpus_graph()
    plan = derive_compilation_plan(
        graph=graph,
        payloads=payloads,
        registry=registry,
        structured_output_configs=CONFIGS,
    )
    assert not any(
        unit.disposition is PlanDisposition.PRESERVE_UNOWNED for unit in plan.units
    )


def test_artifact_relation_requires_typed_owner_edge_and_rejects_foreign_owner() -> None:
    graph, payloads = _module_declaration_graph()
    validate_semantic_graph_v2_payload_closure(graph, payloads)

    missing_graph, missing_payloads = _module_declaration_graph(include_owner_edge=False)
    with pytest.raises(SemanticPayloadError, match="lacks its typed owner edge"):
        validate_semantic_graph_v2_payload_closure(missing_graph, missing_payloads)

    foreign_graph, foreign_payloads = _module_declaration_graph(
        owner_node_id="mozaiks.application.test"
    )
    with pytest.raises(SemanticPayloadError, match="foreign or missing owner"):
        validate_semantic_graph_v2_payload_closure(foreign_graph, foreign_payloads)
