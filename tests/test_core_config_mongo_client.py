from __future__ import annotations

from unittest.mock import Mock

import pytest

from mozaiksai.core import core_config
from mozaiksai.core.secrets import SecretContractError, SecretResolutionError


def test_get_mongo_client_reuses_cached_client_until_closed(monkeypatch) -> None:
    created = []

    class _FakeClient:
        def __init__(self, conn_str: str) -> None:
            self.conn_str = conn_str
            self.closed = False
            created.append(self)

        def close(self) -> None:
            self.closed = True

    monkeypatch.setenv("MONGO_URI", "mongodb://example")
    monkeypatch.setattr(core_config, "AsyncIOMotorClient", _FakeClient)

    core_config.close_mongo_client()
    first = core_config.get_mongo_client()
    second = core_config.get_mongo_client()
    core_config.close_mongo_client()
    third = core_config.get_mongo_client()
    core_config.close_mongo_client()

    assert first is second
    assert first is not third
    assert len(created) == 2
    assert created[0].closed is True
    assert created[1].closed is True


def test_mongo_uses_selected_app_secret_reference(monkeypatch, tmp_path) -> None:
    from mozaiksai.core.secrets import app_secrets

    policy = tmp_path / "secrets.yaml"
    policy.write_text(
        "version: 1\nprovider:\n  type: azure_key_vault\n  azure_key_vault:\n"
        "    vault_url: https://example.vault.azure.net\nsecrets:\n"
        "  - env: MONGO_URI\n    azure_key_vault: {secret_name: selected-app-database}\n"
    )
    monkeypatch.setenv("MOZAIKS_SECRETS_CONFIG_PATH", str(policy))
    monkeypatch.delenv("MONGO_URI", raising=False)
    monkeypatch.delenv("MONGO_URI_SECRET_NAME", raising=False)
    client = Mock()
    client.get_secret.return_value.value = "mongodb://selected-app"
    monkeypatch.setattr(app_secrets, "build_secret_client", Mock(return_value=client))
    motor = Mock()
    monkeypatch.setattr(core_config, "AsyncIOMotorClient", motor)

    core_config.close_mongo_client()
    try:
        assert core_config.get_mongo_client() is motor.return_value
        client.get_secret.assert_called_once_with("selected-app-database")
        motor.assert_called_once_with("mongodb://selected-app")
    finally:
        core_config.close_mongo_client()


def test_mongo_rejects_invalid_manifest_even_with_direct_env(monkeypatch, tmp_path) -> None:
    policy = tmp_path / "secrets.yaml"
    policy.write_text("version: 1\nsecrets: [retired-string-reference]\n")
    monkeypatch.setenv("MOZAIKS_SECRETS_CONFIG_PATH", str(policy))
    monkeypatch.setenv("MONGO_URI", "mongodb://example")
    motor = Mock()
    monkeypatch.setattr(core_config, "AsyncIOMotorClient", motor)
    with pytest.raises(SecretContractError):
        core_config.get_mongo_client()
    motor.assert_not_called()


def test_mongo_does_not_accept_retired_secret_aliases(monkeypatch, tmp_path) -> None:
    policy = tmp_path / "secrets.yaml"
    policy.write_text("version: 1\nprovider: {type: env}\nsecrets: []\n")
    monkeypatch.setenv("MOZAIKS_SECRETS_CONFIG_PATH", str(policy))
    monkeypatch.delenv("MONGO_URI", raising=False)
    for retired in ("MONGOURI", "MONGODB_URI", "MONGO_URL"):
        monkeypatch.setenv(retired, "mongodb://retired")
    with pytest.raises(SecretResolutionError, match="MONGO_URI"):
        core_config.get_mongo_client()

