"""Manual provider health-check plugin framework for app connectors.

This module intentionally does not register any real provider checks. Hosts or
external packages may register provider implementations at process startup.
Checks run only when called explicitly by a server-side action.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol

from mozaiksai.core.data.persistence import AppConnectorStore
from mozaiksai.core.secrets import get_connector_vault_backend

HEALTH_STATUSES = {"healthy", "unhealthy", "unknown"}
SECRET_DETAIL_KEYS = {"secret_value", "secret", "api_key", "apikey", "token", "password"}


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _normalize_id(value: Any) -> str:
    return str(value or "").strip().lower().replace(" ", "_")


def _is_secret_detail_key(key: Any) -> bool:
    key_text = str(key or "").strip().lower()
    return key_text in SECRET_DETAIL_KEYS or any(marker in key_text for marker in ("secret", "api_key", "apikey", "token", "password"))


def redact_health_details(value: Any) -> Any:
    """Return a frontend-safe copy of health details."""

    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        for key, item in value.items():
            if _is_secret_detail_key(key):
                continue
            redacted[str(key)] = redact_health_details(item)
        return redacted
    if isinstance(value, list):
        return [redact_health_details(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _configuration_health(record: dict[str, Any], *, checked_by: str) -> dict[str, Any]:
    fields = record.get("required_fields") if isinstance(record.get("required_fields"), list) else []
    public_config = record.get("public_config") if isinstance(record.get("public_config"), dict) else {}
    missing_fields = []
    has_secret = bool(record.get("secret_available")) or int(record.get("key_length") or 0) > 0
    for field in fields:
        if not isinstance(field, dict) or field.get("required") is False:
            continue
        name = str(field.get("name") or "").strip()
        if not name:
            continue
        field_type = _normalize_id(field.get("type"))
        if field_type in {"secret", "password", "api_key", "token"}:
            if not has_secret:
                missing_fields.append(name)
            continue
        value = public_config.get(name)
        if value is None or (isinstance(value, str) and not value.strip()):
            missing_fields.append(name)
    if missing_fields:
        status = "not_configured"
        message = "Connector is missing required configuration."
    elif record.get("health_status") in {"healthy", "unhealthy"}:
        status = str(record.get("health_status"))
        message = record.get("health_message")
    elif fields or has_secret or public_config:
        status = "configured"
        message = "Connector has required configuration; no provider validation has run."
    else:
        status = "unknown"
        message = "Connector health has not been checked."
    return {
        "status": status,
        "message": message,
        "last_checked_at": record.get("last_checked_at") or _now_iso(),
        "checked_by": checked_by,
        "missing_fields": missing_fields,
        "health_details": redact_health_details(record.get("health_details") or {}),
        "error_code": record.get("health_error_code"),
    }


@dataclass(frozen=True)
class ConnectorHealthResult:
    status: str
    message: str | None = None
    checked_at: str | None = None
    safe_details: dict[str, Any] | None = None
    error_code: str | None = None

    def to_safe_dict(self) -> dict[str, Any]:
        status = _normalize_id(self.status)
        if status not in HEALTH_STATUSES:
            status = "unknown"
        return {
            "status": status,
            "message": self.message,
            "checked_at": self.checked_at or _now_iso(),
            "safe_details": redact_health_details(self.safe_details or {}),
            "error_code": self.error_code,
        }


@dataclass(frozen=True)
class ConnectorHealthContext:
    app_id: str
    service_id: str
    integration_id: str
    checked_by: str = "manual"
    safe_context: dict[str, Any] = field(default_factory=dict)


class SecretHandle:
    """Non-serializable secret wrapper for server-side provider checks."""

    def __init__(self, value: str | None, *, available: bool, provider: str | None = None) -> None:
        self._value = value
        self.available = bool(available)
        self.provider = provider

    @property
    def value(self) -> str | None:
        return self._value

    def __repr__(self) -> str:  # pragma: no cover - defensive secret guard
        return "SecretHandle(value=<redacted>)"

    __str__ = __repr__


class ConnectorSecretReader:
    """Server-side secret reader passed to health providers."""

    def __init__(self, *, app_id: str, service: str) -> None:
        self.app_id = str(app_id)
        self.service = _normalize_id(service)

    async def get_secret(self) -> SecretHandle:
        result = await get_connector_vault_backend().get_secret(app_id=self.app_id, service=self.service)
        return SecretHandle(
            result.get("secret_value"),
            available=bool(result.get("success")),
            provider=result.get("provider"),
        )

    def __repr__(self) -> str:  # pragma: no cover - defensive secret guard
        return f"ConnectorSecretReader(app_id={self.app_id!r}, service={self.service!r})"


class ConnectorHealthProvider(Protocol):
    provider_id: str
    supported_integration_ids: Sequence[str]

    async def check(
        self,
        *,
        connector: dict[str, Any],
        secret_reader: ConnectorSecretReader,
        public_config: dict[str, Any],
        context: ConnectorHealthContext,
    ) -> ConnectorHealthResult:
        ...


_PROVIDERS: dict[str, ConnectorHealthProvider] = {}


def register_connector_health_provider(provider: ConnectorHealthProvider) -> None:
    """Register a provider health checker by supported integration id."""

    integration_ids = getattr(provider, "supported_integration_ids", None)
    if not integration_ids:
        raise ValueError("connector health provider must declare supported_integration_ids")
    for integration_id in integration_ids:
        normalized = _normalize_id(integration_id)
        if not normalized:
            continue
        _PROVIDERS[normalized] = provider


def get_connector_health_provider(integration_id: str) -> ConnectorHealthProvider | None:
    return _PROVIDERS.get(_normalize_id(integration_id))


def connector_health_check_supported(integration_id: str) -> bool:
    return get_connector_health_provider(integration_id) is not None


def clear_connector_health_providers_for_tests() -> None:
    _PROVIDERS.clear()


async def run_connector_health_check(
    *,
    app_id: str,
    service: str,
    checked_by: str = "manual",
    safe_context: dict[str, Any] | None = None,
    store: AppConnectorStore | None = None,
) -> dict[str, Any]:
    """Run an explicitly requested server-side provider health check."""

    connector_store = store or AppConnectorStore()
    normalized_service = _normalize_id(service)
    record = await connector_store.get_connector(app_id=str(app_id), service=normalized_service)
    if not isinstance(record, dict):
        return {
            "status": "not_configured",
            "message": "Connector is not configured.",
            "last_checked_at": _now_iso(),
            "checked_by": checked_by,
            "missing_fields": [],
            "health_check_supported": False,
            "frontend_safe": True,
        }

    integration_id = _normalize_id(record.get("integration_id") or record.get("provider") or record.get("service") or normalized_service)
    provider = get_connector_health_provider(integration_id) or get_connector_health_provider(normalized_service)
    if provider is None:
        config_health = _configuration_health(record, checked_by=checked_by)
        return {
            **config_health,
            "health_check_supported": False,
            "frontend_safe": True,
        }

    context = ConnectorHealthContext(
        app_id=str(app_id),
        service_id=normalized_service,
        integration_id=integration_id,
        checked_by=checked_by,
        safe_context=dict(safe_context or {}),
    )
    secret_reader = ConnectorSecretReader(app_id=str(app_id), service=normalized_service)
    public_config = record.get("public_config") if isinstance(record.get("public_config"), dict) else {}

    try:
        provider_result = await provider.check(
            connector=dict(record),
            secret_reader=secret_reader,
            public_config=dict(public_config),
            context=context,
        )
        safe_result = provider_result.to_safe_dict()
    except Exception:
        safe_result = ConnectorHealthResult(
            status="unhealthy",
            message="Provider health check failed.",
            error_code="provider_check_failed",
        ).to_safe_dict()

    updated = await connector_store.update_connector_health(
        app_id=str(app_id),
        service=normalized_service,
        health_status=safe_result["status"],
        health_message=safe_result.get("message"),
        last_checked_at=safe_result.get("checked_at"),
        checked_by=checked_by,
        health_details=safe_result.get("safe_details") or {},
        error_code=safe_result.get("error_code"),
    )
    return {
        "status": safe_result["status"],
        "message": safe_result.get("message"),
        "last_checked_at": safe_result.get("checked_at"),
        "checked_by": checked_by,
        "missing_fields": [],
        "health_details": safe_result.get("safe_details") or {},
        "error_code": safe_result.get("error_code"),
        "health_check_supported": True,
        "frontend_safe": True,
        "connector": updated,
    }


__all__ = [
    "ConnectorHealthContext",
    "ConnectorHealthProvider",
    "ConnectorHealthResult",
    "ConnectorSecretReader",
    "SecretHandle",
    "clear_connector_health_providers_for_tests",
    "connector_health_check_supported",
    "get_connector_health_provider",
    "redact_health_details",
    "register_connector_health_provider",
    "run_connector_health_check",
]
