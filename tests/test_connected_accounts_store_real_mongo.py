"""The connected-accounts store against a real MongoDB.

A flow's state is claimed exactly once even when callbacks race, connection
records are unique per app, user and provider, and expired flows carry a TTL
index. Opt in with MOZAIKS_RUN_REAL_MONGO_TESTS=1 (MONGO_URI selects the server).
"""

from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from motor.motor_asyncio import AsyncIOMotorClient

from mozaiksai.core.connected_accounts.store import ConnectedAccountStore

pytestmark = pytest.mark.skipif(
    os.environ.get("MOZAIKS_RUN_REAL_MONGO_TESTS") != "1",
    reason="set MOZAIKS_RUN_REAL_MONGO_TESTS=1 for real Mongo store tests",
)


@pytest.fixture
async def store():
    client = AsyncIOMotorClient(os.environ.get("MONGO_URI", "mongodb://127.0.0.1:27017"))
    database = client[f"mozaiks_cat_{uuid4().hex[:16]}"]

    async def collection(name: str):
        return database[name]

    try:
        yield ConnectedAccountStore(collection_factory=collection), database
    finally:
        await client.drop_database(database.name)
        client.close()


def _flow(state_digest: str, flow_id: str, *, user: str = "alice", now: datetime) -> dict:
    return {
        "state_digest": state_digest, "flow_id": flow_id, "owner_user_id": user, "app_id": "app-one",
        "connection_id": "google_calendar", "provider": "google", "binding_digest": "b", "binding_cookie": "c",
        "code_verifier": "v", "requested_scopes": [], "redirect_uri": "https://h/cb", "status": "pending",
        "created_at": now, "updated_at": now, "expires_at": now + timedelta(minutes=10),
    }


async def test_racing_callbacks_claim_a_flow_once(store) -> None:
    accounts, _ = store
    now = datetime.now(UTC)
    await accounts.create_flow(_flow("state-1", "flow-1", now=now))
    claims = await asyncio.gather(*(accounts.claim_flow(state_digest="state-1", now=now) for _ in range(8)))
    assert sum(claim is not None for claim in claims) == 1
    assert await accounts.claim_flow(state_digest="state-1", now=now) is None


async def test_an_expired_flow_cannot_be_claimed_and_has_a_ttl(store) -> None:
    accounts, database = store
    now = datetime.now(UTC)
    await accounts.create_flow(_flow("state-2", "flow-2", now=now - timedelta(minutes=30)))
    assert await accounts.claim_flow(state_digest="state-2", now=now) is None
    indexes = await database["ConnectedAccountFlows"].index_information()
    assert indexes["connected_account_flow_ttl"]["expireAfterSeconds"] == 0
    assert indexes["connected_account_flow_state"]["unique"] is True


async def test_finishing_a_flow_drops_the_verifier(store) -> None:
    accounts, database = store
    now = datetime.now(UTC)
    await accounts.create_flow(_flow("state-3", "flow-3", now=now))
    await accounts.claim_flow(state_digest="state-3", now=now)
    await accounts.finish_flow(flow_id="flow-3", status="connected", message="ok", now=now, keep_for=timedelta(minutes=5))
    stored = await database["ConnectedAccountFlows"].find_one({"flow_id": "flow-3"})
    assert stored["status"] == "connected" and "code_verifier" not in stored


async def test_one_record_per_app_user_and_provider(store) -> None:
    accounts, database = store
    now = datetime.now(UTC)
    for scopes in (["a"], ["a", "b"]):
        await accounts.save_connection(
            app_id="app-one", user_id="alice", provider="google", granted_scopes=scopes,
            account_email="alice@gmail.test", account_subject="sub", now=now,
        )
    await accounts.save_connection(
        app_id="app-two", user_id="alice", provider="google", granted_scopes=["a"],
        account_email=None, account_subject=None, now=now,
    )
    rows = await database["ConnectedAccounts"].find({"user_id": "alice"}).to_list(length=None)
    assert sorted((row["app_id"], tuple(row["granted_scopes"])) for row in rows) == [
        ("app-one", ("a", "b")), ("app-two", ("a",)),
    ]
    assert (await database["ConnectedAccounts"].index_information())["connected_account_identity"]["unique"] is True
    assert await accounts.count_connections(app_id="app-one") == {"google": {"connected": 1, "needs_reconnect": 0}}
    await accounts.mark_needs_reconnect(app_id="app-one", user_id="alice", provider="google", now=now)
    assert await accounts.count_connections(app_id="app-one") == {"google": {"connected": 0, "needs_reconnect": 1}}


async def test_the_offering_is_per_app(store) -> None:
    accounts, _ = store
    now = datetime.now(UTC)
    await accounts.set_offered(app_id="app-one", connection_id="gmail_send", offered=False, now=now)
    await accounts.set_offered(app_id="app-one", connection_id="google_calendar", offered=True, now=now)
    assert await accounts.withheld_connections(app_id="app-one") == frozenset({"gmail_send"})
    assert await accounts.withheld_connections(app_id="app-two") == frozenset()
    await accounts.set_offered(app_id="app-one", connection_id="gmail_send", offered=True, now=now)
    assert await accounts.withheld_connections(app_id="app-one") == frozenset()


async def test_pending_flows_per_user_are_bounded_and_deleted_with_the_account(store) -> None:
    accounts, database = store
    now = datetime.now(UTC)
    for index in range(8):
        await accounts.create_flow(_flow(f"s{index}", f"f{index}", now=now + timedelta(seconds=index)))
    pending = await database["ConnectedAccountFlows"].count_documents({"owner_user_id": "alice"})
    assert pending == 5
    assert await accounts.delete_flows_for_user(app_id="app-one", user_id="alice") == 5


async def test_the_vault_keeps_a_users_grant_encrypted_under_an_opaque_name(store, monkeypatch) -> None:
    from mozaiksai.core.connected_accounts.service import ConnectedAccountsService
    from mozaiksai.core.secrets.connector_vault import MongoConnectorVaultBackend

    _, database = store
    vault = MongoConnectorVaultBackend()

    async def collection():
        return database["ConnectorSecrets"]

    monkeypatch.setattr(vault, "_collection", collection)
    monkeypatch.setenv("MOZAIKS_CONNECTOR_SECRET_KEY", "a" * 64)
    scope_id = ConnectedAccountsService(app_id="app-one", app_root=".")._grant_scope_id("alice@example.com")
    stored = await vault.store_secret(
        scope="user", scope_id=scope_id, service="google",
        secret_value='{"refresh_token": "1//secret-refresh"}', ttl_days=400,
    )
    assert stored["success"] is True
    [document] = await database["ConnectorSecrets"].find({}).to_list(length=None)
    dump = str(document)
    assert "secret-refresh" not in dump and "alice" not in dump and "example.com" not in dump
    found = await vault.get_secret(scope="user", scope_id=scope_id, service="google")
    assert found["status"] == "found" and "secret-refresh" in found["secret_value"]
    await vault.delete_secret(scope="user", scope_id=scope_id, service="google")
    assert (await vault.get_secret(scope="user", scope_id=scope_id, service="google"))["status"] == "not_found"
