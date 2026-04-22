from __future__ import annotations

import importlib
import asyncio
from enum import Enum

import pytest

_dispatcher_mod = importlib.import_module("mozaiksai.core.events.unified_event_dispatcher")
_ports_mod = importlib.import_module("mozaiksai.core.ports.orchestration")
_serialization_mod = importlib.import_module("mozaiksai.core.events.event_serialization")
_runtime_events_mod = importlib.import_module("mozaiksai.core.events.runtime_events")
_ag2_events_mod = importlib.import_module("mozaiksai.core.events.ag2_events")
_ag2_bridge_mod = importlib.import_module("mozaiksai.core.events.ag2_event_bridge")

UnifiedEventDispatcher = _dispatcher_mod.UnifiedEventDispatcher
DomainEvent = _ports_mod.DomainEvent
serialize_event_content = _serialization_mod.serialize_event_content
RUNTIME_AGENT_OUTPUT_VALIDATED = _runtime_events_mod.RUNTIME_AGENT_OUTPUT_VALIDATED
RUNTIME_DECOMPOSITION_PLANNED = _runtime_events_mod.RUNTIME_DECOMPOSITION_PLANNED
ARTIFACT_EVENT_CREATED = _runtime_events_mod.ARTIFACT_EVENT_CREATED
ARTIFACT_EVENT_UPDATED = _runtime_events_mod.ARTIFACT_EVENT_UPDATED
ARTIFACT_EVENT_READY = _runtime_events_mod.ARTIFACT_EVENT_READY
build_runtime_agent_output_validated_event = _runtime_events_mod.build_runtime_agent_output_validated_event
build_artifact_lifecycle_event = _runtime_events_mod.build_artifact_lifecycle_event
build_runtime_context_payload = _runtime_events_mod.build_runtime_context_payload
build_turn_idempotency_key = _runtime_events_mod.build_turn_idempotency_key
DecompositionPlannedEvent = _ag2_events_mod.DecompositionPlannedEvent
ArtifactUpdatedEvent = _ag2_events_mod.ArtifactUpdatedEvent
ArtifactReadyEvent = _ag2_events_mod.ArtifactReadyEvent
AG2EventBridge = _ag2_bridge_mod.AG2EventBridge


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


def test_artifact_lifecycle_builder_rejects_legacy_alias_names() -> None:
    with pytest.raises(ValueError, match="Unsupported artifact lifecycle event type"):
        build_artifact_lifecycle_event(
            event_type="runtime.artifact_updated",
            artifact_id="artifact-1",
            artifact_kind="app_bundle",
            chat_id="chat-1",
            workflow_name="AppGenerator",
        )


@pytest.mark.asyncio
async def test_register_runtime_handler_listens_on_canonical_names() -> None:
    dispatcher = UnifiedEventDispatcher()
    seen = []

    async def _listener(payload):  # type: ignore[no-untyped-def]
        seen.append(payload)

    dispatcher.register_runtime_handler(RUNTIME_DECOMPOSITION_PLANNED, _listener)

    await dispatcher.emit(RUNTIME_DECOMPOSITION_PLANNED, {"kind": RUNTIME_DECOMPOSITION_PLANNED, "value": 1})
    await asyncio.sleep(0)

    assert seen == [{"kind": RUNTIME_DECOMPOSITION_PLANNED, "value": 1}]


@pytest.mark.asyncio
async def test_register_runtime_handler_does_not_listen_on_removed_aliases() -> None:
    dispatcher = UnifiedEventDispatcher()
    seen = []

    async def _listener(payload):  # type: ignore[no-untyped-def]
        seen.append(payload)

    dispatcher.register_runtime_handler(RUNTIME_DECOMPOSITION_PLANNED, _listener)

    await dispatcher.emit("chat.decomposition_planned", {"kind": "chat.decomposition_planned", "value": 1})
    await asyncio.sleep(0)

    assert seen == []


@pytest.mark.asyncio
async def test_ag2_bridge_maps_decomposition_checkpoint_to_runtime_event() -> None:
    dispatcher = UnifiedEventDispatcher()
    seen = []

    async def _listener(payload):  # type: ignore[no-untyped-def]
        seen.append(payload)

    dispatcher.register_runtime_handler(RUNTIME_DECOMPOSITION_PLANNED, _listener)
    bridge = AG2EventBridge(dispatcher)

    event = DecompositionPlannedEvent(
        agent_name="Planner",
        chat_id="chat-bridge",
        workflow_name="BuildParent",
        model_name="BuildPlan",
        structured_data={"workflows": ["A", "B"]},
        context={"chat_id": "chat-bridge", "workflow_name": "BuildParent"},
    )

    handled = await bridge.handle(event)
    await asyncio.sleep(0)

    assert handled is True
    assert len(seen) == 1
    assert seen[0]["kind"] == RUNTIME_DECOMPOSITION_PLANNED
    assert seen[0]["structured_data"] == {"workflows": ["A", "B"]}
    assert seen[0]["context"]["workflow_name"] == "BuildParent"


@pytest.mark.asyncio
async def test_ag2_bridge_maps_artifact_lifecycle_events() -> None:
    dispatcher = UnifiedEventDispatcher()
    seen = []

    async def _listener(payload):  # type: ignore[no-untyped-def]
        seen.append(payload)

    dispatcher.register_handler(ARTIFACT_EVENT_CREATED, _listener)
    dispatcher.register_handler(ARTIFACT_EVENT_READY, _listener)
    bridge = AG2EventBridge(dispatcher)

    created_event = ArtifactUpdatedEvent(
        artifact_id="artifact-1",
        artifact_type="app_bundle",
        chat_id="chat-bridge",
        workflow_name="AppGenerator",
        action="created",
        artifact_version_id="av_1",
    )
    ready_event = ArtifactReadyEvent(
        artifact_id="artifact-1",
        artifact_type="app_bundle",
        chat_id="chat-bridge",
        workflow_name="AppGenerator",
        artifact_version_id="av_1",
    )

    created_handled = await bridge.handle(created_event)
    ready_handled = await bridge.handle(ready_event)
    await asyncio.sleep(0)

    assert created_handled is True
    assert ready_handled is True
    assert len(seen) == 2
    assert seen[0]["kind"] == ARTIFACT_EVENT_CREATED
    assert seen[0]["artifact_version_id"] == "av_1"
    assert seen[1]["kind"] == ARTIFACT_EVENT_READY
    assert seen[1]["artifact_id"] == "artifact-1"
