# Workflow Triggers Specification

**Status:** Specification
**Created:** 2026-04-06
**Depends on:** MODULAR_ARCHITECTURE_V2.md, ../foundations/event-contracts.md, ../foundations/event-system.md

This document formalizes how workflows are triggered in the Mozaiks system.

> **Critical**: All orchestration triggers MUST be event-driven. Workflows emit explicit runtime events at checkpoints; the runtime reacts to these events. See [Event System](../foundations/event-system.md) for the complete model.

---

## Overview

Workflows can be triggered through multiple mechanisms:

1. **Manual Triggers** - User explicitly starts a workflow (chat, API call)
2. **Event Triggers** - System events automatically start workflows
3. **Route Triggers** - HTTP requests map to workflow execution
4. **Schedule Triggers** - Time-based workflow execution
5. **Page Triggers** - UI interactions that invoke workflows

The runtime is responsible for resolving and dispatching all triggers.

---

## 1. Trigger Model

### Trigger Definition Schema

```yaml
# workflows/sales_assistant/orchestrator.yaml

triggers:
  # Manual trigger via chat
  - type: chat
    config:
      session_scoped: true

  # Event-driven trigger
  - type: event
    event: module.deals.created
    condition: "payload.value > 10000"

  # HTTP route trigger
  - type: route
    path: /api/analyze-deal
    method: POST

  # Schedule trigger
  - type: schedule
    cron: "0 9 * * *"  # Daily at 9 AM

  # Page action trigger
  - type: action
    action_id: analyze_contact
    source: contacts_page
```

### Trigger Interface

```python
# packages/core/src/mozaiks_core/interfaces/trigger.py

from typing import Protocol, Dict, Any, Optional, Literal
from dataclasses import dataclass
from enum import Enum


class TriggerType(Enum):
    CHAT = "chat"
    EVENT = "event"
    ROUTE = "route"
    SCHEDULE = "schedule"
    ACTION = "action"


@dataclass
class TriggerDefinition:
    """Definition of a workflow trigger."""
    type: TriggerType
    workflow: str           # Workflow to execute
    config: Dict[str, Any]  # Type-specific configuration

    # Event triggers
    event_type: Optional[str] = None
    condition: Optional[str] = None

    # Route triggers
    path: Optional[str] = None
    method: Optional[str] = None

    # Schedule triggers
    cron: Optional[str] = None

    # Action triggers
    action_id: Optional[str] = None
    source: Optional[str] = None


@dataclass
class TriggerContext:
    """Context passed to workflow when triggered."""
    trigger_type: TriggerType
    trigger_id: str

    # Source data
    event: Optional['Event'] = None      # For event triggers
    request: Optional[Dict] = None        # For route triggers
    action: Optional[Dict] = None         # For action triggers
    schedule: Optional[Dict] = None       # For schedule triggers

    # User context (if available)
    user_id: Optional[str] = None
    session_id: Optional[str] = None


class TriggerResolver(Protocol):
    """Resolves triggers and dispatches to workflows."""

    def register_trigger(self, trigger: TriggerDefinition) -> str:
        """Register a trigger. Returns trigger ID."""
        ...

    def unregister_trigger(self, trigger_id: str) -> bool:
        """Unregister a trigger."""
        ...

    async def handle_event(self, event: 'Event') -> list:
        """Handle an event, return list of triggered workflow runs."""
        ...

    async def handle_route(
        self,
        path: str,
        method: str,
        request: Dict,
    ) -> Optional['ExecutionResult']:
        """Handle a route, return workflow result if matched."""
        ...

    async def handle_action(
        self,
        action_id: str,
        source: str,
        payload: Dict,
    ) -> Optional['ExecutionResult']:
        """Handle a UI action, return workflow result if matched."""
        ...
```

---

## 2. Manual Triggers

### Chat Trigger

