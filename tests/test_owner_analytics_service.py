from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from mozaiksai.core.metrics.funnels import FunnelDef
from mozaiksai.core.metrics.owner_analytics import OwnerAnalyticsService
from mozaiksai.core.metrics.periods import resolve_period

NOW = datetime(2026, 9, 12, tzinfo=UTC)
WINDOW = resolve_period("30d", now=NOW)


def _iso(day_offset: int) -> str:
    stamp = datetime(2026, 9, 12, tzinfo=UTC).timestamp() + day_offset * 86400
    return datetime.fromtimestamp(stamp, tz=UTC).isoformat()


class FakeAppMetrics:
    """Implements the AppMetrics read surface OwnerAnalyticsService uses.

    Snapshot events are (event_name, occurred_at_iso, value); usage events
    are (occurred_at_iso, actor_id); funnel events are
    (event_name, occurred_at_iso, actor_id).
    """

    def __init__(
        self,
        snapshots: list[tuple[str, str, float]] | None = None,
        usage: list[tuple[str, str]] | None = None,
        funnel_events: list[tuple[str, str, str]] | None = None,
        *,
        fail: bool = False,
    ) -> None:
        self.snapshots = snapshots or []
        self.usage = usage or []
        self.funnel_events = funnel_events or []
        self.fail = fail

    def _check(self) -> None:
        if self.fail:
            raise RuntimeError("metrics store unavailable")

    @staticmethod
    def _in_window(occurred_at: str, since: Any, until: Any) -> bool:
        stamp = datetime.fromisoformat(occurred_at)
        if since is not None and stamp < since:
            return False
        if until is not None and stamp > until:
            return False
        return True

    async def summarize_values(self, *, event_names=None, since=None, until=None, group_by=None, **_):
        self._check()
        rows = sorted(
            (
                (occurred_at, value)
                for name, occurred_at, value in self.snapshots
                if name == event_names and self._in_window(occurred_at, since, until)
            ),
        )
        if not rows:
            return {"groups": [], "total_count": 0, "total_sum": 0.0}
        values = [value for _, value in rows]
        return {
            "total_count": len(values),
            "total_sum": float(sum(values)),
            "groups": [
                {
                    "group": None,
                    "count": len(values),
                    "sum": float(sum(values)),
                    "latest": float(values[-1]),
                    "first": float(values[0]),
                }
            ],
        }

    async def summarize(self, *, event_names=None, **_):
        self._check()
        # Mirror the real store: only usage instrumentation events count
        # toward the "ever instrumented" gate, never kpi.* snapshot rows.
        names = set(event_names or [])
        if names and not (names & {"app.page_view", "app.action_invoked"}):
            return {"total": 0, "counts_by_event": {}}
        return {"total": len(self.usage), "counts_by_event": {}}

    async def active_subjects(self, *, since=None, until=None):
        self._check()
        return len(
            {
                actor
                for occurred_at, actor in self.usage
                if actor and self._in_window(occurred_at, since, until)
            }
        )

    async def usage_rollup(self, *, since=None, until=None):
        self._check()
        buckets: dict[str, set[str]] = {}
        for occurred_at, actor in self.usage:
            if not self._in_window(occurred_at, since, until):
                continue
            buckets.setdefault(occurred_at[:10], set()).add(actor)
        return [
            {
                "period_start": day,
                "granularity": "day",
                "page_views": len(actors),
                "action_invocations": 0,
                "unique_sessions": len(actors),
                "active_users": len(actors),
            }
            for day, actors in sorted(buckets.items())
        ]

    async def value_series(self, event_name, *, since=None, until=None, reducer="last"):
        self._check()
        buckets: dict[str, list[float]] = {}
        for name, occurred_at, value in sorted(self.snapshots, key=lambda row: row[1]):
            if name != event_name or not self._in_window(occurred_at, since, until):
                continue
            buckets.setdefault(occurred_at[:10], []).append(float(value))
        return [
            {
                "period_start": day,
                "value": values[-1] if reducer == "last" else sum(values),
            }
            for day, values in sorted(buckets.items())
        ]

    async def funnel(self, steps, *, since=None, until=None, count_distinct=None, **_):
        self._check()
        rows = []
        previous = None
        for step in steps:
            actors = {
                actor
                for name, occurred_at, actor in self.funnel_events
                if name == step and self._in_window(occurred_at, since, until)
            }
            count = len(actors)
            rate = None if previous in (None, 0) else round((count / previous) * 100, 2)
            rows.append({"event_name": step, "count": count, "conversion_rate": rate})
            previous = count
        return {"steps": rows}


