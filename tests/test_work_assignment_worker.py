"""Tests for the generic durable WorkAssignment worker."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import pytest

from mozaiksai.core.workflow.queue import (
    _DEFAULT_MAX_ATTEMPTS,
    ClaimResult,
    QueueItem,
    _validated_max_attempts,
)
from mozaiksai.core.workflow.work_assignment_worker import (
    WORK_ASSIGNMENT_PAYLOAD_KEY,
    WorkAssignmentExecutionContext,
    WorkAssignmentExecutorRegistry,
    WorkAssignmentFailureCategory,
    WorkAssignmentLifecycleEvent,
    WorkAssignmentPermanentError,
    WorkAssignmentRunStatus,
    WorkAssignmentTransientError,
    WorkAssignmentWorker,
)
from mozaiksai.core.workflow.work_contracts import (
    ArtifactIdentity,
    WorkAssignment,
    WorkResult,
    make_work_assignment,
    make_work_result,
    stable_digest,
)


class ManualClock:
    def __init__(self) -> None:
        self.current = datetime(2026, 1, 1, tzinfo=UTC)

    def now(self) -> datetime:
        return self.current

    def advance(self, seconds: int) -> None:
        self.current += timedelta(seconds=seconds)


class InMemoryWorkQueue:
    def __init__(self, *, now_fn: Any) -> None:
        self._records: dict[str, dict[str, Any]] = {}
        self._now_fn = now_fn

    async def enqueue(
        self,
        item: QueueItem,
        *,
        max_attempts: int | None = None,
        retry_delay_seconds: int = 0,
    ) -> str:
        item.max_attempts = _validated_max_attempts(max_attempts)
        item.retry_delay_seconds = max(0, int(retry_delay_seconds))
        self._records[item.item_id] = item.to_document()
        return item.item_id

    async def claim_next(self, worker_id: str, *, lease_seconds: int = 300) -> ClaimResult:
        now = self._now_fn()
        now_iso = now.isoformat()
        lease_seconds = max(10, min(3600, int(lease_seconds)))
        candidates: list[dict[str, Any]] = []
        for doc in self._records.values():
            if doc["status"] == "pending":
                candidates.append(doc)
            elif doc["status"] == "retryable" and (doc.get("next_attempt_at") or "") <= now_iso:
                candidates.append(doc)
            elif doc["status"] == "claimed" and (doc.get("lease_expires_at") or "") <= now_iso:
                candidates.append(doc)
        if not candidates:
            return ClaimResult(claimed=False)
        candidates.sort(key=lambda doc: (-int(doc.get("priority") or 0), str(doc.get("enqueued_at") or "")))
        doc = candidates[0]
        token = str(uuid4())
        doc["status"] = "claimed"
        doc["claimed_by"] = worker_id
        doc["claim_token"] = token
        doc["claimed_at"] = now_iso
        doc["lease_expires_at"] = (now + timedelta(seconds=lease_seconds)).isoformat()
        doc["attempt_count"] = int(doc.get("attempt_count") or 0) + 1
        item = QueueItem.from_document(doc)
        return ClaimResult(claimed=True, item=item, claim_token=token, attempt_count=item.attempt_count)

    async def complete(self, item_id: str, *, claim_token: str) -> bool:
        doc = self._records.get(item_id)
        if not doc or doc.get("status") != "claimed" or doc.get("claim_token") != claim_token:
            return False
        now = self._now_fn()
        doc["status"] = "completed"
        doc["completed_at"] = now.isoformat()
        doc["expires_at"] = (now + timedelta(seconds=3600)).isoformat()
        return True

    async def fail(self, item_id: str, *, claim_token: str, error_category: str | None = None) -> str | None:
        doc = self._records.get(item_id)
        if not doc or doc.get("status") != "claimed" or doc.get("claim_token") != claim_token:
            return None
        now = self._now_fn()
        attempt_count = int(doc.get("attempt_count") or 0)
        max_attempts = int(doc.get("max_attempts") or _DEFAULT_MAX_ATTEMPTS)
        if attempt_count >= max_attempts:
            status = "dead_letter"
            doc["dead_lettered_at"] = now.isoformat()
            doc["next_attempt_at"] = None
            doc["expires_at"] = (now + timedelta(seconds=3600)).isoformat()
        else:
            status = "retryable"
            doc["next_attempt_at"] = (now + timedelta(seconds=int(doc.get("retry_delay_seconds") or 0))).isoformat()
            doc["dead_lettered_at"] = None
            doc["expires_at"] = None
        doc["status"] = status
        doc["last_failed_at"] = now.isoformat()
        doc["error_category"] = (error_category or "execution_error")[:128]
        return status

    async def dead_letter(self, item_id: str, *, claim_token: str, error_category: str | None = None) -> bool:
        doc = self._records.get(item_id)
        if not doc or doc.get("status") != "claimed" or doc.get("claim_token") != claim_token:
            return False
        now = self._now_fn()
        doc["status"] = "dead_letter"
        doc["last_failed_at"] = now.isoformat()
        doc["dead_lettered_at"] = now.isoformat()
        doc["next_attempt_at"] = None
        doc["error_category"] = (error_category or "permanent_failure")[:128]
        doc["expires_at"] = (now + timedelta(seconds=3600)).isoformat()
        return True

    async def renew_lease(self, item_id: str, *, claim_token: str, extend_seconds: int | None = None) -> bool:
        doc = self._records.get(item_id)
        now = self._now_fn()
        if not doc or doc.get("status") != "claimed" or doc.get("claim_token") != claim_token:
            return False
        if not doc.get("lease_expires_at") or doc["lease_expires_at"] <= now.isoformat():
            return False
        doc["lease_expires_at"] = (now + timedelta(seconds=int(extend_seconds or 300))).isoformat()
        return True

    async def active_count(self) -> int:
        return len([doc for doc in self._records.values() if doc.get("status") == "claimed"])

    async def queue_depth(self) -> int:
        return len([doc for doc in self._records.values() if doc.get("status") == "pending"])

    def status(self, item_id: str) -> str:
        return str(self._records[item_id]["status"])

    def document(self, item_id: str) -> dict[str, Any]:
        return dict(self._records[item_id])


class RecordingExecutor:
    def __init__(self, outcomes: list[Any]) -> None:
        self.outcomes = list(outcomes)
        self.contexts: list[WorkAssignmentExecutionContext] = []

    async def execute(self, context: WorkAssignmentExecutionContext) -> WorkResult | dict[str, Any]:
        self.contexts.append(context)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        if callable(outcome):
            maybe = outcome(context)
            return await maybe if hasattr(maybe, "__await__") else maybe
        return outcome


class CrashDuringExecution(BaseException):
    pass


def _assignment(**overrides: Any) -> WorkAssignment:
    payload = {
        "assignment_id": "work-1",
        "plan_id": "plan-1",
        "plan_digest": "a" * 64,
        "baseline_sha": "b" * 40,
        "assignment_kind": "module_contract",
        "owned_paths": ["modules/inventory/module.yaml"],
    }
    payload.update(overrides)
    return make_work_assignment(**payload)


def _result(assignment: WorkAssignment, *, status: str = "completed") -> WorkResult:
    return make_work_result(
        assignment=assignment,
        status=status,
        attempt_id=f"{assignment.assignment_id}:attempt",
        changed_artifacts=[
            {
                "path": assignment.owned_paths[0],
                "operation": "create",
                "content_digest": stable_digest(f"{assignment.assignment_id}:content"),
            }
        ],
    )


def _outside_result(assignment: WorkAssignment) -> WorkResult:
    artifact = ArtifactIdentity(
        path="modules/other/module.yaml",
        operation="create",
        content_digest=stable_digest("outside"),
    )
    payload = {
        "assignment_id": assignment.assignment_id,
        "assignment_digest": assignment.assignment_digest,
        "baseline_sha": assignment.baseline_sha,
        "status": "completed",
        "changed_artifacts": [artifact.model_dump(mode="json")],
        "validation_evidence": [],
        "output_digest": stable_digest({}),
        "attempt_id": "outside-attempt",
    }
    return WorkResult(
        **payload,
        result_digest=stable_digest(payload),
    )


def _item(assignment: WorkAssignment, **payload_overrides: Any) -> QueueItem:
    payload = {
        WORK_ASSIGNMENT_PAYLOAD_KEY: assignment.model_dump(mode="json"),
        "workspace_id": "workspace-1",
    }
    payload.update(payload_overrides)
    return QueueItem(
        workflow_name="WorkAssignmentWorker",
        chat_id="chat-1",
        app_id="app-1",
        user_id="user-1",
        tenant_id="tenant-1",
        payload=payload,
    )


def _registry(executor: RecordingExecutor | None = None, *, kind: str = "module_contract") -> WorkAssignmentExecutorRegistry:
    registry = WorkAssignmentExecutorRegistry()
    if executor is not None:
        registry.register(kind, executor)
    return registry


async def _enqueue_worker(
    *,
    queue: InMemoryWorkQueue,
    assignment: WorkAssignment,
    executor: RecordingExecutor | None,
    max_attempts: int | None = None,
    events: list[WorkAssignmentLifecycleEvent] | None = None,
) -> tuple[str, WorkAssignmentWorker]:
    item = _item(assignment)
    item_id = await queue.enqueue(item, max_attempts=max_attempts)
    worker = WorkAssignmentWorker(
        queue=queue,
        executor_registry=_registry(executor),
        worker_id="worker-1",
        lease_seconds=30,
        lifecycle_event_sink=(events.append if events is not None else None),
    )
    return item_id, worker


@pytest.mark.asyncio
async def test_run_once_claims_validates_executes_and_completes_with_fencing() -> None:
    clock = ManualClock()
    queue = InMemoryWorkQueue(now_fn=clock.now)
    assignment = _assignment()
    events: list[WorkAssignmentLifecycleEvent] = []
    executor = RecordingExecutor([_result(assignment)])
    item_id, worker = await _enqueue_worker(queue=queue, assignment=assignment, executor=executor, events=events)

    outcome = await worker.run_once()

    assert outcome.status is WorkAssignmentRunStatus.COMPLETED
    assert queue.status(item_id) == "completed"
    assert len(executor.contexts) == 1
    context = executor.contexts[0]
    assert context.assignment == assignment
    assert context.claim_token == outcome.claim_token
    assert context.owned_paths == assignment.owned_paths
    assert context.workspace_id == "workspace-1"
    assert events[0].event_type == "work_assignment.completed"
    assert events[0].tenant_id == "tenant-1"


@pytest.mark.asyncio
async def test_crash_before_executor_invocation_is_reclaimed_after_lease_expiry() -> None:
    clock = ManualClock()
    queue = InMemoryWorkQueue(now_fn=clock.now)
    assignment = _assignment()
    item_id = await queue.enqueue(_item(assignment))
    first = await queue.claim_next("crashed-worker", lease_seconds=30)
    assert first.claimed
    executor = RecordingExecutor([_result(assignment)])
    worker = WorkAssignmentWorker(queue=queue, executor_registry=_registry(executor), worker_id="replacement", lease_seconds=30)

    clock.advance(31)
    outcome = await worker.run_once()

    assert outcome.status is WorkAssignmentRunStatus.COMPLETED
    assert outcome.claim_token != first.claim_token
    assert queue.document(item_id)["attempt_count"] == 2
    assert len(executor.contexts) == 1


@pytest.mark.asyncio
async def test_crash_during_execution_leaves_claim_for_replacement_worker() -> None:
    clock = ManualClock()
    queue = InMemoryWorkQueue(now_fn=clock.now)
    assignment = _assignment()
    item_id, worker = await _enqueue_worker(
        queue=queue,
        assignment=assignment,
        executor=RecordingExecutor([CrashDuringExecution("process died")]),
    )

    with pytest.raises(CrashDuringExecution):
        await worker.run_once()

    assert queue.status(item_id) == "claimed"
    clock.advance(31)
    replacement = WorkAssignmentWorker(
        queue=queue,
        executor_registry=_registry(RecordingExecutor([_result(assignment)])),
        worker_id="replacement",
        lease_seconds=30,
    )
    outcome = await replacement.run_once()
    assert outcome.status is WorkAssignmentRunStatus.COMPLETED
    assert queue.document(item_id)["attempt_count"] == 2


@pytest.mark.asyncio
async def test_stale_worker_result_rejected_after_replacement_claim() -> None:
    clock = ManualClock()
    queue = InMemoryWorkQueue(now_fn=clock.now)
    assignment = _assignment()
    item_id = await queue.enqueue(_item(assignment))
    old_claim = await queue.claim_next("old", lease_seconds=30)
    clock.advance(31)
    new_claim = await queue.claim_next("new", lease_seconds=30)
    assert old_claim.claim_token != new_claim.claim_token
    stale_worker = WorkAssignmentWorker(
        queue=queue,
        executor_registry=_registry(RecordingExecutor([_result(assignment)])),
        worker_id="old",
        lease_seconds=30,
    )

    outcome = await stale_worker.process_claim(old_claim)

    assert outcome.status is WorkAssignmentRunStatus.STALE_CLAIM
    assert queue.status(item_id) == "claimed"
    assert await queue.complete(item_id, claim_token=old_claim.claim_token or "") is False
    assert await queue.fail(item_id, claim_token=old_claim.claim_token or "") is None
    assert await queue.renew_lease(item_id, claim_token=old_claim.claim_token or "") is False


@pytest.mark.asyncio
async def test_malformed_payload_dead_letters_without_executor_invocation() -> None:
    clock = ManualClock()
    queue = InMemoryWorkQueue(now_fn=clock.now)
    item_id = await queue.enqueue(QueueItem(workflow_name="WorkAssignmentWorker", chat_id="chat", app_id="app", payload={}))
    executor = RecordingExecutor([RuntimeError("should not run")])
    worker = WorkAssignmentWorker(queue=queue, executor_registry=_registry(executor), worker_id="worker")

    outcome = await worker.run_once()

    assert outcome.status is WorkAssignmentRunStatus.DEAD_LETTER
    assert outcome.error_category is WorkAssignmentFailureCategory.INVALID_PAYLOAD
    assert queue.status(item_id) == "dead_letter"
    assert executor.contexts == []


@pytest.mark.asyncio
async def test_unknown_executor_dead_letters_registered_kind_without_arbitrary_resolution() -> None:
    clock = ManualClock()
    queue = InMemoryWorkQueue(now_fn=clock.now)
    assignment = _assignment(assignment_kind="page_bundle", owned_paths=["ui/pages/home.yaml"])
    item_id, worker = await _enqueue_worker(queue=queue, assignment=assignment, executor=None)

    outcome = await worker.run_once()

    assert outcome.status is WorkAssignmentRunStatus.DEAD_LETTER
    assert outcome.error_category is WorkAssignmentFailureCategory.UNKNOWN_EXECUTOR
    assert queue.document(item_id)["error_category"] == "unknown_executor"


@pytest.mark.asyncio
async def test_transient_executor_retries_inside_assignment_budget_before_queue_retry() -> None:
    clock = ManualClock()
    queue = InMemoryWorkQueue(now_fn=clock.now)
    assignment = _assignment(assignment_retry_limit=1)
    executor = RecordingExecutor([WorkAssignmentTransientError("temporary"), _result(assignment)])
    item_id, worker = await _enqueue_worker(queue=queue, assignment=assignment, executor=executor)

    outcome = await worker.run_once()

    assert outcome.status is WorkAssignmentRunStatus.COMPLETED
    assert outcome.executor_attempt_count == 2
    assert queue.document(item_id)["attempt_count"] == 1
    assert len(executor.contexts) == 2


@pytest.mark.asyncio
async def test_transient_exhaustion_uses_queue_delivery_budget() -> None:
    clock = ManualClock()
    queue = InMemoryWorkQueue(now_fn=clock.now)
    assignment = _assignment(assignment_retry_limit=0)
    executor = RecordingExecutor([WorkAssignmentTransientError("temporary")])
    item_id, worker = await _enqueue_worker(queue=queue, assignment=assignment, executor=executor, max_attempts=2)

    outcome = await worker.run_once()

    assert outcome.status is WorkAssignmentRunStatus.RETRYABLE
    assert queue.status(item_id) == "retryable"
    assert queue.document(item_id)["error_category"] == "executor_transient"


@pytest.mark.asyncio
async def test_permanent_executor_error_dead_letters_immediately() -> None:
    clock = ManualClock()
    queue = InMemoryWorkQueue(now_fn=clock.now)
    assignment = _assignment()
    item_id, worker = await _enqueue_worker(
        queue=queue,
        assignment=assignment,
        executor=RecordingExecutor([WorkAssignmentPermanentError("bad contract")]),
        max_attempts=5,
    )

    outcome = await worker.run_once()

    assert outcome.status is WorkAssignmentRunStatus.DEAD_LETTER
    assert queue.status(item_id) == "dead_letter"
    assert queue.document(item_id)["attempt_count"] == 1
    assert queue.document(item_id)["error_category"] == "executor_permanent"


@pytest.mark.asyncio
async def test_queue_max_attempts_dead_letters_after_delivery_budget() -> None:
    clock = ManualClock()
    queue = InMemoryWorkQueue(now_fn=clock.now)
    assignment = _assignment()
    item_id, worker = await _enqueue_worker(
        queue=queue,
        assignment=assignment,
        executor=RecordingExecutor([WorkAssignmentTransientError("one")]),
        max_attempts=1,
    )

    outcome = await worker.run_once()

    assert outcome.status is WorkAssignmentRunStatus.DEAD_LETTER
    assert queue.status(item_id) == "dead_letter"


@pytest.mark.asyncio
async def test_executor_malformed_result_dead_letters() -> None:
    clock = ManualClock()
    queue = InMemoryWorkQueue(now_fn=clock.now)
    assignment = _assignment()
    item_id, worker = await _enqueue_worker(queue=queue, assignment=assignment, executor=RecordingExecutor([{"not": "result"}]))

    outcome = await worker.run_once()

    assert outcome.status is WorkAssignmentRunStatus.DEAD_LETTER
    assert outcome.error_category is WorkAssignmentFailureCategory.EXECUTOR_RESULT_INVALID
    assert queue.status(item_id) == "dead_letter"


@pytest.mark.asyncio
async def test_executor_result_outside_owned_paths_dead_letters() -> None:
    clock = ManualClock()
    queue = InMemoryWorkQueue(now_fn=clock.now)
    assignment = _assignment()
    item_id, worker = await _enqueue_worker(
        queue=queue,
        assignment=assignment,
        executor=RecordingExecutor([_outside_result(assignment)]),
    )

    outcome = await worker.run_once()

    assert outcome.status is WorkAssignmentRunStatus.DEAD_LETTER
    assert outcome.error_category is WorkAssignmentFailureCategory.EXECUTOR_RESULT_INVALID
    assert queue.status(item_id) == "dead_letter"


@pytest.mark.asyncio
async def test_executor_can_explicitly_renew_current_lease() -> None:
    clock = ManualClock()
    queue = InMemoryWorkQueue(now_fn=clock.now)
    assignment = _assignment()

    async def _renew_then_result(context: WorkAssignmentExecutionContext) -> WorkResult:
        assert await context.renew_lease(120) is True
        return _result(assignment)

    item_id, worker = await _enqueue_worker(
        queue=queue,
        assignment=assignment,
        executor=RecordingExecutor([_renew_then_result]),
    )

    outcome = await worker.run_once()

    assert outcome.status is WorkAssignmentRunStatus.COMPLETED
    assert queue.status(item_id) == "completed"


@pytest.mark.asyncio
async def test_event_emission_failure_after_durable_completion_does_not_reopen_work() -> None:
    clock = ManualClock()
    queue = InMemoryWorkQueue(now_fn=clock.now)
    assignment = _assignment()
    item_id = await queue.enqueue(_item(assignment))

    async def _bad_sink(event: WorkAssignmentLifecycleEvent) -> None:
        raise RuntimeError("event sink down")

    worker = WorkAssignmentWorker(
        queue=queue,
        executor_registry=_registry(RecordingExecutor([_result(assignment)])),
        worker_id="worker",
        lifecycle_event_sink=_bad_sink,
    )

    outcome = await worker.run_once()

    assert outcome.status is WorkAssignmentRunStatus.COMPLETED_EVENT_FAILED
    assert queue.status(item_id) == "completed"


@pytest.mark.asyncio
async def test_identical_delivery_cannot_bypass_fencing() -> None:
    clock = ManualClock()
    queue = InMemoryWorkQueue(now_fn=clock.now)
    assignment = _assignment()
    item_id = await queue.enqueue(_item(assignment))
    claim = await queue.claim_next("worker", lease_seconds=30)
    worker = WorkAssignmentWorker(
        queue=queue,
        executor_registry=_registry(RecordingExecutor([_result(assignment), _result(assignment)])),
        worker_id="worker",
    )

    first = await worker.process_claim(claim)
    second = await worker.process_claim(claim)

    assert first.status is WorkAssignmentRunStatus.COMPLETED
    assert second.status is WorkAssignmentRunStatus.STALE_CLAIM
    assert queue.status(item_id) == "completed"