The primary way users interact with AI workflows is through chat. This is a **pull** model - user initiates.

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                           CHAT TRIGGER FLOW                                  │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  1. User opens chat UI                                                       │
│       │                                                                     │
│       ▼                                                                     │
│  2. WebSocket connection established                                        │
│       - Auth validated                                                      │
│       - Session created or resumed                                          │
│       │                                                                     │
│       ▼                                                                     │
│  3. User sends message                                                      │
│       {"type": "message", "content": "...", "workflow": "assistant"}        │
│       │                                                                     │
│       ▼                                                                     │
│  4. Runtime resolves workflow                                               │
│       - Lookup workflow by name                                             │
│       - Verify user has access                                              │
│       │                                                                     │
│       ▼                                                                     │
│  5. TriggerContext created                                                  │
│       {                                                                     │
│         trigger_type: CHAT,                                                 │
│         trigger_id: "trg_xxx",                                              │
│         user_id: "user_123",                                                │
│         session_id: "sess_456"                                              │
│       }                                                                     │
│       │                                                                     │
│       ▼                                                                     │
│  6. Workflow executed with context                                          │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

### API Trigger

Workflows can be triggered via REST API without chat.

```yaml
# Workflow definition
triggers:
  - type: route
    path: /api/workflows/summarize
    method: POST
```

```bash
# API call
POST /api/workflows/summarize
Authorization: Bearer <token>
Content-Type: application/json

{
  "input": {
    "document_id": "doc_123"
  }
}
```

---

## 3. Event Triggers (Reactive System)

Event triggers allow workflows to respond to system events. This is a **push** model - system initiates.

### Event-to-Workflow Binding

```yaml
# workflows/lead_scorer/orchestrator.yaml

name: lead_scorer
description: Score leads when they are created

triggers:
  - type: event
    event: module.leads.created
    condition: "payload.source == 'website'"
    config:
      debounce_ms: 1000      # Debounce rapid events
      max_concurrent: 5       # Limit concurrent executions
```

### Event Trigger Flow

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                         EVENT TRIGGER FLOW                                   │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  1. Module emits event                                                       │
│       event_bus.publish(Event.create(                                       │
│           event_type="module.leads.created",                                │
│           source="modules",                                                 │
│           payload={"lead_id": "lead_123", "source": "website"}              │
│       ))                                                                    │
│       │                                                                     │
│       ▼                                                                     │
│  2. EventBus routes to TriggerResolver                                      │
│       │                                                                     │
│       ▼                                                                     │
│  3. TriggerResolver evaluates registered triggers                           │
│       ┌───────────────────────────────────────────────────────────────────┐│
│       │ For each trigger with event_type == "module.leads.created":        ││
│       │                                                                    ││
│       │   a) Evaluate condition: payload.source == 'website'               ││
│       │      → True                                                        ││
│       │                                                                    ││
│       │   b) Check debounce: last_trigger + 1000ms < now                   ││
│       │      → True                                                        ││
│       │                                                                    ││
│       │   c) Check concurrency: active_runs < 5                            ││
│       │      → True                                                        ││
│       │                                                                    ││
│       │   d) Trigger matches!                                              ││
│       └───────────────────────────────────────────────────────────────────┘│
│       │                                                                     │
│       ▼                                                                     │
│  4. TriggerContext created from event                                       │
│       {                                                                     │
│         trigger_type: EVENT,                                                │
│         trigger_id: "trg_xxx",                                              │
│         event: <the event>,                                                 │
│         user_id: event.user_id  # May be null for system events            │
│       }                                                                     │
│       │                                                                     │
│       ▼                                                                     │
│  5. Workflow executed asynchronously                                        │
│       - No WebSocket (no user waiting)                                      │
│       - Results stored in database                                          │
│       - Completion event emitted                                            │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

### Condition Expressions

Conditions use a simple expression language:

```yaml
# Simple equality
condition: "payload.status == 'active'"

# Numeric comparison
condition: "payload.value > 10000"

# Multiple conditions (AND)
condition: "payload.source == 'website' and payload.score > 50"

# OR conditions
condition: "payload.priority == 'high' or payload.urgent == true"

# Nested payload access
condition: "payload.customer.tier == 'enterprise'"

# Array membership
condition: "'sales' in payload.tags"
```

### Event Trigger Implementation

