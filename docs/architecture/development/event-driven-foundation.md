# Mozaiks Event-Driven Foundation

> **Status**: Design Document
> **Goal**: Define a modular event system that serves as the basis for all mozaiks apps

## Overview

Everything in a mozaiks app communicates through events on a unified stream. This enables:
- Loose coupling between components
- Real-time reactivity
- Easy extensibility
- Consistent patterns across all apps

```
┌─────────────────────────────────────────────────────────────────────┐
│                        UNIFIED EVENT STREAM                          │
│  ═══════════════════════════════════════════════════════════════    │
│                                                                      │
│   Agents      Tools       UI        Backend      Auth      System   │
│     ↕          ↕          ↕           ↕          ↕          ↕       │
│  emit/sub   emit/sub   emit/sub   emit/sub   emit/sub   emit/sub   │
└─────────────────────────────────────────────────────────────────────┘
```

---

## Event Modules

Events are organized into **modules**. Apps include only the modules they need.

| Module | Purpose | Required |
|--------|---------|----------|
| `core` | Base event types, stream primitives | Yes |
| `agent` | Agent messages, state, handoffs | If using AI |
| `tool` | Tool calls, results, errors | If using tools |
| `ui` | User input, render updates, navigation | If has UI |
| `auth` | Login, logout, token refresh | If has auth |
| `backend` | API requests, data mutations | If has backend |
| `workflow` | Workflow lifecycle, transitions | If using workflows |
| `system` | Errors, metrics, lifecycle | Yes |

---

## Module: `core`

Base primitives that all other modules extend.

```python
# mozaiksai/events/core.py

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Optional
from uuid import UUID, uuid4


@dataclass
class BaseEvent:
    """All events inherit from this."""
    event_id: UUID = field(default_factory=uuid4)
    timestamp: datetime = field(default_factory=datetime.utcnow)
    source: str = ""           # Who emitted: "agent:GreeterAgent", "ui", "backend"
    correlation_id: Optional[UUID] = None  # Links related events
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class Stream:
    """Event stream interface."""

    def send(self, event: BaseEvent) -> None:
        """Emit an event to the stream."""
        ...

    def subscribe(
        self,
        handler: Callable[[BaseEvent], None],
        event_types: list[type[BaseEvent]] | None = None,
        filter: Callable[[BaseEvent], bool] | None = None,
    ) -> SubscriptionId:
        """Subscribe to events."""
        ...

    def unsubscribe(self, sub_id: SubscriptionId) -> None:
        """Remove subscription."""
        ...

    def where(self, condition: Condition) -> "FilteredStream":
        """Create filtered view of stream."""
        ...
```

---

## Module: `agent`

Events for AI agent interactions.

```python
# mozaiksai/events/agent.py

from .core import BaseEvent
from dataclasses import dataclass
from typing import Any, Optional


# ─── Agent Messages ───────────────────────────────────────────────

@dataclass
class AgentMessage(BaseEvent):
    """An agent produced a message."""
    agent_name: str
    content: str
    role: str = "assistant"  # "assistant", "user", "system"
    message_type: str = "text"  # "text", "structured", "stream_chunk"


@dataclass
class AgentMessageChunk(BaseEvent):
    """Streaming chunk from an agent (for real-time UI updates)."""
    agent_name: str
    chunk: str
    chunk_index: int
    is_final: bool = False


@dataclass
class AgentThinking(BaseEvent):
    """Agent is processing (for loading indicators)."""
    agent_name: str
    status: str = "thinking"  # "thinking", "calling_tool", "waiting"


# ─── Agent State ──────────────────────────────────────────────────

@dataclass
class AgentStateChanged(BaseEvent):
    """Agent's internal state changed."""
    agent_name: str
    key: str
    old_value: Any
    new_value: Any


@dataclass
class ContextVariableSet(BaseEvent):
    """A context variable was set."""
    key: str
    value: Any
    scope: str = "workflow"  # "workflow", "agent", "global"


# ─── Agent Lifecycle ──────────────────────────────────────────────

@dataclass
class AgentActivated(BaseEvent):
    """An agent became the active speaker."""
    agent_name: str
    previous_agent: Optional[str] = None
    reason: str = ""


@dataclass
class AgentHandoff(BaseEvent):
    """Control passed from one agent to another."""
    from_agent: str
    to_agent: str
    handoff_type: str  # "condition", "after_work", "explicit"
    context: dict[str, Any] = field(default_factory=dict)


@dataclass
class AgentError(BaseEvent):
    """Agent encountered an error."""
    agent_name: str
    error_type: str
    error_message: str
    recoverable: bool = True
```

