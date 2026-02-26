# Unified Event System Architecture

**Status:** Target Architecture  
**Date:** 2026-02-04  
**Owner:** mozaiks

---

## Executive Summary

This document defines the **target architecture** for mozaiks' event system after cleanup. It consolidates 5+ overlapping systems into a clean, typed, layered approach.

---

## 1. Architectural Principles

### 1.1 Layer Separation

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                          FRONTEND (Browser)                                  │
│                                                                              │
│   ┌────────────────────────────────────────────────────────────────────┐   │
│   │                      WebSocket Client                               │   │
│   │  Receives: chat.* events + agui.* events (if enabled)              │   │
│   │  Sends: user.input.submit, ui.tool.response, artifact.action       │   │
│   └────────────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────────────┘
                                    │
                         WebSocket Connection
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                           TRANSPORT LAYER                                    │
│                                                                              │
│   ┌────────────────────────────────────────────────────────────────────┐   │
│   │                      SimpleTransport                                │   │
│   │  - Manages WebSocket connections                                    │   │
│   │  - Serializes outbound events                                       │   │
│   │  - Deserializes inbound messages                                    │   │
│   │  - Triggers AG-UI dual emission (if enabled)                        │   │
│   └────────────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                          EVENT DISPATCH LAYER                                │
│                                                                              │
│   ┌─────────────────────────┐   ┌─────────────────────────────────────┐   │
│   │   ChatEventDispatcher   │   │      BusinessEventDispatcher        │   │
│   │                         │   │                                     │   │
│   │   Handles:              │   │   Handles:                          │   │
│   │   • chat.*              │   │   • subscription.*                  │   │
│   │   • agui.* (derived)    │   │   • notification.*                  │   │
│   │                         │   │   • settings.*                      │   │
│   │   Source: AG2 runtime   │   │   • entitlement.*                   │   │
│   │                         │   │   • system.*                        │   │
│   │                         │   │                                     │   │
│   │                         │   │   Source: Dispatchers, system code  │   │
│   └─────────────────────────┘   └─────────────────────────────────────┘   │
│                                                                              │
│   ┌─────────────────────────────────────────────────────────────────────┐  │
│   │                      MeteringEventCollector                          │  │
│   │                                                                      │  │
│   │   Collects:                                                          │  │
│   │   • chat.usage_delta, chat.usage_summary                            │  │
│   │   • artifact.*, storage.*, tool.*, workflow.*, api.*                │  │
│   │                                                                      │  │
│   │   Outputs to: Usage accounting, Platform billing API                 │  │
│   └─────────────────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                           RUNTIME LAYER                                      │
│                                                                              │
│   ┌─────────────────────────────────────────────────────────────────────┐  │
│   │                    AG2 Event Adapter                                 │  │
│   │                                                                      │  │
│   │   • Wraps AG2 0.11 run_iter() / a_run_iter()                        │  │
│   │   • Converts typed AG2 events → chat.* events                       │  │
│   │   • Handles InputRequestEvent → user input flow                      │  │
│   │   • Emits custom Mozaiks events (subscription, artifact, etc.)       │  │
│   └─────────────────────────────────────────────────────────────────────┘  │
│                                                                              │
│   ┌─────────────────────────────────────────────────────────────────────┐  │
│   │                    Declarative Dispatchers                           │  │
│   │                                                                      │  │
│   │   SubscriptionDispatcher  NotificationDispatcher  SettingsDispatcher │  │
│   │          ↓                        ↓                      ↓           │  │
│   │   subscription.yaml        notifications.yaml      settings.yaml    │  │
│   │                                                                      │  │
│   │   Emit: subscription.*, notification.*, settings.* events           │  │
│   └─────────────────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────────────┘
```

### 1.2 Event Flow Rules

1. **Chat events** originate from AG2 runtime → chat.* → frontend (optionally → agui.*)
2. **Business events** originate from dispatchers → BusinessEventDispatcher → handlers
3. **Metering events** originate from runtime → MeteringEventCollector → usage tracking
4. **Events do NOT cross boundaries unnecessarily** - chat events don't become business events

---

## 2. Event Taxonomy (Final)

### 2.1 Chat Events (Frontend-Bound)

These events stream to the frontend via WebSocket.

```yaml
# Namespace: chat.*
# Transport: WebSocket
# Source: AG2 runtime (via EventAdapter)
# Consumer: Frontend shell (packages/frontend/shell/src/pages/ChatPage.js)

