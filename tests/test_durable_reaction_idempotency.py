"""Durable reaction idempotency contract tests.

Proves the restart-safe duplicate suppression guarantee:

  - same reaction replay within one process → in-memory suppression (PR #256)
  - same reaction replay after new router instance → durable store suppression
  - different event identity → allowed
  - different tenant → allowed
  - different app → allowed
  - failed execution → mark_failed transitions to retryable → retry allowed
  - max_attempts exhausted → dead_letter → permanently suppressed
  - claim_token fencing → stale worker cannot complete or fail newer attempt
  - no raw payload stored in ledger records

All tests use an in-memory mock store — no MongoDB required.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import MagicMock

import pytest

from mozaiksai.core.runtime.composition.module_event_router import ModuleEventRouter
from mozaiksai.core.runtime.composition.reaction_idempotency_store import (
    LeaseClaim,
    ReactionIdempotencyStore,
)

# ---------------------------------------------------------------------------
# In-memory mock store — mirrors ReactionIdempotencyStore state machine
# without MongoDB.
# ---------------------------------------------------------------------------


class _Record:
    """Mutable per-key ledger record."""

    __slots__ = (
        "status",
        "claim_token",
        "attempt_count",
        "max_attempts",
        "lease_expires_at",
        "next_attempt_at",
    )

    def __init__(
        self,
        *,
        status: str,
        claim_token: str,
        attempt_count: int,
        max_attempts: int | None,
        lease_expires_at: datetime,
    ) -> None:
        self.status = status
        self.claim_token = claim_token
        self.attempt_count = attempt_count
        self.max_attempts = max_attempts
        self.lease_expires_at = lease_expires_at
        self.next_attempt_at: datetime | None = None


class _InMemoryIdempotencyStore(ReactionIdempotencyStore):
    """Drop-in test double that implements the full state machine without MongoDB."""

    def __init__(self, *, now_fn: Any = None) -> None:
        super().__init__(client=object())  # prevents real client creation
        self._records: dict[str, _Record] = {}
        self._now_fn = now_fn or (lambda: datetime.now(UTC))

    def _collection(self) -> Any:  # type: ignore[override]
        raise RuntimeError("_InMemoryIdempotencyStore must not call _collection()")

    def _now(self) -> datetime:
        return self._now_fn()

    async def claim(
        self,
        *,
        app_id: str,
        tenant_id: str | None,
        workspace_id: str | None,
        idempotency_key_str: str,
        lease_seconds: int = 300,
        max_attempts: int | None = None,
    ) -> LeaseClaim:
        from uuid import uuid4

        scope = f"{app_id}|{tenant_id or ''}|{workspace_id or ''}|{idempotency_key_str}"
        now = self._now()
        new_token = str(uuid4())
        ls = max(10, min(3600, int(lease_seconds)))

        rec = self._records.get(scope)

        # No record → first claim.
        if rec is None:
            self._records[scope] = _Record(
                status="claimed",
                claim_token=new_token,
                attempt_count=1,
                max_attempts=max_attempts,
                lease_expires_at=now + timedelta(seconds=ls),
            )
            return LeaseClaim(claimed=True, claim_token=new_token, attempt_count=1)

        # Terminal states → suppress.
        if rec.status in ("completed", "dead_letter"):
            return LeaseClaim(claimed=False)

        # Active lease still held → suppress.
        if rec.status == "claimed" and rec.lease_expires_at >= now:
            return LeaseClaim(claimed=False)

        # Retryable but not yet eligible.
        if rec.status == "retryable" and rec.next_attempt_at is not None and rec.next_attempt_at > now:
            return LeaseClaim(claimed=False)

        # Reclaimable: expired lease or retry-eligible.
        rec.status = "claimed"
        rec.claim_token = new_token
        rec.attempt_count += 1
        rec.max_attempts = max_attempts  # allow caller to update
        rec.lease_expires_at = now + timedelta(seconds=ls)
        rec.next_attempt_at = None
        return LeaseClaim(claimed=True, claim_token=new_token, attempt_count=rec.attempt_count)

    async def complete(
        self,
        *,
        app_id: str,
        tenant_id: str | None,
        workspace_id: str | None,
        idempotency_key_str: str,
        claim_token: str,
    ) -> bool:
        scope = f"{app_id}|{tenant_id or ''}|{workspace_id or ''}|{idempotency_key_str}"
        rec = self._records.get(scope)
        if rec is None or rec.status != "claimed" or rec.claim_token != claim_token:
            return False
        rec.status = "completed"
        return True

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
        from datetime import timedelta

        scope = f"{app_id}|{tenant_id or ''}|{workspace_id or ''}|{idempotency_key_str}"
        rec = self._records.get(scope)
        if rec is None or rec.status != "claimed" or rec.claim_token != claim_token:
            return None
        now = self._now()
        if rec.max_attempts is not None and rec.attempt_count >= rec.max_attempts:
            rec.status = "dead_letter"
            rec.next_attempt_at = None
            return "dead_letter"
        rec.status = "retryable"
        rec.next_attempt_at = now + timedelta(seconds=max(0, int(retry_delay_seconds)))
        return "retryable"

    async def ensure_indexes(self) -> None:
        pass

    def status(self, *, app_id: str, tenant_id: str | None, workspace_id: str | None, idempotency_key_str: str) -> str | None:
        scope = f"{app_id}|{tenant_id or ''}|{workspace_id or ''}|{idempotency_key_str}"
        rec = self._records.get(scope)
        return rec.status if rec is not None else None


# ---------------------------------------------------------------------------
# Module / reaction fixtures
# ---------------------------------------------------------------------------


def _handler_reaction(
    event_type: str,
    *,
    reaction_id: str = "r-1",
    handler_method: str = "on_order",
    idempotency_key: str = "order_id",
    module_id: str = "orders",
    max_attempts: int | None = None,
    retry_delay_seconds: int = 0,
    lease_seconds: int = 300,
) -> MagicMock:
    m = MagicMock()
    m.event_type = event_type
    dump: dict[str, Any] = {
        "id": reaction_id,
        "module_id": module_id,
        "idempotency_key": idempotency_key,
        "target": {"kind": "handler", "handler_method": handler_method},
        "lease_seconds": lease_seconds,
        "retry_delay_seconds": retry_delay_seconds,
    }
    if max_attempts is not None:
        dump["max_attempts"] = max_attempts
    m.model_dump.return_value = dump
    return m


def _loaded_module(name: str, *, handler: Any = None, reactions=None) -> MagicMock:
    m = MagicMock()
    m.name = name
    m.handler = handler or MagicMock()
    m.definition = MagicMock(actions=[], capabilities=[])
    if reactions is not None:
        reactions_manifest = MagicMock()
        reactions_manifest.reactions = reactions
        m.manifests.reactions = reactions_manifest
    else:
        m.manifests.reactions = None
    m.manifests.notifications = None
    m.manifests.events = None
    return m


def _envelope(
    event_id: str = "evt_abc123",
    *,
    app_id: str = "app-1",
    tenant_id: str = "tenant-1",
    workspace_id: str | None = None,
    actor_id: str = "user-1",
    order_id: str = "order-99",
) -> dict[str, Any]:
    return {
        "id": event_id,
        "type": "order.created",
        "app_id": app_id,
        "tenant_id": tenant_id,
        **({"workspace_id": workspace_id} if workspace_id else {}),
        "payload": {
            "app_id": app_id,
            "tenant_id": tenant_id,
            "actor_id": actor_id,
            "order_id": order_id,
        },
    }


def _make_handler(outcomes: list[str]) -> Any:
    """Return a handler whose on_order method produces the given outcomes in sequence."""
    call_idx = 0

    class _Handler:
        async def on_order(self, ctx: Any, **kwargs: Any) -> dict[str, Any]:
            nonlocal call_idx
            outcome = outcomes[min(call_idx, len(outcomes) - 1)]
            call_idx += 1
            if outcome == "error":
                raise RuntimeError("handler failed")
            return {"status": outcome}

    return _Handler()


def _router_with_store(
    store: ReactionIdempotencyStore | None,
    handler: Any,
    *,
    module_id: str = "orders",
    event_type: str = "order.created",
    max_attempts: int | None = None,
    retry_delay_seconds: int = 0,
) -> ModuleEventRouter:
    reaction = _handler_reaction(
        event_type,
        module_id=module_id,
        max_attempts=max_attempts,
        retry_delay_seconds=retry_delay_seconds,
    )
    module = _loaded_module(module_id, handler=handler, reactions=[reaction])
    return ModuleEventRouter(
        [module],
        idempotency_store=store,
    )


# ---------------------------------------------------------------------------
# 1. Same-process replay → in-memory suppression (no store interaction needed)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_same_process_replay_suppressed_in_memory() -> None:
    """Replay of the same event within one router instance is suppressed by the
    in-memory set — the store's claim is only called once."""
    store = _InMemoryIdempotencyStore()
    handler = _make_handler(["ok", "ok"])
    router = _router_with_store(store, handler)
    envelope = _envelope("evt_001")

    await router.handle_event("order.created", envelope)
    await router.handle_event("order.created", envelope)

    # Only one claim, one complete; second call short-circuited by in-memory check.
    assert len(store._records) == 1
    statuses = [rec.status for rec in store._records.values()]
    assert statuses.count("completed") == 1


