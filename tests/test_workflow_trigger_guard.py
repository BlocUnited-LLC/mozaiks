from __future__ import annotations

import asyncio
from collections import defaultdict
from typing import Any

import pytest

from mozaiksai.core.runtime.composition.reaction_idempotency_store import LeaseClaim
from mozaiksai.core.runtime.composition.workflow_trigger_guard import (
    WORKFLOW_TRIGGER_TRACE_KEY,
    WorkflowTriggerGuard,
    workflow_trigger_event_identity,
    workflow_trigger_invocation_id,
)


class _AtomicClaimStore:
    def __init__(self, *, fail_ready: bool = False) -> None:
        self._lock = asyncio.Lock()
        self._records: dict[tuple[str, str, str, str], str] = {}
        self.fail_ready = fail_ready

    async def ensure_indexes(self) -> None:
        if self.fail_ready:
            raise ConnectionError("claim database unavailable")

    async def claim(self, **kwargs: Any) -> LeaseClaim:
        key = (
            kwargs["app_id"],
            kwargs.get("tenant_id") or "",
            kwargs.get("workspace_id") or "",
            kwargs["idempotency_key_str"],
        )
        async with self._lock:
            if key in self._records:
                return LeaseClaim(claimed=False)
            token = f"claim-{len(self._records) + 1}"
            self._records[key] = token
            return LeaseClaim(claimed=True, claim_token=token, attempt_count=1)

    async def complete(self, **kwargs: Any) -> bool:
        key = (
            kwargs["app_id"],
            kwargs.get("tenant_id") or "",
            kwargs.get("workspace_id") or "",
            kwargs["idempotency_key_str"],
        )
        async with self._lock:
            if self._records.get(key) != kwargs["claim_token"]:
                return False
            self._records[key] = "completed"
            return True


class _TenantRateLimiter:
    def __init__(self, limit: int = 100) -> None:
        self.limit = limit
        self.hits: dict[str, int] = defaultdict(int)
        self._lock = asyncio.Lock()

    async def ensure_ready(self) -> None:
        return None

    async def hit(self, tenant_key: str) -> bool:
        async with self._lock:
            self.hits[tenant_key] += 1
            return self.hits[tenant_key] <= self.limit


def _event(
    event_id: str,
    *,
    app_id: str = "app-1",
    tenant_id: str = "tenant-1",
    trace: dict[str, Any] | None = None,
) -> dict[str, Any]:
    event: dict[str, Any] = {
        "id": event_id,
        "type": "domain.tasks.created",
        "tenant": {"app_id": app_id, "tenant_id": tenant_id},
        "actor": {"type": "user", "id": "user-1"},
        "payload": {"task_id": event_id},
    }
    if trace is not None:
        event[WORKFLOW_TRIGGER_TRACE_KEY] = trace
    return event


def _routes() -> dict[str, list[dict[str, Any]]]:
    return {
        "tasks.review": [
            {
                "workflow_id": "ReviewWorkflow",
                "event_type": "domain.tasks.created",
                "trigger": {
                    "type": "event",
                    "event": "domain.tasks.created",
                    "capability_id": "tasks.review",
                },
            }
        ]
    }


def _guard(*, rate_limit: int = 100, fail_ready: bool = False, max_depth: int = 8):
    return WorkflowTriggerGuard(
        claim_store=_AtomicClaimStore(fail_ready=fail_ready),  # type: ignore[arg-type]
        rate_limiter=_TenantRateLimiter(rate_limit),
        max_depth=max_depth,
    )


def test_invocation_identity_uses_event_and_capability_authority() -> None:
    event = _event("evt-1")
    event_identity = workflow_trigger_event_identity(event)
    assert event_identity == "evt-1"
    first = workflow_trigger_invocation_id(event_identity, "tasks.review")
    assert first == workflow_trigger_invocation_id(event_identity, "tasks.review")
    assert first != workflow_trigger_invocation_id(event_identity, "tasks.publish")
    derived = workflow_trigger_event_identity(
        {"type": "domain.tasks.created", "payload": {"task_id": "task-1"}}
    )
    assert derived.startswith("derived_")


