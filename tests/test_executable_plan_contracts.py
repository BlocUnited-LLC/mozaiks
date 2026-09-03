"""ADR 0007 Slice 5A executable-plan contract adversarial proofs."""

from __future__ import annotations

import ast
import copy
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from mozaiksai.core.runtime.app.layout_registry import (
    ArtifactKind,
    ValidatorIdentifier,
    build_app_layout_registry,
)
from mozaiksai.core.semantics.compilation_plan import (
    PlanDisposition,
    PlanGapCode,
    RegistryFamilyRow,
    derive_compilation_plan,
    plan_regeneration_closure,
    snapshot_layout_registry,
)
from mozaiksai.core.semantics.materialization import MaterializationError, _materialize_unit
from mozaiksai.core.semantics.refs import (
    CompilationPlanRef,
    PlanUnitRef,
    SemanticGraphRef,
)
from mozaiksai.core.semantics.resolver import (
    ReferenceResolutionError,
    SemanticReferenceResolver,
)
from mozaiksai.core.workflow.assignment_kinds import (
    ASSIGNMENT_CONTRACT_DESCRIPTORS,
    AssignmentKind,
)
from mozaiksai.core.workflow.structured_output_contracts import (
    StructuredOutputContractRef,
    build_structured_output_contract_ref,
    resolve_structured_output_contract_ref,
)
from tests.test_compilation_plan import _corpus_graph, _registry

ROOT = Path(__file__).resolve().parents[1]
APP_GENERATOR_CONFIG = yaml.safe_load(
    (ROOT / "factory_app/workflows/AppGenerator/structured_outputs.yaml").read_text(
        encoding="utf-8"
    )
)
AGENT_GENERATOR_CONFIG = yaml.safe_load(
    (ROOT / "factory_app/workflows/AgentGenerator/structured_outputs.yaml").read_text(
        encoding="utf-8"
    )
)


class _RegistryOverride:
    def __init__(self, *, path_template: str, **updates: object) -> None:
        source = build_app_layout_registry(())
        self.schema_version = source.schema_version
        families = []
        replaced = False
        for family in source.ordered_families():
            if family.path_template == path_template:
                family = family.model_copy(update=updates)
                replaced = True
            families.append(family)
        assert replaced, path_template
        self._families = tuple(families)

    def ordered_families(self):
        return self._families


def _derive_with_override(*, path_template: str, **updates: object):
    graph, payloads = _corpus_graph()
    return derive_compilation_plan(
        graph=graph,
        payloads=payloads,
        registry=_RegistryOverride(path_template=path_template, **updates),
        structured_output_configs={"AppGenerator": APP_GENERATOR_CONFIG},
    )


def test_registry_executable_metadata_is_canonical_identity() -> None:
    row = next(
        row
        for row in snapshot_layout_registry(_registry()).rows
        if row.path_template == "modules/{module_id}/module.yaml"
    )
    document = row.model_dump(mode="json")
    document["assignment_kinds"] = ["page_bundle", "module_contract"]
    reordered = copy.deepcopy(document)
    reordered["assignment_kinds"].reverse()
    first = RegistryFamilyRow.model_validate(document)
    second = RegistryFamilyRow.model_validate(reordered)
    assert first.assignment_kinds == second.assignment_kinds
    assert first.row_digest == second.row_digest
    assert first.row_digest != row.row_digest

    validator_mutation = row.model_copy(
        update={"validator": ValidatorIdentifier.GENERATED_APP_VALIDATOR}
    )
    assert validator_mutation.row_digest != row.row_digest

    duplicate = row.model_dump(mode="json")
    duplicate["assignment_kinds"] = ["module_contract", "module_contract"]
    with pytest.raises(ValidationError, match="unique"):
        RegistryFamilyRow.model_validate(duplicate)