```python
# packages/runtime/src/mozaiks_runtime/triggers/event_trigger.py

from mozaiks_core.interfaces import Event, TriggerDefinition, TriggerContext
from mozaiks_core.interfaces import TriggerType
import simpleeval


class EventTriggerHandler:
    """Handles event-driven workflow triggers."""

    def __init__(self, workflow_executor):
        self._executor = workflow_executor
        self._triggers: Dict[str, List[TriggerDefinition]] = {}  # event_type -> triggers
        self._active_runs: Dict[str, int] = {}  # trigger_id -> count
        self._last_trigger: Dict[str, datetime] = {}  # trigger_id -> timestamp

    def register(self, trigger: TriggerDefinition) -> str:
        """Register an event trigger."""
        if trigger.type != TriggerType.EVENT:
            raise ValueError("Not an event trigger")

        trigger_id = f"evt_{uuid.uuid4().hex[:8]}"

        event_type = trigger.event_type
        if event_type not in self._triggers:
            self._triggers[event_type] = []

        self._triggers[event_type].append(trigger)
        return trigger_id

    async def handle_event(self, event: Event) -> List[str]:
        """Handle an event, trigger matching workflows."""
        triggered_runs = []

        # Find matching triggers
        triggers = self._triggers.get(event.type, [])

        # Also check wildcard patterns
        for pattern, pattern_triggers in self._triggers.items():
            if self._matches_pattern(event.type, pattern):
                triggers.extend(pattern_triggers)

        for trigger in triggers:
            if await self._should_trigger(trigger, event):
                run_id = await self._execute_trigger(trigger, event)
                triggered_runs.append(run_id)

        return triggered_runs

    async def _should_trigger(self, trigger: TriggerDefinition, event: Event) -> bool:
        """Check if trigger should fire for this event."""

        # Check condition
        if trigger.condition:
            try:
                result = simpleeval.simple_eval(
                    trigger.condition,
                    names={"payload": event.payload}
                )
                if not result:
                    return False
            except Exception:
                return False

        # Check debounce
        debounce_ms = trigger.config.get("debounce_ms", 0)
        if debounce_ms > 0:
            last = self._last_trigger.get(trigger.trigger_id)
            if last and (datetime.utcnow() - last).total_seconds() * 1000 < debounce_ms:
                return False

        # Check concurrency
        max_concurrent = trigger.config.get("max_concurrent", float("inf"))
        active = self._active_runs.get(trigger.trigger_id, 0)
        if active >= max_concurrent:
            return False

        return True

    async def _execute_trigger(self, trigger: TriggerDefinition, event: Event) -> str:
        """Execute the triggered workflow."""
        context = TriggerContext(
            trigger_type=TriggerType.EVENT,
            trigger_id=trigger.trigger_id,
            event=event,
            user_id=event.user_id,
        )

        # Track active run
        self._active_runs[trigger.trigger_id] = self._active_runs.get(trigger.trigger_id, 0) + 1
        self._last_trigger[trigger.trigger_id] = datetime.utcnow()

        try:
            result = await self._executor.execute(
                ExecutionRequest(
                    executor_type=ExecutorType.AI,
                    target=trigger.workflow,
                    params={"trigger_context": context, "event": event.payload},
                    app_id=event.app_id,
                    user_id=event.user_id,
                )
            )
            return result.run_id
        finally:
            self._active_runs[trigger.trigger_id] -= 1
```

---

## 4. Route Triggers

Route triggers map HTTP endpoints directly to workflow execution.

### Route Trigger Definition

```yaml
# workflows/document_analyzer/orchestrator.yaml

name: document_analyzer

triggers:
  - type: route
    path: /api/analyze
    method: POST
    config:
      timeout_seconds: 120
      auth_required: true
      rate_limit: 10/minute
```

