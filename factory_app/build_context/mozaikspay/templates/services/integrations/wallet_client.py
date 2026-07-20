"""Thin MozaiksPay wallet client for generated apps.

This file is copied to:
    services/integrations/wallet_client.py

Consumer-side adapter only. Must not import provider-owned modules, call the
payment provider directly, store secrets, or contain entitlement logic.
Entitlement gates are enforced at the module action dispatcher level.
"""

from __future__ import annotations

import os
from typing import Any, cast

import httpx

_DEFAULT_TIMEOUT = 20.0
_WALLET_API_PREFIX = "/api/mozaikspay/v1/wallet"


class WalletClientError(Exception):
    """Base exception for wallet client errors."""


class WalletClientConfigurationError(WalletClientError):
    """Raised when the wallet client cannot be configured."""


def _clean(value: str | None) -> str:
    return (value or "").strip()


class WalletClient:
    """HTTP client for the MozaiksPay wallet runtime API.

    Reads MOZAIKS_APP_URL (or explicit runtime_base) and INTERNAL_API_KEY
    to call the hosted wallet endpoints. Self-hosted apps set AUTH_ENABLED=false
    or pass a bearer token to authenticate the internal calls.
    """

    def __init__(
        self,
        *,
        runtime_base: str | None = None,
        auth_token: str | None = None,
        timeout: float = _DEFAULT_TIMEOUT,
    ) -> None:
        self._runtime_base = _clean(runtime_base) or _clean(os.getenv("MOZAIKS_APP_URL")) or None
        self._auth_token = _clean(auth_token) or _clean(os.getenv("INTERNAL_API_KEY")) or None
        self._timeout = timeout

    def _base(self) -> str:
        if not self._runtime_base:
            raise WalletClientConfigurationError(
                "WalletClient requires MOZAIKS_APP_URL or explicit runtime_base."
            )
        return self._runtime_base.rstrip("/")

    def _headers(self) -> dict[str, str]:
        headers: dict[str, str] = {"Content-Type": "application/json"}
        if self._auth_token:
            headers["Authorization"] = f"Bearer {self._auth_token}"
        return headers

    async def get_balance(
        self,
        *,
        app_id: str,
        user_id: str | None = None,
        tenant_id: str | None = None,
        workspace_id: str | None = None,
    ) -> dict[str, Any]:
        """Return the token wallet balance for the given scope."""
        params: dict[str, str] = {"app_id": app_id}
        if user_id:
            params["user_id"] = user_id
        if tenant_id:
            params["tenant_id"] = tenant_id
        if workspace_id:
            params["workspace_id"] = workspace_id
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            res = await client.get(
                f"{self._base()}{_WALLET_API_PREFIX}/balance",
                headers=self._headers(),
                params=params,
            )
            res.raise_for_status()
            return cast(dict[str, Any], res.json())

    async def debit(
        self,
        *,
        app_id: str,
        amount: int,
        reason: str,
        idempotency_key: str,
        user_id: str | None = None,
        tenant_id: str | None = None,
        workspace_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Debit tokens from the wallet for the given scope."""
        body: dict[str, Any] = {
            "app_id": app_id,
            "amount": amount,
            "reason": reason,
            "idempotency_key": idempotency_key,
        }
        if user_id:
            body["user_id"] = user_id
        if tenant_id:
            body["tenant_id"] = tenant_id
        if workspace_id:
            body["workspace_id"] = workspace_id
        if metadata:
            body["metadata"] = metadata
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            res = await client.post(
                f"{self._base()}{_WALLET_API_PREFIX}/debit",
                headers=self._headers(),
                json=body,
            )
            res.raise_for_status()
            return cast(dict[str, Any], res.json())
