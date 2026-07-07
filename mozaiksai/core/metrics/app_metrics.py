from __future__ import annotations

"""Generic app metric tracking.

This module is intentionally host-neutral. It records measurable app signals
such as views, clicks, conversions, and feature usage. Hosted product concepts
such as billing, payout percentage, campaign pricing, and marketplace ranking
belong in the app that consumes this primitive.
"""

from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from mozaiksai.core.runtime.persistence import MongoPersistenceContext

APP_METRICS_MODULE_ID = "app_metrics"
APP_METRICS_ENTITY_NAME = "events"

_ALLOWED_VISIBILITIES = {"private", "admin", "public"}
_MAX_EVENT_NAME_CHARS = 128
_MAX_TEXT_CHARS = 256
_INDEXES = [
    {
        "keys": [
            ["event_name", 1],
            ["occurred_at", -1],
        ],
        "name": "metric_event_name_occurred_at",
    },
    {
        "keys": [
            ["subject_type", 1],
            ["subject_id", 1],
            ["occurred_at", -1],
        ],
        "name": "metric_subject_occurred_at",
    },
    {
        "keys": [
            ["attribution_id", 1],
            ["occurred_at", -1],
        ],
        "name": "metric_attribution_occurred_at",
    },
    {
        "keys": [
            ["event_name", 1],
            ["subject_type", 1],
            ["subject_id", 1],
            ["period_end", -1],
        ],
        "name": "metric_value_subject_period_end",
    },
]


class AppMetricTrackError(ValueError):
    """Raised when a metric event cannot be built safely."""


def _timestamp(value: datetime | str | None = None) -> str:
    if value is None:
        return datetime.now(UTC).isoformat()
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        return value.astimezone(UTC).isoformat()
    return str(value).strip()


def _safe_text(value: Any, *, maximum: int = _MAX_TEXT_CHARS) -> str:
    return str(value or "").strip()[:maximum]


def _safe_mapping(value: Mapping[str, Any] | None) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    return {str(key): item for key, item in value.items() if str(key).strip()}


def _safe_group_field(value: str | None) -> str | None:
    if value is None:
        return None
    field = str(value or "").strip()
    if not field:
        return None
    allowed_roots = {"event_name", "subject_type", "subject_id", "unit", "visibility", "dimensions", "metadata"}
    root = field.split(".", 1)[0]
    if root not in allowed_roots:
        raise AppMetricTrackError(
            "group_by must be one of event_name, subject_type, subject_id, unit, visibility, dimensions.<key>, or metadata.<key>"
        )
    return field


def _normalize_event_names(event_names: Iterable[str] | str | None) -> list[str]:
    if event_names is None:
        return []
    if isinstance(event_names, str):
        return [normalize_metric_event_name(event_names)]
    return [normalize_metric_event_name(value) for value in event_names]


def normalize_metric_event_name(event_name: str) -> str:
    """Return a normalized metric event name.

    Names use the same dot-delimited style as module events, but this function
    does not force a business namespace. Apps may track host-neutral names such
    as ``app.viewed`` or ``feature.used``.
    """

    normalized = str(event_name or "").strip().lower().replace(" ", "_")
    if not normalized:
        raise AppMetricTrackError("event_name is required")
    if len(normalized) > _MAX_EVENT_NAME_CHARS:
        raise AppMetricTrackError(f"event_name must be {_MAX_EVENT_NAME_CHARS} characters or fewer")
    return normalized


