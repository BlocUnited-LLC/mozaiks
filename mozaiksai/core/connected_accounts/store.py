"""Records of connected accounts and of authorization flows in progress.

Nothing secret is kept here: a connection record says which user connected
which provider account and which scopes they granted. The refresh token lives
in the connector vault (scope ``user``) and access tokens only in process
memory. A flow record holds the PKCE verifier until the provider's callback
claims it once; flows live in the database, not in process memory, so a
callback that reaches another replica of the app still completes.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from mozaiksai.core.data.persistence.namespaces import SYSTEM_DATABASE, PlatformCollections

MAX_PENDING_FLOWS_PER_USER = 5

CollectionFactory = Callable[[str], Awaitable[Any]]


async def _mongo_collection(name: str) -> Any:
    from mozaiksai.core.data.persistence import AG2PersistenceManager

    manager = AG2PersistenceManager()
    await manager.persistence._ensure_client()  # noqa: SLF001
    client = manager.persistence.client
    if client is None:
        raise RuntimeError("Mongo client not initialized")
    return client[SYSTEM_DATABASE][name]


def _utc(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    return None


class ConnectedAccountStore:
    def __init__(self, collection_factory: CollectionFactory | None = None) -> None:
        self._collection_factory = collection_factory or _mongo_collection
        self._indexed: set[str] = set()

    async def _accounts(self) -> Any:
        collection = await self._collection_factory(PlatformCollections.CONNECTED_ACCOUNTS)
        if PlatformCollections.CONNECTED_ACCOUNTS not in self._indexed:
            await collection.create_index(
                [("app_id", 1), ("user_id", 1), ("provider", 1)], unique=True, name="connected_account_identity",
            )
            self._indexed.add(PlatformCollections.CONNECTED_ACCOUNTS)
        return collection

    async def _settings(self) -> Any:
        collection = await self._collection_factory(PlatformCollections.CONNECTED_ACCOUNT_SETTINGS)
        if PlatformCollections.CONNECTED_ACCOUNT_SETTINGS not in self._indexed:
            await collection.create_index(
                [("app_id", 1), ("connection_id", 1)], unique=True, name="connected_account_setting_identity",
            )
            self._indexed.add(PlatformCollections.CONNECTED_ACCOUNT_SETTINGS)
        return collection

    async def _flows(self) -> Any:
        collection = await self._collection_factory(PlatformCollections.CONNECTED_ACCOUNT_FLOWS)
        if PlatformCollections.CONNECTED_ACCOUNT_FLOWS not in self._indexed:
            await collection.create_index([("state_digest", 1)], unique=True, name="connected_account_flow_state")
            await collection.create_index([("flow_id", 1)], unique=True, name="connected_account_flow_id")
            await collection.create_index([("expires_at", 1)], expireAfterSeconds=0, name="connected_account_flow_ttl")
            self._indexed.add(PlatformCollections.CONNECTED_ACCOUNT_FLOWS)
        return collection

    # -- connections -------------------------------------------------------------------

    async def save_connection(
        self,
        *,
        app_id: str,
        user_id: str,
        provider: str,
        granted_scopes: list[str],
        account_email: str | None,
        account_subject: str | None,
        now: datetime,
    ) -> None:
        accounts = await self._accounts()
        await accounts.update_one(
            {"app_id": app_id, "user_id": user_id, "provider": provider},
            {
                "$set": {
                    "app_id": app_id,
                    "user_id": user_id,
                    "provider": provider,
                    "granted_scopes": sorted(set(granted_scopes)),
                    "account_email": account_email,
                    "account_subject": account_subject,
                    "status": "connected",
                    "updated_at": now,
                },
                "$setOnInsert": {"connected_at": now},
            },
            upsert=True,
        )

    async def get_connection(self, *, app_id: str, user_id: str, provider: str) -> dict[str, Any] | None:
        accounts = await self._accounts()
        record: dict[str, Any] | None = await accounts.find_one({"app_id": app_id, "user_id": user_id, "provider": provider})
        return record

    async def list_connections(self, *, app_id: str, user_id: str) -> list[dict[str, Any]]:
        accounts = await self._accounts()
        return list(await accounts.find({"app_id": app_id, "user_id": user_id}).to_list(length=None))

    async def mark_needs_reconnect(self, *, app_id: str, user_id: str, provider: str, now: datetime) -> None:
        accounts = await self._accounts()
        await accounts.update_one(
            {"app_id": app_id, "user_id": user_id, "provider": provider},
            {"$set": {"status": "needs_reconnect", "updated_at": now}},
        )

    async def update_granted_scopes(
        self, *, app_id: str, user_id: str, provider: str, granted_scopes: list[str], now: datetime,
    ) -> None:
        accounts = await self._accounts()
        await accounts.update_one(
            {"app_id": app_id, "user_id": user_id, "provider": provider},
            {"$set": {"granted_scopes": sorted(set(granted_scopes)), "updated_at": now, "status": "connected"}},
        )

    async def delete_connection(self, *, app_id: str, user_id: str, provider: str) -> bool:
        accounts = await self._accounts()
        result = await accounts.delete_one({"app_id": app_id, "user_id": user_id, "provider": provider})
        return bool(getattr(result, "deleted_count", 0))

    async def count_connections(self, *, app_id: str) -> dict[str, dict[str, int]]:
        """Connected and needs-reconnect counts per provider, for the app's admins. No identities."""
        accounts = await self._accounts()
        counts: dict[str, dict[str, int]] = {}
        for row in await accounts.find({"app_id": app_id}, {"provider": 1, "status": 1}).to_list(length=None):
            bucket = counts.setdefault(str(row.get("provider") or ""), {"connected": 0, "needs_reconnect": 0})
            status = "needs_reconnect" if row.get("status") == "needs_reconnect" else "connected"
            bucket[status] += 1
        return counts

    # -- the app's admins' offering ------------------------------------------------------

    async def withheld_connections(self, *, app_id: str) -> frozenset[str]:
        """Declared connections the app's admins have stopped offering. Everything else is offered."""
        settings = await self._settings()
        rows = await settings.find({"app_id": app_id, "offered": False}, {"connection_id": 1}).to_list(length=None)
        return frozenset(str(row.get("connection_id") or "") for row in rows)

    async def set_offered(self, *, app_id: str, connection_id: str, offered: bool, now: datetime) -> None:
        settings = await self._settings()
        await settings.update_one(
            {"app_id": app_id, "connection_id": connection_id},
            {"$set": {"app_id": app_id, "connection_id": connection_id, "offered": offered, "updated_at": now}},
            upsert=True,
        )

    # -- flows -------------------------------------------------------------------------

    async def create_flow(self, flow: dict[str, Any]) -> None:
        flows = await self._flows()
        owner = flow["owner_user_id"]
        pending = sorted(
            await flows.find({"owner_user_id": owner, "app_id": flow["app_id"], "status": "pending"}).to_list(length=None),
            key=lambda row: _utc(row.get("created_at")) or datetime.min.replace(tzinfo=UTC),
        )
        # One account cannot grow the flow store without bound: drop its oldest pending flows.
        for stale in pending[: max(0, len(pending) - MAX_PENDING_FLOWS_PER_USER + 1)]:
            await flows.delete_one({"flow_id": stale["flow_id"]})
        await flows.insert_one(dict(flow))

    async def claim_flow(self, *, state_digest: str, now: datetime) -> dict[str, Any] | None:
        """Take a pending, unexpired flow exactly once."""
        flows = await self._flows()
        from pymongo import ReturnDocument

        claimed: dict[str, Any] | None = await flows.find_one_and_update(
            {"state_digest": state_digest, "status": "pending", "expires_at": {"$gt": now}},
            {"$set": {"status": "completing", "updated_at": now}},
            return_document=ReturnDocument.AFTER,
        )
        return claimed

    async def finish_flow(self, *, flow_id: str, status: str, message: str, now: datetime, keep_for: timedelta) -> None:
        flows = await self._flows()
        await flows.update_one(
            {"flow_id": flow_id},
            {
                "$set": {"status": status, "message": message, "updated_at": now, "expires_at": now + keep_for},
                "$unset": {"code_verifier": ""},
            },
        )

    async def get_flow(self, *, flow_id: str, owner_user_id: str, app_id: str) -> dict[str, Any] | None:
        flows = await self._flows()
        flow: dict[str, Any] | None = await flows.find_one({"flow_id": flow_id, "owner_user_id": owner_user_id, "app_id": app_id})
        return flow

    async def delete_flows_for_user(self, *, app_id: str, user_id: str) -> int:
        flows = await self._flows()
        result = await flows.delete_many({"app_id": app_id, "owner_user_id": user_id})
        return int(getattr(result, "deleted_count", 0) or 0)


__all__ = ["MAX_PENDING_FLOWS_PER_USER", "CollectionFactory", "ConnectedAccountStore"]
