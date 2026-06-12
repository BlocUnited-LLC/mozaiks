from __future__ import annotations

"""subscriptions_loader — loads app/config/subscriptions.yaml.

The subscriptions config is the canonical plan catalog for SaaS apps.
It declares what subscription plans exist and which capability_ids each
plan grants. At platform startup this config is loaded and passed to the
OSS ConfiguredEntitlementAdapter, which is wired into ModuleExecutor so that
``entitlement_gate`` fields on module actions are enforced.

Non-SaaS apps omit the file entirely. When absent, the platform falls back
to NoOpEntitlementAdapter and all entitlement gates pass unconditionally.

Schema version: mozaiks.subscriptions.v1

Example::

    schema_version: mozaiks.subscriptions.v1
    label: "My SaaS App Plans"
    default_plan_id: free
    assignment_store:
      data_alias: billing.subscriptions
    plans:
      - plan_id: free
        label: Free
        capabilities: []
      - plan_id: pro
        label: Pro
        capabilities:
          - wallet.view
          - analytics.dashboard
        usage_limits:
          - meter_id: ai_tokens
            label: AI tokens
            unit: tokens
            monthly_limit: 100000
"""

import re
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------

_PLAN_ID_RE = re.compile(r"^[a-z0-9_-]+$")
_CAPABILITY_ID_RE = re.compile(r"^[a-z0-9_.]+$")
_METER_ID_RE = re.compile(r"^[a-z0-9_.-]+$")
_DATA_ALIAS_RE = re.compile(r"^[a-z0-9_.-]+$")
_FIELD_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.]*$")

_CONFIG_PATH = Path("config") / "subscriptions.yaml"


# ---------------------------------------------------------------------------
# Error
# ---------------------------------------------------------------------------


class SubscriptionsLoadError(ValueError):
    """Raised when config/subscriptions.yaml is present but invalid."""


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class UsageLimitDef(BaseModel):
    """A display/enforcement hint for metered plan usage."""

    model_config = ConfigDict(extra="forbid")

    meter_id: str
    label: str | None = None
    unit: Literal["tokens", "requests", "credits"] = "tokens"
    monthly_limit: int | None = Field(default=None, ge=0)
    capability_id: str | None = None

    @field_validator("meter_id")
    @classmethod
    def _validate_meter_id(cls, value: str) -> str:
        value = value.strip()
        if not value or not _METER_ID_RE.match(value):
            raise ValueError(
                f"meter_id must match [a-z0-9_.-]+, got {value!r}"
            )
        return value

    @field_validator("capability_id")
    @classmethod
    def _validate_capability_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value:
            return None
        if not _CAPABILITY_ID_RE.match(value):
            raise ValueError(
                f"capability_id must match [a-z0-9_.]+, got {value!r}"
            )
        return value


