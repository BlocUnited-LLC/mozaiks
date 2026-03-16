from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


_EVENT_TYPE_RE = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$")


def _trim_required(value: Any, *, field_name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{field_name} must be non-empty")
    return text


class AutomationModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AutomationDecisionStatus(str, Enum):
    MATCHED = "matched"
    IGNORED = "ignored"
    INVALID = "invalid"


class AutomationActorType(str, Enum):
    SYSTEM = "system"
    USER = "user"
    AGENT = "agent"
    INTEGRATION = "integration"


class AutomationEffectKind(str, Enum):
    WORKFLOW_RUN = "workflow.run"
    WORKFLOW_RESUME = "workflow.resume"
    ARTIFACT_UPSERT = "artifact.upsert"
    NOTIFICATION_SEND = "notification.send"
    NONE = "none"


class AutomationTenant(AutomationModel):
    app_id: str
    user_id: Optional[str] = None
    chat_id: Optional[str] = None
    run_id: Optional[str] = None

    @field_validator("app_id")
    @classmethod
    def _validate_app_id(cls, value: Any) -> str:
        return _trim_required(value, field_name="app_id")

    @field_validator("user_id", "chat_id", "run_id")
    @classmethod
    def _normalize_optional_text(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        text = str(value).strip()
        return text or None


class AutomationActor(AutomationModel):
    id: str
    type: AutomationActorType

    @field_validator("id")
    @classmethod
    def _validate_id(cls, value: Any) -> str:
        return _trim_required(value, field_name="id")


class AutomationSource(AutomationModel):
    layer: str
    component: str
    transport: str
    internal_event: Optional[str] = None

    @field_validator("layer", "component", "transport")
    @classmethod
    def _validate_required_text(cls, value: Any, info):  # type: ignore[no-untyped-def]
        return _trim_required(value, field_name=info.field_name)

    @field_validator("internal_event")
    @classmethod
    def _normalize_internal_event(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        text = str(value).strip()
        return text or None


class SubstrateEventEnvelope(AutomationModel):
    event_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    event_type: str
    timestamp: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    tenant: AutomationTenant
    actor: AutomationActor
    source: AutomationSource
    payload: Dict[str, Any] = Field(default_factory=dict)
    causation_id: Optional[str] = None
    correlation_id: Optional[str] = None

    @field_validator("event_id")
    @classmethod
    def _validate_event_id(cls, value: Any) -> str:
        return _trim_required(value, field_name="event_id")

    @field_validator("event_type")
    @classmethod
    def _validate_event_type(cls, value: Any) -> str:
        event_type = _trim_required(value, field_name="event_type")
        if not _EVENT_TYPE_RE.match(event_type):
            raise ValueError("event_type must use lowercase dot notation")
        return event_type

    @field_validator("causation_id", "correlation_id")
    @classmethod
    def _normalize_optional_ids(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        text = str(value).strip()
        return text or None


class EventCatalogEntry(AutomationModel):
    event_type: str
    source_event: Optional[str] = None
    description: str = ""
    post_commit_only: bool = True

    @field_validator("event_type")
    @classmethod
    def _validate_event_type(cls, value: Any) -> str:
        event_type = _trim_required(value, field_name="event_type")
        if not _EVENT_TYPE_RE.match(event_type):
            raise ValueError("event_type must use lowercase dot notation")
        return event_type

    @field_validator("source_event")
    @classmethod
    def _normalize_source_event(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        text = str(value).strip()
        return text or None


class AutomationEffect(AutomationModel):
    kind: AutomationEffectKind
    workflow: Optional[str] = None
    surface: str = "background"
    message_template: Optional[str] = None

    @field_validator("workflow", "surface", "message_template")
    @classmethod
    def _normalize_optional_text(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    @model_validator(mode="after")
    def _validate_workflow_requirement(self) -> "AutomationEffect":
        if self.kind in {
            AutomationEffectKind.WORKFLOW_RUN,
            AutomationEffectKind.WORKFLOW_RESUME,
        } and not self.workflow:
            raise ValueError("workflow is required for workflow.run and workflow.resume")
        return self


class AutomationRoute(AutomationModel):
    route_id: str
    event_type: str
    when: Dict[str, Any] = Field(default_factory=dict)
    effect: AutomationEffect
    bindings: Dict[str, str] = Field(default_factory=dict)
    enabled: bool = True

    @field_validator("route_id", "event_type")
    @classmethod
    def _validate_required_text(cls, value: Any, info):  # type: ignore[no-untyped-def]
        return _trim_required(value, field_name=info.field_name)

    @field_validator("bindings")
    @classmethod
    def _normalize_bindings(cls, value: Dict[str, str]) -> Dict[str, str]:
        normalized: Dict[str, str] = {}
        for key, raw in value.items():
            name = str(key or "").strip()
            target = str(raw or "").strip()
            if name and target:
                normalized[name] = target
        return normalized


class AutomationConfigBundle(AutomationModel):
    events: list[EventCatalogEntry] = Field(default_factory=list)
    routes: list[AutomationRoute] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validate_cross_references(self) -> "AutomationConfigBundle":
        known_event_types = {entry.event_type for entry in self.events}
        route_ids: set[str] = set()
        errors: list[str] = []

        for route in self.routes:
            if route.route_id in route_ids:
                errors.append(f"duplicate automation route_id '{route.route_id}'")
            route_ids.add(route.route_id)
            if route.event_type not in known_event_types:
                errors.append(
                    f"automation route '{route.route_id}' references unknown event_type '{route.event_type}'"
                )

        if errors:
            raise ValueError("; ".join(errors))
        return self


class AutomationDecision(AutomationModel):
    status: AutomationDecisionStatus
    route_id: Optional[str] = None
    route: Optional[str] = None
    payload: Dict[str, Any] = Field(default_factory=dict)
    detail: Dict[str, Any] = Field(default_factory=dict)

    @field_validator("route_id", "route")
    @classmethod
    def _normalize_optional_text(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        text = str(value).strip()
        return text or None


__all__ = [
    "AutomationActor",
    "AutomationActorType",
    "AutomationConfigBundle",
    "AutomationDecision",
    "AutomationDecisionStatus",
    "AutomationEffect",
    "AutomationEffectKind",
    "AutomationRoute",
    "AutomationSource",
    "AutomationTenant",
    "EventCatalogEntry",
    "SubstrateEventEnvelope",
]