def _snapshot_app(mrr_now: float, mrr_prev: float, paying: float, total: float) -> FakeAppMetrics:
    return FakeAppMetrics(
        snapshots=[
            ("kpi.mrr", _iso(-45), mrr_prev),
            ("kpi.mrr", _iso(-2), mrr_now),
            ("kpi.paying_users", _iso(-2), paying),
            ("kpi.total_users", _iso(-2), total),
            ("kpi.total_users", _iso(-45), total * 0.9),
        ],
        usage=[(_iso(-3), "user-1"), (_iso(-3), "user-2"), (_iso(-40), "user-1")],
    )


def _service(stores: dict[str, FakeAppMetrics]) -> OwnerAnalyticsService:
    return OwnerAnalyticsService(metrics_factory=lambda app_id: stores[app_id])


@pytest.mark.asyncio
async def test_portfolio_sums_additive_metrics_and_derives_rates_from_totals() -> None:
    stores = {
        "app-a": _snapshot_app(900.0, 800.0, paying=90.0, total=100.0),
        "app-b": _snapshot_app(100.0, 120.0, paying=10.0, total=900.0),
    }
    service = _service(stores)
    payload = await service.portfolio(
        [
            {"app_id": "app-a", "name": "Alpha", "lifecycle_state": "active"},
            {"app_id": "app-b", "name": "Beta", "lifecycle_state": "active"},
        ],
        WINDOW,
    )

    assert payload["portfolio"]["mrr"]["value"] == 1000.0
    assert payload["portfolio"]["paying_users"]["value"] == 100.0
    assert payload["portfolio"]["total_users"]["value"] == 1000.0
    # Portfolio conversion derives from totals (10%), not app-average (~45%).
    assert payload["portfolio"]["paid_conversion"]["value"] == 10.0
    assert payload["period"]["id"] == "30d"
    assert set(payload["registry"]) == {
        metric["metric_id"] for metric in payload["registry"].values()
    }


@pytest.mark.asyncio
async def test_portfolio_benchmarks_use_median_across_apps() -> None:
    stores = {
        "app-a": _snapshot_app(900.0, 800.0, paying=90.0, total=100.0),
        "app-b": _snapshot_app(100.0, 120.0, paying=10.0, total=900.0),
    }
    payload = await _service(stores).portfolio(
        [
            {"app_id": "app-a", "name": "Alpha"},
            {"app_id": "app-b", "name": "Beta"},
        ],
        WINDOW,
    )
    assert payload["benchmarks"]["mrr"]["median"] == 500.0
    assert payload["benchmarks"]["mrr"]["sample_size"] == 2


@pytest.mark.asyncio
async def test_portfolio_missing_data_stays_none_not_zero() -> None:
    stores = {"app-a": FakeAppMetrics()}
    payload = await _service(stores).portfolio([{"app_id": "app-a", "name": "Alpha"}], WINDOW)
    assert payload["portfolio"]["mrr"]["value"] is None
    assert payload["portfolio"]["mrr"]["available"] is False
    assert payload["availability"]["revenue"] == "none"
    assert payload["availability"]["users"] == "none"
    assert payload["insights"] == []


@pytest.mark.asyncio
async def test_billing_snapshots_alone_do_not_fabricate_zero_active_users() -> None:
    """An app with revenue but no usage instrumentation reports no active users.

    Billing integrations record kpi.* snapshots into the same per-app metric
    store the analytics reader uses. That must not be mistaken for activity
    instrumentation: reporting "0 active users" for an app that never measured
    users invents a figure, where pending is the truthful answer.
    """

    revenue_only = FakeAppMetrics(
        snapshots=[
            ("kpi.mrr", _iso(-2), 900.0),
            ("kpi.paying_users", _iso(-2), 30.0),
        ],
        usage=[],  # no app.page_view / app.action_invoked has ever been recorded
    )
    payload = await OwnerAnalyticsService(
        metrics_factory=lambda app_id: revenue_only
    ).portfolio([{"app_id": "app-a", "name": "Alpha"}], WINDOW)

    assert payload["portfolio"]["mrr"]["value"] == 900.0
    assert payload["portfolio"]["active_users"]["value"] is None
    assert payload["portfolio"]["active_users"]["available"] is False
    assert payload["availability"]["revenue"] == "partial"


@pytest.mark.asyncio
async def test_portfolio_degrades_per_app_on_store_failure() -> None:
    stores = {
        "app-a": _snapshot_app(900.0, 800.0, paying=90.0, total=100.0),
        "app-broken": FakeAppMetrics(fail=True),
    }
    payload = await _service(stores).portfolio(
        [
            {"app_id": "app-a", "name": "Alpha"},
            {"app_id": "app-broken", "name": "Broken"},
        ],
        WINDOW,
    )
    broken = next(row for row in payload["apps"] if row["app_id"] == "app-broken")
    assert broken["error"] is True
    assert broken["metrics"]["mrr"]["available"] is False
    # The healthy app still contributes to portfolio totals.
    assert payload["portfolio"]["mrr"]["value"] == 900.0


