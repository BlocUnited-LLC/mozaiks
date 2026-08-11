from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from factory_app.workflows.AppGenerator.tools.app_validation import (
    run_app_bundle_acceptance_gate,
)
from mozaiksai.core.auth.adapters.registry import reset_auth_adapter
from mozaiksai.core.validation import (
    GeneratedAppValidationRequest,
    scan_functional_generated_app,
    validate_generated_app_bundle,
)


def _basic_crud_files() -> dict[str, str]:
    return {
        "app.json": json.dumps(
            {
                "appId": "support-operations",
                "appName": "Support Operations",
                "version": "1.0.0",
                "startup": {"landing_spot": "/orders"},
            }
        ),
        "config/ai.json": json.dumps({"chat": {"chat_startup_mode": "ask"}}),
        "config/shell.json": json.dumps({"navigation": {"autoFromPages": True}}),
        "ui/route_manifest.json": json.dumps(
            {
                "pages": [
                    {
                        "path": "/orders",
                        "component": "SchemaPage",
                        "label": "Orders",
                        "schema": "orders",
                    }
                ]
            }
        ),
        "ui/pages/orders.yaml": """
name: orders
route: /orders
title: Orders
sections:
  - id: orders-list
    type: record_list
    config:
      api_endpoint: /api/modules/orders/list_orders
  - id: create-order
    type: form
    config:
      submit_action:
        api_endpoint: /api/modules/orders/create_order
""",
        "data/contract.json": json.dumps(
            {
                "version": "1",
                "app_id": "support-operations",
                "surfaces": [
                    {
                        "surface_id": "orders",
                        "surface_kind": "module",
                        "collections": [{"name": "orders"}],
                    }
                ],
            }
        ),
        "security/secrets.yaml": "version: 1\nsecrets: []\n",
        "modules/orders/module.yaml": """
schema_version: mozaiks.module.v1
module:
  id: orders
  display_name: Orders
  version: 1.0.0
  handler: backend.handler:OrdersModule
actions:
  - id: list_orders
    description: List orders.
    handler_method: list_orders
    input_schema: {type: object, properties: {}}
    output_schema: {type: object}
  - id: create_order
    description: Create an order.
    handler_method: create_order
    input_schema:
      type: object
      required: [customer_name]
      properties:
        customer_name: {type: string}
    output_schema: {type: object}
""",
        "modules/orders/backend/__init__.py": "",
        "modules/orders/backend/handler.py": """
from .service import OrdersService


class OrdersModule:
    def __init__(self):
        self.service = OrdersService()

    async def list_orders(self, ctx, **params):
        return await self.service.list_orders(ctx, **params)

    async def create_order(self, ctx, **params):
        return await self.service.create_order(ctx, **params)
""",
        "modules/orders/backend/service.py": """
class OrdersService:
    async def list_orders(self, ctx, **params):
        return {"orders": []}

    async def create_order(self, ctx, **params):
        return {"order": {"customer_name": params.get("customer_name")}}
""",
    }


def _diagnostic_codes(files: dict[str, str], *, capability_packs: list[dict] | None = None) -> set[str]:
    return {item.code for item in scan_functional_generated_app(files, capability_packs=capability_packs)}


def _materialize_bundle(root: Path, files: dict[str, str]) -> None:
    for relative_path, content in files.items():
        path = root / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")


class _FakeMongoAdmin:
    async def command(self, *_args: Any, **_kwargs: Any) -> dict[str, str]:
        return {"ok": "1"}


class _FakeMongoClient:
    def __init__(self) -> None:
        self.admin = _FakeMongoAdmin()

    def __getitem__(self, name: str) -> _FakeMongoClient:  # noqa: ARG002
        return self

    def __getattr__(self, name: str) -> _FakeMongoClient:  # noqa: ARG002
        return self

    async def command(self, *_args: Any, **_kwargs: Any) -> dict[str, str]:
        return {"ok": "1"}

    async def close(self) -> None:
        return None


def _assert_not_missing_or_placeholder(response, *, surface: str) -> None:
    body = response.text.lower()
    assert response.status_code != 404, f"DECLARED_SURFACE_404 surface={surface} body={response.text}"
    assert response.status_code != 501, f"DECLARED_SURFACE_501 surface={surface} body={response.text}"
    assert "not implemented" not in body, f"DECLARED_SURFACE_PLACEHOLDER surface={surface} body={response.text}"
    assert "not_implemented" not in body, f"DECLARED_SURFACE_PLACEHOLDER surface={surface} body={response.text}"


def test_functional_scanner_accepts_basic_authenticated_crud_bundle() -> None:
    assert scan_functional_generated_app(_basic_crud_files()) == []


def test_public_generated_app_validation_runs_functional_completeness() -> None:
    files = _basic_crud_files()
    files["ui/route_manifest.json"] = json.dumps(
        {"pages": [{"path": "/missing", "component": "MissingPage"}]}
    )

    result = validate_generated_app_bundle(GeneratedAppValidationRequest(files=files))

    assert result.passed is False
    assert any(item.code == "MISSING_ROUTE_COMPONENT" for item in result.diagnostics)


def test_schema_route_requires_matching_declarative_page_schema() -> None:
    files = _basic_crud_files()
    files["ui/route_manifest.json"] = json.dumps(
        {"pages": [{"path": "/orders", "component": "SchemaPage", "schema": "missing"}]}
    )

    assert "MISSING_SCHEMA_PAGE" in _diagnostic_codes(files)


def test_page_module_endpoint_must_resolve_to_declared_action() -> None:
    files = _basic_crud_files()
    files["ui/pages/orders.yaml"] = files["ui/pages/orders.yaml"].replace(
        "/api/modules/orders/create_order",
        "/api/modules/orders/archive_order",
    )

    assert "MISSING_MODULE_ACTION" in _diagnostic_codes(files)


