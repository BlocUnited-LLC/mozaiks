# ==============================================================================
# FILE: mozaiksai/core/workflow/queue.py
# DESCRIPTION: Durable global workflow queue with crash-safe leases, fencing,
#              bounded retries, and dead-letter state.
#
#              Replaces per-instance concurrency limits with a globally fair
#              scheduler. Workflow runs are enqueued and dequeued by workers
#              on any instance, respecting the global concurrency limit.
#
# State Machine
# -------------
# ::
#
#     PENDING -> CLAIMED
#     CLAIMED -> COMPLETED
#     CLAIMED -> RETRYABLE   (failure, retry budget not yet exhausted)
#     CLAIMED -> DEAD_LETTER (failure, retry budget exhausted)
#     CLAIMED [expired lease] -> CLAIMED  (atomic reclaim, new token, incremented attempt)
#     RETRYABLE [next_attempt_at <= now] -> CLAIMED
#     COMPLETED -> terminal (never reclaimed)
#     DEAD_LETTER -> terminal (never automatically reclaimed)
#
# Delivery Guarantee
# ------------------
# Lease-based at-least-once processing with fenced completion.
#
# Each claim receives a unique ``claim_token`` (fencing token). ``complete()``
# and ``fail()`` only succeed for the current claim_token holder.  A stale
# worker whose lease expired cannot complete or fail the newer attempt.
#
# Attempt Counting
# ----------------
# ``attempt_count`` is incremented at **claim time** -- each successful claim
# (including reclaims after crash or retry) counts as one attempt.
#
# ``max_attempts`` is the total number of attempts permitted.  When
# ``attempt_count >= max_attempts`` and a failure is recorded, status becomes
# ``dead_letter``.  When ``max_attempts`` is ``None``, retries are unlimited.
#
# No TTL Index on Lease Fields
# ----------------------------
# ``lease_expires_at`` does NOT carry a MongoDB TTL index.  Completed and
# dead-letter records must remain available for audit.  Lease expiry affects
# claimability only -- not record existence.  The existing ``expires_at`` TTL
# index governs overall item retention and is unrelated to leases.
#
# Backends:
#   MongoWorkflowQueue  -- MongoDB collection (default, no extra infra)
#   NoOpWorkflowQueue   -- preserves per-instance semaphore behavior (no durability)
#
# Configuration (env vars):
#   WORKFLOW_QUEUE_BACKEND          -- "mongo" | "noop" (default: "noop")
#   WORKFLOW_QUEUE_MAX_CONCURRENCY  -- global max concurrent workflows (default: 20)
#   WORKFLOW_QUEUE_ITEM_TTL_SECONDS -- max age of a queued item before it expires (default: 3600)
#   WORKFLOW_QUEUE_POLL_INTERVAL    -- worker poll interval in seconds (default: 1.0)
#   WORKFLOW_QUEUE_LEASE_SECONDS    -- claim lease duration (default: 300, bounds: [10, 3600])
# ==============================================================================
from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable
from uuid import uuid4

from logs.logging_config import get_core_logger

logger = get_core_logger("workflow_queue")

_BACKEND = os.getenv("WORKFLOW_QUEUE_BACKEND", "noop").strip().lower()
_MAX_CONCURRENCY = int(os.getenv("WORKFLOW_QUEUE_MAX_CONCURRENCY", "20").strip() or "20")
_ITEM_TTL = int(os.getenv("WORKFLOW_QUEUE_ITEM_TTL_SECONDS", "3600").strip() or "3600")
_POLL_INTERVAL = float(os.getenv("WORKFLOW_QUEUE_POLL_INTERVAL", "1.0").strip() or "1.0")
_DEFAULT_LEASE_SECONDS = int(
    (os.getenv("WORKFLOW_QUEUE_LEASE_SECONDS") or "300").strip() or "300"
)

_MIN_LEASE_SECONDS: int = 10
_MAX_LEASE_SECONDS: int = 3600