# ---------------------------------------------------------------------------
# 2. New router instance + same event → durable store suppresses replay
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_new_router_instance_replay_suppressed_by_durable_store() -> None:
    """A completed reaction is suppressed even after the process restarts
    (simulated by creating a fresh router that shares the same store)."""
    store = _InMemoryIdempotencyStore()
    handler = _make_handler(["ok", "ok"])
    envelope = _envelope("evt_001")

    # First router: claim + execute + complete.
    router_a = _router_with_store(store, handler)
    await router_a.handle_event("order.created", envelope)
    assert all(rec.status == "completed" for rec in store._records.values())

    # Second router: fresh in-memory set, same store. Must suppress.
    execution_count = 0
    original = handler.on_order

    async def counting_on_order(ctx: Any, **kwargs: Any) -> dict:
        nonlocal execution_count
        execution_count += 1
        return await original(**kwargs)

    handler.on_order = counting_on_order
    router_b = _router_with_store(store, handler)
    await router_b.handle_event("order.created", envelope)

    assert execution_count == 0, "Durable store must suppress replay after new router instance"


# ---------------------------------------------------------------------------
# 3. Different event identity → NOT suppressed
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_different_event_identity_allowed() -> None:
    """Two events with different IDs trigger two distinct reaction executions."""
    store = _InMemoryIdempotencyStore()
    execution_count = 0

    class _CountHandler:
        async def on_order(self, ctx: Any, **kwargs: Any) -> dict:
            nonlocal execution_count
            execution_count += 1
            return {"status": "ok"}

    router = _router_with_store(store, _CountHandler())
    await router.handle_event("order.created", _envelope("evt_001"))
    await router.handle_event("order.created", _envelope("evt_002"))

    assert execution_count == 2
    assert len(store._records) == 2


