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

A failed run's envelope has `status: failed`, `run_completed: false`, the AG2
`close_reason` that ended the channel when one did (`workflow_failed`,
`max_turns`, `no_transition_matched`), and an `error` for the user. When the
transition graph ends the run as `workflow_failed` and the workflow declares
`failure_message_key` in `orchestrator.yaml`, `error` is that context value;
otherwise it is the runtime diagnostic. The session is marked failed before the
envelope is sent.

Every channel close is announced by the settlement that observed it. AG2's hub
delivers a packet before it appends the close it decided while accepting that
packet, so a packet that hands the turn to the user is not a pause until the
runner asks AG2's channel adapter (`on_accepted`) whether the channel is closing.
If it is, the runner settles on the close. A `max_turns` close on the turn that
reverts to the user therefore ends the run as failed instead of pausing it.

Input for a run that has ended is refused with `WORKFLOW_SESSION_TERMINAL`. When
this process announced the run's failure, the message and the error's `reason`
field carry the failure text it reported; the process keeps the reasons of its
most recent failed runs (a bounded map), so a refusal for an older run carries
the code without a reason. A paused run whose channel AG2 had already closed
does not deliver the message, and nothing the paused run already reported is
projected again. Its end is announced once, through the same failed envelope,
before the input is refused. Live-continuation results say they announced their
outcome (`outcome_announced`), so background execution adds no second
`runtime.process_completed` for them; it emits one only for a rejection that
sent no envelope.

Ordering caveat: a completed run announces its outcome before its completed
status is persisted, while a journey advance reads that persisted status. The
two are not synchronized today.

## Related Docs

- [Workflow Architecture](../../architecture/workflows/workflow-architecture.md)
- [UI Surface and Layout Architecture](../../architecture/app/ui-surface-and-layout-architecture.md)
