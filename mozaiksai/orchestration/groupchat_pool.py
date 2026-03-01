# ==============================================================================
# FILE: orchestration/groupchat_pool.py
# DESCRIPTION: GroupChat execution pool for the UniversalOrchestrator.
#
# Executes N sub-GroupChats (from a DecompositionPlan) either in parallel
# or sequentially.  Each sub-chat is a full workflow execution through the
# existing AG2OrchestrationAdapter.
#
# ** STREAMING MIGRATION BOUNDARY **
# When the underlying agentic framework evolves to native async streaming,
# ONLY this file changes:
#   - Replace `AG2OrchestrationAdapter.run()` calls with native streaming APIs
#   - Replace resume semantics with native stream continuation
# The OrchestrationPort contract above stays unchanged.
# ==============================================================================
"""
GroupChat execution pool.

Takes sub-run descriptors (from a DecompositionPlan) and executes them
through the AG2OrchestrationAdapter, yielding DomainEvents for each
sub-chat lifecycle.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, AsyncIterator

from mozaiksai.core.contracts.events import EVENT_SCHEMA_VERSION, DomainEvent
from mozaiksai.core.contracts.runner import RunRequest
from mozaiksai.core.contracts.taxonomy import (
    PROCESS_COMPLETED_EVENT_TYPE,
    PROCESS_FAILED_EVENT_TYPE,
    PROCESS_STARTED_EVENT_TYPE,
)
from mozaiksai.orchestration.decomposition import (
    DecompositionPlan,
    ExecutionMode,
    SubTask,
)
from mozaiksai.orchestration.merge import ChildResult

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Domain event helpers
# ---------------------------------------------------------------------------

def _now() -> datetime:
    return datetime.now(timezone.utc)


def _make_event(
    event_type: str,
    seq: int,
    run_id: str,
    payload: dict[str, Any],
    metadata: dict[str, Any] | None = None,
) -> DomainEvent:
    return DomainEvent(
        event_type=event_type,
        seq=seq,
        occurred_at=_now(),
        run_id=run_id,
        schema_version=EVENT_SCHEMA_VERSION,
        payload=payload,
        metadata=metadata,
    )


# ---------------------------------------------------------------------------
# GroupChatPool
# ---------------------------------------------------------------------------

class GroupChatPool:
    """Executes sub-GroupChats and collects their results.

    Each sub-chat is run through the ``OrchestrationPort`` (currently backed
    by ``AG2OrchestrationAdapter``).  The pool yields ``DomainEvent``s for
    each sub-chat lifecycle and returns a list of ``ChildResult``s.

    Thread safety: one pool instance per parent orchestration run.
    Not reusable across runs.
    """

    def __init__(self, *, parent_run_id: str, parent_app_id: str, parent_user_id: str) -> None:
        self._parent_run_id = parent_run_id
        self._parent_app_id = parent_app_id
        self._parent_user_id = parent_user_id
        self._results: list[ChildResult] = []
        self._seq = 0

    @property
    def results(self) -> list[ChildResult]:
        return list(self._results)

    def _next_seq(self) -> int:
        s = self._seq
        self._seq += 1
        return s

    async def execute(
        self,
        plan: DecompositionPlan,
    ) -> AsyncIterator[DomainEvent]:
        """Execute all sub-tasks in the plan and yield lifecycle events.

        Returns an async iterator of DomainEvent covering the pool lifecycle:
        - ``orchestration.pool_started``
        - Per sub-task: ``process.started`` / ``process.completed`` or ``process.failed``
        - ``orchestration.pool_completed``
        """
        pool_run_id = f"{self._parent_run_id}__pool_{uuid.uuid4().hex[:8]}"

        yield _make_event(
            event_type="orchestration.pool_started",
            seq=self._next_seq(),
            run_id=pool_run_id,
            payload={
                "parent_run_id": self._parent_run_id,
                "task_count": plan.task_count,
                "execution_mode": plan.execution_mode.value,
                "reason": plan.reason,
            },
        )

        if plan.execution_mode == ExecutionMode.PARALLEL:
            async for event in self._execute_parallel(plan, pool_run_id):
                yield event
        else:
            async for event in self._execute_sequential(plan, pool_run_id):
                yield event

        ok = sum(1 for r in self._results if r.success)
        total = len(self._results)

        yield _make_event(
            event_type="orchestration.pool_completed",
            seq=self._next_seq(),
            run_id=pool_run_id,
            payload={
                "parent_run_id": self._parent_run_id,
                "total": total,
                "succeeded": ok,
                "failed": total - ok,
                "all_succeeded": ok == total,
            },
        )

    # -----------------------------------------------------------------------
    # DAG topological sort
    # -----------------------------------------------------------------------

    @staticmethod
    def _topological_sort(sub_tasks: tuple[SubTask, ...]) -> list[SubTask]:
        """Sort sub-tasks respecting ``depends_on`` edges (Kahn's algorithm).

        Tasks with no dependencies come first.  If there are no
        ``depends_on`` edges at all, the original order is preserved.
        Raises ``ValueError`` on cyclic dependencies.
        """
        if not any(st.depends_on for st in sub_tasks):
            return list(sub_tasks)

        by_id: dict[str, SubTask] = {st.task_id: st for st in sub_tasks}
        # Also allow depends_on to reference workflow_name (user convenience)
        by_wf: dict[str, SubTask] = {st.workflow_name: st for st in sub_tasks}

        # Build in-degree map
        in_degree: dict[str, int] = {st.task_id: 0 for st in sub_tasks}
        reverse_edges: dict[str, list[str]] = {st.task_id: [] for st in sub_tasks}

        for st in sub_tasks:
            for dep in st.depends_on:
                dep_task = by_id.get(dep) or by_wf.get(dep)
                if dep_task is None:
                    logger.warning("[POOL] depends_on '%s' not found — ignored", dep)
                    continue
                in_degree[st.task_id] += 1
                reverse_edges[dep_task.task_id].append(st.task_id)

        # Kahn's: start with zero in-degree nodes
        queue = [tid for tid, deg in in_degree.items() if deg == 0]
        ordered: list[SubTask] = []

        while queue:
            tid = queue.pop(0)
            ordered.append(by_id[tid])
            for child_tid in reverse_edges[tid]:
                in_degree[child_tid] -= 1
                if in_degree[child_tid] == 0:
                    queue.append(child_tid)

        if len(ordered) != len(sub_tasks):
            raise ValueError(
                f"Cyclic dependency detected in sub-tasks. "
                f"Sorted {len(ordered)}/{len(sub_tasks)} tasks."
            )
        return ordered

    # -----------------------------------------------------------------------
    # Parallel execution
    # -----------------------------------------------------------------------

    async def _execute_parallel(
        self,
        plan: DecompositionPlan,
        pool_run_id: str,
    ) -> AsyncIterator[DomainEvent]:
        """Fire sub-tasks concurrently, respecting DAG ``depends_on`` edges.

        Tasks with no dependencies start immediately.  Tasks with dependencies
        wait until all prerequisite tasks have completed before launching.
        """
        sorted_tasks = self._topological_sort(plan.sub_tasks)

        # Track completed task IDs so dependents know when to start
        completed_ids: set[str] = set()
        completion_events: dict[str, asyncio.Event] = {
            st.task_id: asyncio.Event() for st in sorted_tasks
        }

        sub_queues: dict[str, asyncio.Queue[DomainEvent | None]] = {}
        tasks: list[asyncio.Task] = []

        async def _wait_and_run(sub_task: SubTask, q: asyncio.Queue[DomainEvent | None]) -> None:
            # Wait for all dependencies to complete
            for dep in sub_task.depends_on:
                dep_task_id = self._resolve_dep_id(dep, sorted_tasks)
                if dep_task_id and dep_task_id in completion_events:
                    await completion_events[dep_task_id].wait()
            # Run the sub-task
            await self._run_sub_task(sub_task, pool_run_id, q)
            completed_ids.add(sub_task.task_id)
            completion_events[sub_task.task_id].set()

        for sub_task in sorted_tasks:
            q: asyncio.Queue[DomainEvent | None] = asyncio.Queue()
            sub_queues[sub_task.task_id] = q
            task = asyncio.create_task(
                _wait_and_run(sub_task, q),
                name=f"pool_{sub_task.task_id}",
            )
            tasks.append(task)

        # Drain queues round-robin until all done
        active = set(sub_queues.keys())
        while active:
            for task_id in list(active):
                q = sub_queues[task_id]
                try:
                    evt = q.get_nowait()
                except asyncio.QueueEmpty:
                    continue
                if evt is None:
                    active.discard(task_id)
                    continue
                yield evt

            if active:
                await asyncio.sleep(0.05)

        # Ensure all tasks are gathered (surface any unhandled exceptions)
        for t in tasks:
            try:
                await t
            except Exception as exc:
                logger.warning("[POOL] Sub-task raised: %s", exc)

    # -----------------------------------------------------------------------
    # Sequential execution
    # -----------------------------------------------------------------------

    async def _execute_sequential(
        self,
        plan: DecompositionPlan,
        pool_run_id: str,
    ) -> AsyncIterator[DomainEvent]:
        """Execute sub-tasks one at a time in topological (DAG) order."""
        sorted_tasks = self._topological_sort(plan.sub_tasks)
        for sub_task in sorted_tasks:
            q: asyncio.Queue[DomainEvent | None] = asyncio.Queue()
            await self._run_sub_task(sub_task, pool_run_id, q)
            while True:
                evt = await q.get()
                if evt is None:
                    break
                yield evt

    # -----------------------------------------------------------------------
    # Helpers
    # -----------------------------------------------------------------------

    @staticmethod
    def _resolve_dep_id(dep: str, tasks: list[SubTask]) -> str | None:
        """Resolve a ``depends_on`` value to a task_id.

        Accepts either a task_id or a workflow_name.
        """
        for t in tasks:
            if t.task_id == dep or t.workflow_name == dep:
                return t.task_id
        return None

    # -----------------------------------------------------------------------
    # Single sub-task execution
    # -----------------------------------------------------------------------

    async def _run_sub_task(
        self,
        sub_task: SubTask,
        pool_run_id: str,
        event_queue: asyncio.Queue[DomainEvent | None],
    ) -> None:
        """Execute a single sub-task and push events + ChildResult."""
        sub_run_id = f"{pool_run_id}__{sub_task.task_id}"
        child_chat_id = f"chat_{sub_task.task_id}_{uuid.uuid4().hex[:8]}"

        # Emit process.started
        await event_queue.put(_make_event(
            event_type=PROCESS_STARTED_EVENT_TYPE,
            seq=self._next_seq(),
            run_id=sub_run_id,
            payload={
                "run_id": sub_run_id,
                "parent_run_id": self._parent_run_id,
                "workflow_name": sub_task.workflow_name,
                "task_id": sub_task.task_id,
            },
        ))

        text_output = ""
        structured_output: dict[str, Any] = {}
        success = False
        error_msg: str | None = None

        try:
            # Build RunRequest for the sub-chat
            request = RunRequest(
                run_id=sub_run_id,
                workflow_name=sub_task.workflow_name,
                app_id=self._parent_app_id,
                user_id=self._parent_user_id,
                chat_id=child_chat_id,
                payload={
                    "initial_message": sub_task.initial_message or "",
                    "parent_run_id": self._parent_run_id,
                },
                metadata={
                    "task_id": sub_task.task_id,
                    "pool_run_id": pool_run_id,
                    **(sub_task.metadata or {}),
                },
            )

            # Execute through the adapter
            from mozaiksai.core.ports.ag2_adapter import get_ag2_orchestration_adapter
            adapter = get_ag2_orchestration_adapter()

            # The adapter's run() is an async generator.  Consume it and
            # forward any DomainEvents it yields.
            async for domain_event in adapter.run(request):
                # Re-tag with sub_run_id for traceability
                forwarded = _make_event(
                    event_type=domain_event.event_type,
                    seq=self._next_seq(),
                    run_id=sub_run_id,
                    payload=domain_event.payload,
                    metadata={
                        **(domain_event.metadata or {}),
                        "source_run_id": domain_event.run_id,
                        "task_id": sub_task.task_id,
                    },
                )
                await event_queue.put(forwarded)

                # Capture final output from the adapter's completion event
                if domain_event.event_type in ("workflow.run_completed", "workflow.resume_completed"):
                    text_output = str(domain_event.payload.get("result", ""))
                    structured_output = domain_event.payload.get("structured_output", {})

            success = True

        except Exception as exc:
            error_msg = str(exc)
            logger.error("[POOL] Sub-task %s failed: %s", sub_task.task_id, exc, exc_info=True)

            await event_queue.put(_make_event(
                event_type=PROCESS_FAILED_EVENT_TYPE,
                seq=self._next_seq(),
                run_id=sub_run_id,
                payload={
                    "run_id": sub_run_id,
                    "task_id": sub_task.task_id,
                    "error": error_msg,
                },
            ))

        if success:
            await event_queue.put(_make_event(
                event_type=PROCESS_COMPLETED_EVENT_TYPE,
                seq=self._next_seq(),
                run_id=sub_run_id,
                payload={
                    "run_id": sub_run_id,
                    "task_id": sub_task.task_id,
                    "result": text_output[:500] if text_output else "ok",
                },
            ))

        # Record result
        self._results.append(ChildResult(
            task_id=sub_task.task_id,
            workflow_name=sub_task.workflow_name,
            run_id=sub_run_id,
            text_output=text_output,
            structured_output=structured_output if isinstance(structured_output, dict) else {},
            success=success,
            error=error_msg,
            metadata=sub_task.metadata,
        ))

        # Sentinel to signal queue completion
        await event_queue.put(None)


__all__ = ["GroupChatPool"]
