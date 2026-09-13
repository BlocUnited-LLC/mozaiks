"""Canonical metric definitions for owner-facing app analytics.

This registry is the single deterministic authority for what a metric *is*:
its domain, unit, aggregation semantics, derivation formula, polarity, and
where its raw values come from. Every surface that renders a metric —
portfolio (World) views, per-app views, and metric drill-downs — consumes
these definitions instead of re-deriving them.

Value semantics are intentionally split along two axes:

- ``aggregation`` — how a portfolio value is produced from per-app values:
  ``additive`` metrics sum across apps; ``derived`` metrics are recomputed
  from underlying portfolio totals (never averaged across apps).
- ``temporality`` — how a period value is produced from raw signals:
  ``stock`` metrics read the latest level in the window (MRR, user counts);
  ``flow`` metrics sum activity across the window (New MRR, new users).

Raw values come from one of three sources:

- ``kpi_snapshot`` — ``kpi.<metric_id>`` snapshot events recorded through
  ``AppMetrics.record_snapshot`` by the system that owns the business fact
  (for example a billing integration recording MRR).
- ``usage_rollup`` — the automatic ``app.page_view`` / ``app.action_invoked``
  instrumentation aggregated by ``AppMetrics``.
- ``derived`` — computed from other registry metrics; no store of its own.

No fallback or fabricated values: a metric without recorded data resolves to
``None`` and surfaces as pending/insufficient in the UI.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

METRICS_SCHEMA_VERSION = "mozaiks.metrics.v1"

KPI_SNAPSHOT_EVENT_PREFIX = "kpi."

MetricDomain = Literal["revenue", "users"]
MetricUnit = Literal["currency_usd", "count", "percent", "ratio"]
MetricAggregation = Literal["additive", "derived"]
MetricTemporality = Literal["stock", "flow"]
MetricPolarity = Literal["higher_is_better", "lower_is_better", "neutral"]
MetricScope = Literal["portfolio", "app"]
MetricSource = Literal["kpi_snapshot", "usage_rollup", "derived"]
DerivationKind = Literal["ratio", "growth", "formula"]
DerivationFormula = Literal["arr", "net_new_mrr", "nrr", "grr", "retention"]


class MetricDerivation(BaseModel):
    """How a derived metric is computed from other registry metrics."""

    model_config = ConfigDict(extra="forbid")

    kind: DerivationKind
    # ratio: numerator / denominator (both computed from portfolio totals).
    numerator: str | None = None
    denominator: str | None = None
    # growth: (current - previous) / previous of the base metric.
    base: str | None = None
    # formula: a named deterministic formula implemented in portfolio math.
    formula: DerivationFormula | None = None

    @model_validator(mode="after")
    def _validate_shape(self) -> MetricDerivation:
        if self.kind == "ratio" and not (self.numerator and self.denominator):
            raise ValueError("ratio derivations require numerator and denominator metric ids")
        if self.kind == "growth" and not self.base:
            raise ValueError("growth derivations require a base metric id")
        if self.kind == "formula" and not self.formula:
            raise ValueError("formula derivations require a formula name")
        return self

    def referenced_metric_ids(self) -> list[str]:
        return [value for value in (self.numerator, self.denominator, self.base) if value]


def _default_scopes() -> list[MetricScope]:
    return ["portfolio", "app"]


class MetricDef(BaseModel):
    """One deterministic metric definition."""

    model_config = ConfigDict(extra="forbid")

    metric_id: str
    domain: MetricDomain
    label: str
    short_label: str | None = None
    description: str
    unit: MetricUnit
    aggregation: MetricAggregation
    temporality: MetricTemporality
    polarity: MetricPolarity = "higher_is_better"
    scopes: list[MetricScope] = Field(default_factory=_default_scopes)
    source: MetricSource
    derivation: MetricDerivation | None = None
    benchmarkable: bool = False
    headline: bool = False
    related: list[str] = Field(default_factory=list)

    @field_validator("metric_id")
    @classmethod
    def _validate_metric_id(cls, value: str) -> str:
        value = value.strip()
        if not value or not value.replace("_", "").isalnum() or value != value.lower():
            raise ValueError(f"metric_id must be lowercase snake_case, got {value!r}")
        return value

    @field_validator("label", "description")
    @classmethod
    def _validate_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("label and description must be non-empty")
        return value

    @model_validator(mode="after")
    def _validate_source_shape(self) -> MetricDef:
        if self.source == "derived" and self.derivation is None:
            raise ValueError(f"derived metric '{self.metric_id}' requires a derivation")
        if self.source != "derived" and self.derivation is not None:
            raise ValueError(f"metric '{self.metric_id}' declares a derivation but is not derived")
        if self.source == "derived" and self.aggregation != "derived":
            raise ValueError(f"derived metric '{self.metric_id}' must use derived aggregation")
        if not self.scopes:
            raise ValueError(f"metric '{self.metric_id}' must declare at least one scope")
        return self

    @property
    def snapshot_event_name(self) -> str | None:
        """The ``kpi.<metric_id>`` event this metric reads, when snapshot-backed."""
        if self.source != "kpi_snapshot":
            return None
        return f"{KPI_SNAPSHOT_EVENT_PREFIX}{self.metric_id}"

    @property
    def display_label(self) -> str:
        return self.short_label or self.label


class MetricRegistry(BaseModel):
    """A validated, closed collection of metric definitions."""

    model_config = ConfigDict(extra="forbid")

    schema_version: str = METRICS_SCHEMA_VERSION
    metrics: list[MetricDef]

    @model_validator(mode="after")
    def _validate_references(self) -> MetricRegistry:
        by_id: dict[str, MetricDef] = {}
        for metric in self.metrics:
            if metric.metric_id in by_id:
                raise ValueError(f"duplicate metric_id '{metric.metric_id}'")
            by_id[metric.metric_id] = metric
        for metric in self.metrics:
            references = list(metric.related)
            if metric.derivation is not None:
                references.extend(metric.derivation.referenced_metric_ids())
            for reference in references:
                if reference not in by_id:
                    raise ValueError(
                        f"metric '{metric.metric_id}' references unknown metric '{reference}'"
                    )
        return self

    def get(self, metric_id: str) -> MetricDef | None:
        for metric in self.metrics:
            if metric.metric_id == metric_id:
                return metric
        return None

    def require(self, metric_id: str) -> MetricDef:
        metric = self.get(metric_id)
        if metric is None:
            raise KeyError(f"unknown metric_id '{metric_id}'")
        return metric

    def for_domain(self, domain: MetricDomain) -> list[MetricDef]:
        return [metric for metric in self.metrics if metric.domain == domain]

    def ids(self) -> list[str]:
        return [metric.metric_id for metric in self.metrics]


def _revenue_metrics() -> list[MetricDef]:
    return [
        MetricDef(
            metric_id="mrr",
            domain="revenue",
            label="MRR",
            description=(
                "Monthly recurring revenue. Recorded as kpi.mrr snapshots by the billing "
                "system of record; the portfolio value is the sum of per-app MRR."
            ),
            unit="currency_usd",
            aggregation="additive",
            temporality="stock",
            source="kpi_snapshot",
            benchmarkable=True,
            headline=True,
            related=["arr", "mrr_growth", "net_new_mrr", "arppu"],
        ),
        MetricDef(
            metric_id="arr",
            domain="revenue",
            label="ARR",
            description="Annualized recurring revenue, derived as MRR x 12.",
            unit="currency_usd",
            aggregation="derived",
            temporality="stock",
            source="derived",
            derivation=MetricDerivation(kind="formula", formula="arr", base="mrr"),
            related=["mrr"],
        ),
        MetricDef(
            metric_id="new_mrr",
            domain="revenue",
            label="New MRR",
            description="Recurring revenue added by new subscriptions during the period.",
            unit="currency_usd",
            aggregation="additive",
            temporality="flow",
            source="kpi_snapshot",
            related=["net_new_mrr", "expansion_mrr"],
        ),
        MetricDef(
            metric_id="expansion_mrr",
            domain="revenue",
            label="Expansion MRR",
            description="Recurring revenue added by existing customers upgrading.",
            unit="currency_usd",
            aggregation="additive",
            temporality="flow",
            source="kpi_snapshot",
            related=["net_new_mrr", "nrr"],
        ),
        MetricDef(
            metric_id="contraction_mrr",
            domain="revenue",
            label="Contraction MRR",
            description="Recurring revenue lost to existing customers downgrading.",
            unit="currency_usd",
            aggregation="additive",
            temporality="flow",
            polarity="lower_is_better",
            source="kpi_snapshot",
            related=["net_new_mrr", "nrr"],
        ),
        MetricDef(
            metric_id="churned_mrr",
            domain="revenue",
            label="Churned MRR",
            description="Recurring revenue lost to cancelled subscriptions.",
            unit="currency_usd",
            aggregation="additive",
            temporality="flow",
            polarity="lower_is_better",
            source="kpi_snapshot",
            related=["net_new_mrr", "grr"],
        ),
        MetricDef(
            metric_id="net_new_mrr",
            domain="revenue",
            label="Net New MRR",
            description=(
                "New + expansion - contraction - churned MRR for the period. Explains "
                "how MRR moved."
            ),
            unit="currency_usd",
            aggregation="derived",
            temporality="flow",
            source="derived",
            derivation=MetricDerivation(kind="formula", formula="net_new_mrr"),
            headline=True,
            related=["new_mrr", "expansion_mrr", "contraction_mrr", "churned_mrr"],
        ),
        MetricDef(
            metric_id="mrr_growth",
            domain="revenue",
            label="MRR growth",
            description="Percent change in MRR versus the comparison period.",
            unit="percent",
            aggregation="derived",
            temporality="stock",
            source="derived",
            derivation=MetricDerivation(kind="growth", base="mrr"),
            benchmarkable=True,
            headline=True,
            related=["mrr", "net_new_mrr"],
        ),
        MetricDef(
            metric_id="nrr",
            domain="revenue",
            label="NRR",
            description=(
                "Net revenue retention: (starting MRR + expansion - contraction - churned) "
                "/ starting MRR, computed from portfolio totals."
            ),
            unit="percent",
            aggregation="derived",
            temporality="flow",
            source="derived",
            derivation=MetricDerivation(kind="formula", formula="nrr"),
            benchmarkable=True,
            headline=True,
            related=["grr", "expansion_mrr", "churned_mrr"],
        ),
        MetricDef(
            metric_id="grr",
            domain="revenue",
            label="GRR",
            description=(
                "Gross revenue retention: (starting MRR - contraction - churned) / "
                "starting MRR, computed from portfolio totals."
            ),
            unit="percent",
            aggregation="derived",
            temporality="flow",
            source="derived",
            derivation=MetricDerivation(kind="formula", formula="grr"),
            related=["nrr", "churned_mrr"],
        ),
        MetricDef(
            metric_id="arpu",
            domain="revenue",
            label="ARPU",
            description="Average recurring revenue per active user: MRR / active users.",
            unit="currency_usd",
            aggregation="derived",
            temporality="stock",
            source="derived",
            derivation=MetricDerivation(kind="ratio", numerator="mrr", denominator="active_users"),
            related=["arppu", "mrr", "active_users"],
        ),
        MetricDef(
            metric_id="arppu",
            domain="revenue",
            label="ARPPU",
            description="Average recurring revenue per paying user: MRR / paying users.",
            unit="currency_usd",
            aggregation="derived",
            temporality="stock",
            source="derived",
            derivation=MetricDerivation(kind="ratio", numerator="mrr", denominator="paying_users"),
            related=["arpu", "mrr", "paying_users"],
        ),
    ]


def _user_metrics() -> list[MetricDef]:
    return [
        MetricDef(
            metric_id="active_users",
            domain="users",
            label="Active users",
            description=(
                "Distinct users with recorded activity in the period, from the automatic "
                "page-view and action instrumentation."
            ),
            unit="count",
            aggregation="additive",
            temporality="stock",
            source="usage_rollup",
            benchmarkable=True,
            headline=True,
            related=["total_users", "paying_users", "user_growth"],
        ),
        MetricDef(
            metric_id="total_users",
            domain="users",
            label="Total users",
            description="All registered users, recorded as kpi.total_users snapshots.",
            unit="count",
            aggregation="additive",
            temporality="stock",
            source="kpi_snapshot",
            related=["new_users", "active_users"],
        ),
        MetricDef(
            metric_id="new_users",
            domain="users",
            label="New users",
            description="Users who signed up during the period.",
            unit="count",
            aggregation="additive",
            temporality="flow",
            source="kpi_snapshot",
            related=["total_users", "paid_conversion"],
        ),
        MetricDef(
            metric_id="paying_users",
            domain="users",
            label="Paying users",
            description="Users with an active paid subscription.",
            unit="count",
            aggregation="additive",
            temporality="stock",
            source="kpi_snapshot",
            benchmarkable=True,
            headline=True,
            related=["paid_conversion", "arppu"],
        ),
        MetricDef(
            metric_id="churned_users",
            domain="users",
            label="Churned users",
            description="Users who cancelled or lapsed during the period.",
            unit="count",
            aggregation="additive",
            temporality="flow",
            polarity="lower_is_better",
            source="kpi_snapshot",
            scopes=["app", "portfolio"],
            related=["churn_rate", "retention"],
        ),
        MetricDef(
            metric_id="user_growth",
            domain="users",
            label="User growth",
            description="Percent change in active users versus the comparison period.",
            unit="percent",
            aggregation="derived",
            temporality="stock",
            source="derived",
            derivation=MetricDerivation(kind="growth", base="active_users"),
            benchmarkable=True,
            headline=True,
            related=["active_users", "new_users"],
        ),
        MetricDef(
            metric_id="paid_conversion",
            domain="users",
            label="Paid conversion",
            description=(
                "Paying users as a share of total users, computed from portfolio totals "
                "rather than averaging per-app rates."
            ),
            unit="percent",
            aggregation="derived",
            temporality="stock",
            source="derived",
            derivation=MetricDerivation(
                kind="ratio", numerator="paying_users", denominator="total_users"
            ),
            benchmarkable=True,
            headline=True,
            related=["paying_users", "total_users"],
        ),
        MetricDef(
            metric_id="churn_rate",
            domain="users",
            label="Churn",
            description=(
                "Churned users during the period as a share of total users at the start "
                "of the period."
            ),
            unit="percent",
            aggregation="derived",
            temporality="flow",
            polarity="lower_is_better",
            source="derived",
            derivation=MetricDerivation(
                kind="ratio", numerator="churned_users", denominator="total_users"
            ),
            benchmarkable=True,
            related=["churned_users", "retention"],
        ),
        MetricDef(
            metric_id="retention",
            domain="users",
            label="Retention",
            description=(
                "Share of the starting user base retained through the period: "
                "100% minus the period churn rate."
            ),
            unit="percent",
            aggregation="derived",
            temporality="flow",
            source="derived",
            derivation=MetricDerivation(kind="formula", formula="retention"),
            benchmarkable=True,
            related=["churn_rate", "active_users"],
        ),
    ]


def build_default_metric_registry() -> MetricRegistry:
    """The canonical registry every Mozaiks app starts from."""

    return MetricRegistry(metrics=[*_revenue_metrics(), *_user_metrics()])


__all__ = [
    "KPI_SNAPSHOT_EVENT_PREFIX",
    "METRICS_SCHEMA_VERSION",
    "MetricDef",
    "MetricDerivation",
    "MetricDomain",
    "MetricRegistry",
    "build_default_metric_registry",
]
