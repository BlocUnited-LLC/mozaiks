"""UsageReporterService — periodic usage-rollup reporting startup service.

Every interval (MOZAIKS_CLOUD_USAGE_REPORT_INTERVAL_SECONDS, default 3600),
rolls up the app's own AppMetrics usage instrumentation for today and
yesterday and posts the daily aggregates through MozaiksCloudUsageClient.
The receiving end upserts by (app_id, period_start), so re-sending a
partial day simply overwrites it with the fuller count.

Off switches, in order:
- No mozaiks_cloud connector / MOZAIKS_CLOUD_* configuration → silently idle
  (checked every cycle, so configuring later needs no restart).
- MOZAIKS_CLOUD_USAGE_REPORTING=0 → hard off.

Aggregate counts only. No per-user or per-session records leave the app.
"""
from __future__ import annotations

import asyncio
import logging
import os
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

logger = logging.getLogger("app.cloud_usage_reporter")

_DISABLED_VALUES = {"0", "false", "no", "off"}
_INITIAL_DELAY_SECONDS = 90


def _interval_seconds() -> int:
    try:
        return max(
            300,
            int(os.environ.get("MOZAIKS_CLOUD_USAGE_REPORT_INTERVAL_SECONDS", "3600")),
        )
    except ValueError:
        return 3600


def _reporting_enabled() -> bool:
    value = str(os.environ.get("MOZAIKS_CLOUD_USAGE_REPORTING", "")).strip().lower()
    return value not in _DISABLED_VALUES


def _app_id() -> str:
    return str(os.environ.get("MOZAIKS_APP_ID", "") or "default").strip() or "default"


class UsageReporterService:
    """Process-lifetime startup service posting daily usage rollups."""

    def __init__(self) -> None:
        self._running = False
        self._task: asyncio.Task | None = None
        self.last_report_at: str | None = None
        self.last_error: str | None = None

    def start(self) -> None:
        self._running = True
        if not _reporting_enabled():
            logger.info("CLOUD_USAGE_REPORTER_DISABLED: MOZAIKS_CLOUD_USAGE_REPORTING=0")
            return
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
        self._task = loop.create_task(self._run_loop())
        logger.info(
            "CLOUD_USAGE_REPORTER_STARTED: interval=%ss", _interval_seconds()
        )

    def stop(self) -> None:
        self._running = False
        if self._task is not None:
            self._task.cancel()
            self._task = None
        logger.info("CLOUD_USAGE_REPORTER_STOPPED")

    async def _run_loop(self) -> None:
        try:
            await asyncio.sleep(_INITIAL_DELAY_SECONDS)
        except asyncio.CancelledError:
            return
        while self._running:
            try:
                await self.report_once()
            except Exception as exc:
                self.last_error = str(exc)[:300]
                logger.warning("CLOUD_USAGE_REPORTER_ERROR: %s", exc)
            try:
                await asyncio.sleep(_interval_seconds())
            except asyncio.CancelledError:
                break

    async def report_once(self) -> dict:
        from app.services.integrations.mozaiks_cloud_usage_client import (
            MozaiksCloudUsageClient,
        )
        from mozaiksai.core.metrics.app_metrics import AppMetrics

        client = MozaiksCloudUsageClient()
        if not await client.is_configured():
            return {"sent": 0, "reason": "unconfigured"}

        since = (datetime.now(UTC) - timedelta(days=2)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        metrics = AppMetrics(SimpleNamespace(app_id=_app_id()))
        rollups = await metrics.usage_rollup(since=since)
        if not rollups:
            return {"sent": 0, "reason": "no_usage"}

        result = await client.post_usage_rollups(
            rollups,
            idempotency_key=f"usage_{uuid.uuid4().hex}",
        )
        self.last_report_at = datetime.now(UTC).isoformat()
        self.last_error = None
        logger.info(
            "CLOUD_USAGE_REPORTER_SENT: rollups=%d accepted=%s",
            len(rollups),
            (result or {}).get("accepted"),
        )
        return {"sent": len(rollups), "result": result}
