"""Provider-free checks for scoped Azure connector secret names and tags."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace

from mozaiksai.core.secrets.connector_vault import AzureKeyVaultConnectorVaultBackend


class _FakeSecretClient:
    def __init__(self) -> None:
        self.secrets: dict[str, SimpleNamespace] = {}
        self.deleted: list[str] = []

    def set_secret(self, name, value, *, tags, content_type, expires_on):
        assert content_type == "mozaiks.connector.secret"
        properties = SimpleNamespace(tags=dict(tags), expires_on=expires_on, version="v1")
        secret = SimpleNamespace(value=value, properties=properties)
        self.secrets[name] = secret
        return secret

    def get_secret(self, name):
        return self.secrets[name]

    def begin_delete_secret(self, name):
        self.deleted.append(name)
        self.secrets.pop(name)


def _backend() -> tuple[AzureKeyVaultConnectorVaultBackend, _FakeSecretClient]:
    backend = AzureKeyVaultConnectorVaultBackend()
    client = _FakeSecretClient()
    backend._client = client  # noqa: SLF001 - no Azure network in this test
    return backend, client


def test_azure_scopes_and_service_aliases_have_independent_names_and_values() -> None:
    backend, client = _backend()

    async def exercise():
        app = await backend.store_secret(scope="app", scope_id="same", service="foo_bar", secret_value="app-value")
        workspace = await backend.store_secret(
            scope="workspace", scope_id="same", service="foo_bar", secret_value="workspace-value"
        )
        alias = await backend.store_secret(scope="app", scope_id="same", service="foo-bar", secret_value="alias-value")
        assert len({app["secret_name"], workspace["secret_name"], alias["secret_name"]}) == 3
        assert client.secrets[app["secret_name"]].properties.tags["scope"] == "app"
        assert client.secrets[workspace["secret_name"]].properties.tags["scope"] == "workspace"
        assert (await backend.get_secret(scope="app", scope_id="same", service="foo_bar"))["secret_value"] == "app-value"
        assert (await backend.get_secret(scope="workspace", scope_id="same", service="foo_bar"))["secret_value"] == "workspace-value"
        assert (await backend.get_secret(scope="app", scope_id="same", service="foo-bar"))["secret_value"] == "alias-value"
        assert (await backend.delete_secret(scope="app", scope_id="same", service="foo_bar"))["success"]
        assert client.deleted == [app["secret_name"]]
        assert (await backend.get_secret(scope="workspace", scope_id="same", service="foo_bar"))["success"]

    asyncio.run(exercise())


def test_azure_read_rejects_missing_or_mismatched_identity_tags() -> None:
    backend, client = _backend()

    async def exercise():
        stored = await backend.store_secret(scope="app", scope_id="id", service="billing", secret_value="value")
        name = stored["secret_name"]
        for tags in ({}, {"scope": "workspace", "scope_id": "id", "service": "billing", "managed_by": "mozaiks"}):
            client.secrets[name].properties.tags = tags
            result = await backend.get_secret(scope="app", scope_id="id", service="billing")
            assert result["success"] is False
            assert result["secret_value"] is None
            assert result["error"] == "Secret identity does not match connector."

    asyncio.run(exercise())


def test_azure_tags_keep_exact_normalized_service() -> None:
    backend, client = _backend()
    stored = asyncio.run(backend.store_secret(
        scope="app", scope_id="id", service=" Foo Bar ", secret_value="value"
    ))
    tags = client.secrets[stored["secret_name"]].properties.tags
    assert tags["service"] == "foo_bar"
    assert tags["scope_id"] == "id"
    assert isinstance(client.secrets[stored["secret_name"]].properties.expires_on, datetime)
    assert client.secrets[stored["secret_name"]].properties.expires_on.tzinfo == UTC