# ---------------------------------------------------------------------------
# 4. Different tenant → NOT suppressed
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_different_tenant_not_suppressed() -> None:
    """Reactions for different tenants with the same event identity are independent."""
    store = _InMemoryIdempotencyStore()
    execution_count = 0

    class _TenantHandler:
        async def on_order(self, ctx: Any, **kwargs: Any) -> dict:
            nonlocal execution_count
            execution_count += 1
            return {"status": "ok"}

    router = _router_with_store(store, _TenantHandler())
    await router.handle_event("order.created", _envelope("evt_001", tenant_id="tenant-A"))
    # Create a second router to bypass in-memory set for the second tenant.
    router2 = _router_with_store(store, _TenantHandler())
    await router2.handle_event("order.created", _envelope("evt_001", tenant_id="tenant-B"))

    assert execution_count == 2


# ---------------------------------------------------------------------------
# 5. Different app → NOT suppressed
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_different_app_not_suppressed() -> None:
    """Reactions for different apps are isolated by the ledger scope."""
    store = _InMemoryIdempotencyStore()
    execution_count = 0

    class _AppHandler:
        async def on_order(self, ctx: Any, **kwargs: Any) -> dict:
            nonlocal execution_count
            execution_count += 1
            return {"status": "ok"}

    router_a = _router_with_store(store, _AppHandler())
    router_b = _router_with_store(store, _AppHandler())
    await router_a.handle_event("order.created", _envelope("evt_001", app_id="app-A"))
    await router_b.handle_event("order.created", _envelope("evt_001", app_id="app-B"))

    assert execution_count == 2
    assert len(store._records) == 2


