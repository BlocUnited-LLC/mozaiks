# ==============================================================================
# FILE: mozaiksai/core/adapters/http_app_backend.py
# DESCRIPTION: HttpAppBackendAdapter — generic HTTP adapter implementing AppBackendPort
#              Any CRUD backend reachable over HTTP works out of the box.
# ==============================================================================
from __future__ import annotations

import logging
import os
from typing import Any

import httpx

from mozaiksai.core.ports.app_backend import (
    BackendHealth,
    BackendResponse,
)

logger = logging.getLogger("mozaiksai.adapters.http_app_backend")

_DEFAULT_BASE_URL = "http://localhost:8000"


class HttpAppBackendAdapter:
    """Generic HTTP adapter for any app backend.

    Configuration (env vars):
        MOZAIKS_BACKEND_URL  — base URL of the app backend
        INTERNAL_API_KEY     — optional shared secret for service-to-service auth
    """

    def __init__(
        self,
        base_url: str | None = None,
        internal_api_key: str | None = None,
        timeout: float = 30.0,
    ) -> None:
        self._base_url = (  # type: ignore[union-attr]
            base_url
            or os.getenv("MOZAIKS_BACKEND_URL", _DEFAULT_BASE_URL)
        ).rstrip("/")
        self._internal_key = internal_api_key or os.getenv("INTERNAL_API_KEY", "")
        self._timeout = httpx.Timeout(timeout, connect=10.0)

    # ------------------------------------------------------------------
    # Header helpers
    # ------------------------------------------------------------------

    def _build_headers(
        self,
        *,
        user_token: str | None = None,
        extra: dict[str, str] | None = None,
    ) -> dict[str, str]:
        headers: dict[str, str] = {"Content-Type": "application/json"}
        if self._internal_key:
            headers["X-Internal-API-Key"] = self._internal_key
        if user_token:
            headers["Authorization"] = f"Bearer {user_token}"
        if extra:
            headers.update(extra)
        return headers

    # ------------------------------------------------------------------
    # AppBackendPort.request
    # ------------------------------------------------------------------

    async def request(
        self,
        method: str,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        user_token: str | None = None,
    ) -> BackendResponse:
        url = f"{self._base_url}{path}"
        merged_headers = self._build_headers(user_token=user_token, extra=headers)
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.request(
                    method=method,
                    url=url,
                    headers=merged_headers,
                    json=json_body,
                )
            if 200 <= resp.status_code < 300:
                try:
                    data = resp.json()
                except Exception as _json_exc:
                    logger.warning(
                        "BACKEND_RESPONSE_NON_JSON method=%s path=%s status=%d: %s",
                        method,
                        path,
                        resp.status_code,
                        _json_exc,
                    )
                    data = {"raw": resp.text}
                return BackendResponse(success=True, status_code=resp.status_code, data=data)
            return BackendResponse(
                success=False,
                status_code=resp.status_code,
                error=f"HTTP {resp.status_code}: {resp.text[:500]}",
            )
        except httpx.HTTPError as exc:
            logger.error("Backend request failed %s %s: %s", method, path, exc)
            return BackendResponse(success=False, error=str(exc))

    # ------------------------------------------------------------------
    # AppBackendPort.emit
    # ------------------------------------------------------------------

    async def emit(self, event_type: str, data: dict[str, Any]) -> bool:
        try:
            from mozaiksai.core.events.unified_event_dispatcher import get_event_dispatcher
            dispatcher = get_event_dispatcher()
            await dispatcher.dispatch(event_type, data)  # type: ignore[arg-type,call-arg]
            logger.debug("Emitted event '%s'", event_type)
            return True
        except Exception as exc:
            logger.debug("Could not emit '%s': %s", event_type, exc)
            return False

    # ------------------------------------------------------------------
    # AppBackendPort.health
    # ------------------------------------------------------------------

    async def health(self) -> BackendHealth:
        try:
            resp = await self.request("GET", "/health")
            if resp.success:
                return BackendHealth(
                    healthy=True,
                    version=(resp.data or {}).get("version", "unknown"),
                    details=resp.data,
                )
            return BackendHealth(healthy=False, details={"error": resp.error})
        except Exception as exc:
            logger.error("Health check failed: %s", exc)
            return BackendHealth(healthy=False, details={"error": str(exc)})


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_adapter: HttpAppBackendAdapter | None = None


def get_app_backend() -> HttpAppBackendAdapter:
    """Return the process-wide app-backend adapter singleton."""
    global _adapter
    if _adapter is None:
        _adapter = HttpAppBackendAdapter()
    return _adapter


__all__ = ["HttpAppBackendAdapter", "get_app_backend"]