_QUEUE_DB = os.getenv("MOZAIKS_APP_DATABASE_NAME", "mozaiks_apps")
_QUEUE_COLLECTION = "workflow_queue"


class QueueItemStatus(StrEnum):
    PENDING = "pending"
    CLAIMED = "claimed"
    COMPLETED = "completed"
    FAILED = "failed"          # pre-upgrade status; old records may have this
    EXPIRED = "expired"        # pre-upgrade status; old records may have this
    RETRYABLE = "retryable"
    DEAD_LETTER = "dead_letter"


@dataclass
class ClaimResult:
    """Result of a ``claim_next()`` call.

    ``item`` is the claimed ``QueueItem`` when ``claimed=True``.
    ``claim_token`` is the fencing token required by ``complete()`` and
    ``fail()``.  ``attempt_count`` is the cumulative number of claim
    attempts for this item (1 on first claim, increments on each reclaim).
    """

    claimed: bool
    item: QueueItem | None = None
    claim_token: str | None = None
    attempt_count: int = 0


@dataclass
class QueueItem:
    """A workflow run enqueued for execution."""

    workflow_name: str
    chat_id: str
    app_id: str
    user_id: str | None = None
    tenant_id: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)
    priority: int = 0                    # Higher = executed first
    item_id: str = field(default_factory=lambda: str(uuid4()))
    enqueued_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    expires_at: str = field(default_factory=lambda: (
        datetime.now(UTC) + timedelta(seconds=_ITEM_TTL)
    ).isoformat())
    status: QueueItemStatus = QueueItemStatus.PENDING
    claimed_by: str | None = None
    claimed_at: str | None = None

    # Lease and retry fields (new)
    claim_token: str | None = None
    lease_expires_at: str | None = None
    attempt_count: int = 0
    max_attempts: int | None = None
    retry_delay_seconds: int = 0
    next_attempt_at: str | None = None
    last_failed_at: str | None = None
    dead_lettered_at: str | None = None
    error_category: str | None = None

    def to_document(self) -> dict[str, Any]:
        return {
            "_id": self.item_id,
            "workflow_name": self.workflow_name,
            "chat_id": self.chat_id,
            "app_id": self.app_id,
            "user_id": self.user_id,
            "tenant_id": self.tenant_id,
            "payload": self.payload,
            "priority": self.priority,
            "enqueued_at": self.enqueued_at,
            "expires_at": self.expires_at,
            "status": self.status.value,
            "claimed_by": self.claimed_by,
            "claimed_at": self.claimed_at,
            "claim_token": self.claim_token,
            "lease_expires_at": self.lease_expires_at,
            "attempt_count": self.attempt_count,
            "max_attempts": self.max_attempts,
            "retry_delay_seconds": self.retry_delay_seconds,
            "next_attempt_at": self.next_attempt_at,
            "last_failed_at": self.last_failed_at,
            "dead_lettered_at": self.dead_lettered_at,
            "error_category": self.error_category,
        }

    @classmethod
    def from_document(cls, doc: dict[str, Any]) -> QueueItem:
        return cls(
            item_id=str(doc.get("_id", uuid4())),
            workflow_name=doc.get("workflow_name", ""),
            chat_id=doc.get("chat_id", ""),
            app_id=doc.get("app_id", ""),
            user_id=doc.get("user_id"),
            tenant_id=doc.get("tenant_id"),
            payload=doc.get("payload", {}),
            priority=int(doc.get("priority", 0)),
            enqueued_at=doc.get("enqueued_at", ""),
            expires_at=doc.get("expires_at", ""),
            status=QueueItemStatus(doc.get("status", "pending")),
            claimed_by=doc.get("claimed_by"),
            claimed_at=doc.get("claimed_at"),
            claim_token=doc.get("claim_token"),
            lease_expires_at=doc.get("lease_expires_at"),
            attempt_count=int(doc.get("attempt_count", 0)),
            max_attempts=doc.get("max_attempts"),
            retry_delay_seconds=int(doc.get("retry_delay_seconds", 0)),
            next_attempt_at=doc.get("next_attempt_at"),
            last_failed_at=doc.get("last_failed_at"),
            dead_lettered_at=doc.get("dead_lettered_at"),
            error_category=doc.get("error_category"),
        )