chat.text:                    # Full agent message
chat.print:                   # Streaming text chunk
chat.tool_call:               # Tool invocation started
chat.tool_response:           # Tool result returned
chat.input_request:           # Awaiting user input
chat.input_ack:               # User input received
chat.input_timeout:           # User input timed out
chat.select_speaker:          # Multi-agent speaker selected
chat.handoff:                 # Agent handoff
chat.structured_output_ready: # Structured output emitted
chat.run_start:               # Workflow started
chat.run_complete:            # Workflow finished
chat.error:                   # Error occurred
chat.usage_delta:             # Token usage increment (also metered)
chat.usage_summary:           # Session token total (also metered)

# Orchestration sub-namespace
chat.orchestration.run_started:     # Run lifecycle
chat.orchestration.run_completed:
chat.orchestration.run_failed:
chat.orchestration.agent_started:   # Agent lifecycle
chat.orchestration.agent_completed:
```

### 2.2 AG-UI Events (Frontend-Bound, Optional)

Derived from chat.* events. Can be disabled via `MOZAIKS_AGUI_ENABLED=0`.

```yaml
# Namespace: agui.*
# Transport: WebSocket (dual emission)
# Source: AGUIEventAdapter
# Consumer: AG-UI compatible frontends

agui.lifecycle.RunStarted:
agui.lifecycle.RunFinished:
agui.lifecycle.RunError:
agui.lifecycle.StepStarted:
agui.lifecycle.StepFinished:

agui.text.TextMessageStart:
agui.text.TextMessageContent:
agui.text.TextMessageEnd:

agui.tool.ToolCallStart:
agui.tool.ToolCallEnd:
agui.tool.ToolCallResult:

agui.state.StateSnapshot:     # (future)
agui.state.StateDelta:        # (future)
```

### 2.3 Business Events (Backend-Only)

These events are handled entirely within the Python backend.

```yaml
# Namespace: subscription.*
# Transport: In-memory (BusinessEventDispatcher)
# Source: SubscriptionDispatcher, platform sync
# Consumer: NotificationDispatcher, EventRouter

subscription.plan_changed:    # User upgraded/downgraded plan
subscription.limit_warning:   # 80%/90% of limit
subscription.limit_reached:   # 100% of limit
subscription.limit_exceeded:  # Over limit (in grace period)
subscription.renewed:         # Subscription renewed
subscription.canceled:        # User canceled
subscription.trial_started:   # Trial began
subscription.trial_ending:    # Trial ending soon (3/1 days)
subscription.trial_ended:     # Trial expired
subscription.payment_failed:  # Payment issue
subscription.payment_success: # Payment succeeded

# Namespace: settings.*
# Source: SettingsDispatcher, API routes
# Consumer: EventRouter, audit logging

settings.updated:             # User changed settings
settings.reset:               # Settings reset to default
settings.imported:            # Settings imported
settings.exported:            # Settings exported

# Namespace: notification.*
# Source: NotificationDispatcher
# Consumer: Delivery handlers, analytics

notification.sent:            # Notification dispatched
notification.delivered:       # Confirmed delivered
notification.clicked:         # User clicked/opened
notification.dismissed:       # User dismissed
notification.failed:          # Delivery failed

# Namespace: entitlement.*
# Source: Platform sync, SubscriptionDispatcher
# Consumer: Feature gates, audit logging

entitlement.granted:          # Feature access granted
entitlement.revoked:          # Feature access revoked
entitlement.expired:          # Time-limited access ended

