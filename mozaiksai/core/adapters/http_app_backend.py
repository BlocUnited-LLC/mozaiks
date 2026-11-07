# ==============================================================================
# FILE: mozaiksai/core/adapters/http_app_backend.py
# DESCRIPTION: HttpAppBackendAdapter — generic HTTP adapter implementing AppBackendPort
#              Any CRUD backend reachable over HTTP works out of the box.
# ==============================================================================
from __future__ import annotations

import logging
import os
from hashlib import sha256
from typing import Any

import httpx

from mozaiksai.core.adapters.circuit_breaker import (
    CircuitOpenError,
    get_circuit_breaker_sync,
)
from mozaiksai.core.ports.app_backend import (
    BackendHealth,
    BackendResponse,
)

logger = logging.getLogger("mozaiksai.adapters.http_app_backend")

_DEFAULT_BASE_URL = "http://localhost:8000"


def _reject_json_constant(_value: str) -> None:
    raise ValueError("Non-JSON numeric constant")


class HttpAppBackendAdapter:
    """Generic HTTP adapter for any app backend.

    Configuration (env vars):
        MOZAIKS_BACKEND_URL  — base URL of the app backend
        INTERNAL_API_KEY     — optional shared secret for service-to-service auth

    Set ``trust_env=False`` for private transports that must ignore environment
    proxies and certificate settings. An explicit empty ``internal_api_key``
    disables ambient key lookup. Circuit state is shared only by adapters for
    the same normalized base URL, independent of credentials and request paths.
    The registry has no eviction; use fixed configured URLs rather than URLs
    supplied per request. ``circuit_breaker_enabled=False`` bypasses it entirely.
    ``strict_json=True`` rejects malformed successful responses instead of
    returning raw text; an empty HTTP 204 succeeds with no data.
    """

    def __init__(
        self,
        base_url: str | None = None,
        internal_api_key: str | None = None,
        timeout: float = 30.0,
        *,
        connect_timeout: float = 10.0,
        trust_env: bool = True,
        strict_json: bool = False,
        circuit_breaker_enabled: bool = True,
    ) -> None:
        self._base_url = (  # type: ignore[union-attr]
            base_url
            or os.getenv("MOZAIKS_BACKEND_URL", _DEFAULT_BASE_URL)
        ).rstrip("/")
        self._internal_key = (
            os.getenv("INTERNAL_API_KEY", "")
            if internal_api_key is None else internal_api_key
        )
        self._timeout = httpx.Timeout(timeout, connect=connect_timeout)
        self._trust_env = trust_env
        self._strict_json = strict_json
        self._circuit_breaker_enabled = circuit_breaker_enabled
        upstream = httpx.URL(self._base_url).copy_with(
            username="", password="", query=None, fragment=None,
        )
        # Registry names appear in logs; do not put URLs or credentials there.
        self._circuit_name = f"app_backend:{sha256(str(upstream).encode()).hexdigest()}"

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

    async def _do_request(
        self,
        method: str,
        url: str,
        merged_headers: dict[str, str],
        json_body: dict[str, Any] | None,
    ) -> BackendResponse:
        """Inner HTTP call — wrapped by circuit breaker."""
        try:
            async with httpx.AsyncClient(
                timeout=self._timeout,
                trust_env=self._trust_env,
                follow_redirects=False,
            ) as client:
                resp = await client.request(
                    method=method,
                    url=url,
                    headers=merged_headers,
                    json=json_body,
                )
        except httpx.HTTPError as exc:
            # The shared breaker logs exceptions. Transport messages can include
            # sensitive URLs or headers, so retain the category, not their text.
            raise httpx.HTTPError(type(exc).__name__) from None
        if 200 <= resp.status_code < 300:
            if self._strict_json and resp.status_code == 204 and not resp.content:
                return BackendResponse(success=True, status_code=204)
            try:
                data = (
                    resp.json(parse_constant=_reject_json_constant)
                    if self._strict_json else resp.json()
                )
            except Exception:
                logger.warning(
                    "BACKEND_RESPONSE_NON_JSON status=%d", resp.status_code,
                )
                if self._strict_json:
                    return BackendResponse(
                        success=False,
                        status_code=resp.status_code,
                        error="backend_invalid_response",
                    )
                data = {"raw": resp.text}
            return BackendResponse(success=True, status_code=resp.status_code, data=data)
        # Treat 5xx as errors so the circuit breaker counts them.
        if resp.status_code >= 500:
            raise httpx.HTTPStatusError(
                f"HTTP {resp.status_code}",
                request=resp.request,
                response=resp,
            )
        return BackendResponse(
            success=False,
            status_code=resp.status_code,
            error=f"HTTP {resp.status_code}: {resp.text[:500]}",
        )

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
            if not self._circuit_breaker_enabled:
                return await self._do_request(method, url, merged_headers, json_body)
            breaker = get_circuit_breaker_sync(self._circuit_name)
            result = await breaker.call(self._do_request, method, url, merged_headers, json_body)
            return result  # type: ignore[no-any-return]
        except CircuitOpenError:
            logger.warning("BACKEND_CIRCUIT_OPEN name=%s", self._circuit_name)
            return BackendResponse(success=False, error="backend_circuit_open")
        except httpx.HTTPError as exc:
            logger.error("BACKEND_REQUEST_FAILED name=%s", self._circuit_name)
            return BackendResponse(
                success=False,
                status_code=exc.response.status_code if isinstance(exc, httpx.HTTPStatusError) else 0,
                error="backend_unavailable",
            )

    # ------------------------------------------------------------------
    # AppBackendPort.emit
    # ------------------------------------------------------------------

    async def emit(self, event_type: str, data: dict[str, Any]) -> bool:
        try:
            from mozaiksai.core.events.unified_event_dispatcher import get_event_dispatcher
            dispatcher = get_event_dispatcher()
            await dispatcher.emit(event_type, data)
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
            return BackendHealth(healthy=False, details={"error": "health_check_failed"})


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
