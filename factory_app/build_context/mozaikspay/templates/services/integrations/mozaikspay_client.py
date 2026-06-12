"""Thin MozaiksPay billing client for generated SaaS apps.

This file is copied to:
    services/integrations/mozaikspay_client.py

It is consumer-side adapter code. It must not import hosted provider modules,
call Stripe directly, store secrets, or create a token usage ledger.
"""

from __future__ import annotations

import os
from typing import Any

import httpx

_DEFAULT_TIMEOUT = 20.0
_BILLING_MODULE_PATH = "/api/modules/hosted_billing"


class MozaiksPayError(Exception):
    """Base exception for MozaiksPay client errors."""


class MozaiksPayConfigurationError(MozaiksPayError):
    """Raised when MOZAIKS_APP_URL is not configured."""


class MozaiksPayHTTPError(MozaiksPayError):
    """Raised when the platform returns a non-2xx HTTP status."""

    def __init__(self, status_code: int, detail: str = "") -> None:
        self.status_code = status_code
        super().__init__(f"HTTP {status_code}: {detail}")


class MozaiksPayApplicationError(MozaiksPayError):
    """Raised when the platform returns success=false."""

    def __init__(self, error: str) -> None:
        self.error = error
        super().__init__(error)


class MozaiksPayClient:
    """App-side client for MozaiksPay SaaS billing surfaces."""

    def __init__(
        self,
        base_url: str | None = None,
        auth_token: str | None = None,
        timeout_seconds: float = _DEFAULT_TIMEOUT,
    ) -> None:
        resolved_base = base_url or os.getenv("MOZAIKS_APP_URL", "")
        if not resolved_base:
            raise MozaiksPayConfigurationError(
                "MOZAIKS_APP_URL is not set. Pass base_url= or configure the app host URL."
            )
        self._base_url = resolved_base.rstrip("/")
        self._auth_token = auth_token
        self._timeout = timeout_seconds

    async def get_subscription_status(self) -> dict[str, Any]:
        """Return safe plan display fields from hosted billing."""
        return await self._post_to_module(_BILLING_MODULE_PATH, "get_billing_status", {})

    async def get_usage_status(self, *, limit: int = 500) -> dict[str, Any]:
        """Return runtime AI token usage for the current app user."""
        return await self.get_runtime_ai_usage(limit=limit)

    async def get_runtime_ai_usage(self, *, limit: int = 500) -> dict[str, Any]:
        """Return OSS runtime AI usage from /api/me/usage."""
        bounded_limit = max(1, min(int(limit or 1), 1000))
        return await self._get(f"/api/me/usage?limit={bounded_limit}")

    async def create_billing_portal_session(
        self,
        *,
        return_url: str,
    ) -> dict[str, Any]:
        """Create a hosted billing portal redirect session."""
        return await self._post_to_module(
            _BILLING_MODULE_PATH,
            "create_billing_portal_session",
            {"return_url": return_url},
        )

    async def _post_to_module(
        self,
        module_path: str,
        action_id: str,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        url = f"{self._base_url}{module_path}/{action_id}"
        return await self._request("POST", url, json=payload or {})

    async def _get(self, path: str) -> dict[str, Any]:
        url = f"{self._base_url}{path}"
        return await self._request("GET", url)

    async def _request(
        self,
        method: str,
        url: str,
        *,
        json: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        headers: dict[str, str] = {"Content-Type": "application/json"}
        if self._auth_token:
            headers["Authorization"] = self._auth_token

        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.request(method, url, json=json, headers=headers)
        except httpx.TimeoutException as exc:
            raise MozaiksPayHTTPError(0, f"Request timed out: {exc}") from exc
        except httpx.RequestError as exc:
            raise MozaiksPayHTTPError(0, f"Network error: {exc}") from exc

        if not response.is_success:
            detail = ""
            try:
                detail = response.json().get("detail") or response.text[:200]
            except Exception:
                detail = response.text[:200]
            raise MozaiksPayHTTPError(response.status_code, detail)

        try:
            data: dict[str, Any] = response.json()
        except Exception as exc:
            raise MozaiksPayHTTPError(response.status_code, "Non-JSON response body") from exc

        if data.get("success") is False and "error" in data:
            raise MozaiksPayApplicationError(str(data["error"]))

        return data
