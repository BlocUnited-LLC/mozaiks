"""Durable reaction idempotency ledger with lease-based safety guarantees.

Provides restart-safe, crash-safe duplicate suppression for module reactions
that declare an ``idempotency_key``.

State Machine
-------------
::

    NEW → CLAIMED
    CLAIMED → COMPLETED
    CLAIMED → RETRYABLE  (failure, retry budget not yet exhausted)
    CLAIMED → DEAD_LETTER  (failure, retry budget exhausted)
    CLAIMED [expired lease] → CLAIMED  (atomic reclaim, new attempt)
    RETRYABLE [next_attempt_at reached] → CLAIMED  (atomic reclaim, new attempt)
    COMPLETED → terminal, never reclaimed
    DEAD_LETTER → terminal, never automatically reclaimed

Delivery Guarantee
------------------
Lease-based at-least-once processing with idempotent completion.

Each claim receives a unique ``claim_token`` (fencing token). ``complete()``
and ``mark_failed()`` only succeed for the current claim_token holder.  A
stale worker whose lease expired cannot complete or fail the newer attempt.

If the reaction handler takes longer than ``lease_seconds``, the claim becomes
reclaimable and another worker may start executing the same reaction
concurrently.  Both workers eventually call ``complete()`` or ``mark_failed()``,
but only the one holding the current claim_token succeeds.  This provides
lease-based at-least-once semantics.  Design reactions to be idempotent.

Attempt Counting
----------------
``attempt_count`` is incremented at **claim time** — each successful claim
(including reclaims after crash or retry) counts as one attempt.

``max_attempts`` is the total number of attempts permitted.  When
``attempt_count >= max_attempts`` and a failure is recorded, status becomes
``dead_letter``.  When ``max_attempts`` is ``None``, retries are unlimited
(failure always produces ``retryable``; the slot is immediately reclaimable
or after ``retry_delay_seconds``).

Example with ``max_attempts=3``: attempt 1 fails → retryable; attempt 2 fails
→ retryable; attempt 3 fails → dead_letter.  If attempt 3 succeeds →
completed.

Crash Recovery
--------------
Recovery is lazy and atomic: when an event is re-delivered, ``claim()``
detects whether the existing record has an expired lease (``status=claimed``
and ``lease_expires_at < now``) and reclaims it in a single
``find_one_and_update`` operation.  No background sweeper is required.

Operators trigger recovery by re-emitting the event or re-delivering via an
external queue.  Stuck ``claimed`` records beyond the configured lease window
may be cleared manually from ``_mz_reaction_idempotency`` if re-emission is
not possible.

No TTL Index on Lease Fields
------------------------------
``lease_expires_at`` does NOT carry a MongoDB TTL index.  Completed and
dead-letter records must remain available for idempotency and audit.  Lease
expiry affects claimability only — not record existence.  A separate retention
job may purge old records by a distinct ``_retain_until`` field; that is
outside the scope of this module.

Collection
----------
``_mz_reaction_idempotency`` in the app database (same
``MOZAIKS_APP_DATABASE_NAME``-configured DB used by module persistence).  The
leading underscore signals a system collection — generated module code must
not access it directly.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal
from uuid import uuid4

_DEFAULT_DATABASE_NAME = "mozaiks_apps"
_COLLECTION_NAME = "_mz_reaction_idempotency"

# Default lease duration — operator-configurable via env
_DEFAULT_LEASE_SECONDS: int = int(
    (os.getenv("REACTION_LEASE_SECONDS") or "300").strip() or "300"
)
# Hard bounds to prevent misconfiguration
_MIN_LEASE_SECONDS: int = 10
_MAX_LEASE_SECONDS: int = 3600

ReactionIdempotencyStatus = Literal["claimed", "retryable", "completed", "dead_letter"]


@dataclass(slots=True, frozen=True)
class LeaseClaim:
    """Result of a ``claim()`` call.

    ``claimed=True`` means this caller holds the execution slot.
    ``claim_token`` is the fencing token required by ``complete()`` and
    ``mark_failed()``.  ``attempt_count`` is the cumulative number of claim
    attempts for this reaction identity (1 on first claim, increments on
    each reclaim).
    """

    claimed: bool
    claim_token: str | None = None
    attempt_count: int = 0


class ReactionIdempotencyStore:
    """Durable store for reaction execution idempotency records.

    See module docstring for state machine, guarantee, and configuration.

    This class is safe to construct eagerly — MongoDB is only contacted on
    the first operation.  Pass a pre-built ``client`` for testing.

    ``_now_fn`` injects a zero-argument callable returning a timezone-aware UTC
    datetime.  Use it in tests to control time without patching globals.
    """

    def __init__(
        self,
        *,
        database_name: str | None = None,
        client: Any | None = None,
        _now_fn: Any | None = None,
    ) -> None:
        self._database_name = (
            (database_name or os.getenv("MOZAIKS_APP_DATABASE_NAME") or "").strip()
            or _DEFAULT_DATABASE_NAME
        )
        self._client = client
        self._collection_handle: Any = None
        self._now_fn = _now_fn if _now_fn is not None else lambda: datetime.now(UTC)

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
    ) -> dict[str, Any]:
        return {
            "app_id": app_id,
            "tenant_id": tenant_id or "",
            "workspace_id": workspace_id or "",
            "idempotency_key_str": idempotency_key_str,
        }

    @staticmethod
    def _bounded_lease(lease_seconds: int) -> int:
        """Clamp lease duration to safe minimum and maximum."""
        return max(_MIN_LEASE_SECONDS, min(_MAX_LEASE_SECONDS, int(lease_seconds)))

    async def claim(
        self,
        *,
        app_id: str,
        tenant_id: str | None,
        workspace_id: str | None,
        idempotency_key_str: str,
        lease_seconds: int = _DEFAULT_LEASE_SECONDS,
        max_attempts: int | None = None,
    ) -> LeaseClaim:
        """Attempt to atomically claim this reaction execution slot.

        Returns ``LeaseClaim(claimed=True, ...)`` if the slot was newly
        claimed or atomically reclaimed (crash recovery / retry).

        Returns ``LeaseClaim(claimed=False)`` when:

        - ``COMPLETED`` — permanently suppressed.
        - ``DEAD_LETTER`` — retry budget exhausted, permanently suppressed.
        - ``CLAIMED`` with an unexpired lease — another worker is executing.
        - ``RETRYABLE`` but ``next_attempt_at`` is still in the future.

        Atomically reclaims when:

        - ``CLAIMED`` and ``lease_expires_at < now`` (process crash recovery).
        - ``RETRYABLE`` and ``next_attempt_at <= now`` (retry window elapsed).

        ``attempt_count`` increments on every claim, including reclaims.
        ``max_attempts`` is stored in the record for use by ``mark_failed()``.

        Algorithm — Phase 1: ``$setOnInsert`` upsert inserts a new record if
        none exists.  Phase 2: ``find_one_and_update`` atomically reclaims a
        reclaimable existing record.  Concurrent first-claim races are resolved
        by the unique index — the loser falls through to Phase 2 and finds an
        unexpired lease, returning ``LeaseClaim(claimed=False)``.
        """
        col = self._collection()
        now = self._now_fn()
        ls = self._bounded_lease(lease_seconds)
        new_token = str(uuid4())
        key = self._key_filter(
            app_id=app_id,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            idempotency_key_str=idempotency_key_str,
        )

        # Phase 1 — insert a brand-new record atomically.
        try:
            result = await col.update_one(
                key,
                {
                    "$setOnInsert": {
                        **key,
                        "status": "claimed",
                        "claim_token": new_token,
                        "attempt_count": 1,
                        "max_attempts": max_attempts,
                        "claimed_at": now,
                        "lease_expires_at": now + timedelta(seconds=ls),
                        "next_attempt_at": None,
                        "last_failed_at": None,
                        "completed_at": None,
                        "dead_lettered_at": None,
                        "error_category": None,
                    }
                },
                upsert=True,
            )
            if result.upserted_id is not None:
                return LeaseClaim(claimed=True, claim_token=new_token, attempt_count=1)
        except Exception as exc:
            # Duplicate-key from concurrent Phase 1 race → fall through to Phase 2.
            exc_str = str(exc)
            if "E11000" not in exc_str and "duplicate" not in type(exc).__name__.lower():
                raise

        # Phase 2 — atomic reclaim for crash recovery or retry-eligible slot.
        doc = await col.find_one_and_update(
            {
                **key,
                "$or": [
                    # Crash recovery: prior worker's lease has expired.
                    {"status": "claimed", "lease_expires_at": {"$lt": now}},
                    # Retry: previous attempt failed and retry window elapsed.
                    {"status": "retryable", "next_attempt_at": {"$lte": now}},
                ],
            },
            {
                "$set": {
                    "status": "claimed",
                    "claim_token": new_token,
                    "claimed_at": now,
                    "lease_expires_at": now + timedelta(seconds=ls),
                    # Update max_attempts in case declaration changed between restarts.
                    "max_attempts": max_attempts,
                },
                "$inc": {"attempt_count": 1},
            },
            return_document=True,
        )
        if doc is not None:
            return LeaseClaim(
                claimed=True,
                claim_token=new_token,
                attempt_count=int(doc.get("attempt_count", 0)),
            )

        # Document exists in a non-reclaimable state.
        return LeaseClaim(claimed=False)

    async def complete(
        self,
        *,
        app_id: str,
        tenant_id: str | None,
        workspace_id: str | None,
        idempotency_key_str: str,
        claim_token: str,
    ) -> bool:
        """Mark this reaction as successfully completed.

        Only updates a ``CLAIMED`` document with the matching ``claim_token``.
        Returns ``True`` if the transition succeeded; ``False`` if fencing
        rejected the call (stale worker or already transitioned).

        ``COMPLETED`` records permanently suppress future replay.
        """
        result = await self._collection().update_one(
            {
                **self._key_filter(
                    app_id=app_id,
                    tenant_id=tenant_id,
                    workspace_id=workspace_id,
                    idempotency_key_str=idempotency_key_str,
                ),
                "status": "claimed",
                "claim_token": claim_token,
            },
            {"$set": {"status": "completed", "completed_at": self._now_fn()}},
        )
        return bool(result.modified_count == 1)

    async def mark_failed(
        self,
        *,
        app_id: str,
        tenant_id: str | None,
        workspace_id: str | None,
        idempotency_key_str: str,
        claim_token: str,
        retry_delay_seconds: int = 0,
        error_category: str | None = None,
    ) -> str | None:
        """Transition a CLAIMED reaction to RETRYABLE or DEAD_LETTER.

        Returns the new status string on success, or ``None`` if fencing
        rejected the call (stale worker — ``claim_token`` mismatch).

        Status determination uses values stored in the document at claim time:

        - ``DEAD_LETTER``: ``max_attempts`` is set and
          ``attempt_count >= max_attempts``.
        - ``RETRYABLE``: all other cases (``max_attempts=None`` → unlimited).

        ``RETRYABLE`` records become re-claimable after ``next_attempt_at``
        (``now + retry_delay_seconds``; default 0 → immediately reclaimable).

        ``DEAD_LETTER`` is terminal.  Operator intervention required.

        No raw exception text, stack traces, or credentials are stored.
        ``error_category`` is a short normalized descriptor (≤128 chars).

        Two-step algorithm: a non-binding ``find_one`` reads attempt metadata
        to determine the new status; a fenced ``update_one`` performs the
        actual transition.  If the record changes between the two operations
        (concurrent reclaim), ``update_one`` modifies nothing and ``None`` is
        returned — the stale failure is safely discarded.
        """
        col = self._collection()
        key = self._key_filter(
            app_id=app_id,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            idempotency_key_str=idempotency_key_str,
        )
        now = self._now_fn()
        next_attempt_at = now + timedelta(seconds=max(0, int(retry_delay_seconds)))

        # Non-binding peek: read attempt metadata to determine new status.
        doc = await col.find_one(
            {**key, "status": "claimed", "claim_token": claim_token}
        )
        if doc is None:
            return None  # Stale worker or already transitioned

        attempt_count = int(doc.get("attempt_count") or 0)
        max_attempts_val = doc.get("max_attempts")

        if max_attempts_val is not None and attempt_count >= int(max_attempts_val):
            new_status: str = "dead_letter"
            dead_lettered_at: datetime | None = now
            na_value: datetime | None = None
        else:
            new_status = "retryable"
            dead_lettered_at = None
            na_value = next_attempt_at

        # Fenced transition — only succeeds if this caller still holds the claim.
        result = await col.update_one(
            {**key, "status": "claimed", "claim_token": claim_token},
            {
                "$set": {
                    "status": new_status,
                    "last_failed_at": now,
                    "next_attempt_at": na_value,
                    "dead_lettered_at": dead_lettered_at,
                    "error_category": (error_category or "execution_error")[:128],
                }
            },
        )
        if result.modified_count == 0:
            return None  # Race: another worker reclaimed between peek and update

        return new_status

    async def ensure_indexes(self) -> None:
        """Create required indexes.  Safe to call on every startup.

        Does NOT create a TTL index on any lease or time field.  Completed and
        dead-letter records must remain available for idempotency and audit.
        See module docstring for retention policy notes.
        """
        col = self._collection()
        # Primary identity uniqueness index — enforces at-most-one record per scope.
        await col.create_index(
            [
                ("app_id", 1),
                ("tenant_id", 1),
                ("workspace_id", 1),
                ("idempotency_key_str", 1),
            ],
            unique=True,
            name="mz_reaction_idempotency_unique",
        )
        # Compound index for efficient expired-lease reclaim lookups (Phase 2 claim).
        await col.create_index(
            [
                ("app_id", 1),
                ("tenant_id", 1),
                ("workspace_id", 1),
                ("idempotency_key_str", 1),
                ("status", 1),
                ("lease_expires_at", 1),
            ],
            name="mz_reaction_claim_reclaim_idx",
            background=True,
        )
        # Compound index for efficient retry-eligible lookups (Phase 2 claim).
        await col.create_index(
            [
                ("app_id", 1),
                ("tenant_id", 1),
                ("workspace_id", 1),
                ("idempotency_key_str", 1),
                ("status", 1),
                ("next_attempt_at", 1),
            ],
            name="mz_reaction_retry_idx",
            background=True,
        )


__all__ = [
    "LeaseClaim",
    "ReactionIdempotencyStatus",
    "ReactionIdempotencyStore",
]
