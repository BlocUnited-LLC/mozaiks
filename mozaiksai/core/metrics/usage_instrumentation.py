"""Generic usage instrumentation for the platform host.

Records two fire-and-forget metric events into the app's own AppMetrics
store (``app_metrics/events`` inside the app's database — nothing leaves
the app):

- ``app.page_view``      one per declarative page-schema serve
- ``app.action_invoked`` one per successful module action dispatch

Env gate: ``MOZAIKS_USAGE_METRICS`` (default on; set to ``0``/``false`` to
disable). Failures are swallowed — instrumentation must never affect a
request. Reporting these counts anywhere (e.g. a hosting operator) is a
separate, explicitly configured concern; see the ``cloud_usage_reporter``
template in the mozaiks_cloud build-context pack.
"""
from __future__ import annotations

import asyncio
import logging
import os
from types import SimpleNamespace
from typing import Any

from mozaiksai.core.metrics.app_metrics import (
    USAGE_ACTION_EVENT,
    USAGE_PAGE_VIEW_EVENT,
    AppMetrics,
)

logger = logging.getLogger("mozaiksai.usage_instrumentation")

_DISABLED_VALUES = {"0", "false", "no", "off"}


def usage_metrics_enabled() -> bool:
    value = str(os.environ.get("MOZAIKS_USAGE_METRICS", "")).strip().lower()
    return value not in _DISABLED_VALUES


def _metrics_for(app_id: str, *, user_id: str | None = None) -> AppMetrics | None:
    app_id = str(app_id or "").strip()
    if not app_id:
        return None
    ctx = SimpleNamespace(app_id=app_id, user_id=user_id)
    return AppMetrics(ctx)


async def _track_silent(
    app_id: str,
    event_name: str,
    *,
    user_id: str | None = None,
    session_id: str | None = None,
    dimensions: dict[str, Any] | None = None,
) -> None:
    try:
        metrics = _metrics_for(app_id, user_id=user_id)
        if metrics is None:
            return
        await metrics.track(
            event_name,
            session_id=session_id,
            visibility="admin",
            dimensions=dimensions or {},
        )
    except Exception as exc:
        logger.debug("USAGE_INSTRUMENTATION_DROPPED %s: %s", event_name, exc)


def _fire(coro) -> None:
    """Schedule instrumentation without blocking the request path."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    task = loop.create_task(coro)
    task.add_done_callback(lambda t: t.exception() if not t.cancelled() else None)


def record_page_view(
    *,
    app_id: str,
    page_name: str,
    user_id: str | None = None,
    session_id: str | None = None,
) -> None:
    """Fire-and-forget page-view event. Safe to call from any request path."""
    if not usage_metrics_enabled():
        return
    _fire(
        _track_silent(
            app_id,
            USAGE_PAGE_VIEW_EVENT,
            user_id=user_id,
            session_id=session_id,
            dimensions={"page": str(page_name or "")[:128]},
        )
    )


def record_action_invocation(
    *,
    app_id: str,
    module_id: str,
    action_id: str,
    user_id: str | None = None,
    session_id: str | None = None,
) -> None:
    """Fire-and-forget module-action event. Safe to call from any request path."""
    if not usage_metrics_enabled():
        return
    _fire(
        _track_silent(
            app_id,
            USAGE_ACTION_EVENT,
            user_id=user_id,
            session_id=session_id,
            dimensions={
                "module_id": str(module_id or "")[:128],
                "action": str(action_id or "")[:128],
            },
        )
    )


__all__ = [
    "record_action_invocation",
    "record_page_view",
    "usage_metrics_enabled",
]
