"""Execution lifecycle state machine."""

from __future__ import annotations

from ..domain.state import RunExecutionState, RunPhase, TaskExecutionState, TaskPhase


class InvalidLifecycleTransition(RuntimeError):
    """Raised when orchestration attempts an invalid phase transition."""


class OrchestratorStateMachine:
    """Validates run/task phase transitions for deterministic lifecycle handling."""

    _RUN_TRANSITIONS: dict[RunPhase, set[RunPhase]] = {
        RunPhase.STARTED: {RunPhase.RUNNING, RunPhase.CANCELLED, RunPhase.FAILED},
        RunPhase.RUNNING: {RunPhase.COMPLETED, RunPhase.CANCELLED, RunPhase.FAILED},
        RunPhase.COMPLETED: set(),
        RunPhase.CANCELLED: set(),
        RunPhase.FAILED: set(),
    }

    _TASK_TRANSITIONS: dict[TaskPhase, set[TaskPhase]] = {
        TaskPhase.PENDING: {TaskPhase.READY, TaskPhase.RUNNING, TaskPhase.CANCELLED},
        TaskPhase.READY: {TaskPhase.RUNNING, TaskPhase.CANCELLED},
        TaskPhase.RUNNING: {
            TaskPhase.COMPLETED,
            TaskPhase.FAILED,
            TaskPhase.CANCELLED,
            TaskPhase.PENDING,
        },
        TaskPhase.COMPLETED: set(),
        TaskPhase.FAILED: set(),
        TaskPhase.CANCELLED: set(),
    }

    def transition_run(self, state: RunExecutionState, target: RunPhase) -> None:
        current = state.phase
        if current == target:
            return
        if target not in self._RUN_TRANSITIONS[current]:
            raise InvalidLifecycleTransition(
                f"Invalid run transition: {current.value} -> {target.value}"
            )
        state.phase = target

    def transition_task(self, state: TaskExecutionState, target: TaskPhase) -> None:
        current = state.phase
        if current == target:
            return
        if target not in self._TASK_TRANSITIONS[current]:
            raise InvalidLifecycleTransition(
                f"Invalid task transition: {current.value} -> {target.value}"
            )
        state.phase = target
