from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime
from typing import Any

import pytest
from pymongo.errors import DuplicateKeyError

from mozaiksai.core.data.persistence.namespaces import RuntimeCollections
from mozaiksai.core.runtime.app.entitlements import ProductPlanSelection
from mozaiksai.core.runtime.app.subscriptions_loader import SubscriptionsConfig
from mozaiksai.core.tokens.plan_resolution import resolve_v2_wallet_plans
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


def _v2_subscriptions_config(
    *, shared_wallet: bool = False, default_allowance: bool = False,
    nested_wallet: bool = False,
) -> SubscriptionsConfig:
    products = [
        {
            "product_id": "platform", "label": "Platform", "default_plan_id": "builder",
            "plans": [{"plan_id": "builder", "label": "Builder"}],
        },
        {
            "product_id": "ai", "label": "AI", "default_plan_id": "ai_starter",
            "plans": [
                {"plan_id": "ai_starter", "label": "Starter", "token_allowances": (
                    [{"wallet_id": "ai_tokens", "amount": 100, "cadence": "monthly"}]
                    if default_allowance else []
                )},
                {"plan_id": "ai_pro", "label": "AI Pro", "token_allowances": [
                    {"wallet_id": "ai_tokens", "amount": 500, "cadence": "monthly"}
                ]},
            ],
        },
    ]
    if shared_wallet:
        products.append({
            "product_id": "other", "label": "Other", "default_plan_id": "other_free",
            "plans": [
                {"plan_id": "other_free", "label": "Free"},
                {"plan_id": "other_paid", "label": "Paid", "token_allowances": [
                    {"wallet_id": "ai_tokens", "amount": 200, "cadence": "monthly"}
                ]},
            ],
        })
    wallet = {"wallet_id": "ai_tokens", "scope": "user"}
    if nested_wallet:
        products[1]["token_wallets"] = [wallet]
    return SubscriptionsConfig.model_validate({
        "schema_version": "mozaiks.subscriptions.v2",
        "label": "Multi-product SaaS",
        "default_product_id": "platform",
        "token_wallets": [] if nested_wallet else [wallet],
        "products": products,
    })


