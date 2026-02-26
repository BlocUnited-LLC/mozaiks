"""Subscription dispatcher — YAML-driven plan/limit enforcement.

Reads ``subscription.yaml`` (or ``subscription/*.yaml``) from a workflow
directory, enforces resource limits, tracks metering, and emits business
events when thresholds are crossed.

Layer: ``core.events`` (shared runtime).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

from mozaiks.contracts.ports.business import ConsumeResult, UsageBackend
from mozaiks.core.events.dispatcher import BusinessEventDispatcher
from mozaiks.core.workflows.yaml_loader import ModularYAMLLoader

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Config data classes
# ---------------------------------------------------------------------------

@dataclass
class ResourceConfig:
    """One resource defined in ``subscription.yaml``."""

    name: str
    unit: str
    description: str = ""


@dataclass
class MeteringRule:
    """One metering / consumption rule."""

    event: str
    resource: str
    aggregation: str = "count"
    field: str | None = None


@dataclass
class PlanConfig:
    """A subscription plan parsed from YAML."""

    name: str
    limits: dict[str, int] = field(default_factory=dict)
    features: list[str] = field(default_factory=list)
    rate_limit: dict[str, int] = field(default_factory=dict)
    price: dict[str, float] = field(default_factory=dict)


@dataclass
class EnforcementConfig:
    """Enforcement behaviour parsed from YAML."""

    on_limit_reached: dict[str, str] = field(default_factory=dict)
    warning_thresholds: list[int] = field(default_factory=lambda: [80, 90, 95])
    grace_period_hours: int = 24


@dataclass
class SubscriptionConfig:
    """Fully-parsed subscription.yaml."""

    resources: dict[str, ResourceConfig] = field(default_factory=dict)
    consumes: list[MeteringRule] = field(default_factory=list)
    plans: dict[str, PlanConfig] = field(default_factory=dict)
    enforcement: EnforcementConfig = field(default_factory=EnforcementConfig)


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------

class SubscriptionDispatcher:
    """Enforces subscription limits based on YAML declarations."""

    def __init__(
        self,
        workflow_name: str,
        backend: UsageBackend,
        *,
        workflow_dir: Path | None = None,
        business_dispatcher: BusinessEventDispatcher | None = None,
    ) -> None:
        self.workflow_name = workflow_name
        self._backend = backend
        self._business = business_dispatcher or BusinessEventDispatcher.get_instance()
        self._config = SubscriptionConfig()

        if workflow_dir is not None:
            self._load(workflow_dir)

    # ------------------------------------------------------------------
    # Loading
    # ------------------------------------------------------------------

    def _load(self, workflow_dir: Path) -> None:
        loader = ModularYAMLLoader(workflow_dir)
        raw = loader.load_section("subscription")
        if not raw:
            return

        # Resources
        for key, val in raw.get("resources", {}).items():
            if isinstance(val, dict):
                self._config.resources[key] = ResourceConfig(
                    name=val.get("name", key),
                    unit=val.get("unit", "count"),
                    description=val.get("description", ""),
                )

        # Metering rules
        for rule in raw.get("consumes", []):
            if isinstance(rule, dict):
                self._config.consumes.append(
                    MeteringRule(
                        event=rule["event"],
                        resource=rule["resource"],
                        aggregation=rule.get("aggregation", "count"),
                        field=rule.get("field"),
                    )
                )

        # Plans
        for key, val in raw.get("plans", {}).items():
            if isinstance(val, dict):
                self._config.plans[key] = PlanConfig(
                    name=val.get("name", key),
                    limits=val.get("limits", {}),
                    features=val.get("features", []),
                    rate_limit=val.get("rate_limit", {}),
                    price=val.get("price", {}),
                )

        # Enforcement
        enf = raw.get("enforcement", {})
        if isinstance(enf, dict):
            self._config.enforcement = EnforcementConfig(
                on_limit_reached=enf.get("on_limit_reached", {}),
                warning_thresholds=enf.get("warning_thresholds", [80, 90, 95]),
                grace_period_hours=enf.get("grace_period_hours", 24),
            )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def check_and_consume(
        self,
        user_id: str,
        resource: str,
        amount: int = 1,
    ) -> ConsumeResult:
        """Check if *user_id* can consume *amount* of *resource*, then consume it."""

        plan_name = await self._backend.get_user_plan(user_id)
        plan = self._config.plans.get(plan_name, self._config.plans.get("free"))
        if plan is None:
            # No plans configured — allow everything.
            return ConsumeResult(allowed=True, remaining=-1)

        limit = plan.limits.get(resource, 0)

        # Unlimited
        if limit == -1:
            await self._backend.increment_usage(user_id, resource, amount)
            return ConsumeResult(allowed=True, remaining=-1)

        current = await self._backend.get_usage(user_id, resource)
        new_total = current + amount
        percent = (new_total / limit) * 100 if limit > 0 else 0

        # Warning thresholds
        prev_percent = (current / limit) * 100 if limit > 0 else 0
        for threshold in self._config.enforcement.warning_thresholds:
            if prev_percent < threshold <= percent:
                await self._business.emit(
                    "subscription.limit_warning",
                    {
                        "user_id": user_id,
                        "resource": resource,
                        "current": new_total,
                        "limit": limit,
                        "percent": percent,
                        "threshold": threshold,
                        "workflow_name": self.workflow_name,
                    },
                )

        # Over limit?
        if new_total > limit:
            behavior = self._config.enforcement.on_limit_reached.get(resource, "block")

            await self._business.emit(
                "subscription.limit_reached",
                {
                    "user_id": user_id,
                    "resource": resource,
                    "current": current,
                    "limit": limit,
                    "behavior": behavior,
                    "workflow_name": self.workflow_name,
                },
            )

            if behavior == "block":
                return ConsumeResult(
                    allowed=False,
                    remaining=0,
                    error=f"{resource} limit reached",
                )

        # Consume
        await self._backend.increment_usage(user_id, resource, amount)
        return ConsumeResult(allowed=True, remaining=max(0, limit - new_total))

    def has_feature(self, plan: str, feature: str) -> bool:
        """Return ``True`` if *plan* includes *feature*."""
        plan_config = self._config.plans.get(plan)
        if not plan_config:
            return False
        return feature in plan_config.features

    @property
    def config(self) -> SubscriptionConfig:
        """Expose parsed config for introspection / testing."""
        return self._config
