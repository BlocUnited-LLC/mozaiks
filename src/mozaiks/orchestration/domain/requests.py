"""Runner request models."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .dag import TaskDAG
from .runtime_context import RuntimeContext


@dataclass(slots=True, kw_only=True)
class RunRequest:
    run_id: str
    dag: TaskDAG
    initial_input: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    runtime_context: RuntimeContext | None = None


@dataclass(slots=True, kw_only=True)
class ResumeRequest:
    run_id: str
    dag: TaskDAG
    runtime_context: RuntimeContext | None = None
