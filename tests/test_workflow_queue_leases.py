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
  - finite default max_attempts: 3 failures with default -> dead_letter
  - max_attempts validation: reject bool, 0, negative, >25
  - new queue instance with same storage can reclaim expired work
  - cross-tenant items remain isolated
  - from_document rejects malformed documents
  - no raw exception stored in error_category
  - ClaimResult contract
  - expires_at set only at terminal transitions (retention TTL safety)
  - NoOp behavior explicitly tested and documented as non-durable

All tests use an in-memory mock store -- no MongoDB required.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import pytest

from mozaiksai.core.workflow.queue import (
    _DEFAULT_MAX_ATTEMPTS,
    _MAX_MAX_ATTEMPTS,
    _MIN_MAX_ATTEMPTS,
    ClaimResult,
    QueueItem,
    QueueItemStatus,
    _validated_max_attempts,
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

    Validates ``max_attempts`` via ``_validated_max_attempts()`` at enqueue,
    matching production behavior.
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
        item.max_attempts = _validated_max_attempts(max_attempts)
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
                lea = rec.doc.get("lease_expires_at")
                # Lease expired -- crash recovery.
                if lea is not None and lea <= now_iso:
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
        now = self._now()
        rec.doc["status"] = "completed"
        rec.doc["completed_at"] = now.isoformat()
        # Set expires_at at terminal transition for TTL retention.
        rec.doc["expires_at"] = (now + timedelta(seconds=3600)).isoformat()
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
        max_attempts_val = int(rec.doc.get("max_attempts") or _DEFAULT_MAX_ATTEMPTS)
        retry_delay = max(0, int(rec.doc.get("retry_delay_seconds") or 0))

        if attempt_count >= max_attempts_val:
            new_status = "dead_letter"
            rec.doc["dead_lettered_at"] = now_iso
            rec.doc["next_attempt_at"] = None
            # Set expires_at at terminal transition for TTL retention.
            rec.doc["expires_at"] = (now + timedelta(seconds=3600)).isoformat()
        else:
            new_status = "retryable"
            rec.doc["next_attempt_at"] = (now + timedelta(seconds=retry_delay)).isoformat()
            rec.doc["dead_lettered_at"] = None
            rec.doc["expires_at"] = None  # Not terminal; no TTL.

        rec.doc["status"] = new_status
        rec.doc["last_failed_at"] = now_iso
        rec.doc["error_category"] = (error_category or "execution_error")[:128]
        return new_status

    async def dead_letter(
        self,
        item_id: str,
        *,
        claim_token: str,
        error_category: str | None = None,
    ) -> bool:
        rec = self._records.get(item_id)
        if rec is None:
            return False
        if rec.doc.get("status") != "claimed" or rec.doc.get("claim_token") != claim_token:
            return False

        now = self._now()
        now_iso = now.isoformat()
        rec.doc["status"] = "dead_letter"
        rec.doc["last_failed_at"] = now_iso
        rec.doc["dead_lettered_at"] = now_iso
        rec.doc["next_attempt_at"] = None
        rec.doc["error_category"] = (error_category or "permanent_failure")[:128]
        rec.doc["expires_at"] = (now + timedelta(seconds=3600)).isoformat()
        return True

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

    def item_expires_at(self, item_id: str) -> str | None:
        rec = self._records.get(item_id)
        return rec.doc.get("expires_at") if rec else None


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
# 25. Finite default: 3 failures with default max_attempts -> dead_letter
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_finite_default_max_attempts_produces_dead_letter() -> None:
    """max_attempts defaults to _DEFAULT_MAX_ATTEMPTS (3).
    After 3 claim+fail cycles, status must be dead_letter."""
    q = InMemoryWorkflowQueue()
    item = _item()
    await q.enqueue(item)  # No explicit max_attempts -> default 3

    for i in range(_DEFAULT_MAX_ATTEMPTS):
        c = await q.claim_next("w-1")
        assert c.claimed, f"Attempt {i + 1} must be claimable"
        s = await q.fail(item.item_id, claim_token=c.claim_token)
        if i < _DEFAULT_MAX_ATTEMPTS - 1:
            assert s == "retryable", f"Attempt {i + 1}: budget remaining -> retryable"
        else:
            assert s == "dead_letter", f"Attempt {i + 1}: budget exhausted -> dead_letter"

    assert q.item_status(item.item_id) == "dead_letter"


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
# 29. from_document rejects malformed documents (strict schema)
# ---------------------------------------------------------------------------


def test_from_document_rejects_missing_id() -> None:
    """QueueItem.from_document raises ValueError when _id is missing."""
    with pytest.raises(ValueError, match="missing required '_id'"):
        QueueItem.from_document({"workflow_name": "wf", "chat_id": "c", "app_id": "a"})


def test_from_document_rejects_missing_workflow_name() -> None:
    with pytest.raises(ValueError, match="missing required 'workflow_name'"):
        QueueItem.from_document({"_id": "x", "chat_id": "c", "app_id": "a"})


def test_from_document_rejects_missing_chat_id() -> None:
    with pytest.raises(ValueError, match="missing required 'chat_id'"):
        QueueItem.from_document({"_id": "x", "workflow_name": "wf", "app_id": "a"})


def test_from_document_rejects_missing_app_id() -> None:
    with pytest.raises(ValueError, match="missing required 'app_id'"):
        QueueItem.from_document({"_id": "x", "workflow_name": "wf", "chat_id": "c"})


def test_from_document_rejects_invalid_status() -> None:
    with pytest.raises(ValueError):
        QueueItem.from_document({
            "_id": "x", "workflow_name": "wf", "chat_id": "c",
            "app_id": "a", "status": "invalid_status_xyz",
        })


def test_from_document_defaults_max_attempts_when_missing() -> None:
    """Documents without max_attempts get the canonical default."""
    item = QueueItem.from_document({
        "_id": "x", "workflow_name": "wf", "chat_id": "c", "app_id": "a",
    })
    assert item.max_attempts == _DEFAULT_MAX_ATTEMPTS


# ---------------------------------------------------------------------------
# 30. No raw exception stored in error_category
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
# 31. ClaimResult dataclass contract
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
# 32. QueueItemStatus enum values (no FAILED or EXPIRED)
# ---------------------------------------------------------------------------


def test_queue_item_status_values() -> None:
    values = [s.value for s in QueueItemStatus]
    assert "retryable" in values
    assert "dead_letter" in values
    assert "pending" in values
    assert "claimed" in values
    assert "completed" in values
    # Removed pre-production speculative statuses
    assert "failed" not in values, "FAILED removed; DEAD_LETTER is the only terminal failure state"
    assert "expired" not in values, "EXPIRED removed; no legitimate use"


# ---------------------------------------------------------------------------
# 33. Renew lease: expired lease cannot be renewed
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
# 34. Active count and queue depth
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
# 35. max_attempts=3 but attempt 3 succeeds -> completed
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
# 36. NoOpWorkflowQueue behavior (explicitly non-durable)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_noop_queue_behavior() -> None:
    """NoOpWorkflowQueue is explicitly non-durable.  Suitable for
    development and testing only.  Does not implement lifecycle guarantees."""
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


@pytest.mark.asyncio
async def test_noop_queue_validates_max_attempts() -> None:
    """NoOpWorkflowQueue still validates max_attempts at enqueue time."""
    from mozaiksai.core.workflow.queue import NoOpWorkflowQueue

    q = NoOpWorkflowQueue()
    item = _item()

    with pytest.raises(ValueError, match="bool"):
        await q.enqueue(item, max_attempts=True)

    with pytest.raises(ValueError, match=">= 1"):
        await q.enqueue(item, max_attempts=0)


# ---------------------------------------------------------------------------
# 37. Stale worker: old token rejected after reclaim
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
# 38. Stale worker: old token cannot renew after reclaim
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stale_worker_cannot_renew_after_reclaim() -> None:
    clock = _Clock()
    q = InMemoryWorkflowQueue(now_fn=clock)
    item = _item()
    await q.enqueue(item)

    c1 = await q.claim_next("w-old", lease_seconds=30)
    clock.advance(31)

    c2 = await q.claim_next("w-new", lease_seconds=300)
    assert c2.claimed

    # Old worker tries to renew -- rejected (wrong token).
    ok = await q.renew_lease(item.item_id, claim_token=c1.claim_token)
    assert ok is False

    # New worker can renew.
    ok2 = await q.renew_lease(item.item_id, claim_token=c2.claim_token)
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


# ---------------------------------------------------------------------------
# 42. max_attempts validation: reject bool
# ---------------------------------------------------------------------------


def test_validated_max_attempts_rejects_bool_true() -> None:
    with pytest.raises(ValueError, match="bool"):
        _validated_max_attempts(True)


def test_validated_max_attempts_rejects_bool_false() -> None:
    with pytest.raises(ValueError, match="bool"):
        _validated_max_attempts(False)


# ---------------------------------------------------------------------------
# 43. max_attempts validation: reject zero
# ---------------------------------------------------------------------------


def test_validated_max_attempts_rejects_zero() -> None:
    with pytest.raises(ValueError, match=f">= {_MIN_MAX_ATTEMPTS}"):
        _validated_max_attempts(0)


# ---------------------------------------------------------------------------
# 44. max_attempts validation: reject negative
# ---------------------------------------------------------------------------


def test_validated_max_attempts_rejects_negative() -> None:
    with pytest.raises(ValueError, match=f">= {_MIN_MAX_ATTEMPTS}"):
        _validated_max_attempts(-5)


# ---------------------------------------------------------------------------
# 45. max_attempts validation: reject above maximum
# ---------------------------------------------------------------------------


def test_validated_max_attempts_rejects_above_max() -> None:
    with pytest.raises(ValueError, match=f"<= {_MAX_MAX_ATTEMPTS}"):
        _validated_max_attempts(_MAX_MAX_ATTEMPTS + 1)


# ---------------------------------------------------------------------------
# 46. max_attempts validation: None -> default
# ---------------------------------------------------------------------------


def test_validated_max_attempts_none_returns_default() -> None:
    assert _validated_max_attempts(None) == _DEFAULT_MAX_ATTEMPTS


# ---------------------------------------------------------------------------
# 47. max_attempts validation: valid values accepted
# ---------------------------------------------------------------------------


def test_validated_max_attempts_valid_values() -> None:
    assert _validated_max_attempts(1) == 1
    assert _validated_max_attempts(3) == 3
    assert _validated_max_attempts(25) == 25


# ---------------------------------------------------------------------------
# 48. Retention TTL safety: PENDING item has no expires_at
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pending_item_has_no_expires_at() -> None:
    """PENDING items must have expires_at=None so the TTL index
    cannot delete active work."""
    q = InMemoryWorkflowQueue()
    item = _item()
    await q.enqueue(item)
    assert q.item_expires_at(item.item_id) is None


# ---------------------------------------------------------------------------
# 49. Retention TTL safety: CLAIMED item has no expires_at
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_claimed_item_has_no_expires_at() -> None:
    q = InMemoryWorkflowQueue()
    item = _item()
    await q.enqueue(item)
    await q.claim_next("w-1")
    assert q.item_expires_at(item.item_id) is None


# ---------------------------------------------------------------------------
# 50. Retention TTL safety: RETRYABLE item has no expires_at
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_retryable_item_has_no_expires_at() -> None:
    q = InMemoryWorkflowQueue()
    item = _item()
    await q.enqueue(item)
    c = await q.claim_next("w-1")
    await q.fail(item.item_id, claim_token=c.claim_token)
    assert q.item_status(item.item_id) == "retryable"
    assert q.item_expires_at(item.item_id) is None


# ---------------------------------------------------------------------------
# 51. Retention TTL: COMPLETED item gets expires_at set
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_completed_item_gets_expires_at() -> None:
    q = InMemoryWorkflowQueue()
    item = _item()
    await q.enqueue(item)
    c = await q.claim_next("w-1")
    await q.complete(item.item_id, claim_token=c.claim_token)
    assert q.item_status(item.item_id) == "completed"
    assert q.item_expires_at(item.item_id) is not None


# ---------------------------------------------------------------------------
# 52. Retention TTL: DEAD_LETTER item gets expires_at set
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dead_letter_item_gets_expires_at() -> None:
    q = InMemoryWorkflowQueue()
    item = _item()
    await q.enqueue(item, max_attempts=1)
    c = await q.claim_next("w-1")
    await q.fail(item.item_id, claim_token=c.claim_token)
    assert q.item_status(item.item_id) == "dead_letter"
    assert q.item_expires_at(item.item_id) is not None


# ---------------------------------------------------------------------------
# 53. Replacement worker can complete after reclaim
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_replacement_worker_completes_after_reclaim() -> None:
    clock = _Clock()
    q = InMemoryWorkflowQueue(now_fn=clock)
    item = _item()
    await q.enqueue(item)

    _c1 = await q.claim_next("w-old", lease_seconds=30)  # noqa: F841
    clock.advance(31)

    c2 = await q.claim_next("w-new", lease_seconds=300)
    assert c2.claimed
    assert c2.attempt_count == 2

    ok = await q.complete(item.item_id, claim_token=c2.claim_token)
    assert ok is True
    assert q.item_status(item.item_id) == "completed"


# ---------------------------------------------------------------------------
# 54. Fenced immediate dead-letter for deterministic permanent failures
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dead_letter_uses_current_fencing_token() -> None:
    q = InMemoryWorkflowQueue()
    item = _item()
    await q.enqueue(item, max_attempts=3)
    claim = await q.claim_next("worker")
    assert claim.claimed

    assert await q.dead_letter(item.item_id, claim_token="stale-token") is False
    assert q.item_status(item.item_id) == "claimed"

    assert await q.dead_letter(
        item.item_id,
        claim_token=claim.claim_token,
        error_category="invalid_payload",
    ) is True
    assert q.item_status(item.item_id) == "dead_letter"
    assert q.item_expires_at(item.item_id) is not None
    doc = q._records[item.item_id].doc
    assert doc["error_category"] == "invalid_payload"
    assert doc["next_attempt_at"] is None


@pytest.mark.asyncio
async def test_stale_dead_letter_rejected_after_reclaim() -> None:
    clock = _Clock()
    q = InMemoryWorkflowQueue(now_fn=clock)
    item = _item()
    await q.enqueue(item, max_attempts=3)
    old = await q.claim_next("w-old", lease_seconds=30)
    assert old.claimed
    clock.advance(31)
    new = await q.claim_next("w-new", lease_seconds=30)
    assert new.claimed

    assert await q.dead_letter(item.item_id, claim_token=old.claim_token) is False
    assert q.item_status(item.item_id) == "claimed"

    assert await q.dead_letter(item.item_id, claim_token=new.claim_token) is True
    assert q.item_status(item.item_id) == "dead_letter"


# ---------------------------------------------------------------------------
# 55. Enqueue with max_attempts=None uses default
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_enqueue_max_attempts_none_uses_default() -> None:
    q = InMemoryWorkflowQueue()
    item = _item()
    await q.enqueue(item, max_attempts=None)

    rec = q._records[item.item_id]
    assert rec.doc["max_attempts"] == _DEFAULT_MAX_ATTEMPTS


# ---------------------------------------------------------------------------
# 56. QueueItem dataclass default max_attempts
# ---------------------------------------------------------------------------


def test_queue_item_default_max_attempts() -> None:
    item = QueueItem(workflow_name="wf", chat_id="c", app_id="a")
    assert item.max_attempts == _DEFAULT_MAX_ATTEMPTS
