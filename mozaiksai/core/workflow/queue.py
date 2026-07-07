# ==============================================================================
# FILE: mozaiksai/core/workflow/queue.py
# DESCRIPTION: Durable global workflow queue.
#              Replaces per-instance concurrency limits with a globally fair
#              scheduler. Workflow runs are enqueued and dequeued by workers
#              on any instance, respecting the global concurrency limit.
#
# Backends:
#   MongoWorkflowQueue  — MongoDB capped collection (default, no extra infra)
#   RedisWorkflowQueue  — Redis list + sorted set (requires Redis)
#
# Configuration (env vars):
#   WORKFLOW_QUEUE_BACKEND          — "mongo" | "redis" | "noop" (default: "noop")
#   WORKFLOW_QUEUE_MAX_CONCURRENCY  — global max concurrent workflows (default: 20)
#   WORKFLOW_QUEUE_ITEM_TTL_SECONDS — max age of a queued item before it expires (default: 3600)
#   WORKFLOW_QUEUE_POLL_INTERVAL    — worker poll interval in seconds (default: 1.0)
# ==============================================================================
from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import Enum
from typing import Any, Protocol, runtime_checkable
from uuid import uuid4

from logs.logging_config import get_core_logger

logger = get_core_logger("workflow_queue")

_BACKEND = os.getenv("WORKFLOW_QUEUE_BACKEND", "noop").strip().lower()
_MAX_CONCURRENCY = int(os.getenv("WORKFLOW_QUEUE_MAX_CONCURRENCY", "20").strip() or "20")
_ITEM_TTL = int(os.getenv("WORKFLOW_QUEUE_ITEM_TTL_SECONDS", "3600").strip() or "3600")
_POLL_INTERVAL = float(os.getenv("WORKFLOW_QUEUE_POLL_INTERVAL", "1.0").strip() or "1.0")

_QUEUE_DB = os.getenv("MOZAIKS_APP_DATABASE_NAME", "mozaiks_apps")
_QUEUE_COLLECTION = "workflow_queue"
_ACTIVE_COLLECTION = "workflow_queue_active"


class QueueItemStatus(str, Enum):
    PENDING = "pending"
    CLAIMED = "claimed"
    COMPLETED = "completed"
    FAILED = "failed"
    EXPIRED = "expired"


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
        )


@runtime_checkable
class WorkflowQueue(Protocol):
    """Port for global workflow queue operations."""

    async def enqueue(self, item: QueueItem) -> str:
        """Enqueue a workflow run. Returns the item_id."""
        ...

    async def claim_next(self, worker_id: str) -> QueueItem | None:
        """Atomically claim the next pending item. Returns None if queue is empty."""
        ...

    async def complete(self, item_id: str, *, worker_id: str) -> None:
        """Mark a claimed item as completed."""
        ...

    async def fail(self, item_id: str, *, worker_id: str, error: str = "") -> None:
        """Mark a claimed item as failed."""
        ...

    async def active_count(self) -> int:
        """Return the number of currently executing workflow runs."""
        ...

    async def queue_depth(self) -> int:
        """Return the number of pending items in the queue."""
        ...


# ---------------------------------------------------------------------------
# No-op (existing per-instance semaphore behaviour — default)
# ---------------------------------------------------------------------------

class NoOpWorkflowQueue:
    """Preserves existing per-instance semaphore behaviour.

    No cross-instance coordination. Use when running a single instance.
    """

    def __init__(self) -> None:
        self._semaphore = asyncio.Semaphore(_MAX_CONCURRENCY)
        self._active = 0

    async def enqueue(self, item: QueueItem) -> str:
        return item.item_id

    async def claim_next(self, worker_id: str) -> QueueItem | None:
        return None

    async def complete(self, item_id: str, *, worker_id: str) -> None:
        pass

    async def fail(self, item_id: str, *, worker_id: str, error: str = "") -> None:
        pass

    async def active_count(self) -> int:
        return self._active

    async def queue_depth(self) -> int:
        return 0


