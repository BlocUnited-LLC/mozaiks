from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError
from pymongo.errors import DuplicateKeyError

from mozaiksai.core.billing.fulfillment import (
    MAX_SUBJECT_REVISION,
    BillingFulfillmentCommand,
    BillingFulfillmentCommandStore,
    BillingFulfillmentConflictError,
    BillingFulfillmentService,
    _json_hash,
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
            if key == "$or":
                if not any(self._matches(doc, clause) for clause in expected):
                    return False
                continue
            actual = doc.get(key)
            if isinstance(expected, dict):
                for operator, operand in expected.items():
                    if operator == "$ne":
                        if isinstance(actual, list):
                            if operand in actual:
                                return False
                        elif actual == operand:
                            return False
                    elif operator == "$gte":
                        if actual is None or not actual >= operand:
                            return False
                    elif operator == "$lt":
                        if actual is None or not actual < operand:
                            return False
                    elif operator == "$exists":
                        if (key in doc) is not bool(operand):
                            return False
                    else:  # pragma: no cover - guards against silent test lies
                        raise NotImplementedError(f"fake mongo operator {operator}")
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
                return SimpleNamespace(matched_count=1, modified_count=1, upserted_id=None)
        if upsert:
            key = (update.get("$setOnInsert") or {}).get("_id") or query.get("_id") or f"doc:{len(self.docs)}"
            if key in self.docs:
                # A filtered upsert whose predicate excluded an existing
                # document collides on the unique _id, exactly as Mongo does.
                raise DuplicateKeyError("duplicate")
            doc = {"_id": key}
            self._apply_update(doc, update, inserted=True)
            self.docs[key] = doc
            return SimpleNamespace(matched_count=0, modified_count=0, upserted_id=key)
        return SimpleNamespace(matched_count=0, modified_count=0, upserted_id=None)

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


class _AllowancePeriodLedger:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def ensure_plan_allowances(self, **kwargs):
        self.calls.append(kwargs)
        return [
            type(
                "Result",
                (),
                {
                    "status": "applied",
                    "entry": {"wallet_id": "ai_tokens", "amount": 1000},
                    "balance": {"balance": 1000},
                },
            )()
        ]


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
async def test_subscription_update_uses_occurred_at_for_allowance_period() -> None:
    assignments = _Collection()
    ledger = _AllowancePeriodLedger()
    service = BillingFulfillmentService(
        config=_subscriptions_config(),
        ledger=ledger,  # type: ignore[arg-type]
        collection_resolver=lambda alias: assignments,
    )

    result = await service.apply(
        BillingFulfillmentCommand(
            command_id="cmd_renewal_period_1",
            event_type="subscription_updated",
            source="test",
            app_id="app_1",
            user_id="user_1",
            plan_id="pro",
            starts_at=datetime(2026, 6, 1, tzinfo=UTC),
            occurred_at=datetime(2026, 7, 1, tzinfo=UTC),
        )
    )

    assert result.success is True
    assert ledger.calls[0]["period_start"] == datetime(2026, 7, 1, tzinfo=UTC)


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


# ---------------------------------------------------------------------------
# Fulfillment ingress authentication boundary (fail closed)
# ---------------------------------------------------------------------------


def _ingress_client(monkeypatch) -> TestClient:
    """Build a fulfillment app with a working service and silenced audit sink."""
    database = _Database()
    config = _top_up_config()
    ledger = TokenWalletLedger(database=database)
    store = BillingFulfillmentCommandStore(database=database)
    service = BillingFulfillmentService(config=config, ledger=ledger, command_store=store)

    class _Audit:
        async def log(self, record: Any) -> None:
            return None

    monkeypatch.setattr("mozaiksai.hosts.routers.billing.get_audit_logger", lambda: _Audit())

    app = FastAPI()
    app.state.subscriptions_config = config
    app.state.billing_fulfillment_command_store = store
    app.state.billing_fulfillment_service_factory = lambda request: service
    app.include_router(billing_router)
    return TestClient(app, raise_server_exceptions=False)


_INGRESS_PAYLOAD = {
    "command_id": "cmd_ingress_1",
    "event_type": "token_top_up_paid",
    "source": "test",
    "app_id": "app_1",
    "user_id": "user_1",
    "token_amount": 100,
}


def _clear_auth_env(monkeypatch) -> None:
    for var in (
        "AUTH_ENABLED",
        "AUTH_PROVIDER",
        "INTERNAL_API_KEY",
        "SUPABASE_URL",
        "KEYCLOAK_URL",
        "KEYCLOAK_REALM",
        "AUTH_JWKS_URL",
        "AUTH_ISSUER",
        "MOZAIKS_OIDC_AUTHORITY",
        "MOZAIKS_OIDC_DISCOVERY_URL",
        # A developer .env may grant the anonymous principal admin roles;
        # these tests exercise the un-granted anonymous path.
        "AUTH_ANON_ROLES",
        "AUTH_ANON_SCOPES",
    ):
        monkeypatch.delenv(var, raising=False)


def test_fulfillment_ingress_fails_closed_when_auth_merely_unconfigured(monkeypatch) -> None:
    """Implicit demo mode (no auth config at all) + no INTERNAL_API_KEY must NOT
    make the fulfillment ingress callable without authentication."""
    from mozaiksai.core.auth.adapters.registry import reset_auth_adapter

    _clear_auth_env(monkeypatch)
    reset_auth_adapter()
    try:
        client = _ingress_client(monkeypatch)
        resp = client.post("/api/billing/fulfillment/apply", json=_INGRESS_PAYLOAD)
        assert resp.status_code == 403
        listing = client.get("/api/admin/billing/fulfillment?app_id=app_1")
        assert listing.status_code == 403
    finally:
        reset_auth_adapter()


def test_fulfillment_ingress_allows_explicitly_disabled_auth_dev_mode(monkeypatch) -> None:
    """AUTH_ENABLED=false is the explicit development contract — local dev keeps working."""
    from mozaiksai.core.auth.adapters.registry import reset_auth_adapter

    _clear_auth_env(monkeypatch)
    monkeypatch.setenv("AUTH_ENABLED", "false")
    reset_auth_adapter()
    try:
        client = _ingress_client(monkeypatch)
        resp = client.post("/api/billing/fulfillment/apply", json=_INGRESS_PAYLOAD)
        assert resp.status_code == 200
        assert resp.json()["status"] == "applied"
    finally:
        reset_auth_adapter()


def test_fulfillment_ingress_requires_key_when_key_configured_even_if_auth_disabled(monkeypatch) -> None:
    """Once INTERNAL_API_KEY is configured, the anonymous dev path closes."""
    from mozaiksai.core.auth.adapters.registry import reset_auth_adapter

    _clear_auth_env(monkeypatch)
    monkeypatch.setenv("AUTH_ENABLED", "false")
    monkeypatch.setenv("INTERNAL_API_KEY", "configured-key-0123456789abcdef")
    reset_auth_adapter()
    try:
        client = _ingress_client(monkeypatch)
        no_key = client.post("/api/billing/fulfillment/apply", json=_INGRESS_PAYLOAD)
        assert no_key.status_code == 403
        wrong_key = client.post(
            "/api/billing/fulfillment/apply",
            json=_INGRESS_PAYLOAD,
            headers={"x-internal-api-key": "wrong"},
        )
        assert wrong_key.status_code == 401
        right_key = client.post(
            "/api/billing/fulfillment/apply",
            json=_INGRESS_PAYLOAD,
            headers={"x-internal-api-key": "configured-key-0123456789abcdef"},
        )
        assert right_key.status_code == 200
    finally:
        reset_auth_adapter()


def test_fulfillment_ingress_internal_key_works_with_auth_enabled(monkeypatch) -> None:
    """Auth enabled with a real provider: the internal key path still authorizes."""
    from mozaiksai.core.auth.adapters.registry import reset_auth_adapter

    _clear_auth_env(monkeypatch)
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setenv("AUTH_PROVIDER", "jwt")
    monkeypatch.setenv("AUTH_JWKS_URL", "https://example.com/.well-known/jwks.json")
    monkeypatch.setenv("AUTH_ISSUER", "https://example.com")
    monkeypatch.setenv("INTERNAL_API_KEY", "configured-key-0123456789abcdef")
    from mozaiksai.core.auth.config import clear_auth_config_cache

    clear_auth_config_cache()
    reset_auth_adapter()
    try:
        client = _ingress_client(monkeypatch)
        anonymous = client.post("/api/billing/fulfillment/apply", json=_INGRESS_PAYLOAD)
        assert anonymous.status_code in (401, 403)
        keyed = client.post(
            "/api/billing/fulfillment/apply",
            json=_INGRESS_PAYLOAD,
            headers={"x-internal-api-key": "configured-key-0123456789abcdef"},
        )
        assert keyed.status_code == 200
    finally:
        clear_auth_config_cache()
        reset_auth_adapter()


# ---------------------------------------------------------------------------
# Fulfillment authenticated-provenance matrix (D3) + environment policy (D2)
# ---------------------------------------------------------------------------


def _principal(*, roles=None, scopes=None, provenance: str):
    from mozaiksai.core.auth.dependencies import UserPrincipal

    return UserPrincipal(
        user_id="user_matrix",
        email=None,
        name="Matrix User",
        roles=list(roles or []),
        scopes=list(scopes or []),
        raw_claims={},
        provider="jwt" if provenance == "token_validated" else "none",
        auth_provenance=provenance,
    )


def _ingress_client_with_principal(monkeypatch, principal) -> TestClient:
    from mozaiksai.core.auth.dependencies import optional_user

    database = _Database()
    config = _top_up_config()
    ledger = TokenWalletLedger(database=database)
    store = BillingFulfillmentCommandStore(database=database)
    service = BillingFulfillmentService(config=config, ledger=ledger, command_store=store)

    class _Audit:
        async def log(self, record: Any) -> None:
            return None

    monkeypatch.setattr("mozaiksai.hosts.routers.billing.get_audit_logger", lambda: _Audit())

    app = FastAPI()
    app.state.subscriptions_config = config
    app.state.billing_fulfillment_command_store = store
    app.state.billing_fulfillment_service_factory = lambda request: service
    app.include_router(billing_router)
    app.dependency_overrides[optional_user] = lambda: principal
    return TestClient(app, raise_server_exceptions=False)


def _auth_enabled_jwt_env(monkeypatch) -> None:
    """Auth enabled with a fully configured jwt provider; no internal key."""
    from mozaiksai.core.auth.adapters.registry import reset_auth_adapter
    from mozaiksai.core.auth.config import clear_auth_config_cache

    _clear_auth_env(monkeypatch)
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setenv("AUTH_PROVIDER", "jwt")
    monkeypatch.setenv("AUTH_JWKS_URL", "https://example.com/.well-known/jwks.json")
    monkeypatch.setenv("AUTH_ISSUER", "https://example.com")
    clear_auth_config_cache()
    reset_auth_adapter()


def test_fulfillment_allows_authenticated_billing_admin(monkeypatch) -> None:
    _auth_enabled_jwt_env(monkeypatch)
    principal = _principal(roles=["admin"], provenance="token_validated")
    client = _ingress_client_with_principal(monkeypatch, principal)

    resp = client.post("/api/billing/fulfillment/apply", json=_INGRESS_PAYLOAD)
    assert resp.status_code == 200
    assert resp.json()["status"] == "applied"


def test_fulfillment_allows_authenticated_billing_scope(monkeypatch) -> None:
    _auth_enabled_jwt_env(monkeypatch)
    principal = _principal(scopes=["billing.fulfillment.apply"], provenance="token_validated")
    client = _ingress_client_with_principal(monkeypatch, principal)

    resp = client.post("/api/billing/fulfillment/apply", json=_INGRESS_PAYLOAD)
    assert resp.status_code == 200


def test_fulfillment_denies_authenticated_unprivileged_principal(monkeypatch) -> None:
    _auth_enabled_jwt_env(monkeypatch)
    principal = _principal(roles=["user"], scopes=["access_as_user"], provenance="token_validated")
    client = _ingress_client_with_principal(monkeypatch, principal)

    resp = client.post("/api/billing/fulfillment/apply", json=_INGRESS_PAYLOAD)
    assert resp.status_code == 403


def test_fulfillment_denies_anonymous_principal_with_admin_roles(monkeypatch) -> None:
    """Admin-looking role strings without authenticated provenance are not authority."""
    _auth_enabled_jwt_env(monkeypatch)
    principal = _principal(roles=["admin"], scopes=["billing.admin"], provenance="anonymous")
    client = _ingress_client_with_principal(monkeypatch, principal)

    resp = client.post("/api/billing/fulfillment/apply", json=_INGRESS_PAYLOAD)
    assert resp.status_code == 403


def test_fulfillment_denies_dev_persona_with_admin_roles(monkeypatch) -> None:
    _auth_enabled_jwt_env(monkeypatch)
    principal = _principal(roles=["admin"], scopes=["billing.admin"], provenance="dev_override")
    client = _ingress_client_with_principal(monkeypatch, principal)

    resp = client.post("/api/billing/fulfillment/apply", json=_INGRESS_PAYLOAD)
    assert resp.status_code == 403


def test_fulfillment_internal_key_allowed_alongside_denied_principal(monkeypatch) -> None:
    """Correct internal key remains independently sufficient."""
    _auth_enabled_jwt_env(monkeypatch)
    monkeypatch.setenv("INTERNAL_API_KEY", "configured-key-0123456789abcdef")
    principal = _principal(roles=["admin"], provenance="anonymous")
    client = _ingress_client_with_principal(monkeypatch, principal)

    without_key = client.post("/api/billing/fulfillment/apply", json=_INGRESS_PAYLOAD)
    assert without_key.status_code == 403
    with_key = client.post(
        "/api/billing/fulfillment/apply",
        json=_INGRESS_PAYLOAD,
        headers={"x-internal-api-key": "configured-key-0123456789abcdef"},
    )
    assert with_key.status_code == 200


def test_fulfillment_spoofed_dev_headers_cannot_gain_authenticated_provenance(monkeypatch) -> None:
    """With auth enabled, dev persona headers are inert: no token means no
    principal at all, and dev overrides only exist in the auth-disabled branch."""
    _auth_enabled_jwt_env(monkeypatch)
    client = _ingress_client(monkeypatch)  # real optional_user dependency

    resp = client.post(
        "/api/billing/fulfillment/apply",
        json=_INGRESS_PAYLOAD,
        headers={
            "X-Mozaiks-Dev-User-Id": "dev_admin",
            "X-Mozaiks-Dev-Roles": "admin",
            "X-Mozaiks-Dev-Scopes": "billing.admin",
        },
    )
    assert resp.status_code == 403


@pytest.mark.parametrize("environment", ["staging", "production"])
def test_fulfillment_fails_closed_in_protected_env_even_if_startup_bypassed(
    monkeypatch, environment
) -> None:
    """Explicit no-auth in a protected environment would never boot; if a
    process somehow reached request handling anyway, the canonical resolution
    still raises and the ingress cannot succeed."""
    from mozaiksai.core.auth.adapters.registry import reset_auth_adapter

    _clear_auth_env(monkeypatch)
    monkeypatch.setenv("AUTH_ENABLED", "false")
    monkeypatch.setenv("ENV", environment)
    reset_auth_adapter()
    try:
        client = _ingress_client(monkeypatch)
        resp = client.post("/api/billing/fulfillment/apply", json=_INGRESS_PAYLOAD)
        assert resp.status_code == 500  # AuthError surfaces; never 200
    finally:
        reset_auth_adapter()


# ── subject revision fencing ─────────────────────────────────────────────────
#
# Commands may carry `subject_revision`: a provider-neutral, monotonically
# increasing ordinal allocated by the upstream billing source per entitlement
# subject. Subscription effects commit only when the incoming revision is
# strictly newer than the one already stored for that subject, so an
# out-of-order command can never regress entitlement state.


def _subscription_command(
    command_id: str,
    *,
    plan_id: str,
    subject_revision: int | None = None,
    event_type: str = "subscription_updated",
    user_id: str = "user_1",
) -> BillingFulfillmentCommand:
    return BillingFulfillmentCommand(
        command_id=command_id,
        event_type=event_type,
        source="test",
        app_id="app_1",
        user_id=user_id,
        plan_id=plan_id,
        subject_revision=subject_revision,
        occurred_at=datetime(2026, 7, 1, tzinfo=UTC),
    )


async def _assignment(assignments: _Collection, *, user_id: str = "user_1"):
    return await assignments.find_one(
        {"app_id": "app_1", "tenant_id": None, "user_id": user_id}
    )


@pytest.mark.asyncio
async def test_newer_revision_applies_and_stamps_the_subject() -> None:
    service, _ledger, assignments = _service()

    result = await service.apply(
        _subscription_command("cmd_rev_1", plan_id="pro", subject_revision=7)
    )

    assert result.status == "applied"
    assert result.success is True
    assignment = await _assignment(assignments)
    assert assignment["plan_id"] == "pro"
    assert assignment["billing_revision"] == 7
    assert result.effects[0].details["subject_revision"] == 7


@pytest.mark.asyncio
async def test_stale_revision_cannot_regress_a_newer_subject() -> None:
    """The core race: an older command completes remotely after a newer one."""
    service, ledger, assignments = _service()

    await service.apply(
        _subscription_command("cmd_new", plan_id="pro", subject_revision=9)
    )
    stale = await service.apply(
        _subscription_command("cmd_old", plan_id="free", subject_revision=4)
    )

    assert stale.status == "superseded"
    assert stale.success is True, "a correctly suppressed command is not an error"
    assert stale.applied == 0
    assert {effect.effect for effect in stale.effects} == {
        "assignment_upsert", "plan_allowances",
    }
    assert all(effect.reason == "stale_revision" for effect in stale.effects)

    assignment = await _assignment(assignments)
    assert assignment["plan_id"] == "pro", "the newer revision must still govern"
    assert assignment["billing_revision"] == 9
    # The stale command must not mint allowances behind the fenced assignment.
    balance = await ledger.query_balance(app_id="app_1", user_id="user_1")
    assert balance["balance"] == 1000, "a fenced command must not mint allowances"


@pytest.mark.asyncio
async def test_equal_revision_is_suppressed() -> None:
    """Equal revisions carry no ordering information, so the first commit wins
    and a second distinct command for the same revision is suppressed."""
    service, _ledger, assignments = _service()

    await service.apply(
        _subscription_command("cmd_a", plan_id="pro", subject_revision=5)
    )
    duplicate = await service.apply(
        _subscription_command("cmd_b", plan_id="free", subject_revision=5)
    )

    assert duplicate.status == "superseded"
    assignment = await _assignment(assignments)
    assert assignment["plan_id"] == "pro"


@pytest.mark.asyncio
async def test_revision_sequence_applies_in_order() -> None:
    service, _ledger, assignments = _service()

    for index, plan_id in enumerate(["free", "pro", "free"], start=1):
        result = await service.apply(
            _subscription_command(f"cmd_seq_{index}", plan_id=plan_id, subject_revision=index)
        )
        assert result.status == "applied"

    assignment = await _assignment(assignments)
    assert assignment["plan_id"] == "free"
    assert assignment["billing_revision"] == 3


@pytest.mark.asyncio
async def test_first_fenced_write_creates_the_subject() -> None:
    """A fenced command for a subject that does not exist yet must insert it."""
    service, _ledger, assignments = _service()

    result = await service.apply(
        _subscription_command(
            "cmd_first", plan_id="pro", subject_revision=1,
            event_type="subscription_activated",
        )
    )

    assert result.status == "applied"
    assert (await _assignment(assignments))["billing_revision"] == 1


@pytest.mark.asyncio
async def test_unfenced_command_keeps_prior_behavior() -> None:
    """Commands without `subject_revision` apply unfenced, exactly as before —
    callers with no ordering authority are unaffected."""
    service, _ledger, assignments = _service()

    await service.apply(_subscription_command("cmd_x", plan_id="pro", subject_revision=9))
    unfenced = await service.apply(_subscription_command("cmd_y", plan_id="free"))

    assert unfenced.status == "applied"
    assignment = await _assignment(assignments)
    assert assignment["plan_id"] == "free"
    assert assignment["billing_revision"] == 9, "an unfenced write leaves the stamp"


@pytest.mark.asyncio
async def test_fence_is_scoped_to_one_entitlement_subject() -> None:
    """Revisions are compared per subject; one customer's newer revision must
    not suppress another customer's older one."""
    service, _ledger, assignments = _service()

    await service.apply(
        _subscription_command("cmd_u1", plan_id="pro", subject_revision=9, user_id="user_1")
    )
    other = await service.apply(
        _subscription_command("cmd_u2", plan_id="pro", subject_revision=2, user_id="user_2")
    )

    assert other.status == "applied"
    assert (await _assignment(assignments, user_id="user_2"))["billing_revision"] == 2


@pytest.mark.asyncio
async def test_stale_cancellation_cannot_revoke_a_newer_activation() -> None:
    service, _ledger, assignments = _service()

    await service.apply(
        _subscription_command(
            "cmd_reactivate", plan_id="pro", subject_revision=12,
            event_type="subscription_activated",
        )
    )
    stale_cancel = await service.apply(
        _subscription_command(
            "cmd_cancel_old", plan_id="pro", subject_revision=8,
            event_type="subscription_cancelled",
        )
    )

    assert stale_cancel.status == "superseded"
    assignment = await _assignment(assignments)
    assert assignment["status"] == "active", "a stale cancel must not revoke access"


@pytest.mark.asyncio
async def test_durable_stale_revision_is_replayable_and_stable() -> None:
    """Replay of a stale command returns the preserved superseded result rather
    than re-evaluating the fence."""
    database = _Database()
    store = BillingFulfillmentCommandStore(database=database)
    assignments = _Collection()
    ledger = TokenWalletLedger(database=database)
    service = BillingFulfillmentService(
        config=_subscriptions_config(),
        ledger=ledger,
        collection_resolver=lambda _alias: assignments,
        command_store=store,
    )

    await service.apply_durable(
        _subscription_command("cmd_head", plan_id="pro", subject_revision=20)
    )
    stale_command = _subscription_command("cmd_stale", plan_id="free", subject_revision=3)
    first = await service.apply_durable(stale_command)
    replayed = await service.apply_durable(stale_command)

    assert first.status == "superseded"
    assert replayed.status == "replayed"
    assert replayed.replayed is True
    assert replayed.success is True
    assert replayed.applied == 0


@pytest.mark.asyncio
async def test_revision_field_can_be_disabled_per_app() -> None:
    """Setting `revision_field: null` opts an app out of fencing entirely."""
    config = SubscriptionsConfig.model_validate(
        {
            **_subscriptions_config().model_dump(mode="json"),
            "assignment_store": {
                "data_alias": "billing.subscriptions",
                "user_id_field": "user_id",
                "revision_field": None,
            },
        }
    )
    database = _Database()
    ledger = TokenWalletLedger(database=database)
    assignments = _Collection()
    service = BillingFulfillmentService(
        config=config,
        ledger=ledger,
        collection_resolver=lambda _alias: assignments,
    )

    await service.apply(_subscription_command("cmd_p", plan_id="pro", subject_revision=9))
    older = await service.apply(_subscription_command("cmd_q", plan_id="free", subject_revision=1))

    assert older.status == "applied"
    assignment = await assignments.find_one(
        {"app_id": "app_1", "tenant_id": None, "user_id": "user_1"}
    )
    assert assignment["plan_id"] == "free"
    assert "billing_revision" not in assignment


# ── subject_revision is strict and BSON-safe ─────────────────────────────────


@pytest.mark.parametrize(
    "value",
    [True, False, 1.0, 0.0, "1", "1.0", "", [1], {"v": 1}, Decimal("1")],
)
def test_subject_revision_rejects_coercible_values(value) -> None:
    """A silently coerced ordering value is worse than a rejected one."""
    with pytest.raises(ValidationError):
        _subscription_command("cmd_strict", plan_id="pro", subject_revision=value)


@pytest.mark.parametrize("value", [-1, MAX_SUBJECT_REVISION + 1, 2**70])
def test_subject_revision_rejects_out_of_range(value) -> None:
    """Bounded to BSON int64 so a revision can always be persisted."""
    with pytest.raises(ValidationError):
        _subscription_command("cmd_range", plan_id="pro", subject_revision=value)


@pytest.mark.parametrize("value", [0, 1, MAX_SUBJECT_REVISION])
def test_subject_revision_accepts_valid_bounds(value) -> None:
    command = _subscription_command("cmd_ok", plan_id="pro", subject_revision=value)
    assert command.subject_revision == value


def test_subject_revision_accepts_bson_int64() -> None:
    """Values read back from Mongo arrive as Int64, an int subclass."""
    from bson.int64 import Int64

    command = _subscription_command("cmd_i64", plan_id="pro", subject_revision=Int64(9))
    assert command.subject_revision == 9


# ── revision_field is a fixed authority field, not a customization point ─────


@pytest.mark.parametrize(
    "value",
    ["app_id", "user_id", "plan_id", "status", "updated_at", "billing_fulfillment",
     "_id", "  ", "a.b", "$set", "billing_revision "],
)
def test_revision_field_rejects_anything_but_the_canonical_name(value) -> None:
    """This field is read and written inside the same update as the assignment
    itself, so an arbitrary name could shadow identity/plan/status fields or
    introduce dotted paths. The only meaningful choice is on or off."""
    from mozaiksai.core.runtime.app.subscriptions_loader import (
        SubscriptionAssignmentStoreDef,
    )

    with pytest.raises(ValidationError):
        SubscriptionAssignmentStoreDef.model_validate(
            {"data_alias": "billing.subscriptions", "revision_field": value}
        )


@pytest.mark.parametrize("value", ["billing_revision", None])
def test_revision_field_accepts_on_and_off(value) -> None:
    from mozaiksai.core.runtime.app.subscriptions_loader import (
        SubscriptionAssignmentStoreDef,
    )

    store = SubscriptionAssignmentStoreDef.model_validate(
        {"data_alias": "billing.subscriptions", "revision_field": value}
    )
    assert store.revision_field == value


def test_revision_field_defaults_on_when_absent() -> None:
    from mozaiksai.core.runtime.app.subscriptions_loader import (
        SubscriptionAssignmentStoreDef,
    )

    store = SubscriptionAssignmentStoreDef.model_validate(
        {"data_alias": "billing.subscriptions"}
    )
    assert store.revision_field == "billing_revision"


# ── pre-fence command identity survives the upgrade ──────────────────────────


def test_unfenced_command_document_omits_the_revision_field() -> None:
    """The canonical document — and therefore the durable command hash — must
    be byte-identical to its pre-fence form for a command that never opted in.
    Serializing an explicit null would turn every pre-upgrade command into a
    content conflict on its first replay."""
    from mozaiksai.core.billing.fulfillment import _command_document, _command_hash

    unfenced = _subscription_command("cmd_unfenced", plan_id="pro")
    document = _command_document(unfenced)
    assert "subject_revision" not in document

    # The hash a pre-#497 build would have produced, reconstructed from the
    # same canonical rules without the field existing at all.
    baseline = dict(document)
    assert _command_hash(unfenced) == _json_hash(baseline)


def test_fenced_command_document_carries_the_revision() -> None:
    from mozaiksai.core.billing.fulfillment import _command_document

    fenced = _subscription_command("cmd_fenced", plan_id="pro", subject_revision=4)
    assert _command_document(fenced)["subject_revision"] == 4


@pytest.mark.asyncio
async def test_wallet_command_hash_is_unchanged_by_the_upgrade() -> None:
    """Wallet commands never carry a revision, so their identity must be
    untouched too."""
    from mozaiksai.core.billing.fulfillment import _command_document, _command_hash

    top_up = BillingFulfillmentCommand(
        command_id="cmd_topup_unfenced",
        event_type="token_top_up_paid",
        source="test",
        app_id="app_1",
        user_id="user_1",
        token_amount=500,
    )
    document = _command_document(top_up)
    assert "subject_revision" not in document
    assert _command_hash(top_up) == _json_hash(document)


# ── replay preserves the original disposition ────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("plan_id", "revision", "expected_status"),
    [("pro", 5, "applied"), ("free", 1, "superseded")],
)
async def test_replay_preserves_the_original_status(
    plan_id, revision, expected_status
) -> None:
    """`status` says THIS response is a replay; `original_status` says what is
    being replayed. Inferring the latter from counters is unsound — an applied
    command and a superseded one both report success with zero rejections."""
    database = _Database()
    store = BillingFulfillmentCommandStore(database=database)
    assignments = _Collection()
    ledger = TokenWalletLedger(database=database)
    service = BillingFulfillmentService(
        config=_subscriptions_config(),
        ledger=ledger,
        collection_resolver=lambda _alias: assignments,
        command_store=store,
    )

    await service.apply_durable(
        _subscription_command("cmd_head", plan_id="pro", subject_revision=4)
    )
    command = _subscription_command(
        f"cmd_{expected_status}", plan_id=plan_id, subject_revision=revision
    )
    first = await service.apply_durable(command)
    replay = await service.apply_durable(command)

    assert first.status == expected_status
    assert first.original_status is None, "a fresh application is not a replay"
    assert replay.status == "replayed"
    assert replay.replayed is True
    assert replay.original_status == expected_status
    assert replay.success is first.success


