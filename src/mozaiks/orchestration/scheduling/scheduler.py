"""Deterministic task scheduler for DAG execution."""

from __future__ import annotations

from dataclasses import dataclass, field

from ..domain.dag import TaskDAG, TaskNode
from ..domain.state import TaskExecutionState, TaskStatus


@dataclass(slots=True, kw_only=True)
class SchedulingResult:
    ready_tasks: list[TaskNode] = field(default_factory=list)
    blocked_tasks: dict[str, list[str]] = field(default_factory=dict)
    completed_tasks: list[str] = field(default_factory=list)
    failed_tasks: list[str] = field(default_factory=list)

    @property
    def all_completed(self) -> bool:
        return not self.ready_tasks and not self.blocked_tasks and not self.failed_tasks


class DeterministicScheduler:
    """Pure deterministic scheduler."""

    def next_batch(
        self,
        dag: TaskDAG,
        task_states: dict[str, TaskExecutionState],
        *,
        batch_size: int,
    ) -> list[TaskNode]:
        """Return the next deterministic batch of schedulable tasks."""
        if batch_size <= 0:
            return []

        ready_tasks = self._ready_tasks(dag=dag, task_states=task_states)
        return ready_tasks[:batch_size]

    def schedule(
        self,
        dag: TaskDAG,
        task_states: dict[str, TaskExecutionState],
        *,
        max_parallel_tasks: int = 1,
    ) -> SchedulingResult:
        result = SchedulingResult()

        ready_tasks = self._ready_tasks(dag=dag, task_states=task_states)
        for node in dag.nodes:
            state = task_states[node.task_id]
            if state.phase == TaskStatus.COMPLETED:
                result.completed_tasks.append(node.task_id)
                continue
            if state.phase in (TaskStatus.FAILED, TaskStatus.CANCELLED):
                result.failed_tasks.append(node.task_id)
                continue
            if state.phase == TaskStatus.RUNNING:
                continue

            dependencies = dag.get_dependencies(node.task_id)
            pending_dependencies = [
                dep
                for dep in dependencies
                if task_states[dep].phase != TaskStatus.COMPLETED
            ]

            if pending_dependencies:
                result.blocked_tasks[node.task_id] = pending_dependencies

        result.ready_tasks = ready_tasks
        if max_parallel_tasks > 0:
            result.ready_tasks = result.ready_tasks[:max_parallel_tasks]
        else:
            result.ready_tasks = []

        return result

    def _ready_tasks(
        self,
        *,
        dag: TaskDAG,
        task_states: dict[str, TaskExecutionState],
    ) -> list[TaskNode]:
        ready: list[TaskNode] = []
        for node in dag.nodes:
            state = task_states[node.task_id]
            if state.phase in (
                TaskStatus.RUNNING,
                TaskStatus.COMPLETED,
                TaskStatus.FAILED,
                TaskStatus.CANCELLED,
            ):
                continue
            dependencies = dag.get_dependencies(node.task_id)
            if any(task_states[dep].phase != TaskStatus.COMPLETED for dep in dependencies):
                continue
            ready.append(node)

        # Deterministic ordering by priority descending then task_id ascending.
        ready.sort(key=lambda task: (-task.priority, task.task_id))
        return ready
