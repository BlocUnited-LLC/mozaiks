from __future__ import annotations

import json
import textwrap
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from pymongo.errors import DuplicateKeyError

from mozaiksai.core.runtime.app.entitlements import ConfiguredEntitlementAdapter
from mozaiksai.core.runtime.app.loader import AppLoader
from mozaiksai.core.runtime.composition.module_executor import ModuleExecutor, ModuleRequest
from mozaiksai.core.tokens.wallet import TokenWalletLedger


class _Cursor:
    def __init__(self, docs: list[dict[str, Any]]) -> None:
        self.docs = docs
        self.limit_value: int | None = None

    def sort(self, key: str, direction: int):
        reverse = direction < 0
        self.docs.sort(
            key=lambda item: item.get(key) or datetime.min.replace(tzinfo=UTC),
            reverse=reverse,
        )
        return self

    def limit(self, value: int):
        self.limit_value = value
        return self

    async def to_list(self, *, length: int):
        limit = self.limit_value or length
        return [deepcopy(item) for item in self.docs[:limit]]


class _Collection:
    def __init__(self, docs: list[dict[str, Any]] | None = None) -> None:
        self.docs: dict[str, dict[str, Any]] = {}
        for item in docs or []:
            key = str(item.get("_id") or len(self.docs))
            self.docs[key] = deepcopy({"_id": key, **item})

    async def create_index(self, *args, **kwargs):
        return None

    def replace_docs(self, docs: list[dict[str, Any]]) -> None:
        self.docs.clear()
        for item in docs:
            key = str(item.get("_id") or len(self.docs))
            self.docs[key] = deepcopy({"_id": key, **item})

    def _matches(self, doc: dict[str, Any], query: dict[str, Any]) -> bool:
        for key, expected in query.items():
            actual = doc.get(key)
            if isinstance(expected, dict):
                if "$ne" in expected:
                    denied = expected["$ne"]
                    if isinstance(actual, list):
                        if denied in actual:
                            return False
                    elif actual == denied:
                        return False
                if "$gte" in expected and not (actual is not None and actual >= expected["$gte"]):
                    return False
                continue
            if actual != expected:
                return False
        return True

    def _project(self, doc: dict[str, Any] | None, projection: dict[str, int] | None):
        if doc is None:
            return None
        value = deepcopy(doc)
        if projection and projection.get("_id") == 0:
            value.pop("_id", None)
        return value

    async def find_one(self, query: dict[str, Any], projection: dict[str, int] | None = None):
        for doc in self.docs.values():
            if self._matches(doc, query):
                return self._project(doc, projection)
        return None

    async def insert_one(self, doc: dict[str, Any]):
        key = doc["_id"]
        if key in self.docs:
            raise DuplicateKeyError("duplicate")
        self.docs[key] = deepcopy(doc)
        return None

    def _apply_update(self, doc: dict[str, Any], update: dict[str, Any], *, inserted: bool = False) -> dict[str, Any]:
        if inserted:
            for key, value in (update.get("$setOnInsert") or {}).items():
                doc[key] = deepcopy(value)
        for key, value in (update.get("$set") or {}).items():
            doc[key] = deepcopy(value)
        for key, value in (update.get("$inc") or {}).items():
            doc[key] = int(doc.get(key) or 0) + int(value)
        for key, value in (update.get("$addToSet") or {}).items():
            values = doc.setdefault(key, [])
            if value not in values:
                values.append(value)
        return doc

    async def update_one(self, query: dict[str, Any], update: dict[str, Any], **kwargs):
        for doc in self.docs.values():
            if self._matches(doc, query):
                self._apply_update(doc, update)
                return None
        return None

    async def find_one_and_update(
        self,
        query: dict[str, Any],
        update: dict[str, Any],
        *,
        upsert: bool = False,
        return_document=None,
    ):
        for doc in self.docs.values():
            if self._matches(doc, query):
                self._apply_update(doc, update)
                return deepcopy(doc)

        key = query.get("_id")
        if upsert and key and key not in self.docs:
            doc = {"_id": key}
            self._apply_update(doc, update, inserted=True)
            self.docs[key] = doc
            return deepcopy(doc)
        return None

    def find(self, query: dict[str, Any], projection: dict[str, int] | None = None):
        docs = [
            self._project(doc, projection) or {}
            for doc in self.docs.values()
            if self._matches(doc, query)
        ]
        return _Cursor(docs)


class _Database:
    def __init__(self) -> None:
        self.collections: dict[str, _Collection] = {}

    def __getitem__(self, name: str) -> _Collection:
        self.collections.setdefault(name, _Collection())
        return self.collections[name]


def _write_files(root: Path, files: dict[str, str]) -> None:
    for rel_path, content in files.items():
        target = root / rel_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")


