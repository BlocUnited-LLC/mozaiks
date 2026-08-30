"""Tests for generic usage instrumentation and AppMetrics.usage_rollup."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from mozaiksai.core.metrics.app_metrics import (
    USAGE_ACTION_EVENT,
    USAGE_PAGE_VIEW_EVENT,
    AppMetrics,
)
from mozaiksai.core.metrics.usage_instrumentation import (
    record_action_invocation,
    record_page_view,
    usage_metrics_enabled,
)


class _RollupCollection:
    """Fake collection returning pre-grouped aggregate rows."""

    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows
        self.pipelines: list[list[dict[str, Any]]] = []

    async def aggregate(self, pipeline):
        self.pipelines.append(list(pipeline))
        return list(self.rows)


class _Persistence:
    def __init__(self, collection) -> None:
        self._collection = collection

    def collection(self, module_id: str, entity_name: str):
        return self._collection


# ---------------------------------------------------------------------------
# usage_rollup
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_usage_rollup_normalizes_grouped_rows():
    collection = _RollupCollection(
        [
            {
                "_id": "2026-08-20",
                "page_views": 12.0,
                "action_invocations": 4.0,
                "sessions": ["s1", "s2", "", None],
                "actors": ["u1", ""],
            },
            {
                "_id": "2026-08-21",
                "page_views": 0,
                "action_invocations": 0,
                "sessions": [],
                "actors": [],
            },
            {"_id": "", "page_views": 99},
        ]
    )
    metrics = AppMetrics(
        SimpleNamespace(app_id="app_1"), persistence=_Persistence(collection)
    )
    rollups = await metrics.usage_rollup()
    assert rollups == [
        {
            "period_start": "2026-08-20",
            "granularity": "day",
            "page_views": 12,
            "action_invocations": 4,
            "unique_sessions": 2,
            "active_users": 1,
        },
        {
            "period_start": "2026-08-21",
            "granularity": "day",
            "page_views": 0,
            "action_invocations": 0,
            "unique_sessions": 0,
            "active_users": 0,
        },
    ]


@pytest.mark.asyncio
async def test_usage_rollup_matches_usage_events_only():
    collection = _RollupCollection([])
    metrics = AppMetrics(
        SimpleNamespace(app_id="app_1"), persistence=_Persistence(collection)
    )
    await metrics.usage_rollup()
    match = collection.pipelines[0][0]["$match"]
    assert set(match["event_name"]["$in"]) == {
        USAGE_PAGE_VIEW_EVENT,
        USAGE_ACTION_EVENT,
    }


# ---------------------------------------------------------------------------
# Instrumentation gate and fire-and-forget behavior
# ---------------------------------------------------------------------------

def test_usage_metrics_enabled_by_default(monkeypatch):
    monkeypatch.delenv("MOZAIKS_USAGE_METRICS", raising=False)
    assert usage_metrics_enabled() is True


def test_usage_metrics_disabled_by_flag(monkeypatch):
    monkeypatch.setenv("MOZAIKS_USAGE_METRICS", "0")
    assert usage_metrics_enabled() is False


def test_record_page_view_noop_when_disabled(monkeypatch):
    monkeypatch.setenv("MOZAIKS_USAGE_METRICS", "false")
    with patch(
        "mozaiksai.core.metrics.usage_instrumentation._fire"
    ) as fire:
        record_page_view(app_id="app_1", page_name="home")
    fire.assert_not_called()


def test_record_page_view_safe_without_event_loop(monkeypatch):
    monkeypatch.delenv("MOZAIKS_USAGE_METRICS", raising=False)
    # No running loop: must silently no-op, never raise.
    record_page_view(app_id="app_1", page_name="home")


@pytest.mark.asyncio
async def test_record_action_invocation_tracks_event(monkeypatch):
    monkeypatch.delenv("MOZAIKS_USAGE_METRICS", raising=False)
    tracked: list[tuple[str, dict[str, Any]]] = []

    class _FakeMetrics:
        async def track(self, event_name, **kwargs):
            tracked.append((event_name, kwargs))
            return {}

    with patch(
        "mozaiksai.core.metrics.usage_instrumentation._metrics_for",
        return_value=_FakeMetrics(),
    ):
        record_action_invocation(
            app_id="app_1", module_id="wallet", action_id="get_summary", user_id="u1"
        )
        await asyncio.sleep(0)

    assert tracked, "expected the instrumentation task to record an event"
    event_name, kwargs = tracked[0]
    assert event_name == USAGE_ACTION_EVENT
    assert kwargs["dimensions"] == {"module_id": "wallet", "action": "get_summary"}


@pytest.mark.asyncio
async def test_instrumentation_swallows_track_failures(monkeypatch):
    monkeypatch.delenv("MOZAIKS_USAGE_METRICS", raising=False)
    broken = MagicMock()
    broken.track = AsyncMock(side_effect=RuntimeError("db down"))
    with patch(
        "mozaiksai.core.metrics.usage_instrumentation._metrics_for",
        return_value=broken,
    ):
        record_page_view(app_id="app_1", page_name="home")
        await asyncio.sleep(0)
    # No exception surfaced — success.