def test_registry_metadata_mutation_propagates_to_snapshot_and_plan_identity() -> None:
    graph, payloads = _corpus_graph()
    base = derive_compilation_plan(
        graph=graph,
        payloads=payloads,
        registry=_registry(),
        structured_output_configs={"AppGenerator": APP_GENERATOR_CONFIG},
    )
    assignment_changed = derive_compilation_plan(
        graph=graph,
        payloads=payloads,
        registry=_RegistryOverride(
            path_template="modules/{module_id}/module.yaml",
            assignment_kinds=(AssignmentKind.PAGE_BUNDLE,),
        ),
        structured_output_configs={"AppGenerator": APP_GENERATOR_CONFIG},
    )
    validator_changed = derive_compilation_plan(
        graph=graph,
        payloads=payloads,
        registry=_RegistryOverride(
            path_template="modules/{module_id}/module.yaml",
            validator=ValidatorIdentifier.GENERATED_APP_VALIDATOR,
        ),
        structured_output_configs={"AppGenerator": APP_GENERATOR_CONFIG},
    )
    assert len({base.registry_digest, assignment_changed.registry_digest, validator_changed.registry_digest}) == 3
    assert len({base.plan_digest, assignment_changed.plan_digest, validator_changed.plan_digest}) == 3

    forward = _RegistryOverride(
        path_template="services/integrations/{pack_id}_client.py",
        assignment_kinds=(AssignmentKind.API_SURFACE, AssignmentKind.SERVICE_FOUNDATION),
    )
    reverse = _RegistryOverride(
        path_template="services/integrations/{pack_id}_client.py",
        assignment_kinds=(AssignmentKind.SERVICE_FOUNDATION, AssignmentKind.API_SURFACE),
    )
    assert snapshot_layout_registry(forward).snapshot_digest == snapshot_layout_registry(
        reverse
    ).snapshot_digest
    forward_plan = derive_compilation_plan(graph=graph, payloads=payloads, registry=forward)
    reverse_plan = derive_compilation_plan(graph=graph, payloads=payloads, registry=reverse)
    assert forward_plan.plan_digest == reverse_plan.plan_digest


def test_structured_output_schema_identity_invalidates_agent_author_reuse() -> None:
    graph, payloads = _corpus_graph()

    def _derive(config: dict[str, object]):
        return derive_compilation_plan(
            graph=graph,
            payloads=payloads,
            registry=_registry(),
            structured_output_configs={"AppGenerator": config},
        )

    def _target(plan):
        return next(
            unit
            for unit in plan.units
            if unit.disposition is PlanDisposition.AGENT_AUTHOR
            and unit.required_structured_output_ref is not None
            and unit.required_structured_output_ref.model_id
            == "ModuleHelperImplementationOutput"
        )

    base = _derive(APP_GENERATOR_CONFIG)
    base_target = _target(base)

    unchanged = _derive(copy.deepcopy(APP_GENERATOR_CONFIG))
    unchanged_closure = plan_regeneration_closure(base, unchanged)
    assert base_target.unit_id in unchanged_closure.reusable

    reordered = copy.deepcopy(APP_GENERATOR_CONFIG)
    reordered["models"] = dict(reversed(tuple(reordered["models"].items())))
    reordered_plan = _derive(reordered)
    reordered_target = _target(reordered_plan)
    assert reordered_target.unit_digest == base_target.unit_digest
    assert base_target.unit_id in plan_regeneration_closure(
        base, reordered_plan
    ).reusable

    unrelated = copy.deepcopy(APP_GENERATOR_CONFIG)
    unrelated_field = next(
        iter(unrelated["models"]["DownloadRequest"]["fields"].values())
    )
    unrelated_field["description"] = "Unrelated contract mutation."
    unrelated_plan = _derive(unrelated)
    unrelated_target = _target(unrelated_plan)
    assert unrelated_target.unit_digest == base_target.unit_digest
    assert base_target.unit_id in plan_regeneration_closure(
        base, unrelated_plan
    ).reusable

    changed = copy.deepcopy(APP_GENERATOR_CONFIG)
    changed["models"]["ModuleHelperImplementationOutput"]["fields"]["helper_source"][
        "description"
    ] += " Authoritative schema mutation."
    changed_plan = _derive(changed)
    changed_target = _target(changed_plan)
    changed_closure = plan_regeneration_closure(base, changed_plan)
    assert changed_target.unit_id == base_target.unit_id
    assert changed_target.unit_digest != base_target.unit_digest
    assert base_target.unit_id in changed_closure.affected
    assert base_target.unit_id not in changed_closure.reusable


@pytest.mark.parametrize(
    ("updates", "expected"),
    [
        ({"assignment_kinds": ()}, PlanGapCode.ASSIGNMENT_UNDECLARED),
        (
            {
                "assignment_kinds": (
                    AssignmentKind.MODULE_HELPER_IMPLEMENTATION,
                    AssignmentKind.CUSTOM_PAGE_IMPLEMENTATION,
                )
            },
            PlanGapCode.ASSIGNMENT_AMBIGUOUS,
        ),
        ({"validator": ValidatorIdentifier.NONE}, PlanGapCode.VALIDATOR_UNDECLARED),
        (
            {"assignment_kinds": (AssignmentKind.CUSTOM_PAGE_IMPLEMENTATION,)},
            PlanGapCode.OUTPUT_CONTRACT_UNRESOLVED,
        ),
    ],
)
def test_agent_author_metadata_failures_are_typed_gaps(
    updates: dict[str, object], expected: PlanGapCode
) -> None:
    plan = _derive_with_override(
        path_template="modules/{module_id}/backend/{helper_id}.py", **updates
    )
    assert any(
        gap.code is expected
        and gap.path_template == "modules/{module_id}/backend/{helper_id}.py"
        for gap in plan.gaps
    )
    assert not any(
        unit.disposition is PlanDisposition.AGENT_AUTHOR
        and unit.outputs
        and unit.outputs[0].path.endswith("/report_hook.py")
        for unit in plan.units
    )


