"""
Docker-backed integration test: self-hosted SaaS entitlement dispatch.

Tests the full runtime flow end-to-end against a real MongoDB instance:

  1. Load a SaaS app bundle (subscriptions.yaml + reports module with entitlement_gate)
  2. Call entitlement_dispatch.activate_subscription → writes assignment to MongoDB
  3. ConfiguredEntitlementAdapter reads the assignment from MongoDB
  4. reports.export_report is dispatched → succeeds (GRANTED)
  5. Deactivate subscription
  6. reports.export_report is dispatched → ENTITLEMENT_REQUIRED

Requires:
  - MOZAIKS_RUN_REAL_MONGO_TESTS=1
  - MONGO_URI pointing at a running MongoDB instance (e.g. localhost:27017 via Docker)

Run manually:
  MOZAIKS_RUN_REAL_MONGO_TESTS=1 MONGO_URI=mongodb://localhost:27017 \\
    pytest tests/test_entitlement_dispatch_docker_integration.py -v

Or with Docker Desktop running:
  docker run -d --rm -p 27017:27017 --name mozaiks-test-mongo mongo:7
  MOZAIKS_RUN_REAL_MONGO_TESTS=1 MONGO_URI=mongodb://localhost:27017 \\
    pytest tests/test_entitlement_dispatch_docker_integration.py -v
  docker stop mozaiks-test-mongo
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
import yaml

from tests.module_authority_test_helpers import enforce_authority, trusted_framework_authority

_RUN_ENV = "MOZAIKS_RUN_REAL_MONGO_TESTS"
_LEGACY_ENV = "MOZAIKS_RUN_MONGO_INTEGRATION"

pytestmark = pytest.mark.skipif(
    os.getenv(_RUN_ENV) != "1" and os.getenv(_LEGACY_ENV) != "1",
    reason=f"set {_RUN_ENV}=1 to run the Docker/real-Mongo entitlement dispatch integration test",
)


# ---------------------------------------------------------------------------
# App bundle fixture
# ---------------------------------------------------------------------------

_SUBSCRIPTIONS_CONFIG = {
    "schema_version": "mozaiks.subscriptions.v1",
    "label": "Analytics SaaS Plans",
    "default_plan_id": "free",
    "assignment_store": {
        "data_alias": "billing.subscriptions",
        "user_id_field": "user_id",
        "active_statuses": ["active"],
    },
    "plans": [
        {
            "plan_id": "free",
            "label": "Free",
            "capabilities": ["reports.view"],
        },
        {
            "plan_id": "pro",
            "label": "Pro",
            "capabilities": ["reports.view", "reports.export"],
        },
    ],
}

_APP_JSON = {
    "appId": "analytics-saas-integration",
    "appName": "Analytics SaaS Integration",
    "version": "1.0.0",
    "startup": {"landing_spot": "/reports"},
}

_REPORTS_MODULE_YAML = """\
schema_version: mozaiks.module.v1
module:
  id: reports
  display_name: Reports
  version: 1.0.0
  handler: backend.handler:ReportsModule
actions:
  - id: list_reports
    description: List reports visible to the authenticated user.
    handler_method: list_reports
    input_schema:
      type: object
      properties: {}
    output_schema:
      type: object
  - id: export_report
    description: Export a report as CSV. Requires Pro plan.
    handler_method: export_report
    entitlement_gate: reports.export
    input_schema:
      type: object
      required:
        - report_id
      properties:
        report_id:
          type: string
    output_schema:
      type: object
capabilities:
  - capability_id: reports.view
    kind: action
    target: list_reports
    title: View reports
  - capability_id: reports.export
    kind: action
    target: export_report
    title: Export reports
"""

_ENTITLEMENT_DISPATCH_MODULE_YAML = """\
schema_version: mozaiks.module.v1
module:
  id: entitlement_dispatch
  display_name: Entitlement Dispatch
  version: 1.0.0
  handler: backend.handler:EntitlementDispatchModule
