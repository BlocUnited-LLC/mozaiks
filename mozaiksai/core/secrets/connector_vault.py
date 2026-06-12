"""Connector secret backend for platform-managed app integrations.

This layer owns durable secret storage for connector credentials. Metadata about
connectors lives in MongoDB via AppConnectorStore; raw secrets belong here.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import re
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from logs.logging_config import get_core_logger

logger = get_core_logger("connector_vault")

_VALID_SECRET_CHARS = re.compile(r"[^a-z0-9-]+")
_backend_singleton: ConnectorVaultBackend | None = None
_backend_signature: tuple[str, str, str] | None = None


class ConnectorVaultBackend(Protocol):
    async def describe(self) -> dict[str, Any]:
        ...

    async def store_secret(
        self,
        *,
        app_id: str,
        service: str,
        secret_value: str,
        display_name: str | None = None,
        ttl_days: int = 30,
    ) -> dict[str, Any]:
        ...

    async def get_secret(self, *, app_id: str, service: str) -> dict[str, Any]:
        ...

    async def delete_secret(self, *, app_id: str, service: str) -> dict[str, Any]:
        ...


def _backend_mode() -> str:
    return str(os.getenv("MOZAIKS_CONNECTOR_SECRET_BACKEND", "auto")).strip().lower() or "auto"


def _vault_name() -> str:
    return str(os.getenv("AZURE_KEY_VAULT_NAME", "")).strip()


def _secret_prefix() -> str:
    return str(os.getenv("MOZAIKS_CONNECTOR_SECRET_PREFIX", "mozaiks-connector")).strip() or "mozaiks-connector"


def _build_signature() -> tuple[str, str, str]:
    return (_backend_mode(), _vault_name(), _secret_prefix())


def _slug(value: str, *, default: str) -> str:
    candidate = _VALID_SECRET_CHARS.sub("-", str(value or "").strip().lower())
    candidate = re.sub(r"-{2,}", "-", candidate).strip("-")
    return candidate or default


def _secret_name(app_id: str, service: str, *, prefix: str | None = None) -> str:
    base_prefix = _slug(prefix or _secret_prefix(), default="mozaiks-connector")
    service_slug = _slug(service, default="service")
    app_slug = _slug(app_id, default="app")
    digest = hashlib.sha1(str(app_id).encode("utf-8")).hexdigest()[:10]
    name = f"{base_prefix}-{service_slug}-{app_slug[:40]}-{digest}"
    return name[:127]


class NoopConnectorVaultBackend:
    async def describe(self) -> dict[str, Any]:
        return {
            "provider": "disabled",
            "configured": False,
            "mode": _backend_mode(),
            "vault_name": None,
            "secret_prefix": _secret_prefix(),
        }

    async def store_secret(
        self,
        *,
        app_id: str,
        service: str,
        secret_value: str,
        display_name: str | None = None,
        ttl_days: int = 30,
    ) -> dict[str, Any]:
        return {
            "success": False,
            "provider": "disabled",
            "secret_name": None,
            "expires_at": None,
            "error": "Connector secret backend is not configured.",
        }

    async def get_secret(self, *, app_id: str, service: str) -> dict[str, Any]:
        return {
            "success": False,
            "provider": "disabled",
            "secret_name": None,
            "secret_value": None,
            "error": "Connector secret backend is not configured.",
        }

    async def delete_secret(self, *, app_id: str, service: str) -> dict[str, Any]:
        return {
            "success": False,
            "provider": "disabled",
            "secret_name": None,
            "error": "Connector secret backend is not configured.",
        }


class AzureKeyVaultConnectorVaultBackend:
    def __init__(self) -> None:
        self._client = None
        self._client_error: str | None = None

    def _get_vault_url(self) -> str | None:
        name = _vault_name()
        if not name:
            return None
        return f"https://{name}.vault.azure.net/"

    def _get_client(self):
        if self._client is not None or self._client_error is not None:
            return self._client
        vault_url = self._get_vault_url()
        if not vault_url:
            self._client_error = "AZURE_KEY_VAULT_NAME is not set."
            return None
        try:
            from azure.identity import DefaultAzureCredential  # type: ignore
            from azure.keyvault.secrets import SecretClient  # type: ignore
        except Exception as exc:  # pragma: no cover - import surface depends on extras
            self._client_error = f"Azure Key Vault dependencies are not installed: {exc}"
            return None
        try:
            credential = DefaultAzureCredential()
            self._client = SecretClient(vault_url=vault_url, credential=credential)
            return self._client
        except Exception as exc:  # pragma: no cover - environment specific
            self._client_error = str(exc)
            return None

    async def describe(self) -> dict[str, Any]:
        client = self._get_client()
        return {
            "provider": "azure_key_vault",
            "configured": client is not None,
            "mode": _backend_mode(),
            "vault_name": _vault_name() or None,
            "secret_prefix": _secret_prefix(),
            "error": self._client_error,
        }

    async def store_secret(
        self,
        *,
        app_id: str,
        service: str,
        secret_value: str,
        display_name: str | None = None,
        ttl_days: int = 30,
    ) -> dict[str, Any]:
        client = self._get_client()
        secret_name = _secret_name(app_id, service)
        if client is None:
            return {
                "success": False,
                "provider": "azure_key_vault",
                "secret_name": secret_name,
                "expires_at": None,
                "error": self._client_error or "Azure Key Vault connector backend is unavailable.",
            }

        expires_at = datetime.now(UTC) + timedelta(days=max(int(ttl_days), 1))
        tags = {
            "managed_by": "mozaiks",
            "app_id": str(app_id),
            "service": _slug(service, default="service"),
        }
        if display_name:
            tags["display_name"] = str(display_name)[:256]

        try:
            secret = await asyncio.to_thread(
                client.set_secret,
                secret_name,
                secret_value,
                tags=tags,
                content_type="mozaiks.connector.secret",
                expires_on=expires_at,
            )
            properties = getattr(secret, "properties", None)
            version = getattr(properties, "version", None)
            return {
                "success": True,
                "provider": "azure_key_vault",
                "secret_name": secret_name,
                "version": version,
                "expires_at": expires_at.isoformat(),
                "secret_available": True,
            }
        except Exception as exc:  # pragma: no cover - depends on Azure service
            logger.warning("Failed to store connector secret %s: %s", secret_name, exc)
            return {
                "success": False,
                "provider": "azure_key_vault",
                "secret_name": secret_name,
                "expires_at": None,
                "error": str(exc),
            }

    async def get_secret(self, *, app_id: str, service: str) -> dict[str, Any]:
        client = self._get_client()
        secret_name = _secret_name(app_id, service)
        if client is None:
            return {
                "success": False,
                "provider": "azure_key_vault",
                "secret_name": secret_name,
                "secret_value": None,
                "error": self._client_error or "Azure Key Vault connector backend is unavailable.",
            }
        try:
            secret = await asyncio.to_thread(client.get_secret, secret_name)
            value = getattr(secret, "value", None)
            props = getattr(secret, "properties", None)
            expires_on = getattr(props, "expires_on", None)
            return {
                "success": bool(value),
                "provider": "azure_key_vault",
                "secret_name": secret_name,
                "secret_value": value,
                "expires_at": expires_on.isoformat() if expires_on else None,
                "error": None if value else "Secret exists but has no value.",
            }
        except Exception as exc:  # pragma: no cover - depends on Azure service
            logger.warning("Failed to fetch connector secret %s: %s", secret_name, exc)
            return {
                "success": False,
                "provider": "azure_key_vault",
                "secret_name": secret_name,
                "secret_value": None,
                "error": str(exc),
            }

    async def delete_secret(self, *, app_id: str, service: str) -> dict[str, Any]:
        client = self._get_client()
        secret_name = _secret_name(app_id, service)
        if client is None:
            return {
                "success": False,
                "provider": "azure_key_vault",
                "secret_name": secret_name,
                "error": self._client_error or "Azure Key Vault connector backend is unavailable.",
            }
        try:
            await asyncio.to_thread(client.begin_delete_secret, secret_name)
            return {
                "success": True,
                "provider": "azure_key_vault",
                "secret_name": secret_name,
                "error": None,
            }
        except Exception as exc:  # pragma: no cover - depends on Azure service
            logger.warning("Failed to delete connector secret %s: %s", secret_name, exc)
            return {
                "success": False,
                "provider": "azure_key_vault",
                "secret_name": secret_name,
                "error": str(exc),
            }


def get_connector_vault_backend() -> ConnectorVaultBackend:
    global _backend_singleton, _backend_signature
    signature = _build_signature()
    if _backend_singleton is not None and _backend_signature == signature:
        return _backend_singleton

    mode, vault_name, _prefix = signature
    if mode in {"disabled", "none", "off", "false"}:
        backend: ConnectorVaultBackend = NoopConnectorVaultBackend()
    elif mode in {"azure", "azure_key_vault", "key_vault"}:
        backend = AzureKeyVaultConnectorVaultBackend()
    elif mode == "auto":
        backend = AzureKeyVaultConnectorVaultBackend() if vault_name else NoopConnectorVaultBackend()
    else:
        logger.warning("Unknown connector secret backend mode '%s'; falling back to disabled.", mode)
        backend = NoopConnectorVaultBackend()

    _backend_singleton = backend
    _backend_signature = signature
    return backend


async def describe_connector_vault_backend() -> dict[str, Any]:
    backend = get_connector_vault_backend()
    return await backend.describe()


def reset_connector_vault_backend() -> None:
    global _backend_singleton, _backend_signature
    _backend_singleton = None
    _backend_signature = None


__all__ = [
    "AzureKeyVaultConnectorVaultBackend",
    "ConnectorVaultBackend",
    "NoopConnectorVaultBackend",
    "describe_connector_vault_backend",
    "get_connector_vault_backend",
    "reset_connector_vault_backend",
]
