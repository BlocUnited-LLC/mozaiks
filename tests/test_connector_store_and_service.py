from __future__ import annotations

import asyncio

from mozaiksai.core.data.persistence.connector_store import AppConnectorStore
from mozaiksai.core.workflow.generator_support.connector_service import (
    get_connector_inventory,
    get_connector_status,
    get_secret_for_e2b,
    record_connector_metadata,
    store_connector,
)


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

    async def store_secret(self, *, app_id: str, service: str, secret_value: str, display_name=None, ttl_days: int = 30):
        key = (app_id, service)
        self.secrets[key] = secret_value
        return {
            "success": True,
            "provider": "fake_vault",
            "secret_name": f"fake-{app_id}-{service}",
            "expires_at": "2026-06-01T00:00:00+00:00",
            "secret_available": True,
        }

    async def get_secret(self, *, app_id: str, service: str):
        key = (app_id, service)
        value = self.secrets.get(key)
        return {
            "success": value is not None,
            "provider": "fake_vault",
            "secret_name": f"fake-{app_id}-{service}",
            "secret_value": value,
            "expires_at": "2026-06-01T00:00:00+00:00" if value is not None else None,
            "error": None if value is not None else "missing",
        }

    async def delete_secret(self, *, app_id: str, service: str):
        key = (app_id, service)
        existed = key in self.secrets
        self.secrets.pop(key, None)
        return {
            "success": existed,
            "provider": "fake_vault",
            "secret_name": f"fake-{app_id}-{service}",
            "error": None if existed else "missing",
        }


def test_app_connector_store_supports_crud() -> None:
    pm = _FakePersistenceManager()
    store = AppConnectorStore(pm=pm)

    created = asyncio.run(
        store.upsert_connector(
            app_id="app_1",
            service="openai",
            display_name="OpenAI",
            user_id="user_1",
            status="metadata_only",
            secret_storage="unmanaged",
            secret_available=False,
            key_length=51,
            notes="Collected during workflow planning.",
        )
    )
    patched = asyncio.run(
        store.patch_connector(
            app_id="app_1",
            service="openai",
            user_id="user_1",
            display_name="OpenAI Platform",
            status="revoked",
            notes="Operator revoked this connector.",
        )
    )
    listed = asyncio.run(store.list_connectors(app_id="app_1"))
    deleted = asyncio.run(store.delete_connector(app_id="app_1", service="openai"))

    collection = pm.client["mozaiksai"]["AppConnectors"]

    assert created["service"] == "openai"
    assert patched["display_name"] == "OpenAI Platform"
    assert patched["status"] == "revoked"
    assert len(listed) == 1
    assert deleted is True
    assert collection.docs == []
    assert any(kwargs.get("name") == "app_connector_unique" for _keys, kwargs in collection.indexes)


def test_connector_service_records_metadata_only_status_without_vault() -> None:
    pm = _FakePersistenceManager()
    store = AppConnectorStore(pm=pm)

    recorded = asyncio.run(
        record_connector_metadata(
            app_id="app_1",
            user_id="user_1",
            service="anthropic",
            display_name="Anthropic",
            key_length=48,
            workflow_name="AgentGenerator",
            chat_id="chat_1",
            agent_message_id="msg_1",
            ui_event_id="ui_1",
            store=store,
        )
    )
    status = asyncio.run(get_connector_status("app_1", "anthropic", store=store))
    stored = asyncio.run(
        store_connector(
            app_id="app_1",
            user_id="user_1",
            service="anthropic",
            secret_value="sk-ant-1234567890",
            display_name="Anthropic",
            store=store,
        )
    )

    assert recorded["saved"] is True
    assert status["exists"] is True
    assert status["status"] == "metadata_only"
    assert stored["success"] is False
    assert stored["metadata_saved"] is True


def test_connector_service_uses_vault_backend_when_available(monkeypatch) -> None:
    pm = _FakePersistenceManager()
    store = AppConnectorStore(pm=pm)
    backend = _FakeVaultBackend()

    import mozaiksai.core.workflow.generator_support.connector_service as connector_service

    monkeypatch.setattr(connector_service, "get_connector_vault_backend", lambda: backend)

    stored = asyncio.run(
        store_connector(
            app_id="app_1",
            user_id="user_1",
            service="stripe",
            secret_value="sk_live_123456",
            display_name="Stripe",
            store=store,
        )
    )
    status = asyncio.run(get_connector_status("app_1", "stripe", store=store))
    secret = asyncio.run(get_secret_for_e2b("app_1", "stripe"))

    assert stored["success"] is True
    assert stored["provider"] == "fake_vault"
    assert status["status"] == "active"
    assert status["connector"]["secret_storage"] == "fake_vault"
    assert secret["success"] is True
    assert secret["secret_value"] == "sk_live_123456"


def test_connector_inventory_summarizes_ready_vs_missing_services(monkeypatch) -> None:
    pm = _FakePersistenceManager()
    store = AppConnectorStore(pm=pm)
    backend = _FakeVaultBackend()

    import mozaiksai.core.workflow.generator_support.connector_service as connector_service

    monkeypatch.setattr(connector_service, "get_connector_vault_backend", lambda: backend)

    asyncio.run(
        store_connector(
            app_id="app_1",
            user_id="user_1",
            service="stripe",
            secret_value="sk_live_123456",
            display_name="Stripe",
            store=store,
        )
    )
    asyncio.run(
        record_connector_metadata(
            app_id="app_1",
            user_id="user_1",
            service="sendgrid",
            display_name="SendGrid",
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
            "app_1",
            required_services=["stripe", "sendgrid", "twilio"],
            store=store,
        )
    )

    assert inventory["ready_services"] == ["stripe"]
    assert inventory["missing_required_services"] == ["sendgrid", "twilio"]
    assert inventory["known_but_unready_required_services"] == ["sendgrid"]
    assert inventory["entirely_missing_required_services"] == ["twilio"]
