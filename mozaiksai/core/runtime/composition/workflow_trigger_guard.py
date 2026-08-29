"""Fail-closed admission guard for module-event workflow triggers.

This is platform-owned event-to-run admission policy.  It composes the
existing durable reaction claim store with the existing ``limits`` rate-limit
library; it does not queue, schedule, or execute workflows.
"""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
from hashlib import sha256
from typing import Any, Literal, Protocol

from mozaiksai.core.runtime.composition.module_event_provenance import (
    module_event_identity,
)
from mozaiksai.core.runtime.composition.reaction_idempotency_store import (
    ReactionIdempotencyStore,
)

WORKFLOW_TRIGGER_TRACE_KEY = "_mozaiks_workflow_trigger"
WORKFLOW_TRIGGER_TRACE_HEADER = "X-Mozaiks-Workflow-Trigger"
_DEFAULT_MAX_DEPTH = 8
_DEFAULT_RATE_PER_MINUTE = 10

TriggerDecisionReason = Literal[
    "allowed",
    "replay",
    "cycle",
    "depth",
    "rate",
    "persistence",
]


class WorkflowTriggerRateLimiter(Protocol):
    async def ensure_ready(self) -> None: ...

    async def hit(self, tenant_key: str) -> bool: ...


@dataclass(frozen=True, slots=True)
class WorkflowTriggerDecision:
    allowed: bool
    reason: TriggerDecisionReason
    invocation_id: str
    event_identity: str
    depth: int
    trace: dict[str, Any] | None = None
    detail: str | None = None


class MongoWorkflowTriggerRateLimiter:
    """Distributed per-tenant moving-window limiter backed by existing Mongo."""

    def __init__(
        self,
        mongo_uri: str,
        *,
        database_name: str | None = None,
        requests_per_minute: int | None = None,
    ) -> None:
        from limits import parse
        from limits.storage import MongoDBStorage
        from limits.strategies import MovingWindowRateLimiter

        rpm = requests_per_minute or _positive_env_int(
            "WORKFLOW_TRIGGER_RATE_LIMIT_PER_MINUTE",
            _DEFAULT_RATE_PER_MINUTE,
        )
        db_name = (
            database_name or os.getenv("MOZAIKS_APP_DATABASE_NAME") or "mozaiks_apps"
        ).strip()
        self._storage = MongoDBStorage(
            mongo_uri,
            database_name=db_name,
            counter_collection_name="_mz_workflow_trigger_rate_counters",
            window_collection_name="_mz_workflow_trigger_rate_windows",
        )
        self._limiter = MovingWindowRateLimiter(self._storage)
        self._limit = parse(f"{rpm}/minute")

    async def ensure_ready(self) -> None:
        ready = await asyncio.to_thread(self._storage.check)
        if not ready:
            raise RuntimeError("workflow trigger rate-limit storage is unavailable")

    async def hit(self, tenant_key: str) -> bool:
        return bool(
            await asyncio.to_thread(
                self._limiter.hit,
                self._limit,
                tenant_key,
                "workflow_capability_trigger",
            )
        )


class WorkflowTriggerGuard:
    """Atomically admit one bounded workflow trigger invocation."""

    def __init__(
        self,
        *,
        claim_store: ReactionIdempotencyStore | None,
        rate_limiter: WorkflowTriggerRateLimiter | None,
        max_depth: int | None = None,
    ) -> None:
        self._claim_store = claim_store
        self._rate_limiter = rate_limiter
        self._max_depth = max_depth or _positive_env_int(
            "WORKFLOW_TRIGGER_MAX_DEPTH",
            _DEFAULT_MAX_DEPTH,
        )
        self._ready = False
        self._ready_lock = asyncio.Lock()

    async def authorize(
        self,
        *,
        capability_id: str,
        source_event: dict[str, Any],
        app_id: str,
        tenant_id: str | None,
        workspace_id: str | None,
    ) -> WorkflowTriggerDecision:
        event_identity = workflow_trigger_event_identity(source_event)
        invocation_id = workflow_trigger_invocation_id(event_identity, capability_id)
        trace_result = _next_trace(
            source_event=source_event,
            capability_id=capability_id,
            event_identity=event_identity,
            invocation_id=invocation_id,
            max_depth=self._max_depth,
        )
        if isinstance(trace_result, WorkflowTriggerDecision):
            return trace_result
        depth, trace = trace_result

        if self._claim_store is None or self._rate_limiter is None:
            return WorkflowTriggerDecision(
                allowed=False,
                reason="persistence",
                invocation_id=invocation_id,
                event_identity=event_identity,
                depth=depth,
                trace=trace,
                detail="durable trigger claim or rate-limit authority is unavailable",
            )

        try:
            await self._ensure_ready()
            key = f"workflow_trigger:{invocation_id}"
            claim = await self._claim_store.claim(
                app_id=app_id,
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                idempotency_key_str=key,
                max_attempts=1,
            )
            if not claim.claimed or not claim.claim_token:
                return WorkflowTriggerDecision(
                    allowed=False,
                    reason="replay",
                    invocation_id=invocation_id,
                    event_identity=event_identity,
                    depth=depth,
                    trace=trace,
                    detail="invocation identity is already claimed or completed",
                )

            # Persist the terminal claim before spawning.  This deliberately
            # chooses at-most-once admission: a crash after this point may lose
            # the invocation, but can never amplify it on replay.
            completed = await self._claim_store.complete(
                app_id=app_id,
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                idempotency_key_str=key,
                claim_token=claim.claim_token,
            )
            if not completed:
                raise RuntimeError("workflow trigger claim could not be finalized")

            tenant_key = ":".join([app_id, tenant_id or app_id, workspace_id or ""])
            if not await self._rate_limiter.hit(tenant_key):
                return WorkflowTriggerDecision(
                    allowed=False,
                    reason="rate",
                    invocation_id=invocation_id,
                    event_identity=event_identity,
                    depth=depth,
                    trace=trace,
                    detail="per-tenant workflow trigger rate limit exceeded",
                )
        except Exception as exc:
            return WorkflowTriggerDecision(
                allowed=False,
                reason="persistence",
                invocation_id=invocation_id,
                event_identity=event_identity,
                depth=depth,
                trace=trace,
                detail=f"{type(exc).__name__}: trigger authority failed",
            )

        return WorkflowTriggerDecision(
            allowed=True,
            reason="allowed",
            invocation_id=invocation_id,
            event_identity=event_identity,
            depth=depth,
            trace=trace,
        )

    async def _ensure_ready(self) -> None:
        if self._ready:
            return
        async with self._ready_lock:
            if self._ready:
                return
            if self._claim_store is None or self._rate_limiter is None:
                raise RuntimeError("workflow trigger authority is unavailable")
            await self._claim_store.ensure_indexes()
            await self._rate_limiter.ensure_ready()
            self._ready = True


