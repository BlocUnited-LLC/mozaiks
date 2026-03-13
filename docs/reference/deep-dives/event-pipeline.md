# Event Pipeline

This is a current-state note on how events move through Mozaiks.

## Three Event Layers

### 1. AG2 runtime stream events

These are turn-level execution events coming out of AG2-backed workflow runs.

### 2. Runtime typed events

These are normalized Mozaiks runtime facts and `DomainEvent` envelopes used for durable orchestration and UI-facing state.

### 3. Platform and business events

These are substrate-level events for modules, notifications, subscriptions, settings, and other platform concerns.

## Current Anchors

- `mozaiksai/core/events/unified_event_dispatcher.py`
- `mozaikscore/core/event_bus.py`
- `mozaikscore/core/websocket_event_bridge.py`

## Related Docs

- [Event Taxonomy](../../architecture/foundations/event-taxonomy.md)
- [Event System Architecture](../../architecture/foundations/event-system-architecture.md)
- [Event Architecture Notes](../../architecture/events/overview.md)