class _ProductPlans:
    def __init__(
        self, ai_plan_id: str | None = "ai_pro", snapshot_amount: int | None = None,
    ) -> None:
        self.ai_plan_id = ai_plan_id
        self.snapshot_amount = snapshot_amount
        self.requested_products: list[str | None] = []

    async def current_product_plan(self, **kwargs) -> ProductPlanSelection:
        product_id = kwargs.get("product_id")
        self.requested_products.append(product_id)
        if product_id != "ai":
            return ProductPlanSelection("platform", "builder", "default_plan")
        if self.ai_plan_id is None:
            return ProductPlanSelection("ai", None, "unavailable")
        if self.ai_plan_id == "ai_starter":
            return ProductPlanSelection("ai", "ai_starter", "default_plan")
        snapshot = (
            [{"wallet_id": "ai_tokens", "amount": self.snapshot_amount,
              "cadence": "monthly"}]
            if self.snapshot_amount is not None else None
        )
        return ProductPlanSelection(
            "ai", self.ai_plan_id, "active_assignment", snapshot
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
@pytest.mark.parametrize("billing_first", [True, False])
async def test_v2_paid_wallet_displays_ai_plan_without_runtime_grant(
    billing_first: bool,
) -> None:
    config = _v2_subscriptions_config()
    entitlements = _ProductPlans(snapshot_amount=350)
    selected = await resolve_v2_wallet_plans(
        config=config, entitlements=entitlements, app_id="app_1", user_id="user_1"
    )
    assert entitlements.requested_products == ["ai"]
    assert selected["ai_tokens"].product_id == "ai"
    assert selected["ai_tokens"].plan_id == "ai_pro"
    assert selected["ai_tokens"].grant_authority == "billing_fulfillment"
    assert selected["ai_tokens"].allowance_source == "assignment_snapshot"

    ledger = _ledger()

    async def billing_grant() -> None:
        await ledger.ensure_plan_allowances(
            config=config,
            app_id="app_1",
            plan_id="ai_pro",
            plan_label="AI Pro",
            token_allowances=[
                {"wallet_id": "ai_tokens", "amount": 350, "cadence": "monthly"}
            ],
            user_id="user_1",
        )

    if billing_first:
        await billing_grant()
    summary = await ledger.wallet_summaries_for_config(
        config=config,
        app_id="app_1",
        user_id="user_1",
        plan_id="builder",
        wallet_plans=selected,
        ensure_allowances=True,
    )
    if not billing_first:
        await billing_grant()

    wallet = summary["wallets"][0]
    assert summary["plan_id"] == "builder"  # Existing primary-plan field.
    assert wallet["product_id"] == "ai"
    assert wallet["plan_id"] == "ai_pro"
    assert wallet["plan_resolution"] == "resolved"
    assert wallet["plan_allowances"][0]["amount"] == 350
    assert wallet["grant_authority"] == "billing_fulfillment"
    assert wallet["allowance_source"] == "assignment_snapshot"
    assert wallet["balance"]["balance"] == (350 if billing_first else 0)
    balance = await ledger.query_balance(
        app_id="app_1", wallet_id="ai_tokens", user_id="user_1"
    )
    assert balance["balance"] == 350
    assert balance["entry_count"] == 1
    entries = await ledger.list_entries(
        app_id="app_1", wallet_id="ai_tokens", user_id="user_1"
    )
    assert entries[0]["metadata"]["plan_id"] == "ai_pro"
    assert "product_id" not in entries[0]["metadata"]  # Existing billing grant.


@pytest.mark.asyncio
@pytest.mark.parametrize("nested_wallet", [True, False])
async def test_v2_default_plan_allowance_sync_is_idempotent_and_has_product_provenance(
    nested_wallet: bool,
) -> None:
    config = _v2_subscriptions_config(
        default_allowance=True, nested_wallet=nested_wallet
    )
    assert config.token_wallet_by_id("ai_tokens") is not None
    selected = await resolve_v2_wallet_plans(
        config=config, entitlements=_ProductPlans("ai_starter"),
        app_id="app_1", user_id="user_1",
    )
    assert selected["ai_tokens"].grant_authority == "runtime_default"
    ledger = _ledger()
    for _ in range(2):
        await ledger.wallet_summaries_for_config(
            config=config, app_id="app_1", user_id="user_1",
            plan_id="builder", wallet_plans=selected, ensure_allowances=True,
        )
    balance = await ledger.query_balance(
        app_id="app_1", wallet_id="ai_tokens", user_id="user_1"
    )
    assert balance["balance"] == 100
    assert balance["entry_count"] == 1
    entries = await ledger.list_entries(
        app_id="app_1", wallet_id="ai_tokens", user_id="user_1"
    )
    assert entries[0]["metadata"]["product_id"] == "ai"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("shared_wallet", "ai_plan_id", "expected_status"),
    [
        (True, "ai_pro", "ambiguous_product"),
        (False, "operator_ai_plan", "unknown_plan"),
        (False, None, "plan_unavailable"),
    ],
)
async def test_v2_ambiguous_or_unknown_wallet_plan_never_auto_grants(
    shared_wallet: bool, ai_plan_id: str | None, expected_status: str,
) -> None:
    config = _v2_subscriptions_config(shared_wallet=shared_wallet)
    selected = await resolve_v2_wallet_plans(
        config=config,
        entitlements=_ProductPlans(ai_plan_id),
        app_id="app_1",
        user_id="user_1",
    )
    assert selected["ai_tokens"].status == expected_status

    ledger = _ledger()
    summary = await ledger.wallet_summaries_for_config(
        config=config,
        app_id="app_1",
        user_id="user_1",
        plan_id="builder",
        wallet_plans=selected,
        ensure_allowances=True,
    )
    wallet = summary["wallets"][0]
    assert wallet["plan_resolution"] == expected_status
    assert wallet["plan_allowances"] == []
    assert wallet["balance"]["balance"] == 0
    assert wallet["balance"]["entry_count"] == 0


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


# ── reservation acquisition never yields without ownership ───────────────────


