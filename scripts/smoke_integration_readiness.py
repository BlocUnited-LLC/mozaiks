from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

# ruff: noqa: I001

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from mozaiksai.core.workflow.generator_support import connector_request, connector_service
from mozaiksai.core.data.persistence.connector_store import ConnectorStore

SECRET_VALUE = "test-secret-value"


class Context:
    def __init__(self, data: dict[str, Any]):
        self.data = dict(data)

    def get(self, key: str, default=None):
        return self.data.get(key, default)

    def set(self, key: str, value):
        self.data[key] = value


class FakeCursor:
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


class FakeCollection:
    def __init__(self) -> None:
        self.docs = []
        self.indexes = []
        self._next_id = 1

    def list_indexes(self):
        return FakeCursor([kwargs | {"name": kwargs.get("name")} for _keys, kwargs in self.indexes])

    async def create_index(self, keys, **kwargs):
        self.indexes.append((list(keys), dict(kwargs)))

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
        return FakeCursor(
            [
                dict(existing)
                for existing in self.docs
                if all(existing.get(key) == value for key, value in query.items())
            ]
        )


class FakeDatabase:
    def __init__(self) -> None:
        self.collections = {}

    def __getitem__(self, name):
        self.collections.setdefault(name, FakeCollection())
        return self.collections[name]


class FakeClient:
    def __init__(self) -> None:
        self.databases = {}

    def __getitem__(self, name):
        self.databases.setdefault(name, FakeDatabase())
        return self.databases[name]


class FakePersistence:
    def __init__(self) -> None:
        self.client = FakeClient()

    async def _ensure_client(self) -> None:
        return None


class FakePersistenceManager:
    def __init__(self) -> None:
        self.persistence = FakePersistence()


class FakeVaultBackend:
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


def analytics_required_fields() -> list[dict[str, Any]]:
    return [
        {"name": "api_key", "label": "API Key", "type": "secret", "required": True, "frontend_safe": False},
        {"name": "endpoint_url", "label": "Endpoint URL", "type": "url", "required": True, "frontend_safe": True},
        {"name": "workspace_id", "label": "Workspace ID", "type": "text", "required": False, "frontend_safe": True},
    ]


def analytics_plan() -> dict[str, Any]:
    fields = analytics_required_fields()
    return {
        "app_kind": "analytics-reporting",
        "external_integrations": [
            {
                "name": "Example Analytics",
                "service": "analytics_provider",
                "provider": "managed_analytics",
                "kind": "api_key",
                "purpose": "Send usage events to an external analytics API.",
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


def redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: redact(item)
            for key, item in value.items()
            if str(key).strip().lower() not in {"secret_value", "secret", "api_key", "apikey", "token", "password"}
        }
    if isinstance(value, list):
        return [redact(item) for item in value]
    if value == SECRET_VALUE:
        return "<redacted>"
    return value


async def run_smoke() -> dict[str, Any]:
    connector_store = ConnectorStore(pm=FakePersistenceManager())
    vault = FakeVaultBackend()
    ui_events: list[dict[str, Any]] = []

    original_inventory = connector_request.get_connector_inventory
    original_save_connector_draft = connector_request.save_connector_draft
    original_save_connector = connector_request.save_connector
    original_use_ui_tool = connector_request.use_ui_tool
    original_vault_backend = connector_service.get_connector_vault_backend

    async def inventory(*, scope, scope_id, required_services=None, store=None):
        del store
        return await connector_service.get_connector_inventory(
            scope=scope,
            scope_id=scope_id,
            required_services=required_services,
            store=connector_store,
        )

    async def patched_save_connector_draft(**kwargs):
        return await connector_service.save_connector_draft(
            **kwargs,
            store=connector_store,
        )

    async def patched_save_connector(**kwargs):
        return await connector_service.save_connector(
            **kwargs,
            store=connector_store,
        )

    async def fake_use_ui_tool(component, payload, *, chat_id=None, workflow_name=None):
        ui_events.append(
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
                            "endpoint_url": "https://analytics.example.test",
                            "workspace_id": "demo-workspace",
                        },
                    }
                ],
            },
        }

    try:
        connector_request.get_connector_inventory = inventory
        connector_request.save_connector_draft = patched_save_connector_draft
        connector_request.save_connector = patched_save_connector
        connector_request.use_ui_tool = fake_use_ui_tool
        connector_service.get_connector_vault_backend = lambda: vault

        context = Context(
            {
                "workflow_name": "AppGenerator",
                "run_id": "run-integration-readiness-smoke",
                "chat_id": "chat-integration-readiness-smoke",
                "app_id": "app-integration-readiness-smoke",
                "user_id": "user-operator",
                "current_build_task_id": "task_analytics_adapter",
                "app_build_plan": analytics_plan(),
            }
        )

        blocked = await connector_request.collect_missing_connector_needs(context_variables=context, prompt=False)
        ready = await connector_request.collect_missing_connector_needs(context_variables=context, prompt=True)
        connector = await connector_store.get(
            scope=ConnectorStore.SCOPE_APP,
            scope_id="app-integration-readiness-smoke",
            service="analytics_provider",
        )
        generated_output = (
            "CONNECTOR_ID = 'analytics_provider'\n"
            "def build_client(connector_config):\n"
            "    return {'endpoint_url': connector_config.public_config['endpoint_url']}\n"
        )

        assertions = {
            "blocked_before_prompt": blocked.get("status") == "blocked"
            and blocked.get("unresolved_required_services") == ["analytics_provider"],
            "emitted_integration_required": bool(ui_events)
            and ui_events[0]["payload"].get("event_type") == "integration.required",
            "readiness_ready_after_submit": ready.get("status") == "ready"
            and ready.get("unresolved_required_services") == [],
            "connector_secret_available": bool((connector or {}).get("secret_available")),
            "public_config_saved": (connector or {}).get("public_config")
            == {
                "endpoint_url": "https://analytics.example.test",
                "workspace_id": "demo-workspace",
            },
            "vault_received_secret": vault.secrets.get(
                ("app-integration-readiness-smoke", "analytics_provider")
            )
            == SECRET_VALUE,
            "generated_output_safe": SECRET_VALUE not in generated_output and "api_key" not in generated_output,
        }

        safe_payload = {
            "scenario": "analytics_provider",
            "success": all(assertions.values()),
            "assertions": assertions,
            "blocked_status": blocked.get("status"),
            "ready_status": ready.get("status"),
            "ui_event_count": len(ui_events),
            "first_request": redact(ui_events[0]["payload"]["integration_requests"][0]) if ui_events else None,
            "connector": redact(connector),
            "generated_output": generated_output,
        }
        rendered = json.dumps(safe_payload, sort_keys=True)
        if SECRET_VALUE in rendered:
            raise RuntimeError("Smoke output leaked the submitted secret")
        return safe_payload
    finally:
        connector_request.get_connector_inventory = original_inventory
        connector_request.save_connector_draft = original_save_connector_draft
        connector_request.save_connector = original_save_connector
        connector_request.use_ui_tool = original_use_ui_tool
        connector_service.get_connector_vault_backend = original_vault_backend


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the integration-readiness workflow smoke.")
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON only.")
    args = parser.parse_args(argv)

    result = asyncio.run(run_smoke())
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        print("Integration readiness smoke:", "PASS" if result["success"] else "FAIL")
        print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