@pytest.mark.asyncio
async def test_replay_of_a_rejected_command_stays_rejected() -> None:
    database = _Database()
    store = BillingFulfillmentCommandStore(database=database)
    assignments = _Collection()
    service = BillingFulfillmentService(
        config=_subscriptions_config(),
        ledger=TokenWalletLedger(database=database),
        collection_resolver=lambda _alias: assignments,
        command_store=store,
    )
    unknown_plan = BillingFulfillmentCommand(
        command_id="cmd_unknown_plan",
        event_type="subscription_activated",
        source="test",
        app_id="app_1",
        user_id="user_1",
        plan_id="does_not_exist",
    )

    first = await service.apply_durable(unknown_plan)
    replay = await service.apply_durable(unknown_plan)

    assert first.status in {"rejected", "partial"}
    assert replay.status == "replayed"
    assert replay.original_status == first.status
    assert replay.success is False


# ── allowance fence is generic and opt-in ────────────────────────────────────


@pytest.mark.asyncio
async def test_ledger_movement_without_a_subject_is_unfenced() -> None:
    """Callers with no ordering authority behave exactly as before."""
    database = _Database()
    ledger = TokenWalletLedger(database=database)

    first = await ledger.credit(
        app_id="app_1", user_id="user_1", amount=50, idempotency_key="k1"
    )
    second = await ledger.credit(
        app_id="app_1", user_id="user_1", amount=50, idempotency_key="k2"
    )

    assert first.status == "applied" and second.status == "applied"
    balance = await ledger.query_balance(app_id="app_1", user_id="user_1")
    assert balance["balance"] == 100
    assert "subject_revisions" not in balance, (
        "ordering state must never reach the public balance surface"
    )


