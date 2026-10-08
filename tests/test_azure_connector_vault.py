"""Provider-free checks for scoped Azure connector secret names and tags."""

from __future__ import annotations

import asyncio
import importlib
import json
import sys
from datetime import UTC, datetime
from types import ModuleType, SimpleNamespace

import pytest

from mozaiksai.core.secrets.connector_vault import AzureKeyVaultConnectorVaultBackend


class _FakeSecretClient:
    def __init__(self) -> None:
        self.secrets: dict[str, SimpleNamespace] = {}
        self.versions: dict[str, list[SimpleNamespace]] = {}
        self.deleted: list[str] = []
        self.set_calls: list[str] = []
        self.version_list_error: Exception | None = None

    def set_secret(self, name, value, *, tags, content_type, expires_on):
        assert content_type == "mozaiks.connector.secret"
        self.set_calls.append(name)
        properties = SimpleNamespace(tags=dict(tags), expires_on=expires_on, version=f"v{len(self.versions.get(name, [])) + 1}")
        secret = SimpleNamespace(value=value, properties=properties)
        self.secrets[name] = secret
        self.versions.setdefault(name, []).append(properties)
        return secret

    def get_secret(self, name):
        if name not in self.secrets:
            from azure.core.exceptions import ResourceNotFoundError

            response = SimpleNamespace(
                reason="Not Found", status_code=404,
                text=lambda: json.dumps({"error": {"code": "SecretNotFound", "message": "Not found"}}),
            )
            raise ResourceNotFoundError(response=response)
        return self.secrets[name]

    def list_properties_of_secret_versions(self, name):
        if self.version_list_error is not None:
            raise self.version_list_error
        return iter(self.versions[name])

    def begin_delete_secret(self, name):
        self.deleted.append(name)
        self.secrets.pop(name)
        self.versions.pop(name)


@pytest.fixture(autouse=True)
def azure_errors(monkeypatch):
    try:
        importlib.import_module("azure.core.exceptions")
        return
    except ModuleNotFoundError as exc:
        if exc.name not in {"azure", "azure.core", "azure.core.exceptions"}:
            raise

    class ResourceNotFoundError(Exception):
        def __init__(self, *, response):
            self.error = SimpleNamespace(code=json.loads(response.text())["error"]["code"])

    azure = ModuleType("azure")
    azure.__path__ = []
    core = ModuleType("azure.core")
    core.__path__ = []
    errors = ModuleType("azure.core.exceptions")
    errors.ResourceNotFoundError = ResourceNotFoundError
    monkeypatch.setitem(sys.modules, "azure", azure)
    monkeypatch.setitem(sys.modules, "azure.core", core)
    monkeypatch.setitem(sys.modules, "azure.core.exceptions", errors)


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
            assert result["status"] == "error"
            assert result["secret_value"] is None
            assert result["error"] == "Secret identity does not match connector."

    asyncio.run(exercise())


def test_azure_delete_rejects_missing_or_mismatched_identity_tags() -> None:
    backend, client = _backend()

    async def exercise():
        stored = await backend.store_secret(scope="app", scope_id="id", service="billing", secret_value="value")
        name = stored["secret_name"]
        for tags in ({}, {"scope": "workspace", "scope_id": "id", "service": "billing", "managed_by": "mozaiks"}):
            client.secrets[name].properties.tags = tags
            result = await backend.delete_secret(scope="app", scope_id="id", service="billing")
            assert result["success"] is False
            assert result["error"] == "Secret identity does not match connector."
            assert name in client.secrets
            assert client.deleted == []
        client.secrets[name].properties.tags = {
            "managed_by": "mozaiks", "connector_prefix": "mozaiks-connector",
            "scope": "app", "scope_id": "id", "service": "billing"
        }
        assert (await backend.delete_secret(scope="app", scope_id="id", service="billing"))["success"]
        assert client.deleted == [name]

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


def test_azure_store_refuses_existing_foreign_name_without_creating_version() -> None:
    backend, client = _backend()

    async def exercise():
        first = await backend.store_secret(scope="app", scope_id="id", service="billing", secret_value="first")
        name = first["secret_name"]
        client.versions[name][0].tags["scope"] = "workspace"
        result = await backend.store_secret(scope="app", scope_id="id", service="billing", secret_value="second")
        assert result["success"] is False
        assert result["error"] == "Secret identity does not match connector."
        assert client.set_calls == [name]
        assert len(client.versions[name]) == 1

    asyncio.run(exercise())


def test_azure_mutations_refuse_mixed_or_unreadable_version_history() -> None:
    backend, client = _backend()

    async def exercise():
        first = await backend.store_secret(scope="app", scope_id="id", service="billing", secret_value="first")
        name = first["secret_name"]
        assert (await backend.store_secret(scope="app", scope_id="id", service="billing", secret_value="second"))["success"]
        client.versions[name][0].tags["scope"] = "workspace"
        assert (await backend.store_secret(scope="app", scope_id="id", service="billing", secret_value="third"))["success"] is False
        assert (await backend.delete_secret(scope="app", scope_id="id", service="billing"))["success"] is False
        assert client.set_calls == [name, name]
        assert client.deleted == []
        client.versions[name][0].tags["scope"] = "app"
        client.version_list_error = RuntimeError("list unavailable")
        assert (await backend.store_secret(scope="app", scope_id="id", service="billing", secret_value="third"))["success"] is False
        assert (await backend.delete_secret(scope="app", scope_id="id", service="billing"))["success"] is False
        assert client.set_calls == [name, name]
        assert client.deleted == []
        client.version_list_error = None
        assert (await backend.delete_secret(scope="app", scope_id="id", service="billing"))["success"]
        assert client.deleted == [name]

    asyncio.run(exercise())