actions:
  - id: activate_subscription
    description: >
      Write a subscription assignment record so ConfiguredEntitlementAdapter
      can grant capabilities for the plan.
    handler_method: activate_subscription
    input_schema:
      type: object
      required:
        - user_id
        - plan_id
      properties:
        user_id:
          type: string
        plan_id:
          type: string
    output_schema:
      type: object
  - id: deactivate_subscription
    description: Mark the subscription assignment inactive.
    handler_method: deactivate_subscription
    input_schema:
      type: object
      properties: {}
    output_schema:
      type: object
capabilities: []
"""


def _write_bundle(root: Path) -> None:
    files: dict[str, str] = {
        "app.json": json.dumps(_APP_JSON),
        "config/ai.json": json.dumps({"chat": {"chat_startup_mode": "ask"}, "workflows": {"entry_point": None}}),
        "config/shell.json": json.dumps({"navigation": {"autoFromPages": True}, "header": {"show": True}}),
        "config/subscriptions.yaml": yaml.safe_dump(_SUBSCRIPTIONS_CONFIG, sort_keys=False),
        "ui/route_manifest.json": json.dumps({"pages": []}),
        "modules/reports/module.yaml": _REPORTS_MODULE_YAML,
        "modules/reports/backend/__init__.py": "",
        "modules/reports/backend/handler.py": _REPORTS_HANDLER,
        "modules/reports/backend/service.py": _REPORTS_SERVICE,
        "modules/entitlement_dispatch/module.yaml": _ENTITLEMENT_DISPATCH_MODULE_YAML,
        "modules/entitlement_dispatch/backend/__init__.py": "",
        "modules/entitlement_dispatch/backend/handler.py": _DISPATCH_HANDLER,
        "modules/entitlement_dispatch/backend/service.py": "",  # repo does the real work inline
    }
    for rel, content in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")


_REPORTS_HANDLER = """\
from .service import ReportsService


class ReportsModule:
    def __init__(self):
        self.service = ReportsService()

    async def list_reports(self, ctx, **params):
        return await self.service.list_reports(ctx, **params)

    async def export_report(self, ctx, **params):
        return await self.service.export_report(ctx, **params)
"""

_REPORTS_SERVICE = """\
class ReportsService:
    async def list_reports(self, ctx, **params):
        return {"reports": []}

    async def export_report(self, ctx, **params):
        return {"csv": "report_id,value\\n1,100"}
"""

_DISPATCH_HANDLER = """\
import os

class EntitlementDispatchModule:
    async def activate_subscription(self, ctx, **params):
        user_id = params.get("user_id")
        plan_id = params.get("plan_id")
        if not user_id or not plan_id:
            raise ValueError("user_id and plan_id are required")
        collection = ctx.persistence.collection("entitlement_dispatch", "subscriptions")
        await collection.update_one(
            {"user_id": user_id},
            {"$set": {"user_id": user_id, "plan_id": plan_id, "status": "active",
                      "granted_capabilities": _capabilities_for_plan(plan_id)}},
            upsert=True,
        )
        return {"activated": True, "plan_id": plan_id}

    async def deactivate_subscription(self, ctx, **params):
        user_id = getattr(ctx, "user_id", None) or params.get("user_id")
        if not user_id:
            return {"deactivated": False, "reason": "no user_id"}
        collection = ctx.persistence.collection("entitlement_dispatch", "subscriptions")
        await collection.update_one(
            {"user_id": user_id},
            {"$set": {"status": "cancelled"}},
        )
        return {"deactivated": True}


def _capabilities_for_plan(plan_id: str) -> list[str]:
    if plan_id == "pro":
        return ["reports.view", "reports.export"]
    return ["reports.view"]
