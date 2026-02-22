"""Run and task state models."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any
from uuid import uuid4


class RunPhase(str, Enum):
    STARTED = "started"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class TaskPhase(str, Enum):
    PENDING = "pending"
    READY = "ready"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


# Backward-compatible aliases for existing imports.
RunStatus = RunPhase
TaskStatus = TaskPhase


@dataclass(slots=True, kw_only=True)
class TaskExecutionState:
    phase: TaskPhase = TaskPhase.PENDING
    attempts: int = 0
    inputs: dict[str, Any] = field(default_factory=dict)
    outputs: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    cancel_requested: bool = False

    @property
    def is_terminal(self) -> bool:
        return self.phase in (
            TaskPhase.COMPLETED,
            TaskPhase.FAILED,
            TaskPhase.CANCELLED,
        )

    @property
    def status(self) -> TaskPhase:
        """Backward-compatible status alias."""
        return self.phase

    @status.setter
    def status(self, value: TaskPhase) -> None:
        self.phase = value


@dataclass(slots=True, kw_only=True)
class RunExecutionState:
    run_id: str
    dag_id: str
    phase: RunPhase = RunPhase.STARTED
    task_states: dict[str, TaskExecutionState] = field(default_factory=dict)
    initial_input: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    cancel_requested: bool = False
    awaiting_ui_input: bool = False

    @property
    def status(self) -> RunPhase:
        """Backward-compatible status alias."""
        return self.phase

    @status.setter
    def status(self, value: RunPhase) -> None:
        self.phase = value

    @property
    def tasks(self) -> dict[str, TaskExecutionState]:
        """Backward-compatible tasks alias."""
        return self.task_states


@dataclass(slots=True, kw_only=True)
class RunCheckpoint:
    run_id: str
    dag_id: str
    checkpoint_version: str = "2.0.0-phase2a"
    checkpoint_key: str = "latest"
    checkpoint_id: str = field(default_factory=lambda: str(uuid4()))
    run_phase: RunPhase = RunPhase.STARTED
    task_states: dict[str, TaskExecutionState] = field(default_factory=dict)
    initial_input: dict[str, Any] = field(default_factory=dict)
    cancel_requested: bool = False
    dag_snapshot: dict[str, Any] | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def run_status(self) -> RunPhase:
        """Backward-compatible run_status alias."""
        return self.run_phase

    @run_status.setter
    def run_status(self, value: RunPhase) -> None:
        self.run_phase = value


# Backward-compatible alias for existing imports.
RunState = RunExecutionState
