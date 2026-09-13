"""Schema-driven funnel definitions for owner analytics.

Apps declare their own lifecycle funnels in ``app/config/metrics.yaml``
instead of being forced into one hard-coded SaaS funnel. A funnel is an
ordered list of stages, each bound to a metric event name recorded through
``ctx.metrics.track`` (or the automatic usage instrumentation). Stage counts
are distinct subjects — actors, sessions, or attribution ids — so conversion
percentages compare populations, not raw event volume.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

FunnelSubject = Literal["actor", "session", "attribution"]

_SUBJECT_FIELDS: dict[str, str] = {
    "actor": "actor_id",
    "session": "session_id",
    "attribution": "attribution_id",
}


class FunnelStepDef(BaseModel):
    """One funnel stage bound to a recorded metric event."""

    model_config = ConfigDict(extra="forbid")

    step_id: str
    label: str
    event_name: str

    @field_validator("step_id")
    @classmethod
    def _validate_step_id(cls, value: str) -> str:
        value = value.strip()
        if not value or value != value.lower() or not value.replace("_", "").isalnum():
            raise ValueError(f"step_id must be lowercase snake_case, got {value!r}")
        return value

    @field_validator("label")
    @classmethod
    def _validate_label(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("funnel step label must be non-empty")
        return value

    @field_validator("event_name")
    @classmethod
    def _validate_event_name(cls, value: str) -> str:
        # Deferred import: app_metrics pulls in the persistence layer, which
        # transitively imports the app loader that loads funnel configs.
        from mozaiksai.core.metrics.app_metrics import normalize_metric_event_name

        return normalize_metric_event_name(value)


class FunnelDef(BaseModel):
    """An ordered, app-declared lifecycle funnel."""

    model_config = ConfigDict(extra="forbid")

    funnel_id: str
    label: str
    description: str | None = None
    subject: FunnelSubject = "actor"
    steps: list[FunnelStepDef] = Field(min_length=2)

    @field_validator("funnel_id")
    @classmethod
    def _validate_funnel_id(cls, value: str) -> str:
        value = value.strip()
        if not value or value != value.lower() or not value.replace("_", "").isalnum():
            raise ValueError(f"funnel_id must be lowercase snake_case, got {value!r}")
        return value

    @field_validator("label")
    @classmethod
    def _validate_label(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("funnel label must be non-empty")
        return value

    @model_validator(mode="after")
    def _validate_steps(self) -> FunnelDef:
        seen: set[str] = set()
        for step in self.steps:
            if step.step_id in seen:
                raise ValueError(
                    f"funnel '{self.funnel_id}' has duplicate step_id '{step.step_id}'"
                )
            seen.add(step.step_id)
        return self

    @property
    def subject_field(self) -> str:
        """The metric-event field distinct subjects are counted on."""

        return _SUBJECT_FIELDS[self.subject]


__all__ = ["FunnelDef", "FunnelStepDef", "FunnelSubject"]
