# Event System Inventory

**Status:** Informational snapshot  
**Last updated:** 2026-03-12

This file is a current-state inventory of the event-related modules that exist
in this repo today.

This file is **not** the normative contract.

Authoritative docs:

- [event-taxonomy.md](../foundations/event-taxonomy.md)
- [event-system-architecture.md](../foundations/event-system-architecture.md)
- [process-and-event-map.md](../foundations/process-and-event-map.md)

---

## Current Event Surfaces

There are multiple event surfaces in the current repo.

| Surface | Purpose | Primary implementation |
|---|---|---|
| Runtime event dispatcher | Routes runtime/domain events to orchestration listeners | `mozaiksai/core/events/unified_event_dispatcher.py` |
| Domain event contract | Engine-agnostic envelope between runtime and adapter | `mozaiksai/core/ports/orchestration.py` |
| Low-level event helpers | payload building and serialization | `mozaiksai/core/events/event_payload_builder.py`, `mozaiksai/core/events/event_serialization.py` |
| Workflow orchestration listeners | MFJ and journey consumers of runtime events | `mozaiksai/core/workflow/pack/workflow_pack_coordinator.py`, `mozaiksai/core/workflow/pack/journey_orchestrator.py` |
| Universal routing listener | typed reroute/change handling | `mozaiksai/core/orchestration/universal_orchestrator.py` |
| Substrate event bus | in-process pub/sub for mozaikscore substrate events | `mozaikscore/core/event_bus.py` |
| WebSocket push bridge | forwards substrate events to connected users | `mozaikscore/core/websocket_event_bridge.py` |

---

## Runtime Event Families In Active Use

### Stream/runtime-facing

- `chat.*`
- `artifact.*`
- `ui.tool.*`
- `transport.*`
- `runtime.*`
- `process.*`
- `task.*`

These are associated with workflow execution, streaming, replay, UI tool
interaction, and orchestration control.

### Substrate/business-facing

- `subscription_*`
- `notification_*`
- `settings_*`
- `module_*`
- `theme_changed`
- `profile_updated`
- `system_announcement`

These are mozaikscore in-process event-bus events today. They do not currently
follow the same dot-taxonomy contract as `DomainEvent`.

That split is real and should be documented rather than hidden.

---

## Current Publishers

Representative publishers in the live repo:

- `shared_app.py`
  - chat/session/workflow API lifecycle
  - websocket workflow connections
- `SimpleTransport`
  - UI event delivery and runtime stream envelopes
- `AG2OrchestrationAdapter`
  - engine-facing run/resume summaries and streamed events through the runtime
- `UnifiedEventDispatcher`
  - runtime domain event routing
- `module_manager`, `notifications_manager`, `settings_manager`, `subscription_manager`
  - substrate/business events via `event_bus`

---

## Current Subscribers / Reactors

Representative subscribers in the live repo:

- `WorkflowPackCoordinator`
  - reacts to structured output and run-complete events
- `JourneyOrchestrator`
  - reacts to workflow completion to auto-advance global journeys
- `UniversalOrchestrator`
  - reacts to typed universal/change routing inputs
- `websocket_event_bridge`
  - reacts to substrate events and pushes them to the frontend

---

## Current Gaps

This inventory also highlights current architectural gaps:

1. There are still two event idioms in the repo:
   - typed runtime `DomainEvent` / normalized runtime events
   - mozaikscore string-key event bus events
2. Some older docs still describe retired components and old repo paths.
3. Business/substrate events are not yet aligned to the canonical dot-taxonomy.
4. A small event inventory is still useful, but it must track real code, not
   legacy architecture.

---

## Summary

The current event system is not one giant bus.

It is a set of cooperating layers:

- runtime domain event dispatch in `mozaiksai`
- orchestration listeners consuming runtime events
- substrate event bus dispatch in `mozaikscore`
- websocket bridging for user-facing push

That is the live system this repo actually has today.

