"""Write interview readiness from validated structured output, not from chat text.

Readiness used to be inferred by matching the literal token ``NEXT`` against the
agent's message. That required the model to emit a bare token with no preamble,
which it does not reliably do: a live run produced two paragraphs of correct
reasoning followed by ``NEXT``, the exact match failed, and the workflow waited
on a user forever. See issue #591.

The agent now states readiness in a typed field. This tool validates that output
and writes the routing key, so the write is deterministic machinery rather than
freeform prose — the same authority boundary the exact-match sentinel protected.
"""

from typing import Any

from mozaiksai.core.workflow.context.frozen import detach
from mozaiksai.core.workflow.outputs.structured import load_workflow_structured_outputs


def _context_set(context_variables: Any | None, key: str, value: Any) -> None:
    if context_variables is None:
        return
    if hasattr(context_variables, "set"):
        try:
            context_variables.set(key, value)
            return
        except Exception:
            pass
    data = getattr(context_variables, "data", None)
    if isinstance(data, dict):
        data[key] = value
        return
    if isinstance(context_variables, dict):
        context_variables[key] = value


async def record_workflow_interview(context_variables: Any = None) -> dict[str, str]:
    if context_variables is None:
        raise ValueError("Workflow interview requires runtime context")

    models, _ = load_workflow_structured_outputs("AgentGenerator")
    result = (
        models["WorkflowInterviewResult"]
        .model_validate(detach(context_variables.get("structured_output")))
        .model_dump(mode="json")
    )

    if not str(result.get("agent_message") or "").strip():
        raise ValueError("Workflow interview requires a user-facing message")

    outcome = str(result["outcome"])
    _context_set(context_variables, "interview_outcome", outcome)
    return {"outcome": outcome}