def test_missing_output_contract_config_is_typed_gap() -> None:
    graph, payloads = _corpus_graph()
    plan = derive_compilation_plan(
        graph=graph,
        payloads=payloads,
        registry=_registry(),
    )
    assert any(
        gap.code is PlanGapCode.OUTPUT_CONTRACT_UNRESOLVED
        and gap.path_template == "modules/{module_id}/backend/{helper_id}.py"
        for gap in plan.gaps
    )


def test_disposition_precedence_never_promotes_other_authorities() -> None:
    graph, payloads = _corpus_graph()
    plan = derive_compilation_plan(
        graph=graph,
        payloads=payloads,
        registry=_registry(),
        structured_output_configs={"AppGenerator": APP_GENERATOR_CONFIG},
    )
    assert any(unit.disposition is PlanDisposition.AGENT_AUTHOR for unit in plan.units)
    assert all(
        unit.disposition is PlanDisposition.RENDER
        for unit in plan.units
        if unit.family_kind == ArtifactKind.APP_UI_PAGE_SCHEMA.value
    )
    assert not any(
        unit.disposition is PlanDisposition.PRESERVE_UNOWNED for unit in plan.units
    )
    assert all(
        unit.disposition is PlanDisposition.EXTERNAL_HANDOFF
        for unit in plan.units
        if unit.outputs and unit.outputs[0].path_scope == "deployment_derived"
    )
    assert not any(
        unit.disposition is PlanDisposition.AGENT_AUTHOR
        for unit in plan.units
        if unit.materializer == "capability_pack_materializer"
    )


def test_graph_and_plan_may_share_subject_identity_and_version() -> None:
    graph, payloads = _corpus_graph()
    plan = derive_compilation_plan(graph=graph, payloads=payloads, registry=_registry())
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
    plan_ref = CompilationPlanRef(
        subject_id=plan.graph_id,
        subject_version=plan.graph_version,
        content_digest=plan.plan_digest,
        scope=plan.scope,
    )
    assert resolver.resolve(graph_ref, requesting_scope=graph.scope).graph_digest == graph.graph_digest
    assert resolver.resolve(plan_ref, requesting_scope=graph.scope).plan_digest == plan.plan_digest


def test_plan_unit_ref_rejects_wrong_unit_and_runtime_shaped_identity() -> None:
    graph, payloads = _corpus_graph()
    plan = derive_compilation_plan(graph=graph, payloads=payloads, registry=_registry())
    resolver = SemanticReferenceResolver()
    resolver.register_compilation_plan(plan)
    ref = PlanUnitRef(
        compilation_plan_ref=CompilationPlanRef(
            subject_id=plan.graph_id,
            subject_version=plan.graph_version,
            content_digest=plan.plan_digest,
            scope=plan.scope,
        ),
        unit_id=plan.units[0].unit_id,
        unit_digest=plan.units[0].unit_digest,
    )
    wrong = ref.model_copy(update={"unit_id": "missing/unit"})
    with pytest.raises(ReferenceResolutionError, match="cold resolution"):
        resolver.resolve_plan_unit(wrong, requesting_scope=plan.scope)
    document = ref.model_dump(mode="json")
    document["unit_id"] = "AG2-Agent/Channel"
    with pytest.raises(ValidationError, match="normalized"):
        PlanUnitRef.model_validate(document)