def test_declared_module_action_requires_implemented_handler_method() -> None:
    files = _basic_crud_files()
    files["modules/orders/module.yaml"] = files["modules/orders/module.yaml"].replace(
        "handler_method: create_order",
        "handler_method: archive_order",
    )

    assert "MISSING_MODULE_ACTION" in _diagnostic_codes(files)


def test_workflow_module_action_reference_must_resolve() -> None:
    files = _basic_crud_files()
    files["workflows/OrderWorkflow/tools.yaml"] = """
tools:
  - id: archive
    module_id: orders
    action_id: archive_order
"""

    assert "MISSING_MODULE_ACTION" in _diagnostic_codes(files)


def test_placeholder_implementation_is_blocking() -> None:
    files = _basic_crud_files()
    files["modules/orders/backend/service.py"] += """

async def legacy_placeholder():
    raise NotImplementedError("not ready")
"""

    assert "PLACEHOLDER_IMPLEMENTATION" in _diagnostic_codes(files)


def test_mozaikspay_selected_capability_requires_public_facade_files() -> None:
    files = _basic_crud_files()

    codes = _diagnostic_codes(files, capability_packs=[{"id": "mozaikspay"}])

    assert "CAPABILITY_FACADE_MISSING" in codes


def test_mozaikspay_facade_supports_inherited_base_handler_methods() -> None:
    files = _basic_crud_files()
    files.update(
        {
            "services/integrations/mozaikspay_client.py": "class MozaiksPayClient: pass\n",
            "modules/billing_portal/module.yaml": """
schema_version: mozaiks.module.v1
module:
  id: billing_portal
  display_name: Billing Portal
  version: 1.0.0
  handler: backend.handler:BillingPortalHandler
actions:
  - id: list_plans
    description: List plans.
    handler_method: list_plans
    input_schema: {type: object, properties: {}}
    output_schema: {type: object}
  - id: get_subscription_status
    description: Get subscription status.
    handler_method: get_subscription_status
    input_schema: {type: object, properties: {}}
    output_schema: {type: object}
  - id: open_billing_portal
    description: Open billing portal.
    handler_method: open_billing_portal
    input_schema: {type: object, properties: {}}
    output_schema: {type: object}
""",
            "modules/billing_portal/backend/__init__.py": "",
            "modules/billing_portal/backend/base_handler.py": """
class BillingPortalBaseHandler:
    async def list_plans(self, ctx, **params):
        return {"plans": []}

    async def get_subscription_status(self, ctx, **params):
        return {"found": False}

    async def open_billing_portal(self, ctx, **params):
        return {"portal_url": None}
""",
            "modules/billing_portal/backend/handler.py": """
from .base_handler import BillingPortalBaseHandler


class BillingPortalHandler(BillingPortalBaseHandler):
    pass
""",
        }
    )

    assert scan_functional_generated_app(files, capability_packs=[{"id": "mozaikspay"}]) == []


@pytest.mark.asyncio
async def test_app_generator_acceptance_gate_includes_functional_completeness() -> None:
    result = await run_app_bundle_acceptance_gate(files=_basic_crud_files())

    assert result["passed"] is True
    assert result["functional_completeness"]["passed"] is True
    assert "functional_completeness" in result["validation_evidence"]["completed"]


def test_generated_crud_bundle_boots_and_serves_declared_http_surfaces(tmp_path, monkeypatch) -> None:
    app_root = tmp_path / "app"
    _materialize_bundle(app_root, _basic_crud_files())
    fake_mongo_client = _FakeMongoClient()
    monkeypatch.setenv("PLATFORM_PATH", str(app_root))
    monkeypatch.setenv("AUTH_ENABLED", "false")
    monkeypatch.setenv("RATE_LIMIT_ENABLED", "false")
    monkeypatch.setenv("MOZAIKS_DATABASE_STARTUP_POLICY", "best_effort")
    monkeypatch.setattr("mozaiksai.hosts.runtime.get_mongo_client", lambda: fake_mongo_client)
    monkeypatch.setattr("mozaiksai.core.startup.validation.get_mongo_client", lambda: fake_mongo_client)
    reset_auth_adapter()

    from mozaiksai.hosts import platform

    with TestClient(platform.app, raise_server_exceptions=False) as client:
        health = client.get("/health")
        assert health.status_code == 200, health.text
        assert health.json()["status"] == "ok"

        shell_config = client.get("/api/shell-config")
        _assert_not_missing_or_placeholder(shell_config, surface="/api/shell-config")
        assert shell_config.status_code == 200, shell_config.text
        pages = shell_config.json().get("pages", [])
        assert any(page.get("path") == "/orders" for page in pages)

        page_schema = client.get("/api/pages/orders")
        _assert_not_missing_or_placeholder(page_schema, surface="/api/pages/orders")
        assert page_schema.status_code == 200, page_schema.text
        assert page_schema.json()["route"] == "/orders"

        list_response = client.post("/api/modules/orders/list_orders", json={})
        _assert_not_missing_or_placeholder(list_response, surface="/api/modules/orders/list_orders")
        assert list_response.status_code == 200, list_response.text
        assert list_response.json() == {"orders": []}

        create_response = client.post(
            "/api/modules/orders/create_order",
            json={"params": {"customer_name": "Ada"}},
        )
        _assert_not_missing_or_placeholder(create_response, surface="/api/modules/orders/create_order")
        assert create_response.status_code == 200, create_response.text
        assert create_response.json() == {"order": {"customer_name": "Ada"}}
