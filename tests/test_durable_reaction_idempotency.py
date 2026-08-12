"""Durable reaction idempotency contract tests.

Proves the restart-safe duplicate suppression guarantee:

  - same reaction replay within one process → in-memory suppression (PR #256)
  - same reaction replay after new router instance → durable store suppression
  - different event identity → allowed
  - different tenant → allowed
  - different app → allowed
  - failed execution → mark_failed releases slot → retry allowed
  - no raw payload stored in ledger records

All tests use an in-memory mock store — no MongoDB required.
"""
from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from mozaiksai.core.runtime.composition.module_event_router import ModuleEventRouter
from mozaiksai.core.runtime.composition.reaction_idempotency_store import (
    ReactionIdempotencyStore,
)

# ---------------------------------------------------------------------------
# In-memory mock store — mirrors ReactionIdempotencyStore semantics without Mongo
# ---------------------------------------------------------------------------


class _InMemoryIdempotencyStore(ReactionIdempotencyStore):
    """Drop-in test double for ReactionIdempotencyStore.

    Uses a plain dict; no MongoDB is needed.
    ``claimed`` keys are stored as True (in-flight / completed) or absent (retry-eligible).
    """

    def __init__(self) -> None:
        super().__init__(client=object())  # prevents real client creation
        self._records: dict[str, str] = {}  # key_str → status

    def _collection(self) -> Any:  # type: ignore[override]
        raise RuntimeError("_InMemoryIdempotencyStore must not call _collection()")

    async def claim(self, *, app_id, tenant_id, workspace_id, idempotency_key_str) -> bool:
        scope = f"{app_id}|{tenant_id or ''}|{workspace_id or ''}|{idempotency_key_str}"
        if scope in self._records:
            return False
        self._records[scope] = "claimed"
        return True

    async def complete(self, *, app_id, tenant_id, workspace_id, idempotency_key_str) -> None:
        scope = f"{app_id}|{tenant_id or ''}|{workspace_id or ''}|{idempotency_key_str}"
        self._records[scope] = "completed"

    async def mark_failed(self, *, app_id, tenant_id, workspace_id, idempotency_key_str) -> None:
        scope = f"{app_id}|{tenant_id or ''}|{workspace_id or ''}|{idempotency_key_str}"
        self._records.pop(scope, None)

    async def ensure_indexes(self) -> None:
        pass

    def status(self, *, app_id, tenant_id, workspace_id, idempotency_key_str) -> str | None:
        scope = f"{app_id}|{tenant_id or ''}|{workspace_id or ''}|{idempotency_key_str}"
        return self._records.get(scope)


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
) -> MagicMock:
    m = MagicMock()
    m.event_type = event_type
    m.model_dump.return_value = {
        "id": reaction_id,
        "module_id": module_id,
        "idempotency_key": idempotency_key,
        "target": {"kind": "handler", "handler_method": handler_method},
    }
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
) -> ModuleEventRouter:
    reaction = _handler_reaction(event_type, module_id=module_id)
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
    records = list(store._records.values())
    assert records.count("completed") == 1
    assert len(records) == 1


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
    assert list(store._records.values()) == ["completed"]

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
    # Second call same event_id but different tenant; router's in-memory key includes
    # app_id+tenant_id from provenance so it won't match (no dupe in in-memory set).
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
# 6. Failed execution → mark_failed releases slot → retry allowed
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_failed_execution_releases_slot_for_retry() -> None:
    """A handler that raises causes mark_failed to delete the record.
    The next attempt with the same event can claim and execute."""
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
    # Router A: first attempt — handler raises, slot should be released.
    router_a = _router_with_store(store, handler)
    await router_a.handle_event("order.created", _envelope("evt_001"))
    # Slot must be absent (mark_failed deletes it).
    assert not store._records, (
        "Failed execution must release the slot so retry is possible"
    )

    # Router B: second attempt — slot is free, handler succeeds.
    router_b = _router_with_store(store, handler)
    await router_b.handle_event("order.created", _envelope("evt_001"))
    assert execution_count == 2
    assert list(store._records.values()) == ["completed"]


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
