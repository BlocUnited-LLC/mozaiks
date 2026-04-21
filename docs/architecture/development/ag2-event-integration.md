# AG2 Event Integration

This document describes how mozaiksai integrates with AG2's event system and the forward-compatibility path to AG2 beta streams.

## Current Architecture

mozaiksai uses AG2's groupchat event system with custom events:

```text
                    AG2 GroupChat
                         │
                         ▼
              a_run_group_chat_iter()
                         │
                         ▼
    ┌────────────────────┴────────────────────┐
    │           Event Stream                   │
    │  (TextEvent, ToolCallEvent, etc.)        │
    │  + Mozaiksai Custom Events               │
    └────────────────────┬────────────────────┘
                         │
                         ▼
              EventStreamProcessor
                         │
              ┌──────────┴──────────┐
              ▼                     ▼
        StandardHandlers      MozaiksaiEventHandler
              │                     │
              ▼                     ▼
         WebSocket              UnifiedEventDispatcher
         Transport                    │
                               ┌──────┴──────┐
                               ▼             ▼
                          Listeners     Business Logic
```

## Key Components

### 1. Custom Events (`mozaiksai/core/events/ag2_events.py`)

AG2-native events using `@wrap_event`:

```python
from autogen.events.base_event import BaseEvent, wrap_event
from autogen.io.base import IOStream

@wrap_event
class StructuredOutputEvent(BaseEvent):
    agent_name: str
    chat_id: str
    output_type: str
    output_data: Dict[str, Any]
    validation_passed: bool = True

# Emit via IOStream
IOStream.get_default().send(StructuredOutputEvent(...))
```

**Event Categories:**

| Category | Events | Purpose |
|----------|--------|---------|
| Control | `WorkflowTriggeredEvent`, `HandoffRequestedEvent`, `PlanCreatedEvent` | AI runtime decisions |
| Runtime | `AgentThinkingEvent`, `StructuredOutputEvent`, `UIToolRequestedEvent` | Live execution state |
| Journey | `JourneyStartedEvent`, `JourneyCompletedEvent` | Child workflow orchestration |

### 2. Event Bridge (`mozaiksai/core/events/ag2_event_bridge.py`)

Converts AG2 events to UnifiedEventDispatcher format:

```python
from mozaiksai.core.events.ag2_event_bridge import AG2EventBridge

bridge = AG2EventBridge(dispatcher)

for event in a_run_group_chat_iter(...):
    if bridge.can_handle(event):
        await bridge.handle(event, ctx)
```

### 3. Stream Handler (`mozaiksai/core/workflow/stream/handlers/mozaiks_event_handler.py`)

Registered in EventStreamProcessor at priority 40:

```python
# In EventStreamProcessor._create_default_registry()
registry.register(MozaiksaiEventHandler())  # Priority 40
```

## Forward Compatibility with AG2 Beta

AG2 beta introduces a new streaming model that aligns well with our architecture:

### Current (GroupChat)

```python
# Current pattern
from autogen.agentchat import a_run_group_chat_iter

for event in a_run_group_chat_iter(pattern=pattern, messages=messages):
    # Handle via registry dispatch
    await processor.registry.dispatch(event, ctx, state)
```

### Future (Beta Streams)

```python
# Beta pattern (when migrating)
from autogen.beta import Agent, MemoryStream

stream = MemoryStream()

# Subscribe to custom events
stream.subscribe(
    handler.handle,
    condition=lambda e: isinstance(e, tuple(ALL_MOZAIKSAI_EVENTS))
)

# Run agent
await agent.ask(message, stream=stream)
```

### Migration Path

1. **Event definitions stay the same** - The `@wrap_event` events work in both systems
2. **Bridge logic stays the same** - `AG2EventBridge` converts events to dispatcher format
3. **Handler logic stays the same** - Only the subscription mechanism changes
4. **UnifiedEventDispatcher stays the same** - It's transport-agnostic

**Key changes when migrating:**

| Component | Current | Beta |
|-----------|---------|------|
| Event loop | `a_run_group_chat_iter()` | `stream.subscribe()` |
| Event emission | `IOStream.get_default().send()` | `context.send()` |
| Handler registration | `registry.register()` | `stream.subscribe()` |
| Event types | Same | Same |

## Usage Examples

### Emitting Custom Events (from tools)

```python
from mozaiksai.core.events import emit_structured_output

# Inside a tool function
async def my_analysis_tool(data: str) -> str:
    result = analyze(data)

    # Emit structured output event
    emit_structured_output(
        agent_name="AnalysisAgent",
        chat_id=context.get("chat_id"),
        output_type="AnalysisResult",
        output_data=result,
    )

    return json.dumps(result)
```

### Subscribing to Events (in business logic)

```python
from mozaiksai.core.events import get_event_dispatcher

dispatcher = get_event_dispatcher()

# Subscribe to structured outputs
dispatcher.register_handler(
    "chat.agent_output_validated",
    my_handler_function
)

async def my_handler_function(payload: Dict[str, Any]):
    output_type = payload.get("output_type")
    data = payload.get("structured_data")
    # Process structured output...
```

### Using in Workflow Tools

```python
from mozaiksai.core.events.ag2_events import emit_ui_tool_requested

def request_user_input_tool(
    input_type: str,
    prompt: str,
    context_variables: ContextVariables
) -> str:
    """Request input from user via UI component."""

    emit_ui_tool_requested(
        ui_tool_id=input_type,
        agent_name="InputAgent",
        chat_id=context_variables.get("chat_id"),
        workflow_name=context_variables.get("workflow_name"),
        payload={"prompt": prompt}
    )

    return "Waiting for user input..."
```

## Event Type Registry

For `yield_on` parameter in `a_run_group_chat_iter()`:

```python
from mozaiksai.core.events.ag2_event_bridge import get_yield_on_events

# Combines standard AG2 events + mozaiksai custom events
yield_on = get_yield_on_events()

for event in a_run_group_chat_iter(
    pattern=pattern,
    messages=messages,
    yield_on=yield_on,  # TextEvent, ToolCallEvent, + custom events
):
    ...
```

## Testing Custom Events

```python
from mozaiksai.core.events.ag2_events import (
    StructuredOutputEvent,
    emit_ag2_event,
)

def test_structured_output_event():
    event = StructuredOutputEvent(
        agent_name="TestAgent",
        chat_id="test-123",
        output_type="TestOutput",
        output_data={"key": "value"},
    )

    # Check event was created correctly
    assert event.agent_name == "TestAgent"

    # Test emission (requires IOStream context)
    success = emit_ag2_event(event)
    # Returns False if IOStream not available (expected in unit tests)
```

## Key Files

| File | Purpose |
|------|---------|
| `mozaiksai/core/events/ag2_events.py` | Custom event definitions |
| `mozaiksai/core/events/ag2_event_bridge.py` | Bridge to UnifiedEventDispatcher |
| `mozaiksai/core/workflow/stream/handlers/mozaiks_event_handler.py` | Stream handler |
| `mozaiksai/core/events/unified_event_dispatcher.py` | Central event dispatcher |
| `mozaiksai/core/workflow/stream/processor.py` | Event stream processor |