@pytest.mark.asyncio
async def test_concurrent_double_delivery_spawns_exactly_one_workflow() -> None:
    from mozaiksai.hosts import platform

    guard = _guard()
    created: list[dict[str, Any]] = []
    emitted: list[tuple[str, dict[str, Any]]] = []

    async def create_session(**kwargs: Any) -> str:
        created.append(kwargs)
        await asyncio.sleep(0)
        return "chat-1"

    async def emit(event_type: str, payload: dict[str, Any]) -> None:
        emitted.append((event_type, payload))

    async def invoke() -> dict[str, Any]:
        return await platform._invoke_workflow_capability(
            capability_id="tasks.review",
            source_event=_event("evt-concurrent"),
            subscription={"id": "reaction-1", "module_id": "tasks"},
            routes=_routes(),
            event_emitter=emit,
            create_session=create_session,
            trigger_guard=guard,
            auto_start=False,
        )

    results = await asyncio.gather(invoke(), invoke())
    assert sorted(result["status"] for result in results) == [
        "created",
        "replay_suppressed",
    ]
    assert len(created) == 1
    assert created[0]["context_variables"][WORKFLOW_TRIGGER_TRACE_KEY]["depth"] == 1
    assert created[0]["trigger_meta"]["trigger_depth"] == 1
    assert {event_type for event_type, _payload in emitted} == {
        "platform.workflow_capability_started",
        "platform.workflow_capability_trigger_rejected",
    }


@pytest.mark.asyncio
async def test_self_and_multi_capability_cycles_terminate() -> None:
    guard = _guard()
    self_cycle = await guard.authorize(
        capability_id="tasks.review",
        source_event=_event(
            "evt-self",
            trace={
                "root_event_id": "root",
                "depth": 1,
                "capability_ids": ["tasks.review"],
                "invocation_ids": ["wti-parent"],
            },
        ),
        app_id="app-1",
        tenant_id="tenant-1",
        workspace_id=None,
    )
    multi_cycle = await guard.authorize(
        capability_id="capability.a",
        source_event=_event(
            "evt-multi",
            trace={
                "root_event_id": "root",
                "depth": 2,
                "capability_ids": ["capability.a", "capability.b"],
                "invocation_ids": ["wti-a", "wti-b"],
            },
        ),
        app_id="app-1",
        tenant_id="tenant-1",
        workspace_id=None,
    )
    assert self_cycle.reason == "cycle"
    assert multi_cycle.reason == "cycle"
    assert self_cycle.allowed is multi_cycle.allowed is False


@pytest.mark.asyncio
async def test_trigger_depth_is_bounded_and_propagated() -> None:
    guard = _guard(max_depth=2)
    allowed = await guard.authorize(
        capability_id="capability.b",
        source_event=_event(
            "evt-depth-2",
            trace={
                "root_event_id": "root",
                "depth": 1,
                "capability_ids": ["capability.a"],
                "invocation_ids": ["wti-a"],
            },
        ),
        app_id="app-1",
        tenant_id="tenant-1",
        workspace_id=None,
    )
    rejected = await guard.authorize(
        capability_id="capability.c",
        source_event=_event("evt-depth-3", trace=allowed.trace),
        app_id="app-1",
        tenant_id="tenant-1",
        workspace_id=None,
    )
    assert allowed.allowed is True
    assert allowed.depth == 2
    assert allowed.trace is not None
    assert rejected.allowed is False
    assert rejected.reason == "depth"
    assert rejected.depth == 3


@pytest.mark.asyncio
async def test_rate_limit_is_tenant_scoped() -> None:
    guard = _guard(rate_limit=1)

    async def authorize(event_id: str, tenant_id: str):
        return await guard.authorize(
            capability_id="tasks.review",
            source_event=_event(event_id, tenant_id=tenant_id),
            app_id="app-1",
            tenant_id=tenant_id,
            workspace_id=None,
        )

    tenant_a_first = await authorize("evt-a-1", "tenant-a")
    tenant_a_second = await authorize("evt-a-2", "tenant-a")
    tenant_b_first = await authorize("evt-b-1", "tenant-b")
    assert tenant_a_first.allowed is True
    assert tenant_a_second.reason == "rate"
    assert tenant_b_first.allowed is True


@pytest.mark.asyncio
async def test_persistence_failure_fails_closed_before_spawn() -> None:
    from mozaiksai.hosts import platform

    created = 0

    async def create_session(**_kwargs: Any) -> str:
        nonlocal created
        created += 1
        return "must-not-exist"

    result = await platform._invoke_workflow_capability(
        capability_id="tasks.review",
        source_event=_event("evt-persistence"),
        subscription={"id": "reaction-1", "module_id": "tasks"},
        routes=_routes(),
        create_session=create_session,
        trigger_guard=_guard(fail_ready=True),
        auto_start=False,
    )
    assert result["status"] == "failed_closed"
    assert result["reason"] == "persistence"
    assert created == 0