def test_structured_output_ref_is_cold_and_schema_pinned() -> None:
    ref = build_structured_output_contract_ref(
        workflow_name="AppGenerator",
        model_id="ModuleHelperImplementationOutput",
        configs={"AppGenerator": APP_GENERATOR_CONFIG},
    )
    assert resolve_structured_output_contract_ref(
        ref, configs={"AppGenerator": APP_GENERATOR_CONFIG}
    )

    changed = copy.deepcopy(APP_GENERATOR_CONFIG)
    changed["models"]["ModuleHelperImplementationOutput"]["fields"]["helper_source"]["description"] += (
        " Canonical mutation."
    )
    with pytest.raises(ValueError, match="schema digest mismatch"):
        resolve_structured_output_contract_ref(ref, configs={"AppGenerator": changed})
    changed_ref = build_structured_output_contract_ref(
        workflow_name="AppGenerator",
        model_id="ModuleHelperImplementationOutput",
        configs={"AppGenerator": changed},
    )
    assert changed_ref.schema_digest != ref.schema_digest

    with pytest.raises(ValueError, match="unknown structured-output model"):
        build_structured_output_contract_ref(
            workflow_name="AppGenerator",
            model_id="UnknownModel",
            configs={"AppGenerator": APP_GENERATOR_CONFIG},
        )
    with pytest.raises(ValueError, match="unknown structured-output workflow"):
        build_structured_output_contract_ref(
            workflow_name="UnknownWorkflow",
            model_id="ModuleHelperImplementationOutput",
            configs={"AppGenerator": APP_GENERATOR_CONFIG},
        )
    with pytest.raises(ValidationError, match="canonical workflow/model identifier"):
        StructuredOutputContractRef(
            workflow_name="module.path:Workflow",
            model_id="ConfigMiddlewareOutput",
            schema_digest="0" * 64,
        )


def test_locator_contains_only_proven_contracts() -> None:
    assert set(ASSIGNMENT_CONTRACT_DESCRIPTORS) == {
        AssignmentKind.MODULE_BACKEND_IMPLEMENTATION,
        AssignmentKind.INTEGRATION_ADAPTER_IMPLEMENTATION,
        AssignmentKind.APP_ROUTE_EXTENSION_IMPLEMENTATION,
        AssignmentKind.CUSTOM_PAGE_IMPLEMENTATION,
        AssignmentKind.WORKFLOW_PARTICIPANT_IMPLEMENTATION,
        AssignmentKind.WORKFLOW_STRUCTURED_MODELS_IMPLEMENTATION,
        AssignmentKind.WORKFLOW_TOOL_IMPLEMENTATION,
        AssignmentKind.WORKFLOW_UI_IMPLEMENTATION,
        AssignmentKind.MODULE_HELPER_IMPLEMENTATION,
        AssignmentKind.MODULE_ADMIN_PAGE_IMPLEMENTATION,
    }
    assert all(
        descriptor.structured_output_model_id
        in (
            APP_GENERATOR_CONFIG["models"]
            if descriptor.workflow_name == "AppGenerator"
            else AGENT_GENERATOR_CONFIG["models"]
        )
        for descriptor in ASSIGNMENT_CONTRACT_DESCRIPTORS.values()
    )


def test_agent_author_has_no_slice_4c_materialization_path() -> None:
    graph, payloads = _corpus_graph()
    plan = derive_compilation_plan(
        graph=graph,
        payloads=payloads,
        registry=_registry(),
        structured_output_configs={"AppGenerator": APP_GENERATOR_CONFIG},
    )
    unit = next(unit for unit in plan.units if unit.disposition is PlanDisposition.AGENT_AUTHOR)
    with pytest.raises(MaterializationError, match="no materialization path"):
        _materialize_unit(
            unit,
            payload_by_node={},
                app_config_selection=None,
            preserved_by_unit={},
            bundle_outputs=[],
            external=[],
            inapplicable=[],
            unsupplied=[],
            input_only=[],
            deferred=[],
        )


def test_slice_5a_substrate_is_production_unwired() -> None:
    allowed = {
        "mozaiksai/core/semantics/compilation_plan.py",
        "mozaiksai/core/semantics/composition_ledger.py",
        "mozaiksai/core/semantics/artifact_revision.py",
        "mozaiksai/core/semantics/__init__.py",
        "mozaiksai/core/semantics/materialization.py",
        "mozaiksai/core/semantics/resolver.py",
        "mozaiksai/core/semantics/refs.py",
        "mozaiksai/core/workflow/plan_assignment_compiler.py",
            "mozaiksai/core/workflow/assignment_artifacts.py",
            "mozaiksai/core/runtime/app/layout_registry.py",
    }
    tokens = ("AGENT_AUTHOR", "PlanUnitRef", "ApprovedAssignmentSpec", "compile_approved_plan")
    offenders: list[str] = []
    for root in (ROOT / "mozaiksai", ROOT / "factory_app"):
        for path in root.rglob("*"):
            if not path.is_file() or path.suffix not in {".py", ".yaml", ".yml"}:
                continue
            relative = path.relative_to(ROOT).as_posix()
            if relative in allowed:
                continue
            content = path.read_text(encoding="utf-8")
            if any(token in content for token in tokens):
                offenders.append(relative)
    assert offenders == []

    production_modules = [
        ROOT / "mozaiksai/core/workflow/task_batches.py",
        ROOT / "mozaiksai/core/workflow/orchestration_patterns.py",
    ]
    for path in production_modules:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imported = {
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module
        }
        assert "mozaiksai.core.workflow.plan_assignment_compiler" not in imported
