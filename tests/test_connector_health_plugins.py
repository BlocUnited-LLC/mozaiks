from __future__ import annotations

# ruff: noqa: I001

import asyncio
from pathlib import Path
from typing import Any

from mozaiksai.core.workflow.generator_support import connector_health
from mozaiksai.core.workflow.generator_support.connector_health import (
    ConnectorHealthResult,
    clear_connector_health_providers_for_tests,
    register_connector_health_provider,
    run_connector_health_check,
)
from mozaiksai.core.workflow.generator_support.connector_request import collect_missing_connector_needs
from mozaiksai.core.workflow.generator_support.connector_service import get_connector_inventory, list_connectors, store_connector
from mozaiksai.core.data.persistence.connector_store import AppConnectorStore

SECRET_VALUE = "test-secret-value"


class _FakeCursor:
    def __init__(self, docs):
        self._docs = list(docs)
        self._limit = None

    def sort(self, key, direction):
        reverse = int(direction) < 0
        self._docs.sort(key=lambda doc: doc.get(key, 0), reverse=reverse)
        return self

    def limit(self, n):
        self._limit = n
        return self

    async def to_list(self, length=None):
        docs = self._docs if self._limit is None else self._docs[: self._limit]
        if length is None:
            return list(docs)
        return list(docs[:length])


class _FakeCollection:
    def __init__(self) -> None:
        self.docs = []
        self.indexes = []
        self._next_id = 1

    def list_indexes(self):
        return _FakeCursor([kwargs | {"name": kwargs.get("name")} for _keys, kwargs in self.indexes])

    async def create_index(self, keys, **kwargs):
        self.indexes.append((list(keys), dict(kwargs)))

    async def update_one(self, query, update, upsert=False):
        doc = None
        for existing in self.docs:
            if all(existing.get(key) == value for key, value in query.items()):
                doc = existing
                break
        if doc is None and upsert:
            doc = dict(query)
            doc["_id"] = self._next_id
            self._next_id += 1
            self.docs.append(doc)
        if doc is None:
            return
        for key, value in update.get("$setOnInsert", {}).items():
            doc.setdefault(key, value)
        for key, value in update.get("$set", {}).items():
            doc[key] = value

    async def find_one(self, query, projection=None):
        del projection
        for existing in self.docs:
            if all(existing.get(key) == value for key, value in query.items()):
                return dict(existing)
        return None

    def find(self, query):
        return _FakeCursor([
            dict(existing)
            for existing in self.docs
            if all(existing.get(key) == value for key, value in query.items())
        ])


class _FakeDatabase:
    def __init__(self) -> None:
        self._collections = {}

    def __getitem__(self, collection_name):
        if collection_name not in self._collections:
            self._collections[collection_name] = _FakeCollection()
        return self._collections[collection_name]


class _FakeClient:
    def __init__(self) -> None:
        self._databases = {}

    def __getitem__(self, database_name):
        if database_name not in self._databases:
            self._databases[database_name] = _FakeDatabase()
        return self._databases[database_name]


class _FakePersistence:
    def __init__(self, client) -> None:
        self.client = client

    async def _ensure_client(self) -> None:
        return None


class _FakePersistenceManager:
    def __init__(self) -> None:
        self.client = _FakeClient()
        self.persistence = _FakePersistence(self.client)


class _FakeVaultBackend:
    def __init__(self) -> None:
        self.secrets = {}

    async def store_secret(self, *, app_id: str, service: str, secret_value: str, display_name=None, ttl_days: int = 30):
        del display_name, ttl_days
        self.secrets[(app_id, service)] = secret_value
        return {
            "success": True,
            "provider": "fake_vault",
            "secret_name": f"fake-{app_id}-{service}",
            "expires_at": "2026-06-01T00:00:00+00:00",
        }

    async def get_secret(self, *, app_id: str, service: str):
        value = self.secrets.get((app_id, service))
        return {
            "success": value is not None,
            "provider": "fake_vault",
            "secret_value": value,
            "secret_name": f"fake-{app_id}-{service}",
        }


class _Context:
    def __init__(self, data: dict[str, Any]):
        self.data = dict(data)

    def get(self, key: str, default=None):
        return self.data.get(key, default)

    def set(self, key: str, value):
        self.data[key] = value


