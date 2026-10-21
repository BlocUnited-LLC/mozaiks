"""Paid token command recovery against Mongo's atomic command and wallet writes."""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator
from uuid import uuid4

import pytest

from mozaiksai.core.billing.fulfillment import (
    COMMANDS_COLLECTION,
    BillingFulfillmentCommand,
    BillingFulfillmentCommandStore,
    BillingFulfillmentConflictError,
    BillingFulfillmentPendingError,
    BillingFulfillmentService,
)
from mozaiksai.core.core_config import close_mongo_client, get_mongo_client
from mozaiksai.core.runtime.app.subscriptions_loader import SubscriptionsConfig
from mozaiksai.core.tokens.wallet import TokenWalletLedger


def _mongo_reachable() -> bool:
    uri = (os.getenv("MONGO_URI") or "").strip()
    if not uri:
        return False
    try:
        from pymongo import MongoClient

        MongoClient(uri, serverSelectionTimeoutMS=2000).admin.command("ping")
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _mongo_reachable(), reason="requires a reachable MongoDB via MONGO_URI"
)


@pytest.fixture()
async def setup() -> AsyncIterator[
    tuple[
        BillingFulfillmentService,
        BillingFulfillmentCommandStore,
        TokenWalletLedger,
        BillingFulfillmentCommand,
    ]
]:
    close_mongo_client()
    client = get_mongo_client()
    suffix = uuid4().hex
    database = client[f"mozaiks_billing_recovery_{suffix}"]
    config = SubscriptionsConfig.model_validate(
        {
            "schema_version": "mozaiks.subscriptions.v1",
            "label": "Recovery proof",
            "default_plan_id": "free",
            "token_wallets": [
                {
                    "wallet_id": "ai_tokens",
                    "label": "AI tokens",
                    "unit": "tokens",
                    "usage_meter_id": "ai_tokens",
                    "scope": "user",
                }
            ],
            "plans": [{"plan_id": "free", "label": "Free", "capabilities": []}],
        }
    )
    store = BillingFulfillmentCommandStore(database=database)
    ledger = TokenWalletLedger(database=database)
    service = BillingFulfillmentService(config=config, ledger=ledger, command_store=store)
    command = BillingFulfillmentCommand(
        command_id=f"cmd_{suffix}",
        event_type="token_top_up_paid",
        source="test",
        app_id=f"app_{suffix}",
        user_id="buyer_1",
        tenant_id="tenant_1",
        workspace_id="workspace_1",
        token_amount=500,
    )
    yield service, store, ledger, command
    await client.drop_database(database.name)
    close_mongo_client()


@pytest.mark.asyncio
async def test_crash_after_credit_recovers_without_a_second_mint(setup) -> None:
    service, store, ledger, command = setup
    assert (await store.start(command)).state == "started"
    assert (await service._apply_wallet_credit(command)).status == "applied"

    original_credit = ledger.credit

    async def forbidden_credit(**_kwargs):
        raise AssertionError("recovery must never call the wallet credit writer")

    ledger.credit = forbidden_credit
    try:
        recovered = await service.apply_durable(command)
        replay = await service.apply_durable(command)
    finally:
        ledger.credit = original_credit

    assert recovered.status == "applied"
    assert recovered.effects[0].details["recovered_from_ledger"] is True
    assert replay.status == "replayed"
    assert replay.original_status == "applied"
    balance = await ledger.query_balance(
        app_id=command.app_id,
        user_id=command.user_id,
        tenant_id=command.tenant_id,
        preferred_scope="user",
    )
    assert (balance["balance"], balance["entry_count"]) == (500, 1)
    assert (await store.list_records(command_id=command.command_id))[0]["status"] == "applied"


@pytest.mark.asyncio
async def test_no_ledger_credit_stays_pending(setup) -> None:
    service, store, ledger, command = setup
    assert (await store.start(command)).state == "started"
    with pytest.raises(BillingFulfillmentPendingError):
        await service.apply_durable(command)
    assert (await store.list_records(command_id=command.command_id))[0]["status"] == "pending"
    balance = await ledger.query_balance(
        app_id=command.app_id,
        user_id=command.user_id,
        tenant_id=command.tenant_id,
        preferred_scope="user",
    )
    assert balance["entry_count"] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("tamper", ["amount", "status", "balance_marker", "command", "identity"])