### Route Trigger Flow

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                          ROUTE TRIGGER FLOW                                  │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  1. HTTP Request arrives                                                    │
│       POST /api/analyze                                                     │
│       Authorization: Bearer <token>                                         │
│       {"document_id": "doc_123"}                                            │
│       │                                                                     │
│       ▼                                                                     │
│  2. Router checks registered route triggers                                 │
│       - Path match: /api/analyze                                            │
│       - Method match: POST                                                  │
│       │                                                                     │
│       ▼                                                                     │
│  3. Auth middleware validates token                                         │
│       │                                                                     │
│       ▼                                                                     │
│  4. TriggerResolver.handle_route()                                          │
│       │                                                                     │
│       ▼                                                                     │
│  5. TriggerContext created                                                  │
│       {                                                                     │
│         trigger_type: ROUTE,                                                │
│         trigger_id: "trg_xxx",                                              │
│         request: {                                                          │
│           path: "/api/analyze",                                             │
│           method: "POST",                                                   │
│           body: {"document_id": "doc_123"},                                 │
│           query: {},                                                        │
│           headers: {...}                                                    │
│         },                                                                  │
│         user_id: "user_123"                                                 │
│       }                                                                     │
│       │                                                                     │
│       ▼                                                                     │
│  6. Workflow executed SYNCHRONOUSLY                                         │
│       - Request waits for completion                                        │
│       - Timeout applied                                                     │
│       │                                                                     │
│       ▼                                                                     │
│  7. Response returned                                                       │
│       {                                                                     │
│         "status": "success",                                                │
│         "result": {...}                                                     │
│       }                                                                     │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

### Route vs Module Routes

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                     ROUTE RESOLUTION PRIORITY                                │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  Request: POST /api/contacts                                                 │
│                                                                              │
│  Resolution Order:                                                          │
│                                                                              │
│  1. Explicit custom routes in app/ui/route_manifest.json                    │
│       routes:                                                               │
│         - path: /api/contacts                                               │
│           handler: workflow:contact_enricher    ← If defined, workflow wins │
│                                                                              │
│  2. Workflow route triggers                                                 │
│       triggers:                                                             │
│         - type: route                                                       │
│           path: /api/contacts                   ← If defined, workflow wins │
│                                                                              │
│  3. Module auto-routes                                                      │
│       /api/{module_name}/{action}               ← Default module routing    │
│                                                                              │
│  This allows workflows to intercept module routes for enrichment/processing │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## 5. Action Triggers (UI Integration)

Action triggers connect UI interactions to workflows without requiring a full chat interface.

### Action Trigger Definition

```yaml
# workflows/contact_analyzer/orchestrator.yaml

name: contact_analyzer

triggers:
  - type: action
    action_id: analyze_contact
    source: contacts_page
    config:
      requires_selection: true    # Needs selected items
      confirmation: true          # Show confirmation dialog
```

### Action Trigger in UI

```yaml
# pages/contacts.yaml

name: contacts_page
title: Contacts

actions:
  - id: analyze_contact
    label: "Analyze with AI"
    icon: sparkles
    trigger:
      type: workflow
      workflow: contact_analyzer
    requires_selection: true
    position: toolbar
```

### Action Trigger Flow

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                         ACTION TRIGGER FLOW                                  │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  1. User clicks "Analyze with AI" button                                    │
│       - Selected items: ["contact_123", "contact_456"]                      │
│       │                                                                     │
│       ▼                                                                     │
│  2. UI sends action request                                                 │
│       POST /api/actions/execute                                             │
│       {                                                                     │
│         "action_id": "analyze_contact",                                     │
│         "source": "contacts_page",                                          │
│         "selection": ["contact_123", "contact_456"],                        │
│         "context": {                                                        │
│           "view": "list",                                                   │
│           "filters": {"status": "active"}                                   │
│         }                                                                   │
│       }                                                                     │
│       │                                                                     │
│       ▼                                                                     │
│  3. TriggerResolver.handle_action()                                         │
│       │                                                                     │
│       ▼                                                                     │
│  4. TriggerContext created                                                  │
│       {                                                                     │
│         trigger_type: ACTION,                                               │
│         trigger_id: "trg_xxx",                                              │
│         action: {                                                           │
│           action_id: "analyze_contact",                                     │
│           source: "contacts_page",                                          │
│           selection: [...],                                                 │
│           context: {...}                                                    │
│         },                                                                  │
│         user_id: "user_123"                                                 │
│       }                                                                     │
│       │                                                                     │
│       ▼                                                                     │
│  5. Workflow executed                                                       │
│       - Can be sync (return result)                                         │
│       - Can be async (return job_id)                                        │
│       │                                                                     │
│       ▼                                                                     │
│  6. UI receives response                                                    │
│       - Sync: Display result                                                │
│       - Async: Show progress, poll for completion                           │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## 6. Schedule Triggers