def workflow_trigger_event_identity(source_event: dict[str, Any]) -> str:
    event_type = str(source_event.get("type") or "").strip()
    identity = module_event_identity(event_type, source_event)
    return (
        identity
        if source_event.get("id") or source_event.get("event_id")
        else f"derived_{identity}"
    )


def workflow_trigger_invocation_id(event_identity: str, capability_id: str) -> str:
    digest = sha256(f"{event_identity}\x00{capability_id.strip()}".encode()).hexdigest()
    return f"wti_{digest}"


def _next_trace(
    *,
    source_event: dict[str, Any],
    capability_id: str,
    event_identity: str,
    invocation_id: str,
    max_depth: int,
) -> tuple[int, dict[str, Any]] | WorkflowTriggerDecision:
    raw = source_event.get(WORKFLOW_TRIGGER_TRACE_KEY)
    if raw is None and isinstance(source_event.get("payload"), dict):
        raw = source_event["payload"].get(WORKFLOW_TRIGGER_TRACE_KEY)
    if raw is None:
        parent_depth = 0
        capabilities: list[str] = []
        invocations: list[str] = []
        root_event_id = event_identity
    elif isinstance(raw, dict):
        parent_depth_raw = raw.get("depth")
        capabilities_raw = raw.get("capability_ids")
        invocations_raw = raw.get("invocation_ids")
        root_event_id = str(raw.get("root_event_id") or event_identity)
        if (
            not isinstance(parent_depth_raw, int)
            or parent_depth_raw < 0
            or not isinstance(capabilities_raw, list)
            or not all(isinstance(item, str) and item for item in capabilities_raw)
            or not isinstance(invocations_raw, list)
            or not all(isinstance(item, str) and item for item in invocations_raw)
            or parent_depth_raw != len(capabilities_raw)
            or len(invocations_raw) != len(capabilities_raw)
        ):
            return WorkflowTriggerDecision(
                allowed=False,
                reason="depth",
                invocation_id=invocation_id,
                event_identity=event_identity,
                depth=max_depth + 1,
                detail="workflow trigger lineage is malformed",
            )
        parent_depth = parent_depth_raw
        capabilities = list(capabilities_raw)
        invocations = list(invocations_raw)
    else:
        return WorkflowTriggerDecision(
            allowed=False,
            reason="depth",
            invocation_id=invocation_id,
            event_identity=event_identity,
            depth=max_depth + 1,
            detail="workflow trigger lineage is malformed",
        )

    next_depth = parent_depth + 1
    if capability_id in capabilities:
        return WorkflowTriggerDecision(
            allowed=False,
            reason="cycle",
            invocation_id=invocation_id,
            event_identity=event_identity,
            depth=next_depth,
            detail="capability already exists in the trigger ancestry",
        )
    if next_depth > max_depth:
        return WorkflowTriggerDecision(
            allowed=False,
            reason="depth",
            invocation_id=invocation_id,
            event_identity=event_identity,
            depth=next_depth,
            detail=f"workflow trigger depth exceeds {max_depth}",
        )

    trace = {
        "root_event_id": root_event_id,
        "depth": next_depth,
        "capability_ids": [*capabilities, capability_id],
        "invocation_ids": [*invocations, invocation_id],
    }
    return next_depth, trace


def _positive_env_int(name: str, default: int) -> int:
    try:
        value = int((os.getenv(name) or str(default)).strip())
    except ValueError:
        return default
    return value if value > 0 else default


__all__ = [
    "MongoWorkflowTriggerRateLimiter",
    "WORKFLOW_TRIGGER_TRACE_HEADER",
    "WORKFLOW_TRIGGER_TRACE_KEY",
    "WorkflowTriggerDecision",
    "WorkflowTriggerGuard",
    "WorkflowTriggerRateLimiter",
    "workflow_trigger_event_identity",
    "workflow_trigger_invocation_id",
]