class _DemoHealthProvider:
    provider_id = "demo_health_provider"
    supported_integration_ids = ["analytics_provider", "managed_analytics"]

    def __init__(self) -> None:
        self.calls = []

    async def check(self, *, connector, secret_reader, public_config, context):
        secret = await secret_reader.get_secret()
        self.calls.append(
            {
                "service": connector["service"],
                "secret_available": secret.available,
                "secret_repr": repr(secret),
                "public_config": public_config,
                "context": context,
            }
        )
        return ConnectorHealthResult(
            status="healthy",
            message="Connector health check passed.",
            checked_at="2026-05-17T12:00:00+00:00",
            safe_details={
                "endpoint_url": public_config.get("endpoint_url"),
                "api_token": SECRET_VALUE,
                "nested": {"password": SECRET_VALUE, "region": "test"},
            },
        )


class _FailingHealthProvider:
    provider_id = "failing_health_provider"
    supported_integration_ids = ["reporting_provider"]

    async def check(self, **kwargs):
        raise RuntimeError(f"boom {SECRET_VALUE}")


def _store() -> AppConnectorStore:
    return AppConnectorStore(pm=_FakePersistenceManager())


def _seed_configured_connector(store: AppConnectorStore, *, service: str = "analytics_provider") -> None:
    asyncio.run(
        store.upsert_connector(
            app_id="app_1",
            service=service,
            display_name="Hosted Analytics",
            user_id="user_1",
            status="active",
            secret_storage="fake_vault",
            secret_available=True,
            key_length=len(SECRET_VALUE),
            public_config={"endpoint_url": "https://analytics.example.test"},
            required_fields=[
                {"name": "api_key", "type": "secret", "required": True, "frontend_safe": False},
                {"name": "endpoint_url", "type": "url", "required": True, "frontend_safe": True},
            ],
        )
    )


def setup_function() -> None:
    clear_connector_health_providers_for_tests()


def teardown_function() -> None:
    clear_connector_health_providers_for_tests()


def test_no_plugin_registered_returns_configuration_health_without_persisting_provider_check() -> None:
    store = _store()
    _seed_configured_connector(store)

    result = asyncio.run(run_connector_health_check(app_id="app_1", service="analytics_provider", store=store))
    connector = asyncio.run(store.get_connector(app_id="app_1", service="analytics_provider"))

    assert result["status"] == "configured"
    assert result["health_check_supported"] is False
    assert "health_status" not in connector
    assert SECRET_VALUE not in repr(result)


def test_manual_health_check_with_fake_provider_updates_safe_health(monkeypatch) -> None:
    store = _store()
    vault = _FakeVaultBackend()
    provider = _DemoHealthProvider()
    _seed_configured_connector(store)
    vault.secrets[("app_1", "analytics_provider")] = SECRET_VALUE
    monkeypatch.setattr(connector_health, "get_connector_vault_backend", lambda: vault)
    register_connector_health_provider(provider)

    result = asyncio.run(run_connector_health_check(app_id="app_1", service="analytics_provider", store=store))
    connector = asyncio.run(store.get_connector(app_id="app_1", service="analytics_provider"))

    assert result["status"] == "healthy"
    assert result["checked_by"] == "manual"
    assert result["last_checked_at"] == "2026-05-17T12:00:00+00:00"
    assert result["health_details"] == {
        "endpoint_url": "https://analytics.example.test",
        "nested": {"region": "test"},
    }
    assert connector["health_status"] == "healthy"
    assert connector["health_details"] == result["health_details"]
    assert provider.calls[0]["secret_available"] is True
    assert provider.calls[0]["secret_repr"] == "SecretHandle(value=<redacted>)"
    assert SECRET_VALUE not in repr(result)
    assert SECRET_VALUE not in repr(connector)


def test_plugin_exception_returns_sanitized_unhealthy_result() -> None:
    store = _store()
    _seed_configured_connector(store, service="reporting_provider")
    register_connector_health_provider(_FailingHealthProvider())

    result = asyncio.run(run_connector_health_check(app_id="app_1", service="reporting_provider", store=store))

    assert result["status"] == "unhealthy"
    assert result["error_code"] == "provider_check_failed"
    assert result["message"] == "Provider health check failed."
    assert SECRET_VALUE not in repr(result)


def test_list_connectors_does_not_invoke_provider_plugin() -> None:
    store = _store()
    provider = _DemoHealthProvider()
    _seed_configured_connector(store)
    register_connector_health_provider(provider)

    connectors = asyncio.run(list_connectors("app_1", store=store))

    assert connectors[0]["health"]["health_check_supported"] is True
    assert provider.calls == []


