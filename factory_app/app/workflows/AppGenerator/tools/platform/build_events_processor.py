
import asyncio
import os
from typing import Any, Dict, Optional

from logs.logging_config import get_core_logger

from .build_events_client import BuildEventsClient
from .build_events_outbox import ensure_indexes, list_due_events, mark_attempt

logger = get_core_logger("platform_build_events_processor")


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in ("1", "true", "yes", "y", "on")


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return int(default)
    try:
        return int(str(raw).strip())
    except Exception:
        return int(default)


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None:
        return float(default)
    try:
        return float(str(raw).strip())
    except Exception:
        return float(default)


class BuildEventsProcessor:
    """Background processor for pending platform build-events notifications.

    This is an *optional* reliability layer: it ensures outbox events are retried
    until the platform accepts them, without blocking workflow execution.
    """

    def __init__(
        self,
        *,
        client: Optional[BuildEventsClient] = None,
        enabled: Optional[bool] = None,
        poll_interval_sec: Optional[float] = None,
        batch_size: Optional[int] = None,
        concurrency: Optional[int] = None,
    ) -> None:
        self._enabled = (
            _env_bool("MOZAIKS_PLATFORM_BUILD_EVENTS_RETRY_ENABLED", True)
            if enabled is None
            else bool(enabled)
        )
        self._poll_interval = (
            float(poll_interval_sec)
            if poll_interval_sec is not None
            else _env_float("MOZAIKS_PLATFORM_BUILD_EVENTS_RETRY_POLL_SEC", 5.0)
        )
        self._batch_size = (
            int(batch_size)
            if batch_size is not None
            else _env_int("MOZAIKS_PLATFORM_BUILD_EVENTS_RETRY_BATCH", 25)
        )
        self._concurrency = (
            int(concurrency)
            if concurrency is not None
            else _env_int("MOZAIKS_PLATFORM_BUILD_EVENTS_RETRY_CONCURRENCY", 5)
        )
        self._client = client or BuildEventsClient()

        self._task: Optional[asyncio.Task] = None
        self._stop = asyncio.Event()
        self._sem = asyncio.Semaphore(max(1, self._concurrency))

    def start(self) -> Optional[asyncio.Task]:
        if not self._enabled:
            logger.info("BuildEventsProcessor disabled (MOZAIKS_PLATFORM_BUILD_EVENTS_RETRY_ENABLED=0)")
            return None
        if not self._client.enabled() or not self._client.configured():
            logger.info(
                "BuildEventsProcessor not started (platform build-events client disabled or not configured)",
                extra={"client_enabled": self._client.enabled(), "client_configured": self._client.configured()},
            )
            return None
        if self._task and not self._task.done():
            return self._task
        self._stop.clear()
        self._task = asyncio.create_task(self._run_loop(), name="build_events_outbox_processor")
        return self._task

    async def stop(self) -> None:
        self._stop.set()
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except Exception:
                pass

    async def _run_loop(self) -> None:
        await ensure_indexes()
        while not self._stop.is_set():
            try:
                due = await list_due_events(limit=self._batch_size)
                if not due:
                    try:
                        await asyncio.wait_for(self._stop.wait(), timeout=max(0.5, self._poll_interval))
                    except asyncio.TimeoutError:
                        pass
                    continue

                tasks = [asyncio.create_task(self._process_one(doc)) for doc in due if isinstance(doc, dict)]
                if tasks:
                    await asyncio.gather(*tasks, return_exceptions=True)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # pragma: no cover
                logger.error("BuildEventsProcessor loop error: %s", exc, exc_info=True)
                await asyncio.sleep(max(1.0, self._poll_interval))

    async def _process_one(self, doc: Dict[str, Any]) -> None:
        outbox_id = str(doc.get("_id") or "").strip()
        app_id = str(doc.get("app_id") or "").strip()
        payload = doc.get("payload")
        if not outbox_id or not app_id or not isinstance(payload, dict):
            return

        async with self._sem:
            try:
                result = await self._client.post_build_event(app_id=app_id, payload=payload)
                await mark_attempt(
                    outbox_id=outbox_id,
                    ok=result.ok,
                    status_code=result.status_code,
                    error=result.error,
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # pragma: no cover
                await mark_attempt(outbox_id=outbox_id, ok=False, error=str(exc))


__all__ = ["BuildEventsProcessor"]
