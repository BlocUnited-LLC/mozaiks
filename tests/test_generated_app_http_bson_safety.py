"""Real-HTTP proof: Mongo-backed module CRUD through the packaged platform host.

Boots the actual platform app over a generated-style bundle whose module repo
uses ``ctx.persistence`` (documents get driver-generated ObjectId ``_id``) and
drives create → list → read → update → delete over HTTP against real local
Mongo. Every response must be valid JSON with stable string identifiers and no
bare HTTP 500 — the exact defect shape this boundary previously produced.
"""
from __future__ import annotations

import json
import os

import pytest
from fastapi.testclient import TestClient

from mozaiksai.core.auth.adapters.registry import reset_auth_adapter
from tests.test_generated_app_functional_acceptance import (
    _basic_crud_files,
    _materialize_bundle,
)

pytestmark = pytest.mark.skipif(
    os.environ.get("MOZAIKS_RUN_REAL_MONGO_TESTS") != "1",
    reason="set MOZAIKS_RUN_REAL_MONGO_TESTS=1 for real-Mongo HTTP BSON-safety tests",
)

_MONGO_URI = os.environ.get("MONGO_URI", "mongodb://127.0.0.1:27017")

_PERSISTENCE_HANDLER = '''
class OrdersBaseHandler:
    def _collection(self, ctx):
        return ctx.persistence.collection("orders", "orders")

    async def list_orders(self, ctx, **params):
        items = await self._collection(ctx).find_many({}, limit=50)
        return {"orders": items, "count": len(items)}

    async def create_order(self, ctx, **params):
        import uuid as _uuid

        order = {"order_id": str(_uuid.uuid4()), "customer_name": params.get("customer_name", "")}
        await self._collection(ctx).insert_one(order)
        return {"order": {"order_id": order["order_id"]}}

    async def read_order(self, ctx, **params):
        return {"order": await self._collection(ctx).find_one({"order_id": params.get("order_id", "")})}

    async def update_order(self, ctx, **params):
        collection = self._collection(ctx)
        await collection.update_one(
            {"order_id": params.get("order_id", "")},
            {"$set": {"customer_name": params.get("customer_name", "")}},
        )
        return {"order": await collection.find_one({"order_id": params.get("order_id", "")})}

    async def archive_order(self, ctx, **params):
        deleted = await self._collection(ctx).delete_one({"order_id": params.get("order_id", "")})
        return {"archived": bool(deleted)}

    async def corrupt_order(self, ctx, **params):
        return {"handle": {"a", "b"}}
'''

_PROFILE_YAML = """
schema_version: mozaiks.profile.v1
panels:
  - id: orders_panel
    title: Orders
    kind: list
    action: list_orders
    fields:
      - id: order_id
        label: Order
      - id: customer_name
        label: Customer
"""

_MODULE_YAML = """
schema_version: mozaiks.module.v1
module:
  id: orders
  display_name: Orders
  version: 1.0.0
  handler: backend.handler:OrdersHandler
actions:
  - id: list_orders
    description: List orders.
    handler_method: list_orders
    input_schema: {type: object, properties: {}}
    output_schema: {type: object}
  - id: create_order
    description: Create an order.
    handler_method: create_order
    input_schema: {type: object, properties: {customer_name: {type: string}}}
    output_schema: {type: object}
  - id: read_order
    description: Read one order.
    handler_method: read_order
    input_schema: {type: object, properties: {order_id: {type: string}}}
    output_schema: {type: object}
  - id: update_order
    description: Update one order.
    handler_method: update_order
    input_schema: {type: object, properties: {order_id: {type: string}, customer_name: {type: string}}}
    output_schema: {type: object}
  - id: archive_order
    description: Delete one order.
    handler_method: archive_order
    input_schema: {type: object, properties: {order_id: {type: string}}}
    output_schema: {type: object}
  - id: corrupt_order
    description: Return a value outside the JSON transport contract.
    handler_method: corrupt_order
    input_schema: {type: object, properties: {}}
    output_schema: {type: object}
"""


def _persistence_bundle() -> dict[str, str]:
    files = _basic_crud_files()
    files = {
        path: content
        for path, content in files.items()
        if not path.startswith("modules/orders/")
    }
    files["modules/orders/module.yaml"] = _MODULE_YAML
    files["modules/orders/contracts/profile.yaml"] = _PROFILE_YAML
    files["modules/orders/backend/__init__.py"] = ""
    files["modules/orders/backend/base_handler.py"] = _PERSISTENCE_HANDLER
    files["modules/orders/backend/handler.py"] = (
        "from .base_handler import OrdersBaseHandler\n\n\n"
        "class OrdersHandler(OrdersBaseHandler):\n    pass\n"
    )
    return files


