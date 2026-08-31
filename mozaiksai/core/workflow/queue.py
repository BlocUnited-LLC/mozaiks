# ==============================================================================
# FILE: mozaiksai/core/workflow/queue.py
# DESCRIPTION: Durable global workflow queue with crash-safe leases, fencing,
#              bounded retries, and dead-letter state.
#
#              Production ingress uses targeted claims so the instance that
#              owns the live transport also owns execution. The process-local
#              semaphore remains a resource throttle, not admission authority.
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
# Lease-based processing with holder-scoped completion. Automatic recovery is
# allowed only before execution_started_at. An expired started claim is
# dead-lettered rather than replayed without a side-effect idempotency proof.
#
# Each claim receives a unique holder token. ``complete()``
# and ``fail()`` only succeed for the current claim_token holder.  A stale
# worker whose lease expired cannot complete or fail the newer attempt.
#
# Attempt Counting
# ----------------
# ``attempt_count`` is incremented at **claim time** -- each successful claim
# (including reclaims after crash or retry) counts as one attempt.
#
# ``max_attempts`` is the total number of attempts permitted (finite, default 3,
# range [1, 25]).  When ``attempt_count >= max_attempts`` and a failure is
# recorded, status becomes ``dead_letter``.
#
# Retention vs Lease
# ------------------
# ``lease_expires_at`` does NOT carry a MongoDB TTL index.  Lease expiry
# affects claimability only -- not record existence.
#
# ``expires_at`` is set only at terminal transitions (COMPLETED, DEAD_LETTER)
# to enable TTL-based retention cleanup.  Active work (PENDING, CLAIMED,
# RETRYABLE) has ``expires_at=None`` and is never deleted by the TTL sweeper.
#
# Modes:
#   required -- MongoDB is authoritative and failures close admission
#   local    -- explicit single-process, non-durable execution boundary
#
# Configuration (env vars):
#   MOZAIKS_WORKFLOW_ADMISSION_MODE -- "required" | "local"
#   WORKFLOW_QUEUE_ITEM_TTL_SECONDS -- max age of a queued item before it expires (default: 3600)
#   WORKFLOW_QUEUE_LEASE_SECONDS    -- claim lease duration (default: 300, bounds: [10, 3600])
# ==============================================================================
from __future__ import annotations

import asyncio
import hashlib
import json
import os
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable
from uuid import uuid4

from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from logs.logging_config import get_core_logger
from mozaiksai.core.core_config import get_mongo_client
from mozaiksai.core.data.persistence.namespaces import SYSTEM_DATABASE, RuntimeCollections

logger = get_core_logger("workflow_queue")

_MAX_CONCURRENCY = int(os.getenv("WORKFLOW_QUEUE_MAX_CONCURRENCY", "20").strip() or "20")
_ITEM_TTL = int(os.getenv("WORKFLOW_QUEUE_ITEM_TTL_SECONDS", "3600").strip() or "3600")
_DEFAULT_LEASE_SECONDS = int(
    (os.getenv("WORKFLOW_QUEUE_LEASE_SECONDS") or "300").strip() or "300"
)

_MIN_LEASE_SECONDS: int = 10
_MAX_LEASE_SECONDS: int = 3600

_DEFAULT_MAX_ATTEMPTS: int = 3
_MIN_MAX_ATTEMPTS: int = 1
_MAX_MAX_ATTEMPTS: int = 25

_QUEUE_DB = SYSTEM_DATABASE
_QUEUE_COLLECTION = RuntimeCollections.WORKFLOW_QUEUE


class QueueAuthorityUnavailableError(RuntimeError):
    """Mongo admission authority could not prove a queue transition."""


class QueueIdentityConflictError(RuntimeError):
    """An admission id was replayed with different immutable identity."""


