from __future__ import annotations

# ruff: noqa: I001

import asyncio
from datetime import UTC, datetime, timedelta

from mozaiksai.core.workflow.generator_support.connector_service import (
    compute_connector_health,
    get_connector,
    get_connector_inventory,
    get_secret,
    save_connector,
    save_connector_draft,
)
from mozaiksai.core.data.persistence.connector_store import ConnectorStore

SECRET_VALUE = "secret-payment-provider-value"


def _future_expiry(days: int = 30) -> str:
    return (datetime.now(UTC) + timedelta(days=days)).isoformat()


class _FakeCursor:
    def __init__(self, docs):
        self._docs = list(docs)
        self._limit = None

    def sort(self, key, direction):
        reverse = int(direction) < 0
        self._docs.sort(key=lambda doc: doc.get(key, 0), reverse=reverse)
        return self

    def limit(self, value):
        self._limit = int(value)
        return self

    async def to_list(self, length=None):
        if self._limit is not None:
            return list(self._docs[: self._limit])
        if length is None:
            return list(self._docs)
        return list(self._docs[:length])


class _DeleteResult:
    def __init__(self, deleted_count):
        self.deleted_count = deleted_count


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
        is_new = False
        if doc is None and upsert:
            doc = dict(query)
            doc["_id"] = self._next_id
            self._next_id += 1
            self.docs.append(doc)
            is_new = True
        if doc is None:
            return
        for key, value in update.get("$setOnInsert", {}).items():
            if is_new:
                doc[key] = value
        for key, value in update.get("$set", {}).items():
            doc[key] = value

    async def find_one(self, query, projection=None):
        for existing in self.docs:
            if all(existing.get(key) == value for key, value in query.items()):
                return dict(existing)
        return None

    def find(self, query):
        filtered = [
            dict(existing)
            for existing in self.docs
            if all(existing.get(key) == value for key, value in query.items())
        ]
        return _FakeCursor(filtered)

    async def delete_one(self, query):
        before = len(self.docs)
        self.docs = [
            existing
            for existing in self.docs
            if not all(existing.get(key) == value for key, value in query.items())
        ]
        return _DeleteResult(before - len(self.docs))


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

    async def describe(self):
        return {
            "provider": "fake_vault",
            "configured": True,
            "mode": "test",
            "vault_name": "fake",
            "secret_prefix": "test",
        }

    async def store_secret(self, *, scope_id: str, service: str, secret_value: str, display_name=None, ttl_days: int = 30):
        key = (scope_id, service)
        self.secrets[key] = secret_value
        return {
            "success": True,
            "provider": "fake_vault",
            "secret_name": f"fake-{scope_id}-{service}",
            "expires_at": _future_expiry(),
            "secret_available": True,
        }

    async def get_secret(self, *, scope_id: str, service: str):
        key = (scope_id, service)
        value = self.secrets.get(key)
        return {
            "success": value is not None,
            "provider": "fake_vault",
            "secret_name": f"fake-{scope_id}-{service}",
            "secret_value": value,
            "expires_at": _future_expiry() if value is not None else None,
            "error": None if value is not None else "missing",
        }

    async def delete_secret(self, *, scope_id: str, service: str):
        key = (scope_id, service)
        existed = key in self.secrets
        self.secrets.pop(key, None)
        return {
            "success": existed,
            "provider": "fake_vault",
            "secret_name": f"fake-{scope_id}-{service}",
            "error": None if existed else "missing",
        }

def test_connector_store_supports_crud() -> None:
    pm = _FakePersistenceManager()
    store = ConnectorStore(pm=pm)

    created = asyncio.run(
        store.upsert(
            scope=ConnectorStore.SCOPE_APP,
            scope_id="app_1",
            service="model_provider",
            display_name="Model Provider",
            user_id="user_1",
            status="metadata_only",
            secret_storage="unmanaged",
            secret_available=False,
            key_length=51,
            notes="Collected during workflow planning.",
            public_config={"base_url": "https://api.example.test"},
        )
    )
    patched = asyncio.run(
        store.patch(
            scope=ConnectorStore.SCOPE_APP,
            scope_id="app_1",
            service="model_provider",
            user_id="user_1",
            display_name="Model Provider Platform",
            status="revoked",
            notes="Operator revoked this connector.",
        )
    )
    listed = asyncio.run(store.list(scope=ConnectorStore.SCOPE_APP, scope_id="app_1"))
    deleted = asyncio.run(store.delete(scope=ConnectorStore.SCOPE_APP, scope_id="app_1", service="model_provider"))

    collection = pm.client["mozaiksai"]["Connectors"]

    assert created["service"] == "model_provider"
    assert created["public_config"] == {"base_url": "https://api.example.test"}
    assert patched["display_name"] == "Model Provider Platform"
    assert patched["status"] == "revoked"
    assert len(listed) == 1
    assert deleted is True
    assert collection.docs == []
    assert any(kwargs.get("name") == "connector_scope_unique" for _keys, kwargs in collection.indexes)