Schedule triggers execute workflows on a time-based schedule.

### Schedule Trigger Definition

```yaml
# workflows/daily_digest/orchestrator.yaml

name: daily_digest

triggers:
  - type: schedule
    cron: "0 9 * * *"       # Daily at 9 AM
    timezone: "America/New_York"
    config:
      skip_if_running: true
      retry_on_failure: 2
```

### Schedule Trigger Flow

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                        SCHEDULE TRIGGER FLOW                                 │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  1. Scheduler ticks (every minute)                                          │
│       │                                                                     │
│       ▼                                                                     │
│  2. Check all schedule triggers                                             │
│       ┌───────────────────────────────────────────────────────────────────┐│
│       │ For each schedule trigger:                                         ││
│       │                                                                    ││
│       │   cron: "0 9 * * *"                                                ││
│       │   current_time: 09:00 (matches!)                                   ││
│       │                                                                    ││
│       │   skip_if_running: true                                            ││
│       │   is_running: false                                                ││
│       │   → Trigger matches!                                               ││
│       └───────────────────────────────────────────────────────────────────┘│
│       │                                                                     │
│       ▼                                                                     │
│  3. TriggerContext created                                                  │
│       {                                                                     │
│         trigger_type: SCHEDULE,                                             │
│         trigger_id: "trg_xxx",                                              │
│         schedule: {                                                         │
│           cron: "0 9 * * *",                                                │
│           scheduled_time: "2026-04-06T09:00:00Z",                           │
│           run_number: 142                                                   │
│         },                                                                  │
│         user_id: null  # System trigger, no user                            │
│       }                                                                     │
│       │                                                                     │
│       ▼                                                                     │
│  4. Workflow executed as SYSTEM user                                        │
│       - app_id from workflow config                                         │
│       - user_id = "__system__"                                              │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## 7. Trigger Resolution in Runtime

### TriggerResolver Implementation

```python
# packages/runtime/src/mozaiks_runtime/triggers/resolver.py

class TriggerResolver:
    """Central trigger resolution and dispatch."""

    def __init__(
        self,
        workflow_executor: WorkflowExecutor,
        event_bus: EventBus,
    ):
        self._executor = workflow_executor
        self._event_bus = event_bus

        self._event_handler = EventTriggerHandler(workflow_executor)
        self._route_handler = RouteTriggerHandler(workflow_executor)
        self._action_handler = ActionTriggerHandler(workflow_executor)
        self._schedule_handler = ScheduleTriggerHandler(workflow_executor)

        # Subscribe to all events for trigger evaluation
        self._event_bus.subscribe("*", self._on_event)

    def register_workflow_triggers(self, workflow_name: str, triggers: List[TriggerDefinition]):
        """Register all triggers for a workflow."""
        for trigger in triggers:
            trigger.workflow = workflow_name

            if trigger.type == TriggerType.EVENT:
                self._event_handler.register(trigger)
            elif trigger.type == TriggerType.ROUTE:
                self._route_handler.register(trigger)
            elif trigger.type == TriggerType.ACTION:
                self._action_handler.register(trigger)
            elif trigger.type == TriggerType.SCHEDULE:
                self._schedule_handler.register(trigger)

    async def _on_event(self, event: Event):
        """Handle incoming events."""
        await self._event_handler.handle_event(event)

    async def handle_route(self, path: str, method: str, request: Dict) -> Optional[ExecutionResult]:
        """Try to match a route trigger."""
        return await self._route_handler.handle_route(path, method, request)

    async def handle_action(self, action_id: str, source: str, payload: Dict) -> Optional[ExecutionResult]:
        """Handle a UI action."""
        return await self._action_handler.handle_action(action_id, source, payload)
```