def _bounded_lease(lease_seconds: int) -> int:
    """Clamp lease duration to safe minimum and maximum."""
    return max(_MIN_LEASE_SECONDS, min(_MAX_LEASE_SECONDS, int(lease_seconds)))


@runtime_checkable
class WorkflowQueue(Protocol):
    """Port for global workflow queue operations."""

    async def enqueue(
        self,
        item: QueueItem,
        *,
        max_attempts: int | None = None,
        retry_delay_seconds: int = 0,
    ) -> str:
        """Enqueue a workflow run. Returns the item_id."""
        ...

    async def claim_next(
        self,
        worker_id: str,
        *,
        lease_seconds: int = _DEFAULT_LEASE_SECONDS,
    ) -> ClaimResult:
        """Atomically claim the next eligible item.

        Returns ``ClaimResult(claimed=True, ...)`` on success, or
        ``ClaimResult(claimed=False)`` when no eligible items exist.
        """
        ...

    async def complete(self, item_id: str, *, claim_token: str) -> bool:
        """Mark a claimed item as completed.

        Only succeeds for the current ``claim_token`` holder.
        Returns ``True`` if the transition succeeded; ``False`` if fencing
        rejected the call (stale worker or already transitioned).
        """
        ...

    async def fail(
        self,
        item_id: str,
        *,
        claim_token: str,
        error_category: str | None = None,
    ) -> str | None:
        """Transition a CLAIMED item to RETRYABLE or DEAD_LETTER.

        Returns the new status string on success, or ``None`` if fencing
        rejected the call (stale worker -- claim_token mismatch).
        """
        ...

    async def renew_lease(
        self,
        item_id: str,
        *,
        claim_token: str,
        extend_seconds: int | None = None,
    ) -> bool:
        """Extend the lease on a currently-claimed item.

        Only succeeds if the lease has not yet expired and ``claim_token``
        matches.  Returns ``True`` if extended; ``False`` otherwise.
        """
        ...

    async def active_count(self) -> int:
        """Return the number of currently executing workflow runs."""
        ...

    async def queue_depth(self) -> int:
        """Return the number of pending items in the queue."""
        ...


# ---------------------------------------------------------------------------
# No-op (existing per-instance semaphore behaviour -- default)
# ---------------------------------------------------------------------------

class NoOpWorkflowQueue:
    """Preserves existing per-instance semaphore behaviour.

    No cross-instance coordination, no durability guarantees, and no
    implementation of the lease/fencing/retry lifecycle.  Use when running
    a single instance without the need for crash-safe queue semantics.
    """

    def __init__(self) -> None:
        self._semaphore = asyncio.Semaphore(_MAX_CONCURRENCY)
        self._active = 0

    async def enqueue(
        self,
        item: QueueItem,
        *,
        max_attempts: int | None = None,
        retry_delay_seconds: int = 0,
    ) -> str:
        return item.item_id

    async def claim_next(
        self,
        worker_id: str,
        *,
        lease_seconds: int = _DEFAULT_LEASE_SECONDS,
    ) -> ClaimResult:
        return ClaimResult(claimed=False)

    async def complete(self, item_id: str, *, claim_token: str) -> bool:
        return False

    async def fail(
        self,
        item_id: str,
        *,
        claim_token: str,
        error_category: str | None = None,
    ) -> str | None:
        return None

    async def renew_lease(
        self,
        item_id: str,
        *,
        claim_token: str,
        extend_seconds: int | None = None,
    ) -> bool:
        return False

    async def active_count(self) -> int:
        return self._active

    async def queue_depth(self) -> int:
        return 0


