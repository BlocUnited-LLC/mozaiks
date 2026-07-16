from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pymongo.errors import DuplicateKeyError

from mozaiksai.core.billing.fulfillment import (
    BillingFulfillmentCommand,
    BillingFulfillmentCommandStore,
    BillingFulfillmentConflictError,
    BillingFulfillmentService,
)
from mozaiksai.core.runtime.app.subscriptions_loader import SubscriptionsConfig
from mozaiksai.core.tokens.guard import TokenUsageGuard
from mozaiksai.core.tokens.wallet import TokenWalletLedger
from mozaiksai.hosts.routers.billing import router as billing_router


class _Cursor:
    def __init__(self, docs: list[dict[str, Any]]) -> None:
        self.docs = docs
        self.limit_value: int | None = None

    def sort(self, key: str, direction: int):
        reverse = direction < 0
        self.docs.sort(key=lambda item: item.get(key) or datetime.min.replace(tzinfo=UTC), reverse=reverse)
        return self

    def limit(self, value: int):
        self.limit_value = value
        return self

    async def to_list(self, *, length: int):
        limit = self.limit_value or length
        return [deepcopy(item) for item in self.docs[:limit]]


class _Collection:
    def __init__(self) -> None:
        self.docs: dict[str, dict[str, Any]] = {}

    async def create_index(self, *args, **kwargs):
        return None

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

    async def update_one(self, query: dict[str, Any], update: dict[str, Any], *, upsert: bool = False):
        for doc in self.docs.values():
            if self._matches(doc, query):
                self._apply_update(doc, update)
                return None
        if upsert:
            key = (update.get("$setOnInsert") or {}).get("_id") or query.get("_id") or f"doc:{len(self.docs)}"
            doc = {"_id": key}
            self._apply_update(doc, update, inserted=True)
            self.docs[key] = doc
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


def _subscriptions_config() -> SubscriptionsConfig:
    return SubscriptionsConfig.model_validate(
        {
            "schema_version": "mozaiks.subscriptions.v1",
            "label": "Test SaaS",
            "default_plan_id": "free",
            "assignment_store": {
                "data_alias": "billing.subscriptions",
                "user_id_field": "user_id",
            },
            "token_wallets": [
                {
                    "wallet_id": "ai_tokens",
                    "label": "AI tokens",
                    "unit": "tokens",
                    "usage_meter_id": "ai_tokens",
                    "scope": "user",
                    "auto_debit_usage": True,
                }
            ],
            "plans": [
                {
                    "plan_id": "free",
                    "label": "Free",
                    "capabilities": [],
                },
                {
                    "plan_id": "pro",
                    "label": "Pro",
                    "capabilities": ["dashboard.view", "ai.chat"],
                    "usage_limits": [
                        {
                            "meter_id": "ai_tokens",
                            "label": "AI tokens",
                            "unit": "tokens",
                            "monthly_limit": 1000,
                        }
                    ],
                    "token_allowances": [
                        {
                            "wallet_id": "ai_tokens",
                            "amount": 1000,
                            "cadence": "monthly",
                        }
                    ],
                },
            ],
        }
    )


def _service() -> tuple[BillingFulfillmentService, TokenWalletLedger, _Collection]:
    database = _Database()
    assignments = _Collection()
    ledger = TokenWalletLedger(database=database)
    service = BillingFulfillmentService(
        config=_subscriptions_config(),
        ledger=ledger,
        collection_resolver=lambda alias: assignments,
    )
    return service, ledger, assignments


def _top_up_config() -> SubscriptionsConfig:
    return SubscriptionsConfig.model_validate(
        {
            "schema_version": "mozaiks.subscriptions.v1",
            "label": "Usage SaaS",
            "default_plan_id": "free",
            "token_wallets": [
                {
                    "wallet_id": "ai_tokens",
                    "label": "AI tokens",
                    "unit": "tokens",
                    "usage_meter_id": "ai_tokens",
                    "scope": "user",
                    "auto_debit_usage": True,
                    "allow_negative_balance": False,
                }
            ],
            "plans": [
                {
                    "plan_id": "free",
                    "label": "Free",
                    "capabilities": [],
                    "token_allowances": [],
                }
            ],
        }
    )


def test_fulfillment_command_rejects_secret_shaped_metadata() -> None:
    with pytest.raises(ValueError, match="secret-shaped"):
        BillingFulfillmentCommand.model_validate(
            {
                "command_id": "cmd_1",
                "event_type": "token_top_up_paid",
                "source": "test",
                "app_id": "app_1",
                "user_id": "user_1",
                "token_amount": 100,
                "metadata": {"webhook_signature": "v1=abc"},
            }
        )