@pytest.mark.asyncio
async def test_app_analytics_builds_movement_bridge() -> None:
    metrics = FakeAppMetrics(
        snapshots=[
            ("kpi.mrr", _iso(-45), 1000.0),
            ("kpi.mrr", _iso(-1), 1350.0),
            ("kpi.new_mrr", _iso(-10), 500.0),
            ("kpi.expansion_mrr", _iso(-10), 200.0),
            ("kpi.contraction_mrr", _iso(-10), 100.0),
            ("kpi.churned_mrr", _iso(-10), 250.0),
        ],
    )
    service = OwnerAnalyticsService(metrics_factory=lambda app_id: metrics)
    payload = await service.app_analytics({"app_id": "app-a", "name": "Alpha"}, WINDOW)

    movement = payload["movement"]
    assert movement["available"] is True
    assert movement["starting_mrr"] == 1000.0
    assert movement["ending_mrr"] == 1350.0
    assert movement["new_mrr"] == 500.0
    assert movement["unexplained"] is None  # 1000 + 350 == 1350
    assert payload["metrics"]["net_new_mrr"]["value"] == 350.0
    assert payload["metrics"]["nrr"]["value"] == 85.0


@pytest.mark.asyncio
async def test_app_analytics_counts_distinct_funnel_subjects() -> None:
    funnel = FunnelDef.model_validate(
        {
            "funnel_id": "activation",
            "label": "Activation",
            "subject": "actor",
            "steps": [
                {"step_id": "signed_up", "label": "Signed up", "event_name": "user.signed_up"},
                {"step_id": "paid", "label": "Paid", "event_name": "subscription.activated"},
            ],
        }
    )
    metrics = FakeAppMetrics(
        funnel_events=[
            ("user.signed_up", _iso(-5), "u1"),
            ("user.signed_up", _iso(-5), "u2"),
            ("user.signed_up", _iso(-4), "u1"),  # repeat actor: not double counted
            ("subscription.activated", _iso(-3), "u1"),
        ],
        usage=[(_iso(-3), "u1")],
    )
    service = OwnerAnalyticsService(metrics_factory=lambda app_id: metrics)
    payload = await service.app_analytics(
        {"app_id": "app-a", "name": "Alpha"}, WINDOW, funnel=funnel
    )

    assert payload["funnel"]["configured"] is True
    steps = payload["funnel"]["steps"]
    assert [step["count"] for step in steps] == [2, 1]
    assert steps[1]["conversion_rate"] == 50.0


@pytest.mark.asyncio
async def test_app_analytics_without_funnel_reports_unconfigured() -> None:
    service = OwnerAnalyticsService(metrics_factory=lambda app_id: FakeAppMetrics())
    payload = await service.app_analytics({"app_id": "app-a", "name": "Alpha"}, WINDOW)
    assert payload["funnel"] == {"configured": False}


@pytest.mark.asyncio
async def test_metric_detail_includes_benchmark_and_rejects_unknown_metric() -> None:
    stores = {
        "app-a": _snapshot_app(900.0, 800.0, paying=90.0, total=100.0),
        "app-b": _snapshot_app(100.0, 120.0, paying=10.0, total=900.0),
    }
    service = _service(stores)
    peers = [{"app_id": "app-a", "name": "Alpha"}, {"app_id": "app-b", "name": "Beta"}]

    detail = await service.metric_detail(
        "mrr", WINDOW, app={"app_id": "app-a", "name": "Alpha"}, peer_apps=peers
    )
    assert detail["definition"]["metric_id"] == "mrr"
    assert detail["value"]["value"] == 900.0
    assert detail["benchmark"]["median"] == 500.0
    assert any(entry["metric_id"] == "arr" for entry in detail["related"])

    with pytest.raises(KeyError):
        await service.metric_detail(
            "made_up_metric", WINDOW, app={"app_id": "app-a", "name": "Alpha"}, peer_apps=peers
        )


@pytest.mark.asyncio
async def test_ownership_boundary_only_reads_provided_apps() -> None:
    """The service must only touch stores for the app rows the caller passed."""

    touched: list[str] = []

    def factory(app_id: str) -> FakeAppMetrics:
        touched.append(app_id)
        return FakeAppMetrics()

    service = OwnerAnalyticsService(metrics_factory=factory)
    await service.portfolio([{"app_id": "owned-app", "name": "Mine"}], WINDOW)
    assert set(touched) == {"owned-app"}
