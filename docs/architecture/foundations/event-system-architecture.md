# Event System Architecture

**Status:** Core architecture reference  
**Last updated:** 2026-03-12

---

## Purpose

This document defines the current target architecture for event handling in the
unified `mozaiks` repo.

It is the reference for:

- event channels
- event ownership
- dispatch responsibilities
- the boundary between runtime stream events and substrate/business events

---

## Design Principles

1. Events are immutable facts.
2. Runtime stream events and substrate business events are different channels.
3. Event ownership follows repo boundaries.
4. Typed runtime events should not depend on AG2 types.
5. Event docs must point to live code, not retired paths.

---

## The Three Active Event Channels

### Channel 1: Runtime stream channel

Purpose:

- workflow/run lifecycle
- chat/runtime/UI-tool stream delivery
- orchestration reactions such as MFJ and journey progression

Primary code paths:

- `mozaiksai/core/ports/orchestration.py`
- `mozaiksai/core/events/unified_event_dispatcher.py`
- `mozaiksai/core/transport/simple_transport.py`
- `shared_app.py`

Typical event families:

- `process.*`
- `task.*`
- `chat.*`
- `artifact.*`
- `ui.tool.*`
- `transport.*`
- `runtime.*`

### Channel 2: Substrate/business event bus

Purpose:

- notifications
- settings
- subscriptions
- module execution and substrate-side user events

Primary code paths:

- `mozaikscore/core/event_bus.py`
- `mozaikscore/core/notifications_manager.py`
- `mozaikscore/core/settings_manager.py`
- `mozaikscore/core/subscription_manager.py`
- `mozaikscore/core/module_manager.py`

Typical event names today:

- `notification_created`
- `settings_updated`
- `subscription_updated`
- `module_executed`
- `theme_changed`

These are not yet aligned to the canonical dot-taxonomy.
That is a known gap, not a hidden feature.

### Channel 3: WebSocket push bridge

Purpose:

- forward substrate-side events to connected frontend clients

Primary code path:

- `mozaikscore/core/websocket_event_bridge.py`

This is not the same thing as the runtime workflow stream.

---

## Core Components

| Component | Responsibility | Path |
|---|---|---|
| `DomainEvent` | engine-agnostic runtime envelope | `mozaiksai/core/ports/orchestration.py` |
| `AG2OrchestrationAdapter` | engine execution + runtime event production | `mozaiksai/core/adapters/ag2_orchestration.py` |
| `UnifiedEventDispatcher` | runtime event routing and orchestration listeners | `mozaiksai/core/events/unified_event_dispatcher.py` |
| `WorkflowPackCoordinator` | MFJ listener/consumer | `mozaiksai/core/workflow/pack/workflow_pack_coordinator.py` |
| `JourneyOrchestrator` | global journey completion listener | `mozaiksai/core/workflow/pack/journey_orchestrator.py` |
| `UniversalOrchestrator` | typed reroute/change routing | `mozaiksai/core/orchestration/universal_orchestrator.py` |
| `EventBus` | substrate in-process pub/sub | `mozaikscore/core/event_bus.py` |
| `websocket_event_bridge` | substrate event -> websocket push | `mozaikscore/core/websocket_event_bridge.py` |

---

## Event Ownership

### `mozaiksai`

Owns:

- workflow runtime events
- orchestration listeners
- engine-neutral envelopes
- UI tool stream semantics
- workflow completion / fan-in / reroute triggers

### `mozaikscore`

Owns:

- module/settings/notifications/subscription substrate events
- user-targeted/broadcast push for those substrate events

### Frontend

Consumes:

- workflow/runtime stream events via the AI transport
- substrate push events via the websocket bridge

These should remain conceptually separate even when they both end up in the UI.

---

## Flow A: Runtime Workflow Events

1. `shared_app.py` or transport starts/resumes a workflow.
2. The AG2 adapter emits normalized runtime/domain events.
3. `UnifiedEventDispatcher` routes those events.
4. `WorkflowPackCoordinator`, `JourneyOrchestrator`, and other listeners react.
5. `SimpleTransport` sends replay-safe envelopes to the frontend.

---

## Flow B: Substrate Business Events

1. mozaikscore manager emits an event through `event_bus`.
2. In-process subscribers react.
3. `websocket_event_bridge` optionally forwards user-targeted/broadcast events
   to connected clients.

---

## Guardrails

1. Do not collapse all events into one giant bus.
2. Do not pretend the substrate event bus already matches the canonical runtime
   taxonomy.
3. Do not route business/commercial behavior through AG2-oriented runtime
   streams.
4. Do not document retired components as if they still exist.

---

## Cross References

- [event-taxonomy.md](event-taxonomy.md)
- [process-and-event-map.md](process-and-event-map.md)
- [workflow-architecture.md](workflow-architecture.md)