@pytest.mark.asyncio
async def test_subscription_activation_upserts_assignment_and_idempotent_allowance() -> None:
    service, ledger, assignments = _service()
    command = BillingFulfillmentCommand(
        command_id="cmd_activate_1",
        event_type="subscription_activated",
        source="test",
        app_id="app_1",
        user_id="user_1",
        plan_id="pro",
        occurred_at=datetime(2026, 7, 1, tzinfo=UTC),
    )

    first = await service.apply(command)
    second = await service.apply(command)

    assert first.success is True
    assert second.success is True
    assignment = await assignments.find_one(
        {"app_id": "app_1", "tenant_id": None, "user_id": "user_1"}
    )
    assert assignment["plan_id"] == "pro"
    assert assignment["status"] == "active"
    assert assignment["granted_capabilities"] == [
        {"capability_id": "dashboard.view"},
        {"capability_id": "ai.chat"},
    ]
    assert assignment["plan_snapshot"]["token_allowances"][0]["amount"] == 1000

    balance = await ledger.query_balance(app_id="app_1", user_id="user_1")
    assert balance["balance"] == 1000
    assert balance["total_allocated"] == 1000
    assert balance["entry_count"] == 1


@pytest.mark.asyncio
async def test_subscription_cancellation_marks_assignment_inactive() -> None:
    service, _ledger, assignments = _service()
    await service.apply(
        BillingFulfillmentCommand(
            command_id="cmd_activate_1",
            event_type="subscription_activated",
            source="test",
            app_id="app_1",
            user_id="user_1",
            plan_id="pro",
        )
    )

    result = await service.apply(
        BillingFulfillmentCommand(
            command_id="cmd_cancel_1",
            event_type="subscription_cancelled",
            source="test",
            app_id="app_1",
            user_id="user_1",
            plan_id="pro",
            occurred_at=datetime(2026, 7, 15, tzinfo=UTC),
        )
    )

    assert result.success is True
    assignment = await assignments.find_one(
        {"app_id": "app_1", "tenant_id": None, "user_id": "user_1"}
    )
    assert assignment["status"] == "cancelled"
    assert assignment["expires_at"] == "2026-07-15T00:00:00+00:00"


@pytest.mark.asyncio
async def test_paid_token_top_up_is_idempotent() -> None:
    service, ledger, _assignments = _service()
    command = BillingFulfillmentCommand(
        command_id="cmd_topup_1",
        event_type="token_top_up_paid",
        source="test",
        app_id="app_1",
        user_id="user_1",
        token_amount=500,
    )

    first = await service.apply(command)
    second = await service.apply(command)

    assert first.success is True
    assert second.success is True
    balance = await ledger.query_balance(app_id="app_1", user_id="user_1")
    assert balance["balance"] == 500
    assert balance["total_credited"] == 500
    assert balance["entry_count"] == 1


@pytest.mark.asyncio
async def test_refund_rejects_when_token_balance_is_insufficient() -> None:
    service, ledger, _assignments = _service()

    result = await service.apply(
        BillingFulfillmentCommand(
            command_id="cmd_refund_1",
            event_type="refund_applied",
            source="test",
            app_id="app_1",
            user_id="user_1",
            token_amount=250,
        )
    )

    assert result.success is False
    assert result.effects[0].effect == "wallet_debit"
    assert result.effects[0].status == "rejected"
    assert result.effects[0].reason == "insufficient_balance"
    balance = await ledger.query_balance(app_id="app_1", user_id="user_1")
    assert balance["balance"] == 0


@pytest.mark.asyncio
async def test_unknown_static_plan_without_snapshot_is_rejected() -> None:
    service, _ledger, _assignments = _service()

    result = await service.apply(
        BillingFulfillmentCommand(
            command_id="cmd_unknown_plan_1",
            event_type="subscription_activated",
            source="test",
            app_id="app_1",
            user_id="user_1",
            plan_id="enterprise",
        )
    )

    assert result.success is False
    assert result.effects[0].effect == "assignment_upsert"
    assert result.effects[0].status == "rejected"
    assert result.effects[0].reason == "unknown_plan"


