import pytest

from factory_app.workflows.AgentGenerator.tools.record_workflow_interview import (
    record_workflow_interview,
)
from mozaiksai.core.workflow.workflow_manager import workflow_manager
from tests.test_factory_auto_tool_acceptance import factory_manager  # noqa: F401


@pytest.mark.usefixtures("factory_manager")
@pytest.mark.parametrize("outcome", ["needs_input", "ready"])
async def test_workflow_interview_rejects_message_mismatch_before_readiness_write(outcome):
    info = workflow_manager.reload_workflow("AgentGenerator")
    assert not info.get("error"), info
    initial_outcome = "ready" if outcome == "needs_input" else "needs_input"
    context = {
        "interview_outcome": initial_outcome,
        "structured_output": {
            "agent_message": "Approved question or confirmation",
            "outcome": outcome,
        },
    }

    with pytest.raises(ValueError, match="must match"):
        await record_workflow_interview("Different message", context)

    assert context["interview_outcome"] == initial_outcome