### Integration with Router

```python
# packages/runtime/src/mozaiks_runtime/router/dispatcher.py

class RequestDispatcher:
    """Routes requests to appropriate handlers."""

    def __init__(
        self,
        app_definition: AppDefinition,
        executor_registry: ExecutorRegistry,
        trigger_resolver: TriggerResolver,
    ):
        self._app_def = app_definition
        self._executors = executor_registry
        self._triggers = trigger_resolver

    async def dispatch(self, request: Request, context: RequestContext) -> Response:
        """Dispatch a request."""
        path = request.url.path
        method = request.method

        # 1. Check workflow route triggers first
        trigger_result = await self._triggers.handle_route(path, method, {
            "body": await request.json() if method in ["POST", "PUT"] else {},
            "query": dict(request.query_params),
            "headers": dict(request.headers),
        })

        if trigger_result:
            return JSONResponse(trigger_result.data)

        # 2. Check explicit custom routes in app/ui/route_manifest.json
        route = self._match_explicit_route(path, method)
        if route:
            return await self._dispatch_route(route, request, context)

        # 3. Fall back to module auto-routes
        if path.startswith("/api/") and self._app_def.capabilities.modules:
            return await self._dispatch_module_route(path, method, request, context)

        raise HTTPException(404, "Not found")
```

---

## 8. Trigger Events

All trigger executions emit events for observability.

### Trigger Execution Events

```yaml
# Event: Trigger Started
type: "Orchestration.TriggerStarted"
payload:
  trigger_id: "trg_xxx"
  trigger_type: "event"
  workflow: "lead_scorer"
  run_id: "run_123"
  source_event_id: "evt_456"  # If event trigger

# Event: Trigger Completed
type: "Orchestration.TriggerCompleted"
payload:
  trigger_id: "trg_xxx"
  trigger_type: "event"
  workflow: "lead_scorer"
  run_id: "run_123"
  duration_ms: 4500
  status: "success"

# Event: Trigger Failed
type: "Orchestration.TriggerFailed"
payload:
  trigger_id: "trg_xxx"
  trigger_type: "event"
  workflow: "lead_scorer"
  run_id: "run_123"
  error: "Timeout exceeded"
  retryable: true
```

---

## 9. Trigger Configuration in Workflow Bundles

### Complete Example

```yaml
# app/workflows/lead_scorer/orchestrator.yaml

workflow_name: lead_scorer

triggers:
  - type: event
    event: domain.deals.created
    condition: "payload.value > 10000"

---
# app/workflows/daily_summary/orchestrator.yaml

workflow_name: daily_summary

triggers:
  - type: schedule
    cron: "0 17 * * 1-5"

---
# app/workflows/contact_enricher/orchestrator.yaml

workflow_name: contact_enricher

triggers:
  - type: action
    action_id: enrich_contacts
    source: contacts_page
  - type: route
    path: /api/enrich
    method: POST
```

---

## 10. Runtime Execution Events (MFJ Orchestration)

> **Critical**: This section describes events that trigger orchestration decisions. These are NOT the same as domain events. See [Event System](../foundations/event-system.md) for complete details.

### Runtime Events for MFJ

MFJ (Mid-Flight Journey) orchestration is triggered by explicit runtime events, NOT by discovering structured outputs in transcripts.

```yaml
# Runtime events that trigger orchestration decisions

runtime.decomposition_planned:
  description: "Decomposition agent has planned task breakdown"
  triggers: "MFJ fan-out"
  emitted_by: "DecompositionAgent (explicit emit, not inferred)"
  payload:
    plan_id: string
    tasks: array

runtime.fan_out_requested:
  description: "Parallel task execution should begin"
  triggers: "Start parallel child tasks"
  payload:
    parent_id: string
    child_tasks: array

runtime.fan_in_ready:
  description: "All parallel tasks completed"
  triggers: "Resume parent workflow"
  payload:
    parent_id: string
    results: array

runtime.build_plan_created:
  description: "App build plan created"
  triggers: "Build pipeline start"
  payload:
    plan_id: string
    spec: object
```