def _generated_saas_files() -> dict[str, str]:
    return {
        "app.json": json.dumps(
            {
                "appId": "generated-saas",
                "appName": "Generated SaaS",
                "version": "1.0.0",
                "startup": {"landing_spot": "/reports"},
            }
        ),
        "config/subscriptions.yaml": textwrap.dedent(
            """
            schema_version: mozaiks.subscriptions.v1
            label: Generated SaaS Plans
            default_plan_id: free
            assignment_store:
              data_alias: billing.subscriptions
              app_id_field: app_id
              user_id_field: user_id
              tenant_id_field: tenant_id
              plan_id_field: plan_id
              status_field: status
              capabilities_field: granted_capabilities
              plan_snapshot_field: plan_snapshot
              active_statuses: [active, trialing]
            token_wallets:
              - wallet_id: ai_tokens
                label: AI tokens
                unit: tokens
                usage_meter_id: ai_tokens
                scope: user
                auto_debit_usage: true
            plans:
              - plan_id: free
                label: Free
                capabilities: [reports.view]
                token_allowances: []
              - plan_id: pro
                label: Pro
                capabilities: [reports.view, reports.generate]
                usage_limits:
                  - meter_id: ai_tokens
                    label: AI tokens
                    unit: tokens
                    monthly_limit: 1000
                    capability_id: reports.generate
                token_allowances:
                  - wallet_id: ai_tokens
                    amount: 1000
                    cadence: monthly
                    label: Monthly AI tokens
            """
        ),
        "modules/reports/module.yaml": textwrap.dedent(
            """
            schema_version: mozaiks.module.v1
            module:
              id: reports
              display_name: Reports
              version: 1.0.0
              handler: backend.handler:ReportsModule
            actions:
              - id: generate_report
                description: Generate an AI report.
                handler_method: generate_report
                entitlement_gate: reports.generate
                input_schema:
                  type: object
                  required: [topic]
                  properties:
                    topic:
                      type: string
                output_schema:
                  type: object
                  required: [report_id]
            capabilities:
              - capability_id: reports.generate
                kind: action
                target: generate_report
                title: Generate Report
            """
        ),
        "modules/reports/backend/__init__.py": "",
        "modules/reports/backend/handler.py": textwrap.dedent(
            """
            class ReportsModule:
                async def generate_report(self, ctx, *, topic):
                    return {"report_id": "report_1", "topic": topic}
            """
        ),
    }


def _active_assignment(*, status: str = "active") -> dict[str, Any]:
    return {
        "_id": "sub_1",
        "app_id": "generated-saas",
        "user_id": "user_1",
        "tenant_id": None,
        "plan_id": "pro",
        "status": status,
        "granted_capabilities": [
            {"capability_id": "reports.generate"},
        ],
        "plan_snapshot": {
            "granted_capabilities": [
                {"capability_id": "reports.generate"},
            ],
        },
    }


@pytest.mark.asyncio
async def test_factory_shaped_saas_app_enforces_subscription_and_spends_tokens(tmp_path: Path) -> None:
    app_root = tmp_path / "app"
    _write_files(app_root, _generated_saas_files())

    loaded = await AppLoader.load(str(app_root))

    assert loaded.subscriptions_config is not None
    assert loaded.subscriptions_config.token_wallet_by_id("ai_tokens") is not None
    assert loaded.modules[0].action_entitlement_map == {"generate_report": "reports.generate"}

    assignments = _Collection([])
    adapter = ConfiguredEntitlementAdapter(
        config=loaded.subscriptions_config,
        collection_resolver=lambda alias: assignments,
    )
    executor = ModuleExecutor(entitlement_checker=adapter)
    for module in loaded.modules:
        executor.register(
            module.name,
            module.handler,
            action_method_map=module.action_method_map,
            action_permissions=module.action_permissions_map,
            action_schemas=module.action_schemas_map,
            action_entitlements=module.action_entitlement_map,
        )

    denied = await executor.execute(
        ModuleRequest(
            module="reports",
            action="generate_report",
            params={"topic": "retention"},
            app_id="generated-saas",
            user_id="user_1",
            granted_permissions=[],
        )
    )
    assert denied.success is False
    assert denied.error_code == "ENTITLEMENT_REQUIRED"

    assignments.replace_docs([_active_assignment()])
    granted = await executor.execute(
        ModuleRequest(
            module="reports",
            action="generate_report",
            params={"topic": "retention"},
            app_id="generated-saas",
            user_id="user_1",
            granted_permissions=[],
        )
    )
    assert granted.success is True
    assert granted.data == {"report_id": "report_1", "topic": "retention"}

    ledger = TokenWalletLedger(database=_Database())
    await ledger.ensure_plan_allowances(
        config=loaded.subscriptions_config,
        app_id="generated-saas",
        plan_id=await adapter.current_plan_id(app_id="generated-saas", user_id="user_1"),
        user_id="user_1",
        period_start=datetime(2026, 6, 14, tzinfo=UTC),
    )
    await ledger.record_usage_debit(
        {
            "event_id": "usage_evt_report_1",
            "app_id": "generated-saas",
            "user_id": "user_1",
            "workflow_name": "ReportGenerator",
            "agent_name": "ReportAgent",
            "total_tokens": 125,
        },
        wallet=loaded.subscriptions_config.token_wallet_by_id("ai_tokens"),
    )
    await ledger.record_usage_debit(
        {
            "event_id": "usage_evt_report_1",
            "app_id": "generated-saas",
            "user_id": "user_1",
            "total_tokens": 125,
        },
        wallet=loaded.subscriptions_config.token_wallet_by_id("ai_tokens"),
    )

    balance = await ledger.query_balance(app_id="generated-saas", user_id="user_1")
    assert balance["balance"] == 875
    assert balance["total_allocated"] == 1000
    assert balance["total_spent"] == 125
    assert balance["entry_count"] == 2

    assignments.replace_docs([_active_assignment(status="cancelled")])
    cancelled = await executor.execute(
        ModuleRequest(
            module="reports",
            action="generate_report",
            params={"topic": "retention"},
            app_id="generated-saas",
            user_id="user_1",
            granted_permissions=[],
        )
    )
    assert cancelled.success is False
    assert cancelled.error_code == "ENTITLEMENT_REQUIRED"
