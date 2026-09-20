from __future__ import annotations

from typing import Any

from mozaiksai.core.runtime.app.layout_registry import ValidatorIdentifier
from mozaiksai.core.semantics.refs import (
    CompilationPlanRef,
    ExecutionAccessScopeRef,
    PlanUnitRef,
)
from mozaiksai.core.workflow.assignment_kinds import AssignmentKind
from mozaiksai.core.workflow.plan_assignment_compiler import CompiledAssignment
from mozaiksai.core.workflow.structured_output_contracts import (
    build_structured_output_contract_ref,
    stable_digest,
)


def structured_config(
    *, registry: dict[str, str | None] | None = None
) -> dict[str, Any]:
    return {
        "schema_version": "mozaiks.structured_outputs.v1",
        "models": {
            "ArtifactOutput": {
                "type": "model",
                "fields": {"message": {"type": "str"}},
            }
        },
        "registry": registry if registry is not None else {"ArtifactAuthor": "ArtifactOutput"},
    }


def agent_config(*, participant: str = "ArtifactAuthor") -> dict[str, Any]:
    return {
        "agents": [
            {
                "name": participant,
                "system_message": "Produce one bounded artifact result.",
                "structured_outputs_required": True,
            }
        ]
    }


def compiled_assignment(
    *,
    paths: tuple[str, ...] = ("data/contract.json",),
    base_revision_digest: str | None = None,
    validator: ValidatorIdentifier = ValidatorIdentifier.DATA_CONTRACT_LOADER,
) -> tuple[CompiledAssignment, dict[str, Any]]:
    config = structured_config()
    ref = build_structured_output_contract_ref(
        workflow_name="AppGenerator",
        model_id="ArtifactOutput",
        exact_model_ids=frozenset(), configs={"AppGenerator": config},
    )
    scope = ExecutionAccessScopeRef(tenant_id="tenant", workspace_id="workspace")
    plan_ref = CompilationPlanRef(
        subject_id="graph",
        subject_version=1,
        content_digest="a" * 64,
        scope=scope,
    )
    unit_ref = PlanUnitRef(
        compilation_plan_ref=plan_ref,
        unit_id="artifact/unit",
        unit_digest="b" * 64,
    )
    payload: dict[str, Any] = {
        "plan_unit_ref": unit_ref,
        "assignment_kind": AssignmentKind.PERSISTENCE_CONTRACT,
        "owned_paths": paths,
        "depends_on_unit_refs": (),
        "dependency_context_refs": (),
        "required_structured_output_ref": ref,
        "required_validators": (validator,),
        "assignment_retry_limit": 1,
        "base_revision_digest": base_revision_digest,
    }
    digest = stable_digest(payload)
    return (
        CompiledAssignment(
            assignment_id=f"wa_{digest[:24]}",
            assignment_digest=digest,
            **payload,
        ),
        config,
    )
