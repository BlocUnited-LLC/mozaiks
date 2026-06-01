from __future__ import annotations

import pytest

from mozaiksai.core.events.ag2_events import (
    ArtifactReadyEvent,
    HandoffRequestedEvent,
    StructuredOutputEvent,
)
from mozaiksai.core.events.runtime_events import (
    RUNTIME_AGENT_OUTPUT_VALIDATED,
    build_artifact_lifecycle_event,
    build_runtime_agent_output_validated_event,
)
from mozaiksai.core.transport.simple_transport import SimpleTransport
from mozaiksai.core.workflow.task_batches import _emit_task_batch_activity


def test_transport_serializes_mozaiks_ag2_events_without_pydantic_internals() -> None:
    transport = SimpleTransport()
    event = StructuredOutputEvent(
        agent_name="AppPlanAgent",
        chat_id="chat-1",
        output_type="AppPlan",
        output_data={"summary": "done"},
        validation_passed=True,
    )

    payload = transport._serialize_ag2_events(event)

    assert payload == {
        "type": "structured_output",
        "content": {
            "uuid": payload["content"]["uuid"],
            "agent_name": "AppPlanAgent",
            "chat_id": "chat-1",
            "output_type": "AppPlan",
            "output_data": {"summary": "done"},
            "validation_passed": True,
        },
    }
    assert isinstance(payload["content"]["uuid"], str)
    assert "model_fields" not in payload
    assert "model_fields" not in payload["content"]


def test_transport_serializes_artifact_and_handoff_events() -> None:
    transport = SimpleTransport()

    artifact = transport._serialize_ag2_events(
        ArtifactReadyEvent(
            artifact_id="artifact-1",
            artifact_type="app_bundle",
            chat_id="chat-1",
            workflow_name="AppGenerator",
            artifact_version_id="version-1",
        )
    )
    handoff = transport._serialize_ag2_events(
        HandoffRequestedEvent(
            from_agent="WorkflowStrategyAgent",
            to_agent="AppPlanAgent",
            reason="Need concrete app plan",
            chat_id="chat-1",
            context_snapshot={},
        )
    )

    assert artifact["type"] == "artifact_ready"
    assert artifact["content"]["artifact_id"] == "artifact-1"
    assert artifact["content"]["workflow_name"] == "AppGenerator"
    assert handoff["type"] == "handoff_requested"
    assert handoff["content"]["from_agent"] == "WorkflowStrategyAgent"
    assert handoff["content"]["to_agent"] == "AppPlanAgent"


def test_runtime_event_builders_emit_canonical_payloads() -> None:
    output = build_runtime_agent_output_validated_event(
        agent="AppPlanAgent",
        model_name="AppBuildPlanOutput",
        structured_data={"AppBuildPlan": {"app_name": "Support Operations"}},
        auto_tool_call=True,
        context={"chat_id": "chat-1", "workflow_name": "AppGenerator"},
    )
    artifact = build_artifact_lifecycle_event(
        event_type="artifact.ready",
        artifact_id="artifact-1",
        artifact_kind="app_bundle",
        chat_id="chat-1",
        workflow_name="AppGenerator",
        artifact_version_id="version-1",
    )

    assert output["kind"] == RUNTIME_AGENT_OUTPUT_VALIDATED
    assert output["agent"] == "AppPlanAgent"
    assert output["auto_tool_call"] is True
    assert output["structured_data"]["AppBuildPlan"]["app_name"] == "Support Operations"
    assert artifact["kind"] == "artifact.ready"
    assert artifact["artifact_type"] == "app_bundle"
    assert artifact["artifact_version_id"] == "version-1"


@pytest.mark.asyncio
async def test_task_batch_activity_emits_direct_activity_payload() -> None:
    class _Transport:
        def __init__(self) -> None:
            self.events: list[tuple[dict, str]] = []

        async def send_event_to_ui(self, payload: dict, chat_id: str) -> None:
            self.events.append((payload, chat_id))

    transport = _Transport()

    await _emit_task_batch_activity(
        transport,
        "chat-1",
        {
            "phase": "started",
            "batch_id": "app_build_tasks",
            "task_count": 4,
        },
    )

    assert transport.events == [
        (
            {
                "kind": "activity",
                "activity_type": "task_batch",
                "phase": "started",
                "batch_id": "app_build_tasks",
                "task_count": 4,
            },
            "chat-1",
        )
    ]
