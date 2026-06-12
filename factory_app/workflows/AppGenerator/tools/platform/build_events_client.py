
import asyncio
import os
import random
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Optional

import aiohttp

from logs.logging_config import get_core_logger

logger = get_core_logger("platform_build_events")


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in ("1", "true", "yes", "y", "on")


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _default_platform_base_url() -> str:
    for key in ("MOZAIKS_PLATFORM_BASE_URL", "MOZAIKS_BACKEND_URL"):
        raw = os.getenv(key)
        if raw and str(raw).strip():
            return str(raw).strip().rstrip("/")
    return ""


def _default_platform_api_key() -> str:
    for key in ("MOZAIKS_PLATFORM_INTERNAL_API_KEY", "INTERNAL_API_KEY"):
        raw = os.getenv(key)
        if raw and str(raw).strip():
            return str(raw).strip()
    return ""


@dataclass(frozen=True)
class BuildEventResult:
    ok: bool
    status_code: Optional[int] = None
    error: Optional[str] = None


class BuildEventsClient:
    """Async client for notifying the Mozaiks platform about build lifecycle events.

    Canonical platform endpoint:
      POST /api/apps/{appId}/build-events

    This client MUST be resilient and MUST NOT crash workflows if the platform is unavailable.
    """

    def __init__(
        self,
        *,
        base_url: Optional[str] = None,
        api_key: Optional[str] = None,
        enabled: Optional[bool] = None,
        timeout_sec: float = 10.0,
        max_attempts: int = 5,
        backoff_base_sec: float = 0.5,
        backoff_max_sec: float = 10.0,
    ) -> None:
        self._enabled = _env_bool("MOZAIKS_PLATFORM_BUILD_EVENTS_ENABLED", True) if enabled is None else bool(enabled)
        # Treat explicit empty strings as an override (do not fall back to env defaults).
        resolved_base_url = base_url if base_url is not None else _default_platform_base_url()
        resolved_api_key = api_key if api_key is not None else _default_platform_api_key()
        self._base_url = str(resolved_base_url or "").strip().rstrip("/")
        self._api_key = str(resolved_api_key or "").strip()
        self._timeout = aiohttp.ClientTimeout(total=float(timeout_sec))
        self._max_attempts = max(1, int(max_attempts))
        self._backoff_base = max(0.05, float(backoff_base_sec))
        self._backoff_max = max(self._backoff_base, float(backoff_max_sec))

    def enabled(self) -> bool:
        return bool(self._enabled)

    def configured(self) -> bool:
        return bool(self._base_url) and bool(self._api_key)

    def _headers(self, *, event_id: Optional[str] = None, idempotency_key: Optional[str] = None) -> Dict[str, str]:
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "X-Internal-Api-Key": self._api_key,
        }
        if event_id:
            headers["X-Event-Id"] = str(event_id)
        if idempotency_key:
            headers["X-Idempotency-Key"] = str(idempotency_key)
        return headers

    async def post_build_event(self, *, app_id: str, payload: Dict[str, Any]) -> BuildEventResult:
        if not self._enabled:
            return BuildEventResult(ok=False, error="disabled")
        if not self.configured():
            logger.info(
                "Platform build-events client not configured; skipping",
                extra={"has_base_url": bool(self._base_url), "has_api_key": bool(self._api_key)},
            )
            return BuildEventResult(ok=False, error="not_configured")

        app_id_norm = str(app_id or "").strip()
        if not app_id_norm:
            return BuildEventResult(ok=False, error="missing_app_id")

        url = f"{self._base_url}/api/apps/{app_id_norm}/build-events"

        event_id = None
        idempotency_key = None
        try:
            if isinstance(payload, dict):
                event_id = payload.get("eventId") or payload.get("event_id")
                idempotency_key = payload.get("idempotencyKey") or payload.get("idempotency_key")
        except Exception:
            pass

        last_err: Optional[str] = None
        last_status: Optional[int] = None

        for attempt in range(self._max_attempts):
            try:
                async with aiohttp.ClientSession(timeout=self._timeout) as session:
                    async with session.post(
                        url,
                        json=payload,
                        headers=self._headers(event_id=event_id, idempotency_key=idempotency_key),
                    ) as resp:
                        last_status = int(resp.status)
                        if resp.status in (200, 201, 202, 204):
                            return BuildEventResult(ok=True, status_code=last_status)

                        # 409 often means "already processed" under idempotent write semantics.
                        if resp.status == 409:
                            return BuildEventResult(ok=True, status_code=last_status)

                        body = await resp.text()
                        body = (body or "").strip()
                        if len(body) > 2000:
                            body = body[:2000] + "…"
                        last_err = f"http_{resp.status}: {body}"

                        # Retry on transient conditions only.
                        if resp.status == 429 or resp.status >= 500:
                            raise RuntimeError(last_err)

                        # For 4xx (except 429/409), do not retry.
                        return BuildEventResult(ok=False, status_code=last_status, error=last_err)

            except asyncio.CancelledError:
                raise
            except Exception as exc:
                last_err = str(exc) or last_err or "request_failed"
                if attempt >= self._max_attempts - 1:
                    break
                # Exponential backoff with bounded jitter.
                base = min(self._backoff_max, self._backoff_base * (2**attempt))
                delay = base + random.uniform(0.0, min(0.25, base / 2))
                await asyncio.sleep(delay)

        return BuildEventResult(ok=False, status_code=last_status, error=last_err)


__all__ = ["BuildEventsClient", "BuildEventResult", "_utc_iso"]