---

## Module: `tool`

Events for tool/function calls.

```python
# mozaiksai/events/tool.py

from .core import BaseEvent
from dataclasses import dataclass
from typing import Any, Optional


@dataclass
class ToolCallRequested(BaseEvent):
    """Agent requested a tool call."""
    tool_name: str
    agent_name: str
    arguments: dict[str, Any]
    call_id: str


@dataclass
class ToolCallStarted(BaseEvent):
    """Tool execution began."""
    tool_name: str
    call_id: str


@dataclass
class ToolCallCompleted(BaseEvent):
    """Tool execution finished successfully."""
    tool_name: str
    call_id: str
    result: Any
    duration_ms: int


@dataclass
class ToolCallFailed(BaseEvent):
    """Tool execution failed."""
    tool_name: str
    call_id: str
    error_type: str
    error_message: str


@dataclass
class ToolCallApprovalRequired(BaseEvent):
    """Tool needs human approval before executing (HITL)."""
    tool_name: str
    call_id: str
    agent_name: str
    arguments: dict[str, Any]
    risk_level: str = "medium"  # "low", "medium", "high"


@dataclass
class ToolCallApproved(BaseEvent):
    """Human approved tool execution."""
    call_id: str
    approved_by: str


@dataclass
class ToolCallRejected(BaseEvent):
    """Human rejected tool execution."""
    call_id: str
    rejected_by: str
    reason: Optional[str] = None
```

---

## Module: `ui`

Events for user interface interactions.

```python
# mozaiksai/events/ui.py

from .core import BaseEvent
from dataclasses import dataclass
from typing import Any, Optional


# ─── User Input ───────────────────────────────────────────────────

@dataclass
class UserMessage(BaseEvent):
    """User sent a message."""
    content: str
    user_id: str
    attachments: list[dict] = field(default_factory=list)


@dataclass
class UserAction(BaseEvent):
    """User performed a UI action (button click, selection, etc.)."""
    action_type: str  # "button_click", "selection", "form_submit"
    action_id: str
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass
class UserTyping(BaseEvent):
    """User is typing (for presence indicators)."""
    user_id: str
    is_typing: bool


# ─── Render Updates ───────────────────────────────────────────────

@dataclass
class RenderUpdate(BaseEvent):
    """UI should update a component."""
    component_id: str
    update_type: str  # "replace", "append", "remove", "patch"
    data: Any


@dataclass
class ArtifactCreated(BaseEvent):
    """A displayable artifact was created (code, chart, file, etc.)."""
    artifact_type: str  # "code", "chart", "image", "file", "table"
    artifact_id: str
    content: Any
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class NotificationRequested(BaseEvent):
    """Show a notification to the user."""
    message: str
    level: str = "info"  # "info", "success", "warning", "error"
    duration_ms: Optional[int] = 5000
    action: Optional[dict] = None  # {label: "Undo", event: UndoEvent(...)}


# ─── Navigation ───────────────────────────────────────────────────

@dataclass
class NavigationRequested(BaseEvent):
    """Request to navigate to a different page/view."""
    target: str  # "/dashboard", "/settings", etc.
    params: dict[str, Any] = field(default_factory=dict)


@dataclass
class ModalRequested(BaseEvent):
    """Request to show a modal dialog."""
    modal_type: str
    title: str
    content: Any
    actions: list[dict] = field(default_factory=list)
```

---

## Module: `auth`

Events for authentication and authorization.

```python
# mozaiksai/events/auth.py

from .core import BaseEvent
from dataclasses import dataclass
from typing import Any, Optional


@dataclass
class AuthStateChanged(BaseEvent):
    """Authentication state changed."""
    is_authenticated: bool
    user_id: Optional[str] = None


@dataclass
class LoginRequested(BaseEvent):
    """User initiated login."""
    provider: str  # "azure-ad", "auth0", "google", etc.
    redirect_uri: Optional[str] = None


@dataclass
class LoginSucceeded(BaseEvent):
    """Login completed successfully."""
    user_id: str
    email: Optional[str]
    display_name: Optional[str]
    provider: str
    scopes: list[str] = field(default_factory=list)


@dataclass
class LoginFailed(BaseEvent):
    """Login failed."""
    provider: str
    error_code: str
    error_message: str


@dataclass
class LogoutRequested(BaseEvent):
    """User initiated logout."""
    reason: str = "user_initiated"


@dataclass
class LogoutCompleted(BaseEvent):
    """Logout completed."""
    pass


@dataclass
class TokenRefreshed(BaseEvent):
    """Access token was refreshed."""
    expires_at: datetime


@dataclass
class TokenExpired(BaseEvent):
    """Access token expired."""
    pass


@dataclass
class PermissionDenied(BaseEvent):
    """User doesn't have permission for an action."""
    action: str
    resource: str
    required_permission: str
```