# ---------------------------------------------------------------------------
# 6. Failed execution → mark_failed → retryable → retry allowed
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_failed_execution_transitions_to_retryable_then_retry_succeeds() -> None:
    """A handler that raises causes mark_failed to set retryable status.
    The next attempt (via a fresh router) can claim and execute."""
    store = _InMemoryIdempotencyStore()
    execution_count = 0

    class _FlappyHandler:
        _attempt = 0

        async def on_order(self, ctx: Any, **kwargs: Any) -> dict:
            nonlocal execution_count
            self.__class__._attempt += 1
            execution_count += 1
            if self.__class__._attempt == 1:
                raise RuntimeError("transient failure")
            return {"status": "ok"}

    handler = _FlappyHandler()
    # Router A: first attempt — handler raises, slot should become retryable.
    router_a = _router_with_store(store, handler)
    await router_a.handle_event("order.created", _envelope("evt_001"))
    # Slot must be retryable (not completed, not absent).
    assert len(store._records) == 1
    statuses = [rec.status for rec in store._records.values()]
    assert "retryable" in statuses, (
        "Failed execution must transition to retryable, not delete the record"
    )

    # Router B: second attempt — slot is retry-eligible (next_attempt_at = now), handler succeeds.
    router_b = _router_with_store(store, handler)
    await router_b.handle_event("order.created", _envelope("evt_001"))
    assert execution_count == 2
    statuses_after = [rec.status for rec in store._records.values()]
    assert "completed" in statuses_after


# ---------------------------------------------------------------------------
# 7. Ledger records do not contain raw event payload
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ledger_record_contains_no_raw_payload() -> None:
    """The idempotency key string stored in the ledger must not reproduce
    raw customer payload.  It encodes structural identity only."""
    store = _InMemoryIdempotencyStore()
    handler = _make_handler(["ok"])
    router = _router_with_store(store, handler)
    sensitive_payload = {"order_id": "order-99", "card_number": "4111-1111-1111-1111"}
    envelope = {
        "id": "evt_sensitive",
        "type": "order.created",
        "app_id": "app-1",
        "tenant_id": "tenant-1",
        "payload": {**sensitive_payload, "app_id": "app-1", "tenant_id": "tenant-1"},
    }
    await router.handle_event("order.created", envelope)

    for key in store._records:
        assert "4111-1111-1111-1111" not in key, (
            "Durable idempotency key must not reproduce raw customer payload"
        )


# ---------------------------------------------------------------------------
# 8. max_attempts=1 → single failure → dead_letter (not retryable)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_max_attempts_exhausted_produces_dead_letter() -> None:
    """When max_attempts=1 and the handler fails, status becomes dead_letter."""
    store = _InMemoryIdempotencyStore()
    handler = _make_handler(["error"])

    router = _router_with_store(store, handler, max_attempts=1)
    await router.handle_event("order.created", _envelope("evt_001"))

    statuses = [rec.status for rec in store._records.values()]
    assert "dead_letter" in statuses, "Single failure with max_attempts=1 must dead_letter"


