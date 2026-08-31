from __future__ import annotations

import asyncio
from typing import Any

import pytest

from mozaiksai.core.runtime.execution_admission import (
    WorkflowAdmissionBusyError,
    WorkflowAdmissionRequest,
    execute_with_workflow_admission,
    reset_local_workflow_admission,
)
from mozaiksai.core.workflow import queue as queue_mod
from mozaiksai.core.workflow.queue import (
    ClaimResult,
    MongoWorkflowQueue,
    QueueAuthorityUnavailableError,
    QueueItem,
    QueueItemStatus,
    WorkflowAdmissionMode,
    canonical_admission_id,
)


class _MemoryMongoQueue(MongoWorkflowQueue):
    def __init__(self) -> None:
        super().__init__()
        self.item: QueueItem | None = None
        self.token = "claim-token"
        self.claim_calls = 0
        self.fail_enqueue = False
        self.enqueue_gate: asyncio.Event | None = None

    async def enqueue(self, item: QueueItem, **_kwargs: Any) -> str:
        if self.enqueue_gate is not None:
            await self.enqueue_gate.wait()
        if self.fail_enqueue:
            raise QueueAuthorityUnavailableError("outage")
        if self.item is None:
            self.item = item
        return item.item_id

    async def claim_item(self, item_id: str, worker_id: str, **_kwargs: Any) -> ClaimResult:
        del item_id, worker_id
        self.claim_calls += 1
        assert self.item is not None
        if self.item.status is not QueueItemStatus.PENDING:
            return ClaimResult(False)
        self.item.status = QueueItemStatus.CLAIMED
        self.item.claim_token = self.token
        self.item.attempt_count = 1
        return ClaimResult(True, self.item, self.token, 1)

    async def get(self, _item_id: str) -> QueueItem | None:
        return self.item

    async def mark_execution_started(self, _item_id: str, *, claim_token: str) -> bool:
        assert self.item is not None
        if self.item.claim_token != claim_token:
            return False
        self.item.execution_started_at = "started"
        return True

    async def renew_lease(self, *_args: Any, **_kwargs: Any) -> bool:
        return True

    async def complete(
        self,
        _item_id: str,
        *,
        claim_token: str,
        result: dict[str, Any] | None = None,
    ) -> bool:
        assert self.item is not None
        if self.item.claim_token != claim_token:
            return False
        self.item.status = QueueItemStatus.COMPLETED
        self.item.completed_result = result
        return True

    async def dead_letter_expired_started(self, _item_id: str) -> bool:
        return False

    async def dead_letter_exhausted(self, _item_id: str) -> bool:
        return False

    async def dead_letter(self, *_args: Any, **_kwargs: Any) -> bool:
        assert self.item is not None
        self.item.status = QueueItemStatus.DEAD_LETTER
        return True

    async def fail(self, *_args: Any, **_kwargs: Any) -> str | None:
        assert self.item is not None
        self.item.status = QueueItemStatus.RETRYABLE
        return QueueItemStatus.RETRYABLE.value


@pytest.fixture(autouse=True)
def _reset_admission() -> None:
    queue_mod.reset_workflow_admission_state()
    reset_local_workflow_admission()
    yield
    queue_mod.reset_workflow_admission_state()
    reset_local_workflow_admission()


def _request(*, tenant_id: str = "tenant-a") -> WorkflowAdmissionRequest:
    return WorkflowAdmissionRequest(
        tenant_id=tenant_id,
        workspace_id="workspace-a",
        app_id="app-a",
        chat_id="chat-a",
        workflow_name="GenesisBuild",
        run_id="chat-a",
        operation_id="request-1",
        request_digest="a" * 64,
        user_id="user-a",
    )


def _required(fake: _MemoryMongoQueue) -> None:
    queue_mod._configured_admission_mode = WorkflowAdmissionMode.REQUIRED
    queue_mod._queue = fake


