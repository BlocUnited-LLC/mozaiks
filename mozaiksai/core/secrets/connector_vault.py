"""Connector secret backend for platform-managed app integrations.

This layer owns durable secret storage for connector credentials. Metadata about
connectors lives in MongoDB via ConnectorStore; raw secrets belong here.
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
        scope_id: str,
        service: str,
        secret_value: str,
        display_name: str | None = None,
        ttl_days: int = 30,
    ) -> dict[str, Any]:
        ...

    async def get_secret(self, *, scope_id: str, service: str) -> dict[str, Any]:
        ...

    async def delete_secret(self, *, scope_id: str, service: str) -> dict[str, Any]:
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


def _secret_name(scope_id: str, service: str, *, prefix: str | None = None) -> str:
    base_prefix = _slug(prefix or _secret_prefix(), default="mozaiks-connector")
    service_slug = _slug(service, default="service")
    scope_slug = _slug(scope_id, default="scope")
    digest = hashlib.sha1(str(scope_id).encode("utf-8")).hexdigest()[:10]
    name = f"{base_prefix}-{service_slug}-{scope_slug[:40]}-{digest}"
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
        scope_id: str,
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

    async def get_secret(self, *, scope_id: str, service: str) -> dict[str, Any]:
        return {
            "success": False,
            "provider": "disabled",
            "secret_name": None,
            "secret_value": None,
            "error": "Connector secret backend is not configured.",
        }

    async def delete_secret(self, *, scope_id: str, service: str) -> dict[str, Any]:
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
            logger.error("Azure Key Vault client initialisation failed: %s", exc, exc_info=True)
            self._client_error = "Azure Key Vault client could not be initialised."
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
        scope_id: str,
        service: str,
        secret_value: str,
        display_name: str | None = None,
        ttl_days: int = 30,
    ) -> dict[str, Any]:
        client = self._get_client()
        secret_name = _secret_name(scope_id, service)
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
            "scope_id": str(scope_id),
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
            logger.error("Failed to store connector secret %s: %s", secret_name, exc, exc_info=True)
            return {
                "success": False,
                "provider": "azure_key_vault",
                "secret_name": secret_name,
                "expires_at": None,
                "error": "Secret could not be stored.",
            }

    async def get_secret(self, *, scope_id: str, service: str) -> dict[str, Any]:
        client = self._get_client()
        secret_name = _secret_name(scope_id, service)
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
            logger.error("Failed to fetch connector secret %s: %s", secret_name, exc, exc_info=True)
            return {
                "success": False,
                "provider": "azure_key_vault",
                "secret_name": secret_name,
                "secret_value": None,
                "error": "Secret could not be retrieved.",
            }

    async def delete_secret(self, *, scope_id: str, service: str) -> dict[str, Any]:
        client = self._get_client()
        secret_name = _secret_name(scope_id, service)
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
            logger.error("Failed to delete connector secret %s: %s", secret_name, exc, exc_info=True)
            return {
                "success": False,
                "provider": "azure_key_vault",
                "secret_name": secret_name,
                "error": "Secret could not be deleted.",
            }


def _derive_fernet_key() -> bytes:
    """Derive a 32-byte Fernet key from MOZAIKS_CONNECTOR_SECRET_KEY or SECRET_KEY.

    Priority:
    1. MOZAIKS_CONNECTOR_SECRET_KEY — explicit 32-byte hex or URL-safe base64 key
    2. SECRET_KEY — application secret, HKDF-derived to 32 bytes
    3. Fallback — deterministic dev-only key with a loud warning
    """
    import base64
    import hmac as _hmac

    raw = os.getenv("MOZAIKS_CONNECTOR_SECRET_KEY", "").strip()
    if raw:
        # Accept URL-safe base64 directly (Fernet key format) or hex
        try:
            decoded = base64.urlsafe_b64decode(raw + "==")
            if len(decoded) == 32:
                return base64.urlsafe_b64encode(decoded)
        except Exception:
            pass
        try:
            decoded = bytes.fromhex(raw)
            if len(decoded) == 32:
                return base64.urlsafe_b64encode(decoded)
        except Exception:
            pass

    app_secret = os.getenv("SECRET_KEY", "").strip()
    if app_secret:
        # HKDF-lite: HMAC-SHA256(key=app_secret, msg=b"mozaiks-connector-vault")
        digest = _hmac.new(app_secret.encode(), b"mozaiks-connector-vault", "sha256").digest()
        return base64.urlsafe_b64encode(digest)

    logger.warning(
        "MOZAIKS_CONNECTOR_SECRET_KEY and SECRET_KEY are not set. "
        "Using a deterministic dev-only key — DO NOT use in production."
    )
    digest = _hmac.new(b"mozaiks-dev-only", b"mozaiks-connector-vault", "sha256").digest()
    return base64.urlsafe_b64encode(digest)


class MongoConnectorVaultBackend:
    """Connector secret backend that stores Fernet-encrypted secrets in MongoDB.

    Used automatically when MOZAIKS_CONNECTOR_SECRET_BACKEND is 'auto' or 'mongo'
    and no external vault is configured. Suitable for local dev and OSS self-hosters.

    Encryption key priority:
    1. MOZAIKS_CONNECTOR_SECRET_KEY (explicit 32-byte hex or URL-safe base64)
    2. SECRET_KEY (derived via HMAC-SHA256)
    3. Dev-only deterministic fallback (warns loudly)
    """

    def __init__(self) -> None:
        self._fernet: Any | None = None
        self._fernet_error: str | None = None

    def _get_fernet(self) -> Any | None:
        if self._fernet is not None or self._fernet_error is not None:
            return self._fernet
        try:
            from cryptography.fernet import Fernet  # type: ignore[import]
            key = _derive_fernet_key()
            self._fernet = Fernet(key)
        except Exception as exc:
            self._fernet_error = f"Fernet init failed: {exc}"
            logger.error("MongoConnectorVaultBackend: %s", self._fernet_error)
        return self._fernet

    async def _collection(self) -> Any:
        from mozaiksai.core.data.persistence import AG2PersistenceManager
        from mozaiksai.core.data.persistence.namespaces import (
            SYSTEM_DATABASE,
            PlatformCollections,
        )
        pm = AG2PersistenceManager()
        await pm.persistence._ensure_client()  # noqa: SLF001
        client = pm.persistence.client
        if client is None:
            raise RuntimeError("Mongo client not initialized")
        return client[SYSTEM_DATABASE][PlatformCollections.CONNECTOR_SECRETS]

    def _encrypt(self, value: str) -> str | None:
        f = self._get_fernet()
        if f is None:
            return None
        return f.encrypt(value.encode()).decode()

    def _decrypt(self, token: str) -> str | None:
        f = self._get_fernet()
        if f is None:
            return None
        try:
            return f.decrypt(token.encode()).decode()
        except Exception:
            return None

    async def describe(self) -> dict[str, Any]:
        fernet = self._get_fernet()
        return {
            "provider": "mongo",
            "configured": fernet is not None,
            "mode": _backend_mode(),
            "vault_name": None,
            "secret_prefix": _secret_prefix(),
            "error": self._fernet_error,
        }

    async def store_secret(
        self,
        *,
        scope_id: str,
        service: str,
        secret_value: str,
        display_name: str | None = None,
        ttl_days: int = 30,
    ) -> dict[str, Any]:
        fernet = self._get_fernet()
        if fernet is None:
            return {
                "success": False,
                "provider": "mongo",
                "secret_name": _secret_name(scope_id, service),
                "expires_at": None,
                "error": self._fernet_error or "Encryption backend unavailable.",
            }
        encrypted = self._encrypt(secret_value)
        if encrypted is None:
            return {
                "success": False,
                "provider": "mongo",
                "secret_name": _secret_name(scope_id, service),
                "expires_at": None,
                "error": "Encryption failed.",
            }
        secret_name = _secret_name(scope_id, service)
        now = datetime.now(UTC)
        expires_at = now + timedelta(days=max(int(ttl_days), 1))
        try:
            coll = await self._collection()
            await coll.update_one(
                {"scope_id": str(scope_id), "service": _slug(service, default="service")},
                {
                    "$set": {
                        "scope_id": str(scope_id),
                        "service": _slug(service, default="service"),
                        "secret_name": secret_name,
                        "encrypted_value": encrypted,
                        "display_name": display_name,
                        "stored_at": now.isoformat(),
                        "expires_at": expires_at.isoformat(),
                    }
                },
                upsert=True,
            )
            return {
                "success": True,
                "provider": "mongo",
                "secret_name": secret_name,
                "expires_at": expires_at.isoformat(),
                "secret_available": True,
            }
        except Exception as exc:
            logger.error("MongoConnectorVaultBackend.store_secret failed: %s", exc, exc_info=True)
            return {
                "success": False,
                "provider": "mongo",
                "secret_name": _secret_name(scope_id, service),
                "expires_at": None,
                "error": "Secret could not be stored.",
            }

    async def get_secret(self, *, scope_id: str, service: str) -> dict[str, Any]:
        fernet = self._get_fernet()
        secret_name = _secret_name(scope_id, service)
        if fernet is None:
            return {
                "success": False,
                "provider": "mongo",
                "secret_name": secret_name,
                "secret_value": None,
                "error": self._fernet_error or "Encryption backend unavailable.",
            }
        try:
            coll = await self._collection()
            doc = await coll.find_one(
                {"scope_id": str(scope_id), "service": _slug(service, default="service")}
            )
        except Exception as exc:
            logger.error("MongoConnectorVaultBackend.get_secret failed: %s", exc, exc_info=True)
            return {
                "success": False,
                "provider": "mongo",
                "secret_name": secret_name,
                "secret_value": None,
                "error": "Secret could not be retrieved.",
            }
        if not doc:
            return {
                "success": False,
                "provider": "mongo",
                "secret_name": secret_name,
                "secret_value": None,
                "error": "Secret not found.",
            }
        decrypted = self._decrypt(doc.get("encrypted_value") or "")
        return {
            "success": bool(decrypted),
            "provider": "mongo",
            "secret_name": secret_name,
            "secret_value": decrypted,
            "expires_at": doc.get("expires_at"),
            "error": None if decrypted else "Secret could not be decrypted.",
        }

    async def delete_secret(self, *, scope_id: str, service: str) -> dict[str, Any]:
        secret_name = _secret_name(scope_id, service)
        try:
            coll = await self._collection()
            result = await coll.delete_one(
                {"scope_id": str(scope_id), "service": _slug(service, default="service")}
            )
            return {
                "success": result.deleted_count > 0,
                "provider": "mongo",
                "secret_name": secret_name,
                "error": None if result.deleted_count > 0 else "Secret not found.",
            }
        except Exception as exc:
            logger.error("MongoConnectorVaultBackend.delete_secret failed: %s", exc, exc_info=True)
            return {
                "success": False,
                "provider": "mongo",
                "secret_name": secret_name,
                "error": "Secret could not be deleted.",
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
    elif mode in {"mongo", "mongodb"}:
        backend = MongoConnectorVaultBackend()
    elif mode == "auto":
        if vault_name:
            backend = AzureKeyVaultConnectorVaultBackend()
        else:
            backend = MongoConnectorVaultBackend()
    else:
        logger.warning("Unknown connector secret backend mode '%s'; falling back to mongo.", mode)
        backend = MongoConnectorVaultBackend()

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
    "MongoConnectorVaultBackend",
    "NoopConnectorVaultBackend",
    "describe_connector_vault_backend",
    "get_connector_vault_backend",
    "reset_connector_vault_backend",
]