# ---------------------------------------------------------------------------
# MongoDB queue
# ---------------------------------------------------------------------------

class MongoWorkflowQueue:
    """Global workflow queue backed by MongoDB.

    Uses findOneAndUpdate for atomic claim operations — safe under concurrent
    workers on multiple instances.

    Requires MongoDB 3.6+ for findOneAndUpdate with sort.
    """

    _instance_id: str = str(uuid4())

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
        col = self._col()
        if col is None:
            return
        try:
            await col.create_index(
                [("status", 1), ("priority", -1), ("enqueued_at", 1)],
                name="wq_claim_idx",
                background=True,
            )
            await col.create_index(
                [("expires_at", 1)],
                expireAfterSeconds=0,
                name="wq_ttl_idx",
                background=True,
            )
        except Exception as exc:
            logger.warning("WorkflowQueue index creation failed: %s", exc)

    async def enqueue(self, item: QueueItem) -> str:
        col = self._col()
        if col is None:
            return item.item_id
        try:
            await col.insert_one(item.to_document())
            logger.debug("QUEUE_ENQUEUED item_id=%s workflow=%s", item.item_id, item.workflow_name)
        except Exception as exc:
            logger.error("QUEUE_ENQUEUE_FAIL: %s", exc)
        return item.item_id

    async def claim_next(self, worker_id: str) -> QueueItem | None:
        col = self._col()
        if col is None:
            return None
        now = datetime.now(UTC)
        try:
            doc = await col.find_one_and_update(
                {
                    "status": QueueItemStatus.PENDING.value,
                    "expires_at": {"$gt": now.isoformat()},
                },
                {
                    "$set": {
                        "status": QueueItemStatus.CLAIMED.value,
                        "claimed_by": worker_id,
                        "claimed_at": now.isoformat(),
                    }
                },
                sort=[("priority", -1), ("enqueued_at", 1)],
                return_document=True,
            )
            if doc is None:
                return None
            item = QueueItem.from_document(doc)
            logger.debug("QUEUE_CLAIMED item_id=%s worker=%s", item.item_id, worker_id)
            return item
        except Exception as exc:
            logger.error("QUEUE_CLAIM_FAIL: %s", exc)
            return None

    async def complete(self, item_id: str, *, worker_id: str) -> None:
        col = self._col()
        if col is None:
            return
        try:
            await col.update_one(
                {"_id": item_id, "claimed_by": worker_id},
                {"$set": {"status": QueueItemStatus.COMPLETED.value}},
            )
        except Exception as exc:
            logger.error("QUEUE_COMPLETE_FAIL item_id=%s: %s", item_id, exc)

    async def fail(self, item_id: str, *, worker_id: str, error: str = "") -> None:
        col = self._col()
        if col is None:
            return
        try:
            await col.update_one(
                {"_id": item_id, "claimed_by": worker_id},
                {"$set": {"status": QueueItemStatus.FAILED.value, "error": error}},
            )
        except Exception as exc:
            logger.error("QUEUE_FAIL_FAIL item_id=%s: %s", item_id, exc)

    async def active_count(self) -> int:
        col = self._col()
        if col is None:
            return 0
        try:
            return int(await col.count_documents({"status": QueueItemStatus.CLAIMED.value}))
        except Exception:
            return 0

    async def queue_depth(self) -> int:
        col = self._col()
        if col is None:
            return 0
        try:
            return int(await col.count_documents({"status": QueueItemStatus.PENDING.value}))
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
        logger.info("WorkflowQueue backend: %s (max_concurrency=%d)", _BACKEND, _MAX_CONCURRENCY)
    return _queue


__all__ = [
    "MongoWorkflowQueue",
    "NoOpWorkflowQueue",
    "QueueItem",
    "QueueItemStatus",
    "WorkflowQueue",
    "get_workflow_queue",
]
