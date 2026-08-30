from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

import httpx

_CONNECTOR_SERVICE = "mozaiks_cloud"
_DEFAULT_TIMEOUT = 20.0
_DEFAULT_MAX_RETRIES = 2
_PROVIDER_API_PREFIX = "/api/mozaiks-cloud/v1"
_API_VERSION = "mozaiks.cloud.v1"
_RETRYABLE_STATUS_CODES = {408, 429, 500, 502, 503, 504}
_ERROR_KIND_BY_STATUS = {
    400: "validation_error",
    401: "auth_error",
    403: "auth_error",
    404: "not_found",
    409: "conflict",
    422: "validation_error",
    429: "rate_limited",
}


class MozaiksCloudError(Exception):
    """Base error raised by Mozaiks Cloud integration clients."""


class MozaiksCloudConfigurationError(MozaiksCloudError):
    """Raised when the Mozaiks Cloud connector is not configured."""


class MozaiksCloudHTTPError(MozaiksCloudError):
    def __init__(
        self,
        status_code: int,
        *,
        code: str = "unknown",
        message: str = "",
        kind: str = "unknown",
        retryable: bool = False,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.status_code = status_code
        self.code = code
        self.kind = kind
        self.retryable = retryable
        self.details = details or {}
        super().__init__(f"Mozaiks Cloud HTTP {status_code} [{code}]: {message}")


@dataclass(frozen=True)
class MozaiksCloudConnectorSettings:
    api_base: str | None = None
    api_key: str | None = None
    app_id: str | None = None
    source: str = "unconfigured"


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _env_settings() -> MozaiksCloudConnectorSettings:
    return MozaiksCloudConnectorSettings(
        api_base=_clean(os.getenv("MOZAIKS_CLOUD_API_BASE")) or None,
        api_key=_clean(os.getenv("MOZAIKS_CLOUD_API_KEY")) or None,
        app_id=_clean(os.getenv("MOZAIKS_CLOUD_APP_ID")) or None,
        source="env",
    )


async def _load_connector_settings(app_id: str | None = None) -> MozaiksCloudConnectorSettings | None:
    if not app_id:
        return None
    try:
        from mozaiksai.core.data.persistence.connector_store import ConnectorStore
        from mozaiksai.core.secrets import get_connector_vault_backend

        record = await ConnectorStore().get(
            scope=ConnectorStore.SCOPE_APP,
            scope_id=str(app_id),
            service=_CONNECTOR_SERVICE,
        )
        if not isinstance(record, dict):
            return None
        public_config = record.get("public_config") if isinstance(record.get("public_config"), dict) else {}
        secret_result = await get_connector_vault_backend().get_secret(
            scope_id=str(app_id),
            service=_CONNECTOR_SERVICE,
        )
        credential = _clean(secret_result.get("secret_value")) if isinstance(secret_result, dict) else ""
        api_base = _clean(public_config.get("api_base")) or None
        cloud_app_id = _clean(public_config.get("app_id")) or None
        if not api_base and not credential:
            return None
        return MozaiksCloudConnectorSettings(
            api_base=api_base,
            api_key=credential or None,
            app_id=cloud_app_id,
            source="connector",
        )
    except Exception:
        return None


def _authorization_header(value: str) -> str:
    return value if value.lower().startswith("bearer ") else f"Bearer {value}"


def _error_from_response(status_code: int, body: dict[str, Any]) -> MozaiksCloudHTTPError:
    raw_error = body.get("error") if isinstance(body, dict) else None
    envelope = raw_error if isinstance(raw_error, dict) else {}
    code = _clean(envelope.get("code") or body.get("error_code") or body.get("code") or "unknown")
    message = _clean(envelope.get("message") or body.get("detail") or body.get("error") or "")
    kind = _clean(envelope.get("kind") or body.get("error_kind") or _ERROR_KIND_BY_STATUS.get(status_code))
    if not kind and status_code >= 500:
        kind = "provider_error"
    if not kind:
        kind = "unknown"
    retryable = bool(envelope.get("retryable")) or status_code in _RETRYABLE_STATUS_CODES
    details = envelope.get("details") if isinstance(envelope.get("details"), dict) else None
    return MozaiksCloudHTTPError(
        status_code,
        code=code,
        message=message,
        kind=kind,
        retryable=retryable,
        details=details,
    )


class MozaiksCloudTransport:
    def __init__(
        self,
        api_base: str | None = None,
        api_key: str | None = None,
        app_id: str | None = None,
        timeout_seconds: float = _DEFAULT_TIMEOUT,
        max_retries: int = _DEFAULT_MAX_RETRIES,
        connector_settings_loader: Any = None,
    ) -> None:
        self._api_base = _clean(api_base) or None
        self._api_key = _clean(api_key) or None
        self._app_id = _clean(app_id) or None
        self._timeout = timeout_seconds
        self._max_retries = max(0, min(max_retries, 5))
        self._connector_settings_loader = connector_settings_loader or _load_connector_settings
        self._settings_cache: MozaiksCloudConnectorSettings | None = None

    async def settings(self) -> MozaiksCloudConnectorSettings:
        if self._settings_cache is not None:
            return self._settings_cache
        connector = await self._connector_settings_loader(self._app_id)
        env = _env_settings()
        resolved = MozaiksCloudConnectorSettings(
            api_base=self._api_base or (connector.api_base if connector else None) or env.api_base,
            api_key=self._api_key or (connector.api_key if connector else None) or env.api_key,
            app_id=self._app_id or (connector.app_id if connector else None) or env.app_id,
            source="explicit" if (self._api_base or self._api_key) else (connector.source if connector else env.source),
        )
        if not resolved.api_base:
            raise MozaiksCloudConfigurationError(
                "Mozaiks Cloud API base is required. Configure the mozaiks_cloud connector "
                "or set MOZAIKS_CLOUD_API_BASE."
            )
        if not resolved.api_key:
            raise MozaiksCloudConfigurationError(
                "Mozaiks Cloud API key is required. Configure the mozaiks_cloud connector "
                "or set MOZAIKS_CLOUD_API_KEY."
            )
        self._settings_cache = resolved
        return resolved

    async def request(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
        params: dict[str, str] | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        cfg = await self.settings()
        headers: dict[str, str] = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "X-MozaiksCloud-Api-Version": _API_VERSION,
            "Authorization": _authorization_header(cfg.api_key or ""),
        }
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        url = f"{(cfg.api_base or '').rstrip('/')}{_PROVIDER_API_PREFIX}{path}"
        attempts = self._max_retries + 1
        last_error: MozaiksCloudHTTPError | None = None
        for attempt in range(attempts):
            try:
                async with httpx.AsyncClient(timeout=self._timeout) as client:
                    response = await client.request(method, url, json=json, params=params, headers=headers)
            except httpx.TimeoutException as exc:
                last_error = MozaiksCloudHTTPError(0, code="timeout", message=str(exc), kind="timeout", retryable=True)
                if attempt + 1 >= attempts:
                    raise last_error from exc
                continue
            except httpx.RequestError as exc:
                last_error = MozaiksCloudHTTPError(
                    0,
                    code="network_error",
                    message=str(exc),
                    kind="provider_error",
                    retryable=True,
                )
                if attempt + 1 >= attempts:
                    raise last_error from exc
                continue

            body: dict[str, Any] = {}
            try:
                parsed = response.json()
                body = parsed if isinstance(parsed, dict) else {}
            except Exception:
                body = {}

            if response.is_success:
                if body.get("success") is False:
                    raise _error_from_response(response.status_code, body)
                return body

            last_error = _error_from_response(response.status_code, body)
            if not last_error.retryable or attempt + 1 >= attempts:
                raise last_error
        if last_error is not None:
            raise last_error
        raise MozaiksCloudHTTPError(0, code="request_failed", message="Request failed", kind="unknown")