def test_connector_store_separates_workspace_and_app_scope() -> None:
    pm = _FakePersistenceManager()
    store = ConnectorStore(pm=pm)

    asyncio.run(
        store.upsert(
            scope=ConnectorStore.SCOPE_WORKSPACE,
            scope_id="ws_1",
            service="payment_provider",
            status="active",
            secret_storage="fake_vault",
            secret_available=True,
        )
    )
    asyncio.run(
        store.upsert(
            scope=ConnectorStore.SCOPE_APP,
            scope_id="app_1",
            service="payment_provider",
            status="metadata_only",
            secret_storage="unmanaged",
            secret_available=False,
        )
    )

    ws_connector = asyncio.run(store.get(scope=ConnectorStore.SCOPE_WORKSPACE, scope_id="ws_1", service="payment_provider"))
    app_connector = asyncio.run(store.get(scope=ConnectorStore.SCOPE_APP, scope_id="app_1", service="payment_provider"))
    ws_list = asyncio.run(store.list(scope=ConnectorStore.SCOPE_WORKSPACE, scope_id="ws_1"))
    app_list = asyncio.run(store.list(scope=ConnectorStore.SCOPE_APP, scope_id="app_1"))

    assert ws_connector is not None
    assert ws_connector["scope"] == "workspace"
    assert ws_connector["scope_id"] == "ws_1"
    assert ws_connector["status"] == "active"
    assert app_connector is not None
    assert app_connector["scope"] == "app"
    assert app_connector["scope_id"] == "app_1"
    assert app_connector["status"] == "metadata_only"
    assert len(ws_list) == 1
    assert len(app_list) == 1


def test_connector_service_records_metadata_only_status_without_vault() -> None:
    pm = _FakePersistenceManager()
    store = ConnectorStore(pm=pm)

    recorded = asyncio.run(
        save_connector_draft(
            scope=ConnectorStore.SCOPE_APP,
            scope_id="app_1",
            user_id="user_1",
            service="model_provider",
            display_name="Model Provider",
            key_length=48,
            workflow_name="AgentGenerator",
            chat_id="chat_1",
            agent_message_id="msg_1",
            ui_event_id="ui_1",
            store=store,
        )
    )
    connector = asyncio.run(
        get_connector(scope=ConnectorStore.SCOPE_APP, scope_id="app_1", service="model_provider", store=store)
    )
    stored = asyncio.run(
        save_connector(
            scope=ConnectorStore.SCOPE_APP,
            scope_id="app_1",
            user_id="user_1",
            service="model_provider",
            secret_value="secret-model-provider-value",
            display_name="Model Provider",
            store=store,
        )
    )

    assert recorded["saved"] is True
    assert connector is not None
    assert connector["status"] == "metadata_only"
    assert stored["success"] is False
    assert stored["connector"]["status"] == "metadata_only"


def test_connector_service_uses_vault_backend_when_available(monkeypatch) -> None:
    pm = _FakePersistenceManager()
    store = ConnectorStore(pm=pm)
    backend = _FakeVaultBackend()

    import mozaiksai.core.workflow.generator_support.connector_service as connector_service

    monkeypatch.setattr(connector_service, "get_connector_vault_backend", lambda: backend)

    stored = asyncio.run(
        save_connector(
            scope=ConnectorStore.SCOPE_APP,
            scope_id="app_1",
            user_id="user_1",
            service="payment_provider",
            provider="payment_provider",
            integration_id="payment_provider_payments",
            secret_value=SECRET_VALUE,
            display_name="Payment Provider",
            public_config={"webhook_url": "https://hooks.example.test/payments"},
            required_fields=[
                {"name": "api_key", "type": "secret", "required": True, "frontend_safe": False},
                {"name": "webhook_url", "type": "url", "required": True, "frontend_safe": True},
                {"name": "workspace_id", "type": "text", "required": False, "frontend_safe": True},
            ],
            store=store,
        )
    )
    connector = asyncio.run(
        get_connector(scope=ConnectorStore.SCOPE_APP, scope_id="app_1", service="payment_provider", store=store)
    )
    secret = asyncio.run(get_secret(scope_id="app_1", service="payment_provider"))

    assert stored["success"] is True
    assert stored["connector"]["secret_storage"] == "fake_vault"
    assert stored["connector"]["provider"] == "payment_provider"
    assert stored["connector"]["integration_id"] == "payment_provider_payments"
    assert connector is not None
    assert connector["status"] == "active"
    assert connector["secret_storage"] == "fake_vault"
    assert connector["provider"] == "payment_provider"
    assert connector["integration_id"] == "payment_provider_payments"
    assert connector["public_config"] == {"webhook_url": "https://hooks.example.test/payments"}
    assert connector["health"]["status"] == "configured"
    assert connector["health"]["missing_fields"] == []
    assert secret["success"] is True
    assert secret["secret_value"] == SECRET_VALUE