---

## Module: `backend`

Events for backend/API interactions.

```python
# mozaiksai/events/backend.py

from .core import BaseEvent
from dataclasses import dataclass
from typing import Any, Optional


# ─── API Requests ─────────────────────────────────────────────────

@dataclass
class ApiRequestStarted(BaseEvent):
    """API request initiated."""
    request_id: str
    method: str
    endpoint: str
    payload: Optional[dict] = None


@dataclass
class ApiRequestCompleted(BaseEvent):
    """API request succeeded."""
    request_id: str
    status_code: int
    response: Any
    duration_ms: int


@dataclass
class ApiRequestFailed(BaseEvent):
    """API request failed."""
    request_id: str
    status_code: Optional[int]
    error_type: str
    error_message: str


# ─── Data Mutations ───────────────────────────────────────────────

@dataclass
class EntityCreated(BaseEvent):
    """A data entity was created."""
    entity_type: str  # "user", "app", "workflow", etc.
    entity_id: str
    data: dict[str, Any]


@dataclass
class EntityUpdated(BaseEvent):
    """A data entity was updated."""
    entity_type: str
    entity_id: str
    changes: dict[str, Any]  # {field: {old: x, new: y}}


@dataclass
class EntityDeleted(BaseEvent):
    """A data entity was deleted."""
    entity_type: str
    entity_id: str


# ─── Real-time Sync ───────────────────────────────────────────────

@dataclass
class DataSyncRequested(BaseEvent):
    """Request to sync data from backend."""
    entity_type: str
    query: Optional[dict] = None


@dataclass
class DataSyncCompleted(BaseEvent):
    """Data sync completed."""
    entity_type: str
    items: list[dict]
    total_count: int
```

---

## Module: `workflow`

Events for workflow orchestration.

```python
# mozaiksai/events/workflow.py

from .core import BaseEvent
from dataclasses import dataclass
from typing import Any, Optional


@dataclass
class WorkflowStarted(BaseEvent):
    """Workflow execution began."""
    workflow_name: str
    workflow_id: str
    initial_context: dict[str, Any]
    triggered_by: str  # "user", "schedule", "event", "api"


@dataclass
class WorkflowCompleted(BaseEvent):
    """Workflow finished successfully."""
    workflow_name: str
    workflow_id: str
    result: Any
    duration_ms: int


@dataclass
class WorkflowFailed(BaseEvent):
    """Workflow failed."""
    workflow_name: str
    workflow_id: str
    error_type: str
    error_message: str
    failed_at_agent: Optional[str] = None


@dataclass
class WorkflowPaused(BaseEvent):
    """Workflow paused (waiting for input, HITL, etc.)."""
    workflow_name: str
    workflow_id: str
    reason: str
    waiting_for: str  # "user_input", "approval", "external_event"


@dataclass
class WorkflowResumed(BaseEvent):
    """Workflow resumed after pause."""
    workflow_name: str
    workflow_id: str


@dataclass
class WorkflowCancelled(BaseEvent):
    """Workflow was cancelled."""
    workflow_name: str
    workflow_id: str
    cancelled_by: str
    reason: Optional[str] = None


# ─── Turns & Transitions ──────────────────────────────────────────

@dataclass
class TurnStarted(BaseEvent):
    """A new turn in the workflow began."""
    workflow_id: str
    turn_number: int
    active_agent: str


@dataclass
class TurnCompleted(BaseEvent):
    """A turn in the workflow completed."""
    workflow_id: str
    turn_number: int
    agent: str
    outcome: str  # "message", "tool_call", "handoff", "terminate"
```

---

## Module: `system`

Events for system-level concerns.