# ---------------------------------------------------------------------------
# 9. Dead-letter is terminal — second attempt is permanently suppressed
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dead_letter_is_terminal() -> None:
    """A dead_letter reaction is suppressed on every subsequent delivery."""
    store = _InMemoryIdempotencyStore()
    execution_count = 0

    class _Handler:
        _attempt = 0

        async def on_order(self, ctx: Any, **kwargs: Any) -> dict:
            nonlocal execution_count
            execution_count += 1
            self.__class__._attempt += 1
            if self.__class__._attempt == 1:
                raise RuntimeError("fail")
            return {"status": "ok"}

    router_a = _router_with_store(store, _Handler(), max_attempts=1)
    await router_a.handle_event("order.created", _envelope("evt_001"))
    assert any(rec.status == "dead_letter" for rec in store._records.values())

    # Second router should not re-execute.
    router_b = _router_with_store(store, _Handler(), max_attempts=1)
    await router_b.handle_event("order.created", _envelope("evt_001"))

    assert execution_count == 1, "Dead-letter must permanently suppress re-execution"


# ---------------------------------------------------------------------------
# 10. max_attempts=3 → 2 failures then success
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_max_attempts_3_two_failures_then_success() -> None:
    """With max_attempts=3, two failures produce retryable; third attempt succeeds → completed."""
    store = _InMemoryIdempotencyStore()
    execution_count = 0
    fail_count = 0

    class _Handler:
        async def on_order(self, ctx: Any, **kwargs: Any) -> dict:
            nonlocal execution_count, fail_count
            execution_count += 1
            if fail_count < 2:
                fail_count += 1
                raise RuntimeError("transient")
            return {"status": "ok"}

    handler = _Handler()
    for _ in range(3):
        router = _router_with_store(store, handler, max_attempts=3)
        await router.handle_event("order.created", _envelope("evt_001"))

    assert execution_count == 3
    statuses = [rec.status for rec in store._records.values()]
    assert "completed" in statuses


# ---------------------------------------------------------------------------
# 11. Fencing — complete() with wrong token returns False
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_complete_with_wrong_token_returns_false() -> None:
    """complete() with a mismatched claim_token must return False (stale worker rejected)."""
    store = _InMemoryIdempotencyStore()
    key = dict(
        app_id="app-1",
        tenant_id="tenant-1",
        workspace_id=None,
        idempotency_key_str="key-1",
    )
    # Claim once.
    claim = await store.claim(**key, lease_seconds=300)
    assert claim.claimed
    assert claim.claim_token is not None

    # complete() with the wrong token must fail.
    rejected = await store.complete(**key, claim_token="wrong-token")
    assert rejected is False

    # The record should remain claimed.
    assert store.status(**key) == "claimed"


# ---------------------------------------------------------------------------
# 12. Fencing — mark_failed() with wrong token returns None
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mark_failed_with_wrong_token_returns_none() -> None:
    """mark_failed() with a mismatched claim_token must return None (stale worker rejected)."""
    store = _InMemoryIdempotencyStore()
    key = dict(
        app_id="app-1",
        tenant_id="tenant-1",
        workspace_id=None,
        idempotency_key_str="key-1",
    )
    claim = await store.claim(**key, lease_seconds=300)
    assert claim.claimed

    result = await store.mark_failed(**key, claim_token="wrong-token")
    assert result is None

    # Record still claimed.
    assert store.status(**key) == "claimed"


# ---------------------------------------------------------------------------
# 13. Fencing — correct token completes successfully
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_complete_with_correct_token() -> None:
    """complete() with the correct claim_token transitions to completed."""
    store = _InMemoryIdempotencyStore()
    key = dict(
        app_id="app-1",
        tenant_id="tenant-1",
        workspace_id=None,
        idempotency_key_str="key-1",
    )
    claim = await store.claim(**key, lease_seconds=300)
    assert claim.claimed

    success = await store.complete(**key, claim_token=claim.claim_token)
    assert success is True
    assert store.status(**key) == "completed"


# ---------------------------------------------------------------------------
# 14. Completed record suppresses re-claim
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_completed_record_suppresses_reclaim() -> None:
    """A completed slot cannot be re-claimed even with an expired lease."""
    store = _InMemoryIdempotencyStore()
    key = dict(
        app_id="app-1",
        tenant_id="tenant-1",
        workspace_id=None,
        idempotency_key_str="key-1",
    )
    claim = await store.claim(**key, lease_seconds=300)
    await store.complete(**key, claim_token=claim.claim_token)

    second = await store.claim(**key)
    assert second.claimed is False