def test_connector_inventory_summarizes_ready_vs_missing_services(monkeypatch) -> None:
    pm = _FakePersistenceManager()
    store = ConnectorStore(pm=pm)
    backend = _FakeVaultBackend()

    import mozaiksai.core.workflow.generator_support.connector_service as connector_service

    monkeypatch.setattr(connector_service, "get_connector_vault_backend", lambda: backend)

    asyncio.run(
        save_connector(
            scope=ConnectorStore.SCOPE_APP,
            scope_id="app_1",
            user_id="user_1",
            service="payment_provider",
            secret_value=SECRET_VALUE,
            display_name="Payment Provider",
            store=store,
        )
    )
    asyncio.run(
        save_connector_draft(
            scope=ConnectorStore.SCOPE_APP,
            scope_id="app_1",
            user_id="user_1",
            service="email_provider",
            display_name="Email Provider",
            key_length=48,
            workflow_name="AgentGenerator",
            chat_id="chat_1",
            agent_message_id="msg_1",
            ui_event_id="ui_1",
            store=store,
        )
    )

    inventory = asyncio.run(
        get_connector_inventory(
            scope=ConnectorStore.SCOPE_APP,
            scope_id="app_1",
            required_services=["payment_provider", "email_provider", "sms_provider"],
            store=store,
        )
    )

    assert inventory["ready_services"] == ["payment_provider"]
    assert inventory["missing_required_services"] == ["email_provider", "sms_provider"]
    assert inventory["known_but_unready_required_services"] == ["email_provider"]
    assert inventory["entirely_missing_required_services"] == ["sms_provider"]

def test_compute_connector_health_reports_missing_secret_without_value() -> None:
    record = {
        "service": "analytics_provider",
        "secret_available": False,
        "key_length": 0,
        "public_config": {"endpoint_url": "https://analytics.example.test"},
    }

    health = compute_connector_health(
        record,
        required_fields=[
            {"name": "api_key", "type": "secret", "required": True, "frontend_safe": False},
            {"name": "endpoint_url", "type": "url", "required": True, "frontend_safe": True},
        ],
    )

    assert health["status"] == "not_configured"
    assert health["missing_fields"] == ["api_key"]
    assert SECRET_VALUE not in repr(health)

def test_compute_connector_health_reports_missing_non_secret_field() -> None:
    record = {
        "service": "analytics_provider",
        "secret_available": True,
        "key_length": 24,
        "public_config": {},
    }

    health = compute_connector_health(
        record,
        required_fields=[
            {"name": "api_key", "type": "secret", "required": True, "frontend_safe": False},
            {"name": "endpoint_url", "type": "url", "required": True, "frontend_safe": True},
            {"name": "workspace_id", "type": "text", "required": False, "frontend_safe": True},
        ],
    )

    assert health["status"] == "not_configured"
    assert health["missing_fields"] == ["endpoint_url"]


def test_compute_connector_health_optional_fields_do_not_block_configured() -> None:
    record = {
        "service": "analytics_provider",
        "secret_available": True,
        "key_length": 24,
        "public_config": {"endpoint_url": "https://analytics.example.test"},
    }

    health = compute_connector_health(
        record,
        required_fields=[
            {"name": "api_key", "type": "secret", "required": True, "frontend_safe": False},
            {"name": "endpoint_url", "type": "url", "required": True, "frontend_safe": True},
            {"name": "workspace_id", "type": "text", "required": False, "frontend_safe": True},
        ],
    )

    assert health["status"] == "configured"
    assert health["missing_fields"] == []


def test_compute_connector_health_for_unknown_connector_is_deterministic() -> None:
    health = compute_connector_health(
        None,
        required_fields=[
            {"name": "api_key", "type": "secret", "required": True, "frontend_safe": False},
            {"name": "endpoint_url", "type": "url", "required": True, "frontend_safe": True},
        ],
        checked_by="manual",
    )

    assert health["status"] == "not_configured"
    assert health["missing_fields"] == ["api_key", "endpoint_url"]
    assert health["checked_by"] == "manual"
    assert health["frontend_safe"] is True
