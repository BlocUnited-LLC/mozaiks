"""Tests for MongoConnectorVaultBackend — encrypted connector secret storage in MongoDB."""

from __future__ import annotations

import asyncio
import os
from typing import Any
from unittest.mock import MagicMock, patch


def _make_backend(env: dict[str, str] | None = None):
    """Create a MongoConnectorVaultBackend with controlled env and a mock collection."""
    from mozaiksai.core.secrets.connector_vault import (
        MongoConnectorVaultBackend,
        reset_connector_vault_backend,
    )

    reset_connector_vault_backend()

    env = env or {"MOZAIKS_CONNECTOR_SECRET_KEY": "", "SECRET_KEY": "test-app-secret-key"}
    backend = MongoConnectorVaultBackend()

    # Patch the env reads used inside _derive_fernet_key
    with patch.dict(os.environ, env, clear=False):
        # Force Fernet init with patched env
        backend._fernet = None
        backend._fernet_error = None
        fernet = backend._get_fernet()
        assert fernet is not None, "Fernet init should succeed with SECRET_KEY set"

    return backend


class _FakeCollection:
    """In-memory mock for a MongoDB collection."""

    def __init__(self) -> None:
        self._docs: dict[tuple[str, str], dict[str, Any]] = {}

    async def update_one(self, filter_: dict, update: dict, upsert: bool = False) -> None:
        key = (filter_["scope_id"], filter_["service"])
        doc = self._docs.get(key, {})
        doc.update(update.get("$set", {}))
        self._docs[key] = doc

    async def find_one(self, filter_: dict) -> dict[str, Any] | None:
        key = (filter_["scope_id"], filter_["service"])
        return self._docs.get(key)

    async def delete_one(self, filter_: dict) -> MagicMock:
        key = (filter_["scope_id"], filter_["service"])
        existed = key in self._docs
        if existed:
            del self._docs[key]
        result = MagicMock()
        result.deleted_count = 1 if existed else 0
        return result


def _backend_with_collection(env: dict[str, str] | None = None):
    """Return (backend, fake_collection) pair with a wired fake collection."""
    env = env or {"SECRET_KEY": "test-app-secret-key-for-vault", "MOZAIKS_CONNECTOR_SECRET_KEY": ""}
    coll = _FakeCollection()

    with patch.dict(os.environ, env, clear=False):
        from mozaiksai.core.secrets.connector_vault import MongoConnectorVaultBackend
        backend = MongoConnectorVaultBackend()
        # Force init
        backend._fernet = None
        backend._fernet_error = None
        with patch.dict(os.environ, env, clear=False):
            backend._get_fernet()

    async def _coll():
        return coll

    backend._collection = _coll
    return backend, coll


# ── describe ─────────────────────────────────────────────────────────────────

def test_mongo_backend_describe_returns_provider_mongo() -> None:
    backend, _ = _backend_with_collection()

    result = asyncio.run(backend.describe())

    assert result["provider"] == "mongo"
    assert result["configured"] is True
    assert result["error"] is None


# ── store_secret ─────────────────────────────────────────────────────────────

def test_mongo_backend_store_returns_success() -> None:
    backend, coll = _backend_with_collection()

    result = asyncio.run(
        backend.store_secret(scope_id="ws_1", service="openai", secret_value="sk-test-key", ttl_days=30)
    )

    assert result["success"] is True
    assert result["provider"] == "mongo"
    assert result["expires_at"] is not None
    assert result.get("secret_available") is True


def test_mongo_backend_store_encrypts_value() -> None:
    backend, coll = _backend_with_collection()
    secret = "sk-super-secret-key"

    asyncio.run(backend.store_secret(scope_id="ws_1", service="openai", secret_value=secret, ttl_days=30))

    doc = coll._docs.get(("ws_1", "openai"))
    assert doc is not None
    # The stored value must not be the raw secret
    assert doc.get("encrypted_value") != secret
    assert secret not in str(doc.get("encrypted_value", ""))


def test_mongo_backend_store_sets_ttl_expiry() -> None:
    import datetime

    backend, coll = _backend_with_collection()

    asyncio.run(
        backend.store_secret(scope_id="ws_1", service="openai", secret_value="sk-key", ttl_days=14)
    )

    doc = coll._docs.get(("ws_1", "openai"))
    expires_at = doc.get("expires_at")
    assert expires_at is not None
    expires_dt = datetime.datetime.fromisoformat(expires_at)
    delta = expires_dt - datetime.datetime.now(datetime.UTC)
    # Should be 14 days ± a few seconds
    assert 13 <= delta.days <= 14


# ── get_secret ────────────────────────────────────────────────────────────────

def test_mongo_backend_get_secret_decrypts_correctly() -> None:
    backend, _ = _backend_with_collection()
    secret = "sk-roundtrip-test"

    asyncio.run(backend.store_secret(scope_id="ws_1", service="anthropic", secret_value=secret, ttl_days=30))
    result = asyncio.run(backend.get_secret(scope_id="ws_1", service="anthropic"))

    assert result["success"] is True
    assert result["secret_value"] == secret
    assert result["provider"] == "mongo"


def test_mongo_backend_get_secret_not_found_returns_failure() -> None:
    backend, _ = _backend_with_collection()

    result = asyncio.run(backend.get_secret(scope_id="ws_1", service="missing_service"))

    assert result["success"] is False
    assert result["secret_value"] is None
    assert result["provider"] == "mongo"


