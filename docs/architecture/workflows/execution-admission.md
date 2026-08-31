# Workflow Execution Admission

Production workflow execution has two ordered authorities:

1. Mongo workflow admission owns immutable run-turn identity, cross-process
   claim ownership, bounded attempts, and terminal outcome replay.
2. The chat execution lease owns mutable `(app_id, chat_id)` session, WAL, UI,
   pending-input, AG2 stream, and AG2 Network knowledge writes for the admitted
   execution.

The canonical execution funnel is
`SimpleTransport.handle_user_input_from_api`. HTTP input, WebSocket user turns,
host auto-start, workflow start/switch/batch handlers, journey spawns, live AG2
Network continuation, Genesis Build, and Refinement Run launches converge on
that funnel before AG2 runs. Task-batch concurrency remains internal to the
already-admitted AG2 execution. Read-only reconnect/history replay does not
claim admission or the chat execution lease.

The Mongo queue stores tenant, workspace, app, chat, workflow, run, operation,
and user identity plus the admitted request digest as immutable top-level
fields. Payload data is not identity authority. Reusing an operation id with
changed request content is rejected. A deterministic admission id makes concurrent identical producers
converge on one record. The ingress instance uses a targeted atomic claim so
the process that owns the live WebSocket also owns UI delivery; the previously
documented autonomous global poller did not exist and is not introduced here.

Claim renewal is holder-scoped. Completion, failure, and dead-letter transitions
require the current claim token, so a stale release cannot affect a successor.
An expired claim is reclaimable only while `execution_started_at` is absent.
Once execution starts, an expired claim is terminally dead-lettered on replay;
automatic rerun would otherwise risk repeating an externally visible model or
tool side effect without an idempotency proof.

`required` mode is selected whenever database persistence is enabled and fails
startup or execution closed if Mongo or required indexes cannot be verified.
`local` mode is explicit, process-local, and non-durable. The existing local
semaphore remains only a resource throttle.

Current exclusions are intentional: no cancellation endpoint, orphan adoption
or reconciliation, storage-level fencing token, token reservation, workflow
rewrite, or AG2 replacement is part of this slice. Process-local cancellation
currently has no production API caller. A crash before the execution-started
marker is safely reclaimable; a crash after it remains dead-lettered for the
later orphan-reconciliation lane rather than being silently rerun. Existing
`IN_PROGRESS` session records are not adopted by this slice.
