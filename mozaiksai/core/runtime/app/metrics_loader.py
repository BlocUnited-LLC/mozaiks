from __future__ import annotations

"""metrics_loader — loads app/config/metrics.yaml.

The metrics config declares app-specific analytics configuration on top of
the built-in metric registry (``mozaiksai.core.metrics.definitions``):
lifecycle funnels with product-specific stages. Apps without the file get
the default registry and no funnel — a valid non-analytics-configured app.

Schema version: mozaiks.metrics.v1

Example::

    schema_version: mozaiks.metrics.v1
    label: "My App Analytics"
    funnels:
      - funnel_id: activation
        label: Activation
        subject: actor
        steps:
          - step_id: signed_up
            label: Signed up
            event_name: user.signed_up
          - step_id: created_project
            label: Created a project
            event_name: project.created
          - step_id: paid
            label: Paid
            event_name: subscription.activated
"""

from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from mozaiksai.core.metrics.definitions import METRICS_SCHEMA_VERSION
from mozaiksai.core.metrics.funnels import FunnelDef

_CONFIG_PATH = Path("config") / "metrics.yaml"


class MetricsConfigLoadError(ValueError):
    """Raised when config/metrics.yaml exists but is invalid."""


class MetricsConfig(BaseModel):
    """Validated app analytics configuration."""

    model_config = ConfigDict(extra="forbid")

    schema_version: str
    label: str | None = None
    default_funnel_id: str | None = None
    funnels: list[FunnelDef] = Field(default_factory=list)

    @field_validator("schema_version")
    @classmethod
    def _validate_schema_version(cls, value: str) -> str:
        value = value.strip()
        if value != METRICS_SCHEMA_VERSION:
            raise ValueError(
                f"schema_version must be {METRICS_SCHEMA_VERSION!r}, got {value!r}"
            )
        return value

    @model_validator(mode="after")
    def _validate_funnel_references(self) -> MetricsConfig:
        seen: set[str] = set()
        for funnel in self.funnels:
            if funnel.funnel_id in seen:
                raise ValueError(f"duplicate funnel_id '{funnel.funnel_id}'")
            seen.add(funnel.funnel_id)
        if self.default_funnel_id is not None and self.default_funnel_id not in seen:
            raise ValueError(
                f"default_funnel_id '{self.default_funnel_id}' is not a declared funnel"
            )
        return self

    def funnel_by_id(self, funnel_id: str | None) -> FunnelDef | None:
        key = str(funnel_id or "").strip()
        for funnel in self.funnels:
            if funnel.funnel_id == key:
                return funnel
        return None

    @property
    def default_funnel(self) -> FunnelDef | None:
        if self.default_funnel_id:
            return self.funnel_by_id(self.default_funnel_id)
        return self.funnels[0] if self.funnels else None


def load_metrics_config(app_root: Path) -> MetricsConfig | None:
    """Load app/config/metrics.yaml.

    Returns:
        Parsed MetricsConfig, or None when the file does not exist (apps
        without custom analytics configuration).

    Raises:
        MetricsConfigLoadError: When the file exists but is invalid YAML or
            fails schema validation.
    """

    path = Path(app_root) / _CONFIG_PATH
    if not path.exists():
        return None

    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise MetricsConfigLoadError(f"Failed to read config/metrics.yaml: {exc}") from exc

    if not isinstance(raw, dict):
        raise MetricsConfigLoadError("config/metrics.yaml must be a YAML object")

    try:
        return MetricsConfig.model_validate(raw)
    except MetricsConfigLoadError:
        raise
    except Exception as exc:
        raise MetricsConfigLoadError(f"Invalid config/metrics.yaml: {exc}") from exc


__all__ = ["MetricsConfig", "MetricsConfigLoadError", "load_metrics_config"]