# ---------------------------------------------------------------------------
# 15. Expired-lease reclaim returns new claim_token (crash recovery)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_expired_lease_is_reclaimable() -> None:
    """A claimed record with an expired lease can be atomically reclaimed."""
    past = datetime(2020, 1, 1, tzinfo=UTC)
    future = datetime(2099, 1, 1, tzinfo=UTC)

    call_count = 0

    def _clock() -> datetime:
        nonlocal call_count
        call_count += 1
        # First claim → past (so lease immediately expires).
        # Subsequent claims → future.
        if call_count == 1:
            return past
        return future

    store = _InMemoryIdempotencyStore(now_fn=_clock)
    key = dict(
        app_id="app-1",
        tenant_id="tenant-1",
        workspace_id=None,
        idempotency_key_str="key-1",
    )
    first = await store.claim(**key, lease_seconds=10)
    assert first.claimed
    assert first.attempt_count == 1

    # Lease already expired (lease_expires_at = past + 10s < future).
    second = await store.claim(**key, lease_seconds=300)
    assert second.claimed, "Expired lease must be reclaimable"
    assert second.claim_token != first.claim_token, "Reclaim must issue a new token"
    assert second.attempt_count == 2


# ---------------------------------------------------------------------------
# 16. Retryable with future next_attempt_at is NOT reclaimable yet
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_retryable_future_next_attempt_not_reclaimable() -> None:
    """A retryable record whose next_attempt_at is in the future must not be claimed."""
    store = _InMemoryIdempotencyStore()
    key = dict(
        app_id="app-1",
        tenant_id="tenant-1",
        workspace_id=None,
        idempotency_key_str="key-1",
    )
    claim = await store.claim(**key)
    # Mark failed with a 60-second retry delay — next_attempt_at is in the future.
    status = await store.mark_failed(**key, claim_token=claim.claim_token, retry_delay_seconds=60)
    assert status == "retryable"

    # Immediate re-claim should be rejected.
    second = await store.claim(**key)
    assert second.claimed is False, "Retryable with future next_attempt_at must not be re-claimed"


# ---------------------------------------------------------------------------
# 17. LeaseClaim dataclass contract
# ---------------------------------------------------------------------------


def test_lease_claim_defaults() -> None:
    """LeaseClaim(claimed=False) must be constructible with defaults."""
    lc = LeaseClaim(claimed=False)
    assert lc.claim_token is None
    assert lc.attempt_count == 0


def test_lease_claim_frozen() -> None:
    """LeaseClaim must be immutable."""
    lc = LeaseClaim(claimed=True, claim_token="tok", attempt_count=1)
    with pytest.raises(AttributeError):
        lc.claimed = False  # type: ignore[misc]


# ---------------------------------------------------------------------------
# 18. multi-tenant scope isolation at store level
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_store_tenant_scope_isolation() -> None:
    """Claims for different tenants occupy distinct ledger slots."""
    store = _InMemoryIdempotencyStore()
    base = dict(app_id="app-1", workspace_id=None, idempotency_key_str="key-1")

    claim_a = await store.claim(**base, tenant_id="tenant-A")
    claim_b = await store.claim(**base, tenant_id="tenant-B")

    assert claim_a.claimed is True
    assert claim_b.claimed is True
    assert claim_a.claim_token != claim_b.claim_token
    assert len(store._records) == 2


# ---------------------------------------------------------------------------
# 19. multi-app scope isolation at store level
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_store_app_scope_isolation() -> None:
    """Claims for different apps occupy distinct ledger slots."""
    store = _InMemoryIdempotencyStore()
    base = dict(tenant_id="tenant-1", workspace_id=None, idempotency_key_str="key-1")

    claim_a = await store.claim(**base, app_id="app-A")
    claim_b = await store.claim(**base, app_id="app-B")

    assert claim_a.claimed is True
    assert claim_b.claimed is True
    assert len(store._records) == 2


