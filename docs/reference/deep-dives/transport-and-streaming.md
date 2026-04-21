# Transport And Streaming

This note summarizes the current transport model in Mozaiks.

## Current Runtime Shape

The active browser-facing transport is WebSocket-based and centered on:

- `mozaiksai/core/transport/simple_transport.py`

Supporting modules include:

- WebSocket protocol and buffering helpers
- workflow bridge helpers
- UI tool response handling
- input request handling

## What The Transport Owns

- browser session connectivity
- event delivery
- UI tool round-trips
- reconnect and buffering behavior
- chat/workflow session bridging

## What The Transport Does Not Own

- app-specific business logic
- workflow decomposition logic
- product-specific orchestration semantics

## Related Docs

- [Workflow Architecture](../../architecture/foundations/workflow-architecture.md)
- [UI Surface and Layout Architecture](../../architecture/foundations/ui-surface-and-layout-architecture.md)