def _assert_json_200(response, surface: str) -> dict:
    assert response.status_code == 200, f"{surface}: {response.status_code} {response.text[:300]}"
    payload = json.loads(response.text)
    return payload


def test_persistence_backed_crud_over_http_is_json_safe(tmp_path, monkeypatch) -> None:
    app_root = tmp_path / "app"
    _materialize_bundle(app_root, _persistence_bundle())
    monkeypatch.setenv("PLATFORM_PATH", str(app_root))
    monkeypatch.setenv("AUTH_ENABLED", "false")
    monkeypatch.setenv("RATE_LIMIT_ENABLED", "false")
    monkeypatch.setenv("MOZAIKS_DATABASE_STARTUP_POLICY", "best_effort")
    monkeypatch.setenv("MONGO_URI", _MONGO_URI)
    reset_auth_adapter()

    from mozaiksai.core.runtime.persistence.mongo import MongoPersistenceContext

    probe = MongoPersistenceContext(app_id="support-operations")
    collection_name = probe.collection_name("orders", "orders")
    database_name = probe.database_name

    from mozaiksai.hosts import platform

    try:
        with TestClient(platform.app, raise_server_exceptions=False) as client:
            created = _assert_json_200(
                client.post(
                    "/api/modules/orders/create_order?app_id=support-operations",
                    json={"params": {"customer_name": "Ada"}},
                ),
                "create_order",
            )
            order_id = created["order"]["order_id"]
            assert order_id

            listed = _assert_json_200(
                client.post("/api/modules/orders/list_orders?app_id=support-operations", json={}),
                "list_orders",
            )
            assert listed["count"] >= 1
            first = next(o for o in listed["orders"] if o["order_id"] == order_id)
            # The driver-generated ObjectId reaches the wire as a stable string.
            assert isinstance(first["_id"], str) and len(first["_id"]) == 24

            read = _assert_json_200(
                client.post(
                    "/api/modules/orders/read_order?app_id=support-operations",
                    json={"params": {"order_id": order_id}},
                ),
                "read_order",
            )
            assert isinstance(read["order"]["_id"], str)
            assert read["order"]["customer_name"] == "Ada"

            updated = _assert_json_200(
                client.post(
                    "/api/modules/orders/update_order?app_id=support-operations",
                    json={"params": {"order_id": order_id, "customer_name": "Grace"}},
                ),
                "update_order",
            )
            assert updated["order"]["customer_name"] == "Grace"
            assert isinstance(updated["order"]["_id"], str)

            # GET module dispatch surface as an additional embedding path.
            listed_get = _assert_json_200(
                client.get("/api/modules/orders/list_orders?app_id=support-operations"),
                "list_orders (GET)",
            )
            assert any(isinstance(o.get("_id"), str) for o in listed_get["orders"])

            # Profile-panel embedding surface: the platform hydrates the
            # module action into the panel payload; the Mongo documents must
            # arrive as valid JSON with string identifiers, no crash.
            panels = _assert_json_200(
                client.get("/api/me/profile-panels"),
                "profile-panels",
            )
            orders_panel = next(
                p for p in panels["panels"] if p["id"] == "orders_panel"
            )
            assert orders_panel["error"] is None, orders_panel
            hydrated_orders = orders_panel["data"]["orders"]
            assert any(
                o["order_id"] == order_id and isinstance(o["_id"], str)
                for o in hydrated_orders
            ), f"order {order_id} missing from panel data: {hydrated_orders!r}"

            # A result outside the transport contract fails typed at the
            # executor and reaches the wire as the platform's controlled
            # JSON error envelope (the host masks 500 details by policy).
            # An unhandled FastAPI serialization crash would instead produce
            # Starlette's plain-text "Internal Server Error" body — so a
            # parseable JSON envelope here proves the typed path handled it.
            corrupt_response = client.post(
                "/api/modules/orders/corrupt_order?app_id=support-operations", json={}
            )
            assert corrupt_response.status_code == 500
            corrupt_payload = json.loads(corrupt_response.text)
            assert corrupt_payload == {"detail": "Internal server error"}

            # The server survives the invalid result: the next request works.
            still_alive = _assert_json_200(
                client.get("/api/modules/orders/list_orders?app_id=support-operations"),
                "list_orders (after corrupt)",
            )
            assert still_alive["count"] >= 1

            archived = _assert_json_200(
                client.post(
                    "/api/modules/orders/archive_order?app_id=support-operations",
                    json={"params": {"order_id": order_id}},
                ),
                "archive_order",
            )
            assert archived["archived"] is True
    finally:
        import asyncio

        from motor.motor_asyncio import AsyncIOMotorClient

        async def _cleanup() -> None:
            cleaner = AsyncIOMotorClient(_MONGO_URI)
            try:
                await cleaner[database_name].drop_collection(collection_name)
            finally:
                cleaner.close()

        asyncio.run(_cleanup())