```python
# mozaiksai/events/system.py

from .core import BaseEvent
from dataclasses import dataclass
from typing import Any, Optional


# ─── Errors ───────────────────────────────────────────────────────

@dataclass
class ErrorOccurred(BaseEvent):
    """A system error occurred."""
    error_type: str
    error_message: str
    stack_trace: Optional[str] = None
    context: dict[str, Any] = field(default_factory=dict)
    severity: str = "error"  # "warning", "error", "critical"


# ─── Lifecycle ────────────────────────────────────────────────────

@dataclass
class AppStarted(BaseEvent):
    """Application started."""
    app_id: str
    app_name: str
    version: str


@dataclass
class AppShutdown(BaseEvent):
    """Application shutting down."""
    reason: str


@dataclass
class ConnectionEstablished(BaseEvent):
    """WebSocket/transport connection established."""
    connection_id: str
    transport_type: str  # "websocket", "sse", "polling"


@dataclass
class ConnectionLost(BaseEvent):
    """Connection lost."""
    connection_id: str
    reason: str
    will_retry: bool


# ─── Metrics ──────────────────────────────────────────────────────

@dataclass
class MetricRecorded(BaseEvent):
    """A metric was recorded."""
    metric_name: str
    value: float
    unit: str
    tags: dict[str, str] = field(default_factory=dict)


@dataclass
class TokenUsageRecorded(BaseEvent):
    """LLM token usage recorded."""
    model: str
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    agent_name: Optional[str] = None
    workflow_id: Optional[str] = None
```

---

## Stream Implementation

How the stream works under the hood.

```python
# mozaiksai/events/stream.py

from typing import Callable, Any
from collections import defaultdict
from uuid import UUID, uuid4
import asyncio


SubscriptionId = UUID


class EventStream:
    """
    Central event stream that all components publish to and subscribe from.

    Usage:
        stream = EventStream()

        # Subscribe to specific event types
        stream.subscribe(handle_message, event_types=[AgentMessage])

        # Subscribe with filter
        stream.subscribe(
            handle_greeter,
            event_types=[AgentMessage],
            filter=lambda e: e.agent_name == "GreeterAgent"
        )

        # Emit events
        stream.send(AgentMessage(agent_name="GreeterAgent", content="Hello!"))
    """

    def __init__(self):
        self._subscribers: dict[SubscriptionId, tuple[
            Callable,
            list[type] | None,
            Callable | None
        ]] = {}
        self._history: list[BaseEvent] = []
        self._max_history = 1000

    def send(self, event: BaseEvent) -> None:
        """Emit an event to all matching subscribers."""
        self._history.append(event)
        if len(self._history) > self._max_history:
            self._history = self._history[-self._max_history:]

        for sub_id, (handler, event_types, filter_fn) in self._subscribers.items():
            # Type filter
            if event_types and type(event) not in event_types:
                continue

            # Custom filter
            if filter_fn and not filter_fn(event):
                continue

            # Call handler (async-aware)
            if asyncio.iscoroutinefunction(handler):
                asyncio.create_task(handler(event))
            else:
                handler(event)

    def subscribe(
        self,
        handler: Callable[[BaseEvent], Any],
        event_types: list[type[BaseEvent]] | None = None,
        filter: Callable[[BaseEvent], bool] | None = None,
    ) -> SubscriptionId:
        """Subscribe to events."""
        sub_id = uuid4()
        self._subscribers[sub_id] = (handler, event_types, filter)
        return sub_id

    def unsubscribe(self, sub_id: SubscriptionId) -> None:
        """Remove a subscription."""
        self._subscribers.pop(sub_id, None)

    def where(self, event_type: type[BaseEvent]) -> "FilteredStream":
        """Create a filtered view of the stream."""
        return FilteredStream(self, event_type)

    def replay(
        self,
        event_types: list[type[BaseEvent]] | None = None,
        since: datetime | None = None,
    ) -> list[BaseEvent]:
        """Replay historical events."""
        events = self._history
        if event_types:
            events = [e for e in events if type(e) in event_types]
        if since:
            events = [e for e in events if e.timestamp >= since]
        return events


class FilteredStream:
    """A filtered view of an event stream."""

    def __init__(self, parent: EventStream, event_type: type[BaseEvent]):
        self._parent = parent
        self._event_type = event_type

    def subscribe(self, handler: Callable) -> SubscriptionId:
        return self._parent.subscribe(handler, event_types=[self._event_type])

    def send(self, event: BaseEvent) -> None:
        if isinstance(event, self._event_type):
            self._parent.send(event)
```

---

## App Configuration

Apps declare which event modules they use.

```yaml
# platform/config/events.yaml

modules:
  - core      # Always included
  - system    # Always included
  - agent     # Include for AI apps
  - tool      # Include if using tools
  - ui        # Include if has UI
  - auth      # Include if has auth
  - workflow  # Include if using workflows
  # - backend # Omit if no backend

# Custom events for this app
custom_events:
  - name: PaymentProcessed
    module: payments
    fields:
      amount: float
      currency: str
      user_id: str

  - name: SubscriptionChanged
    module: payments
    fields:
      user_id: str
      plan: str
      action: str  # "upgrade", "downgrade", "cancel"
```

