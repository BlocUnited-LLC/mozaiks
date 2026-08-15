"""Crash-safe workflow-queue lease, fencing, and retry contract tests.

Proves:
  - pending -> claimed -> completed lifecycle
  - pending -> claimed -> retryable failure (attempt budget remaining)
  - attempt exhaustion -> dead_letter
  - completed record not reclaimable
  - dead_letter record not reclaimable
  - concurrent workers claim different items (simulated)
  - expired-lease reclaim produces new token and incremented attempt_count
  - priority/order is deterministic
  - stale token cannot complete / fail / renew
  - current token completes / fails / renews successfully
  - unexpired claim not stealable
  - retryable item with future next_attempt_at not claimable
  - retryable item with elapsed next_attempt_at is claimable
  - max_attempts=1: failure -> dead_letter immediately
  - max_attempts=3: 2 failures -> retryable, 3rd failure -> dead_letter
  - max_attempts=None: unlimited retries always produce retryable
  - new queue instance with same storage can reclaim expired work
  - cross-tenant items remain isolated
  - old records without new fields load safely
  - no raw exception stored in error_category
  - ClaimResult contract

All tests use an in-memory mock store -- no MongoDB required.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import pytest

from mozaiksai.core.workflow.queue import (
    ClaimResult,
    QueueItem,
    QueueItemStatus,
)

# ---------------------------------------------------------------------------
# In-memory queue -- mirrors MongoWorkflowQueue state machine without MongoDB
# ---------------------------------------------------------------------------


class _Record:
    """Mutable per-item record stored in the in-memory queue."""

    __slots__ = (
        "doc",
    )

    def __init__(self, doc: dict[str, Any]) -> None:
        self.doc = doc


class InMemoryWorkflowQueue:
    """Drop-in test double implementing the full lease state machine.

    Uses string ISO timestamps for consistency with the production
    MongoWorkflowQueue (which stores ISO strings, not datetime objects).
    """

    def __init__(self, *, now_fn: Any = None) -> None:
        self._records: dict[str, _Record] = {}
        self._now_fn = now_fn or (lambda: datetime.now(UTC))

    def _now(self) -> datetime:
        return self._now_fn()

    async def enqueue(
        self,
        item: QueueItem,
        *,
        max_attempts: int | None = None,
        retry_delay_seconds: int = 0,
    ) -> str:
        item.max_attempts = max_attempts
        item.retry_delay_seconds = max(0, int(retry_delay_seconds))
        doc = item.to_document()
        self._records[item.item_id] = _Record(doc)
        return item.item_id

    async def claim_next(
        self,
        worker_id: str,
        *,
        lease_seconds: int = 300,
    ) -> ClaimResult:
        now = self._now()
        now_iso = now.isoformat()
        ls = max(10, min(3600, int(lease_seconds)))
        new_token = str(uuid4())
        lease_exp = (now + timedelta(seconds=ls)).isoformat()

        # Build candidate list -- same eligibility as MongoWorkflowQueue.
        eligible: list[_Record] = []
        for rec in self._records.values():
            status = rec.doc.get("status")
            if status == "pending":
                eligible.append(rec)
            elif status == "retryable":
                na = rec.doc.get("next_attempt_at")
                if na is not None and na <= now_iso:
                    eligible.append(rec)
                elif na is None:
                    eligible.append(rec)
            elif status == "claimed":
                ct = rec.doc.get("claim_token")
                lea = rec.doc.get("lease_expires_at")
                # Pre-upgrade records (no claim_token).
                if ct is None:
                    eligible.append(rec)
                # Lease expired.
                elif lea is not None and lea <= now_iso:
                    eligible.append(rec)

        if not eligible:
            return ClaimResult(claimed=False)

        # Sort: priority desc, enqueued_at asc (deterministic).
        eligible.sort(
            key=lambda r: (-int(r.doc.get("priority", 0)), r.doc.get("enqueued_at", "")),
        )
        rec = eligible[0]

        # Atomic update.
        rec.doc["status"] = "claimed"
        rec.doc["claimed_by"] = worker_id
        rec.doc["claim_token"] = new_token
        rec.doc["claimed_at"] = now_iso
        rec.doc["lease_expires_at"] = lease_exp
        rec.doc["attempt_count"] = int(rec.doc.get("attempt_count", 0)) + 1

        item = QueueItem.from_document(rec.doc)
        return ClaimResult(
            claimed=True,
            item=item,
            claim_token=new_token,
            attempt_count=item.attempt_count,
        )

    async def complete(self, item_id: str, *, claim_token: str) -> bool:
        rec = self._records.get(item_id)
        if rec is None:
            return False
        if rec.doc.get("status") != "claimed" or rec.doc.get("claim_token") != claim_token:
            return False
        rec.doc["status"] = "completed"
        rec.doc["completed_at"] = self._now().isoformat()
        return True

    async def fail(
        self,
        item_id: str,
        *,
        claim_token: str,
        error_category: str | None = None,
    ) -> str | None:
        rec = self._records.get(item_id)
        if rec is None:
            return None
        if rec.doc.get("status") != "claimed" or rec.doc.get("claim_token") != claim_token:
            return None

        now = self._now()
        now_iso = now.isoformat()
        attempt_count = int(rec.doc.get("attempt_count") or 0)
        max_attempts_val = rec.doc.get("max_attempts")
        retry_delay = max(0, int(rec.doc.get("retry_delay_seconds") or 0))

        if max_attempts_val is not None and attempt_count >= int(max_attempts_val):
            new_status = "dead_letter"
            rec.doc["dead_lettered_at"] = now_iso
            rec.doc["next_attempt_at"] = None
        else:
            new_status = "retryable"
            rec.doc["next_attempt_at"] = (now + timedelta(seconds=retry_delay)).isoformat()
            rec.doc["dead_lettered_at"] = None

        rec.doc["status"] = new_status
        rec.doc["last_failed_at"] = now_iso
        rec.doc["error_category"] = (error_category or "execution_error")[:128]
        return new_status

    async def renew_lease(
        self,
        item_id: str,
        *,
        claim_token: str,
        extend_seconds: int | None = None,
    ) -> bool:
        rec = self._records.get(item_id)
        if rec is None:
            return False
        now = self._now()
        now_iso = now.isoformat()
        if rec.doc.get("status") != "claimed" or rec.doc.get("claim_token") != claim_token:
            return False
        lea = rec.doc.get("lease_expires_at")
        if lea is None or lea <= now_iso:
            return False  # Lease already expired.
        ext = max(10, min(3600, int(extend_seconds or 300)))
        rec.doc["lease_expires_at"] = (now + timedelta(seconds=ext)).isoformat()
        return True

    async def active_count(self) -> int:
        return sum(1 for r in self._records.values() if r.doc.get("status") == "claimed")

    async def queue_depth(self) -> int:
        return sum(1 for r in self._records.values() if r.doc.get("status") == "pending")

    def item_status(self, item_id: str) -> str | None:
        rec = self._records.get(item_id)
        return rec.doc.get("status") if rec else None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _item(
    *,
    item_id: str | None = None,
    workflow_name: str = "test_wf",
    chat_id: str = "chat-1",
    app_id: str = "app-1",
    tenant_id: str = "tenant-1",
    priority: int = 0,
) -> QueueItem:
    return QueueItem(
        item_id=item_id or str(uuid4()),
        workflow_name=workflow_name,
        chat_id=chat_id,
        app_id=app_id,
        tenant_id=tenant_id,
        priority=priority,
    )


class _Clock:
    """Controllable clock for tests."""

    def __init__(self, start: datetime | None = None) -> None:
        self._time = start or datetime(2025, 1, 1, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self._time

    def advance(self, seconds: int) -> None:
        self._time += timedelta(seconds=seconds)

    def set(self, dt: datetime) -> None:
        self._time = dt


# ---------------------------------------------------------------------------
# 1. Basic lifecycle: pending -> claimed -> completed
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pending_to_claimed_to_completed() -> None:
    q = InMemoryWorkflowQueue()
    item = _item()
    await q.enqueue(item)
    assert q.item_status(item.item_id) == "pending"

    claim = await q.claim_next("worker-1")
    assert claim.claimed is True
    assert claim.claim_token is not None
    assert claim.attempt_count == 1
    assert claim.item is not None
    assert claim.item.item_id == item.item_id
    assert q.item_status(item.item_id) == "claimed"

    ok = await q.complete(item.item_id, claim_token=claim.claim_token)
    assert ok is True
    assert q.item_status(item.item_id) == "completed"


# ---------------------------------------------------------------------------
# 2. Basic lifecycle: pending -> claimed -> retryable failure
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pending_to_claimed_to_retryable() -> None:
    q = InMemoryWorkflowQueue()
    item = _item()
    await q.enqueue(item, max_attempts=3)

    claim = await q.claim_next("worker-1")
    assert claim.claimed

    new_status = await q.fail(item.item_id, claim_token=claim.claim_token)
    assert new_status == "retryable"
    assert q.item_status(item.item_id) == "retryable"


# ---------------------------------------------------------------------------
# 3. Attempt exhaustion -> dead_letter
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_attempt_exhaustion_produces_dead_letter() -> None:
    q = InMemoryWorkflowQueue()
    item = _item()
    await q.enqueue(item, max_attempts=2)

    # Attempt 1: claim + fail.
    c1 = await q.claim_next("w-1")
    assert c1.attempt_count == 1
    s1 = await q.fail(item.item_id, claim_token=c1.claim_token)
    assert s1 == "retryable"

    # Attempt 2: claim + fail -> dead_letter.
    c2 = await q.claim_next("w-1")
    assert c2.attempt_count == 2
    s2 = await q.fail(item.item_id, claim_token=c2.claim_token)
    assert s2 == "dead_letter"
    assert q.item_status(item.item_id) == "dead_letter"


# ---------------------------------------------------------------------------
# 4. Completed record not reclaimable
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_completed_not_reclaimable() -> None:
    q = InMemoryWorkflowQueue()
    item = _item()
    await q.enqueue(item)

    c = await q.claim_next("w-1")
    await q.complete(item.item_id, claim_token=c.claim_token)
    assert q.item_status(item.item_id) == "completed"

    # Try to claim again -- nothing eligible.
    c2 = await q.claim_next("w-2")
    assert c2.claimed is False


# ---------------------------------------------------------------------------
# 5. Dead_letter record not reclaimable
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dead_letter_not_reclaimable() -> None:
    q = InMemoryWorkflowQueue()
    item = _item()
    await q.enqueue(item, max_attempts=1)

    c = await q.claim_next("w-1")
    await q.fail(item.item_id, claim_token=c.claim_token)
    assert q.item_status(item.item_id) == "dead_letter"

    c2 = await q.claim_next("w-2")
    assert c2.claimed is False


# ---------------------------------------------------------------------------
# 6. Concurrent workers claim different items
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_concurrent_workers_claim_different_items() -> None:
    q = InMemoryWorkflowQueue()
    item_a = _item(item_id="a")
    item_b = _item(item_id="b")
    await q.enqueue(item_a)
    await q.enqueue(item_b)

    c1 = await q.claim_next("w-1")
    assert c1.claimed
    c2 = await q.claim_next("w-2")
    assert c2.claimed
    assert c1.item.item_id != c2.item.item_id


# ---------------------------------------------------------------------------
# 7. Same item cannot be claimed twice (while lease valid)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_same_item_not_double_claimed() -> None:
    q = InMemoryWorkflowQueue()
    item = _item()
    await q.enqueue(item)

    c1 = await q.claim_next("w-1")
    assert c1.claimed

    # No other items in queue -- second claim returns nothing.
    c2 = await q.claim_next("w-2")
    assert c2.claimed is False


# ---------------------------------------------------------------------------
# 8. Expired-lease reclaim produces new token and incremented attempt
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_expired_lease_reclaim() -> None:
    clock = _Clock()
    q = InMemoryWorkflowQueue(now_fn=clock)
    item = _item()
    await q.enqueue(item)

    c1 = await q.claim_next("w-1", lease_seconds=60)
    assert c1.claimed
    assert c1.attempt_count == 1

    # Advance past lease expiry.
    clock.advance(61)

    c2 = await q.claim_next("w-2", lease_seconds=60)
    assert c2.claimed is True, "Expired lease must be reclaimable"
    assert c2.claim_token != c1.claim_token, "Reclaim must issue a new token"
    assert c2.attempt_count == 2


# ---------------------------------------------------------------------------
# 9. Priority/order is deterministic
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_priority_ordering() -> None:
    q = InMemoryWorkflowQueue()
    low = _item(item_id="low", priority=0)
    high = _item(item_id="high", priority=10)
    await q.enqueue(low)
    await q.enqueue(high)

    c = await q.claim_next("w-1")
    assert c.claimed
    assert c.item.item_id == "high", "Higher priority must be claimed first"


# ---------------------------------------------------------------------------
# 10. Fencing: stale token cannot complete
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stale_token_cannot_complete() -> None:
    q = InMemoryWorkflowQueue()
    item = _item()
    await q.enqueue(item)

    _c = await q.claim_next("w-1")  # noqa: F841
    result = await q.complete(item.item_id, claim_token="wrong-token")
    assert result is False
    assert q.item_status(item.item_id) == "claimed"


# ---------------------------------------------------------------------------
# 11. Fencing: stale token cannot fail
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stale_token_cannot_fail() -> None:
    q = InMemoryWorkflowQueue()
    item = _item()
    await q.enqueue(item)

    await q.claim_next("w-1")
    result = await q.fail(item.item_id, claim_token="wrong-token")
    assert result is None
    assert q.item_status(item.item_id) == "claimed"


# ---------------------------------------------------------------------------
# 12. Fencing: stale token cannot renew
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stale_token_cannot_renew() -> None:
    q = InMemoryWorkflowQueue()
    item = _item()
    await q.enqueue(item)

    await q.claim_next("w-1")
    result = await q.renew_lease(item.item_id, claim_token="wrong-token")
    assert result is False


# ---------------------------------------------------------------------------
# 13. Fencing: current token completes successfully
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_current_token_completes() -> None:
    q = InMemoryWorkflowQueue()
    item = _item()
    await q.enqueue(item)

    c = await q.claim_next("w-1")
    ok = await q.complete(item.item_id, claim_token=c.claim_token)
    assert ok is True
    assert q.item_status(item.item_id) == "completed"


# ---------------------------------------------------------------------------
# 14. Fencing: current token fails successfully
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_current_token_fails() -> None:
    q = InMemoryWorkflowQueue()
    item = _item()
    await q.enqueue(item)

    c = await q.claim_next("w-1")
    new_status = await q.fail(item.item_id, claim_token=c.claim_token)
    assert new_status == "retryable"


# ---------------------------------------------------------------------------
# 15. Fencing: current token renews successfully
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_current_token_renews() -> None:
    q = InMemoryWorkflowQueue()
    item = _item()
    await q.enqueue(item)

    c = await q.claim_next("w-1")
    ok = await q.renew_lease(item.item_id, claim_token=c.claim_token)
    assert ok is True


# ---------------------------------------------------------------------------
# 16. Unexpired claim not stealable
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unexpired_claim_not_stealable() -> None:
    clock = _Clock()
    q = InMemoryWorkflowQueue(now_fn=clock)
    item = _item()
    await q.enqueue(item)

    c = await q.claim_next("w-1", lease_seconds=300)
    assert c.claimed

    # Only advance 10 seconds -- lease still valid.
    clock.advance(10)
    c2 = await q.claim_next("w-2")
    assert c2.claimed is False, "Unexpired lease must not allow another claim"


# ---------------------------------------------------------------------------
# 17. New token issued on reclaim after expired lease
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_new_token_on_reclaim() -> None:
    clock = _Clock()
    q = InMemoryWorkflowQueue(now_fn=clock)
    item = _item()
    await q.enqueue(item)

    c1 = await q.claim_next("w-1", lease_seconds=30)
    clock.advance(31)

    c2 = await q.claim_next("w-2", lease_seconds=30)
    assert c2.claimed
    assert c2.claim_token != c1.claim_token


# ---------------------------------------------------------------------------
# 18. Attempt count increments exactly once per claim
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_attempt_count_increments() -> None:
    clock = _Clock()
    q = InMemoryWorkflowQueue(now_fn=clock)
    item = _item()
    await q.enqueue(item, max_attempts=5)

    c1 = await q.claim_next("w-1", lease_seconds=30)
    assert c1.attempt_count == 1
    await q.fail(item.item_id, claim_token=c1.claim_token)

    c2 = await q.claim_next("w-1")
    assert c2.attempt_count == 2
    await q.fail(item.item_id, claim_token=c2.claim_token)

    c3 = await q.claim_next("w-1")
    assert c3.attempt_count == 3


# ---------------------------------------------------------------------------
# 19. Completed record survives simulated lease expiration
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_completed_survives_lease_expiry() -> None:
    clock = _Clock()
    q = InMemoryWorkflowQueue(now_fn=clock)
    item = _item()
    await q.enqueue(item)

    c = await q.claim_next("w-1", lease_seconds=30)
    await q.complete(item.item_id, claim_token=c.claim_token)

    # Fast forward well past lease expiry.
    clock.advance(3600)
    assert q.item_status(item.item_id) == "completed"

    # Must not be reclaimable.
    c2 = await q.claim_next("w-2")
    assert c2.claimed is False


# ---------------------------------------------------------------------------
# 20. Dead_letter record survives simulated lease expiration
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dead_letter_survives_lease_expiry() -> None:
    clock = _Clock()
    q = InMemoryWorkflowQueue(now_fn=clock)
    item = _item()
    await q.enqueue(item, max_attempts=1)

    c = await q.claim_next("w-1", lease_seconds=30)
    await q.fail(item.item_id, claim_token=c.claim_token)

    clock.advance(3600)
    assert q.item_status(item.item_id) == "dead_letter"

    c2 = await q.claim_next("w-2")
    assert c2.claimed is False


# ---------------------------------------------------------------------------
# 21. Retryable with future next_attempt_at not claimable
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_retryable_future_not_claimable() -> None:
    clock = _Clock()
    q = InMemoryWorkflowQueue(now_fn=clock)
    item = _item()
    await q.enqueue(item, retry_delay_seconds=60)

    c = await q.claim_next("w-1")
    await q.fail(item.item_id, claim_token=c.claim_token)
    assert q.item_status(item.item_id) == "retryable"

    # next_attempt_at is now + 60s; only advance 10s.
    clock.advance(10)
    c2 = await q.claim_next("w-1")
    assert c2.claimed is False, "Retryable with future next_attempt_at must not be claimable"


# ---------------------------------------------------------------------------
# 22. Retryable with elapsed next_attempt_at is claimable
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_retryable_elapsed_is_claimable() -> None:
    clock = _Clock()
    q = InMemoryWorkflowQueue(now_fn=clock)
    item = _item()
    await q.enqueue(item, retry_delay_seconds=60)

    c = await q.claim_next("w-1")
    await q.fail(item.item_id, claim_token=c.claim_token)

    # Advance past retry delay.
    clock.advance(61)
    c2 = await q.claim_next("w-1")
    assert c2.claimed is True
    assert c2.attempt_count == 2


# ---------------------------------------------------------------------------
# 23. max_attempts=1: failure -> dead_letter immediately
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_max_attempts_1_dead_letter() -> None:
    q = InMemoryWorkflowQueue()
    item = _item()
    await q.enqueue(item, max_attempts=1)

    c = await q.claim_next("w-1")
    assert c.attempt_count == 1
    s = await q.fail(item.item_id, claim_token=c.claim_token)
    assert s == "dead_letter"


# ---------------------------------------------------------------------------
# 24. max_attempts=3: 2 failures -> retryable, 3rd failure -> dead_letter
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_max_attempts_3_lifecycle() -> None:
    q = InMemoryWorkflowQueue()
    item = _item()
    await q.enqueue(item, max_attempts=3)

    c1 = await q.claim_next("w-1")
    s1 = await q.fail(item.item_id, claim_token=c1.claim_token)
    assert s1 == "retryable"

    c2 = await q.claim_next("w-1")
    s2 = await q.fail(item.item_id, claim_token=c2.claim_token)
    assert s2 == "retryable"

    c3 = await q.claim_next("w-1")
    s3 = await q.fail(item.item_id, claim_token=c3.claim_token)
    assert s3 == "dead_letter"


# ---------------------------------------------------------------------------
# 25. max_attempts=None: unlimited retries
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_max_attempts_none_unlimited() -> None:
    q = InMemoryWorkflowQueue()
    item = _item()
    await q.enqueue(item, max_attempts=None)

    for i in range(10):
        c = await q.claim_next("w-1")
        assert c.claimed
        s = await q.fail(item.item_id, claim_token=c.claim_token)
        assert s == "retryable", f"Attempt {i + 1}: unlimited must always produce retryable"


# ---------------------------------------------------------------------------
# 26. Retry delay is calculated correctly
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_retry_delay_calculation() -> None:
    clock = _Clock()
    q = InMemoryWorkflowQueue(now_fn=clock)
    item = _item()
    await q.enqueue(item, retry_delay_seconds=120)

    c = await q.claim_next("w-1")
    await q.fail(item.item_id, claim_token=c.claim_token)

    # Should not be claimable until 120s have passed.
    clock.advance(119)
    c2 = await q.claim_next("w-1")
    assert c2.claimed is False

    clock.advance(2)  # Now at 121s total.
    c3 = await q.claim_next("w-1")
    assert c3.claimed is True


# ---------------------------------------------------------------------------
# 27. New queue instance reclaims expired work
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_new_instance_reclaims_expired() -> None:
    clock = _Clock()
    q1 = InMemoryWorkflowQueue(now_fn=clock)
    item = _item()
    await q1.enqueue(item)

    c1 = await q1.claim_next("w-1", lease_seconds=30)
    assert c1.claimed

    # Simulate crash: create a new queue with the same storage.
    clock.advance(31)
    q2 = InMemoryWorkflowQueue(now_fn=clock)
    q2._records = q1._records  # Share storage.

    c2 = await q2.claim_next("w-2", lease_seconds=60)
    assert c2.claimed is True
    assert c2.claim_token != c1.claim_token
    assert c2.attempt_count == 2


# ---------------------------------------------------------------------------
# 28. Cross-tenant items remain isolated
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cross_tenant_isolation() -> None:
    q = InMemoryWorkflowQueue()
    item_a = _item(item_id="a", tenant_id="tenant-A")
    item_b = _item(item_id="b", tenant_id="tenant-B")
    await q.enqueue(item_a)
    await q.enqueue(item_b)

    # Both should be independently claimable.
    c1 = await q.claim_next("w-1")
    c2 = await q.claim_next("w-2")
    assert c1.claimed
    assert c2.claimed

    tenants = {c1.item.tenant_id, c2.item.tenant_id}
    assert tenants == {"tenant-A", "tenant-B"}


# ---------------------------------------------------------------------------
# 29. Old records without new fields load safely
# ---------------------------------------------------------------------------


def test_old_document_without_new_fields() -> None:
    """QueueItem.from_document handles pre-upgrade documents that lack
    claim_token, lease_expires_at, attempt_count, etc."""
    old_doc = {
        "_id": "old-item-1",
        "workflow_name": "wf",
        "chat_id": "chat",
        "app_id": "app",
        "status": "claimed",
        "claimed_by": "worker-old",
        "claimed_at": "2024-01-01T00:00:00+00:00",
        "enqueued_at": "2024-01-01T00:00:00+00:00",
        "expires_at": "2024-01-02T00:00:00+00:00",
    }
    item = QueueItem.from_document(old_doc)
    assert item.item_id == "old-item-1"
    assert item.claim_token is None
    assert item.lease_expires_at is None
    assert item.attempt_count == 0
    assert item.max_attempts is None
    assert item.error_category is None


# ---------------------------------------------------------------------------
# 30. Old CLAIMED records without claim_token are reclaimable
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_old_claimed_records_reclaimable() -> None:
    """Pre-upgrade CLAIMED records (no claim_token) are immediately reclaimable."""
    q = InMemoryWorkflowQueue()

    # Manually inject an old-style claimed record.
    old_doc = {
        "_id": "old-item",
        "workflow_name": "wf",
        "chat_id": "chat",
        "app_id": "app",
        "tenant_id": "t",
        "status": "claimed",
        "claimed_by": "old-worker",
        "claim_token": None,
        "lease_expires_at": None,
        "attempt_count": 0,
        "priority": 0,
        "enqueued_at": "2024-01-01T00:00:00+00:00",
        "expires_at": "2025-01-01T00:00:00+00:00",
        "payload": {},
    }
    q._records["old-item"] = _Record(old_doc)

    c = await q.claim_next("w-new")
    assert c.claimed is True
    assert c.item.item_id == "old-item"
    assert c.claim_token is not None
    assert c.attempt_count == 1


# ---------------------------------------------------------------------------
# 31. No raw exception stored in error_category
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_error_category_truncation() -> None:
    q = InMemoryWorkflowQueue()
    item = _item()
    await q.enqueue(item)

    c = await q.claim_next("w-1")
    long_category = "x" * 200
    await q.fail(item.item_id, claim_token=c.claim_token, error_category=long_category)

    rec = q._records[item.item_id]
    stored = rec.doc.get("error_category", "")
    assert len(stored) == 128
    assert stored == "x" * 128


@pytest.mark.asyncio
async def test_error_category_default() -> None:
    q = InMemoryWorkflowQueue()
    item = _item()
    await q.enqueue(item)

    c = await q.claim_next("w-1")
    await q.fail(item.item_id, claim_token=c.claim_token, error_category=None)

    rec = q._records[item.item_id]
    assert rec.doc.get("error_category") == "execution_error"


# ---------------------------------------------------------------------------
# 32. ClaimResult dataclass contract
# ---------------------------------------------------------------------------


def test_claim_result_defaults() -> None:
    cr = ClaimResult(claimed=False)
    assert cr.item is None
    assert cr.claim_token is None
    assert cr.attempt_count == 0


def test_claim_result_values() -> None:
    cr = ClaimResult(claimed=True, claim_token="tok", attempt_count=3)
    assert cr.claimed is True
    assert cr.claim_token == "tok"
    assert cr.attempt_count == 3


# ---------------------------------------------------------------------------
# 33. QueueItemStatus enum includes new states
# ---------------------------------------------------------------------------


def test_queue_item_status_values() -> None:
    assert "retryable" in [s.value for s in QueueItemStatus]
    assert "dead_letter" in [s.value for s in QueueItemStatus]
    assert "pending" in [s.value for s in QueueItemStatus]
    assert "claimed" in [s.value for s in QueueItemStatus]
    assert "completed" in [s.value for s in QueueItemStatus]


# ---------------------------------------------------------------------------
# 34. Renew lease: expired lease cannot be renewed
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_expired_lease_cannot_be_renewed() -> None:
    clock = _Clock()
    q = InMemoryWorkflowQueue(now_fn=clock)
    item = _item()
    await q.enqueue(item)

    c = await q.claim_next("w-1", lease_seconds=30)
    clock.advance(31)

    ok = await q.renew_lease(item.item_id, claim_token=c.claim_token)
    assert ok is False, "Expired lease must not be renewable"


# ---------------------------------------------------------------------------
# 35. Active count and queue depth
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_active_count_and_queue_depth() -> None:
    q = InMemoryWorkflowQueue()
    await q.enqueue(_item(item_id="a"))
    await q.enqueue(_item(item_id="b"))
    await q.enqueue(_item(item_id="c"))

    assert await q.queue_depth() == 3
    assert await q.active_count() == 0

    c1 = await q.claim_next("w-1")
    assert await q.queue_depth() == 2
    assert await q.active_count() == 1

    _c2 = await q.claim_next("w-2")  # noqa: F841
    assert await q.queue_depth() == 1
    assert await q.active_count() == 2

    await q.complete(c1.item.item_id, claim_token=c1.claim_token)
    assert await q.active_count() == 1


# ---------------------------------------------------------------------------
# 36. max_attempts=3 but attempt 3 succeeds -> completed
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_max_attempts_3_third_attempt_succeeds() -> None:
    q = InMemoryWorkflowQueue()
    item = _item()
    await q.enqueue(item, max_attempts=3)

    c1 = await q.claim_next("w-1")
    await q.fail(item.item_id, claim_token=c1.claim_token)

    c2 = await q.claim_next("w-1")
    await q.fail(item.item_id, claim_token=c2.claim_token)

    c3 = await q.claim_next("w-1")
    assert c3.attempt_count == 3
    ok = await q.complete(item.item_id, claim_token=c3.claim_token)
    assert ok is True
    assert q.item_status(item.item_id) == "completed"


# ---------------------------------------------------------------------------
# 37. NoOpWorkflowQueue behavior
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_noop_queue_behavior() -> None:
    """NoOpWorkflowQueue preserves per-instance semaphore behavior --
    no durability, no lease lifecycle."""
    from mozaiksai.core.workflow.queue import NoOpWorkflowQueue

    q = NoOpWorkflowQueue()
    item = _item()

    item_id = await q.enqueue(item)
    assert item_id == item.item_id

    claim = await q.claim_next("w-1")
    assert claim.claimed is False

    ok = await q.complete("any", claim_token="any")
    assert ok is False

    status = await q.fail("any", claim_token="any")
    assert status is None

    renew = await q.renew_lease("any", claim_token="any")
    assert renew is False

    assert await q.active_count() == 0
    assert await q.queue_depth() == 0


# ---------------------------------------------------------------------------
# 38. Stale worker: old token rejected after reclaim
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stale_worker_after_reclaim() -> None:
    """After a lease expires and the item is reclaimed by a new worker,
    the old worker's token is rejected for both complete and fail."""
    clock = _Clock()
    q = InMemoryWorkflowQueue(now_fn=clock)
    item = _item()
    await q.enqueue(item)

    c1 = await q.claim_next("w-old", lease_seconds=30)
    clock.advance(31)

    c2 = await q.claim_next("w-new", lease_seconds=300)
    assert c2.claimed

    # Old worker tries to complete -- rejected.
    ok = await q.complete(item.item_id, claim_token=c1.claim_token)
    assert ok is False

    # Old worker tries to fail -- rejected.
    s = await q.fail(item.item_id, claim_token=c1.claim_token)
    assert s is None

    # New worker can complete.
    ok2 = await q.complete(item.item_id, claim_token=c2.claim_token)
    assert ok2 is True


