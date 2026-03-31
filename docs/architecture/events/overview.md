# Event Architecture Notes

**Status:** Informational only  
**Last updated:** 2026-03-12

This directory contains supplemental event-architecture notes and inventories.

These files are not the authoritative contract.

Authoritative event architecture lives in:

- [../foundations/event-taxonomy.md](../foundations/event-taxonomy.md)
- [../foundations/event-system-architecture.md](../foundations/event-system-architecture.md)
- [../foundations/process-and-event-map.md](../foundations/process-and-event-map.md)

Use this directory for:

- current-state inventories
- migration context
- explanatory notes that should not be treated as runtime contract

Current live implementation anchors:

- runtime event dispatch: `mozaiksai/core/events/unified_event_dispatcher.py`
- runtime event envelope: `mozaiksai/core/ports/orchestration.py`
- transport push bridge: `mozaiksai/core/transport/simple_transport.py`
- app backend integration boundary: `mozaiksai/core/ports/app_backend.py`


