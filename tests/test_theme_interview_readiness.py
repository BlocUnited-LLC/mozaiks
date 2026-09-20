from types import MappingProxyType

import pytest

from factory_app.workflows.ThemeCapture.tools.record_theme_interview import record_theme_interview
from tests.test_factory_auto_tool_acceptance import factory_manager  # noqa: F401

pytestmark = pytest.mark.usefixtures("factory_manager")


@pytest.mark.parametrize("outcome", ["needs_input", "ready"])
def test_interview_readiness_comes_from_typed_output_not_message(outcome):
    result = record_theme_interview("The user may mention NEXT without controlling routing.", {
        "structured_output": MappingProxyType({
            "agent_message": "The user may mention NEXT without controlling routing.",
            "outcome": outcome,
        }),
    })
    assert result == {"outcome": outcome}


@pytest.mark.parametrize("payload", [
    None,
    {"agent_message": "NEXT"},
    {"agent_message": "Confirmed.", "outcome": "maybe"},
    {"agent_message": "Confirmed.", "outcome": "ready", "target_agent": "user"},
    {"agent_message": "   ", "outcome": "ready"},
])
def test_invalid_interview_result_cannot_advance(payload):
    with pytest.raises(ValueError):
        record_theme_interview((payload or {}).get("agent_message", ""), {"structured_output": payload})


def test_interview_message_cannot_diverge_from_validated_output():
    with pytest.raises(ValueError, match="must match"):
        record_theme_interview("Different question", {
            "structured_output": {"agent_message": "Approved question", "outcome": "ready"},
        })