# Namespace: system.*
# Source: Runtime health checks, error handlers
# Consumer: Monitoring, alerting

system.error:                 # Runtime error
system.warning:               # Runtime warning
system.health_check:          # Health check result
system.rate_limited:          # Rate limit triggered
```

### 2.4 Metering Events (Backend-Only)

Collected for usage tracking and billing.

```yaml
# Namespace: chat.usage_*
# Source: AG2 runtime
# Consumer: MeteringEventCollector → Platform billing

chat.usage_delta:             # Token usage per LLM call
chat.usage_summary:           # Total tokens for session

# Namespace: artifact.*
# Source: Artifact tools
# Consumer: MeteringEventCollector

artifact.created:             # Artifact generated
artifact.downloaded:          # Artifact downloaded
artifact.exported:            # Artifact exported (PDF, etc.)

# Namespace: storage.*
# Source: Storage tools
# Consumer: MeteringEventCollector

storage.uploaded:             # File uploaded
storage.deleted:              # File deleted

# Namespace: tool.*
# Source: Tool execution
# Consumer: MeteringEventCollector

tool.executed:                # Tool/function called
tool.failed:                  # Tool execution failed

# Namespace: workflow.*
# Source: Orchestration
# Consumer: MeteringEventCollector

workflow.started:             # Workflow session started
workflow.completed:           # Workflow finished successfully
workflow.failed:              # Workflow failed

# Namespace: api.*
# Source: External API calls
# Consumer: MeteringEventCollector

api.request:                  # External API call made
```

---

## 3. Component Specifications

### 3.1 ChatEventDispatcher

**Purpose:** Convert AG2 events to chat.* events and emit to WebSocket.

**Location:** `packages/python/ai-runtime/mozaiks_ai/runtime/events/chat_event_dispatcher.py`

```python
class ChatEventDispatcher:
    """Dispatches AG2 events as chat.* events to WebSocket."""
    
    def __init__(self, transport: SimpleTransport):
        self._transport = transport
        self._agui_adapter = AGUIEventAdapter()
        self._agui_enabled = is_agui_enabled()
    
    async def dispatch(self, event: Dict[str, Any]) -> None:
        """Send chat.* event and optionally agui.* dual emission."""
        # Send native chat.* event
        await self._transport.send(event)
        
        # Send agui.* if enabled
        if self._agui_enabled:
            agui_events = self._agui_adapter.build_agui_events(event, ...)
            for agui_event in agui_events:
                await self._transport.send(agui_event)
```

### 3.2 BusinessEventDispatcher

**Purpose:** Route business events to registered handlers.

**Location:** `packages/python/ai-runtime/mozaiks_ai/runtime/events/business_event_dispatcher.py`

```python
class BusinessEventDispatcher:
    """Central dispatcher for subscription/notification/settings events."""
    
    _instance: Optional["BusinessEventDispatcher"] = None
    
    @classmethod
    def get_instance(cls) -> "BusinessEventDispatcher":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance
    
    def __init__(self):
        self._handlers: Dict[str, List[Callable]] = {}
        self._global_handlers: List[Callable] = []
    
    def register(self, event_pattern: str, handler: Callable) -> None:
        """Register handler for event pattern (supports wildcards)."""
        # "subscription.*" matches all subscription events
        # "subscription.limit_*" matches limit_warning, limit_reached, etc.
        self._handlers.setdefault(event_pattern, []).append(handler)
    
    def register_global(self, handler: Callable) -> None:
        """Register handler that receives ALL business events."""
        self._global_handlers.append(handler)
    
    async def emit(self, event_type: str, payload: Dict[str, Any]) -> None:
        """Emit business event to matching handlers."""
        event = {
            "event_id": uuid.uuid4().hex[:12],
            "event_type": event_type,
            "event_ts": datetime.now(UTC).isoformat(),
            **payload,
        }
        
        # Call matching handlers
        for pattern, handlers in self._handlers.items():
            if self._matches(event_type, pattern):
                for handler in handlers:
                    await self._invoke(handler, event)
        
        # Call global handlers
        for handler in self._global_handlers:
            await self._invoke(handler, event)
    
    def _matches(self, event_type: str, pattern: str) -> bool:
        """Check if event type matches pattern."""
        if pattern.endswith(".*"):
            prefix = pattern[:-2]
            return event_type.startswith(prefix + ".")
        return event_type == pattern
