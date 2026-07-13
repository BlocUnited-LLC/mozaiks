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
        token_allowances:
          - wallet_id: ai_tokens
            amount: 100000
            cadence: monthly
    token_wallets:
      - wallet_id: ai_tokens
        label: AI token balance
        unit: tokens
        usage_meter_id: ai_tokens
        scope: user
        auto_debit_usage: true
    usage_charge_policies:
      - meter_id: ai_tokens
        label: AI usage
        source: runtime_llm_usage
        basis: provider_cost_usd
        markup_percent: 35
        rounding: cent
    pricing_catalog:
      default_group_id: platform
      groups:
        - group_id: platform
          label: Platform
          description: App access, collaboration, and hosted operations.
          plan_ids: [free, pro]
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
_WALLET_ID_RE = re.compile(r"^[a-z0-9_.-]+$")
_DATA_ALIAS_RE = re.compile(r"^[a-z0-9_.-]+$")
_FIELD_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.]*$")
_CATALOG_ID_RE = re.compile(r"^[a-z0-9_-]+$")
_CAPABILITY_GROUP_RE = re.compile(r"^[a-z0-9_.-]+$")

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


class TokenWalletDef(BaseModel):
    """A provider-neutral token wallet exposed by the runtime.

    Wallets define the spendable unit and scope. They do not define payment
    providers, prices, checkout behavior, or hosted-product monetization.
    """

    model_config = ConfigDict(extra="forbid")

    wallet_id: str = "ai_tokens"
    label: str | None = None
    unit: Literal["tokens", "credits"] = "tokens"
    usage_meter_id: str | None = "ai_tokens"
    scope: Literal["user", "tenant"] = "user"
    auto_debit_usage: bool = False
    allow_negative_balance: bool = False

    @field_validator("wallet_id")
    @classmethod
    def _validate_wallet_id(cls, value: str) -> str:
        value = value.strip()
        if not value or not _WALLET_ID_RE.match(value):
            raise ValueError(
                f"wallet_id must match [a-z0-9_.-]+, got {value!r}"
            )
        return value

    @field_validator("label")
    @classmethod
    def _validate_label(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        return value or None

    @field_validator("usage_meter_id")
    @classmethod
    def _validate_usage_meter_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value:
            return None
        if not _METER_ID_RE.match(value):
            raise ValueError(
                f"usage_meter_id must match [a-z0-9_.-]+, got {value!r}"
            )
        return value


class TokenAllowanceDef(BaseModel):
    """Token amount granted by a subscription plan.

    Runtime code can materialize these as idempotent wallet allocation entries.
    """

    model_config = ConfigDict(extra="forbid")

    wallet_id: str = "ai_tokens"
    amount: int = Field(ge=0)
    cadence: Literal["monthly", "one_time", "manual"] = "monthly"
    label: str | None = None

    @field_validator("wallet_id")
    @classmethod
    def _validate_wallet_id(cls, value: str) -> str:
        value = value.strip()
        if not value or not _WALLET_ID_RE.match(value):
            raise ValueError(
                f"wallet_id must match [a-z0-9_.-]+, got {value!r}"
            )
        return value

    @field_validator("label")
    @classmethod
    def _validate_label(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        return value or None


class UsageChargePolicyDef(BaseModel):
    """Provider-neutral customer usage charge policy.

    This describes how an app estimates customer-facing usage charges from
    measured runtime usage. It does not create payment provider prices,
    invoices, checkout sessions, or authoritative settlement records.
    """

    model_config = ConfigDict(extra="forbid")

    meter_id: str = "ai_tokens"
    label: str | None = None
    source: Literal["runtime_llm_usage"] = "runtime_llm_usage"
    basis: Literal["provider_cost_usd", "tokens"] = "provider_cost_usd"
    markup_percent: float = Field(default=0.0, ge=0)
    unit_price_usd_per_1k: float | None = Field(default=None, ge=0)
    minimum_charge_usd: float = Field(default=0.0, ge=0)
    rounding: Literal["none", "cent", "micro_usd"] = "cent"

    @field_validator("meter_id")
    @classmethod
    def _validate_meter_id(cls, value: str) -> str:
        value = value.strip()
        if not value or not _METER_ID_RE.match(value):
            raise ValueError(
                f"meter_id must match [a-z0-9_.-]+, got {value!r}"
            )
        return value

    @field_validator("label")
    @classmethod
    def _validate_label(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        return value or None

    @model_validator(mode="after")
    def _validate_charge_basis(self) -> UsageChargePolicyDef:
        if self.basis == "tokens" and self.unit_price_usd_per_1k is None:
            raise ValueError(
                "usage_charge_policies with basis='tokens' must set unit_price_usd_per_1k"
            )
        if self.basis == "provider_cost_usd" and self.unit_price_usd_per_1k is not None:
            raise ValueError(
                "unit_price_usd_per_1k is only valid when basis='tokens'"
            )
        return self


class PlanDef(BaseModel):
    """A single subscription plan with its granted capabilities."""

    model_config = ConfigDict(extra="forbid")

    plan_id: str
    label: str
    description: str | None = None
    capabilities: list[str] = Field(default_factory=list)
    usage_limits: list[UsageLimitDef] = Field(default_factory=list)
    token_allowances: list[TokenAllowanceDef] = Field(default_factory=list)

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


class PricingCatalogGroupDef(BaseModel):
    """Display grouping for public pricing and plan comparison surfaces.

    Pricing catalog groups are UI metadata only. They do not grant capabilities,
    create prices, or replace the plan catalog. Runtime entitlement enforcement
    still reads ``plans[].capabilities`` and active assignment records.
    """

    model_config = ConfigDict(extra="forbid")

    group_id: str
    label: str
    description: str | None = None
    kind: Literal["subscription", "service", "add_on", "mixed"] = "subscription"
    plan_ids: list[str] = Field(default_factory=list)
    capability_groups: list[str] = Field(default_factory=list)
    add_on_ids: list[str] = Field(default_factory=list)

    @field_validator("group_id")
    @classmethod
    def _validate_group_id(cls, value: str) -> str:
        value = value.strip()
        if not value or not _CATALOG_ID_RE.match(value):
            raise ValueError(
                f"group_id must match [a-z0-9_-]+, got {value!r}"
            )
        return value

    @field_validator("label")
    @classmethod
    def _validate_label(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("label must be non-empty")
        return value

    @field_validator("description")
    @classmethod
    def _validate_description(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        return value or None

    @field_validator("plan_ids", mode="before")
    @classmethod
    def _validate_plan_ids(cls, value: object) -> list[str]:
        if value is None:
            return []
        if not isinstance(value, list):
            raise ValueError("plan_ids must be a list")
        result: list[str] = []
        for raw in value:
            plan_id = str(raw or "").strip()
            if not plan_id:
                continue
            if not _PLAN_ID_RE.match(plan_id):
                raise ValueError(
                    f"plan_id must match [a-z0-9_-]+, got {plan_id!r}"
                )
            result.append(plan_id)
        return result

    @field_validator("capability_groups", mode="before")
    @classmethod
    def _validate_capability_groups(cls, value: object) -> list[str]:
        if value is None:
            return []
        if not isinstance(value, list):
            raise ValueError("capability_groups must be a list")
        result: list[str] = []
        for raw in value:
            group = str(raw or "").strip()
            if not group:
                continue
            if not _CAPABILITY_GROUP_RE.match(group):
                raise ValueError(
                    f"capability_group must match [a-z0-9_.-]+, got {group!r}"
                )
            result.append(group)
        return result

    @field_validator("add_on_ids", mode="before")
    @classmethod
    def _validate_add_on_ids(cls, value: object) -> list[str]:
        if value is None:
            return []
        if not isinstance(value, list):
            raise ValueError("add_on_ids must be a list")
        result: list[str] = []
        for raw in value:
            add_on_id = str(raw or "").strip()
            if not add_on_id:
                continue
            if not _CATALOG_ID_RE.match(add_on_id):
                raise ValueError(
                    f"add_on_id must match [a-z0-9_-]+, got {add_on_id!r}"
                )
            result.append(add_on_id)
        return result


class PricingCatalogDef(BaseModel):
    """Optional display catalog for grouping plans into pricing tabs."""

    model_config = ConfigDict(extra="forbid")

    default_group_id: str | None = None
    groups: list[PricingCatalogGroupDef] = Field(default_factory=list)

    @field_validator("default_group_id")
    @classmethod
    def _validate_default_group_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value:
            return None
        if not _CATALOG_ID_RE.match(value):
            raise ValueError(
                f"default_group_id must match [a-z0-9_-]+, got {value!r}"
            )
        return value

    @model_validator(mode="after")
    def _validate_groups(self) -> PricingCatalogDef:
        if not self.groups:
            raise ValueError("pricing_catalog.groups must be non-empty")
        group_ids = [group.group_id for group in self.groups]
        if len(group_ids) != len(set(group_ids)):
            raise ValueError("pricing_catalog group_ids must be unique")
        if self.default_group_id and self.default_group_id not in set(group_ids):
            raise ValueError(
                f"default_group_id {self.default_group_id!r} must reference a "
                f"declared pricing_catalog group_id; known group_ids: {group_ids}"
            )
        return self


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
    token_wallets: list[TokenWalletDef] = Field(default_factory=list)
    usage_charge_policies: list[UsageChargePolicyDef] = Field(default_factory=list)
    pricing_catalog: PricingCatalogDef | None = None
    plans: list[PlanDef]

    @field_validator("label")
    @classmethod
    def _validate_label(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("label must be non-empty")
        return value

    @model_validator(mode="after")
    def _validate_plan_catalog(self) -> SubscriptionsConfig:
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
        wallet_ids = [wallet.wallet_id for wallet in self.token_wallets]
        if len(wallet_ids) != len(set(wallet_ids)):
            raise ValueError("token_wallet wallet_ids must be unique")
        usage_charge_meter_ids = [policy.meter_id for policy in self.usage_charge_policies]
        if len(usage_charge_meter_ids) != len(set(usage_charge_meter_ids)):
            raise ValueError("usage_charge_policies meter_ids must be unique")
        declared_wallet_ids = set(wallet_ids)
        for plan in self.plans:
            for allowance in plan.token_allowances:
                if declared_wallet_ids and allowance.wallet_id not in declared_wallet_ids:
                    raise ValueError(
                        f"token_allowance wallet_id {allowance.wallet_id!r} must reference token_wallets"
                    )
        if self.pricing_catalog:
            declared_plan_ids = set(plan_ids)
            for group in self.pricing_catalog.groups:
                unknown_plan_ids = [
                    plan_id
                    for plan_id in group.plan_ids
                    if plan_id not in declared_plan_ids
                ]
                if unknown_plan_ids:
                    raise ValueError(
                        f"pricing_catalog group {group.group_id!r} references "
                        f"unknown plan_ids: {unknown_plan_ids}"
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

    def plan_by_id(self, plan_id: str | None) -> PlanDef:
        """Return a declared plan, falling back to the configured default."""

        requested = str(plan_id or "").strip()
        for plan in self.plans:
            if plan.plan_id == requested:
                return plan
        for plan in self.plans:
            if plan.plan_id == self.default_plan_id:
                return plan
        return self.plans[0]

    def token_wallet_by_id(self, wallet_id: str) -> TokenWalletDef | None:
        wallet_key = str(wallet_id or "").strip()
        for wallet in self.token_wallets:
            if wallet.wallet_id == wallet_key:
                return wallet
        return None

    def usage_charge_policy_by_meter_id(self, meter_id: str) -> UsageChargePolicyDef | None:
        meter_key = str(meter_id or "").strip()
        for policy in self.usage_charge_policies:
            if policy.meter_id == meter_key:
                return policy
        return None


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
    "PricingCatalogDef",
    "PricingCatalogGroupDef",
    "SubscriptionAssignmentStoreDef",
    "SubscriptionsConfig",
    "SubscriptionsLoadError",
    "TokenAllowanceDef",
    "TokenWalletDef",
    "UsageChargePolicyDef",
    "UsageLimitDef",
    "load_subscriptions_config",
]