### Critical Rule: Events, Not Outputs

```
┌─────────────────────────────────────────────────────────────────────────────┐
│               MFJ TRIGGERING: EVENTS, NOT OUTPUTS                            │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  ❌ WRONG:                                                                   │
│  DecompositionAgent → structured output → runtime parses → MFJ triggered    │
│                                                                              │
│  ✅ CORRECT:                                                                 │
│  DecompositionAgent → emits runtime.decomposition_planned → MFJ triggered   │
│                                                                              │
│  The difference:                                                            │
│  • WRONG relies on parsing outputs after the fact                           │
│  • CORRECT reacts to explicit events in real-time                           │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## 11. Revision Events (Control-Plane Triggers)

Revision requests are routed via control-plane events, not inferred from user messages.

### Revision Event Types

```yaml
# Control-plane events for revision routing

app.patch_requested:
  description: "Minor fix that doesn't change architecture"
  routing: "targeted_update (single file)"
  examples:
    - "Fix typo in contact form label"
    - "Change button color"
    - "Fix validation error message"

app.design_change_requested:
  description: "Visual, brand, layout, or UI-schema change that keeps the same product concept"
  routing: "design_refinement_or_schema_rebuild"
  examples:
    - "Switch the app to a premium dark theme"
    - "Rework the dashboard layout"
    - "Change navigation to a sidebar"

app.feature_change_requested:
  description: "Add or modify a feature within existing architecture"
  routing: "partial_mfj_rebuild (scoped fan-out)"
  examples:
    - "Add email field to contacts"
    - "Add export button to table"
    - "Add new page for reports"

app.core_change_requested:
  description: "Fundamental change that requires re-planning"
  routing: "restart_value_engine (full rebuild)"
  examples:
    - "Change from CRM to project management"
    - "Add multi-tenancy support"
    - "Switch data model entirely"
```

### Revision Routing Flow

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                       REVISION EVENT ROUTING                                 │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  User: "Add email field to contacts"                                        │
│        │                                                                     │
│        ▼                                                                     │
│  Classifier Agent                                                            │
│        │                                                                     │
│        └── emit: app.feature_change_requested                                │
│                  { scope: "contacts_module", change: "add_email_field" }     │
│                                                                              │
│        ▼                                                                     │
│  Runtime Router (reacts to event)                                            │
│        │                                                                     │
│        ├── app.patch_requested ──────► Targeted Update                       │
│        │                                                                     │
│        ├── app.design_change_requested ─────► Design / Schema Re-entry       │
│        │                                                                     │
│        ├── app.feature_change_requested ──────► Scoped MFJ                   │
│        │         │                                                           │
│        │         └── emit: runtime.decomposition_planned (scoped)            │
│        │                                                                     │
│        └── app.core_change_requested ──────► Restart ValueEngine            │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

See [Refinement Control Plane](REFINEMENT_CONTROL_PLANE_SPEC.md) for the
authoritative re-entry and persistence contract behind these events.

---

## Summary

| Trigger Type | Initiation | Execution | Use Case |
|--------------|------------|-----------|----------|
| **Chat** | User message | Streaming | Interactive AI assistant |
| **Event** | System event | Async | Reactive automation |
| **Route** | HTTP request | Sync | API endpoints |
| **Action** | UI button | Sync/Async | UI-triggered AI |
| **Schedule** | Cron timer | Async | Scheduled tasks |

### Key Principles

1. **Workflows are triggered, not called directly** - Always go through the trigger system
2. **Runtime resolves triggers** - Centralized resolution logic
3. **Events are facts** - They describe what happened, triggers decide what to do
4. **Actions bridge UI and AI** - Structured way to invoke AI from pages
5. **All triggers emit events** - Full observability
6. **Event-first orchestration** - MFJ triggered by events, not output discovery
7. **Runtime events separate from domain events** - Different layers, different purposes
8. **Revision events route rebuilds** - Control-plane events determine rebuild scope
