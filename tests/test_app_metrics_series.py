from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from mozaiksai.core.metrics import AppMetrics, AppMetricTrackError


class FakeCollection:
    """Fake collection with a pipeline interpreter covering the aggregation
    operators the series/distinct reads use: $match, $sort, $group with
    $sum / $last / $addToSet accumulators, $cond/$eq expressions, and
    $substrBytes group ids."""

    def __init__(self) -> None:
        self.inserted: list[dict[str, Any]] = []

    async def ensure_indexes(self, indexes):
        return None

    async def insert_one(self, document):
        self.inserted.append(dict(document))

    async def count(self, query):
        return sum(1 for row in self.inserted if _matches(row, query))

    async def find_many(self, query, *, limit=50, sort=None, projection=None):
        rows = [row for row in self.inserted if _matches(row, query)]
        return rows[:limit]

    async def aggregate(self, pipeline):
        rows = list(self.inserted)
        for stage in pipeline:
            if "$match" in stage:
                rows = [row for row in rows if _matches(row, stage["$match"])]
            elif "$sort" in stage:
                for key, direction in reversed(list(stage["$sort"].items())):
                    rows.sort(key=lambda row: _field(row, key) or "", reverse=int(direction) < 0)
            elif "$group" in stage:
                rows = _group(rows, stage["$group"])
        return rows


def _field(row: dict[str, Any], dotted: str) -> Any:
    value: Any = row
    for part in dotted.split("."):
        if not isinstance(value, dict):
            return None
        value = value.get(part)
    return value


def _matches(row: dict[str, Any], query: dict[str, Any]) -> bool:
    for key, expected in query.items():
        actual = _field(row, key)
        if isinstance(expected, dict):
            if "$in" in expected and actual not in expected["$in"]:
                return False
            if "$gte" in expected and (actual is None or actual < expected["$gte"]):
                return False
            if "$lte" in expected and (actual is None or actual > expected["$lte"]):
                return False
            continue
        if actual != expected:
            return False
    return True


def _eval(row: dict[str, Any], expr: Any) -> Any:
    if isinstance(expr, str) and expr.startswith("$"):
        return _field(row, expr[1:])
    if isinstance(expr, dict):
        if "$substrBytes" in expr:
            target, start, length = expr["$substrBytes"]
            return str(_eval(row, target) or "")[start : start + length]
        if "$cond" in expr:
            condition, then_expr, else_expr = expr["$cond"]
            return _eval(row, then_expr) if _eval(row, condition) else _eval(row, else_expr)
        if "$eq" in expr:
            left, right = expr["$eq"]
            return _eval(row, left) == _eval(row, right)
    return expr


def _group(rows: list[dict[str, Any]], spec: dict[str, Any]) -> list[dict[str, Any]]:
    grouped: dict[Any, list[dict[str, Any]]] = {}
    for row in rows:
        key = _eval(row, spec["_id"]) if spec["_id"] is not None else None
        grouped.setdefault(key, []).append(row)

    results = []
    for key, bucket in grouped.items():
        out: dict[str, Any] = {"_id": key}
        for field_name, accumulator in spec.items():
            if field_name == "_id":
                continue
            operator, operand = next(iter(accumulator.items()))
            if operator == "$sum":
                out[field_name] = sum(float(_eval(row, operand) or 0) for row in bucket)
            elif operator == "$last":
                out[field_name] = _eval(bucket[-1], operand)
            elif operator == "$addToSet":
                seen: list[Any] = []
                for row in bucket:
                    value = _eval(row, operand)
                    if value not in seen:
                        seen.append(value)
                out[field_name] = seen
            else:  # pragma: no cover - unexpected operator in these tests
                raise AssertionError(f"unsupported accumulator {operator}")
        results.append(out)
    results.sort(key=lambda row: str(row.get("_id")))
    return results


def _metrics() -> AppMetrics:
    collection = FakeCollection()
    persistence = SimpleNamespace(collection=lambda module, entity: collection)
    ctx = SimpleNamespace(app_id="app-1", persistence=persistence)
    # Pin persistence directly so tests can swap ctx (for actor changes)
    # without re-resolving it.
    return AppMetrics(ctx, persistence=persistence)


@pytest.mark.asyncio
async def test_value_series_last_keeps_latest_value_per_day() -> None:
    metrics = _metrics()
    await metrics.record_snapshot("kpi.mrr", value=100, unit="usd", occurred_at="2026-09-01T08:00:00+00:00")
    await metrics.record_snapshot("kpi.mrr", value=120, unit="usd", occurred_at="2026-09-01T20:00:00+00:00")
    await metrics.record_snapshot("kpi.mrr", value=140, unit="usd", occurred_at="2026-09-02T09:00:00+00:00")

    series = await metrics.value_series("kpi.mrr", reducer="last")

    assert series == [
        {"period_start": "2026-09-01", "value": 120.0},
        {"period_start": "2026-09-02", "value": 140.0},
    ]


@pytest.mark.asyncio
async def test_value_series_sum_totals_flow_values_per_day() -> None:
    metrics = _metrics()
    await metrics.record_snapshot("kpi.new_mrr", value=50, unit="usd", occurred_at="2026-09-01T08:00:00+00:00")
    await metrics.record_snapshot("kpi.new_mrr", value=25, unit="usd", occurred_at="2026-09-01T20:00:00+00:00")

    series = await metrics.value_series("kpi.new_mrr", reducer="sum")

    assert series == [{"period_start": "2026-09-01", "value": 75.0}]


@pytest.mark.asyncio
async def test_value_series_rejects_unknown_reducer() -> None:
    with pytest.raises(AppMetricTrackError, match="reducer"):
        await _metrics().value_series("kpi.mrr", reducer="median")


@pytest.mark.asyncio
async def test_active_subjects_counts_distinct_actors_across_window() -> None:
    metrics = _metrics()
    for actor, occurred_at in (
        ("user-1", "2026-09-01T08:00:00+00:00"),
        ("user-1", "2026-09-02T08:00:00+00:00"),
        ("user-2", "2026-09-02T09:00:00+00:00"),
    ):
        metrics.ctx = SimpleNamespace(app_id="app-1", user_id=actor)
        await metrics.track("app.page_view", occurred_at=occurred_at)

    # user-1 was active on two days but counts once across the window.
    assert await metrics.active_subjects() == 2


@pytest.mark.asyncio
async def test_funnel_count_distinct_deduplicates_repeat_subjects() -> None:
    metrics = _metrics()
    events = [
        ("user.signed_up", "user-1"),
        ("user.signed_up", "user-1"),
        ("user.signed_up", "user-2"),
        ("subscription.activated", "user-1"),
    ]
    for event_name, actor in events:
        metrics.ctx = SimpleNamespace(app_id="app-1", user_id=actor)
        await metrics.track(event_name)

    raw = await metrics.funnel(["user.signed_up", "subscription.activated"])
    distinct = await metrics.funnel(
        ["user.signed_up", "subscription.activated"], count_distinct="actor_id"
    )

    assert [step["count"] for step in raw["steps"]] == [3, 1]
    assert [step["count"] for step in distinct["steps"]] == [2, 1]
    assert distinct["steps"][1]["conversion_rate"] == 50.0


@pytest.mark.asyncio
async def test_funnel_count_distinct_rejects_unsafe_fields() -> None:
    with pytest.raises(AppMetricTrackError, match="count_distinct"):
        await _metrics().funnel(["a.b"], count_distinct="metadata.secret")