# ---------------------------------------------------------------------------
# 20. attempt_count accumulates across retries
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_attempt_count_increments_on_reclaim() -> None:
    """attempt_count in LeaseClaim increments with each successful re-claim."""
    store = _InMemoryIdempotencyStore()
    key = dict(app_id="app-1", tenant_id="t-1", workspace_id=None, idempotency_key_str="k-1")

    c1 = await store.claim(**key, max_attempts=5)
    assert c1.attempt_count == 1

    # Fail without delay → immediately retryable.
    await store.mark_failed(**key, claim_token=c1.claim_token, retry_delay_seconds=0)

    c2 = await store.claim(**key, max_attempts=5)
    assert c2.claimed is True
    assert c2.attempt_count == 2

    await store.mark_failed(**key, claim_token=c2.claim_token, retry_delay_seconds=0)

    c3 = await store.claim(**key, max_attempts=5)
    assert c3.attempt_count == 3


# ---------------------------------------------------------------------------
# 21. Router — no idempotency_key → store not called
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_idempotency_key_store_not_called() -> None:
    """Reactions without idempotency_key must never touch the store."""
    store = _InMemoryIdempotencyStore()

    class _Handler:
        async def on_order(self, ctx: Any, **kwargs: Any) -> dict:
            return {"status": "ok"}

    reaction = MagicMock()
    reaction.event_type = "order.created"
    reaction.model_dump.return_value = {
        "id": "r-no-key",
        "module_id": "orders",
        "idempotency_key": None,
        "target": {"kind": "handler", "handler_method": "on_order"},
        "lease_seconds": 300,
        "retry_delay_seconds": 0,
    }
    module = _loaded_module("orders", handler=_Handler(), reactions=[reaction])
    router = ModuleEventRouter([module], idempotency_store=store)
    await router.handle_event("order.created", _envelope("evt_001"))

    assert len(store._records) == 0, "Store must not be touched for reactions without idempotency_key"


# ---------------------------------------------------------------------------
# 22. Router — no store configured → reaction executes normally
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_store_reaction_executes() -> None:
    """Without an idempotency_store, reactions execute as normal (no suppression)."""
    execution_count = 0

    class _Handler:
        async def on_order(self, ctx: Any, **kwargs: Any) -> dict:
            nonlocal execution_count
            execution_count += 1
            return {"status": "ok"}

    reaction = _handler_reaction("order.created")
    module = _loaded_module("orders", handler=_Handler(), reactions=[reaction])
    router = ModuleEventRouter([module], idempotency_store=None)
    await router.handle_event("order.created", _envelope("evt_001"))
    await router.handle_event("order.created", _envelope("evt_002"))

    assert execution_count == 2


# ---------------------------------------------------------------------------
# 23. Router — dead_letter event emitted after budget exhausted
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_router_emits_dead_lettered_event() -> None:
    """After max_attempts failures, router emits runtime.reaction.dead_lettered."""
    store = _InMemoryIdempotencyStore()
    emitted_events: list[tuple[str, dict]] = []

    async def _emitter(event_type: str, payload: dict) -> None:
        emitted_events.append((event_type, payload))

    class _Handler:
        async def on_order(self, ctx: Any, **kwargs: Any) -> dict:
            raise RuntimeError("always fails")

    reaction = _handler_reaction("order.created", max_attempts=1)
    module = _loaded_module("orders", handler=_Handler(), reactions=[reaction])
    router = ModuleEventRouter([module], idempotency_store=store, event_emitter=_emitter)
    await router.handle_event("order.created", _envelope("evt_001"))

    dead_lettered = [et for et, _ in emitted_events if et == "runtime.reaction.dead_lettered"]
    assert dead_lettered, "Router must emit runtime.reaction.dead_lettered after budget exhausted"