async def test_exact_completed_replay_does_not_execute_twice() -> None:
    fake = _MemoryMongoQueue()
    _required(fake)
    executions = 0

    async def execute() -> dict[str, Any]:
        nonlocal executions
        executions += 1
        return {"status": "success", "value": 7}

    assert await execute_with_workflow_admission(_request(), execute) == {
        "status": "success",
        "value": 7,
    }
    assert await execute_with_workflow_admission(_request(), execute) == {
        "status": "success",
        "value": 7,
    }
    assert executions == 1


async def test_concurrent_identical_consumer_is_busy_without_side_effect() -> None:
    fake = _MemoryMongoQueue()
    _required(fake)
    entered = asyncio.Event()
    release = asyncio.Event()
    executions = 0

    async def execute() -> dict[str, Any]:
        nonlocal executions
        executions += 1
        entered.set()
        await release.wait()
        return {"status": "success"}

    owner = asyncio.create_task(execute_with_workflow_admission(_request(), execute))
    await entered.wait()
    with pytest.raises(WorkflowAdmissionBusyError):
        await execute_with_workflow_admission(_request(), execute)
    assert executions == 1
    release.set()
    await owner


async def test_queue_outage_refuses_before_execution() -> None:
    fake = _MemoryMongoQueue()
    fake.fail_enqueue = True
    _required(fake)
    executed = False

    async def execute() -> dict[str, Any]:
        nonlocal executed
        executed = True
        return {"status": "success"}

    with pytest.raises(QueueAuthorityUnavailableError):
        await execute_with_workflow_admission(_request(), execute)
    assert not executed
    assert fake.claim_calls == 0


async def test_required_index_verification_failure_is_authority_failure(monkeypatch) -> None:
    class _BrokenCollection:
        async def create_index(self, *_args: Any, **_kwargs: Any) -> None:
            raise RuntimeError("index denied")

    queue = MongoWorkflowQueue()
    monkeypatch.setattr(queue, "_col", lambda *_args, **_kwargs: _BrokenCollection())
    with pytest.raises(QueueAuthorityUnavailableError, match="index"):
        await queue.ensure_indexes()


async def test_cancelled_producer_waiting_to_enqueue_never_claims_or_executes() -> None:
    fake = _MemoryMongoQueue()
    fake.enqueue_gate = asyncio.Event()
    _required(fake)
    executed = False

    async def execute() -> dict[str, Any]:
        nonlocal executed
        executed = True
        return {"status": "success"}

    task = asyncio.create_task(execute_with_workflow_admission(_request(), execute))
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert fake.claim_calls == 0
    assert not executed


def test_canonical_admission_identity_is_scope_bound_and_payload_independent() -> None:
    base = dict(
        workspace_id="workspace-a",
        app_id="app-a",
        chat_id="chat-a",
        workflow_name="RefinementRun",
        run_id="chat-a",
        operation_id="request-1",
    )
    assert canonical_admission_id(tenant_id="tenant-a", **base) != canonical_admission_id(
        tenant_id="tenant-b", **base
    )


async def test_explicit_local_mode_remains_usable_and_non_durable() -> None:
    queue_mod.configure_workflow_admission(WorkflowAdmissionMode.LOCAL)
    executions = 0

    async def execute() -> dict[str, Any]:
        nonlocal executions
        executions += 1
        return {"status": "success"}

    await execute_with_workflow_admission(_request(), execute)
    await execute_with_workflow_admission(_request(), execute)
    assert executions == 1

    reset_local_workflow_admission()
    await execute_with_workflow_admission(_request(), execute)
    assert executions == 2


def test_persisted_host_auto_selects_required_mode(monkeypatch) -> None:
    monkeypatch.delenv("MOZAIKS_WORKFLOW_ADMISSION_MODE", raising=False)
    monkeypatch.setenv("MONGO_URI", "mongodb://authority.example:27017")
    monkeypatch.setenv("MOZAIKS_DATABASE_STARTUP_POLICY", "best_effort")
    assert queue_mod.configure_workflow_admission() is WorkflowAdmissionMode.REQUIRED
