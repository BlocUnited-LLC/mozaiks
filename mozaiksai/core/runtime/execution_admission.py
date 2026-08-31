"""Durable workflow execution admission ahead of chat mutation and AG2.

Mongo owns immutable admission, cross-process claim ownership, bounded retry
state, and terminal replay. The existing chat execution lease remains the
per-chat mutation authority inside the admitted callback. Local mode is an
explicit single-process, non-durable boundary.
"""
from __future__ import annotations

import asyncio
import contextlib
import os
import socket
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from mozaiksai.core.workflow.queue import (
    MongoWorkflowQueue,
    QueueAuthorityUnavailableError,
    QueueIdentityConflictError,
    QueueItem,
    QueueItemStatus,
    WorkflowAdmissionMode,
    canonical_admission_id,
    get_workflow_admission_mode,
    get_workflow_queue,
)


class WorkflowAdmissionBusyError(RuntimeError):
    """Another consumer currently owns this execution admission."""


class WorkflowAdmissionRejectedError(RuntimeError):
    """The durable admission is terminal and cannot be executed."""


class WorkflowAdmissionExpiredError(RuntimeError):
    """A started claim expired and was refused automatic replay."""


class WorkflowAdmissionDeadLetterError(RuntimeError):
    """The admission already reached the durable dead-letter state."""


class WorkflowClaimLostError(RuntimeError):
    """The active consumer lost its renewable Mongo claim."""


@dataclass(frozen=True)
class WorkflowAdmissionRequest:
    tenant_id: str
    workspace_id: str
    app_id: str
    chat_id: str
    workflow_name: str
    run_id: str
    operation_id: str
    request_digest: str
    user_id: str | None = None

    def __post_init__(self) -> None:
        for name in (
            "tenant_id",
            "workspace_id",
            "app_id",
            "chat_id",
            "workflow_name",
            "run_id",
            "operation_id",
            "request_digest",
        ):
            if not str(getattr(self, name) or "").strip():
                raise ValueError(f"{name} is required for workflow admission")

    @property
    def admission_id(self) -> str:
        return canonical_admission_id(
            tenant_id=self.tenant_id,
            workspace_id=self.workspace_id,
            app_id=self.app_id,
            chat_id=self.chat_id,
            workflow_name=self.workflow_name,
            run_id=self.run_id,
            operation_id=self.operation_id,
        )


_local_locks: dict[str, asyncio.Lock] = {}
_local_request_digests: dict[str, str] = {}
_local_results: dict[str, dict[str, Any]] = {}
_local_semaphore = asyncio.Semaphore(
    max(1, int(os.getenv("MOZAIKS_MAX_PARALLEL_WORKFLOWS", "4")))
)


def _worker_id() -> str:
    return f"{socket.gethostname()}:{os.getpid()}:{uuid4()}"


async def _run_local(
    request: WorkflowAdmissionRequest,
    execute: Callable[[], Awaitable[dict[str, Any]]],
) -> dict[str, Any]:
    lock = _local_locks.setdefault(request.admission_id, asyncio.Lock())
    async with lock:
        previous_digest = _local_request_digests.get(request.admission_id)
        if previous_digest is not None and previous_digest != request.request_digest:
            raise QueueIdentityConflictError(
                f"local admission identity conflict for {request.admission_id}"
            )
        if request.admission_id in _local_results:
            return dict(_local_results[request.admission_id])
        _local_request_digests[request.admission_id] = request.request_digest
        async with _local_semaphore:
            result = await execute()
        _local_results[request.admission_id] = dict(result)
        return result


