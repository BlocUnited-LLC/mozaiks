from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime
from typing import Any

import pytest
from pymongo.errors import DuplicateKeyError

from mozaiksai.core.data.persistence.namespaces import RuntimeCollections
from mozaiksai.core.runtime.app.subscriptions_loader import SubscriptionsConfig
from mozaiksai.core.tokens.wallet import TokenWalletLedger


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


def _ledger() -> TokenWalletLedger:
    return TokenWalletLedger(database=_Database())


def _subscriptions_config() -> SubscriptionsConfig:
    return SubscriptionsConfig.model_validate(
        {
            "schema_version": "mozaiks.subscriptions.v1",
            "label": "SaaS",
            "default_plan_id": "pro",
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
                    "plan_id": "pro",
                    "label": "Pro",
                    "capabilities": ["ai.chat"],
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
                }
            ],
        }
    )


@pytest.mark.asyncio
async def test_credit_is_idempotent_and_updates_balance_once() -> None:
    ledger = _ledger()

    first = await ledger.credit(
        app_id="app_1",
        user_id="user_1",
        amount=100,
        idempotency_key="payment:1",
    )
    second = await ledger.credit(
        app_id="app_1",
        user_id="user_1",
        amount=100,
        idempotency_key="payment:1",
    )

    assert first.status == "applied"
    assert second.status == "applied"
    assert second.balance["balance"] == 100
    assert second.balance["entry_count"] == 1


@pytest.mark.asyncio
async def test_idempotency_key_reuse_with_different_amount_is_rejected() -> None:
    ledger = _ledger()
    await ledger.credit(
        app_id="app_1",
        user_id="user_1",
        amount=100,
        idempotency_key="payment:1",
    )

    with pytest.raises(ValueError, match="idempotency_key was reused"):
        await ledger.credit(
            app_id="app_1",
            user_id="user_1",
            amount=200,
            idempotency_key="payment:1",
        )


@pytest.mark.asyncio
async def test_debit_rejects_when_balance_is_insufficient() -> None:
    ledger = _ledger()

    result = await ledger.debit(
        app_id="app_1",
        user_id="user_1",
        amount=50,
        idempotency_key="usage:1",
    )

    assert result.status == "rejected"
    assert result.entry["rejection_reason"] == "insufficient_balance"
    assert result.balance["balance"] == 0


@pytest.mark.asyncio
async def test_debit_spends_existing_balance_and_is_idempotent() -> None:
    ledger = _ledger()
    await ledger.credit(
        app_id="app_1",
        user_id="user_1",
        amount=100,
        idempotency_key="payment:1",
    )

    first = await ledger.debit(
        app_id="app_1",
        user_id="user_1",
        amount=40,
        idempotency_key="usage:1",
    )
    second = await ledger.debit(
        app_id="app_1",
        user_id="user_1",
        amount=40,
        idempotency_key="usage:1",
    )

    assert first.status == "applied"
    assert second.status == "applied"
    assert second.balance["balance"] == 60
    assert second.balance["total_spent"] == 40


@pytest.mark.asyncio
async def test_subscription_allowances_are_monthly_and_idempotent() -> None:
    ledger = _ledger()
    config = _subscriptions_config()

    await ledger.ensure_plan_allowances(
        config=config,
        app_id="app_1",
        user_id="user_1",
        period_start=datetime(2026, 6, 14, tzinfo=UTC),
    )
    await ledger.ensure_plan_allowances(
        config=config,
        app_id="app_1",
        user_id="user_1",
        period_start=datetime(2026, 6, 20, tzinfo=UTC),
    )

    balance = await ledger.query_balance(app_id="app_1", user_id="user_1")
    assert balance["balance"] == 1000
    assert balance["total_allocated"] == 1000
    assert balance["entry_count"] == 1


@pytest.mark.asyncio
async def test_subscription_allowances_can_use_assignment_snapshot() -> None:
    ledger = _ledger()
    config = _subscriptions_config()

    await ledger.ensure_plan_allowances(
        config=config,
        app_id="app_1",
        plan_id="operator_plus",
        plan_label="Operator Plus",
        token_allowances=[
            {
                "wallet_id": "ai_tokens",
                "amount": 2500,
                "cadence": "monthly",
                "label": "Operator catalog monthly AI tokens",
            }
        ],
        user_id="user_1",
        period_start=datetime(2026, 6, 14, tzinfo=UTC),
    )
    await ledger.ensure_plan_allowances(
        config=config,
        app_id="app_1",
        plan_id="operator_plus",
        plan_label="Operator Plus",
        token_allowances=[
            {
                "wallet_id": "ai_tokens",
                "amount": 2500,
                "cadence": "monthly",
                "label": "Operator catalog monthly AI tokens",
            }
        ],
        user_id="user_1",
        period_start=datetime(2026, 6, 20, tzinfo=UTC),
    )

    balance = await ledger.query_balance(app_id="app_1", user_id="user_1")
    assert balance["balance"] == 2500
    assert balance["total_allocated"] == 2500
    assert balance["entry_count"] == 1

    entries = await ledger.list_entries(app_id="app_1", user_id="user_1")
    assert entries[0]["reason"] == "Operator catalog monthly AI tokens"
    assert entries[0]["metadata"]["plan_id"] == "operator_plus"


@pytest.mark.asyncio
async def test_usage_debit_uses_event_id_for_idempotency() -> None:
    ledger = _ledger()
    config = _subscriptions_config()
    wallet = config.token_wallets[0]
    await ledger.credit(
        app_id="app_1",
        user_id="user_1",
        amount=100,
        idempotency_key="payment:1",
    )

    await ledger.record_usage_debit(
        {
            "event_id": "usage_evt_1",
            "app_id": "app_1",
            "user_id": "user_1",
            "chat_id": "chat_1",
            "workflow_name": "Chat",
            "prompt_tokens": 10,
            "completion_tokens": 15,
            "total_tokens": 25,
        },
        wallet=wallet,
    )
    await ledger.record_usage_debit(
        {
            "event_id": "usage_evt_1",
            "app_id": "app_1",
            "user_id": "user_1",
            "total_tokens": 25,
        },
        wallet=wallet,
    )

    balance = await ledger.query_balance(app_id="app_1", user_id="user_1")
    assert balance["balance"] == 75
    assert balance["total_spent"] == 25


@pytest.mark.asyncio
async def test_metadata_rejects_secret_shaped_values() -> None:
    ledger = _ledger()

    with pytest.raises(ValueError, match="secret-shaped"):
        await ledger.credit(
            app_id="app_1",
            user_id="user_1",
            amount=100,
            idempotency_key="payment:1",
            metadata={"api_key": "env://SAFE_HANDLE"},
        )


@pytest.mark.asyncio
async def test_list_entries_returns_public_entries() -> None:
    ledger = _ledger()
    await ledger.credit(
        app_id="app_1",
        user_id="user_1",
        amount=100,
        idempotency_key="payment:1",
    )

    entries = await ledger.list_entries(app_id="app_1", user_id="user_1")

    assert len(entries) == 1
    assert entries[0]["entry_id"].startswith("token_wallet_entry:")
    assert "_id" not in entries[0]


def test_runtime_collections_include_token_wallet_collections() -> None:
    assert RuntimeCollections.RUNTIME_TOKEN_WALLET_BALANCES == "RuntimeTokenWalletBalances"
    assert RuntimeCollections.RUNTIME_TOKEN_WALLET_ENTRIES == "RuntimeTokenWalletEntries"