# ── the revision field is reserved against every other mapping ───────────────


@pytest.mark.parametrize(
    "mapping",
    [
        "app_id_field", "tenant_id_field", "workspace_id_field", "user_id_field",
        "plan_id_field", "status_field", "starts_at_field", "expires_at_field",
        "capabilities_field", "plan_snapshot_field",
    ],
)
def test_no_mapping_may_target_the_reserved_revision_field(mapping) -> None:
    """An identity or business mapping aimed at the ordering field would let
    ordinary writes clobber the fence — caught at load, not at runtime."""
    from mozaiksai.core.runtime.app.subscriptions_loader import (
        SubscriptionAssignmentStoreDef,
    )

    with pytest.raises(ValidationError, match="reserved revision field"):
        SubscriptionAssignmentStoreDef.model_validate(
            {"data_alias": "billing.subscriptions", mapping: "billing_revision"}
        )


@pytest.mark.parametrize("value", ["billing_revision.inner", "billing_revision.a.b"])
def test_mappings_nested_under_the_reserved_field_are_rejected(value) -> None:
    from mozaiksai.core.runtime.app.subscriptions_loader import (
        SubscriptionAssignmentStoreDef,
    )

    with pytest.raises(ValidationError, match="reserved revision field"):
        SubscriptionAssignmentStoreDef.model_validate(
            {"data_alias": "billing.subscriptions", "plan_id_field": value}
        )


