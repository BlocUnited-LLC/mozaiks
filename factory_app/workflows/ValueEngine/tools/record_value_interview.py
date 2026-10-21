"""Validate concept intake readiness without parsing conversational control words."""

from typing import Any

from mozaiksai.core.workflow.context.frozen import detach
from mozaiksai.core.workflow.outputs.structured import load_workflow_structured_outputs


def record_value_interview(agent_message: str, context_variables: Any = None) -> dict[str, str]:
    if context_variables is None:
        raise ValueError("Value interview requires runtime context")
    models, _ = load_workflow_structured_outputs("ValueEngine")
    result = models["ValueInterviewResult"].model_validate(
        detach(context_variables.get("structured_output"))
    ).model_dump(mode="json")
    if not result["agent_message"].strip():
        raise ValueError("Value interview requires a user-facing message")
    if agent_message != result["agent_message"]:
        raise ValueError("Value interview message must match its validated output")
    return {"outcome": result["outcome"]}