async def test_inconsistent_credit_or_command_stays_pending(setup, tamper: str) -> None:
    service, store, ledger, command = setup
    assert (await store.start(command)).state == "started"
    assert (await service._apply_wallet_credit(command)).status == "applied"
    balances, entries = await ledger._collections()
    entry = (
        await ledger.list_entries(
            app_id=command.app_id,
            user_id=command.user_id,
            tenant_id=command.tenant_id,
            preferred_scope="user",
        )
    )[0]
    database = await ledger._db()
    commands = database[COMMANDS_COLLECTION]
    if tamper == "amount":
        await entries.update_one({"_id": entry["entry_id"]}, {"$set": {"amount": 999}})
    elif tamper == "status":
        await entries.update_one({"_id": entry["entry_id"]}, {"$set": {"status": "pending"}})
    elif tamper == "balance_marker":
        await balances.update_one(
            {"_id": entry["balance_id"]}, {"$pull": {"applied_entry_ids": entry["entry_id"]}}
        )
    elif tamper == "command":
        await commands.update_one(
            {"_id": command.command_id}, {"$set": {"command.token_amount": 999}}
        )
    else:
        await commands.update_one(
            {"_id": command.command_id}, {"$set": {"workspace_id": "other_workspace"}}
        )
    with pytest.raises(BillingFulfillmentPendingError):
        await service.apply_durable(command)
    assert (await store.list_records(command_id=command.command_id))[0]["status"] == "pending"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("credit_user", "credit_tenant"),
    [("buyer_2", "tenant_1"), ("buyer_1", "tenant_2")],
)
async def test_other_subject_credit_does_not_recover_command(
    setup, credit_user: str, credit_tenant: str
) -> None:
    service, store, ledger, command = setup
    assert (await store.start(command)).state == "started"
    await ledger.credit(
        app_id=command.app_id,
        wallet_id=command.wallet_id,
        amount=500,
        idempotency_key=(
            f"billing_fulfillment:{command.command_id}:wallet_credit:{command.wallet_id}"
        ),
        user_id=credit_user,
        tenant_id=credit_tenant,
        preferred_scope="user",
        source="billing_fulfillment",
        reason="Paid token credit",
        metadata=service._wallet_metadata(command),
    )
    with pytest.raises(BillingFulfillmentPendingError):
        await service.apply_durable(command)
    assert (await store.list_records(command_id=command.command_id))[0]["status"] == "pending"


@pytest.mark.asyncio
async def test_conflicting_retry_never_recovers_paid_command(setup) -> None:
    service, store, _ledger, command = setup
    assert (await store.start(command)).state == "started"
    assert (await service._apply_wallet_credit(command)).status == "applied"
    with pytest.raises(BillingFulfillmentConflictError):
        await service.apply_durable(command.model_copy(update={"token_amount": 600}))
    assert (await store.list_records(command_id=command.command_id))[0]["status"] == "pending"


@pytest.mark.asyncio
async def test_granted_credit_is_outside_paid_top_up_recovery(setup) -> None:
    service, store, _ledger, command = setup
    grant = command.model_copy(update={"event_type": "token_credit_granted"})
    assert (await store.start(grant)).state == "started"
    assert (await service._apply_wallet_credit(grant)).status == "applied"
    with pytest.raises(BillingFulfillmentPendingError):
        await service.apply_durable(grant)
    assert (await store.list_records(command_id=grant.command_id))[0]["status"] == "pending"


@pytest.mark.asyncio
async def test_concurrent_recovery_has_one_terminal_result_and_one_credit(setup) -> None:
    service, store, ledger, command = setup
    assert (await store.start(command)).state == "started"
    assert (await service._apply_wallet_credit(command)).status == "applied"
    original_lookup = ledger.find_applied_credit
    both_waiting = asyncio.Event()
    arrivals = 0

    async def paired_lookup(**kwargs):
        nonlocal arrivals
        arrivals += 1
        if arrivals == 2:
            both_waiting.set()
        await asyncio.wait_for(both_waiting.wait(), timeout=3)
        return await original_lookup(**kwargs)

    ledger.find_applied_credit = paired_lookup
    results = await asyncio.gather(service.apply_durable(command), service.apply_durable(command))
    assert {result.status for result in results} == {"applied", "replayed"}
    assert all(result.success for result in results)
    assert all(result.effects[0].status == "applied" for result in results)
    balance = await ledger.query_balance(
        app_id=command.app_id,
        user_id=command.user_id,
        tenant_id=command.tenant_id,
        preferred_scope="user",
    )
    assert (balance["balance"], balance["entry_count"]) == (500, 1)
    assert (await store.list_records(command_id=command.command_id))[0]["status"] == "applied"
