"""Validate interview readiness without interpreting conversational routing markers."""

from typing import Any

from mozaiksai.core.workflow.context.frozen import detach
from mozaiksai.core.workflow.outputs.structured import load_workflow_structured_outputs


def record_theme_interview(agent_message: str, context_variables: Any = None) -> dict[str, str]:
    if context_variables is None:
        raise ValueError("Theme interview requires runtime context")
    models, _ = load_workflow_structured_outputs("ThemeCapture")
    result = models["ThemeInterviewResult"].model_validate(
        detach(context_variables.get("structured_output"))
    ).model_dump(mode="json")
    if not result["agent_message"].strip():
        raise ValueError("Theme interview requires a user-facing message")
    if agent_message != result["agent_message"]:
        raise ValueError("Theme interview message must match its validated output")
    return {"outcome": result["outcome"]}