@pytest.mark.asyncio
async def test_durable_fulfillment_replays_and_rejects_command_id_conflicts() -> None:
    database = _Database()
    assignments = _Collection()
    ledger = TokenWalletLedger(database=database)
    store = BillingFulfillmentCommandStore(database=database)
    service = BillingFulfillmentService(
        config=_subscriptions_config(),
        ledger=ledger,
        collection_resolver=lambda alias: assignments,
        command_store=store,
    )
    command = BillingFulfillmentCommand(
        command_id="cmd_durable_topup_1",
        event_type="token_top_up_paid",
        source="test",
        app_id="app_1",
        user_id="user_1",
        token_amount=500,
    )

    first = await service.apply_durable(command)
    replay = await service.apply_durable(command)

    assert first.status == "applied"
    assert first.command_log_id == "cmd_durable_topup_1"
    assert replay.status == "replayed"
    assert replay.replayed is True
    balance = await ledger.query_balance(app_id="app_1", user_id="user_1")
    assert balance["balance"] == 500
    assert balance["entry_count"] == 1

    records = await store.list_records(app_id="app_1")
    assert records[0]["command_id"] == "cmd_durable_topup_1"
    assert records[0]["status"] == "applied"
    assert records[0]["replay_count"] == 1

    with pytest.raises(BillingFulfillmentConflictError):
        await service.apply_durable(
            BillingFulfillmentCommand(
                command_id="cmd_durable_topup_1",
                event_type="token_top_up_paid",
                source="test",
                app_id="app_1",
                user_id="user_1",
                token_amount=750,
            )
        )


@pytest.mark.asyncio
async def test_cash_to_token_smoke_allows_debits_then_blocks_depleted_wallet() -> None:
    database = _Database()
    config = _top_up_config()
    ledger = TokenWalletLedger(database=database)
    service = BillingFulfillmentService(
        config=config,
        ledger=ledger,
        command_store=BillingFulfillmentCommandStore(database=database),
    )

    await service.apply_durable(
        BillingFulfillmentCommand(
            command_id="cmd_smoke_topup_1",
            event_type="token_top_up_paid",
            source="test",
            app_id="app_1",
            user_id="user_1",
            token_amount=50,
        )
    )

    guard = TokenUsageGuard(config=config, ledger=ledger)
    allowed = await guard.check(app_id="app_1", user_id="user_1", required_tokens=40)
    assert allowed.allowed is True
    assert allowed.reason == "sufficient_balance"

    wallet = config.token_wallet_by_id("ai_tokens")
    assert wallet is not None
    await ledger.record_usage_debit(
        {
            "event_id": "usage_evt_smoke_1",
            "app_id": "app_1",
            "user_id": "user_1",
            "total_tokens": 40,
        },
        wallet=wallet,
    )

    depleted = await guard.check(app_id="app_1", user_id="user_1", required_tokens=11)
    assert depleted.allowed is False
    assert depleted.error_code == "INSUFFICIENT_TOKENS"
    assert depleted.balance == 10


def test_fulfillment_apply_route_replays_and_exposes_command_log(monkeypatch) -> None:
    database = _Database()
    config = _top_up_config()
    ledger = TokenWalletLedger(database=database)
    store = BillingFulfillmentCommandStore(database=database)
    service = BillingFulfillmentService(
        config=config,
        ledger=ledger,
        command_store=store,
    )

    class _Audit:
        def __init__(self) -> None:
            self.records: list[Any] = []

        async def log(self, record: Any) -> None:
            self.records.append(record)

    audit = _Audit()
    monkeypatch.setenv("INTERNAL_API_KEY", "test")
    monkeypatch.setattr(
        "mozaiksai.hosts.routers.billing.get_audit_logger",
        lambda: audit,
    )

    app = FastAPI()
    app.state.subscriptions_config = config
    app.state.billing_fulfillment_command_store = store
    app.state.billing_fulfillment_service_factory = lambda request: service
    app.include_router(billing_router)
    client = TestClient(app, raise_server_exceptions=False)

    payload = {
        "command_id": "cmd_route_topup_1",
        "event_type": "token_top_up_paid",
        "source": "test",
        "app_id": "app_1",
        "user_id": "user_1",
        "token_amount": 500,
    }
    headers = {"x-internal-" + "api-key": "test"}

    first = client.post("/api/billing/fulfillment/apply", json=payload, headers=headers)
    replay = client.post("/api/billing/fulfillment/apply", json=payload, headers=headers)
    conflict_payload = {**payload, "token_amount": 750}
    conflict = client.post("/api/billing/fulfillment/apply", json=conflict_payload, headers=headers)
    listing = client.get("/api/admin/billing/fulfillment?app_id=app_1", headers=headers)

    assert first.status_code == 200
    assert first.json()["status"] == "applied"
    assert first.json()["command_log_id"] == "cmd_route_topup_1"
    assert replay.status_code == 200
    assert replay.json()["status"] == "replayed"
    assert replay.json()["replayed"] is True
    assert conflict.status_code == 409
    assert listing.status_code == 200
    assert listing.json()["records"][0]["command_id"] == "cmd_route_topup_1"
    assert len(audit.records) == 2