def test_mongo_backend_get_secret_returns_expires_at() -> None:
    backend, _ = _backend_with_collection()

    asyncio.run(backend.store_secret(scope_id="ws_1", service="openai", secret_value="sk-key", ttl_days=7))
    result = asyncio.run(backend.get_secret(scope_id="ws_1", service="openai"))

    assert result["expires_at"] is not None


# ── delete_secret ─────────────────────────────────────────────────────────────

def test_mongo_backend_delete_removes_stored_secret() -> None:
    backend, coll = _backend_with_collection()

    asyncio.run(backend.store_secret(scope_id="ws_1", service="openai", secret_value="sk-key", ttl_days=30))
    result = asyncio.run(backend.delete_secret(scope_id="ws_1", service="openai"))

    assert result["success"] is True
    assert coll._docs.get(("ws_1", "openai")) is None


def test_mongo_backend_delete_missing_returns_failure() -> None:
    backend, _ = _backend_with_collection()

    result = asyncio.run(backend.delete_secret(scope_id="ws_1", service="not_there"))

    assert result["success"] is False


# ── upsert behaviour ──────────────────────────────────────────────────────────

def test_mongo_backend_store_overwrites_existing_secret() -> None:
    backend, _ = _backend_with_collection()

    asyncio.run(backend.store_secret(scope_id="ws_1", service="openai", secret_value="sk-old", ttl_days=30))
    asyncio.run(backend.store_secret(scope_id="ws_1", service="openai", secret_value="sk-new", ttl_days=30))
    result = asyncio.run(backend.get_secret(scope_id="ws_1", service="openai"))

    assert result["secret_value"] == "sk-new"


# ── key derivation ────────────────────────────────────────────────────────────

def test_mongo_backend_uses_explicit_secret_key_env() -> None:
    import base64

    # Generate a valid 32-byte key
    key_bytes = b"a" * 32
    key_b64 = base64.urlsafe_b64encode(key_bytes).decode()

    env = {"MOZAIKS_CONNECTOR_SECRET_KEY": key_b64, "SECRET_KEY": ""}
    with patch.dict(os.environ, env, clear=False):
        from mozaiksai.core.secrets.connector_vault import _derive_fernet_key
        derived = _derive_fernet_key()
        assert derived == base64.urlsafe_b64encode(key_bytes)


def test_mongo_backend_derives_key_from_secret_key_when_no_explicit_key() -> None:
    env = {"MOZAIKS_CONNECTOR_SECRET_KEY": "", "SECRET_KEY": "my-app-secret"}
    with patch.dict(os.environ, env, clear=False):
        from mozaiksai.core.secrets.connector_vault import _derive_fernet_key
        key1 = _derive_fernet_key()
        key2 = _derive_fernet_key()
        # Should be deterministic
        assert key1 == key2
        assert len(key1) > 0


def test_mongo_backend_different_app_secrets_produce_different_keys() -> None:
    from mozaiksai.core.secrets.connector_vault import _derive_fernet_key

    with patch.dict(os.environ, {"MOZAIKS_CONNECTOR_SECRET_KEY": "", "SECRET_KEY": "secret-a"}, clear=False):
        key_a = _derive_fernet_key()
    with patch.dict(os.environ, {"MOZAIKS_CONNECTOR_SECRET_KEY": "", "SECRET_KEY": "secret-b"}, clear=False):
        key_b = _derive_fernet_key()

    assert key_a != key_b


# ── backend selection ─────────────────────────────────────────────────────────

def test_get_connector_vault_backend_auto_no_vault_returns_mongo() -> None:
    from mozaiksai.core.secrets.connector_vault import (
        MongoConnectorVaultBackend,
        get_connector_vault_backend,
        reset_connector_vault_backend,
    )

    reset_connector_vault_backend()
    with patch.dict(
        os.environ,
        {"MOZAIKS_CONNECTOR_SECRET_BACKEND": "auto", "AZURE_KEY_VAULT_NAME": ""},
        clear=False,
    ):
        backend = get_connector_vault_backend()

    assert isinstance(backend, MongoConnectorVaultBackend)
    reset_connector_vault_backend()


def test_get_connector_vault_backend_explicit_mongo_mode() -> None:
    from mozaiksai.core.secrets.connector_vault import (
        MongoConnectorVaultBackend,
        get_connector_vault_backend,
        reset_connector_vault_backend,
    )

    reset_connector_vault_backend()
    with patch.dict(
        os.environ,
        {"MOZAIKS_CONNECTOR_SECRET_BACKEND": "mongo", "AZURE_KEY_VAULT_NAME": ""},
        clear=False,
    ):
        backend = get_connector_vault_backend()

    assert isinstance(backend, MongoConnectorVaultBackend)
    reset_connector_vault_backend()


def test_get_connector_vault_backend_disabled_still_returns_noop() -> None:
    from mozaiksai.core.secrets.connector_vault import (
        NoopConnectorVaultBackend,
        get_connector_vault_backend,
        reset_connector_vault_backend,
    )

    reset_connector_vault_backend()
    with patch.dict(os.environ, {"MOZAIKS_CONNECTOR_SECRET_BACKEND": "disabled"}, clear=False):
        backend = get_connector_vault_backend()

    assert isinstance(backend, NoopConnectorVaultBackend)
    reset_connector_vault_backend()
