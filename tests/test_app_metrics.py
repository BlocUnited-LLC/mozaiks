from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from mozaiksai.core.metrics import AppMetrics, normalize_metric_event_name
from mozaiksai.core.runtime.composition.module_context import ModuleContext
from mozaiksai.core.runtime.composition.module_executor import ModuleExecutor, ModuleRequest
from tests.module_authority_test_helpers import trusted_framework_authority


class FakeCollection:
    def __init__(self) -> None:
        self.inserted: list[dict[str, Any]] = []
        self.indexes: list[dict[str, Any]] = []
        self.find_queries: list[dict[str, Any]] = []
        self.aggregate_pipelines: list[list[dict[str, Any]]] = []

    async def ensure_indexes(self, indexes):
        self.indexes.extend(indexes)

    async def insert_one(self, document):
        self.inserted.append(dict(document))

    async def count(self, query):
        return sum(1 for row in self.inserted if _matches(row, query))

    async def find_many(self, query, *, limit=50, sort=None, projection=None):
        self.find_queries.append(dict(query))
        rows = [row for row in self.inserted if _matches(row, query)]
        if sort:
            for key, direction in reversed(list(sort)):
                rows.sort(key=lambda row: _field_value(row, key) or "", reverse=direction < 0)
        return rows[:limit]

    async def aggregate(self, pipeline):
        self.aggregate_pipelines.append(list(pipeline))
        rows = list(self.inserted)
        for stage in pipeline:
            if "$match" in stage:
                rows = [row for row in rows if _matches(row, stage["$match"])]
            elif "$sort" in stage:
                for key, direction in reversed(list(stage["$sort"].items())):
                    rows.sort(key=lambda row: _field_value(row, key) or "", reverse=int(direction) < 0)
            elif "$group" in stage:
                rows = _group_rows(rows, stage["$group"])
        return rows


class FakePersistence:
    def __init__(self) -> None:
        self.collection_handle = FakeCollection()
        self.calls: list[tuple[str, str]] = []

    def collection(self, module_id: str, entity_name: str):
        self.calls.append((module_id, entity_name))
        return self.collection_handle


def _field_value(row: dict[str, Any], dotted_key: str) -> Any:
    value: Any = row
    for part in dotted_key.split("."):
        if not isinstance(value, dict):
            return None
        value = value.get(part)
    return value


def _matches(row: dict[str, Any], query: dict[str, Any]) -> bool:
    for key, expected in query.items():
        actual = _field_value(row, key)
        if isinstance(expected, dict):
            if "$in" in expected and actual not in expected["$in"]:
                return False
            if "$gte" in expected and actual < expected["$gte"]:
                return False
            if "$lte" in expected and actual > expected["$lte"]:
                return False
            continue
        if actual != expected:
            return False
    return True


def _group_rows(rows: list[dict[str, Any]], group_spec: dict[str, Any]) -> list[dict[str, Any]]:
    groups: dict[Any, list[dict[str, Any]]] = {}
    raw_group_id = group_spec.get("_id")
    for row in rows:
        if isinstance(raw_group_id, str) and raw_group_id.startswith("$"):
            group_id = _field_value(row, raw_group_id[1:])
        else:
            group_id = raw_group_id
        groups.setdefault(group_id, []).append(row)

    result: list[dict[str, Any]] = []
    for group_id, group_rows in groups.items():
        values = [float(row.get("value") or 0) for row in group_rows]
        result.append(
            {
                "_id": group_id,
                "count": len(group_rows),
                "sum": sum(values),
                "avg": sum(values) / len(values) if values else None,
                "min": min(values) if values else None,
                "max": max(values) if values else None,
                "first": values[0] if values else None,
                "first_occurred_at": group_rows[0].get("occurred_at") if group_rows else None,
                "latest": values[-1] if values else None,
                "latest_occurred_at": group_rows[-1].get("occurred_at") if group_rows else None,
            }
        )
    return result


def test_normalize_metric_event_name_uses_dot_delimited_lowercase() -> None:
    assert normalize_metric_event_name("App Viewed") == "app_viewed"


@pytest.mark.asyncio
async def test_app_metrics_track_persists_canonical_event() -> None:
    persistence = FakePersistence()
    ctx = SimpleNamespace(
        app_id="app-1",
        tenant_id="tenant-1",
        workspace_id="workspace-1",
        user_id="user-1",
        correlation_id="corr-1",
        module_id="marketplace",
        action_id="record_click",
        persistence=persistence,
    )

    event = await AppMetrics(ctx).track(
        "campaign.click",
        subject_type="marketplace_placement",
        subject_id="placement-1",
        attribution_id="attr-1",
        session_id="session-1",
        visibility="admin",
        dimensions={"listing_id": "listing-1"},
        metadata={"slot": "sponsored"},
    )

    assert persistence.calls[0] == ("app_metrics", "events")
    assert persistence.collection_handle.indexes
    assert persistence.collection_handle.inserted == [event]
    assert event["event_name"] == "campaign.click"
    assert event["subject_id"] == "placement-1"
    assert event["actor_id"] == "user-1"
    assert event["source"] == {"layer": "module", "module_id": "marketplace", "action_id": "record_click"}
    assert event["dimensions"]["listing_id"] == "listing-1"


