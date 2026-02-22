"""Canonical event model emitted by the engine."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, TypeAlias
from uuid import uuid4


@dataclass(slots=True, kw_only=True)
class EventBase:
    """Base class for all canonical events."""

    run_id: str
    task_id: str | None = None
    event_id: str = field(default_factory=lambda: str(uuid4()))
    occurred_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    event_type: str = field(init=False, default="event.base")

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["occurred_at"] = self.occurred_at.isoformat()
        return payload


@dataclass(slots=True, kw_only=True)
class RunStarted(EventBase):
    event_type: str = field(init=False, default="process.started")
    input_payload: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True, kw_only=True)
class RunRunning(EventBase):
    event_type: str = field(init=False, default="process.running")


@dataclass(slots=True, kw_only=True)
class RunResumed(EventBase):
    event_type: str = field(init=False, default="process.resumed")
    checkpoint_id: str


@dataclass(slots=True, kw_only=True)
class RunCancelled(EventBase):
    event_type: str = field(init=False, default="process.cancelled")
    reason: str = "cancelled"


@dataclass(slots=True, kw_only=True)
class RunCompleted(EventBase):
    event_type: str = field(init=False, default="process.completed")
    outputs: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True, kw_only=True)
class RunFailed(EventBase):
    event_type: str = field(init=False, default="process.failed")
    error: str


@dataclass(slots=True, kw_only=True)
class TaskStarted(EventBase):
    event_type: str = field(init=False, default="task.started")
    task_type: str
    inputs: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True, kw_only=True)
class TaskRetrying(EventBase):
    event_type: str = field(init=False, default="task.retrying")
    attempt: int
    reason: str


@dataclass(slots=True, kw_only=True)
class TaskCompleted(EventBase):
    event_type: str = field(init=False, default="task.completed")
    outputs: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True, kw_only=True)
class TaskFailed(EventBase):
    event_type: str = field(init=False, default="task.failed")
    attempt: int
    error: str


@dataclass(slots=True, kw_only=True)
class TaskCancelled(EventBase):
    event_type: str = field(init=False, default="task.cancelled")
    reason: str = "cancelled"


@dataclass(slots=True, kw_only=True)
class MessageDelta(EventBase):
    event_type: str = field(init=False, default="evaluation.message_delta")
    source: str
    delta: str


@dataclass(slots=True, kw_only=True)
class ToolCalled(EventBase):
    event_type: str = field(init=False, default="tool.called")
    tool_name: str
    tool_id: str | None = None
    execution_mode: str = "in_process"
    arguments: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True, kw_only=True)
class ToolCallCandidate(EventBase):
    event_type: str = field(init=False, default="tool.call_candidate")
    tool_name: str
    arguments: dict[str, Any] = field(default_factory=dict)
    source: str = "adapter"


@dataclass(slots=True, kw_only=True)
class ToolPolicyDecision(EventBase):
    event_type: str = field(init=False, default="tool.policy_decision")
    tool_name: str
    decision: str
    reason: str
    call_index_task: int
    call_index_run: int


@dataclass(slots=True, kw_only=True)
class UIToolRequested(EventBase):
    event_type: str = field(init=False, default="ui.tool.requested")
    tool_name: str
    ui_tool_id: str
    tool_id: str | None = None
    execution_mode: str = "in_process"
    blocking: bool = False
    awaiting_input: bool = False
    arguments: dict[str, Any] = field(default_factory=dict)
    component: str | None = None
    mode: str | None = None


@dataclass(slots=True, kw_only=True)
class UIToolInputSubmitted(EventBase):
    event_type: str = field(init=False, default="ui.tool.input.submitted")
    tool_name: str
    ui_tool_id: str
    tool_id: str | None = None
    execution_mode: str = "in_process"
    input_payload: dict[str, Any] = field(default_factory=dict)
    component: str | None = None
    mode: str | None = None


@dataclass(slots=True, kw_only=True)
class UIToolCompleted(EventBase):
    event_type: str = field(init=False, default="ui.tool.completed")
    tool_name: str
    ui_tool_id: str
    tool_id: str | None = None
    execution_mode: str = "in_process"
    success: bool
    output: Any = None
    error: str | None = None
    component: str | None = None
    mode: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True, kw_only=True)
class UIToolFailed(EventBase):
    event_type: str = field(init=False, default="ui.tool.failed")
    tool_name: str
    ui_tool_id: str
    tool_id: str | None = None
    execution_mode: str = "in_process"
    error: str
    component: str | None = None
    mode: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True, kw_only=True)
class ToolResult(EventBase):
    event_type: str = field(init=False, default="tool.result")
    tool_name: str
    tool_id: str | None = None
    execution_mode: str = "in_process"
    success: bool
    output: Any = None
    error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True, kw_only=True)
class ToolError(EventBase):
    event_type: str = field(init=False, default="tool.error")
    tool_name: str
    tool_id: str | None = None
    execution_mode: str = "in_process"
    error: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True, kw_only=True)
class StructuredOutputInvalid(EventBase):
    event_type: str = field(init=False, default="structured_output.invalid")
    errors: list[str] = field(default_factory=list)
    schema: dict[str, Any] = field(default_factory=dict)


CanonicalEvent: TypeAlias = (
    RunStarted
    | RunRunning
    | RunResumed
    | RunCancelled
    | RunCompleted
    | RunFailed
    | TaskStarted
    | TaskRetrying
    | TaskCompleted
    | TaskFailed
    | TaskCancelled
    | MessageDelta
    | ToolCalled
    | ToolCallCandidate
    | ToolPolicyDecision
    | UIToolRequested
    | UIToolInputSubmitted
    | UIToolCompleted
    | UIToolFailed
    | ToolResult
    | ToolError
    | StructuredOutputInvalid
)