# ---------------------------------------------------------------------------
# MongoDB queue
# ---------------------------------------------------------------------------

class MongoWorkflowQueue:
    """Global workflow queue backed by MongoDB.

    Uses ``find_one_and_update`` for atomic claim operations -- safe under
    concurrent workers on multiple instances.

    See module docstring for state machine, guarantee, and configuration.

    ``_now_fn`` injects a zero-argument callable returning a timezone-aware
    UTC datetime.  Use it in tests to control time without patching globals.
    """

    _instance_id: str = str(uuid4())

    def __init__(self, *, _now_fn: Any | None = None) -> None:
        self._now_fn = _now_fn if _now_fn is not None else lambda: datetime.now(UTC)

    def _now(self) -> datetime:
        return self._now_fn()

    def _col(self, name: str = _QUEUE_COLLECTION) -> Any | None:
        try:
            from mozaiksai.core.core_config import get_mongo_client

            client = get_mongo_client()
            if client is None:
                return None
            return client[_QUEUE_DB][name]
        except Exception:
            return None

    async def ensure_indexes(self) -> None:
        """Create required indexes.  Safe to call on every startup.

        Does NOT create a TTL index on any lease or time field.  The existing
        ``expires_at`` TTL index governs overall item retention and is
        unrelated to leases.  Completed and dead-letter records must remain
        available for audit until the retention TTL removes them.
        """
        col = self._col()
        if col is None:
            return
        try:
            # Primary eligibility index for claim_next.
            await col.create_index(
                [("status", 1), ("priority", -1), ("enqueued_at", 1)],
                name="wq_claim_idx",
                background=True,
            )
            # Lease expiry index for expired-claim recovery.
            await col.create_index(
                [("status", 1), ("lease_expires_at", 1)],
                name="wq_lease_expiry_idx",
                background=True,
            )
            # Retry eligibility index.
            await col.create_index(
                [("status", 1), ("next_attempt_at", 1)],
                name="wq_retry_idx",
                background=True,
            )
            # Retention TTL on expires_at (item-level, not lease-level).
            await col.create_index(
                [("expires_at", 1)],
                expireAfterSeconds=0,
                name="wq_ttl_idx",
                background=True,
            )
        except Exception as exc:
            logger.warning("WorkflowQueue index creation failed: %s", exc)

    async def enqueue(
        self,
        item: QueueItem,
        *,
        max_attempts: int | None = None,
        retry_delay_seconds: int = 0,
    ) -> str:
        item.max_attempts = max_attempts
        item.retry_delay_seconds = max(0, int(retry_delay_seconds))
        col = self._col()
        if col is None:
            return item.item_id
        try:
            await col.insert_one(item.to_document())
            logger.debug(
                "QUEUE_ENQUEUED item_id=%s workflow=%s",
                item.item_id,
                item.workflow_name,
            )
        except Exception as exc:
            logger.error("QUEUE_ENQUEUE_FAIL: %s", exc)
        return item.item_id

    async def claim_next(
        self,
        worker_id: str,
        *,
        lease_seconds: int = _DEFAULT_LEASE_SECONDS,
    ) -> ClaimResult:
        """Atomically claim the next eligible item.

        Eligible items are:
          - ``PENDING``
          - ``RETRYABLE`` with ``next_attempt_at <= now``
          - ``CLAIMED`` with ``lease_expires_at <= now`` (crash recovery)
          - Pre-upgrade ``CLAIMED`` without ``claim_token``

        Returns ``ClaimResult(claimed=True, ...)`` on success.
        """
        col = self._col()
        if col is None:
            return ClaimResult(claimed=False)

        now = self._now()
        now_iso = now.isoformat()
        ls = _bounded_lease(lease_seconds)
        new_token = str(uuid4())
        lease_exp = (now + timedelta(seconds=ls)).isoformat()

        try:
            doc = await col.find_one_and_update(
                {
                    "$or": [
                        # Normal pending items.
                        {"status": QueueItemStatus.PENDING.value},
                        # Retry-eligible items.
                        {
                            "status": QueueItemStatus.RETRYABLE.value,
                            "next_attempt_at": {"$lte": now_iso},
                        },
                        # Crash recovery: lease expired on a claimed item.
                        {
                            "status": QueueItemStatus.CLAIMED.value,
                            "lease_expires_at": {"$lte": now_iso},
                        },
                        # Pre-upgrade compat: old CLAIMED records without
                        # claim_token (pre-upgrade) are immediately
                        # reclaimable since they have no lease.
                        {
                            "status": QueueItemStatus.CLAIMED.value,
                            "claim_token": None,
                        },
                        {
                            "status": QueueItemStatus.CLAIMED.value,
                            "claim_token": {"$exists": False},
                        },
                    ],
                },
                {
                    "$set": {
                        "status": QueueItemStatus.CLAIMED.value,
                        "claimed_by": worker_id,
                        "claim_token": new_token,
                        "claimed_at": now_iso,
                        "lease_expires_at": lease_exp,
                    },
                    "$inc": {"attempt_count": 1},
                },
                sort=[("priority", -1), ("enqueued_at", 1)],
                return_document=True,
            )
            if doc is None:
                return ClaimResult(claimed=False)
            item = QueueItem.from_document(doc)
            logger.debug(
                "QUEUE_CLAIMED item_id=%s worker=%s attempt=%d",
                item.item_id,
                worker_id,
                item.attempt_count,
            )
            return ClaimResult(
                claimed=True,
                item=item,
                claim_token=new_token,
                attempt_count=item.attempt_count,
            )
        except Exception as exc:
            logger.error("QUEUE_CLAIM_FAIL: %s", exc)
            return ClaimResult(claimed=False)

    async def complete(self, item_id: str, *, claim_token: str) -> bool:
        """Mark a claimed item as completed.

        Only succeeds for the current ``claim_token`` holder.
        """
        col = self._col()
        if col is None:
            return False
        try:
            now_iso = self._now().isoformat()
            result = await col.update_one(
                {
                    "_id": item_id,
                    "status": QueueItemStatus.CLAIMED.value,
                    "claim_token": claim_token,
                },
                {"$set": {"status": QueueItemStatus.COMPLETED.value, "completed_at": now_iso}},
            )
            return bool(result.modified_count == 1)
        except Exception as exc:
            logger.error("QUEUE_COMPLETE_FAIL item_id=%s: %s", item_id, exc)
            return False

    async def fail(
        self,
        item_id: str,
        *,
        claim_token: str,
        error_category: str | None = None,
    ) -> str | None:
        """Transition a CLAIMED item to RETRYABLE or DEAD_LETTER.

        Status determination uses values stored in the document at claim time:

        - ``DEAD_LETTER``: ``max_attempts`` is set and
          ``attempt_count >= max_attempts``.
        - ``RETRYABLE``: all other cases (``max_attempts=None`` -> unlimited).

        ``RETRYABLE`` records become re-claimable after ``next_attempt_at``
        (``now + retry_delay_seconds``; default 0 -> immediately reclaimable).

        ``DEAD_LETTER`` is terminal.

        No raw exception text, stack traces, or credentials are stored.
        ``error_category`` is a short normalized descriptor (<=128 chars).

        Two-step algorithm: a non-binding ``find_one`` reads attempt metadata
        to determine the new status; a fenced ``update_one`` performs the
        actual transition.
        """
        col = self._col()
        if col is None:
            return None

        now = self._now()
        now_iso = now.isoformat()

        try:
            # Non-binding peek: read attempt metadata.
            doc = await col.find_one(
                {
                    "_id": item_id,
                    "status": QueueItemStatus.CLAIMED.value,
                    "claim_token": claim_token,
                }
            )
            if doc is None:
                return None  # Stale worker or already transitioned.

            attempt_count = int(doc.get("attempt_count") or 0)
            max_attempts_val = doc.get("max_attempts")
            retry_delay = max(0, int(doc.get("retry_delay_seconds") or 0))
            next_attempt_iso = (now + timedelta(seconds=retry_delay)).isoformat()

            if max_attempts_val is not None and attempt_count >= int(max_attempts_val):
                new_status = QueueItemStatus.DEAD_LETTER.value
                dead_lettered_at: str | None = now_iso
                na_value: str | None = None
            else:
                new_status = QueueItemStatus.RETRYABLE.value
                dead_lettered_at = None
                na_value = next_attempt_iso

            # Fenced transition.
            result = await col.update_one(
                {
                    "_id": item_id,
                    "status": QueueItemStatus.CLAIMED.value,
                    "claim_token": claim_token,
                },
                {
                    "$set": {
                        "status": new_status,
                        "last_failed_at": now_iso,
                        "next_attempt_at": na_value,
                        "dead_lettered_at": dead_lettered_at,
                        "error_category": (error_category or "execution_error")[:128],
                    }
                },
            )
            if result.modified_count == 0:
                return None  # Race: another worker reclaimed between peek and update.

            logger.debug(
                "QUEUE_FAILED item_id=%s -> %s attempt=%d",
                item_id,
                new_status,
                attempt_count,
            )
            return new_status
        except Exception as exc:
            logger.error("QUEUE_FAIL_FAIL item_id=%s: %s", item_id, exc)
            return None

    async def renew_lease(
        self,
        item_id: str,
        *,
        claim_token: str,
        extend_seconds: int | None = None,
    ) -> bool:
        """Extend the lease on a currently-claimed item.

        Only succeeds if the lease has not yet expired and ``claim_token``
        matches.
        """
        col = self._col()
        if col is None:
            return False

        now = self._now()
        now_iso = now.isoformat()
        ext = _bounded_lease(extend_seconds or _DEFAULT_LEASE_SECONDS)
        new_lease_exp = (now + timedelta(seconds=ext)).isoformat()

        try:
            result = await col.update_one(
                {
                    "_id": item_id,
                    "status": QueueItemStatus.CLAIMED.value,
                    "claim_token": claim_token,
                    "lease_expires_at": {"$gt": now_iso},
                },
                {"$set": {"lease_expires_at": new_lease_exp}},
            )
            return bool(result.modified_count == 1)
        except Exception as exc:
            logger.error("QUEUE_RENEW_FAIL item_id=%s: %s", item_id, exc)
            return False

    async def active_count(self) -> int:
        col = self._col()
        if col is None:
            return 0
        try:
            return int(
                await col.count_documents(
                    {"status": QueueItemStatus.CLAIMED.value}
                )
            )
        except Exception:
            return 0

    async def queue_depth(self) -> int:
        col = self._col()
        if col is None:
            return 0
        try:
            return int(
                await col.count_documents(
                    {"status": QueueItemStatus.PENDING.value}
                )
            )
        except Exception:
            return 0


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

_queue: WorkflowQueue | None = None


def get_workflow_queue() -> WorkflowQueue:
    """Return the configured global workflow queue (process-wide singleton)."""
    global _queue
    if _queue is None:
        if _BACKEND == "mongo":
            _queue = MongoWorkflowQueue()
        else:
            _queue = NoOpWorkflowQueue()
        logger.info(
            "WorkflowQueue backend: %s (max_concurrency=%d)",
            _BACKEND,
            _MAX_CONCURRENCY,
        )
    return _queue


__all__ = [
    "ClaimResult",
    "MongoWorkflowQueue",
    "NoOpWorkflowQueue",
    "QueueItem",
    "QueueItemStatus",
    "WorkflowQueue",
    "get_workflow_queue",
]
