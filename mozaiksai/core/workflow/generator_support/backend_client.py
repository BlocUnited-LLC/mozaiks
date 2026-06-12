"""Small backend HTTP client used by generator export tools."""

from __future__ import annotations

import os
from typing import Any

import httpx


class BackendClient:
    def __init__(
        self,
        *,
        base_url: str | None = None,
        api_key: str | None = None,
        timeout: float = 60.0,
    ) -> None:
        self.base_url = (
            base_url
            or os.getenv("MOZAIKS_PLATFORM_BASE_URL")
            or os.getenv("MOZAIKS_BACKEND_URL")
            or "http://localhost:8000"
        ).rstrip("/")
        self.api_key = api_key if api_key is not None else os.getenv("INTERNAL_API_KEY", "").strip()
        self._timeout = httpx.Timeout(timeout, connect=10.0)

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["X-Internal-API-Key"] = self.api_key
        return headers

    async def get(
        self,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        error_msg: str = "Backend GET failed",
    ) -> dict[str, Any]:
        return await self._request("GET", path, params=params, error_msg=error_msg)

    async def post(
        self,
        path: str,
        *,
        json: dict[str, Any] | None = None,
        error_msg: str = "Backend POST failed",
    ) -> dict[str, Any]:
        return await self._request("POST", path, json=json, error_msg=error_msg)

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
        error_msg: str,
    ) -> dict[str, Any]:
        normalized_path = path if path.startswith("/") else f"/{path}"
        url = f"{self.base_url}{normalized_path}"
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            response = await client.request(
                method,
                url,
                headers=self._headers(),
                params=params,
                json=json,
            )
        if 200 <= response.status_code < 300:
            try:
                payload = response.json()
            except Exception:
                return {"raw": response.text}
            return payload if isinstance(payload, dict) else {"data": payload}
        raise RuntimeError(f"{error_msg}: HTTP {response.status_code}: {response.text[:500]}")


__all__ = ["BackendClient"]