class AppMetrics:
    """Durable, app-scoped metric signal tracker.

    ``ctx.metrics`` exposes this class to module services. The underlying
    collection is app-scoped through ``ctx.persistence`` when available. Metrics
    are deliberately generic: callers provide event names, subjects, attribution
    ids, and dimensions; product-specific money logic remains outside OSS.
    """

    def __init__(
        self,
        ctx: Any,
        *,
        module_id: str = APP_METRICS_MODULE_ID,
        entity_name: str = APP_METRICS_ENTITY_NAME,
        persistence: Any | None = None,
    ) -> None:
        self.ctx = ctx
        self.module_id = module_id
        self.entity_name = entity_name
        self.persistence = persistence
        self._indexes_ensured = False

    def _persistence(self) -> Any:
        persistence = self.persistence or getattr(self.ctx, "persistence", None)
        if persistence is not None:
            return persistence

        app_id = _safe_text(getattr(self.ctx, "app_id", ""))
        if not app_id:
            raise RuntimeError("AppMetrics requires ctx.persistence or ctx.app_id")

        self.persistence = MongoPersistenceContext(
            app_id=app_id,
            tenant_id=getattr(self.ctx, "tenant_id", None),
            workspace_id=getattr(self.ctx, "workspace_id", None),
            user_id=getattr(self.ctx, "user_id", None),
        )
        return self.persistence

    def _collection(self) -> Any:
        return self._persistence().collection(self.module_id, self.entity_name)

    async def ensure_indexes(self) -> None:
        collection = self._collection()
        ensure = getattr(collection, "ensure_indexes", None)
        if callable(ensure):
            await ensure(_INDEXES)
        self._indexes_ensured = True

    async def _ensure_indexes_once(self) -> None:
        if not self._indexes_ensured:
            await self.ensure_indexes()

    def _base_query(
        self,
        *,
        event_names: Iterable[str] | str | None = None,
        subject_type: str | None = None,
        subject_id: str | None = None,
        attribution_id: str | None = None,
        session_id: str | None = None,
        unit: str | None = None,
        aggregation: str | None = None,
        dimension_filters: Mapping[str, Any] | None = None,
        since: datetime | str | None = None,
        until: datetime | str | None = None,
        period_start: datetime | str | None = None,
        period_end: datetime | str | None = None,
    ) -> dict[str, Any]:
        query: dict[str, Any] = {}
        normalized_names = _normalize_event_names(event_names)
        if len(normalized_names) == 1:
            query["event_name"] = normalized_names[0]
        elif normalized_names:
            query["event_name"] = {"$in": normalized_names}
        if subject_type:
            query["subject_type"] = _safe_text(subject_type)
        if subject_id:
            query["subject_id"] = _safe_text(subject_id)
        if attribution_id:
            query["attribution_id"] = _safe_text(attribution_id)
        if session_id:
            query["session_id"] = _safe_text(session_id)
        if unit:
            query["unit"] = _safe_text(unit, maximum=64)
        if aggregation:
            query["aggregation"] = _safe_text(aggregation, maximum=64)
        for key, value in _safe_mapping(dimension_filters).items():
            query[f"dimensions.{key}"] = value
        if since is not None or until is not None:
            occurred_at: dict[str, str] = {}
            if since is not None:
                occurred_at["$gte"] = _timestamp(since)
            if until is not None:
                occurred_at["$lte"] = _timestamp(until)
            query["occurred_at"] = occurred_at
        if period_start is not None:
            query["period_start"] = {"$gte": _timestamp(period_start)}
        if period_end is not None:
            query["period_end"] = {"$lte": _timestamp(period_end)}
        return query

    async def track(
        self,
        event_name: str,
        *,
        subject_type: str | None = None,
        subject_id: str | None = None,
        value: int | float = 1,
        unit: str = "count",
        attribution_id: str | None = None,
        session_id: str | None = None,
        visibility: str = "private",
        dimensions: Mapping[str, Any] | None = None,
        metadata: Mapping[str, Any] | None = None,
        occurred_at: datetime | str | None = None,
        period_start: datetime | str | None = None,
        period_end: datetime | str | None = None,
        aggregation: str | None = None,
    ) -> dict[str, Any]:
        """Persist one metric event and return the canonical document."""

        normalized_name = normalize_metric_event_name(event_name)
        normalized_visibility = _safe_text(visibility or "private").lower()
        if normalized_visibility not in _ALLOWED_VISIBILITIES:
            raise AppMetricTrackError(f"visibility must be one of {sorted(_ALLOWED_VISIBILITIES)}")

        try:
            numeric_value = float(value)
        except Exception as exc:
            raise AppMetricTrackError("value must be numeric") from exc

        event: dict[str, Any] = {
            "metric_event_id": f"metric_{uuid4().hex}",
            "event_name": normalized_name,
            "subject_type": _safe_text(subject_type),
            "subject_id": _safe_text(subject_id),
            "value": numeric_value,
            "unit": _safe_text(unit or "count", maximum=64) or "count",
            "attribution_id": _safe_text(attribution_id),
            "session_id": _safe_text(session_id),
            "visibility": normalized_visibility,
            "aggregation": _safe_text(aggregation or "", maximum=64),
            "app_id": _safe_text(getattr(self.ctx, "app_id", "")),
            "tenant_id": _safe_text(getattr(self.ctx, "tenant_id", "")),
            "workspace_id": _safe_text(getattr(self.ctx, "workspace_id", "")),
            "actor_id": _safe_text(getattr(self.ctx, "user_id", "")),
            "correlation_id": _safe_text(getattr(self.ctx, "correlation_id", "")),
            "source": {
                "layer": "module",
                "module_id": _safe_text(getattr(self.ctx, "module_id", "")),
                "action_id": _safe_text(getattr(self.ctx, "action_id", "")),
            },
            "dimensions": _safe_mapping(dimensions),
            "metadata": _safe_mapping(metadata),
            "occurred_at": _timestamp(occurred_at),
            "created_at": _timestamp(),
        }
        if period_start is not None:
            event["period_start"] = _timestamp(period_start)
        if period_end is not None:
            event["period_end"] = _timestamp(period_end)
        await self._ensure_indexes_once()
        await self._collection().insert_one(event)
        return event

    async def record_snapshot(
        self,
        metric_name: str,
        *,
        value: int | float,
        unit: str,
        subject_type: str | None = None,
        subject_id: str | None = None,
        period_start: datetime | str | None = None,
        period_end: datetime | str | None = None,
        aggregation: str = "instant",
        attribution_id: str | None = None,
        session_id: str | None = None,
        visibility: str = "private",
        dimensions: Mapping[str, Any] | None = None,
        metadata: Mapping[str, Any] | None = None,
        occurred_at: datetime | str | None = None,
    ) -> dict[str, Any]:
        """Persist a numeric KPI snapshot.

        Snapshot semantics stay generic: the owning app decides what the metric
        means and whether it is eligible for billing, campaign reporting, or UI.
        """

        snapshot_metadata = {"metric_kind": "snapshot", **_safe_mapping(metadata)}
        return await self.track(
            metric_name,
            subject_type=subject_type,
            subject_id=subject_id,
            value=value,
            unit=unit,
            attribution_id=attribution_id,
            session_id=session_id,
            visibility=visibility,
            dimensions=dimensions,
            metadata=snapshot_metadata,
            occurred_at=occurred_at,
            period_start=period_start,
            period_end=period_end,
            aggregation=aggregation,
        )

    async def summarize(
        self,
        *,
        event_names: Iterable[str] | str | None = None,
        subject_type: str | None = None,
        subject_id: str | None = None,
        attribution_id: str | None = None,
        session_id: str | None = None,
        dimension_filters: Mapping[str, Any] | None = None,
        since: datetime | str | None = None,
        until: datetime | str | None = None,
    ) -> dict[str, Any]:
        """Return counts for a filtered metric slice."""

        collection = self._collection()
        query = self._base_query(
            event_names=event_names,
            subject_type=subject_type,
            subject_id=subject_id,
            attribution_id=attribution_id,
            session_id=session_id,
            dimension_filters=dimension_filters,
            since=since,
            until=until,
        )
        total = int(await collection.count(query))
        counts_by_event: dict[str, int] = {}
        for name in _normalize_event_names(event_names):
            event_query = {**query, "event_name": name}
            counts_by_event[name] = int(await collection.count(event_query))

        return {
            "total": total,
            "counts_by_event": counts_by_event,
            "query": query,
        }

    async def summarize_values(
        self,
        *,
        event_names: Iterable[str] | str | None = None,
        subject_type: str | None = None,
        subject_id: str | None = None,
        attribution_id: str | None = None,
        session_id: str | None = None,
        unit: str | None = None,
        aggregation: str | None = None,
        dimension_filters: Mapping[str, Any] | None = None,
        since: datetime | str | None = None,
        until: datetime | str | None = None,
        period_start: datetime | str | None = None,
        period_end: datetime | str | None = None,
        group_by: str | None = "event_name",
    ) -> dict[str, Any]:
        """Return value aggregates for a filtered metric slice.

        This is intended for real KPI reads such as revenue, active users,
        conversion totals, and retention snapshots. Product-specific meaning
        remains in the consuming module.
        """

        collection = self._collection()
        query = self._base_query(
            event_names=event_names,
            subject_type=subject_type,
            subject_id=subject_id,
            attribution_id=attribution_id,
            session_id=session_id,
            unit=unit,
            aggregation=aggregation,
            dimension_filters=dimension_filters,
            since=since,
            until=until,
            period_start=period_start,
            period_end=period_end,
        )
        safe_group_by = _safe_group_field(group_by)
        group_id: Any = None if safe_group_by is None else f"${safe_group_by}"
        pipeline = [
            {"$match": query},
            {"$sort": {"occurred_at": 1}},
            {
                "$group": {
                    "_id": group_id,
                    "count": {"$sum": 1},
                    "sum": {"$sum": "$value"},
                    "avg": {"$avg": "$value"},
                    "min": {"$min": "$value"},
                    "max": {"$max": "$value"},
                    "first": {"$first": "$value"},
                    "first_occurred_at": {"$first": "$occurred_at"},
                    "latest": {"$last": "$value"},
                    "latest_occurred_at": {"$last": "$occurred_at"},
                }
            },
            {"$sort": {"_id": 1}},
        ]
        rows = await collection.aggregate(pipeline)
        groups = [
            {
                "group": row.get("_id"),
                "count": int(row.get("count") or 0),
                "sum": float(row.get("sum") or 0),
                "avg": None if row.get("avg") is None else float(row.get("avg")),
                "min": None if row.get("min") is None else float(row.get("min")),
                "max": None if row.get("max") is None else float(row.get("max")),
                "first": None if row.get("first") is None else float(row.get("first")),
                "first_occurred_at": row.get("first_occurred_at"),
                "latest": None if row.get("latest") is None else float(row.get("latest")),
                "latest_occurred_at": row.get("latest_occurred_at"),
            }
            for row in rows
        ]
        return {
            "total_count": sum(group["count"] for group in groups),
            "total_sum": sum(group["sum"] for group in groups),
            "groups": groups,
            "group_by": safe_group_by,
            "query": query,
        }

    async def funnel(
        self,
        steps: Iterable[str],
        *,
        subject_type: str | None = None,
        subject_id: str | None = None,
        attribution_id: str | None = None,
        session_id: str | None = None,
        dimension_filters: Mapping[str, Any] | None = None,
        since: datetime | str | None = None,
        until: datetime | str | None = None,
    ) -> dict[str, Any]:
        """Count each step and compute step-to-step conversion rates."""

        normalized_steps = _normalize_event_names(steps)
        rows: list[dict[str, Any]] = []
        previous_count: int | None = None
        for step in normalized_steps:
            result = await self.summarize(
                event_names=step,
                subject_type=subject_type,
                subject_id=subject_id,
                attribution_id=attribution_id,
                session_id=session_id,
                dimension_filters=dimension_filters,
                since=since,
                until=until,
            )
            count = int(result["total"])
            if previous_count is None or previous_count == 0:
                rate = None
            else:
                rate = round((count / previous_count) * 100, 2)
            rows.append({"event_name": step, "count": count, "conversion_rate": rate})
            previous_count = count
        return {"steps": rows}

    async def recent(
        self,
        *,
        event_names: Iterable[str] | str | None = None,
        limit: int = 50,
        subject_type: str | None = None,
        subject_id: str | None = None,
        attribution_id: str | None = None,
        session_id: str | None = None,
        dimension_filters: Mapping[str, Any] | None = None,
    ) -> list[Mapping[str, Any]]:
        collection = self._collection()
        query = self._base_query(
            event_names=event_names,
            subject_type=subject_type,
            subject_id=subject_id,
            attribution_id=attribution_id,
            session_id=session_id,
            dimension_filters=dimension_filters,
        )
        raw_rows = await collection.find_many(query, limit=limit, sort=[("occurred_at", -1)])
        if not isinstance(raw_rows, list):
            return []
        rows: list[Mapping[str, Any]] = []
        for row in raw_rows:
            if isinstance(row, Mapping):
                rows.append(row)
        return rows


__all__ = [
    "APP_METRICS_ENTITY_NAME",
    "APP_METRICS_MODULE_ID",
    "AppMetricTrackError",
    "AppMetrics",
    "normalize_metric_event_name",
]
