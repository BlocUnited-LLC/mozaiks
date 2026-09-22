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

An accepted execution that reaches a terminal AG2 outcome sends one
`chat.run_complete` envelope, and `send_event_to_ui` dispatches
`runtime.process_completed` from it. That is the single source of the event for
accepted runs: background execution does not emit it again. A second dispatch
advances the journey twice, and the duplicate start of the next step is then
refused by its chat execution lease.

A start rejected before execution — a busy or unavailable chat lease, a lost
lease, a terminal session — sends no envelope, so background execution emits the
event itself. Such a rejection can report an earlier run's terminal status, so
that event reports failure and retains the rejection's error code and context.
The workflow is marked completed only when the operation succeeds with an
explicit completed run status. Accepting input into an existing session without
an execution outcome does not emit completion.

Ordering caveat: a run announces its outcome before its terminal status is
persisted, while a journey advance reads that persisted status. The two are not
synchronized today.

## Related Docs

- [Workflow Architecture](../../architecture/workflows/workflow-architecture.md)
- [UI Surface and Layout Architecture](../../architecture/app/ui-surface-and-layout-architecture.md)