def test_reserved_field_check_is_skipped_when_fencing_is_off() -> None:
    """With fencing off there is no ordering authority to protect."""
    from mozaiksai.core.runtime.app.subscriptions_loader import (
        SubscriptionAssignmentStoreDef,
    )

    store = SubscriptionAssignmentStoreDef.model_validate(
        {
            "data_alias": "billing.subscriptions",
            "revision_field": None,
            "plan_id_field": "billing_revision",
        }
    )
    assert store.plan_id_field == "billing_revision"


def test_unrelated_mappings_remain_valid_with_fencing_on() -> None:
    from mozaiksai.core.runtime.app.subscriptions_loader import (
        SubscriptionAssignmentStoreDef,
    )

    store = SubscriptionAssignmentStoreDef.model_validate(
        {
            "data_alias": "billing.subscriptions",
            "user_id_field": "scope.user",
            "plan_id_field": "billing_plan",
        }
    )
    assert store.revision_field == "billing_revision"
    assert store.user_id_field == "scope.user"


# ── configured dotted paths resolve the same way everywhere ──────────────────


def test_dotted_subject_paths_resolve_for_typed_comparison() -> None:
    """Reading `document["scope.user"]` instead of walking into `scope.user`
    reports a mismatch for a row that genuinely is the same subject."""
    from mozaiksai.core.billing.fulfillment import _resolve_document_path

    document = {"app_id": "app_1", "scope": {"user": "u-123", "tenant": None}}
    assert _resolve_document_path(document, "scope.user") == "u-123"
    assert _resolve_document_path(document, "scope.tenant") is None
    assert _resolve_document_path(document, "app_id") == "app_1"
    assert _resolve_document_path(document, "scope.missing") is None
    assert _resolve_document_path(document, "app_id.nested") is None