def test_connector_save_does_not_invoke_provider_plugin(monkeypatch) -> None:
    store = _store()
    vault = _FakeVaultBackend()
    provider = _DemoHealthProvider()
    monkeypatch.setattr(
        "mozaiksai.core.workflow.generator_support.connector_service.get_connector_vault_backend",
        lambda: vault,
    )
    register_connector_health_provider(provider)

    saved = asyncio.run(
        store_connector(
            app_id="app_1",
            user_id="user_1",
            service="analytics_provider",
            secret_value=SECRET_VALUE,
            display_name="Hosted Analytics",
            public_config={"endpoint_url": "https://analytics.example.test"},
            required_fields=[
                {"name": "api_key", "type": "secret", "required": True, "frontend_safe": False},
                {"name": "endpoint_url", "type": "url", "required": True, "frontend_safe": True},
            ],
            store=store,
        )
    )

    assert saved["success"] is True
    assert provider.calls == []


def test_readiness_ignores_unhealthy_provider_health_by_default() -> None:
    store = _store()
    _seed_configured_connector(store)
    asyncio.run(
        store.update_connector_health(
            app_id="app_1",
            service="analytics_provider",
            health_status="unhealthy",
            health_message="Manual review required.",
            last_checked_at="2026-05-17T12:00:00+00:00",
            checked_by="manual",
        )
    )

    inventory = asyncio.run(
        get_connector_inventory("app_1", required_services=["analytics_provider"], store=store)
    )

    assert inventory["ready_services"] == ["analytics_provider"]
    assert inventory["missing_required_services"] == []


def test_collect_missing_connector_needs_does_not_run_provider_health(monkeypatch) -> None:
    provider = _DemoHealthProvider()
    register_connector_health_provider(provider)

    async def fake_inventory(app_id: str, required_services=None, store=None):
        del app_id, store
        return {
            "required_services": list(required_services or []),
            "ready_services": ["analytics_provider"],
            "missing_required_services": [],
            "connectors": [
                {
                    "service": "analytics_provider",
                    "secret_available": True,
                    "health_status": "unhealthy",
                }
            ],
        }

    monkeypatch.setattr(
        "mozaiksai.core.workflow.generator_support.connector_request.get_connector_inventory",
        fake_inventory,
    )
    context = _Context(
        {
            "app_id": "app_1",
            "app_build_plan": {
                "external_integrations": [
                    {"service": "analytics_provider", "required_at": "validation_time"}
                ]
            },
        }
    )

    result = asyncio.run(collect_missing_connector_needs(context_variables=context))

    assert result["status"] == "ready"
    assert provider.calls == []


def test_studio_health_check_endpoint_requires_auth_and_returns_redacted_health(monkeypatch) -> None:
    from mozaiksai.hosts import studio

    source = (Path(studio.__file__).read_text(encoding="utf-8") if studio.__file__ else "")
    assert '@app.post("/api/studio/integrations/connectors/{service}/health-check")' in source
    assert "principal: UserPrincipal = Depends(require_user_scope)" in source

    async def fake_runner(*, app_id: str, service: str, checked_by: str):
        assert app_id == "app_1"
        assert service == "analytics_provider"
        assert checked_by == "manual"
        return {
            "status": "healthy",
            "message": "Connector health check passed.",
            "last_checked_at": "2026-05-17T12:00:00+00:00",
            "checked_by": "manual",
            "health_details": {
                "endpoint_url": "https://analytics.example.test",
                "api_key": SECRET_VALUE,
                "nested": {"token": SECRET_VALUE, "region": "test"},
            },
            "health_check_supported": True,
            "frontend_safe": True,
        }

    monkeypatch.setattr(studio, "_resolve_studio_scope", lambda principal, app_id=None: (app_id, "user_1"))
    monkeypatch.setattr(studio, "run_connector_health_check", fake_runner)

    response = asyncio.run(
        studio.check_integration_connector_health(
            service="analytics_provider",
            app_id="app_1",
            principal=object(),
        )
    )

    assert response["health"]["status"] == "healthy"
    assert response["health"]["safe_details"] == {
        "endpoint_url": "https://analytics.example.test",
        "nested": {"region": "test"},
    }
    assert SECRET_VALUE not in repr(response)


def test_studio_connector_request_models_validate_with_postponed_annotations() -> None:
    from mozaiksai.hosts.studio import IntegrationConnectorCreateRequest

    request = IntegrationConnectorCreateRequest.model_validate(
        {
            "service": "analytics_provider",
            "display_name": "Hosted Analytics",
            "public_config": {"endpoint_url": "http://localhost:8100/api/health"},
            "required_fields": [
                {
                    "name": "api_key",
                    "label": "API Key",
                    "type": "secret",
                    "required": True,
                    "frontend_safe": False,
                }
            ],
            "secret_value": SECRET_VALUE,
        }
    )

    assert request.service == "analytics_provider"
    assert request.required_fields[0]["name"] == "api_key"

