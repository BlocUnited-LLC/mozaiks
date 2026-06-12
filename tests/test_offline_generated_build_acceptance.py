from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from factory_app.workflows.AppGenerator.tools.app_validation import (
    run_app_bundle_acceptance_gate,
    validate_app_bundle_from_request,
)
from factory_app.workflows.AppGenerator.tools.export_app_code import resolve_export_gate
from factory_app.workflows.AppGenerator.tools.generated_bundle_scanner import scan_generated_bundle
from mozaiksai.core.runtime.app.loader import AppLoader


class _Context:
    def __init__(self, initial: dict[str, Any]) -> None:
        self.data = dict(initial)

    def get(self, key: str, default: Any = None) -> Any:
        return self.data.get(key, default)

    def set(self, key: str, value: Any) -> None:
        self.data[key] = value


def _generated_build_files() -> dict[str, str]:
    data_contract = {
        "version": "1",
        "app_id": "support-operations",
        "surfaces": [
            {
                "surface_id": "orders",
                "surface_kind": "module",
                "collections": [
                    {
                        "name": "orders",
                        "ownership": {
                            "surface_id": "orders",
                            "surface_kind": "module",
                        },
                    }
                ],
            }
        ],
        "shared_collections": [],
    }

    return {
        "app.json": json.dumps(
            {
                "appId": "support-operations",
                "appName": "Support Operations",
                "version": "1.0.0",
                "startup": {"landing_spot": "/orders"},
            }
        ),
        "config/ai.json": json.dumps(
            {
                "chat": {"chat_startup_mode": "ask"},
                "workflows": {"entry_point": None, "resume_policy": "manual"},
            }
        ),
        "config/shell.json": json.dumps(
            {
                "navigation": {"autoFromPages": True},
                "header": {"show": True},
            }
        ),
        "ui/route_manifest.json": json.dumps(
            {
                "pages": [
                    {
                        "path": "/orders",
                        "component": "SchemaPage",
                        "label": "Orders",
                        "order": 10,
                        "schema": "orders",
                        "navigation": {"group": "main"},
                        "meta": {"title": "Orders"},
                    }
                ]
            }
        ),
        "ui/pages/orders.yaml": """
name: Orders
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
        "data/contract.json": json.dumps(data_contract),
        "security/secrets.yaml": """
version: 1
secrets:
  - name: ORDERS_WEBHOOK_SECRET
    env: ORDERS_WEBHOOK_SECRET
    required: false
""",
        "modules/orders/module.yaml": """
schema_version: mozaiks.module.v1
module:
  id: orders
  display_name: Orders
  version: 1.0.0
  handler: backend.handler:OrdersModule
actions:
  - id: list_orders
    description: List orders visible to the current workspace.
    handler_method: list_orders
    input_schema:
      type: object
      properties: {}
    output_schema:
      type: object
  - id: create_order
    description: Create an order record.
    handler_method: create_order
    input_schema:
      type: object
      required:
        - customer_name
        - status
      properties:
        customer_name:
          type: string
        status:
          type: string
    output_schema:
      type: object
capabilities:
  - capability_id: orders.list
    kind: action
    target: list_orders
    title: List orders
  - capability_id: orders.create
    kind: action
    target: create_order
    title: Create order
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
        records = await OrdersRepo(ctx).list_orders()
        return {"orders": records}

    async def create_order(self, ctx, **params):
        order = {
            "customer_name": params.get("customer_name"),
            "status": params.get("status"),
        }
        created = await OrdersRepo(ctx).create_order(order)
        return {"order": created}


class OrdersRepo:
    def __init__(self, ctx):
        self.ctx = ctx

    async def list_orders(self):
        persistence = getattr(self.ctx, "persistence", None)
        if persistence is None:
            return []
        collection = persistence.collection("orders", "orders")
        return await collection.find({}).to_list(length=100)

    async def create_order(self, order):
        persistence = getattr(self.ctx, "persistence", None)
        if persistence is None:
            return order
        collection = persistence.collection("orders", "orders")
        result = await collection.insert_one(order)
        return {**order, "id": str(result.inserted_id)}
""",
    }


def _write_files(root: Path, files: dict[str, str]) -> None:
    for rel_path, content in files.items():
        path = root / rel_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")


@pytest.mark.asyncio
async def test_offline_generated_build_acceptance_gate_loads_runtime_app(tmp_path: Path) -> None:
    """Offline generated-build promotion gate.

    This exercises the deterministic acceptance path without OpenAI, AG2 model
    calls, npm, Docker, MongoDB, or HTTP.
    """

    files = _generated_build_files()
    context = _Context(
        {
            "workflow_name": "AppGenerator",
            "app_id": "support-operations",
            "chat_id": "offline-build-acceptance",
            "generated_files": files,
            "app_build_plan": {
                "capability_packs": [
                    {
                        "module_id": "orders",
                        "actions": ["list_orders", "create_order"],
                    }
                ]
            },
        }
    )

    scan_errors = scan_generated_bundle(files)
    assert scan_errors == []

    validation = await validate_app_bundle_from_request(
        {
            "validation_strategy": "skip",
            "start_dev_server": False,
        },
        context_variables=context,
    )

    assert validation["status"] == "success"
    assert validation["app_bundle_acceptance_result"]["status"] == "passed"
    assert validation["integration_tests_passed"] is True
    assert context.get("app_bundle_acceptance_status") == "passed"
    assert context.get("app_bundle_validation_evidence")["failed"] == []
    assert context.get("integration_tests_passed") is True
    assert context.get("bundle_scan_result")["passed"] is True
    assert context.get("wiring_validation_result")["passed"] is True
    assert context.get("module_implementation_validation_result")["passed"] is True
    assert context.get("module_runtime_quality_result")["passed"] is True

    app_root = tmp_path / "app"
    _write_files(app_root, files)

    loaded = await AppLoader.load(str(app_root))

    assert loaded.definition.name == "Support Operations"
    assert [module.name for module in loaded.modules] == ["orders"]
    assert [page.name for page in loaded.definition.pages] == ["orders"]
    assert loaded.data_entities_by_key[("orders", "orders")]["name"] == "orders"


@pytest.mark.asyncio
async def test_offline_generated_build_acceptance_blocks_unwired_page_endpoint() -> None:
    files = _generated_build_files()
    files["ui/pages/orders.yaml"] = files["ui/pages/orders.yaml"].replace(
        "/api/modules/orders/create_order",
        "/api/modules/orders/missing_action",
    )
    context = _Context(
        {
            "app_id": "support-operations",
            "chat_id": "offline-build-acceptance",
            "generated_files": files,
            "app_validation_status": "skipped",
            "app_validation_strategy_used": "skip",
            "app_build_plan": {
                "capability_packs": [
                    {
                        "module_id": "orders",
                        "actions": ["list_orders", "create_order"],
                    }
                ]
            },
        }
    )

    acceptance = await run_app_bundle_acceptance_gate(
        files=files,
        context_variables=context,
    )
    gate = resolve_export_gate(context)

    assert acceptance["status"] == "failed"
    assert "module_wiring" in acceptance["validation_evidence"]["failed"]
    assert context.get("app_bundle_acceptance_status") == "failed"
    assert gate["allow_export"] is False
    assert "App bundle acceptance failed." in gate["reasons"]
