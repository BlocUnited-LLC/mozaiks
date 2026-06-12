from __future__ import annotations

import asyncio
import importlib
from enum import Enum

import pytest

_dispatcher_mod = importlib.import_module("mozaiksai.core.events.unified_event_dispatcher")
_ports_mod = importlib.import_module("mozaiksai.core.ports.orchestration")
_serialization_mod = importlib.import_module("mozaiksai.core.events.event_serialization")
_runtime_events_mod = importlib.import_module("mozaiksai.core.events.runtime_events")

UnifiedEventDispatcher = _dispatcher_mod.UnifiedEventDispatcher
DomainEvent = _ports_mod.DomainEvent
serialize_event_content = _serialization_mod.serialize_event_content
RUNTIME_AGENT_OUTPUT_VALIDATED = _runtime_events_mod.RUNTIME_AGENT_OUTPUT_VALIDATED
ARTIFACT_EVENT_CREATED = _runtime_events_mod.ARTIFACT_EVENT_CREATED
ARTIFACT_EVENT_UPDATED = _runtime_events_mod.ARTIFACT_EVENT_UPDATED
ARTIFACT_EVENT_READY = _runtime_events_mod.ARTIFACT_EVENT_READY
build_runtime_agent_output_validated_event = _runtime_events_mod.build_runtime_agent_output_validated_event
build_artifact_lifecycle_event = _runtime_events_mod.build_artifact_lifecycle_event
build_runtime_context_payload = _runtime_events_mod.build_runtime_context_payload
build_turn_idempotency_key = _runtime_events_mod.build_turn_idempotency_key


@pytest.mark.asyncio
async def test_emit_domain_event_notifies_registered_listeners() -> None:
    dispatcher = UnifiedEventDispatcher()
    seen = []

    async def _listener(payload):  # type: ignore[no-untyped-def]
        seen.append(payload)

    dispatcher.register_handler("runtime.test_event", _listener)

    event = DomainEvent(
        kind="runtime.test_event",
        payload={"status": "ok"},
        chat_id="chat-123",
        source="unit_test",
    )

    success = await dispatcher.emit_domain_event(event)
    await asyncio.sleep(0)

    assert success is True
    assert seen
    assert seen[0]["kind"] == "runtime.test_event"
    assert seen[0]["payload"] == {"status": "ok"}
    assert seen[0]["chat_id"] == "chat-123"
    assert seen[0]["source"] == "unit_test"


def test_build_outbound_event_envelope_supports_domain_event() -> None:
    dispatcher = UnifiedEventDispatcher()
    event = DomainEvent(
        kind="runtime.test_event",
        payload={"count": 1},
        chat_id="chat-456",
        source="unit_test",
    )

    envelope = dispatcher.build_outbound_event_envelope(
        raw_event=event,
        chat_id=event.chat_id,
    )

    assert envelope is not None
    assert envelope["type"] == "runtime.test_event"
    assert envelope["chat_id"] == "chat-456"
    assert envelope["data"]["payload"] == {"count": 1}
    assert envelope["data"]["source"] == "unit_test"


def test_build_outbound_event_envelope_suppresses_first_hidden_initial_message(monkeypatch) -> None:
    dispatcher = UnifiedEventDispatcher()

    monkeypatch.setattr(
        _dispatcher_mod,
        "matches_hidden_initial_message",
        lambda **kwargs: kwargs.get("workflow_name") == "ValueEngine"
        and kwargs.get("content") == "Hidden workflow primer"
        and kwargs.get("agent_name") == "user",
    )

    first = dispatcher.build_outbound_event_envelope(
        raw_event={"kind": "text", "content": "Hidden workflow primer", "agent": "user"},
        chat_id="chat-1",
        workflow_name="ValueEngine",
    )
    second = dispatcher.build_outbound_event_envelope(
        raw_event={"kind": "text", "content": "Hidden workflow primer", "agent": "user"},
        chat_id="chat-1",
        workflow_name="ValueEngine",
    )

    assert first is not None
    assert first["type"] == "chat.text"
    assert first["data"]["_mozaiks_hide"] is True
    assert second is not None
    assert second["type"] == "chat.text"
    assert second["data"].get("_mozaiks_hide") is not True


def test_build_outbound_event_envelope_marks_hidden_initial_seed_messages() -> None:
    dispatcher = UnifiedEventDispatcher()

    envelope = dispatcher.build_outbound_event_envelope(
        raw_event={
            "kind": "text",
            "content": "ValueInterviewAgent: guide the user to a concrete app direction.",
            "agent": "user",
            "role": "user",
            "_mozaiks_seed_kind": "initial_message",
        },
        chat_id="chat-seed",
        workflow_name="ValueEngine",
    )

    assert envelope is not None
    assert envelope["type"] == "chat.text"
    assert envelope["data"]["_mozaiks_hide"] is True
    assert envelope["data"]["_mozaiks_seed_kind"] == "initial_message"


def test_serialize_event_content_normalizes_enum_to_value() -> None:
    class WorkflowName(Enum):
        SMOKE_CHILD = "SmokeChild"

    payload = {"name": WorkflowName.SMOKE_CHILD}

    assert serialize_event_content(payload) == {"name": "SmokeChild"}


def test_runtime_event_builder_uses_canonical_names() -> None:
    event = build_runtime_agent_output_validated_event(
        agent="Planner",
        model_name="BuildPlan",
        structured_data={"workflows": ["A"]},
        auto_tool_call=True,
        context={"workflow_name": "BuildParent", "chat_id": "chat-1"},
        turn_idempotency_key=build_turn_idempotency_key("chat-1", 3),
    )

    assert event["kind"] == RUNTIME_AGENT_OUTPUT_VALIDATED
    assert event["runtime_event_type"] == RUNTIME_AGENT_OUTPUT_VALIDATED
    assert event["context"]["workflow_name"] == "BuildParent"
    assert event["auto_tool_call"] is True


def test_artifact_lifecycle_builder_uses_canonical_names() -> None:
    event = build_artifact_lifecycle_event(
        event_type=ARTIFACT_EVENT_CREATED,
        artifact_id="artifact-1",
        artifact_kind="app_bundle",
        chat_id="chat-1",
        workflow_name="AppGenerator",
        artifact_version_id="av_1",
        files_manifest=[{"path": "src/App.tsx"}],
        metadata={"source": "compile"},
    )

    assert event["kind"] == ARTIFACT_EVENT_CREATED
    assert event["runtime_event_type"] == ARTIFACT_EVENT_CREATED
    assert event["artifact_kind"] == "app_bundle"
    assert event["artifact_version_id"] == "av_1"
    assert event["files_manifest"] == [{"path": "src/App.tsx"}]


def test_runtime_context_payload_serializes_context_variables() -> None:
    payload = build_runtime_context_payload(
        chat_id="chat-ctx",
        app_id="app-1",
        workflow_name="BuildParent",
        turn_sequence=7,
        context_variables={"stage": "planning", "count": 2},
    )

    assert payload == {
        "chat_id": "chat-ctx",
        "app_id": "app-1",
        "workflow_name": "BuildParent",
        "turn_sequence": 7,
        "context_variables": {"stage": "planning", "count": 2},
    }


def test_artifact_lifecycle_builder_rejects_removed_alias_names() -> None:
    with pytest.raises(ValueError, match="Unsupported artifact lifecycle event type"):
        build_artifact_lifecycle_event(
            event_type="runtime.artifact_updated",
            artifact_id="artifact-1",
            artifact_kind="app_bundle",
            chat_id="chat-1",
            workflow_name="AppGenerator",
        )



