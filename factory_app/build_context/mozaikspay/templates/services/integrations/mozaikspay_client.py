"""Thin MozaiksPay billing client for generated SaaS apps.

This file is copied to:
    services/integrations/mozaikspay_client.py

It is consumer-side adapter code. It must not import provider-owned modules,
call Stripe directly, store secrets, or create a token usage ledger.
"""

from __future__ import annotations

import os
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

import httpx

_DEFAULT_TIMEOUT = 20.0
_CONNECTOR_SERVICE = "mozaikspay"
_PROVIDER_API_PREFIX = "/api/mozaikspay/v1"


class MozaiksPayError(Exception):
    """Base exception for MozaiksPay client errors."""


class MozaiksPayConfigurationError(MozaiksPayError):
    """Raised when MozaiksPay provider config is unavailable."""


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


@dataclass(frozen=True)
class MozaiksPayConnectorSettings:
    api_base: str | None = None
    client_id: str | None = None
    client_secret: str | None = None
    runtime_base: str | None = None
    source: str = "unconfigured"

    @property
    def has_provider_credentials(self) -> bool:
        return bool(self.api_base and self.client_id and self.client_secret)


ConnectorSettingsLoader = Callable[[str | None], Awaitable[MozaiksPayConnectorSettings | None]]


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _env_settings() -> MozaiksPayConnectorSettings:
    api_base = _clean(os.getenv("MOZAIKSPAY_API_BASE")) or None
    runtime_base = _clean(os.getenv("MOZAIKS_APP_URL")) or None
    return MozaiksPayConnectorSettings(
        api_base=api_base,
        client_id=_clean(os.getenv("MOZAIKSPAY_CLIENT_ID")) or None,
        client_secret=(
            _clean(os.getenv("MOZAIKSPAY_CLIENT_SECRET"))
            or _clean(os.getenv("MOZAIKSPAY_API_KEY"))
            or None
        ),
        runtime_base=runtime_base,
        source="env",
    )


async def _load_connector_settings(app_id: str | None) -> MozaiksPayConnectorSettings | None:
    """Load app-scoped MozaiksPay connector config when Studio stored it.

    The connector record stores only frontend-safe config. The client secret is
    fetched from the configured connector vault. Missing or unavailable vault
    state falls back to environment variables in the caller.
    """

    if not app_id:
        return None
    try:
        from mozaiksai.core.data.persistence.connector_store import AppConnectorStore
        from mozaiksai.core.secrets import get_connector_vault_backend

        record = await AppConnectorStore().get_connector(app_id=str(app_id), service=_CONNECTOR_SERVICE)
        if not isinstance(record, dict):
            return None
        public_config = record.get("public_config") if isinstance(record.get("public_config"), dict) else {}
        secret_result = await get_connector_vault_backend().get_secret(
            app_id=str(app_id),
            service=_CONNECTOR_SERVICE,
        )
        client_secret = _clean(secret_result.get("secret_value")) if isinstance(secret_result, dict) else ""
        api_base = _clean(public_config.get("api_base")) or _clean(public_config.get("base_url")) or None
        client_id = _clean(public_config.get("client_id")) or None
        if not any([api_base, client_id, client_secret]):
            return None
        return MozaiksPayConnectorSettings(
            api_base=api_base,
            client_id=client_id,
            client_secret=client_secret or None,
            runtime_base=_clean(public_config.get("runtime_base")) or _clean(os.getenv("MOZAIKS_APP_URL")) or None,
            source="connector",
        )
    except Exception:
        return None


def _authorization_header(value: str) -> str:
    return value if value.lower().startswith(("bearer ", "basic ")) else f"Bearer {value}"