"""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _has_mongo_env() -> bool:
    return bool(os.getenv("MONGO_URI") or os.getenv("MONGODB_URI") or os.getenv("MONGO_URL"))


def _collection_name_for_billing_subscriptions() -> str:
    """Return the canonical Mongo collection name for the billing.subscriptions alias."""
    from mozaiksai.core.runtime.persistence import collection_name_for_alias
    return collection_name_for_alias(
        "billing.subscriptions",
        contract={
            "version": "1",
            "app_id": "analytics-saas-integration",
            "surfaces": [
                {
                    "surface_id": "entitlement_dispatch",
                    "surface_kind": "module",
                    "collections": [
                        {
                            "name": "subscriptions",
                            "ownership": {"surface_id": "entitlement_dispatch", "surface_kind": "module"},
                            "data_alias": "billing.subscriptions",
                        }
                    ],
                }
            ],
            "shared_collections": [],
        },
    )


# ---------------------------------------------------------------------------
# Test
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_entitlement_dispatch_docker_integration(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Full entitlement dispatch integration test against real MongoDB.

    Flow:
        1. Write bundle to disk, load with AppLoader.
        2. Subscribe user_id="user_pro" to the "pro" plan via entitlement_dispatch.
        3. Assert export_report succeeds (GRANTED) for user_pro.
        4. Assert export_report is ENTITLEMENT_REQUIRED for a free-plan user.
        5. Deactivate user_pro's subscription.
        6. Assert export_report is now ENTITLEMENT_REQUIRED for user_pro.
    """
    if not _has_mongo_env():
        pytest.skip("MONGO_URI is required for this test (Docker Desktop or real MongoDB)")

    from mozaiksai.core.core_config import close_mongo_client, get_mongo_client
    from mozaiksai.core.runtime.app.entitlements import ConfiguredEntitlementAdapter
    from mozaiksai.core.runtime.app.loader import AppLoader
    from mozaiksai.core.runtime.composition.module_executor import ModuleExecutor, ModuleRequest

    # Reset the cached Motor client so a fresh one is created on the current
    # event loop. pytest-rerunfailures reruns the test on a new loop; without
    # this the old (closed-loop) client is reused and Motor raises RuntimeError.
    close_mongo_client()

    # Use a per-test database name so tests are isolated
    test_db = f"mozaiks_entitlement_integration_{uuid4().hex[:10]}"
    monkeypatch.setenv("MOZAIKS_APP_DATABASE_NAME", test_db)

    app_root = tmp_path / "app"
    _write_bundle(app_root)

    # Load the app
    loaded = await AppLoader.load(str(app_root))
    assert loaded.definition.name == "Analytics SaaS Integration"
    assert {m.name for m in loaded.modules} == {"reports", "entitlement_dispatch"}
    assert loaded.subscriptions_config is not None
    assert loaded.subscriptions_config.assignment_store is not None

    app_id = "analytics-saas-integration"

    # Wire a collection resolver so the entitlement adapter finds the right
    # collection in our test database (no need to load the full app_data_contract path)
    from mozaiksai.core.runtime.persistence.naming import collection_name_for

    client = get_mongo_client()
    # The handler writes to ctx.persistence.collection("entitlement_dispatch", "subscriptions"),
    # which resolves to the namespaced collection name (includes app_id hash).
    # The entitlement adapter's _resolver must point to the same collection.
    real_collection_name = collection_name_for(
        app_id=app_id,
        module_id="entitlement_dispatch",
        entity_name="subscriptions",
    )
    subscriptions_collection = client[test_db][real_collection_name]

    def _resolver(data_alias: str) -> Any:
        # Only the billing.subscriptions alias is used in this test
        assert data_alias == "billing.subscriptions", f"Unexpected alias: {data_alias!r}"
        return subscriptions_collection

    entitlement_adapter = ConfiguredEntitlementAdapter(
        config=loaded.subscriptions_config,
        collection_resolver=_resolver,
    )

    # Build executor with entitlement checking wired
    executor = ModuleExecutor(entitlement_checker=entitlement_adapter)

    # Register module handlers from loaded app
    for module in loaded.modules:
        executor.register(
            module.name,
            module.handler,
            action_method_map=module.action_method_map,
            action_entitlements=module.action_entitlement_map,
        )

    try:
        # ---- Step 1: before activation, export_report is blocked for user_free ----
        pre_result = await executor.execute(
            ModuleRequest(
                module="reports",
                action="export_report",
                app_id=app_id,
                user_id="user_free",
                params={"report_id": "r1"},
                authority=enforce_authority(),  # user request — triggers entitlement check
            )
        )
        assert pre_result.success is False
        assert pre_result.error_code == "ENTITLEMENT_REQUIRED", (
            f"Expected ENTITLEMENT_REQUIRED before any subscription, got: {pre_result.error_code!r}"
        )

        # ---- Step 2: activate user_pro on the pro plan ----
        activate_result = await executor.execute(
            ModuleRequest(
                module="entitlement_dispatch",
                action="activate_subscription",
                app_id=app_id,
                user_id="user_pro",
                params={"user_id": "user_pro", "plan_id": "pro"}, authority=trusted_framework_authority(),
            )
        )
        assert activate_result.success is True, (
            f"activate_subscription failed: {activate_result!r}"
        )
        assert activate_result.data.get("activated") is True

        # Verify the assignment document was written to MongoDB
        doc = await subscriptions_collection.find_one({"user_id": "user_pro"})
        assert doc is not None, "Expected assignment document in MongoDB after activation"
        assert doc["status"] == "active"
        assert doc["plan_id"] == "pro"
        assert "reports.export" in (doc.get("granted_capabilities") or [])

        # ---- Step 3: export_report succeeds for user_pro ----
        export_result = await executor.execute(
            ModuleRequest(
                module="reports",
                action="export_report",
                app_id=app_id,
                user_id="user_pro",
                params={"report_id": "r1"},
                authority=enforce_authority(),  # user request — triggers entitlement check
            )
        )
        assert export_result.success is True, (
            f"export_report should succeed for pro user, got: {export_result!r}"
        )
        assert "csv" in export_result.data

        # ---- Step 4: export_report still blocked for user_free ----
        free_result = await executor.execute(
            ModuleRequest(
                module="reports",
                action="export_report",
                app_id=app_id,
                user_id="user_free",
                params={"report_id": "r1"},
                authority=enforce_authority(),  # user request — triggers entitlement check
            )
        )
        assert free_result.success is False
        assert free_result.error_code == "ENTITLEMENT_REQUIRED", (
            f"user_free should be denied export_report, got: {free_result.error_code!r}"
        )

        # ---- Step 5: list_reports works for both (not gated) ----
        list_result = await executor.execute(
            ModuleRequest(
                module="reports",
                action="list_reports",
                app_id=app_id,
                user_id="user_free",
                params={},
                authority=enforce_authority(),  # user request — triggers entitlement check
            )
        )
        assert list_result.success is True

        # ---- Step 6: deactivate user_pro ----
        deactivate_result = await executor.execute(
            ModuleRequest(
                module="entitlement_dispatch",
                action="deactivate_subscription",
                app_id=app_id,
                user_id="user_pro",
                params={"user_id": "user_pro"}, authority=trusted_framework_authority(),
            )
        )
        assert deactivate_result.success is True

        doc_after = await subscriptions_collection.find_one({"user_id": "user_pro"})
        assert doc_after is not None
        assert doc_after["status"] == "cancelled"

        # ---- Step 7: export_report now blocked for user_pro after deactivation ----
        post_deactivate_result = await executor.execute(
            ModuleRequest(
                module="reports",
                action="export_report",
                app_id=app_id,
                user_id="user_pro",
                params={"report_id": "r1"},
                authority=enforce_authority(),  # user request — triggers entitlement check
            )
        )
        assert post_deactivate_result.success is False
        assert post_deactivate_result.error_code == "ENTITLEMENT_REQUIRED", (
            f"user_pro should be blocked after deactivation, got: {post_deactivate_result.error_code!r}"
        )

    finally:
        # Synchronous cleanup to avoid event-loop-closed errors in pytest-asyncio teardown
        sync_client = __import__("pymongo").MongoClient(
            os.getenv("MONGO_URI") or os.getenv("MONGODB_URI") or "mongodb://localhost:27017"
        )
        sync_client.drop_database(test_db)
        sync_client.close()