@pytest.mark.asyncio
async def test_app_metrics_summarize_and_funnel_use_filters() -> None:
    persistence = FakePersistence()
    ctx = SimpleNamespace(app_id="app-1", persistence=persistence)
    metrics = AppMetrics(ctx)

    await metrics.track("app.viewed", dimensions={"listing_id": "listing-1"})
    await metrics.track("campaign.impression", dimensions={"listing_id": "listing-1"})
    await metrics.track("campaign.click", dimensions={"listing_id": "listing-1"})
    await metrics.track("campaign.click", dimensions={"listing_id": "other"})

    summary = await metrics.summarize(
        event_names=["campaign.impression", "campaign.click"],
        dimension_filters={"listing_id": "listing-1"},
    )
    funnel = await metrics.funnel(
        ["campaign.impression", "campaign.click"],
        dimension_filters={"listing_id": "listing-1"},
    )

    assert summary["total"] == 2
    assert summary["counts_by_event"] == {"campaign.impression": 1, "campaign.click": 1}
    assert funnel["steps"][0]["count"] == 1
    assert funnel["steps"][1]["conversion_rate"] == 100.0


@pytest.mark.asyncio
async def test_app_metrics_record_snapshot_and_summarize_values() -> None:
    persistence = FakePersistence()
    ctx = SimpleNamespace(app_id="app-1", persistence=persistence)
    metrics = AppMetrics(ctx)

    snapshot = await metrics.record_snapshot(
        "kpi.active_users",
        subject_type="app",
        subject_id="app-1",
        value=120,
        unit="users",
        period_start="2026-07-01T00:00:00+00:00",
        period_end="2026-07-01T23:59:59+00:00",
        occurred_at="2026-07-02T00:00:00+00:00",
        aggregation="daily_unique",
    )
    await metrics.record_snapshot(
        "kpi.active_users",
        subject_type="app",
        subject_id="app-1",
        value=180,
        unit="users",
        period_start="2026-07-02T00:00:00+00:00",
        period_end="2026-07-02T23:59:59+00:00",
        occurred_at="2026-07-03T00:00:00+00:00",
        aggregation="daily_unique",
    )
    await metrics.record_snapshot(
        "kpi.arr",
        subject_type="app",
        subject_id="app-1",
        value=200_000,
        unit="usd_cents",
        occurred_at="2026-07-03T00:00:00+00:00",
    )

    result = await metrics.summarize_values(
        event_names="kpi.active_users",
        subject_type="app",
        subject_id="app-1",
        unit="users",
        aggregation="daily_unique",
    )

    assert snapshot["metadata"]["metric_kind"] == "snapshot"
    assert snapshot["period_end"] == "2026-07-01T23:59:59+00:00"
    assert result["total_count"] == 2
    assert result["total_sum"] == 300.0
    assert result["groups"] == [
        {
            "group": "kpi.active_users",
            "count": 2,
            "sum": 300.0,
            "avg": 150.0,
            "min": 120.0,
            "max": 180.0,
            "first": 120.0,
            "first_occurred_at": "2026-07-02T00:00:00+00:00",
            "latest": 180.0,
            "latest_occurred_at": "2026-07-03T00:00:00+00:00",
        }
    ]
    assert persistence.collection_handle.aggregate_pipelines[-1][0]["$match"]["event_name"] == "kpi.active_users"


@pytest.mark.asyncio
async def test_app_metrics_summarize_values_rejects_unsafe_group_field() -> None:
    persistence = FakePersistence()
    ctx = SimpleNamespace(app_id="app-1", persistence=persistence)

    with pytest.raises(ValueError, match="group_by must be one of"):
        await AppMetrics(ctx).summarize_values(group_by="payment.provider_secret")


@pytest.mark.asyncio
async def test_module_context_exposes_lazy_metrics_tracker() -> None:
    persistence = FakePersistence()
    ctx = ModuleContext(app_id="app-1", module_id="demo", action_id="run", persistence=persistence)

    await ctx.metrics.track("feature.used", subject_type="feature", subject_id="search")

    assert persistence.collection_handle.inserted[0]["event_name"] == "feature.used"
    assert persistence.collection_handle.inserted[0]["source"]["module_id"] == "demo"


@pytest.mark.asyncio
async def test_module_executor_injects_module_and_action_into_context() -> None:
    class Handler:
        async def run(self, ctx):
            return {"module_id": ctx.module_id, "action_id": ctx.action_id}

    executor = ModuleExecutor()
    executor.register("analytics", Handler())

    result = await executor.execute(ModuleRequest(module="analytics", action="run", app_id="app-1", authority=trusted_framework_authority()))

    assert result.success is True
    assert result.data == {"module_id": "analytics", "action_id": "run"}