async def execute_with_workflow_admission(
    request: WorkflowAdmissionRequest,
    execute: Callable[[], Awaitable[dict[str, Any]]],
) -> dict[str, Any]:
    """Execute exactly once per immutable admission identity.

    Automatic claim recovery is permitted only before ``execution_started_at``.
    A crash after that marker is terminally dead-lettered on replay so Mozaiks
    never re-spends model tokens or repeats a tool/external side effect without
    an independent idempotency proof.
    """
    if get_workflow_admission_mode() is WorkflowAdmissionMode.LOCAL:
        return await _run_local(request, execute)

    queue = get_workflow_queue()
    if not isinstance(queue, MongoWorkflowQueue):
        raise QueueAuthorityUnavailableError("required workflow admission is not Mongo-backed")

    item = QueueItem(
        item_id=request.admission_id,
        tenant_id=request.tenant_id,
        workspace_id=request.workspace_id,
        app_id=request.app_id,
        chat_id=request.chat_id,
        workflow_name=request.workflow_name,
        run_id=request.run_id,
        operation_id=request.operation_id,
        request_digest=request.request_digest,
        user_id=request.user_id,
        payload={},
    )
    await queue.enqueue(item, max_attempts=3)
    claim = await queue.claim_item(request.admission_id, _worker_id())
    if not claim.claimed or not claim.claim_token:
        await queue.dead_letter_exhausted(request.admission_id)
        current = await queue.get(request.admission_id)
        if current is None:
            raise QueueAuthorityUnavailableError("durable admission disappeared after enqueue")
        if current.status is QueueItemStatus.COMPLETED:
            return dict(current.completed_result or {})
        if current.status is QueueItemStatus.DEAD_LETTER:
            raise WorkflowAdmissionDeadLetterError(current.error_category or "dead_letter")
        if (
            current.status is QueueItemStatus.CLAIMED
            and current.execution_started_at is not None
            and await queue.dead_letter_expired_started(request.admission_id)
        ):
            raise WorkflowAdmissionExpiredError("expired_after_execution_started")
        raise WorkflowAdmissionBusyError(request.admission_id)

    claim_token = claim.claim_token
    lease_seconds = max(10, int(os.getenv("WORKFLOW_QUEUE_LEASE_SECONDS", "300")))
    owner = asyncio.current_task()
    lost = asyncio.Event()

    async def _renew() -> None:
        try:
            while True:
                await asyncio.sleep(max(1.0, lease_seconds / 3))
                if not await queue.renew_lease(
                    request.admission_id,
                    claim_token=claim_token,
                    extend_seconds=lease_seconds,
                ):
                    lost.set()
                    if owner is not None:
                        owner.cancel()
                    return
        except asyncio.CancelledError:
            raise
        except Exception:
            lost.set()
            if owner is not None:
                owner.cancel()

    renewal = asyncio.create_task(_renew(), name=f"workflow-admission-renew:{request.admission_id}")
    started = False
    try:
        started = await queue.mark_execution_started(
            request.admission_id,
            claim_token=claim_token,
        )
        if not started:
            raise WorkflowClaimLostError(request.admission_id)
        result = await execute()
        if lost.is_set():
            raise WorkflowClaimLostError(request.admission_id)
        if not await queue.complete(
            request.admission_id,
            claim_token=claim_token,
            result=result,
        ):
            raise WorkflowClaimLostError(request.admission_id)
        return result
    except asyncio.CancelledError as exc:
        if lost.is_set():
            raise WorkflowClaimLostError(request.admission_id) from exc
        raise
    except WorkflowClaimLostError:
        raise
    except Exception:
        if started:
            with contextlib.suppress(Exception):
                await queue.dead_letter(
                    request.admission_id,
                    claim_token=claim_token,
                    error_category="execution_failed_after_start",
                )
        else:
            with contextlib.suppress(Exception):
                await queue.fail(
                    request.admission_id,
                    claim_token=claim_token,
                    error_category="admission_failed_before_start",
                )
        raise
    finally:
        renewal.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await renewal


def reset_local_workflow_admission() -> None:
    _local_locks.clear()
    _local_request_digests.clear()
    _local_results.clear()


__all__ = [
    "WorkflowAdmissionBusyError",
    "WorkflowAdmissionDeadLetterError",
    "WorkflowAdmissionExpiredError",
    "WorkflowAdmissionRejectedError",
    "WorkflowAdmissionRequest",
    "WorkflowClaimLostError",
    "execute_with_workflow_admission",
    "reset_local_workflow_admission",
]
