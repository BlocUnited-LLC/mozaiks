from __future__ import annotations

# ruff: noqa: I001

import asyncio
from pathlib import Path
from typing import Any

import pytest

from mozaiksai.core.workflow.generator_support import connector_request, connector_service
from mozaiksai.core.data.persistence.connector_store import ConnectorStore

REPO_ROOT = Path(__file__).resolve().parents[1]
SECRET_VALUE = "secret-analytics-provider-api-key"


class _Context:
    def __init__(self, data: dict[str, Any]):
        self.data = dict(data)

    def get(self, key: str, default=None):
        return self.data.get(key, default)

    def set(self, key: str, value):
        self.data[key] = value


class _FakeCursor:
    def __init__(self, docs):
        self._docs = list(docs)
        self._limit: int | None = None

    def sort(self, key, direction):
        reverse = int(direction) < 0
        self._docs.sort(key=lambda doc: doc.get(key, 0), reverse=reverse)
        return self

    def limit(self, n: int):
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
        self._next_id = 1

    def list_indexes(self):
        return _FakeCursor([])

    async def create_index(self, keys, **kwargs):
        return None

    async def update_one(self, query, update, upsert=False):
        doc = next(
            (existing for existing in self.docs if all(existing.get(key) == value for key, value in query.items())),
            None,
        )
        is_new = False
        if doc is None and upsert:
            doc = dict(query)
            doc["_id"] = self._next_id
            self._next_id += 1
            self.docs.append(doc)
            is_new = True
        if doc is None:
            return None
        if is_new:
            doc.update(update.get("$setOnInsert", {}))
        doc.update(update.get("$set", {}))
        return None

    async def find_one(self, query, projection=None):
        del projection
        for existing in self.docs:
            if all(existing.get(key) == value for key, value in query.items()):
                return dict(existing)
        return None

    def find(self, query):
        return _FakeCursor(
            [
                dict(existing)
                for existing in self.docs
                if all(existing.get(key) == value for key, value in query.items())
            ]
        )


class _FakeDatabase:
    def __init__(self) -> None:
        self.collections = {}

    def __getitem__(self, name):
        self.collections.setdefault(name, _FakeCollection())
        return self.collections[name]


class _FakeClient:
    def __init__(self) -> None:
        self.databases = {}

    def __getitem__(self, name):
        self.databases.setdefault(name, _FakeDatabase())
        return self.databases[name]


class _FakePersistence:
    def __init__(self) -> None:
        self.client = _FakeClient()

    async def _ensure_client(self) -> None:
        return None


class _FakePersistenceManager:
    def __init__(self) -> None:
        self.persistence = _FakePersistence()


class _FakeVaultBackend:
    def __init__(self) -> None:
        self.secrets = {}

    async def store_secret(self, *, scope_id: str, service: str, secret_value: str, display_name=None, ttl_days: int = 30):
        del display_name, ttl_days
        self.secrets[(scope_id, service)] = secret_value
        return {
            "success": True,
            "provider": "fake_vault",
            "secret_name": f"fake-{scope_id}-{service}",
            "expires_at": "2026-06-01T00:00:00+00:00",
        }

    async def get_secret(self, *, scope_id: str, service: str):
        value = self.secrets.get((scope_id, service))
        return {
            "success": value is not None,
            "provider": "fake_vault",
            "secret_name": f"fake-{scope_id}-{service}",
            "secret_value": value,
            "expires_at": "2026-06-01T00:00:00+00:00" if value is not None else None,
        }

    async def delete_secret(self, *, scope_id: str, service: str):
        existed = (scope_id, service) in self.secrets
        self.secrets.pop((scope_id, service), None)
        return {"success": existed, "provider": "fake_vault"}


def _analytics_required_fields() -> list[dict[str, Any]]:
    return [
        {
            "name": "api_key",
            "label": "API Key",
            "type": "secret",
            "required": True,
            "frontend_safe": False,
        },
        {
            "name": "endpoint_url",
            "label": "Endpoint URL",
            "type": "url",
            "required": True,
            "frontend_safe": True,
        },
        {
            "name": "workspace_id",
            "label": "Workspace ID",
            "type": "text",
            "required": False,
            "frontend_safe": True,
        },
    ]


