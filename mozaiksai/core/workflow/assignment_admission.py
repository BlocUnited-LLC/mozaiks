"""Pure offline resolution of a compiled assignment to one Factory participant."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from mozaiksai.core.workflow.declarative.contracts import (
    AgentsConfig,
    StructuredOutputsConfig,
)
from mozaiksai.core.workflow.plan_assignment_compiler import CompiledAssignment
from mozaiksai.core.workflow.structured_output_contracts import (
    resolve_structured_output_contract_ref,
)


@dataclass(frozen=True, slots=True)
class ResolvedAssignmentAdmission:
    """Ephemeral declarative participant selection; never canonical identity."""

    assignment: CompiledAssignment
    logical_participant_key: str


def resolve_assignment_admission(
    assignment: CompiledAssignment,
    *,
    structured_output_configs: Mapping[str, Any],
    workflow_agent_configs: Mapping[str, Any],
) -> ResolvedAssignmentAdmission:
    """Resolve exactly one Factory participant without creating runtime identity."""

    verified_assignment = CompiledAssignment.model_validate(
        assignment.model_dump(mode="json")
    )
    contract_ref = verified_assignment.required_structured_output_ref
    resolve_structured_output_contract_ref(
        contract_ref, configs=structured_output_configs
    )

    raw_structured = structured_output_configs.get(contract_ref.workflow_name)
    if raw_structured is None:
        raise ValueError(
            f"workflow mismatch: no structured-output config for "
            f"{contract_ref.workflow_name!r}"
        )
    structured = StructuredOutputsConfig.model_validate(raw_structured)
    matches = tuple(
        sorted(
            participant
            for participant, model_id in structured.registry.items()
            if model_id == contract_ref.model_id
        )
    )
    if not matches:
        raise ValueError(
            f"structured-output model {contract_ref.model_id!r} has no Factory participant"
        )
    if len(matches) != 1:
        raise ValueError(
            f"structured-output model {contract_ref.model_id!r} has ambiguous Factory "
            f"participants: {list(matches)}"
        )

    raw_agents = workflow_agent_configs.get(contract_ref.workflow_name)
    if raw_agents is None:
        raise ValueError(
            f"workflow mismatch: no agent declarations for {contract_ref.workflow_name!r}"
        )
    agents = AgentsConfig.model_validate(raw_agents)
    declared = {agent.name: agent for agent in agents.agents}
    participant = matches[0]
    declaration = declared.get(participant)
    if declaration is None:
        raise ValueError(
            f"Factory participant {participant!r} is not declared by workflow "
            f"{contract_ref.workflow_name!r}"
        )
    if not declaration.structured_outputs_required:
        raise ValueError(
            f"Factory participant {participant!r} does not require structured output"
        )
    return ResolvedAssignmentAdmission(
        assignment=verified_assignment,
        logical_participant_key=participant,
    )


__all__ = ["ResolvedAssignmentAdmission", "resolve_assignment_admission"]