def canonical_admission_id(
    *,
    tenant_id: str,
    workspace_id: str,
    app_id: str,
    chat_id: str,
    workflow_name: str,
    run_id: str,
    operation_id: str,
) -> str:
    """Return the immutable, payload-independent identity for one admission."""
    fields = {
        "v": 1,
        "tenant_id": tenant_id,
        "workspace_id": workspace_id,
        "app_id": app_id,
        "chat_id": chat_id,
        "workflow_name": workflow_name,
        "run_id": run_id,
        "operation_id": operation_id,
    }
    encoded = json.dumps(fields, sort_keys=True, separators=(",", ":")).encode()
    return "wqa_" + hashlib.sha256(encoded).hexdigest()


class QueueItemStatus(StrEnum):
    PENDING = "pending"
    CLAIMED = "claimed"
    COMPLETED = "completed"
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
    workspace_id: str | None = None
    run_id: str | None = None
    operation_id: str | None = None
    request_digest: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)
    priority: int = 0                    # Higher = executed first
    item_id: str = field(default_factory=lambda: str(uuid4()))
    enqueued_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    expires_at: str | None = None  # Set only at terminal transition; TTL index deletes after this
    status: QueueItemStatus = QueueItemStatus.PENDING
    claimed_by: str | None = None
    claimed_at: str | None = None

    # Lease and retry fields
    claim_token: str | None = None
    lease_expires_at: str | None = None
    attempt_count: int = 0
    max_attempts: int = _DEFAULT_MAX_ATTEMPTS
    retry_delay_seconds: int = 0
    next_attempt_at: str | None = None
    last_failed_at: str | None = None
    dead_lettered_at: str | None = None
    error_category: str | None = None
    execution_started_at: str | None = None
    completed_result: dict[str, Any] | None = None

    def to_document(self) -> dict[str, Any]:
        return {
            "_id": self.item_id,
            "workflow_name": self.workflow_name,
            "chat_id": self.chat_id,
            "app_id": self.app_id,
            "user_id": self.user_id,
            "tenant_id": self.tenant_id,
            "workspace_id": self.workspace_id,
            "run_id": self.run_id or self.chat_id,
            "operation_id": self.operation_id or self.item_id,
            "request_digest": self.request_digest,
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
            "execution_started_at": self.execution_started_at,
            "completed_result": self.completed_result,
        }

    @classmethod
    def from_document(cls, doc: dict[str, Any]) -> QueueItem:
        """Reconstruct a ``QueueItem`` from a MongoDB document.

        Raises ``ValueError`` if required identity fields (``_id``,
        ``workflow_name``, ``chat_id``, ``app_id``) are missing or empty,
        or if the ``status`` value is not a recognised ``QueueItemStatus``.
        """
        _id = doc.get("_id")
        if not _id:
            raise ValueError("QueueItem document missing required '_id'")
        for field_name in ("workflow_name", "chat_id", "app_id"):
            if not doc.get(field_name):
                raise ValueError(
                    f"QueueItem document missing required '{field_name}'"
                )

        raw_max = doc.get("max_attempts")
        max_attempts = (
            _DEFAULT_MAX_ATTEMPTS if raw_max is None else int(raw_max)
        )

        return cls(
            item_id=str(_id),
            workflow_name=doc["workflow_name"],
            chat_id=doc["chat_id"],
            app_id=doc["app_id"],
            user_id=doc.get("user_id"),
            tenant_id=doc.get("tenant_id"),
            workspace_id=doc.get("workspace_id"),
            run_id=doc.get("run_id") or doc["chat_id"],
            operation_id=doc.get("operation_id") or str(_id),
            request_digest=doc.get("request_digest"),
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
            max_attempts=max_attempts,
            retry_delay_seconds=int(doc.get("retry_delay_seconds", 0)),
            next_attempt_at=doc.get("next_attempt_at"),
            last_failed_at=doc.get("last_failed_at"),
            dead_lettered_at=doc.get("dead_lettered_at"),
            error_category=doc.get("error_category"),
            execution_started_at=doc.get("execution_started_at"),
            completed_result=doc.get("completed_result"),
        )


def _bounded_lease(lease_seconds: int) -> int:
    """Clamp lease duration to safe minimum and maximum."""
    return max(_MIN_LEASE_SECONDS, min(_MAX_LEASE_SECONDS, int(lease_seconds)))


