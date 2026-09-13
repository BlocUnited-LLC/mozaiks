"""Owner-scoped portfolio and per-app analytics assembly.

This service turns raw metric signals into the deterministic payloads the
Studio World/App analytics surfaces render. It deliberately does **not**
resolve ownership: callers (the Studio host) pass the app records the
authenticated owner is allowed to see, resolved through the app registry.
The service then reads each app's own metric store and applies the registry
aggregation semantics from ``portfolio.py``.

Data sources per metric (see ``definitions.py``):

- ``kpi_snapshot`` metrics read ``kpi.<metric_id>`` events recorded through
  ``AppMetrics.record_snapshot`` by whichever system owns the business fact.
- ``usage_rollup`` metrics read the automatic usage instrumentation.
- ``derived`` metrics are computed from the others — after portfolio
  summation, so rates come from totals, never per-app averages.

Missing data stays ``None`` end to end; nothing is fabricated.
"""

from __future__ import annotations

import asyncio
import logging
from types import SimpleNamespace
from typing import Any, Protocol

from mozaiksai.core.metrics.app_metrics import AppMetrics
from mozaiksai.core.metrics.definitions import (
    MetricRegistry,
    build_default_metric_registry,
)
from mozaiksai.core.metrics.insights import build_portfolio_insights
from mozaiksai.core.metrics.periods import PeriodWindow
from mozaiksai.core.metrics.portfolio import (
    derive_metric_value,
    metric_value_payload,
    portfolio_median,
    registry_payload,
    sum_available,
)

logger = logging.getLogger(__name__)

# Metrics whose daily series the trend panels can render. Derived series
# (arr, net_new_mrr) are computed from these deterministically.
SERIES_BASE_METRIC_IDS = (
    "mrr",
    "new_mrr",
    "expansion_mrr",
    "contraction_mrr",
    "churned_mrr",
    "paying_users",
    "new_users",
    "active_users",
)
SERIES_METRIC_IDS = ("mrr", "arr", "net_new_mrr", "active_users", "paying_users", "new_users")

_BRIDGE_EPSILON = 0.005


class MetricsFactory(Protocol):
    def __call__(self, app_id: str) -> AppMetrics: ...


def _default_metrics_factory(app_id: str) -> AppMetrics:
    ctx = SimpleNamespace(
        app_id=app_id,
        tenant_id=None,
        workspace_id=None,
        user_id=None,
        correlation_id=None,
        module_id="owner_analytics",
        action_id=None,
    )
    return AppMetrics(ctx)


def _empty_values(registry: MetricRegistry) -> dict[str, dict[str, Any]]:
    return {
        metric.metric_id: metric_value_payload(metric, None, None)
        for metric in registry.metrics
    }


class _AppSignals:
    """Raw per-app metric signals for one period window."""

    def __init__(self) -> None:
        self.current: dict[str, float | None] = {}
        self.previous: dict[str, float | None] = {}
        self.starting_mrr: float | None = None
        self.previous_starting_mrr: float | None = None
        self.series: dict[str, list[dict[str, Any]]] = {}
        self.error: bool = False


