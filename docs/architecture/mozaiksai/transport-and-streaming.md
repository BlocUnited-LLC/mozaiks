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

## Execution Outcomes

Background execution emits successful `runtime.process_completed` events and marks
the workflow completed only when the operation succeeds with an explicit completed
run status. A rejected start can report an earlier run's terminal status; its
event instead reports failure and retains the rejection's error code and context.
Accepting input into an existing session without an execution outcome does not
emit completion.

## Related Docs

- [Workflow Architecture](../../architecture/workflows/workflow-architecture.md)
- [UI Surface and Layout Architecture](../../architecture/app/ui-surface-and-layout-architecture.md)
