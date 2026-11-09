"""Shared declared-event validation and envelopes for module-owned emitters."""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from copy import deepcopy
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from mozaiksai.core.runtime.composition.module_event_provenance import ModuleEventRejection
from mozaiksai.core.runtime.composition.schema_validation import validate_json_schema
from mozaiksai.core.runtime.composition.workflow_trigger_guard import WORKFLOW_TRIGGER_TRACE_KEY


def module_event_id(event_id: str | None = None) -> str:
    if event_id is not None and (
        not isinstance(event_id, str)
        or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", event_id) is None
    ):
        raise ValueError("event_id must be a nonempty, bounded event identifier")
    return event_id or f"evt_{uuid4().hex}"


def module_event_rejection(
    *,
    module_id: str,
    action_id: str,
    event_id: str,
    event_type: str,
    payload: Any,
    declared_emits: Iterable[str] | None,
    payload_schema: Any,
) -> ModuleEventRejection | None:
    if declared_emits is not None and event_type not in declared_emits:
        safe_type = re.sub(r"[^A-Za-z0-9_.\-]", "?", event_type)
        if len(safe_type) > 128:
            safe_type = safe_type[:128] + "..."
        return ModuleEventRejection(
            event_id=event_id,
            event_type=safe_type,
            category="undeclared",
            reason=f"Action {module_id}.{action_id} does not declare this event in module.yaml emits.",
            validator="emits",
        )
    if payload_schema is None:
        return None
    diagnostic = validate_json_schema(payload, payload_schema)
    if diagnostic is None:
        return None
    return ModuleEventRejection(
        event_id=event_id,
        event_type=event_type,
        category=diagnostic.category,
        reason=diagnostic.message,
        validator=diagnostic.validator,
        schema_path=diagnostic.schema_path,
    )


def build_module_event_envelope(
    *,
    module_id: str,
    action_id: str,
    app_id: str,
    tenant_id: str | None,
    workspace_id: str | None,
    actor: Mapping[str, Any] | None,
    correlation_id: str | None,
    authority: Mapping[str, Any],
    event_id: str,
    event_type: str,
    payload: dict[str, Any],
    causation_id: str | None = None,
    trigger_trace: Any = None,
) -> dict[str, Any]:
    tenant_scope = {"app_id": app_id, "tenant_id": tenant_id}
    if workspace_id:
        tenant_scope["workspace_id"] = workspace_id
    correlation = {"correlation_id": correlation_id}
    if causation_id:
        correlation["causation_id"] = causation_id
    envelope: dict[str, Any] = {
        "id": event_id,
        "type": event_type,
        "version": 1,
        "occurred_at": datetime.now(UTC).isoformat(),
        "source": {
            "layer": "module",
            "app_id": app_id,
            "module_id": module_id,
            "action_id": action_id,
            "capability_id": f"{module_id}.{action_id}",
        },
        "tenant": tenant_scope,
        "correlation": correlation,
        "payload": payload,
        "visibility": "internal",
        "authority": dict(authority),
    }
    if actor:
        envelope["actor"] = dict(actor)
    if trigger_trace is not None:
        envelope[WORKFLOW_TRIGGER_TRACE_KEY] = deepcopy(trigger_trace)
    return envelope