```

### 3.3 MeteringEventCollector

**Purpose:** Collect usage events for billing and analytics.

**Location:** `packages/python/ai-runtime/mozaiks_ai/runtime/events/metering_collector.py`
 
```python
class MeteringEventCollector:
    """Collects metering events for usage tracking."""
    
    def __init__(self):
        self._usage_buffer: List[Dict] = []
        self._flush_interval = 60  # seconds
        self._platform_client = get_platform_usage_client()
    
    async def collect(self, event_type: str, payload: Dict[str, Any]) -> None:
        """Buffer metering event for batch processing."""
        self._usage_buffer.append({
            "event_type": event_type,
            "timestamp": datetime.now(UTC).isoformat(),
            **payload,
        })
        
        if len(self._usage_buffer) >= 100:
            await self._flush()
    
    async def _flush(self) -> None:
        """Send buffered events to platform."""
        if not self._usage_buffer:
            return
        
        batch = self._usage_buffer.copy()
        self._usage_buffer.clear()
        
        try:
            await self._platform_client.report_usage(batch)
        except Exception as e:
            logger.warning(f"Failed to report usage: {e}")
            # Re-queue failed events
            self._usage_buffer = batch + self._usage_buffer
```

### 3.4 AG2EventAdapter (0.11)

**Purpose:** Convert typed AG2 0.11 events to chat.* format.

**Location:** `packages/python/ai-runtime/mozaiks_ai/runtime/events/ag2_event_adapter.py`

```python
from autogen.events.agent_events import (
    TextEvent, ToolCallEvent, ToolResponseEvent,
    InputRequestEvent, TerminationEvent,
)

class AG2EventAdapter:
    """Adapts AG2 0.11 typed events to chat.* events."""
    
    def process(self, event) -> Optional[Dict[str, Any]]:
        """Convert AG2 event to chat.* envelope."""
        
        if isinstance(event, TextEvent):
            return {
                "type": "chat.text",
                "data": {
                    "content": event.content.content,
                    "agent": getattr(event.content, "sender", None),
                },
            }
        
        if isinstance(event, ToolCallEvent):
            calls = event.content.tool_calls or []
            if calls:
                call = calls[0]
                return {
                    "type": "chat.tool_call",
                    "data": {
                        "call_id": call.id,
                        "name": call.function.name,
                        "arguments": call.function.arguments,
                    },
                }
        
        if isinstance(event, ToolResponseEvent):
            return {
                "type": "chat.tool_response",
                "data": {
                    "call_id": event.content.call_id,
                    "result": event.content.result,
                    "status": "success",
                },
            }
        
        if isinstance(event, InputRequestEvent):
            return {
                "type": "chat.input_request",
                "data": {
                    "request_id": str(uuid.uuid4()),
                    "prompt": event.content.prompt,
                },
            }
        
        if isinstance(event, TerminationEvent):
            return {
                "type": "chat.run_complete",
                "data": {
                    "reason": "termination",
                },
            }
        
        return None
```

---

## 4. Integration Points

### 4.1 Declarative Dispatchers → Business Events

```python
# SubscriptionDispatcher emits business events
class SubscriptionDispatcher:
    async def check_usage(self, user_id: str, resource: str) -> CheckResult:
        usage = await self._backend.get_usage(user_id, resource)
        limit = self._config.get_limit(resource)
        percent = (usage / limit) * 100
        
        if percent >= 100:
            await self._business_dispatcher.emit(
                "subscription.limit_reached",
                {"user_id": user_id, "resource": resource, "usage": usage, "limit": limit}
            )
        elif percent >= 90:
            await self._business_dispatcher.emit(
                "subscription.limit_warning",
                {"user_id": user_id, "resource": resource, "percent": 90}
            )