class PlanDef(BaseModel):
    """A single subscription plan with its granted capabilities."""

    model_config = ConfigDict(extra="forbid")

    plan_id: str
    label: str
    description: str | None = None
    capabilities: list[str] = Field(default_factory=list)
    usage_limits: list[UsageLimitDef] = Field(default_factory=list)

    @field_validator("plan_id")
    @classmethod
    def _validate_plan_id(cls, value: str) -> str:
        value = value.strip()
        if not value or not _PLAN_ID_RE.match(value):
            raise ValueError(
                f"plan_id must match [a-z0-9_-]+, got {value!r}"
            )
        return value

    @field_validator("label")
    @classmethod
    def _validate_label(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("label must be non-empty")
        return value

    @field_validator("capabilities", mode="before")
    @classmethod
    def _validate_capabilities(cls, value: object) -> list[str]:
        if value is None:
            return []
        if not isinstance(value, list):
            raise ValueError("capabilities must be a list")
        result: list[str] = []
        for raw in value:
            cap = str(raw).strip()
            if not cap:
                continue
            if not _CAPABILITY_ID_RE.match(cap):
                raise ValueError(
                    f"capability_id must match [a-z0-9_.]+, got {cap!r}"
                )
            result.append(cap)
        return result


class SubscriptionAssignmentStoreDef(BaseModel):
    """Data-contract backed subscription assignment store definition.

    The configured entitlement adapter reads assignment records from this alias
    at module dispatch time. Provider-specific payment integrations remain
    app-owned; this declaration only describes where active plan assignment
    state is stored and which fields carry capability data.
    """

    model_config = ConfigDict(extra="forbid")

    data_alias: str = "subscriptions.assignments"
    app_id_field: str = "app_id"
    tenant_id_field: str | None = "tenant_id"
    workspace_id_field: str | None = None
    user_id_field: str | None = None
    plan_id_field: str = "plan_id"
    status_field: str = "status"
    starts_at_field: str | None = "starts_at"
    expires_at_field: str | None = "expires_at"
    capabilities_field: str | None = "granted_capabilities"
    plan_snapshot_field: str | None = "plan_snapshot"
    active_statuses: list[str] = Field(default_factory=lambda: ["active", "pending", "trialing"])

    @field_validator("data_alias")
    @classmethod
    def _validate_data_alias(cls, value: str) -> str:
        value = value.strip()
        if not value or not _DATA_ALIAS_RE.match(value):
            raise ValueError(
                f"data_alias must match [a-z0-9_.-]+, got {value!r}"
            )
        return value

    @field_validator(
        "app_id_field",
        "tenant_id_field",
        "workspace_id_field",
        "user_id_field",
        "plan_id_field",
        "status_field",
        "starts_at_field",
        "expires_at_field",
        "capabilities_field",
        "plan_snapshot_field",
    )
    @classmethod
    def _validate_field_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value:
            return None
        if not _FIELD_NAME_RE.match(value):
            raise ValueError(
                f"field names must match [A-Za-z_][A-Za-z0-9_.]*, got {value!r}"
            )
        return value

    @field_validator("active_statuses", mode="before")
    @classmethod
    def _validate_active_statuses(cls, value: object) -> list[str]:
        if value is None:
            return ["active", "pending", "trialing"]
        if not isinstance(value, list):
            raise ValueError("active_statuses must be a list")
        statuses: list[str] = []
        for raw in value:
            status = str(raw or "").strip().lower()
            if not status:
                continue
            if not _PLAN_ID_RE.match(status):
                raise ValueError(
                    f"status values must match [a-z0-9_-]+, got {status!r}"
                )
            statuses.append(status)
        if not statuses:
            raise ValueError("active_statuses must be non-empty")
        return statuses


class SubscriptionsConfig(BaseModel):
    """Parsed and validated contents of app/config/subscriptions.yaml."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["mozaiks.subscriptions.v1"]
    label: str
    default_plan_id: str
    assignment_store: SubscriptionAssignmentStoreDef | None = None
    plans: list[PlanDef]

    @field_validator("label")
    @classmethod
    def _validate_label(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("label must be non-empty")
        return value

    @model_validator(mode="after")
    def _validate_plan_catalog(self) -> "SubscriptionsConfig":
        if not self.plans:
            raise ValueError("plans must be non-empty")
        plan_ids = [p.plan_id for p in self.plans]
        if len(plan_ids) != len(set(plan_ids)):
            raise ValueError("plan_ids must be unique")
        if self.default_plan_id not in set(plan_ids):
            raise ValueError(
                f"default_plan_id {self.default_plan_id!r} must reference a "
                f"declared plan_id; known plan_ids: {plan_ids}"
            )
        return self

    def capabilities_for_plan(self, plan_id: str) -> frozenset[str]:
        """Return the capability_ids granted by plan_id.

        Falls back to the default plan when plan_id is unknown.
        Returns an empty frozenset when the default plan itself has no
        capabilities declared.
        """
        for plan in self.plans:
            if plan.plan_id == plan_id:
                return frozenset(plan.capabilities)
        # Unknown plan_id — fall back to the default plan.
        for plan in self.plans:
            if plan.plan_id == self.default_plan_id:
                return frozenset(plan.capabilities)
        return frozenset()


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------


def load_subscriptions_config(app_root: Path) -> SubscriptionsConfig | None:
    """Load app/config/subscriptions.yaml.

    Returns:
        Parsed SubscriptionsConfig, or None when the file does not exist
        (non-SaaS apps).

    Raises:
        SubscriptionsLoadError: When the file exists but is invalid YAML
            or fails schema validation.
    """
    path = Path(app_root) / _CONFIG_PATH
    if not path.exists():
        return None

    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise SubscriptionsLoadError(
            f"Failed to read config/subscriptions.yaml: {exc}"
        ) from exc

    if not isinstance(raw, dict):
        raise SubscriptionsLoadError(
            "config/subscriptions.yaml must be a YAML object"
        )

    try:
        return SubscriptionsConfig.model_validate(raw)
    except Exception as exc:
        raise SubscriptionsLoadError(
            f"Invalid config/subscriptions.yaml: {exc}"
        ) from exc


__all__ = [
    "PlanDef",
    "SubscriptionAssignmentStoreDef",
    "SubscriptionsConfig",
    "SubscriptionsLoadError",
    "UsageLimitDef",
    "load_subscriptions_config",
]