def test_same_subject_under_dotted_mapping_is_not_a_collision() -> None:
    from mozaiksai.core.billing.fulfillment import BillingFulfillmentService

    existing = {"app_id": "app_1", "scope": {"user": "u-123"}}
    query = {"app_id": "app_1", "scope.user": "u-123"}
    assert BillingFulfillmentService._assignment_subject_mismatch(
        existing, query=query
    ) is None


def test_different_subject_under_dotted_mapping_is_a_collision() -> None:
    from mozaiksai.core.billing.fulfillment import BillingFulfillmentService

    existing = {"app_id": "app_1", "scope": {"user": None}}
    query = {"app_id": "app_1", "scope.user": "None"}
    assert BillingFulfillmentService._assignment_subject_mismatch(
        existing, query=query
    ) == "scope.user"


# ── terminal-status aggregation precedence ───────────────────────────────────


@pytest.mark.parametrize(
    ("effects", "expected"),
    [
        ([("applied", None), ("applied", None)], "applied"),
        ([("applied", None), ("skipped", "no_token_allowances")], "applied"),
        ([("rejected", "boom"), ("applied", None)], "partial"),
        ([("rejected", "boom"), ("rejected", "boom")], "rejected"),
        ([("skipped", "stale_revision"), ("skipped", "stale_revision")], "superseded"),
        # An earlier effect really did apply, but a newer revision invalidated
        # the rest of the command: the command as a whole was overtaken.
        ([("applied", None), ("rejected", "stale_subject_revision")], "partial"),
        ([("applied", None), ("skipped", "stale_subject_revision")], "superseded"),
        ([("applied", None), ("skipped", "stale_revision")], "superseded"),
    ],
)
def test_status_aggregation_precedence(effects, expected) -> None:
    from mozaiksai.core.billing.fulfillment import (
        BillingFulfillmentEffectResult,
        _status_from_effects,
    )

    results = [
        BillingFulfillmentEffectResult(effect="plan_allowances", status=status, reason=reason)
        for status, reason in effects
    ]
    assert _status_from_effects(results) == expected