class OwnerAnalyticsService:
    """Deterministic analytics assembly over owner-provided app records."""

    def __init__(
        self,
        *,
        registry: MetricRegistry | None = None,
        metrics_factory: MetricsFactory | None = None,
    ) -> None:
        self.registry = registry or build_default_metric_registry()
        self._metrics_factory = metrics_factory or _default_metrics_factory

    # ── raw signal collection ────────────────────────────────────────────

    async def _latest_value(self, metrics: AppMetrics, event_name: str, until: Any) -> float | None:
        result = await metrics.summarize_values(
            event_names=event_name, until=until, group_by=None
        )
        groups = result.get("groups") or []
        if not groups:
            return None
        latest = groups[0].get("latest")
        return None if latest is None else float(latest)

    async def _flow_sum(
        self, metrics: AppMetrics, event_name: str, since: Any, until: Any
    ) -> float | None:
        result = await metrics.summarize_values(
            event_names=event_name, since=since, until=until, group_by=None
        )
        groups = result.get("groups") or []
        if not groups or int(groups[0].get("count") or 0) == 0:
            return None
        return float(groups[0].get("sum") or 0.0)

    async def _collect_app_signals(
        self, app_id: str, window: PeriodWindow, *, include_series: bool
    ) -> _AppSignals:
        signals = _AppSignals()
        metrics = self._metrics_factory(app_id)

        for metric in self.registry.metrics:
            if metric.source != "kpi_snapshot":
                continue
            event_name = metric.snapshot_event_name
            if not event_name:
                continue
            if metric.temporality == "stock":
                signals.current[metric.metric_id] = await self._latest_value(
                    metrics, event_name, window.until
                )
                signals.previous[metric.metric_id] = await self._latest_value(
                    metrics, event_name, window.previous_until
                )
            else:
                signals.current[metric.metric_id] = await self._flow_sum(
                    metrics, event_name, window.since, window.until
                )
                signals.previous[metric.metric_id] = await self._flow_sum(
                    metrics, event_name, window.previous_since, window.previous_until
                )
            if include_series and metric.metric_id in SERIES_BASE_METRIC_IDS:
                signals.series[metric.metric_id] = await metrics.value_series(
                    event_name,
                    since=window.since,
                    until=window.until,
                    reducer="last" if metric.temporality == "stock" else "sum",
                )

        # Activity-derived active users: distinct actors over the window, with
        # "never instrumented" distinguished from "no activity this period".
        ever = await metrics.summarize()
        if int(ever.get("total") or 0) > 0:
            signals.current["active_users"] = float(
                await metrics.active_subjects(since=window.since, until=window.until)
            )
            signals.previous["active_users"] = float(
                await metrics.active_subjects(
                    since=window.previous_since, until=window.previous_until
                )
            )
            if include_series:
                rollups = await metrics.usage_rollup(since=window.since, until=window.until)
                signals.series["active_users"] = [
                    {"period_start": row["period_start"], "value": float(row["active_users"])}
                    for row in rollups
                ]
        else:
            signals.current["active_users"] = None
            signals.previous["active_users"] = None

        # MRR level at the start of each window, for NRR/GRR.
        signals.starting_mrr = await self._latest_value(metrics, "kpi.mrr", window.since)
        signals.previous_starting_mrr = await self._latest_value(
            metrics, "kpi.mrr", window.previous_since
        )
        return signals

    async def _safe_collect(
        self, app_id: str, window: PeriodWindow, *, include_series: bool
    ) -> _AppSignals:
        try:
            return await self._collect_app_signals(
                app_id, window, include_series=include_series
            )
        except Exception:
            logger.warning("OWNER_ANALYTICS_APP_READ_FAILED app_id=%s", app_id, exc_info=True)
            signals = _AppSignals()
            signals.error = True
            return signals

    # ── value derivation ─────────────────────────────────────────────────

    def _finalize_values(self, signals: _AppSignals) -> dict[str, dict[str, Any]]:
        values: dict[str, dict[str, Any]] = {}
        for metric in self.registry.metrics:
            current, previous = derive_metric_value(
                metric,
                current_totals=signals.current,
                previous_totals=signals.previous,
                starting_mrr=signals.starting_mrr,
                previous_starting_mrr=signals.previous_starting_mrr,
            )
            values[metric.metric_id] = metric_value_payload(metric, current, previous)
        return values

    def _sum_signals(self, per_app: list[_AppSignals]) -> _AppSignals:
        combined = _AppSignals()
        base_ids = [m.metric_id for m in self.registry.metrics if m.source != "derived"]
        for metric_id in base_ids:
            combined.current[metric_id] = sum_available(
                [signals.current.get(metric_id) for signals in per_app]
            )
            combined.previous[metric_id] = sum_available(
                [signals.previous.get(metric_id) for signals in per_app]
            )
        combined.starting_mrr = sum_available([s.starting_mrr for s in per_app])
        combined.previous_starting_mrr = sum_available(
            [s.previous_starting_mrr for s in per_app]
        )
        combined.series = self._sum_series(per_app)
        return combined

    def _sum_series(self, per_app: list[_AppSignals]) -> dict[str, list[dict[str, Any]]]:
        summed: dict[str, list[dict[str, Any]]] = {}
        for metric_id in SERIES_BASE_METRIC_IDS:
            buckets: dict[str, float] = {}
            seen_any = False
            for signals in per_app:
                for point in signals.series.get(metric_id) or []:
                    value = point.get("value")
                    if value is None:
                        continue
                    seen_any = True
                    key = str(point.get("period_start"))
                    buckets[key] = buckets.get(key, 0.0) + float(value)
            if seen_any:
                summed[metric_id] = [
                    {"period_start": key, "value": buckets[key]} for key in sorted(buckets)
                ]
        return summed

    def _derived_series(
        self, series: dict[str, list[dict[str, Any]]]
    ) -> dict[str, list[dict[str, Any]]]:
        result: dict[str, list[dict[str, Any]]] = {
            metric_id: points for metric_id, points in series.items()
            if metric_id in SERIES_METRIC_IDS
        }
        mrr_series = series.get("mrr")
        if mrr_series:
            result["arr"] = [
                {"period_start": p["period_start"], "value": None if p["value"] is None else p["value"] * 12.0}
                for p in mrr_series
            ]
        flow_ids = ("new_mrr", "expansion_mrr", "contraction_mrr", "churned_mrr")
        if any(series.get(metric_id) for metric_id in flow_ids):
            buckets: dict[str, float] = {}
            for metric_id, sign in zip(flow_ids, (1.0, 1.0, -1.0, -1.0), strict=True):
                for point in series.get(metric_id) or []:
                    if point.get("value") is None:
                        continue
                    key = str(point.get("period_start"))
                    buckets[key] = buckets.get(key, 0.0) + sign * float(point["value"])
            result["net_new_mrr"] = [
                {"period_start": key, "value": buckets[key]} for key in sorted(buckets)
            ]
        return result

    def _domain_availability(self, values: dict[str, dict[str, Any]]) -> dict[str, str]:
        availability: dict[str, str] = {}
        for domain in ("revenue", "users"):
            headline = [
                metric for metric in self.registry.for_domain(domain) if metric.headline
            ]
            available = [
                metric for metric in headline if values.get(metric.metric_id, {}).get("available")
            ]
            if not available:
                availability[domain] = "none"
            elif len(available) == len(headline):
                availability[domain] = "full"
            else:
                availability[domain] = "partial"
        return availability

    def _benchmarks(
        self, app_values: list[dict[str, dict[str, Any]]]
    ) -> dict[str, dict[str, Any]]:
        benchmarks: dict[str, dict[str, Any]] = {}
        for metric in self.registry.metrics:
            if not metric.benchmarkable:
                continue
            values = [
                row.get(metric.metric_id, {}).get("value") for row in app_values
            ]
            median_value = portfolio_median(values)
            benchmarks[metric.metric_id] = {
                "median": median_value,
                "sample_size": len([v for v in values if v is not None]),
            }
        return benchmarks

    # ── public assembly ──────────────────────────────────────────────────

    async def portfolio(
        self, apps: list[dict[str, Any]], window: PeriodWindow
    ) -> dict[str, Any]:
        """The World View payload: portfolio totals, per-app rows, insights."""

        per_app_signals = await asyncio.gather(
            *[
                self._safe_collect(str(app.get("app_id")), window, include_series=True)
                for app in apps
            ]
        )

        app_rows: list[dict[str, Any]] = []
        for app, signals in zip(apps, per_app_signals, strict=True):
            values = (
                _empty_values(self.registry)
                if signals.error
                else self._finalize_values(signals)
            )
            app_rows.append(
                {
                    "app_id": app.get("app_id"),
                    "name": app.get("name") or app.get("app_id"),
                    "lifecycle_state": app.get("lifecycle_state"),
                    "metrics": values,
                    "error": signals.error,
                }
            )

        combined = self._sum_signals([s for s in per_app_signals if not s.error])
        portfolio_values = self._finalize_values(combined)

        return {
            "period": window.to_payload(),
            "registry": registry_payload(self.registry),
            "portfolio": portfolio_values,
            "series": self._derived_series(combined.series),
            "apps": app_rows,
            "benchmarks": self._benchmarks([row["metrics"] for row in app_rows]),
            "insights": build_portfolio_insights(app_rows),
            "availability": self._domain_availability(portfolio_values),
        }

    async def app_analytics(
        self,
        app: dict[str, Any],
        window: PeriodWindow,
        *,
        funnel: Any | None = None,
    ) -> dict[str, Any]:
        """The App View payload: values, series, MRR movement, funnel."""

        app_id = str(app.get("app_id"))
        signals = await self._safe_collect(app_id, window, include_series=True)
        values = (
            _empty_values(self.registry) if signals.error else self._finalize_values(signals)
        )

        movement = self._mrr_movement(signals)
        funnel_payload = await self._funnel_payload(app_id, window, funnel)

        return {
            "period": window.to_payload(),
            "registry": registry_payload(self.registry),
            "app": {
                "app_id": app.get("app_id"),
                "name": app.get("name") or app.get("app_id"),
                "lifecycle_state": app.get("lifecycle_state"),
            },
            "metrics": values,
            "series": self._derived_series(signals.series),
            "movement": movement,
            "funnel": funnel_payload,
            "insights": build_portfolio_insights(
                [
                    {
                        "app_id": app.get("app_id"),
                        "name": app.get("name") or app.get("app_id"),
                        "metrics": values,
                    }
                ]
            ),
            "availability": self._domain_availability(values),
            "error": signals.error,
        }

    def _mrr_movement(self, signals: _AppSignals) -> dict[str, Any]:
        components = {
            "new_mrr": signals.current.get("new_mrr"),
            "expansion_mrr": signals.current.get("expansion_mrr"),
            "contraction_mrr": signals.current.get("contraction_mrr"),
            "churned_mrr": signals.current.get("churned_mrr"),
        }
        starting = signals.starting_mrr
        ending = signals.current.get("mrr")
        available = starting is not None and any(
            value is not None for value in components.values()
        )
        unexplained: float | None = None
        if available and ending is not None:
            explained = (
                (starting or 0.0)
                + (components["new_mrr"] or 0.0)
                + (components["expansion_mrr"] or 0.0)
                - (components["contraction_mrr"] or 0.0)
                - (components["churned_mrr"] or 0.0)
            )
            difference = ending - explained
            if abs(difference) > _BRIDGE_EPSILON:
                unexplained = difference
        return {
            "available": available,
            "starting_mrr": starting,
            "ending_mrr": ending,
            "unexplained": unexplained,
            **components,
        }

    async def _funnel_payload(
        self, app_id: str, window: PeriodWindow, funnel: Any | None
    ) -> dict[str, Any]:
        if funnel is None:
            return {"configured": False}
        try:
            metrics = self._metrics_factory(app_id)
            result = await metrics.funnel(
                [step.event_name for step in funnel.steps],
                since=window.since,
                until=window.until,
                count_distinct=funnel.subject_field,
            )
        except Exception:
            logger.warning("OWNER_ANALYTICS_FUNNEL_FAILED app_id=%s", app_id, exc_info=True)
            return {"configured": True, "available": False, "funnel_id": funnel.funnel_id}
        counted = result.get("steps") or []
        steps = []
        for step, row in zip(funnel.steps, counted, strict=False):
            steps.append(
                {
                    "step_id": step.step_id,
                    "label": step.label,
                    "event_name": step.event_name,
                    "count": int(row.get("count") or 0),
                    "conversion_rate": row.get("conversion_rate"),
                }
            )
        return {
            "configured": True,
            "available": any(step["count"] > 0 for step in steps),
            "funnel_id": funnel.funnel_id,
            "label": funnel.label,
            "description": funnel.description,
            "subject": funnel.subject,
            "steps": steps,
        }

    async def metric_detail(
        self,
        metric_id: str,
        window: PeriodWindow,
        *,
        app: dict[str, Any],
        peer_apps: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Drill-down payload for one metric on one app.

        Includes the definition, value envelope, daily series when the metric
        supports one, derivation drivers, related metric envelopes, and a
        portfolio-median benchmark across the owner's other apps.
        """

        metric = self.registry.require(metric_id)
        app_id = str(app.get("app_id"))
        signals = await self._safe_collect(app_id, window, include_series=True)
        values = (
            _empty_values(self.registry) if signals.error else self._finalize_values(signals)
        )
        series = self._derived_series(signals.series)

        drivers: list[dict[str, Any]] = []
        if metric.derivation is not None:
            driver_ids = metric.derivation.referenced_metric_ids()
            if metric.derivation.formula in {"net_new_mrr", "nrr", "grr"}:
                driver_ids = ["new_mrr", "expansion_mrr", "contraction_mrr", "churned_mrr"]
            elif metric.derivation.formula == "retention":
                driver_ids = ["churned_users", "total_users"]
            for driver_id in driver_ids:
                if driver_id in values:
                    drivers.append(values[driver_id])

        related = [values[r] for r in metric.related if r in values]

        benchmark: dict[str, Any] | None = None
        if metric.benchmarkable and peer_apps:
            peer_values: list[float | None] = []
            for peer in peer_apps:
                peer_id = str(peer.get("app_id"))
                if peer_id == app_id:
                    peer_values.append(values.get(metric_id, {}).get("value"))
                    continue
                peer_signals = await self._safe_collect(peer_id, window, include_series=False)
                if peer_signals.error:
                    peer_values.append(None)
                    continue
                peer_metric_values = self._finalize_values(peer_signals)
                peer_values.append(peer_metric_values.get(metric_id, {}).get("value"))
            benchmark = {
                "kind": "portfolio_median",
                "label": "Portfolio median",
                "median": portfolio_median(peer_values),
                "sample_size": len([v for v in peer_values if v is not None]),
            }

        return {
            "period": window.to_payload(),
            "definition": registry_payload(self.registry)[metric_id],
            "app": {
                "app_id": app.get("app_id"),
                "name": app.get("name") or app.get("app_id"),
            },
            "value": values.get(metric_id),
            "series": series.get(metric_id, []),
            "drivers": drivers,
            "related": related,
            "benchmark": benchmark,
            "error": signals.error,
        }


__all__ = [
    "SERIES_METRIC_IDS",
    "OwnerAnalyticsService",
]
