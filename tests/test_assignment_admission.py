from __future__ import annotations

from dataclasses import asdict, is_dataclass

import pytest
from pydantic import ValidationError

from mozaiksai.core.workflow.assignment_admission import (
    ResolvedAssignmentAdmission,
    resolve_assignment_admission,
)
from mozaiksai.core.workflow.structured_output_contracts import stable_digest
from tests.slice_5b_helpers import agent_config, compiled_assignment, structured_config


def test_unique_factory_participant_resolves_ephemerally() -> None:
    assignment, config = compiled_assignment()
    admission = resolve_assignment_admission(
        assignment,
        structured_output_configs={"AppGenerator": config},
        workflow_agent_configs={"AppGenerator": agent_config()},
    )
    assert isinstance(admission, ResolvedAssignmentAdmission)
    assert is_dataclass(admission)
    assert not hasattr(type(admission), "model_fields")
    assert admission.assignment == assignment
    assert admission.logical_participant_key == "ArtifactAuthor"
    assert set(asdict(admission)) == {"assignment", "logical_participant_key"}


def test_zero_and_multiple_participant_matches_fail() -> None:
    assignment, _ = compiled_assignment()
    zero = structured_config(registry={"Other": None})
    with pytest.raises(ValueError, match="no Factory participant"):
        resolve_assignment_admission(
            assignment,
            structured_output_configs={"AppGenerator": zero},
            workflow_agent_configs={"AppGenerator": agent_config()},
        )

    multiple = structured_config(
        registry={"ArtifactAuthor": "ArtifactOutput", "SecondAuthor": "ArtifactOutput"}
    )
    with pytest.raises(ValueError, match="ambiguous Factory participants"):
        resolve_assignment_admission(
            assignment,
            structured_output_configs={"AppGenerator": multiple},
            workflow_agent_configs={
                "AppGenerator": {
                    "agents": agent_config()["agents"]
                    + agent_config(participant="SecondAuthor")["agents"]
                }
            },
        )


def test_undeclared_participant_and_workflow_mismatch_fail() -> None:
    assignment, config = compiled_assignment()
    with pytest.raises(ValueError, match="not declared"):
        resolve_assignment_admission(
            assignment,
            structured_output_configs={"AppGenerator": config},
            workflow_agent_configs={"AppGenerator": agent_config(participant="Other")},
        )
    with pytest.raises(ValueError, match="workflow mismatch"):
        resolve_assignment_admission(
            assignment,
            structured_output_configs={"AppGenerator": config},
            workflow_agent_configs={},
        )


def test_stale_schema_and_runtime_identity_injection_fail() -> None:
    assignment, config = compiled_assignment()
    stale_ref = assignment.required_structured_output_ref.model_copy(
        update={"schema_digest": "0" * 64}
    )
    document = assignment.model_dump(mode="json")
    document["required_structured_output_ref"] = stale_ref.model_dump(mode="json")
    document["assignment_digest"] = "0" * 64
    with pytest.raises(ValidationError, match="assignment_digest"):
        type(assignment).model_validate(document)

    runtime = assignment.model_dump(mode="json")
    runtime["agent_id"] = "agent-runtime-1"
    with pytest.raises(ValidationError, match="extra"):
        type(assignment).model_validate(runtime)

    forged_document = assignment.model_dump(mode="json")
    forged_document["required_structured_output_ref"] = stale_ref.model_dump(mode="json")
    payload = {
        key: value
        for key, value in forged_document.items()
        if key not in {"assignment_id", "assignment_digest"}
    }
    if not assignment.semantic_identity_bindings:
        payload.pop("semantic_identity_bindings")
    forged_digest = stable_digest(payload)
    forged_document["assignment_digest"] = forged_digest
    forged_document["assignment_id"] = f"wa_{forged_digest[:24]}"
    forged = type(assignment).model_validate(forged_document)
    with pytest.raises(ValueError, match="schema digest mismatch"):
        resolve_assignment_admission(
            forged,
            structured_output_configs={"AppGenerator": config},
            workflow_agent_configs={"AppGenerator": agent_config()},
        )
