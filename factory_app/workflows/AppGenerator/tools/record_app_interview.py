"""Validate app interview readiness through the declared structured-output contract."""

from typing import Any

from mozaiksai.core.workflow.context.frozen import detach
from mozaiksai.core.workflow.outputs.structured import load_workflow_structured_outputs


def record_app_interview(agent_message: str, context_variables: Any = None) -> dict[str, str]:
    if context_variables is None:
        raise ValueError("App interview requires runtime context")
    models, _ = load_workflow_structured_outputs("AppGenerator")
    result = models["AppInterviewResult"].model_validate(
        detach(context_variables.get("structured_output"))
    ).model_dump(mode="json")
    if not result["agent_message"].strip():
        raise ValueError("App interview requires a user-facing message")
    if agent_message != result["agent_message"]:
        raise ValueError("App interview message must match its validated output")
    return {"outcome": result["outcome"]}
