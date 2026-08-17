"""Generic durable worker for typed WorkAssignment queue items.

The worker is explicitly invoked through ``run_once()``. It does not spawn
threads, own a polling loop, or schedule DAGs. WorkflowQueue owns durable
at-least-once delivery, leases, queue delivery attempts, and fencing. The
WorkAssignment contract owns assignment authority, bounded in-claim executor
retries, owned paths, assignment identity, and result validation.

External side effects performed by registered executors must be idempotent by
assignment_id/result attempt_id because a queue item may be delivered more than
once after crash or lease expiry.
"""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from .assignment_kinds import AssignmentKind
from .queue import ClaimResult, QueueItem, QueueItemStatus, WorkflowQueue
from .work_contracts import (
    WorkAssignment,
    WorkResult,
    validate_work_result_for_assignment,
)

WORK_ASSIGNMENT_PAYLOAD_KEY = "work_assignment"


class WorkAssignmentFailureCategory(StrEnum):
    INVALID_PAYLOAD = "invalid_payload"
    UNKNOWN_EXECUTOR = "unknown_executor"
    EXECUTOR_TRANSIENT = "executor_transient"
    EXECUTOR_PERMANENT = "executor_permanent"
    EXECUTOR_RESULT_INVALID = "executor_result_invalid"
    EXECUTOR_RESULT_FAILED = "executor_result_failed"
    EXECUTOR_RESULT_SKIPPED = "executor_result_skipped"


class WorkAssignmentRunStatus(StrEnum):
    NO_ITEM = "no_item"
    COMPLETED = "completed"
    RETRYABLE = "retryable"
    DEAD_LETTER = "dead_letter"
    STALE_CLAIM = "stale_claim"
    COMPLETED_EVENT_FAILED = "completed_event_failed"
    RETRYABLE_EVENT_FAILED = "retryable_event_failed"
    DEAD_LETTER_EVENT_FAILED = "dead_letter_event_failed"


class WorkAssignmentPermanentError(Exception):
    """Executor-raised permanent failure; retrying the same payload is invalid."""


class WorkAssignmentTransientError(Exception):
    """Executor-raised transient failure eligible for assignment/queue retry."""


class WorkAssignmentLeaseLostError(Exception):
    """Executor lost the current queue lease and must not publish a result."""