# ── one effective fencing decision ───────────────────────────────────────────


def test_fencing_requires_both_command_and_store_authority() -> None:
    """Deciding per effect produced a half-fenced system: an app that opted out
    still had wallet allowances refused as stale while its assignment took the
    older revision."""
    from mozaiksai.core.billing.fulfillment import BillingFulfillmentService
    from mozaiksai.core.runtime.app.subscriptions_loader import SubscriptionsConfig

    def _service_with(revision_field):
        config = SubscriptionsConfig.model_validate(
            {
                **_subscriptions_config().model_dump(mode="json"),
                "assignment_store": {
                    "data_alias": "billing.subscriptions",
                    "user_id_field": "user_id",
                    "revision_field": revision_field,
                },
            }
        )
        return BillingFulfillmentService(config=config, ledger=None)

    fenced = _subscription_command("cmd_f", plan_id="pro", subject_revision=3)
    unfenced = _subscription_command("cmd_u", plan_id="pro")

    on = _service_with("billing_revision")
    off = _service_with(None)

    assert on._fencing_enabled(fenced) is True
    assert on._fencing_enabled(unfenced) is False
    assert off._fencing_enabled(fenced) is False, "store opt-out disables everything"
    assert off._subject_key(fenced) is None, "no wallet authority when opted out"
    assert on._subject_key(fenced) is not None