```

### 4.2 Business Events → Notifications

```python
# EventRouter wires business events to notifications
class EventRouter:
    def __init__(self):
        self._business_dispatcher = BusinessEventDispatcher.get_instance()
        self._notification_dispatcher = NotificationDispatcher()
        
        # Register mappings
        self._business_dispatcher.register(
            "subscription.limit_warning",
            self._handle_limit_warning
        )
    
    async def _handle_limit_warning(self, event: Dict) -> None:
        await self._notification_dispatcher.trigger(
            "subscription_limit_warning",
            user_id=event["user_id"],
            context={
                "resource": event["resource"],
                "percent": event["percent"],
            }
        )
```

### 4.3 Chat Events → Metering

```python
# ChatEventDispatcher collects metering events
class ChatEventDispatcher:
    async def dispatch(self, event: Dict[str, Any]) -> None:
        # Send to frontend
        await self._transport.send(event)
        
        # Collect metering events
        if event["type"].startswith("chat.usage_"):
            await self._metering_collector.collect(event["type"], event["data"])
```

---

## 5. Configuration

### 5.1 Environment Variables

```bash
# AG-UI dual emission
MOZAIKS_AGUI_ENABLED=1              # Enable agui.* events (default: 1)

# Metering
MOZAIKS_METERING_ENABLED=1          # Enable metering collection (default: 1)
MOZAIKS_METERING_FLUSH_INTERVAL=60  # Seconds between flushes (default: 60)

# Business events
MOZAIKS_BUSINESS_EVENTS_LOG=1       # Log all business events (default: 0)
```

### 5.2 Workflow YAML

```yaml
# workflows/my-workflow/orchestrator.yaml
events:
  # Which metering events this workflow emits
  emits:
    - chat.usage_delta
    - artifact.created
    - tool.executed
  
  # Business events that trigger notifications
  triggers:
    - event: subscription.limit_warning
      notification: limit_warning_inline
```

---

## 6. Migration Checklist

### 6.1 Files to Create

- [ ] `events/chat_event_dispatcher.py` - Chat event dispatch
- [ ] `events/business_event_dispatcher.py` - Business event dispatch  
- [ ] `events/metering_collector.py` - Metering collection
- [ ] `events/ag2_event_adapter.py` - AG2 0.11 adapter
- [ ] `events/event_router.py` - Business → Notification wiring

### 6.2 Files to Deprecate

- [ ] `mozaiks_infra/event_bus.py` - Replace with BusinessEventDispatcher
- [ ] `bridge/event_bridge.py` - Merge into event_router.py
- [ ] `events/event_serialization.py` - Replace with AG2EventAdapter (partial)

### 6.3 Files to Modify

- [ ] `events/unified_event_dispatcher.py` - Simplify, delegate to specialized dispatchers
- [ ] `orchestration_patterns.py` - Use AG2 0.11 run_iter()
- [ ] `transport/simple_transport.py` - Use ChatEventDispatcher

---

## 7. See Also

- [EVENT_SYSTEM_INVENTORY.md](../events/EVENT_SYSTEM_INVENTORY.md) - Current state
- [AG2_011_MIGRATION_PLAN.md](../events/AG2_011_MIGRATION_PLAN.md) - AG2 upgrade path
- [DECLARATIVE_RUNTIME_SYSTEM.md](../events/DECLARATIVE_RUNTIME_SYSTEM.md) - YAML-based dispatchers
- [LEGACY_CLEANSE_PLAN.md](../events/LEGACY_CLEANSE_PLAN.md) - Deprecation timeline
- [PROCESS_AND_EVENT_MAP.md](PROCESS_AND_EVENT_MAP.md) - Process boundaries, transports, end-to-end traces

