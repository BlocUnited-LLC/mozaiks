"""DAGExecutor — runtime-layer DAG scheduler for capability-based runs.

Executes a directed acyclic graph (DAG) of tasks by dispatching each
task through ``RunSupervisor.start_run()``.  Tasks whose dependencies
are satisfied run concurrently; topological order is enforced.

Design rules
------------
* Dispatches exclusively through ``RunSupervisor.start_run()``
* Never imports AG2, engine adapters, or workflow internals
* Propagates completed task outputs to dependent tasks via context
* Streams ``DomainEvent``s from all tasks in real time
* Reusable across orchestrators — no coupling to ``UniversalOrchestrator``

Execution model
---------------
Graph:

    A
    │
  ┌─┴─┐
  ▼   ▼
  B   C
  │   │
  └─┬─┘
    ▼
    D

1. Topological sort: A → [B, C] → D
2. A starts immediately (no dependencies).
3. B and C start concurrently once A completes.
4. D starts once both B and C complete.
5. Outputs from completed tasks are injected into dependents' context
   under ``context["upstream_outputs"][task_id]``.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, AsyncIterator

from mozaiksai.contracts import DomainEvent, RunRequest, EVENT_SCHEMA_VERSION

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class DAGTask:
    """A single node in the execution DAG.

    Parameters
    ----------
    task_id:
        Unique identifier within the DAG.  Used as the dependency key.
    capability:
        Capability name dispatched to ``RunSupervisor``.  Defaults to ``"agent"``.
    workflow_name:
        Optional workflow name forwarded inside ``RunRequest``.
    input:
        Task-specific input.  Upstream outputs are merged in before dispatch.
    metadata:
        Arbitrary metadata forwarded to ``RunRequest.metadata``.
    depends_on:
        IDs of tasks that must complete before this task can start.
    app_id / user_id / chat_id:
        Tenant/session identifiers forwarded to ``RunRequest``.
    """

    task_id: str
    capability: str = "agent"
    workflow_name: str | None = None
    input: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    depends_on: tuple[str, ...] = field(default_factory=tuple)
    app_id: str | None = None
    user_id: str | None = None
    chat_id: str | None = None


@dataclass
class DAGTaskResult:
    """Result of a single DAG task execution."""

    task_id: str
    run_id: str
    success: bool
    text_output: str = ""
    structured_output: dict[str, Any] = field(default_factory=dict)
    error: str | None = None


@dataclass
class DAGResult:
    """Aggregate result of a full DAG execution."""

    dag_run_id: str
    task_results: dict[str, DAGTaskResult]
    all_succeeded: bool

    @property
    def succeeded_count(self) -> int:
        return sum(1 for r in self.task_results.values() if r.success)

    @property
    def failed_count(self) -> int:
        return sum(1 for r in self.task_results.values() if not r.success)


# ---------------------------------------------------------------------------
# DAGExecutor
# ---------------------------------------------------------------------------

class DAGExecutor:
    """Schedules and executes a DAG of capability-based tasks.

    Usage::

        executor = DAGExecutor(run_supervisor=get_run_supervisor())

        tasks = [
            DAGTask(task_id="A", capability="agent", workflow_name="build"),
            DAGTask(task_id="B", capability="agent", workflow_name="auth", depends_on=("A",)),
            DAGTask(task_id="C", capability="agent", workflow_name="billing", depends_on=("A",)),
            DAGTask(task_id="D", capability="agent", workflow_name="gateway", depends_on=("B", "C")),
        ]

        result = DAGResult(...)
        async for event in executor.execute(tasks, parent_run_id="parent-1"):
            process(event)

    Parameters
    ----------
    run_supervisor :
        A ``RunSupervisor`` instance (or compatible duck-type with ``start_run``).
        If not provided, the global singleton is used.
    """

    def __init__(self, *, run_supervisor=None) -> None:
        self._run_supervisor = run_supervisor

    def _get_supervisor(self):
        if self._run_supervisor is not None:
            return self._run_supervisor
        from mozaiksai.runtime.execution.run_supervisor import get_run_supervisor
        return get_run_supervisor()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def execute(
        self,
        tasks: list[DAGTask],
        *,
        parent_run_id: str | None = None,
        app_id: str | None = None,
        user_id: str | None = None,
        chat_id: str | None = None,
    ) -> tuple[AsyncIterator[DomainEvent], DAGResult]:
        """Execute the DAG and return a (stream, result) pair.

        Prefer using ``execute_stream`` which handles the plumbing.

        Returns
        -------
        tuple[AsyncIterator[DomainEvent], DAGResult]
            An async iterator of DomainEvents and the final DAGResult.
            The result is populated after the iterator is exhausted.
        """
        raise NotImplementedError("Use execute_stream() instead.")

    async def execute_stream(
        self,
        tasks: list[DAGTask],
        *,
        parent_run_id: str | None = None,
        app_id: str | None = None,
        user_id: str | None = None,
        chat_id: str | None = None,
    ) -> AsyncIterator[DomainEvent]:
        """Execute the DAG, yielding ``DomainEvent``s as tasks run.

        After the iterator is exhausted, results are available via
        ``DAGExecutor.last_result``.

        Parameters
        ----------
        tasks :
            List of ``DAGTask`` nodes.  Order does not matter; topological
            sort is computed internally.
        parent_run_id :
            Optional parent run identifier for traceability.
        app_id / user_id / chat_id :
            Tenant/session identifiers applied to all tasks that don't
            supply their own.

        Yields
        ------
        DomainEvent
            Live events from all tasks, in completion order.
        """
        dag_run_id = parent_run_id or f"dag_{uuid.uuid4().hex[:12]}"
        task_results: dict[str, DAGTaskResult] = {}
        self.last_result: DAGResult | None = None

        if not tasks:
            self.last_result = DAGResult(
                dag_run_id=dag_run_id,
                task_results={},
                all_succeeded=True,
            )
            return

        try:
            sorted_tasks = self._topological_sort(tasks)
        except ValueError as exc:
            logger.error("[DAG_EXECUTOR] Topological sort failed: %s", exc)
            raise

        yield _make_event(
            event_type="dag.started",
            run_id=dag_run_id,
            payload={
                "dag_run_id": dag_run_id,
                "task_count": len(tasks),
                "task_ids": [t.task_id for t in sorted_tasks],
            },
        )

        # Shared completed outputs: task_id → text/structured output
        completed_outputs: dict[str, dict[str, Any]] = {}

        # Execute concurrently, honouring dependency order
        async for event in self._execute_concurrent(
            sorted_tasks,
            dag_run_id=dag_run_id,
            completed_outputs=completed_outputs,
            task_results=task_results,
            fallback_app_id=app_id,
            fallback_user_id=user_id,
            fallback_chat_id=chat_id,
        ):
            yield event

        all_ok = all(r.success for r in task_results.values())

        yield _make_event(
            event_type="dag.completed",
            run_id=dag_run_id,
            payload={
                "dag_run_id": dag_run_id,
                "total": len(task_results),
                "succeeded": sum(1 for r in task_results.values() if r.success),
                "failed": sum(1 for r in task_results.values() if not r.success),
                "all_succeeded": all_ok,
            },
        )

        self.last_result = DAGResult(
            dag_run_id=dag_run_id,
            task_results=task_results,
            all_succeeded=all_ok,
        )

    # ------------------------------------------------------------------
    # Topological sort (Kahn's algorithm)
    # ------------------------------------------------------------------

    @staticmethod
    def _topological_sort(tasks: list[DAGTask]) -> list[DAGTask]:
        """Return tasks in topological order respecting ``depends_on``.

        Raises ``ValueError`` on cycles.
        """
        if not any(t.depends_on for t in tasks):
            return list(tasks)

        by_id: dict[str, DAGTask] = {t.task_id: t for t in tasks}

        in_degree: dict[str, int] = {t.task_id: 0 for t in tasks}
        reverse_edges: dict[str, list[str]] = {t.task_id: [] for t in tasks}

        for task in tasks:
            for dep in task.depends_on:
                if dep not in by_id:
                    logger.warning(
                        "[DAG_EXECUTOR] depends_on '%s' not found in task list — ignored", dep
                    )
                    continue
                in_degree[task.task_id] += 1
                reverse_edges[dep].append(task.task_id)

        queue: list[str] = [tid for tid, deg in in_degree.items() if deg == 0]
        ordered: list[DAGTask] = []

        while queue:
            tid = queue.pop(0)
            ordered.append(by_id[tid])
            for child_tid in reverse_edges[tid]:
                in_degree[child_tid] -= 1
                if in_degree[child_tid] == 0:
                    queue.append(child_tid)

        if len(ordered) != len(tasks):
            raise ValueError(
                f"[DAG_EXECUTOR] Cycle detected — sorted {len(ordered)}/{len(tasks)} tasks."
            )

        return ordered

    # ------------------------------------------------------------------
    # Concurrent execution with dependency tracking
    # ------------------------------------------------------------------

    async def _execute_concurrent(
        self,
        sorted_tasks: list[DAGTask],
        *,
        dag_run_id: str,
        completed_outputs: dict[str, dict[str, Any]],
        task_results: dict[str, DAGTaskResult],
        fallback_app_id: str | None,
        fallback_user_id: str | None,
        fallback_chat_id: str | None,
    ) -> AsyncIterator[DomainEvent]:
        """Fire tasks as their dependencies complete.

        Uses per-task ``asyncio.Queue[DomainEvent | None]`` to stream events
        from concurrent tasks back to the caller without blocking.
        """
        completion_events: dict[str, asyncio.Event] = {
            t.task_id: asyncio.Event() for t in sorted_tasks
        }
        sub_queues: dict[str, asyncio.Queue] = {}
        asyncio_tasks: list[asyncio.Task] = []

        async def _wait_and_run(
            dag_task: DAGTask, q: asyncio.Queue
        ) -> None:
            # Wait for all declared dependencies
            for dep_id in dag_task.depends_on:
                if dep_id in completion_events:
                    await completion_events[dep_id].wait()

            # Build task_input with upstream outputs injected
            task_input = dict(dag_task.input)
            upstream: dict[str, Any] = {}
            for dep_id in dag_task.depends_on:
                if dep_id in completed_outputs:
                    upstream[dep_id] = completed_outputs[dep_id]
            if upstream:
                task_input["upstream_outputs"] = upstream

            await self._run_task(
                dag_task,
                dag_run_id=dag_run_id,
                task_input=task_input,
                event_queue=q,
                task_results=task_results,
                completed_outputs=completed_outputs,
                fallback_app_id=fallback_app_id,
                fallback_user_id=fallback_user_id,
                fallback_chat_id=fallback_chat_id,
            )
            completion_events[dag_task.task_id].set()

        for dag_task in sorted_tasks:
            q: asyncio.Queue = asyncio.Queue()
            sub_queues[dag_task.task_id] = q
            t = asyncio.create_task(
                _wait_and_run(dag_task, q),
                name=f"dag_{dag_task.task_id}",
            )
            asyncio_tasks.append(t)

        # Drain all queues until all tasks signal completion (None sentinel)
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
                await asyncio.sleep(0.01)

        # Surface any unhandled exceptions from tasks
        for t in asyncio_tasks:
            try:
                await t
            except Exception as exc:  # pragma: no cover
                logger.warning("[DAG_EXECUTOR] Task coroutine raised: %s", exc)

    # ------------------------------------------------------------------
    # Single task execution
    # ------------------------------------------------------------------

    async def _run_task(
        self,
        dag_task: DAGTask,
        *,
        dag_run_id: str,
        task_input: dict[str, Any],
        event_queue: asyncio.Queue,
        task_results: dict[str, DAGTaskResult],
        completed_outputs: dict[str, dict[str, Any]],
        fallback_app_id: str | None,
        fallback_user_id: str | None,
        fallback_chat_id: str | None,
    ) -> None:
        """Execute one task via RunSupervisor and push events + result."""
        run_id = f"{dag_run_id}__{dag_task.task_id}"

        await event_queue.put(_make_event(
            event_type="dag.task_started",
            run_id=run_id,
            payload={
                "task_id": dag_task.task_id,
                "dag_run_id": dag_run_id,
                "capability": dag_task.capability,
                "workflow_name": dag_task.workflow_name,
            },
        ))

        request = RunRequest(
            run_id=run_id,
            capability=dag_task.capability,
            workflow_name=dag_task.workflow_name,
            context=task_input,
            metadata={
                "task_id": dag_task.task_id,
                "dag_run_id": dag_run_id,
                **dag_task.metadata,
            },
            app_id=dag_task.app_id or fallback_app_id,
            user_id=dag_task.user_id or fallback_user_id,
            chat_id=dag_task.chat_id or fallback_chat_id,
        )

        text_output = ""
        structured_output: dict[str, Any] = {}
        success = False
        error_msg: str | None = None

        try:
            async for domain_event in self._get_supervisor().start_run(request):
                # Forward the event, re-tagged with dag context
                forwarded = _make_event(
                    event_type=domain_event.event_type,
                    run_id=run_id,
                    payload=domain_event.data,
                    metadata={
                        **(domain_event.metadata or {}),
                        "task_id": dag_task.task_id,
                        "dag_run_id": dag_run_id,
                        "source_run_id": domain_event.run_id,
                    },
                )
                await event_queue.put(forwarded)

                # Capture outputs from terminal events
                if domain_event.event_type in (
                    "workflow.run_completed",
                    "workflow.resume_completed",
                    "orchestration.run_completed",
                ):
                    text_output = str(domain_event.data.get("result", ""))
                    structured_output = domain_event.data.get("structured_output", {})
                    if not isinstance(structured_output, dict):
                        structured_output = {}

            success = True

        except Exception as exc:
            error_msg = str(exc)
            logger.error(
                "[DAG_EXECUTOR] Task '%s' failed: %s", dag_task.task_id, exc, exc_info=True
            )
            await event_queue.put(_make_event(
                event_type="dag.task_failed",
                run_id=run_id,
                payload={
                    "task_id": dag_task.task_id,
                    "dag_run_id": dag_run_id,
                    "error": error_msg,
                },
            ))

        if success:
            await event_queue.put(_make_event(
                event_type="dag.task_completed",
                run_id=run_id,
                payload={
                    "task_id": dag_task.task_id,
                    "dag_run_id": dag_run_id,
                    "result": text_output[:500] if text_output else "",
                },
            ))

        # Record result and store output for downstream tasks
        result = DAGTaskResult(
            task_id=dag_task.task_id,
            run_id=run_id,
            success=success,
            text_output=text_output,
            structured_output=structured_output,
            error=error_msg,
        )
        task_results[dag_task.task_id] = result
        completed_outputs[dag_task.task_id] = {
            "text_output": text_output,
            "structured_output": structured_output,
            "success": success,
        }

        # Signal queue completion
        await event_queue.put(None)


# ---------------------------------------------------------------------------
# Event helper
# ---------------------------------------------------------------------------

_seq_counter: int = 0


def _make_event(
    event_type: str,
    run_id: str,
    payload: dict[str, Any],
    metadata: dict[str, Any] | None = None,
) -> DomainEvent:
    global _seq_counter
    seq = _seq_counter
    _seq_counter += 1
    return DomainEvent(
        event_type=event_type,
        seq=seq,
        occurred_at=datetime.now(timezone.utc),
        run_id=run_id,
        schema_version=EVENT_SCHEMA_VERSION,
        data=payload,
        metadata=metadata,
    )


# ---------------------------------------------------------------------------
# Module exports
# ---------------------------------------------------------------------------

__all__ = [
    "DAGExecutor",
    "DAGResult",
    "DAGTask",
    "DAGTaskResult",
]
