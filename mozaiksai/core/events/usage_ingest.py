from __future__ import annotations

import asyncio
import os
import random
from typing import Any, Dict, Optional

import aiohttp

from logs.logging_config import get_core_logger

logger = get_core_logger("usage_ingest")


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in ("1", "true", "yes", "y", "on")


class UsageIngestClient:
    """Best-effort control-plane usage ingest (measurement only)."""

    def __init__(
        self,
        *,
        url: Optional[str] = None,
        enabled: Optional[bool] = None,
        timeout_sec: float = 5.0,
        max_attempts: int = 3,
        backoff_base_sec: float = 0.5,
        backoff_max_sec: float = 5.0,
    ) -> None:
        self._enabled = (
            _env_bool("CONTROL_PLANE_USAGE_INGEST_ENABLED", False)
            if enabled is None
            else bool(enabled)
        )
        self._url = (url or os.getenv("CONTROL_PLANE_USAGE_INGEST_URL", "")).strip()
        self._timeout = aiohttp.ClientTimeout(total=float(timeout_sec))
        self._max_attempts = max(1, int(max_attempts))
        self._backoff_base = max(0.05, float(backoff_base_sec))
        self._backoff_max = max(self._backoff_base, float(backoff_max_sec))

    def enabled(self) -> bool:
        return bool(self._enabled) and bool(self._url)

    async def handle_usage_summary(self, payload: Dict[str, Any]) -> None:
        if not self.enabled():
            return
        if not isinstance(payload, dict):
            return

        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

        last_err: Optional[str] = None
        last_status: Optional[int] = None

        for attempt in range(self._max_attempts):
            try:
                async with aiohttp.ClientSession(timeout=self._timeout) as session:
                    async with session.post(self._url, json=payload, headers=headers) as resp:
                        last_status = int(resp.status)
                        if resp.status in (200, 201, 202, 204) or resp.status == 409:
                            return

                        body = (await resp.text()).strip()
                        if len(body) > 2000:
                            body = body[:2000] + "..."
                        last_err = f"http_{resp.status}: {body}"

                        # Retry on transient errors only.
                        if resp.status == 429 or resp.status >= 500:
                            raise RuntimeError(last_err)

                        logger.debug(
                            "control_plane_usage_ingest_rejected",
                            extra={"status": last_status, "error": last_err},
                        )
                        return
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                last_err = str(exc) or last_err or "request_failed"
                if attempt >= self._max_attempts - 1:
                    break
                base = min(self._backoff_max, self._backoff_base * (2**attempt))
                delay = base + random.uniform(0.0, min(0.25, base / 2))
                await asyncio.sleep(delay)

        logger.debug(
            "control_plane_usage_ingest_failed",
            extra={"status": last_status, "error": last_err},
        )


_global_client: Optional[UsageIngestClient] = None


def get_usage_ingest_client() -> UsageIngestClient:
    global _global_client
    if _global_client is None:
        _global_client = UsageIngestClient()
    return _global_client

