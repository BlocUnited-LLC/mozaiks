# ==============================================================================
# FILE: core/adapters/core_client.py
# DESCRIPTION: CoreServiceClient — HTTP adapter implementing CoreServicePort.
#              Calls mozaikscore REST API over internal network.
#              Follows the same singleton pattern as AG2OrchestrationAdapter.
# ==============================================================================
from __future__ import annotations

import os
import logging
from typing import Any, Dict, Optional

import httpx

from mozaiksai.core.ports.core_service import (
    CoreServicePort,
    ModuleRequest,
    ModuleResult,
    NotificationRequest,
    SubstrateHealth,
)

logger = logging.getLogger("mozaiksai.adapters.core_client")


class CoreServiceClient:
    """Implements :class:`CoreServicePort` via HTTP calls to the mozaikscore substrate.

    Lifecycle:
        1. Singleton per process — created at startup via ``get_core_client()``.
        2. AG2 tool functions (core_bridge) call methods on this adapter.
        3. Transport layer may also proxy admin calls.

    Configuration:
        MOZAIKSCORE_URL   — base URL (default: http://localhost:8001)
        INTERNAL_API_KEY  — shared secret for internal-only endpoints
    """

    def __init__(self) -> None:
        self._base_url = os.getenv("MOZAIKSCORE_URL", "http://localhost:8001").rstrip("/")
        self._internal_key = os.getenv("INTERNAL_API_KEY", "")
        self._timeout = httpx.Timeout(30.0, connect=10.0)

    # ------------------------------------------------------------------
    # Internal HTTP helpers
    # ------------------------------------------------------------------

    def _internal_headers(self) -> Dict[str, str]:
        """Headers for internal (service-to-service) calls."""
        headers: Dict[str, str] = {"Content-Type": "application/json"}
        if self._internal_key:
            headers["X-Internal-API-Key"] = self._internal_key
        return headers

    def _auth_headers(self, token: str) -> Dict[str, str]:
        """Headers for user-scoped calls that need JWT forwarding."""
        headers: Dict[str, str] = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        if self._internal_key:
            headers["X-Internal-API-Key"] = self._internal_key
        return headers

    async def _request(
        self,
        method: str,
        path: str,
        headers: Dict[str, str],
        json_body: Optional[Dict[str, Any]] = None,
    ) -> httpx.Response:
        """Execute an HTTP request against mozaikscore."""
        url = f"{self._base_url}{path}"
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            response = await client.request(
                method=method,
                url=url,
                headers=headers,
                json=json_body,
            )
        return response

    # ------------------------------------------------------------------
    # CoreServicePort.execute_module
    # ------------------------------------------------------------------

    async def execute_module(self, request: ModuleRequest) -> ModuleResult:
        """Execute a mozaikscore module via POST /api/execute/{module_name}."""
        try:
            payload = {
                "action": request.action,
                **request.payload,
            }
            resp = await self._request(
                "POST",
                f"/api/execute/{request.module_name}",
                self._internal_headers(),
                json_body=payload,
            )
            if resp.status_code == 200:
                return ModuleResult(success=True, data=resp.json())
            else:
                return ModuleResult(
                    success=False,
                    error=f"HTTP {resp.status_code}: {resp.text[:500]}",
                )
        except httpx.HTTPError as exc:
            logger.error("execute_module failed module=%s: %s", request.module_name, exc)
            return ModuleResult(success=False, error=str(exc))

    # ------------------------------------------------------------------
    # CoreServicePort.get_navigation
    # ------------------------------------------------------------------

    async def get_navigation(self, user_id: str, token: str) -> Dict[str, Any]:
        """Fetch navigation config via GET /api/navigation."""
        try:
            resp = await self._request("GET", "/api/navigation", self._auth_headers(token))
            if resp.status_code == 200:
                return resp.json()
            logger.warning("get_navigation HTTP %d for user=%s", resp.status_code, user_id)
            return {"navigation": []}
        except httpx.HTTPError as exc:
            logger.error("get_navigation failed user=%s: %s", user_id, exc)
            return {"navigation": []}

    # ------------------------------------------------------------------
    # CoreServicePort.get_user_profile
    # ------------------------------------------------------------------

    async def get_user_profile(self, user_id: str, token: str) -> Dict[str, Any]:
        """Fetch user profile via GET /api/user-profile."""
        try:
            resp = await self._request("GET", "/api/user-profile", self._auth_headers(token))
            if resp.status_code == 200:
                return resp.json()
            logger.warning("get_user_profile HTTP %d for user=%s", resp.status_code, user_id)
            return {}
        except httpx.HTTPError as exc:
            logger.error("get_user_profile failed user=%s: %s", user_id, exc)
            return {}

    # ------------------------------------------------------------------
    # CoreServicePort.get_subscription
    # ------------------------------------------------------------------

    async def get_subscription(self, user_id: str, token: str) -> Dict[str, Any]:
        """Fetch subscription status via GET /api/user-subscription."""
        try:
            resp = await self._request("GET", "/api/user-subscription", self._auth_headers(token))
            if resp.status_code == 200:
                return resp.json()
            logger.warning("get_subscription HTTP %d for user=%s", resp.status_code, user_id)
            return {"plan": "free", "status": "unknown"}
        except httpx.HTTPError as exc:
            logger.error("get_subscription failed user=%s: %s", user_id, exc)
            return {"plan": "free", "status": "unknown"}

    # ------------------------------------------------------------------
    # CoreServicePort.create_notification
    # ------------------------------------------------------------------

    async def create_notification(self, request: NotificationRequest) -> bool:
        """Create a notification via POST /__mozaiks/admin/notifications/broadcast."""
        try:
            payload = {
                "user_ids": [request.user_id],
                "title": request.title,
                "message": request.message,
                "category": request.category,
            }
            if request.channels:
                payload["channels"] = request.channels
            if request.metadata:
                payload["metadata"] = request.metadata
            resp = await self._request(
                "POST",
                "/__mozaiks/admin/notifications/broadcast",
                self._internal_headers(),
                json_body=payload,
            )
            return resp.status_code == 200
        except httpx.HTTPError as exc:
            logger.error("create_notification failed user=%s: %s", request.user_id, exc)
            return False

    # ------------------------------------------------------------------
    # CoreServicePort.admin_list_users
    # ------------------------------------------------------------------

    async def admin_list_users(
        self, token: str, page: int = 1, per_page: int = 20
    ) -> Dict[str, Any]:
        """Paginated user listing via GET /__mozaiks/admin/users."""
        try:
            resp = await self._request(
                "GET",
                f"/__mozaiks/admin/users?page={page}&per_page={per_page}",
                self._auth_headers(token),
            )
            if resp.status_code == 200:
                return resp.json()
            logger.warning("admin_list_users HTTP %d", resp.status_code)
            return {"users": [], "total": 0}
        except httpx.HTTPError as exc:
            logger.error("admin_list_users failed: %s", exc)
            return {"users": [], "total": 0}

    # ------------------------------------------------------------------
    # CoreServicePort.admin_get_analytics
    # ------------------------------------------------------------------

    async def admin_get_analytics(self, token: str) -> Dict[str, Any]:
        """Fetch KPI analytics via GET /__mozaiks/admin/analytics/kpis."""
        try:
            resp = await self._request(
                "GET",
                "/__mozaiks/admin/analytics/kpis",
                self._auth_headers(token),
            )
            if resp.status_code == 200:
                return resp.json()
            logger.warning("admin_get_analytics HTTP %d", resp.status_code)
            return {}
        except httpx.HTTPError as exc:
            logger.error("admin_get_analytics failed: %s", exc)
            return {}

    # ------------------------------------------------------------------
    # CoreServicePort.health
    # ------------------------------------------------------------------

    async def health(self) -> SubstrateHealth:
        """Check mozaikscore health via GET /."""
        try:
            resp = await self._request("GET", "/", self._internal_headers())
            if resp.status_code == 200:
                data = resp.json()
                return SubstrateHealth(
                    healthy=True,
                    version=data.get("version", "unknown"),
                    details=data,
                )
            return SubstrateHealth(healthy=False, details={"status_code": resp.status_code})
        except httpx.HTTPError as exc:
            logger.error("health check failed: %s", exc)
            return SubstrateHealth(healthy=False, details={"error": str(exc)})

    # ------------------------------------------------------------------
    # Cross-substrate event relay: mozaiksai → mozaikscore
    # ------------------------------------------------------------------

    async def relay_event(self, event_type: str, data: Dict[str, Any]) -> bool:
        """Relay an event to mozaikscore's internal event bus.

        Used by UnifiedEventDispatcher handlers to forward workflow events
        (e.g., workflow_completed, agent_output) to the application substrate.
        """
        payload = {
            "source": "mozaiksai",
            "event": event_type,
            "data": data,
        }
        try:
            resp = await self._request(
                "POST",
                "/__mozaiks/internal/relay-event",
                self._internal_headers(),
                json_body=payload,
            )
            if resp.status_code == 200:
                logger.debug("Relayed event '%s' to mozaikscore", event_type)
                return True
            logger.warning("relay_event HTTP %d for '%s'", resp.status_code, event_type)
            return False
        except httpx.HTTPError as exc:
            logger.debug("Could not relay '%s' to mozaikscore: %s", event_type, exc)
            return False

    # ------------------------------------------------------------------
    # Capabilities (informational, mirrors OrchestrationPort pattern)
    # ------------------------------------------------------------------

    def capabilities(self) -> Dict[str, Any]:
        return {
            "substrate": "mozaikscore",
            "base_url": self._base_url,
            "supports_modules": True,
            "supports_notifications": True,
            "supports_subscriptions": True,
            "supports_admin": True,
        }


# ---------------------------------------------------------------------------
# Module-level singleton accessor
# ---------------------------------------------------------------------------

_client: Optional[CoreServiceClient] = None


def get_core_client() -> CoreServiceClient:
    """Return the process-wide CoreServiceClient singleton."""
    global _client
    if _client is None:
        _client = CoreServiceClient()
    return _client


__all__ = ["CoreServiceClient", "get_core_client"]
