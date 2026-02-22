"""Canonical domain event contracts."""

from __future__ import annotations

from datetime import datetime, timezone
import re
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

EVENT_SCHEMA_VERSION = "1.0.0"
EVENT_TYPE_PATTERN = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$")


class DomainEvent(BaseModel):
    """Canonical runtime event envelope."""

    model_config = ConfigDict(extra="forbid")

    event_type: str = Field(..., min_length=1)
    seq: int = Field(..., ge=0)
    occurred_at: datetime
    run_id: str = Field(..., min_length=1)
    schema_version: str = Field(..., min_length=1)
    payload: dict[str, Any]
    metadata: dict[str, Any] | None = None

    @field_validator("event_type")
    @classmethod
    def validate_event_type(cls, value: str) -> str:
        if not EVENT_TYPE_PATTERN.fullmatch(value):
            raise ValueError("event_type must follow '<domain>.<action>' naming.")
        return value

    @field_validator("schema_version")
    @classmethod
    def validate_schema_version(cls, value: str) -> str:
        if value != EVENT_SCHEMA_VERSION:
            raise ValueError(f"schema_version must be '{EVENT_SCHEMA_VERSION}'.")
        return value

    @field_validator("occurred_at")
    @classmethod
    def ensure_timezone_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("occurred_at must be timezone aware.")
        return value.astimezone(timezone.utc)


class EventEnvelope(BaseModel):
    """Alias of DomainEvent for envelope-oriented integrations."""

    model_config = ConfigDict(extra="forbid")

    event_type: str = Field(..., min_length=1)
    seq: int = Field(..., ge=0)
    occurred_at: datetime
    run_id: str = Field(..., min_length=1)
    schema_version: str = Field(..., min_length=1)
    payload: dict[str, Any]
    metadata: dict[str, Any] | None = None

    @field_validator("event_type")
    @classmethod
    def validate_event_type(cls, value: str) -> str:
        if not EVENT_TYPE_PATTERN.fullmatch(value):
            raise ValueError("event_type must follow '<domain>.<action>' naming.")
        return value

    @field_validator("schema_version")
    @classmethod
    def validate_schema_version(cls, value: str) -> str:
        if value != EVENT_SCHEMA_VERSION:
            raise ValueError(f"schema_version must be '{EVENT_SCHEMA_VERSION}'.")
        return value

    @field_validator("occurred_at")
    @classmethod
    def ensure_timezone_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("occurred_at must be timezone aware.")
        return value.astimezone(timezone.utc)


__all__ = ["DomainEvent", "EVENT_SCHEMA_VERSION", "EventEnvelope"]
