# Event System Inventory

**Status:** Informational snapshot  
**Last updated:** 2026-03-12

This file is a current-state inventory of the event-related modules that exist
in this repo.

This file is **not** the normative contract.

Authoritative docs:

- [event-taxonomy.md](../foundations/event-taxonomy.md)
- [event-system-architecture.md](../foundations/event-system-architecture.md)
- [process-and-event-map.md](../foundations/process-and-event-map.md)

---

## Event Surfaces

There are multiple event surfaces in the repo.

| Surface | Purpose | Primary implementation |
|---|---|---|
| Runtime event dispatcher | Routes runtime/domain events to orchestration listeners | `mozaiksai/core/events/unified_event_dispatcher.py` |
| Domain event contract | Engine-agnostic envelope between runtime and adapter | `mozaiksai/core/ports/orchestration.py` |
| Low-level event helpers | payload serialization and handler utilities | `mozaiksai/core/events/event_serialization.py`, `mozaiksai/core/events/auto_tool_handler.py`, `mozaiksai/core/events/handoff_events.py` |
| Workflow orchestration listeners | MFJ and journey consumers of runtime events | `mozaiksai/core/workflow/pack/workflow_pack_coordinator.py`, `mozaiksai/core/workflow/pack/journey_orchestrator.py` |
| Universal routing listener | typed reroute/change handling | `mozaiksai/core/orchestration/universal_orchestrator.py` |
| Transport bridge | forwards runtime events to connected clients | `mozaiksai/core/transport/simple_transport.py`, `shared_app.py` |
| External app backend adapter | generic boundary to backend-owned event systems | `mozaiksai/core/ports/app_backend.py`, `mozaiksai/core/adapters/http_app_backend.py`, `mozaiksai/core/workflow/app_backend_tools.py` |

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

### App backend/business-facing

Business-event families are owned by external app backends and vary by
deployment. They are outside the `mozaiksai` runtime taxonomy and cross the
runtime boundary through `AppBackendPort` and backend-facing workflow tools.

---

## Publishers

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
- backend-facing workflow tools
  - outbound requests and event emission through `AppBackendPort`

---

## Subscribers / Reactors

Representative subscribers in the live repo:

- `WorkflowPackCoordinator`
  - reacts to validated agent output and run-complete events
- `JourneyOrchestrator`
  - reacts to workflow completion to auto-advance global journeys
- `UniversalOrchestrator`
  - reacts to typed universal/change routing inputs
- `SimpleTransport`
  - pushes runtime events to connected clients

---

## Current Gaps

This inventory also highlights current architectural gaps:

1. There are still two event idioms in the repo:
  - typed runtime `DomainEvent` / normalized runtime events
  - backend-specific business event shapes behind `AppBackendPort`
2. Some older docs still describe retired components and old repo paths.
3. Business/app backend events are not yet aligned to the canonical dot-taxonomy.
4. A small event inventory is still useful, but it must track real code, not
   retired architecture notes.

---

## Summary

The current event system is not one giant bus.

It is a set of cooperating layers:

- runtime domain event dispatch in `mozaiksai`
- orchestration listeners consuming runtime events
- transport bridging for user-facing push
- external app backend integration through `AppBackendPort`

That is the live system this repo actually has.