def _analytics_plan() -> dict[str, Any]:
    fields = _analytics_required_fields()
    return {
        "app_kind": "analytics-reporting",
        "external_integrations": [
            {
                "name": "Hosted Analytics",
                "service": "analytics_provider",
                "provider": "managed_analytics",
                "kind": "api_key",
                "purpose": "Send event analytics to an external analytics API.",
                "required_at": "validation_time",
                "required_fields": fields,
            }
        ],
        "build_tasks": [
            {
                "task_id": "task_analytics_adapter",
                "task_type": "api_surface",
                "integration_needs": [
                    {
                        "service": "analytics_provider",
                        "provider": "managed_analytics",
                        "purpose": "Validate analytics adapter configuration.",
                        "required_at": "validation_time",
                        "required_fields": fields,
                    }
                ],
            }
        ],
    }


def test_plan_fixture_declares_neutral_analytics_integration() -> None:
    plan = _analytics_plan()
    integration = plan["external_integrations"][0]
    task_need = plan["build_tasks"][0]["integration_needs"][0]

    assert integration["service"] == "analytics_provider"
    assert integration["provider"] == "managed_analytics"
    assert task_need["service"] == "analytics_provider"
    assert integration["required_fields"][0]["type"] == "secret"
    assert integration["required_fields"][0]["frontend_safe"] is False
    assert [field["name"] for field in integration["required_fields"][1:]] == [
        "endpoint_url",
        "workspace_id",
    ]


@pytest.mark.asyncio
async def test_appgenerator_integration_readiness_blocks_requests_saves_and_passes(monkeypatch) -> None:
    connector_store = ConnectorStore(pm=_FakePersistenceManager())
    vault = _FakeVaultBackend()
    ui_calls: list[dict[str, Any]] = []

    monkeypatch.setattr(connector_service, "get_connector_vault_backend", lambda: vault)

    async def inventory(*, scope, scope_id, required_services=None, store=None):
        del store
        return await connector_service.get_connector_inventory(
            scope=scope,
            scope_id=scope_id,
            required_services=required_services,
            store=connector_store,
        )

    async def record_metadata(**kwargs):
        return await connector_service.save_connector_draft(
            **kwargs,
            store=connector_store,
        )

    async def save_connector(**kwargs):
        return await connector_service.save_connector(
            **kwargs,
            store=connector_store,
        )

    async def fake_use_ui_tool(component, payload, *, chat_id=None, workflow_name=None):
        ui_calls.append(
            {
                "component": component,
                "payload": payload,
                "chat_id": chat_id,
                "workflow_name": workflow_name,
            }
        )
        return {
            "status": "submitted",
            "ui_event_id": "evt-analytics-provider",
            "data": {
                "status": "submitted",
                "services": [
                    {
                        "service": "analytics_provider",
                        "fields": {
                            "api_key": SECRET_VALUE,
                            "endpoint_url": "https://analytics.example.test/events",
                            "workspace_id": "workspace-neutral",
                        },
                    }
                ],
            },
        }

    monkeypatch.setattr(connector_request, "get_connector_inventory", inventory)
    monkeypatch.setattr(connector_request, "save_connector_draft", record_metadata)
    monkeypatch.setattr(connector_request, "save_connector", save_connector)
    monkeypatch.setattr(connector_request, "use_ui_tool", fake_use_ui_tool)

    context = _Context(
        {
            "workflow_name": "AppGenerator",
            "run_id": "run-analytics-smoke",
            "chat_id": "chat-analytics-smoke",
            "app_id": "app-analytics-smoke",
            "user_id": "user-operator",
            "current_build_task_id": "task_analytics_adapter",
            "app_build_plan": _analytics_plan(),
        }
    )

    dry_run = await connector_request.collect_missing_connector_needs(
        context_variables=context,
        prompt=False,
    )

    assert dry_run["status"] == "needs_configuration"
    assert dry_run["unresolved_required_services"] == ["analytics_provider"]

    result = await connector_request.collect_missing_connector_needs(context_variables=context)

    assert result["status"] == "ready"
    assert result["unresolved_required_services"] == []
    assert context.data["integration_readiness_status"] == "ready"
    assert context.data["ready_connector_services"] == ["analytics_provider"]

    request_payload = ui_calls[0]["payload"]
    integration_request = request_payload["integration_requests"][0]
    assert request_payload["event_type"] == "integration.required"
    assert integration_request["integration_id"] == "analytics_provider"
    assert integration_request["provider"] == "managed_analytics"
    assert integration_request["purpose"] == "Send event analytics to an external analytics API."
    assert integration_request["secret_fields"][0]["name"] == "api_key"
    assert [field["name"] for field in integration_request["non_secret_fields"]] == [
        "endpoint_url",
        "workspace_id",
    ]
    assert integration_request["permissions_required"] == ["integrations.manage"]
    assert integration_request["resume"]["workflow_name"] == "AppGenerator"
    assert integration_request["resume"]["run_id"] == "run-analytics-smoke"
    assert integration_request["resume"]["step_id"] == "task_analytics_adapter"

    connector = await connector_store.get(scope=ConnectorStore.SCOPE_APP, scope_id="app-analytics-smoke", service="analytics_provider")
    assert connector is not None
    assert connector["secret_available"] is True
    assert connector["public_config"] == {
        "endpoint_url": "https://analytics.example.test/events",
        "workspace_id": "workspace-neutral",
    }
    assert vault.secrets[("app-analytics-smoke", "analytics_provider")] == SECRET_VALUE

    assert SECRET_VALUE not in repr(request_payload)
    assert SECRET_VALUE not in repr(result)
    assert SECRET_VALUE not in repr(connector)


