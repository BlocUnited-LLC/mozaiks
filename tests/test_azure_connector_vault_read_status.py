"""Azure connector reads distinguish a missing secret from vault failures."""

from __future__ import annotations

import asyncio
import importlib
import json
import sys
from types import ModuleType, SimpleNamespace

import pytest

from mozaiksai.core.secrets.connector_vault import (
    AzureKeyVaultConnectorVaultBackend,
    NoopConnectorVaultBackend,
)
from mozaiksai.core.workflow.generator_support import connector_service


@pytest.fixture
def azure_errors(monkeypatch):
    """Use the real SDK locally and a scoped exception stub in core-only CI."""
    try:
        return importlib.import_module("azure.core.exceptions")
    except ModuleNotFoundError as exc:
        if exc.name not in {"azure", "azure.core", "azure.core.exceptions"}:
            raise

    class ResourceNotFoundError(Exception):
        def __init__(self, *, response):
            self.error = SimpleNamespace(code=json.loads(response.text())["error"]["code"])

    class ServiceRequestError(Exception):
        pass

    azure = ModuleType("azure")
    azure.__path__ = []
    core = ModuleType("azure.core")
    core.__path__ = []
    errors = ModuleType("azure.core.exceptions")
    errors.ResourceNotFoundError = ResourceNotFoundError
    errors.ServiceRequestError = ServiceRequestError
    monkeypatch.setitem(sys.modules, "azure", azure)
    monkeypatch.setitem(sys.modules, "azure.core", core)
    monkeypatch.setitem(sys.modules, "azure.core.exceptions", errors)
    return errors


class _FakeClient:
    def __init__(self, *, secret=None, error: Exception | None = None):
        self.secret = secret
        self.error = error

    def get_secret(self, _name: str):
        if self.error is not None:
            raise self.error
        return self.secret


def _azure_error(code: str, azure_errors):
    response = SimpleNamespace(
        reason="Not Found",
        status_code=404,
        text=lambda: json.dumps({"error": {"code": code, "message": "Not found"}}),
    )
    return azure_errors.ResourceNotFoundError(response=response)


def _read(client: _FakeClient):
    backend = AzureKeyVaultConnectorVaultBackend()
    backend._client = client
    return asyncio.run(backend.get_secret(scope="app", scope_id="app-1", service="mozaikspay"))


def test_azure_confirmed_secret_not_found(azure_errors) -> None:
    result = _read(_FakeClient(error=_azure_error("SecretNotFound", azure_errors)))

    assert result["success"] is False
    assert result["status"] == "not_found"
    assert result["secret_value"] is None


def test_azure_other_404_does_not_prove_secret_absence(azure_errors) -> None:
    result = _read(_FakeClient(error=_azure_error("VaultNotFound", azure_errors)))

    assert result["success"] is False
    assert result["status"] == "error"
    assert result["secret_value"] is None


def test_azure_transport_failure_is_not_absence(azure_errors) -> None:
    result = _read(_FakeClient(error=azure_errors.ServiceRequestError("temporarily unavailable")))

    assert result["success"] is False
    assert result["status"] == "error"
    assert result["secret_value"] is None
    assert "temporarily unavailable" not in str(result)


def test_azure_found_secret_keeps_existing_read_value(azure_errors) -> None:
    secret = SimpleNamespace(
        value="test-secret-value",
        properties=SimpleNamespace(
            expires_on=None,
            tags={"managed_by": "mozaiks", "connector_prefix": "mozaiks-connector", "scope": "app", "scope_id": "app-1", "service": "mozaikspay"},
        ),
    )
    result = _read(_FakeClient(secret=secret))

    assert result["success"] is True
    assert result["status"] == "found"
    assert result["secret_value"] == "test-secret-value"


def test_disabled_backend_is_error_not_absence() -> None:
    result = asyncio.run(
        NoopConnectorVaultBackend().get_secret(scope="app", scope_id="app-1", service="mozaikspay")
    )

    assert result["success"] is False
    assert result["status"] == "error"
    assert result["secret_value"] is None


def test_connector_service_preserves_backend_read_status(monkeypatch) -> None:
    class _Vault:
        async def get_secret(self, *, scope: str, scope_id: str, service: str):
            assert (scope, scope_id, service) == ("app", "app-1", "mozaikspay")
            return {
                "success": False,
                "status": "not_found",
                "provider": "mongo",
                "secret_name": "test-name",
                "secret_value": None,
                "error": "Secret not found.",
            }

    monkeypatch.setattr(connector_service, "get_connector_vault_backend", lambda: _Vault())
    result = asyncio.run(connector_service.get_secret(scope="app", scope_id="app-1", service="mozaikspay"))

    assert result["status"] == "not_found"
    assert result["success"] is False
    assert result["secret_value"] is None