# ── designer literal matches the runtime contract exactly ────────────────────


def test_designer_revision_field_compiles_to_the_runtime_literal() -> None:
    """A `str | null` schema could generate a value the runtime rejects, so the
    designer declares the same finite literal — and it must actually COMPILE
    through the canonical structured-output builder, not merely parse as YAML."""
    from pathlib import Path

    import yaml as _yaml

    from mozaiksai.core.workflow.outputs.structured import build_models_from_config

    schema = _yaml.safe_load(
        (
            Path(__file__).resolve().parents[1]
            / "factory_app/workflows/SubscriptionContractDesigner/structured_outputs.yaml"
        ).read_text(encoding="utf-8")
    )
    field = schema["models"]["AssignmentStore"]["fields"]["revision_field"]
    assert field["type"] == "union"
    assert field["variants"] == ["BillingRevisionField", "null"]
    assert schema["models"]["BillingRevisionField"] == {
        "type": "literal",
        "values": ["billing_revision"],
    }

    models = build_models_from_config(schema["models"])
    store = models["AssignmentStore"]
    base = {
        "data_alias": "billing.subscriptions",
        "app_id_field": "app_id",
        "plan_id_field": "plan_id",
        "status_field": "status",
        "active_statuses": ["active"],
    }
    assert store(**base, revision_field="billing_revision").revision_field is not None
    assert store(**base, revision_field=None).revision_field is None
    with pytest.raises(ValidationError):
        store(**base, revision_field="other_field")


