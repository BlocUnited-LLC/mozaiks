"""Durable reaction idempotency ledger.

Provides restart-safe duplicate suppression for module reactions that declare
an idempotency_key.  The canonical execution path remains unchanged:

    UnifiedEventDispatcher → ModuleEventRouter

This store is an optional persistence seam injected into the router.  It
complements the in-memory idempotency set added in PR #256 — that set provides
a fast O(1) check within one process lifetime; this store provides the same
guarantee across process restarts.

Guarantee
---------
At-most-once execution after a reaction record reaches ``completed`` status.
A ``claimed`` record that never reached ``completed`` (e.g. process crash
mid-execution) is treated as retry-eligible: status is set to ``failed`` by
the router when execution raises, allowing the next restart to re-claim.

Scope
-----
Records are keyed by (app_id, tenant_id, workspace_id, idempotency_key_str).
The idempotency_key_str encodes the full reaction identity tuple so that two
different reactions with the same declared ``idempotency_key`` template never
collide.

Collection
----------
``_mz_reaction_idempotency`` in the app database (same
``MOZAIKS_APP_DATABASE_NAME``-configured DB used by module persistence).  The
leading underscore signals a system collection — generated module code must
not access it directly.

Independence from event bus
---------------------------
This store talks to MongoDB directly.  It does NOT go through
MongoEventBus/RedisEventBus or through ``ctx.persistence``; those layers are
not in scope for the router's execution path.
"""
from __future__ import annotations

import os
from datetime import UTC, datetime
from typing import Any, Literal

_DEFAULT_DATABASE_NAME = "mozaiks_apps"
_COLLECTION_NAME = "_mz_reaction_idempotency"

ReactionIdempotencyStatus = Literal["claimed", "completed", "failed"]


class ReactionIdempotencyStore:
    """Durable store for reaction execution idempotency records.

    Records are keyed by a compound index over
    (app_id, tenant_id, workspace_id, idempotency_key_str).

    Status transitions::

        claimed → completed   (successful execution)
        claimed → failed      (execution raised; retry-eligible)

    This class is safe to construct eagerly — MongoDB is only contacted on
    the first operation.  Callers may pass a pre-built ``client`` for testing.
    """

    def __init__(
        self,
        *,
        database_name: str | None = None,
        client: Any | None = None,
    ) -> None:
        self._database_name = (
            (database_name or os.getenv("MOZAIKS_APP_DATABASE_NAME") or "").strip()
            or _DEFAULT_DATABASE_NAME
        )
        self._client = client
        self._collection_handle: Any = None

    def _collection(self) -> Any:
        if self._collection_handle is None:
            if self._client is None:
                from mozaiksai.core.core_config import get_mongo_client

                self._client = get_mongo_client()
            self._collection_handle = self._client[self._database_name][_COLLECTION_NAME]
        return self._collection_handle

    @staticmethod
    def _key_filter(
        *,
        app_id: str,
        tenant_id: str | None,
        workspace_id: str | None,
        idempotency_key_str: str,
    ) -> dict[str, str]:
        return {
            "app_id": app_id,
            "tenant_id": tenant_id or "",
            "workspace_id": workspace_id or "",
            "idempotency_key_str": idempotency_key_str,
        }

    async def claim(
        self,
        *,
        app_id: str,
        tenant_id: str | None,
        workspace_id: str | None,
        idempotency_key_str: str,
    ) -> bool:
        """Attempt to claim this reaction execution slot.

        Returns ``True`` if the slot was newly claimed (this process should
        proceed with execution).

        Returns ``False`` if the slot already exists in any status, meaning:

        - ``completed``: a prior run finished successfully → suppress.
        - ``claimed``: a concurrent or crashed run holds the slot → suppress.
        - ``failed``: a prior run failed and did not release the slot before
          crashing; ``mark_failed`` normally frees the slot for retry, but if
          the process died between claiming and marking failed the slot stays
          as ``claimed``.  Suppress in that case; the operator may clear the
          record manually or rely on a future TTL/cleanup job.

        The upsert is not distributed-atomic across replicas, but it is
        sufficient for the documented guarantee: ``at-most-once after
        completed``.
        """
        key = self._key_filter(
            app_id=app_id,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            idempotency_key_str=idempotency_key_str,
        )
        now = datetime.now(UTC)
        result = await self._collection().update_one(
            key,
            {
                "$setOnInsert": {
                    **key,
                    "status": "claimed",
                    "claimed_at": now,
                    "completed_at": None,
                }
            },
            upsert=True,
        )
        return result.upserted_id is not None

    async def complete(
        self,
        *,
        app_id: str,
        tenant_id: str | None,
        workspace_id: str | None,
        idempotency_key_str: str,
    ) -> None:
        """Mark this reaction as successfully completed.

        Future replays with the same key will be suppressed by ``claim``.
        """
        await self._collection().update_one(
            self._key_filter(
                app_id=app_id,
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                idempotency_key_str=idempotency_key_str,
            ),
            {"$set": {"status": "completed", "completed_at": datetime.now(UTC)}},
        )

    async def mark_failed(
        self,
        *,
        app_id: str,
        tenant_id: str | None,
        workspace_id: str | None,
        idempotency_key_str: str,
    ) -> None:
        """Mark this reaction as failed so a future attempt may retry.

        Removes the document rather than setting status=failed so that the
        next event with the same key can re-claim the slot.  This allows
        at-most-once after completion while still permitting retry after
        failure.
        """
        await self._collection().delete_one(
            self._key_filter(
                app_id=app_id,
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                idempotency_key_str=idempotency_key_str,
            )
        )

    async def ensure_indexes(self) -> None:
        """Create the unique compound index.  Safe to call on every startup."""
        await self._collection().create_index(
            [
                ("app_id", 1),
                ("tenant_id", 1),
                ("workspace_id", 1),
                ("idempotency_key_str", 1),
            ],
            unique=True,
            name="mz_reaction_idempotency_unique",
        )


__all__ = ["ReactionIdempotencyStore", "ReactionIdempotencyStatus"]
