from __future__ import annotations

import uuid
from typing import Any, Dict, Optional

from mozaiksai.core.events.event_serialization import serialize_event_content


RUNTIME_AGENT_OUTPUT_VALIDATED = "runtime.agent_output_validated"
RUNTIME_DECOMPOSITION_PLANNED = "runtime.decomposition_planned"
RUNTIME_PROCESS_COMPLETED = "runtime.process_completed"
ARTIFACT_EVENT_CREATED = "artifact.created"
ARTIFACT_EVENT_UPDATED = "artifact.updated"
ARTIFACT_EVENT_READY = "artifact.ready"
ARTIFACT_EVENT_DELETED = "artifact.deleted"


def build_turn_idempotency_key(chat_id: str, turn_sequence: int) -> str:
    """Build a stable idempotency key for a chat turn."""
    turn_uuid = uuid.uuid5(uuid.NAMESPACE_URL, f"{chat_id}:{turn_sequence}")
    return f"turn-{turn_uuid.hex}"


def build_runtime_context_payload(
    *,
    chat_id: str,
    app_id: str,
    workflow_name: str,
    turn_sequence: Optional[int] = None,
    user_id: Optional[str] = None,
    context_variables: Any = None,
) -> Dict[str, Any]:
    """Build a sanitized runtime context snapshot for downstream listeners."""
    payload: Dict[str, Any] = {
        "chat_id": chat_id,
        "app_id": app_id,
        "workflow_name": workflow_name,
    }
    if turn_sequence is not None:
        payload["turn_sequence"] = int(turn_sequence)
    if user_id:
        payload["user_id"] = user_id

    raw_ctx: Optional[Dict[str, Any]] = None
    if context_variables is not None:
        if hasattr(context_variables, "data") and isinstance(getattr(context_variables, "data"), dict):
            raw_ctx = dict(getattr(context_variables, "data"))
        elif hasattr(context_variables, "to_dict") and callable(getattr(context_variables, "to_dict")):
            raw_ctx = dict(context_variables.to_dict())
        elif isinstance(context_variables, dict):
            raw_ctx = dict(context_variables)

    if raw_ctx:
        payload["context_variables"] = {
            key: serialize_event_content(value)
            for key, value in raw_ctx.items()
        }

    return payload


def build_runtime_agent_output_validated_event(
    *,
    agent: str,
    model_name: str,
    structured_data: Any,
    auto_tool_call: bool,
    context: Optional[Dict[str, Any]] = None,
    source: str = "runtime",
    turn_idempotency_key: Optional[str] = None,
    pattern_context_ref: Any = None,
    validation_passed: Optional[bool] = None,
) -> Dict[str, Any]:
    """Build the canonical runtime payload for validated structured output."""
    payload: Dict[str, Any] = {
        "kind": RUNTIME_AGENT_OUTPUT_VALIDATED,
        "runtime_event_type": RUNTIME_AGENT_OUTPUT_VALIDATED,
        "agent": agent,
        "agent_name": agent,
        "model_name": model_name,
        "structured_data": serialize_event_content(structured_data),
        "auto_tool_call": bool(auto_tool_call),
        "context": context or {},
        "source": source,
    }
    if turn_idempotency_key:
        payload["turn_idempotency_key"] = turn_idempotency_key
    if pattern_context_ref is not None:
        payload["_pattern_context_ref"] = pattern_context_ref
    if validation_passed is not None:
        payload["validation_passed"] = bool(validation_passed)
    return payload


def build_runtime_decomposition_planned_event(
    *,
    agent: str,
    model_name: str,
    structured_data: Any,
    context: Optional[Dict[str, Any]] = None,
    source: str = "runtime",
) -> Dict[str, Any]:
    """Build the canonical runtime payload for decomposition planning."""
    return {
        "kind": RUNTIME_DECOMPOSITION_PLANNED,
        "runtime_event_type": RUNTIME_DECOMPOSITION_PLANNED,
        "agent": agent,
        "agent_name": agent,
        "model_name": model_name,
        "structured_data": serialize_event_content(structured_data),
        "context": context or {},
        "source": source,
    }


def build_artifact_lifecycle_event(
    *,
    event_type: str,
    artifact_id: str,
    artifact_kind: str,
    chat_id: str,
    workflow_name: str,
    app_id: Optional[str] = None,
    artifact_version_id: Optional[str] = None,
    files_manifest: Optional[Any] = None,
    metadata: Optional[Dict[str, Any]] = None,
    source: str = "runtime",
) -> Dict[str, Any]:
    if event_type not in {
        ARTIFACT_EVENT_CREATED,
        ARTIFACT_EVENT_UPDATED,
        ARTIFACT_EVENT_READY,
        ARTIFACT_EVENT_DELETED,
    }:
        raise ValueError(f"Unsupported artifact lifecycle event type: {event_type}")

    payload: Dict[str, Any] = {
        "kind": event_type,
        "runtime_event_type": event_type,
        "artifact_id": artifact_id,
        "artifact_kind": artifact_kind,
        "artifact_type": artifact_kind,
        "chat_id": chat_id,
        "workflow_name": workflow_name,
        "source": source,
    }
    if app_id:
        payload["app_id"] = app_id
    if artifact_version_id:
        payload["artifact_version_id"] = artifact_version_id
    if files_manifest is not None:
        payload["files_manifest"] = serialize_event_content(files_manifest)
    if metadata:
        payload["metadata"] = serialize_event_content(metadata)
    return payload


__all__ = [
    "RUNTIME_AGENT_OUTPUT_VALIDATED",
    "RUNTIME_DECOMPOSITION_PLANNED",
    "ARTIFACT_EVENT_CREATED",
    "ARTIFACT_EVENT_UPDATED",
    "ARTIFACT_EVENT_READY",
    "ARTIFACT_EVENT_DELETED",
    "build_turn_idempotency_key",
    "build_runtime_context_payload",
    "build_runtime_agent_output_validated_event",
    "build_runtime_decomposition_planned_event",
    "build_artifact_lifecycle_event",
]