@pytest.mark.parametrize("value", ["other_field", "billing_revisions", "", "  "])
def test_runtime_rejects_any_other_revision_field_value(value) -> None:
    from mozaiksai.core.runtime.app.subscriptions_loader import (
        SubscriptionAssignmentStoreDef,
    )

    with pytest.raises(ValidationError):
        SubscriptionAssignmentStoreDef.model_validate(
            {"data_alias": "billing.subscriptions", "revision_field": value}
        )


# ── an existing index that already provides the guarantee is accepted ────────


class _IndexInformation:
    def __init__(self, information: dict[str, dict]) -> None:
        self._information = information

    async def index_information(self) -> dict[str, dict]:
        return self._information


_REQUIRED_SUBJECT_KEYS = [("app_id", 1), ("tenant_id", 1), ("user_id", 1)]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("definition", "accepted"),
    [
        ({"key": _REQUIRED_SUBJECT_KEYS, "unique": True}, True),
        # Same guarantee, different name — the name is not the contract.
        ({"key": _REQUIRED_SUBJECT_KEYS, "unique": True, "v": 2}, True),
        ({"key": _REQUIRED_SUBJECT_KEYS, "unique": True,
          "collation": {"locale": "simple"}}, True),
        # Not unique at all.
        ({"key": _REQUIRED_SUBJECT_KEYS}, False),
        # A different subject definition.
        ({"key": [("app_id", 1), ("user_id", 1)], "unique": True}, False),
        # Right fields, wrong order — a compound index is ordered.
        ({"key": [("user_id", 1), ("tenant_id", 1), ("app_id", 1)], "unique": True}, False),
        # Descending is a different index.
        ({"key": [("app_id", 1), ("tenant_id", 1), ("user_id", -1)], "unique": True}, False),
        # Narrowed coverage: subjects outside the filter may still duplicate.
        ({"key": _REQUIRED_SUBJECT_KEYS, "unique": True, "sparse": True}, False),
        ({"key": _REQUIRED_SUBJECT_KEYS, "unique": True,
          "partialFilterExpression": {"status": "active"}}, False),
        # A collation changes which values compare equal.
        ({"key": _REQUIRED_SUBJECT_KEYS, "unique": True,
          "collation": {"locale": "en", "strength": 2}}, False),
    ],
)
async def test_existing_index_equivalence(definition, accepted) -> None:
    from mozaiksai.core.billing.fulfillment import BillingFulfillmentService

    collection = _IndexInformation({"_id_": {"key": [("_id", 1)]}, "candidate": definition})
    assert (
        await BillingFulfillmentService._subject_uniqueness_already_guaranteed(
            collection, _REQUIRED_SUBJECT_KEYS
        )
        is accepted
    )


@pytest.mark.asyncio
async def test_a_collection_that_cannot_report_indexes_is_not_assumed_guaranteed() -> None:
    from mozaiksai.core.billing.fulfillment import BillingFulfillmentService

    class _Silent:
        pass

    assert (
        await BillingFulfillmentService._subject_uniqueness_already_guaranteed(
            _Silent(), _REQUIRED_SUBJECT_KEYS
        )
        is False
    )


# ── the ordering key distinguishes subjects the assignment id conflates ──────


def test_subject_identity_preserves_value_types() -> None:
    """`_assignment_id` string-formats scope values, so a null tenant and the
    literal string "None" produce the same id. Ordering authority keyed that
    way would let one subject fence the other out."""
    from mozaiksai.core.billing.fulfillment import _assignment_id, _subject_identity

    null_scope = {"app_id": "app_1", "tenant_id": None, "user_id": "u1"}
    forged_scope = {"app_id": "app_1", "tenant_id": "None", "user_id": "u1"}

    assert _assignment_id("app_1", null_scope) == _assignment_id("app_1", forged_scope)
    assert _subject_identity("app_1", null_scope) != _subject_identity(
        "app_1", forged_scope
    )
    # And it stays stable for the same subject regardless of key order.
    assert _subject_identity("app_1", null_scope) == _subject_identity(
        "app_1", {"user_id": "u1", "app_id": "app_1", "tenant_id": None}
    )
