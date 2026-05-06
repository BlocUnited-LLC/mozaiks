from __future__ import annotations

from types import SimpleNamespace

from tests.import_utils import import_module_directly


_handler_mod = import_module_directly("mozaiksai.core.workflow.stream.handlers.mozaiks_event_handler")
_events_mod = import_module_directly("mozaiksai.core.events.ag2_events")


MozaiksaiEventHandler = _handler_mod.MozaiksaiEventHandler
StructuredOutputEvent = _events_mod.StructuredOutputEvent
ArtifactReadyEvent = _events_mod.ArtifactReadyEvent
DecompositionPlannedEvent = _events_mod.DecompositionPlannedEvent
HandoffRequestedEvent = _events_mod.HandoffRequestedEvent


def _ctx() -> SimpleNamespace:
    return SimpleNamespace(chat_id="chat-1", workflow_name="AppGenerator")


def test_build_ui_payload_converts_structured_output_into_activity() -> None:
    handler = MozaiksaiEventHandler()
    event = StructuredOutputEvent(
        agent_name="AppPlanAgent",
        chat_id="chat-1",
        output_type="AppPlan",
        output_data={"summary": "done"},
        validation_passed=True,
    )

    payload = handler._build_ui_payload(event, _ctx())

    assert payload == {
        "kind": "activity",
        "activity_type": "structured_output",
        "status": "validated",
        "agent": "AppPlanAgent",
        "message": "AppPlanAgent produced validated AppPlan.",
        "chat_id": "chat-1",
        "workflow_name": "AppGenerator",
        "metadata": {
            "output_type": "AppPlan",
            "validation_passed": True,
        },
    }


def test_build_ui_payload_converts_artifact_ready_into_activity() -> None:
    handler = MozaiksaiEventHandler()
    event = ArtifactReadyEvent(
        artifact_id="artifact-1",
        artifact_type="app_bundle",
        chat_id="chat-1",
        workflow_name="AppGenerator",
        artifact_version_id="version-1",
    )

    payload = handler._build_ui_payload(event, _ctx())

    assert payload == {
        "kind": "activity",
        "activity_type": "artifact_ready",
        "status": "ready",
        "agent": None,
        "message": "App Bundle ready.",
        "chat_id": "chat-1",
        "workflow_name": "AppGenerator",
        "metadata": {
            "artifact_type": "app_bundle",
            "artifact_id": "artifact-1",
            "artifact_version_id": "version-1",
        },
    }


def test_build_ui_payload_converts_decomposition_and_handoff_into_activity() -> None:
    handler = MozaiksaiEventHandler()
    decomposition = DecompositionPlannedEvent(
        agent_name="WorkflowStrategyAgent",
        chat_id="chat-1",
        workflow_name="AppGenerator",
        model_name="WorkflowPlan",
        structured_data={"workflows": [{"name": "One"}, {"name": "Two"}]},
        context={},
    )
    handoff = HandoffRequestedEvent(
        from_agent="WorkflowStrategyAgent",
        to_agent="AppPlanAgent",
        reason="Need concrete app plan",
        chat_id="chat-1",
        context_snapshot={},
    )

    decomposition_payload = handler._build_ui_payload(decomposition, _ctx())
    handoff_payload = handler._build_ui_payload(handoff, _ctx())

    assert decomposition_payload == {
        "kind": "activity",
        "activity_type": "decomposition_planned",
        "status": "planned",
        "agent": "WorkflowStrategyAgent",
        "message": "WorkflowStrategyAgent planned 2 child workflows.",
        "chat_id": "chat-1",
        "workflow_name": "AppGenerator",
        "metadata": {"workflow_count": 2},
    }
    assert handoff_payload == {
        "kind": "activity",
        "activity_type": "handoff_requested",
        "status": "handoff",
        "agent": "WorkflowStrategyAgent",
        "message": "Handoff: WorkflowStrategyAgent to AppPlanAgent.",
        "chat_id": "chat-1",
        "workflow_name": "AppGenerator",
        "metadata": {
            "from_agent": "WorkflowStrategyAgent",
            "to_agent": "AppPlanAgent",
            "reason": "Need concrete app plan",
        },
    }