# ---------------------------------------------------------------------------
# 24. Router — dead_letter event payload contains no raw customer data
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dead_lettered_event_contains_no_customer_payload() -> None:
    """The dead_lettered event payload must not contain customer data."""
    store = _InMemoryIdempotencyStore()
    emitted_events: list[tuple[str, dict]] = []

    async def _emitter(event_type: str, payload: dict) -> None:
        emitted_events.append((event_type, payload))

    class _Handler:
        async def on_order(self, ctx: Any, **kwargs: Any) -> dict:
            raise RuntimeError("fail")

    sensitive_envelope = {
        "id": "evt_pii",
        "type": "order.created",
        "app_id": "app-1",
        "tenant_id": "tenant-1",
        "payload": {
            "app_id": "app-1",
            "tenant_id": "tenant-1",
            "order_id": "order-99",
            "card_number": "4111-1111-1111-1111",
        },
    }
    reaction = _handler_reaction("order.created", max_attempts=1)
    module = _loaded_module("orders", handler=_Handler(), reactions=[reaction])
    router = ModuleEventRouter([module], idempotency_store=store, event_emitter=_emitter)
    await router.handle_event("order.created", sensitive_envelope)

    for et, payload in emitted_events:
        if et == "runtime.reaction.dead_lettered":
            payload_str = str(payload)
            assert "4111-1111-1111-1111" not in payload_str, (
                "dead_lettered event must not reproduce customer payload"
            )


# ---------------------------------------------------------------------------
# 25. ModuleReaction model — lease/retry field validators
# ---------------------------------------------------------------------------


def test_module_reaction_bool_rejected_for_lease_seconds() -> None:
    """lease_seconds must not accept a boolean."""
    from pydantic import ValidationError

    from mozaiksai.core.runtime.app.module_loader import ModuleReaction

    with pytest.raises(ValidationError):
        ModuleReaction(
            id="r-1",
            event_type="domain.test",
            target={"kind": "handler", "handler_method": "handle"},
            lease_seconds=True,  # type: ignore[arg-type]
        )


def test_module_reaction_lease_seconds_out_of_bounds() -> None:
    """lease_seconds < 10 or > 3600 must raise a validation error."""
    from pydantic import ValidationError

    from mozaiksai.core.runtime.app.module_loader import ModuleReaction

    with pytest.raises(ValidationError):
        ModuleReaction(
            id="r-1",
            event_type="domain.test",
            target={"kind": "handler", "handler_method": "handle"},
            lease_seconds=5,
        )
    with pytest.raises(ValidationError):
        ModuleReaction(
            id="r-1",
            event_type="domain.test",
            target={"kind": "handler", "handler_method": "handle"},
            lease_seconds=3601,
        )


def test_module_reaction_max_attempts_zero_rejected() -> None:
    """max_attempts=0 must be rejected (must be at least 1)."""
    from pydantic import ValidationError

    from mozaiksai.core.runtime.app.module_loader import ModuleReaction

    with pytest.raises(ValidationError):
        ModuleReaction(
            id="r-1",
            event_type="domain.test",
            target={"kind": "handler", "handler_method": "handle"},
            max_attempts=0,
        )


def test_module_reaction_retry_delay_negative_rejected() -> None:
    """retry_delay_seconds < 0 must raise a validation error."""
    from pydantic import ValidationError

    from mozaiksai.core.runtime.app.module_loader import ModuleReaction

    with pytest.raises(ValidationError):
        ModuleReaction(
            id="r-1",
            event_type="domain.test",
            target={"kind": "handler", "handler_method": "handle"},
            retry_delay_seconds=-1,
        )


def test_module_reaction_valid_retry_config() -> None:
    """Valid lease/retry config passes without error."""
    from mozaiksai.core.runtime.app.module_loader import ModuleReaction, ModuleReactionTarget

    reaction = ModuleReaction(
        id="r-1",
        event_type="domain.test",
        target=ModuleReactionTarget(kind="handler", handler_method="handle"),
        lease_seconds=60,
        max_attempts=3,
        retry_delay_seconds=10,
    )
    assert reaction.lease_seconds == 60
    assert reaction.max_attempts == 3
    assert reaction.retry_delay_seconds == 10


def test_module_reaction_defaults() -> None:
    """Default values are applied when optional retry fields are omitted."""
    from mozaiksai.core.runtime.app.module_loader import ModuleReaction, ModuleReactionTarget

    reaction = ModuleReaction(
        id="r-1",
        event_type="domain.test",
        target=ModuleReactionTarget(kind="handler", handler_method="handle"),
    )
    assert reaction.lease_seconds == 300
    assert reaction.max_attempts is None
    assert reaction.retry_delay_seconds == 0