class MozaiksPayClient:
    """App-side client for MozaiksPay SaaS billing surfaces."""

    def __init__(
        self,
        api_base: str | None = None,
        runtime_base: str | None = None,
        client_id: str | None = None,
        client_secret: str | None = None,
        auth_token: str | None = None,
        app_id: str | None = None,
        timeout_seconds: float = _DEFAULT_TIMEOUT,
        connector_settings_loader: ConnectorSettingsLoader | None = None,
    ) -> None:
        self._api_base = _clean(api_base) or None
        self._runtime_base = _clean(runtime_base) or None
        self._client_id = _clean(client_id) or None
        self._client_secret = _clean(client_secret) or None
        self._auth_token = _clean(auth_token) or None
        self._app_id = _clean(app_id) or None
        self._timeout = timeout_seconds
        self._connector_settings_loader = connector_settings_loader or _load_connector_settings
        self._settings_cache: MozaiksPayConnectorSettings | None = None

    async def get_subscription_status(self) -> dict[str, Any]:
        """Return safe plan display fields from MozaiksPay."""
        return await self.get_subscription_status_for_scope()

    async def get_subscription_status_for_scope(
        self,
        *,
        user_id: str | None = None,
        tenant_id: str | None = None,
        workspace_id: str | None = None,
    ) -> dict[str, Any]:
        """Return safe plan display fields for an app-owned billing scope."""
        settings = await self._settings()
        self._require_provider_credentials(settings)
        return await self._request(
            "GET",
            f"{settings.api_base.rstrip('/')}{_PROVIDER_API_PREFIX}/subscription/status",
            params=_scope_params(user_id=user_id, tenant_id=tenant_id, workspace_id=workspace_id),
            settings=settings,
            provider_auth=True,
        )

    async def get_usage_status(self, *, limit: int = 500) -> dict[str, Any]:
        """Return runtime AI token usage for the current app user."""
        return await self.get_runtime_ai_usage(limit=limit)

    async def get_runtime_ai_usage(self, *, limit: int = 500) -> dict[str, Any]:
        """Return OSS runtime AI usage from this generated app's /api/me/usage."""
        settings = await self._settings()
        runtime_base = (settings.runtime_base or "").rstrip("/")
        if not runtime_base:
            raise MozaiksPayConfigurationError(
                "MOZAIKS_APP_URL is not set. Configure the generated app runtime URL for usage display; "
                "MOZAIKSPAY_API_BASE is only the MozaiksPay provider host."
            )
        bounded_limit = max(1, min(int(limit or 1), 1000))
        return await self._request(
            "GET",
            f"{runtime_base}/api/me/usage?limit={bounded_limit}",
            settings=settings,
            provider_auth=False,
        )

    async def create_billing_portal_session(
        self,
        *,
        return_url: str,
        user_id: str | None = None,
        tenant_id: str | None = None,
        workspace_id: str | None = None,
    ) -> dict[str, Any]:
        """Create a hosted billing portal redirect session."""
        settings = await self._settings()
        self._require_provider_credentials(settings)
        payload = {
            "return_url": return_url,
            **_scope_params(user_id=user_id, tenant_id=tenant_id, workspace_id=workspace_id),
        }
        return await self._request(
            "POST",
            f"{settings.api_base.rstrip('/')}{_PROVIDER_API_PREFIX}/billing-portal/session",
            json=payload,
            settings=settings,
            provider_auth=True,
        )

    async def _settings(self) -> MozaiksPayConnectorSettings:
        if self._settings_cache is not None:
            return self._settings_cache

        connector = await self._connector_settings_loader(self._app_id)
        env = _env_settings()
        settings = MozaiksPayConnectorSettings(
            api_base=self._api_base or (connector.api_base if connector else None) or env.api_base,
            client_id=self._client_id or (connector.client_id if connector else None) or env.client_id,
            client_secret=self._client_secret or (connector.client_secret if connector else None) or env.client_secret,
            runtime_base=self._runtime_base or (connector.runtime_base if connector else None) or env.runtime_base,
            source="explicit" if self._api_base or self._client_id or self._client_secret else (connector.source if connector else env.source),
        )
        if not settings.api_base:
            raise MozaiksPayConfigurationError(
                "MozaiksPay is not configured. Add the mozaikspay connector or set MOZAIKSPAY_API_BASE."
            )
        self._settings_cache = settings
        return settings

    @staticmethod
    def _require_provider_credentials(settings: MozaiksPayConnectorSettings) -> None:
        if not settings.has_provider_credentials:
            raise MozaiksPayConfigurationError(
                "MozaiksPay provider credentials are required. Configure the mozaikspay "
                "connector with api_base, client_id, and client_secret."
            )

    async def _request(
        self,
        method: str,
        url: str,
        *,
        json: dict[str, Any] | None = None,
        params: dict[str, str] | None = None,
        settings: MozaiksPayConnectorSettings,
        provider_auth: bool,
    ) -> dict[str, Any]:
        headers: dict[str, str] = {"Content-Type": "application/json"}
        if provider_auth and settings.client_id and settings.client_secret:
            headers["X-MozaiksPay-Client-Id"] = settings.client_id
            headers["Authorization"] = _authorization_header(settings.client_secret)
        elif self._auth_token:
            headers["Authorization"] = _authorization_header(self._auth_token)

        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.request(method, url, json=json, params=params, headers=headers)
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


def _scope_params(
    *,
    user_id: str | None = None,
    tenant_id: str | None = None,
    workspace_id: str | None = None,
) -> dict[str, str]:
    return {
        key: value
        for key, value in {
            "user_id": _clean(user_id),
            "tenant_id": _clean(tenant_id),
            "workspace_id": _clean(workspace_id),
        }.items()
        if value
    }