def _reservation_entry(entry_id: str) -> dict:
    return {
        "_id": entry_id,
        "entry_id": entry_id,
        "balance_id": "app:ai_tokens:user:u1",
        "operation": "allocation",
        "direction": "credit",
        "amount": 100,
        "signed_amount": 100,
        "status": "pending",
        "reservation_owner": "rsv_original",
        "reservation_generation": 0,
    }


class _ContendedEntries:
    """Every compare-and-swap loses to a competitor that never settles."""

    def __init__(self, entry_id: str) -> None:
        self.entry_id = entry_id
        self.generation = 0
        self.swaps = 0

    async def find_one(self, _filter, _projection=None):
        document = _reservation_entry(self.entry_id)
        document["reservation_generation"] = self.generation
        return document

    async def find_one_and_update(self, *_args, **_kwargs):
        self.swaps += 1
        self.generation += 1
        return None

    async def insert_one(self, _document):  # pragma: no cover - not reached
        raise AssertionError("a pending reservation exists; insert must not run")


@pytest.mark.asyncio
async def test_unresolvable_contention_refuses_instead_of_writing() -> None:
    """Bounded attempts, then an operational refusal. Spinning forever is not
    an option and neither is moving the balance without the reservation that
    explains the movement."""
    from mozaiksai.core.tokens.wallet import (
        TokenWalletLedger,
        TokenWalletReservationUnavailable,
        _now,
    )

    entry_id = "token_wallet_entry:contended"
    entries = _ContendedEntries(entry_id)
    ledger = TokenWalletLedger(database=object())

    with pytest.raises(TokenWalletReservationUnavailable):
        await ledger._acquire_entry_reservation(
            entries,
            entry_id=entry_id,
            entry=_reservation_entry(entry_id),
            reservation_owner="rsv_mine",
            now=_now(),
        )
    assert entries.swaps > 1, "the attempt must be retried, not abandoned at once"


class _VanishingEntries:
    """The observed reservation is deleted before the swap can take it."""

    def __init__(self, entry_id: str) -> None:
        self.entry_id = entry_id
        self.present = True
        self.inserted: dict | None = None

    async def find_one(self, _filter, _projection=None):
        if not self.present:
            return None
        return _reservation_entry(self.entry_id)

    async def find_one_and_update(self, *_args, **_kwargs):
        self.present = False
        return None

    async def insert_one(self, document):
        self.inserted = dict(document)


@pytest.mark.asyncio
async def test_a_lost_swap_is_followed_by_a_fresh_reservation() -> None:
    """A swap that matches nothing means the world moved. The next attempt
    re-reads, finds the reservation gone, and makes one it owns."""
    from mozaiksai.core.tokens.wallet import TokenWalletLedger, _now

    entry_id = "token_wallet_entry:vanishing"
    entries = _VanishingEntries(entry_id)
    ledger = TokenWalletLedger(database=object())

    acquired = await ledger._acquire_entry_reservation(
        entries,
        entry_id=entry_id,
        entry=_reservation_entry(entry_id) | {"reservation_owner": "rsv_mine"},
        reservation_owner="rsv_mine",
        now=_now(),
    )

    assert acquired is None, "ownership, not a terminal outcome"
    assert entries.inserted is not None
    assert entries.inserted["reservation_owner"] == "rsv_mine"


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["applied", "rejected"])
async def test_a_settled_entry_is_returned_rather_than_reserved(status) -> None:
    from mozaiksai.core.tokens.wallet import TokenWalletLedger, _now

    entry_id = "token_wallet_entry:settled"

    class _Settled:
        async def find_one(self, _filter, _projection=None):
            return _reservation_entry(entry_id) | {"status": status}

        async def insert_one(self, _document):  # pragma: no cover
            raise AssertionError("a settled entry must not be re-reserved")

    ledger = TokenWalletLedger(database=object())
    acquired = await ledger._acquire_entry_reservation(
        _Settled(),
        entry_id=entry_id,
        entry=_reservation_entry(entry_id),
        reservation_owner="rsv_mine",
        now=_now(),
    )
    assert acquired is not None
    assert acquired["status"] == status
