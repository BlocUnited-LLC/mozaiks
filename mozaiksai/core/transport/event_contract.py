"""
mozaiks.ui.event.v1 — Canonical event type contract.

All WebSocket event type strings are defined here. The unified event dispatcher
and UI layer import from this module. Wire-format strings are stable.
"""
from __future__ import annotations

import uuid
from collections.abc import Mapping
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel

# Deterministic event ID namespace
NAMESPACE_MOZAIKS = uuid.UUID("6ba7b810-9dad-11d1-80b4-00c04fd430c8")  # uuid.NAMESPACE_URL
EVENT_ENVELOPE_SCHEMA_VERSION: Literal["mozaiks.ui.event.v1"] = "mozaiks.ui.event.v1"


class MozaiksEventType(StrEnum):
    ERROR = "error"  # Grandfathered pre-namespaced application error envelope.
    # ── Outbound: AG2 chat stream events ──────────────────────────────────────
    CHAT_TEXT = "chat.text"
    CHAT_PRINT = "chat.print"
    CHAT_TOOL_CALL = "chat.tool_call"
    CHAT_TOOL_RESPONSE = "chat.tool_response"
    CHAT_TOOL_PROGRESS = "chat.tool_progress"
    CHAT_TOOL_CALL_DISMISS = "chat.tool_call_dismiss"
    CHAT_RUN_START = "chat.run_start"
    CHAT_RUN_COMPLETE = "chat.run_complete"
    CHAT_ERROR = "chat.error"
    CHAT_STREAM_CHUNK = "chat.stream_chunk"
    CHAT_STREAM_END = "chat.stream_end"
    CHAT_AGENT_OUTPUT_VALIDATED = "chat.agent_output_validated"
    CHAT_GREETING_ECHO = "chat.greeting_echo"
    # ── Outbound: Session / flow events ───────────────────────────────────────
    CHAT_INPUT_ACK = "chat.input_ack"
    CHAT_INPUT_TIMEOUT = "chat.input_timeout"
    CHAT_SELECT_SPEAKER = "chat.select_speaker"
    CHAT_RESUME_BOUNDARY = "chat.resume_boundary"
    CHAT_AWAITING_REPLY = "chat.awaiting_reply"
    CHAT_ACTIVITY = "chat.activity"
    CHAT_ATTACHMENT_UPLOADED = "chat.attachment_uploaded"
    CHAT_CUSTOM_EVENT = "chat.custom_event"
    # ── Outbound: Usage / token budget events ─────────────────────────────────
    CHAT_USAGE_DELTA = "chat.usage_delta"
    CHAT_USAGE_SUMMARY = "chat.usage_summary"
    CHAT_TOKEN_BUDGET_ALERT = "chat.token_budget_alert"
    # ── Outbound: Workflow state events ───────────────────────────────────────
    CHAT_WORKFLOW_STARTED = "chat.workflow_started"
    CHAT_WORKFLOW_REROUTED = "chat.workflow_rerouted"
    CHAT_WORKFLOW_BATCH_STARTED = "chat.workflow_batch_started"
    CHAT_CONTEXT_SWITCHED = "chat.context_switched"
    CHAT_MODE_CHANGED = "chat.mode_changed"
    CHAT_GENERAL_SESSION_CREATED = "chat.general_session_created"
    CHAT_NAVIGATE = "chat.navigate"
    # ── Inbound: Client-initiated actions ─────────────────────────────────────
    CHAT_ARTIFACT_ACTION = "chat.artifact_action"
    CHAT_SWITCH_WORKFLOW = "chat.switch_workflow"
    CHAT_ENTER_GENERAL_MODE = "chat.enter_general_mode"
    CHAT_START_GENERAL_CHAT = "chat.start_general_chat"
    CHAT_START_WORKFLOW = "chat.start_workflow"
    CHAT_START_WORKFLOW_BATCH = "chat.start_workflow_batch"
    # ── Outbound: UI component events (L2 primitive contract) ─────────────────
    UI_RENDER = "ui.render"
    UI_UPDATE = "ui.update"
    UI_DISMISS = "ui.dismiss"


def compute_event_id(
    tenant_id: str,
    run_id: str | None,
    session_id: str | None,
    sequence_number: int,
    event_type: MozaiksEventType,
) -> str:
    """Deterministic event ID for replay and deduplication."""
    scope = run_id or session_id or "global"
    return uuid.uuid5(
        NAMESPACE_MOZAIKS,
        f"{tenant_id}:{scope}:{sequence_number}:{event_type}",
    ).hex


class MozaiksEventEnvelope(BaseModel):
    """Outbound WebSocket event envelope.

    Carries a canonical ``type`` string (a ``MozaiksEventType`` member) plus
    optional correlation metadata. Additional transport fields such as ``data``,
    ``chat_id``, and ``timestamp`` are allowed via ``extra = "allow"`` so this
    model can wrap arbitrary event payloads without stripping them.
    """

    model_config = {"extra": "allow"}

    schema_version: Literal["mozaiks.ui.event.v1"]
    type: MozaiksEventType
    event_id: str | None = None
    corr: str | None = None  # correlation / trace ID


def validate_event_envelope_schema_version(envelope: Mapping[str, Any]) -> None:
    """Fail closed when an outbound envelope omits or changes its wire version."""
    version = envelope.get("schema_version")
    if version != EVENT_ENVELOPE_SCHEMA_VERSION:
        raise ValueError(
            "event envelope schema_version must be "
            f"{EVENT_ENVELOPE_SCHEMA_VERSION!r}, got {version!r}"
        )


async def send_event_envelope(websocket: Any, envelope: Mapping[str, Any]) -> None:
    """Validate an application event envelope at the direct WebSocket boundary."""
    validate_event_envelope_schema_version(envelope)
    await websocket.send_json(dict(envelope))


__all__ = [
    "NAMESPACE_MOZAIKS",
    "EVENT_ENVELOPE_SCHEMA_VERSION",
    "MozaiksEventType",
    "MozaiksEventEnvelope",
    "compute_event_id",
    "send_event_envelope",
    "validate_event_envelope_schema_version",
]