def test_generated_output_references_connector_id_without_raw_secret() -> None:
    generated_files = [
        {
            "filename": "modules/analytics/services/integrations/analytics_provider_client.py",
            "content": (
                "CONNECTOR_ID = 'analytics_provider'\n"
                "def build_client(connector_config):\n"
                "    return {'connector_id': CONNECTOR_ID, 'endpoint_url': connector_config.public_config['endpoint_url']}\n"
            ),
        },
        {
            "filename": "modules/analytics/module.yaml",
            "content": "capabilities:\n  - external_analytics\nintegration_refs:\n  - analytics_provider\n",
        },
    ]

    combined = "\n".join(file["content"] for file in generated_files)

    assert "analytics_provider" in combined
    assert SECRET_VALUE not in combined
    assert "api_key =" not in combined
    assert "secret-" not in combined


@pytest.mark.asyncio
async def test_studio_connector_read_response_redacts_secret_shaped_fields(monkeypatch) -> None:
    from mozaiksai.hosts import studio

    async def fake_list_connectors(*, scope, scope_id, store=None):
        assert scope_id == "app-analytics-smoke"
        return [
            {
                "scope_id": scope_id,
                "service": "analytics_provider",
                "secret_available": True,
                "secret_value": SECRET_VALUE,
                "public_config": {
                    "endpoint_url": "https://analytics.example.test/events",
                    "api_key": SECRET_VALUE,
                    "nested": {"token": SECRET_VALUE, "workspace_id": "workspace-neutral"},
                },
            }
        ]

    monkeypatch.setattr(studio, "_resolve_studio_scope", lambda principal, app_id=None: (app_id, "user-operator"))
    monkeypatch.setattr(studio, "list_connectors", fake_list_connectors)

    response = await studio.get_integration_connectors(app_id="app-analytics-smoke", principal=object())

    assert response["connectors"][0]["public_config"]["endpoint_url"] == "https://analytics.example.test/events"
    assert response["connectors"][0]["public_config"]["nested"]["workspace_id"] == "workspace-neutral"
    assert response["connectors"][0]["health"]["status"] == "configured"
    assert response["connectors"][0]["health"]["frontend_safe"] is True
    assert "api_key" not in response["connectors"][0]["public_config"]
    assert "token" not in response["connectors"][0]["public_config"]["nested"]
    assert SECRET_VALUE not in repr(response)


def test_connector_metadata_redacts_misclassified_public_config_secret() -> None:
    store = ConnectorStore(pm=_FakePersistenceManager())

    connector = asyncio.run(_upsert_misclassified_connector(store))

    assert connector["public_config"] == {
        "endpoint_url": "https://analytics.example.test/events",
        "nested": {"workspace_id": "workspace-neutral"},
    }
    assert SECRET_VALUE not in repr(connector)


async def _upsert_misclassified_connector(store: ConnectorStore) -> dict[str, Any]:
    return await store.upsert(
        scope=ConnectorStore.SCOPE_APP,
        scope_id="app-analytics-smoke",
        service="analytics_provider",
        display_name="Hosted Analytics",
        user_id="user-operator",
        status="metadata_only",
        secret_storage="unmanaged",
        secret_available=False,
        public_config={
            "endpoint_url": "https://analytics.example.test/events",
            "api_key": SECRET_VALUE,
            "nested": {"token": SECRET_VALUE, "workspace_id": "workspace-neutral"},
        },
    )