---

## Usage Examples

### Example 1: Agent-only app (no UI)

```python
from mozaiksai.events import EventStream
from mozaiksai.events.agent import AgentMessage, AgentHandoff
from mozaiksai.events.tool import ToolCallCompleted

stream = EventStream()

# Log all agent messages
stream.subscribe(
    lambda e: print(f"[{e.agent_name}]: {e.content}"),
    event_types=[AgentMessage]
)

# Track tool usage
stream.subscribe(
    lambda e: metrics.record("tool_call", tool=e.tool_name, duration=e.duration_ms),
    event_types=[ToolCallCompleted]
)
```

### Example 2: Full app with UI

```python
from mozaiksai.events import EventStream
from mozaiksai.events.agent import AgentMessage, AgentThinking
from mozaiksai.events.ui import UserMessage, RenderUpdate
from mozaiksai.events.auth import LoginSucceeded, LogoutCompleted

stream = EventStream()

# UI subscribes to render updates
stream.where(AgentMessage).subscribe(lambda e: ui.append_message(e))
stream.where(AgentThinking).subscribe(lambda e: ui.show_typing_indicator(e.agent_name))

# Auth state syncs to UI
stream.where(LoginSucceeded).subscribe(lambda e: ui.set_user(e))
stream.where(LogoutCompleted).subscribe(lambda e: ui.clear_user())

# User input goes to stream
def on_user_send(text):
    stream.send(UserMessage(content=text, user_id=current_user.id))
```

### Example 3: mozaiks-platform as a mozaiks app

```python
# The platform itself uses the same event system

from mozaiksai.events import EventStream
from mozaiksai.events.auth import LoginSucceeded
from mozaiksai.events.backend import EntityCreated, EntityUpdated

stream = EventStream()

# When user logs in, sync their apps from .NET backend
@stream.where(LoginSucceeded).subscribe
async def sync_user_apps(event: LoginSucceeded):
    apps = await hosting_api.get_user_apps(event.user_id)
    for app in apps:
        stream.send(EntityCreated(entity_type="app", entity_id=app.id, data=app))

# When an app is created in the UI, notify .NET backend
@stream.where(EntityCreated).subscribe
async def notify_backend(event: EntityCreated):
    if event.entity_type == "app" and event.source == "ui":
        await hosting_api.create_app(event.data)
```

---

## Transport Layer

Events flow over different transports depending on context.

```
┌─────────────────────────────────────────────────────────────────┐
│                        EVENT STREAM                              │
└──────────────────────────┬──────────────────────────────────────┘
                           │
          ┌────────────────┼────────────────┐
          │                │                │
          ▼                ▼                ▼
    ┌──────────┐    ┌──────────┐    ┌──────────┐
    │ In-Memory │    │ WebSocket│    │  Redis   │
    │ (single  │    │ (browser │    │ (multi-  │
    │ process) │    │ ↔ server)│    │ instance)│
    └──────────┘    └──────────┘    └──────────┘
```

```python
# Transport adapters

class InMemoryTransport:
    """For single-process apps."""
    pass

class WebSocketTransport:
    """For browser ↔ server communication."""

    async def on_websocket_message(self, ws, data):
        event = deserialize_event(data)
        stream.send(event)

    async def forward_to_client(self, event: BaseEvent):
        if should_forward_to_client(event):
            await ws.send(serialize_event(event))

class RedisTransport:
    """For multi-instance deployments."""

    async def publish(self, event: BaseEvent):
        await redis.publish("events", serialize_event(event))

    async def subscribe_loop(self):
        async for message in redis.subscribe("events"):
            event = deserialize_event(message)
            stream.send(event)
```

---

## Next Steps

1. **Implement core event types** in `mozaiksai/events/`
2. **Create stream implementation** that works with AG2 beta
3. **Build transport adapters** (WebSocket for UI, Redis for scale)
4. **Update orchestration** to emit events instead of direct calls
5. **Update chat-ui** to subscribe to events instead of polling
6. **Migrate MOZ-UI** to use event subscriptions

This foundation enables:
- Any app to use the same primitives
- mozaiks-platform to become a mozaiks app itself
- Easy addition of new event types for custom use cases
- Consistent debugging/monitoring (just watch the event stream)