def _validated_max_attempts(value: int | None) -> int:
    """Validate and normalize ``max_attempts`` to a finite bounded integer.

    Returns ``_DEFAULT_MAX_ATTEMPTS`` when *value* is ``None``.
    Rejects booleans, non-positive values, and values above ``_MAX_MAX_ATTEMPTS``.
    """
    if value is None:
        return _DEFAULT_MAX_ATTEMPTS
    if isinstance(value, bool):
        raise ValueError(
            f"max_attempts must be an integer, not bool ({value!r})"
        )
    value = int(value)
    if value < _MIN_MAX_ATTEMPTS:
        raise ValueError(
            f"max_attempts must be >= {_MIN_MAX_ATTEMPTS}, got {value}"
        )
    if value > _MAX_MAX_ATTEMPTS:
        raise ValueError(
            f"max_attempts must be <= {_MAX_MAX_ATTEMPTS}, got {value}"
        )
    return value


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
        """Enqueue a workflow run. Returns the item_id.

        ``max_attempts`` defaults to ``_DEFAULT_MAX_ATTEMPTS`` (3) when
        ``None``.  Validated by ``_validated_max_attempts()`` before storage.
        """
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

    async def dead_letter(
        self,
        item_id: str,
        *,
        claim_token: str,
        error_category: str | None = None,
    ) -> bool:
        """Terminally dead-letter a claimed item.

        Only succeeds for the current ``claim_token`` holder. This is for
        deterministic permanent failures where retrying the same payload cannot
        produce a valid execution, such as malformed contracts or unknown
        registered executors.
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
# No-op (explicit local single-process behavior)
# ---------------------------------------------------------------------------

class NoOpWorkflowQueue:
    """Non-durable single-instance queue stub.

    No cross-instance coordination, no durability guarantees, and no
    implementation of the lease/fencing/retry lifecycle.  Suitable for
    development and testing only.
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
        _validated_max_attempts(max_attempts)  # validate even in NoOp
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

    async def dead_letter(
        self,
        item_id: str,
        *,
        claim_token: str,
        error_category: str | None = None,
    ) -> bool:
        return False

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

    def __init__(self, *, client: Any | None = None, _now_fn: Any | None = None) -> None:
        self._client = client
        self._now_fn = _now_fn if _now_fn is not None else lambda: datetime.now(UTC)

    def _now(self) -> datetime:
        return self._now_fn()

    def _col(self, name: str = _QUEUE_COLLECTION) -> Any | None:
        try:
            client = self._client or get_mongo_client()
            if client is None:
                return None
            return client[_QUEUE_DB][name]
        except Exception:
            return None

    def _required_col(self) -> Any:
        collection = self._col()
        if collection is None:
            raise QueueAuthorityUnavailableError("Mongo workflow queue collection unavailable")
        return collection

    async def ensure_indexes(self) -> None:
        """Create required indexes.  Safe to call on every startup.

        ``expires_at`` TTL index fires only for terminal records (COMPLETED,
        DEAD_LETTER) where the field is non-null.  Active work has
        ``expires_at=None`` and is never touched by the TTL sweeper.
        """
        col = self._required_col()
        try:
            await col.create_index(
                [("tenant_id", 1), ("workspace_id", 1), ("app_id", 1), ("operation_id", 1)],
                name="wq_scope_operation_idx",
                background=True,
            )
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
            # Retention TTL on expires_at (terminal records only; null = skip).
            await col.create_index(
                [("expires_at", 1)],
                expireAfterSeconds=0,
                name="wq_ttl_idx",
                background=True,
            )
            indexes = await col.list_indexes().to_list(length=None)
            names = {str(spec.get("name")) for spec in indexes}
            required = {
                "wq_scope_operation_idx",
                "wq_claim_idx",
                "wq_lease_expiry_idx",
                "wq_retry_idx",
                "wq_ttl_idx",
            }
            if not required.issubset(names):
                raise QueueAuthorityUnavailableError(
                    f"workflow queue indexes not verified: {sorted(required - names)}"
                )
        except Exception as exc:
            raise QueueAuthorityUnavailableError(
                f"workflow queue index creation or verification failed: {exc}"
            ) from exc

    async def enqueue(
        self,
        item: QueueItem,
        *,
        max_attempts: int | None = None,
        retry_delay_seconds: int = 0,
    ) -> str:
        item.max_attempts = _validated_max_attempts(max_attempts)
        item.retry_delay_seconds = max(0, int(retry_delay_seconds))
        col = self._required_col()
        document = item.to_document()
        immutable_fields = (
            "_id",
            "tenant_id",
            "workspace_id",
            "app_id",
            "chat_id",
            "workflow_name",
            "run_id",
            "operation_id",
            "request_digest",
            "user_id",
        )
        try:
            await col.insert_one(document)
            logger.debug(
                "QUEUE_ENQUEUED item_id=%s workflow=%s",
                item.item_id,
                item.workflow_name,
            )
        except DuplicateKeyError:
            existing = await col.find_one({"_id": item.item_id})
            if not isinstance(existing, dict):
                raise QueueAuthorityUnavailableError(
                    "duplicate admission could not be read back"
                ) from None
            mismatched = [
                name for name in immutable_fields if existing.get(name) != document.get(name)
            ]
            if mismatched:
                raise QueueIdentityConflictError(
                    f"admission identity conflict for {item.item_id}: {mismatched}"
                ) from None
        except (QueueAuthorityUnavailableError, QueueIdentityConflictError):
            raise
        except Exception as exc:
            raise QueueAuthorityUnavailableError(f"workflow admission failed: {exc}") from exc
        return item.item_id

    async def get(self, item_id: str) -> QueueItem | None:
        """Read one durable admission, failing closed on authority errors."""
        try:
            doc = await self._required_col().find_one({"_id": item_id})
        except QueueAuthorityUnavailableError:
            raise
        except Exception as exc:
            raise QueueAuthorityUnavailableError(f"workflow admission read failed: {exc}") from exc
        return QueueItem.from_document(doc) if isinstance(doc, dict) else None

    async def claim_item(
        self,
        item_id: str,
        worker_id: str,
        *,
        lease_seconds: int = _DEFAULT_LEASE_SECONDS,
    ) -> ClaimResult:
        """Atomically claim one ingress-owned admission.

        Expired claims are reclaimable only before execution started. Once an
        owner crossed the side-effect boundary, automatic replay is forbidden.
        """
        col = self._required_col()
        now = self._now()
        now_iso = now.isoformat()
        token = str(uuid4())
        lease_exp = (now + timedelta(seconds=_bounded_lease(lease_seconds))).isoformat()
        query = {
            "_id": item_id,
            "$expr": {
                "$lt": [
                    {"$ifNull": ["$attempt_count", 0]},
                    {"$ifNull": ["$max_attempts", _DEFAULT_MAX_ATTEMPTS]},
                ]
            },
            "$or": [
                {"status": QueueItemStatus.PENDING.value},
                {
                    "status": QueueItemStatus.RETRYABLE.value,
                    "$or": [
                        {"next_attempt_at": None},
                        {"next_attempt_at": {"$lte": now_iso}},
                    ],
                },
                {
                    "status": QueueItemStatus.CLAIMED.value,
                    "lease_expires_at": {"$lte": now_iso},
                    "execution_started_at": None,
                },
            ],
        }
        try:
            doc = await col.find_one_and_update(
                query,
                {
                    "$set": {
                        "status": QueueItemStatus.CLAIMED.value,
                        "claimed_by": worker_id,
                        "claim_token": token,
                        "claimed_at": now_iso,
                        "lease_expires_at": lease_exp,
                    },
                    "$inc": {"attempt_count": 1},
                },
                return_document=ReturnDocument.AFTER,
            )
        except Exception as exc:
            raise QueueAuthorityUnavailableError(f"workflow claim failed: {exc}") from exc
        if not isinstance(doc, dict):
            return ClaimResult(claimed=False)
        item = QueueItem.from_document(doc)
        return ClaimResult(True, item, token, item.attempt_count)

    async def mark_execution_started(self, item_id: str, *, claim_token: str) -> bool:
        """Cross the no-retry side-effect boundary for the current holder."""
        now_iso = self._now().isoformat()
        try:
            result = await self._required_col().update_one(
                {
                    "_id": item_id,
                    "status": QueueItemStatus.CLAIMED.value,
                    "claim_token": claim_token,
                    "lease_expires_at": {"$gt": now_iso},
                    "execution_started_at": None,
                },
                {"$set": {"execution_started_at": now_iso}},
            )
        except Exception as exc:
            raise QueueAuthorityUnavailableError(
                f"workflow side-effect boundary could not be recorded: {exc}"
            ) from exc
        return bool(result.modified_count == 1)

    async def dead_letter_exhausted(self, item_id: str) -> bool:
        """Terminally close an expired pre-start claim with no attempts left."""
        now = self._now()
        now_iso = now.isoformat()
        retain_until = (now + timedelta(seconds=_ITEM_TTL)).isoformat()
        try:
            result = await self._required_col().update_one(
                {
                    "_id": item_id,
                    "status": QueueItemStatus.CLAIMED.value,
                    "lease_expires_at": {"$lte": now_iso},
                    "$expr": {
                        "$gte": [
                            {"$ifNull": ["$attempt_count", 0]},
                            {"$ifNull": ["$max_attempts", _DEFAULT_MAX_ATTEMPTS]},
                        ]
                    },
                },
                {
                    "$set": {
                        "status": QueueItemStatus.DEAD_LETTER.value,
                        "dead_lettered_at": now_iso,
                        "last_failed_at": now_iso,
                        "error_category": "claim_attempts_exhausted",
                        "next_attempt_at": None,
                        "expires_at": retain_until,
                    }
                },
            )
        except Exception as exc:
            raise QueueAuthorityUnavailableError(
                f"exhausted workflow claim could not be dead-lettered: {exc}"
            ) from exc
        return bool(result.modified_count == 1)

    async def dead_letter_expired_started(self, item_id: str) -> bool:
        """Terminally refuse automatic replay after an expired started claim."""
        now = self._now()
        now_iso = now.isoformat()
        retain_until = (now + timedelta(seconds=_ITEM_TTL)).isoformat()
        try:
            result = await self._required_col().update_one(
                {
                    "_id": item_id,
                    "status": QueueItemStatus.CLAIMED.value,
                    "lease_expires_at": {"$lte": now_iso},
                    "execution_started_at": {"$ne": None},
                },
                {
                    "$set": {
                        "status": QueueItemStatus.DEAD_LETTER.value,
                        "dead_lettered_at": now_iso,
                        "last_failed_at": now_iso,
                        "error_category": "expired_after_execution_started",
                        "next_attempt_at": None,
                        "expires_at": retain_until,
                    }
                },
            )
        except Exception as exc:
            raise QueueAuthorityUnavailableError(
                f"expired workflow claim could not be dead-lettered: {exc}"
            ) from exc
        return bool(result.modified_count == 1)

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
          - pre-start ``CLAIMED`` with ``lease_expires_at <= now``

        Every claim is also constrained by the item's finite attempt budget.
        Authority failure is distinct from an empty or contended queue.

        Returns ``ClaimResult(claimed=True, ...)`` on success.
        """
        col = self._required_col()

        now = self._now()
        now_iso = now.isoformat()
        ls = _bounded_lease(lease_seconds)
        new_token = str(uuid4())
        lease_exp = (now + timedelta(seconds=ls)).isoformat()

        try:
            doc = await col.find_one_and_update(
                {
                    "$expr": {
                        "$lt": [
                            {"$ifNull": ["$attempt_count", 0]},
                            {"$ifNull": ["$max_attempts", _DEFAULT_MAX_ATTEMPTS]},
                        ]
                    },
                    "$or": [
                        # Normal pending items.
                        {"status": QueueItemStatus.PENDING.value},
                        # Retry-eligible items.
                        {
                            "status": QueueItemStatus.RETRYABLE.value,
                            "$or": [
                                {"next_attempt_at": None},
                                {"next_attempt_at": {"$lte": now_iso}},
                            ],
                        },
                        # Crash recovery: lease expired on a claimed item.
                        {
                            "status": QueueItemStatus.CLAIMED.value,
                            "lease_expires_at": {"$lte": now_iso},
                            "execution_started_at": None,
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
                return_document=ReturnDocument.AFTER,
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
            raise QueueAuthorityUnavailableError(f"workflow claim failed: {exc}") from exc

    async def complete(
        self,
        item_id: str,
        *,
        claim_token: str,
        result: dict[str, Any] | None = None,
    ) -> bool:
        """Mark a claimed item as completed.

        Only succeeds for the current ``claim_token`` holder.
        Sets ``expires_at`` to enable TTL-based retention cleanup.
        """
        col = self._required_col()
        try:
            now = self._now()
            now_iso = now.isoformat()
            retain_until = (now + timedelta(seconds=_ITEM_TTL)).isoformat()
            outcome = result
            update_result = await col.update_one(
                {
                    "_id": item_id,
                    "status": QueueItemStatus.CLAIMED.value,
                    "claim_token": claim_token,
                },
                {
                    "$set": {
                        "status": QueueItemStatus.COMPLETED.value,
                        "completed_at": now_iso,
                        "expires_at": retain_until,
                        "completed_result": outcome,
                    },
                },
            )
            return bool(update_result.modified_count == 1)
        except Exception as exc:
            raise QueueAuthorityUnavailableError(
                f"workflow completion failed for {item_id}: {exc}"
            ) from exc

    async def fail(
        self,
        item_id: str,
        *,
        claim_token: str,
        error_category: str | None = None,
    ) -> str | None:
        """Transition a CLAIMED item to RETRYABLE or DEAD_LETTER.

        Status determination uses values stored in the document at claim time:

        - ``DEAD_LETTER``: ``attempt_count >= max_attempts``.
        - ``RETRYABLE``: ``attempt_count < max_attempts``.

        ``max_attempts`` is always a finite integer (validated at enqueue time).

        ``RETRYABLE`` records become re-claimable after ``next_attempt_at``
        (``now + retry_delay_seconds``; default 0 -> immediately reclaimable).

        ``DEAD_LETTER`` is terminal.

        No raw exception text, stack traces, or credentials are stored.
        ``error_category`` is a short normalized descriptor (<=128 chars).

        Two-step algorithm: a non-binding ``find_one`` reads attempt metadata
        to determine the new status; a fenced ``update_one`` performs the
        actual transition.
        """
        col = self._required_col()

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
            max_attempts_val = int(
                doc.get("max_attempts") or _DEFAULT_MAX_ATTEMPTS
            )
            retry_delay = max(0, int(doc.get("retry_delay_seconds") or 0))
            next_attempt_iso = (now + timedelta(seconds=retry_delay)).isoformat()

            if attempt_count >= max_attempts_val:
                new_status = QueueItemStatus.DEAD_LETTER.value
                dead_lettered_at: str | None = now_iso
                na_value: str | None = None
                retain_until: str | None = (
                    now + timedelta(seconds=_ITEM_TTL)
                ).isoformat()
            else:
                new_status = QueueItemStatus.RETRYABLE.value
                dead_lettered_at = None
                na_value = next_attempt_iso
                retain_until = None  # Not terminal; no TTL yet.

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
                        "expires_at": retain_until,
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
            raise QueueAuthorityUnavailableError(
                f"workflow failure transition failed for {item_id}: {exc}"
            ) from exc

    async def dead_letter(
        self,
        item_id: str,
        *,
        claim_token: str,
        error_category: str | None = None,
    ) -> bool:
        """Terminally dead-letter a CLAIMED item using the fencing token."""
        col = self._required_col()

        now = self._now()
        now_iso = now.isoformat()
        retain_until = (now + timedelta(seconds=_ITEM_TTL)).isoformat()
        try:
            result = await col.update_one(
                {
                    "_id": item_id,
                    "status": QueueItemStatus.CLAIMED.value,
                    "claim_token": claim_token,
                },
                {
                    "$set": {
                        "status": QueueItemStatus.DEAD_LETTER.value,
                        "last_failed_at": now_iso,
                        "next_attempt_at": None,
                        "dead_lettered_at": now_iso,
                        "error_category": (error_category or "permanent_failure")[:128],
                        "expires_at": retain_until,
                    }
                },
            )
            return bool(result.modified_count == 1)
        except Exception as exc:
            raise QueueAuthorityUnavailableError(
                f"workflow dead-letter transition failed for {item_id}: {exc}"
            ) from exc

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
        col = self._required_col()

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
            raise QueueAuthorityUnavailableError(
                f"workflow claim renewal failed for {item_id}: {exc}"
            ) from exc

    async def active_count(self) -> int:
        col = self._required_col()
        try:
            return int(
                await col.count_documents(
                    {"status": QueueItemStatus.CLAIMED.value}
                )
            )
        except Exception as exc:
            raise QueueAuthorityUnavailableError(
                f"workflow active-count read failed: {exc}"
            ) from exc

    async def queue_depth(self) -> int:
        col = self._required_col()
        try:
            return int(
                await col.count_documents(
                    {"status": QueueItemStatus.PENDING.value}
                )
            )
        except Exception as exc:
            raise QueueAuthorityUnavailableError(
                f"workflow queue-depth read failed: {exc}"
            ) from exc


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

_queue: WorkflowQueue | None = None


class WorkflowAdmissionMode(StrEnum):
    REQUIRED = "required"
    LOCAL = "local"


_configured_admission_mode: WorkflowAdmissionMode | None = None


def configure_workflow_admission(
    mode: WorkflowAdmissionMode | str | None = None,
    *,
    client: Any | None = None,
) -> WorkflowAdmissionMode:
    """Pin durable admission for persisted/production hosts and local otherwise."""
    global _configured_admission_mode, _queue
    raw = str(mode or os.getenv("MOZAIKS_WORKFLOW_ADMISSION_MODE") or "").strip().lower()
    if raw:
        resolved = WorkflowAdmissionMode(raw)
    else:
        from mozaiksai.core.runtime.persistence.startup_policy import (
            database_persistence_is_enabled,
            get_database_startup_policy,
        )

        resolved = (
            WorkflowAdmissionMode.REQUIRED
            if database_persistence_is_enabled(get_database_startup_policy())
            else WorkflowAdmissionMode.LOCAL
        )
    _configured_admission_mode = resolved
    _queue = (
        MongoWorkflowQueue(client=client)
        if resolved is WorkflowAdmissionMode.REQUIRED
        else NoOpWorkflowQueue()
    )
    logger.info("Workflow admission mode configured: %s", resolved)
    return resolved


def get_workflow_admission_mode() -> WorkflowAdmissionMode:
    if _configured_admission_mode is not None:
        return _configured_admission_mode
    raw = (os.getenv("MOZAIKS_WORKFLOW_ADMISSION_MODE") or "").strip().lower()
    return WorkflowAdmissionMode(raw) if raw else WorkflowAdmissionMode.LOCAL


def reset_workflow_admission_state() -> None:
    """Test hook for the configured queue and local admission registry."""
    global _configured_admission_mode, _queue
    _configured_admission_mode = None
    _queue = None


def get_workflow_queue() -> WorkflowQueue:
    """Return the configured global workflow queue (process-wide singleton)."""
    global _queue
    if _queue is None:
        if get_workflow_admission_mode() is WorkflowAdmissionMode.REQUIRED:
            _queue = MongoWorkflowQueue()
        else:
            _queue = NoOpWorkflowQueue()
        logger.info(
            "Workflow admission mode: %s (local_max_concurrency=%d)",
            get_workflow_admission_mode(),
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
    "WorkflowAdmissionMode",
    "QueueAuthorityUnavailableError",
    "QueueIdentityConflictError",
    "canonical_admission_id",
    "configure_workflow_admission",
    "get_workflow_queue",
    "get_workflow_admission_mode",
    "reset_workflow_admission_state",
    "_DEFAULT_MAX_ATTEMPTS",
    "_MIN_MAX_ATTEMPTS",
    "_MAX_MAX_ATTEMPTS",
]