# ---------------------------------------------------------------------------
# 39. retry_delay_seconds=0 means immediately reclaimable
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_zero_retry_delay_immediately_reclaimable() -> None:
    q = InMemoryWorkflowQueue()
    item = _item()
    await q.enqueue(item, retry_delay_seconds=0)

    c1 = await q.claim_next("w-1")
    await q.fail(item.item_id, claim_token=c1.claim_token)

    # Immediately reclaimable.
    c2 = await q.claim_next("w-1")
    assert c2.claimed is True


# ---------------------------------------------------------------------------
# 40. QueueItem round-trip through to_document/from_document preserves fields
# ---------------------------------------------------------------------------


def test_queue_item_round_trip() -> None:
    item = QueueItem(
        workflow_name="wf",
        chat_id="chat-1",
        app_id="app-1",
        tenant_id="t-1",
        priority=5,
        max_attempts=3,
        retry_delay_seconds=10,
        claim_token="tok-abc",
        attempt_count=2,
        error_category="timeout",
    )
    doc = item.to_document()
    restored = QueueItem.from_document(doc)

    assert restored.workflow_name == "wf"
    assert restored.priority == 5
    assert restored.max_attempts == 3
    assert restored.retry_delay_seconds == 10
    assert restored.claim_token == "tok-abc"
    assert restored.attempt_count == 2
    assert restored.error_category == "timeout"


# ---------------------------------------------------------------------------
# 41. Enqueue sets max_attempts and retry_delay on the item
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_enqueue_sets_retry_config() -> None:
    q = InMemoryWorkflowQueue()
    item = _item()
    await q.enqueue(item, max_attempts=5, retry_delay_seconds=30)

    rec = q._records[item.item_id]
    assert rec.doc["max_attempts"] == 5
    assert rec.doc["retry_delay_seconds"] == 30