class WorkAssignmentWorkerOutcome(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    status: WorkAssignmentRunStatus
    item_id: str | None = None
    assignment_id: str | None = None
    assignment_kind: AssignmentKind | None = None
    claim_token: str | None = None
    queue_attempt_count: int = 0
    executor_attempt_count: int = 0
    queue_transition: QueueItemStatus | None = None
    error_category: WorkAssignmentFailureCategory | None = None
    result_digest: str | None = None
    lifecycle_event_emitted: bool = False


class WorkAssignmentLifecycleEvent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    event_type: str = Field(pattern=r"^work_assignment\.(completed|retryable|dead_letter)$")
    item_id: str
    assignment_id: str
    assignment_kind: AssignmentKind
    worker_id: str
    app_id: str
    chat_id: str
    user_id: str | None = None
    tenant_id: str | None = None
    workspace_id: str | None = None
    queue_attempt_count: int
    executor_attempt_count: int
    queue_transition: QueueItemStatus
    result_digest: str | None = None
    error_category: WorkAssignmentFailureCategory | None = None


@dataclass(frozen=True, slots=True)
class WorkAssignmentExecutionContext:
    assignment: WorkAssignment
    item: QueueItem
    claim_token: str
    queue_attempt_count: int
    executor_attempt: int
    worker_id: str
    allowed_agent_ids: tuple[str, ...]
    owned_paths: tuple[str, ...]
    required_structured_output_id: str | None
    workspace_id: str | None
    renew_lease: Callable[[int | None], Awaitable[bool]]


@runtime_checkable
class WorkAssignmentExecutor(Protocol):
    async def execute(self, context: WorkAssignmentExecutionContext) -> WorkResult | Mapping[str, Any]:
        """Execute a validated assignment and return a typed result payload."""
        ...


class WorkAssignmentExecutorRegistry:
    """Closed registry from assignment kind to executor implementation."""

    def __init__(self) -> None:
        self._executors: dict[AssignmentKind, WorkAssignmentExecutor] = {}

    def register(self, kind: AssignmentKind | str, executor: WorkAssignmentExecutor) -> None:
        assignment_kind = AssignmentKind(kind)
        if assignment_kind in self._executors:
            raise ValueError(f"executor already registered for assignment kind {assignment_kind.value!r}")
        if not callable(getattr(executor, "execute", None)):
            raise ValueError("work assignment executor must expose async execute(context)")
        self._executors[assignment_kind] = executor

    def resolve(self, kind: AssignmentKind | str) -> WorkAssignmentExecutor | None:
        return self._executors.get(AssignmentKind(kind))

    def registered_kinds(self) -> tuple[AssignmentKind, ...]:
        return tuple(sorted(self._executors, key=lambda item: item.value))


LifecycleEventSink = Callable[[WorkAssignmentLifecycleEvent], Awaitable[None] | None]


class WorkAssignmentWorker:
    """One-claim worker over WorkflowQueue and typed WorkAssignment contracts."""

    def __init__(
        self,
        *,
        queue: WorkflowQueue,
        executor_registry: WorkAssignmentExecutorRegistry,
        worker_id: str,
        lease_seconds: int = 300,
        lifecycle_event_sink: LifecycleEventSink | None = None,
    ) -> None:
        worker = str(worker_id or "").strip()
        if not worker:
            raise ValueError("worker_id must be non-empty")
        self.queue = queue
        self.executor_registry = executor_registry
        self.worker_id = worker
        self.lease_seconds = int(lease_seconds)
        self.lifecycle_event_sink = lifecycle_event_sink

    async def run_once(self) -> WorkAssignmentWorkerOutcome:
        claim = await self.queue.claim_next(self.worker_id, lease_seconds=self.lease_seconds)
        if not claim.claimed or claim.item is None or not claim.claim_token:
            return WorkAssignmentWorkerOutcome(status=WorkAssignmentRunStatus.NO_ITEM)
        return await self.process_claim(claim)

    async def process_claim(self, claim: ClaimResult) -> WorkAssignmentWorkerOutcome:
        if not claim.claimed or claim.item is None or not claim.claim_token:
            return WorkAssignmentWorkerOutcome(status=WorkAssignmentRunStatus.NO_ITEM)

        item = claim.item
        token = claim.claim_token
        try:
            assignment = _assignment_from_queue_item(item)
        except Exception:
            return await self._dead_letter_claim(
                item=item,
                claim_token=token,
                queue_attempt_count=claim.attempt_count,
                assignment=None,
                executor_attempt_count=0,
                error_category=WorkAssignmentFailureCategory.INVALID_PAYLOAD,
                result_digest=None,
            )

        executor = self.executor_registry.resolve(assignment.assignment_kind)
        if executor is None:
            return await self._dead_letter_claim(
                item=item,
                claim_token=token,
                queue_attempt_count=claim.attempt_count,
                assignment=assignment,
                executor_attempt_count=0,
                error_category=WorkAssignmentFailureCategory.UNKNOWN_EXECUTOR,
                result_digest=None,
            )

        max_executor_attempts = assignment.assignment_retry_limit + 1
        last_transient_category = WorkAssignmentFailureCategory.EXECUTOR_TRANSIENT

        async def _renew_lease(extend_seconds: int | None = None) -> bool:
            return await self.queue.renew_lease(
                item.item_id,
                claim_token=token,
                extend_seconds=extend_seconds,
            )

        for executor_attempt in range(1, max_executor_attempts + 1):
            context = WorkAssignmentExecutionContext(
                assignment=assignment,
                item=item,
                claim_token=token,
                queue_attempt_count=claim.attempt_count,
                executor_attempt=executor_attempt,
                worker_id=self.worker_id,
                allowed_agent_ids=assignment.allowed_agent_ids,
                owned_paths=assignment.owned_paths,
                required_structured_output_id=assignment.required_structured_output_id,
                workspace_id=_workspace_id(item),
                renew_lease=_renew_lease,
            )
            try:
                raw_result = await executor.execute(context)
                result = _coerce_work_result(raw_result)
                validate_work_result_for_assignment(assignment=assignment, result=result)
            except WorkAssignmentPermanentError:
                return await self._dead_letter_claim(
                    item=item,
                    claim_token=token,
                    queue_attempt_count=claim.attempt_count,
                    assignment=assignment,
                    executor_attempt_count=executor_attempt,
                    error_category=WorkAssignmentFailureCategory.EXECUTOR_PERMANENT,
                    result_digest=None,
                )
            except WorkAssignmentTransientError:
                if executor_attempt < max_executor_attempts:
                    continue
                return await self._fail_claim(
                    item=item,
                    claim_token=token,
                    queue_attempt_count=claim.attempt_count,
                    assignment=assignment,
                    executor_attempt_count=executor_attempt,
                    error_category=last_transient_category,
                    result_digest=None,
                )
            except WorkAssignmentLeaseLostError:
                return _stale_outcome(
                    item,
                    token,
                    claim.attempt_count,
                    assignment,
                    executor_attempt,
                )
            except Exception:
                return await self._dead_letter_claim(
                    item=item,
                    claim_token=token,
                    queue_attempt_count=claim.attempt_count,
                    assignment=assignment,
                    executor_attempt_count=executor_attempt,
                    error_category=WorkAssignmentFailureCategory.EXECUTOR_RESULT_INVALID,
                    result_digest=None,
                )

            if result.status == "completed":
                return await self._complete_claim(
                    item=item,
                    claim_token=token,
                    queue_attempt_count=claim.attempt_count,
                    assignment=assignment,
                    executor_attempt_count=executor_attempt,
                    result_digest=result.result_digest,
                )
            if result.status == "failed":
                return await self._fail_claim(
                    item=item,
                    claim_token=token,
                    queue_attempt_count=claim.attempt_count,
                    assignment=assignment,
                    executor_attempt_count=executor_attempt,
                    error_category=WorkAssignmentFailureCategory.EXECUTOR_RESULT_FAILED,
                    result_digest=result.result_digest,
                )
            return await self._dead_letter_claim(
                item=item,
                claim_token=token,
                queue_attempt_count=claim.attempt_count,
                assignment=assignment,
                executor_attempt_count=executor_attempt,
                error_category=WorkAssignmentFailureCategory.EXECUTOR_RESULT_SKIPPED,
                result_digest=result.result_digest,
            )

        return await self._fail_claim(
            item=item,
            claim_token=token,
            queue_attempt_count=claim.attempt_count,
            assignment=assignment,
            executor_attempt_count=max_executor_attempts,
            error_category=last_transient_category,
            result_digest=None,
        )

    async def _complete_claim(
        self,
        *,
        item: QueueItem,
        claim_token: str,
        queue_attempt_count: int,
        assignment: WorkAssignment,
        executor_attempt_count: int,
        result_digest: str,
    ) -> WorkAssignmentWorkerOutcome:
        completed = await self.queue.complete(item.item_id, claim_token=claim_token)
        if not completed:
            return _stale_outcome(item, claim_token, queue_attempt_count, assignment, executor_attempt_count)
        event_ok = await self._emit_event(
            item=item,
            assignment=assignment,
            queue_attempt_count=queue_attempt_count,
            executor_attempt_count=executor_attempt_count,
            queue_transition=QueueItemStatus.COMPLETED,
            result_digest=result_digest,
            error_category=None,
        )
        event_sink_configured = self.lifecycle_event_sink is not None
        return WorkAssignmentWorkerOutcome(
            status=(
                WorkAssignmentRunStatus.COMPLETED
                if event_ok or not event_sink_configured
                else WorkAssignmentRunStatus.COMPLETED_EVENT_FAILED
            ),
            item_id=item.item_id,
            assignment_id=assignment.assignment_id,
            assignment_kind=assignment.assignment_kind,
            claim_token=claim_token,
            queue_attempt_count=queue_attempt_count,
            executor_attempt_count=executor_attempt_count,
            queue_transition=QueueItemStatus.COMPLETED,
            result_digest=result_digest,
            lifecycle_event_emitted=event_ok and event_sink_configured,
        )

    async def _fail_claim(
        self,
        *,
        item: QueueItem,
        claim_token: str,
        queue_attempt_count: int,
        assignment: WorkAssignment,
        executor_attempt_count: int,
        error_category: WorkAssignmentFailureCategory,
        result_digest: str | None,
    ) -> WorkAssignmentWorkerOutcome:
        new_status = await self.queue.fail(
            item.item_id,
            claim_token=claim_token,
            error_category=error_category.value,
        )
        if new_status is None:
            return _stale_outcome(item, claim_token, queue_attempt_count, assignment, executor_attempt_count)
        queue_transition = QueueItemStatus(new_status)
        event_ok = await self._emit_event(
            item=item,
            assignment=assignment,
            queue_attempt_count=queue_attempt_count,
            executor_attempt_count=executor_attempt_count,
            queue_transition=queue_transition,
            result_digest=result_digest,
            error_category=error_category,
        )
        base_status = (
            WorkAssignmentRunStatus.RETRYABLE
            if queue_transition is QueueItemStatus.RETRYABLE
            else WorkAssignmentRunStatus.DEAD_LETTER
        )
        event_sink_configured = self.lifecycle_event_sink is not None
        if not event_ok and event_sink_configured:
            base_status = (
                WorkAssignmentRunStatus.RETRYABLE_EVENT_FAILED
                if queue_transition is QueueItemStatus.RETRYABLE
                else WorkAssignmentRunStatus.DEAD_LETTER_EVENT_FAILED
            )
        return WorkAssignmentWorkerOutcome(
            status=base_status,
            item_id=item.item_id,
            assignment_id=assignment.assignment_id,
            assignment_kind=assignment.assignment_kind,
            claim_token=claim_token,
            queue_attempt_count=queue_attempt_count,
            executor_attempt_count=executor_attempt_count,
            queue_transition=queue_transition,
            error_category=error_category,
            result_digest=result_digest,
            lifecycle_event_emitted=event_ok and event_sink_configured,
        )

    async def _dead_letter_claim(
        self,
        *,
        item: QueueItem,
        claim_token: str,
        queue_attempt_count: int,
        assignment: WorkAssignment | None,
        executor_attempt_count: int,
        error_category: WorkAssignmentFailureCategory,
        result_digest: str | None,
    ) -> WorkAssignmentWorkerOutcome:
        dead_lettered = await self.queue.dead_letter(
            item.item_id,
            claim_token=claim_token,
            error_category=error_category.value,
        )
        if not dead_lettered:
            return WorkAssignmentWorkerOutcome(
                status=WorkAssignmentRunStatus.STALE_CLAIM,
                item_id=item.item_id,
                assignment_id=assignment.assignment_id if assignment else None,
                assignment_kind=assignment.assignment_kind if assignment else None,
                claim_token=claim_token,
                queue_attempt_count=queue_attempt_count,
                executor_attempt_count=executor_attempt_count,
                queue_transition=None,
                error_category=error_category,
                result_digest=result_digest,
                lifecycle_event_emitted=False,
            )
        event_ok = True
        if assignment is not None:
            event_ok = await self._emit_event(
                item=item,
                assignment=assignment,
                queue_attempt_count=queue_attempt_count,
                executor_attempt_count=executor_attempt_count,
                queue_transition=QueueItemStatus.DEAD_LETTER,
                result_digest=result_digest,
                error_category=error_category,
            )
        return WorkAssignmentWorkerOutcome(
            status=(
                WorkAssignmentRunStatus.DEAD_LETTER
                if event_ok or self.lifecycle_event_sink is None
                else WorkAssignmentRunStatus.DEAD_LETTER_EVENT_FAILED
            ),
            item_id=item.item_id,
            assignment_id=assignment.assignment_id if assignment else None,
            assignment_kind=assignment.assignment_kind if assignment else None,
            claim_token=claim_token,
            queue_attempt_count=queue_attempt_count,
            executor_attempt_count=executor_attempt_count,
            queue_transition=QueueItemStatus.DEAD_LETTER,
            error_category=error_category,
            result_digest=result_digest,
            lifecycle_event_emitted=event_ok and self.lifecycle_event_sink is not None and assignment is not None,
        )

    async def _emit_event(
        self,
        *,
        item: QueueItem,
        assignment: WorkAssignment,
        queue_attempt_count: int,
        executor_attempt_count: int,
        queue_transition: QueueItemStatus,
        result_digest: str | None,
        error_category: WorkAssignmentFailureCategory | None,
    ) -> bool:
        if self.lifecycle_event_sink is None:
            return True
        event = WorkAssignmentLifecycleEvent(
            event_type=f"work_assignment.{queue_transition.value}",
            item_id=item.item_id,
            assignment_id=assignment.assignment_id,
            assignment_kind=assignment.assignment_kind,
            worker_id=self.worker_id,
            app_id=item.app_id,
            chat_id=item.chat_id,
            user_id=item.user_id,
            tenant_id=item.tenant_id,
            workspace_id=_workspace_id(item),
            queue_attempt_count=queue_attempt_count,
            executor_attempt_count=executor_attempt_count,
            queue_transition=queue_transition,
            result_digest=result_digest,
            error_category=error_category,
        )
        try:
            maybe_awaitable = self.lifecycle_event_sink(event)
            if inspect.isawaitable(maybe_awaitable):
                await maybe_awaitable
            return True
        except Exception:
            return False


def _assignment_from_queue_item(item: QueueItem) -> WorkAssignment:
    payload = item.payload
    if not isinstance(payload, Mapping):
        raise ValueError("queue payload must be a mapping")
    assignment_payload = payload.get(WORK_ASSIGNMENT_PAYLOAD_KEY)
    if not isinstance(assignment_payload, Mapping):
        raise ValueError(f"queue payload missing {WORK_ASSIGNMENT_PAYLOAD_KEY!r}")
    return WorkAssignment.model_validate(assignment_payload)


def _coerce_work_result(value: WorkResult | Mapping[str, Any]) -> WorkResult:
    if isinstance(value, WorkResult):
        return value
    if isinstance(value, Mapping):
        return WorkResult.model_validate(value)
    raise ValueError("executor must return WorkResult or mapping")


def _workspace_id(item: QueueItem) -> str | None:
    value = item.payload.get("workspace_id") if isinstance(item.payload, Mapping) else None
    text = str(value or "").strip()
    return text or None


def _stale_outcome(
    item: QueueItem,
    claim_token: str,
    queue_attempt_count: int,
    assignment: WorkAssignment,
    executor_attempt_count: int,
) -> WorkAssignmentWorkerOutcome:
    return WorkAssignmentWorkerOutcome(
        status=WorkAssignmentRunStatus.STALE_CLAIM,
        item_id=item.item_id,
        assignment_id=assignment.assignment_id,
        assignment_kind=assignment.assignment_kind,
        claim_token=claim_token,
        queue_attempt_count=queue_attempt_count,
        executor_attempt_count=executor_attempt_count,
        lifecycle_event_emitted=False,
    )


__all__ = [
    "WORK_ASSIGNMENT_PAYLOAD_KEY",
    "WorkAssignmentExecutionContext",
    "WorkAssignmentExecutor",
    "WorkAssignmentExecutorRegistry",
    "WorkAssignmentFailureCategory",
    "WorkAssignmentLeaseLostError",
    "WorkAssignmentLifecycleEvent",
    "WorkAssignmentPermanentError",
    "WorkAssignmentRunStatus",
    "WorkAssignmentTransientError",
    "WorkAssignmentWorker",
    "WorkAssignmentWorkerOutcome",
]
