# Event Pipeline

This is a current-state note on how events move through Mozaiks.

The current architecture is backend-agnostic: app events come from an external
app backend and enter the runtime through the configured ingress boundary.

## Three Event Layers

### 1. AG2 runtime stream events

These are turn-level execution events coming out of AG2-backed workflow runs.

### 2. Runtime typed events

These are normalized Mozaiks runtime facts and `DomainEvent` envelopes used for durable orchestration and UI-facing state.

### 3. Platform and business events

These are app-backend and platform-level events for modules, notifications, subscriptions, settings, and other shared platform concerns.

## Current Anchors

- `mozaiksai/core/events/unified_event_dispatcher.py`
- `mozaiksai/core/ports/app_backend.py`
- `mozaiksai/core/workflow/app_backend_tools.py`

## Related Docs

- [Event Taxonomy](../../architecture/foundations/event-taxonomy.md)
- [Event System Architecture](../../architecture/foundations/event-system-architecture.md)
- [Event Architecture Notes](../../architecture/events/overview.md)
