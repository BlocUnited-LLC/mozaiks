# ADR 0019: Imported Genesis Event Witness Semantics

Date: 2026-10-08

Status: Proposed. Trust boundary assessed; imported Genesis acceptance remains closed.

## Question

Can a source-free observer prove that an imported action invoked the runtime
event validator for every `ctx.emit` call, including a rejected call, using
the #891 event gateway and an observer-controlled action ingress?

## Threat model and current boundary

The imported app's Python is untrusted. Its handler can mutate the
`ModuleContext` passed into the action, replace `ctx._emit`, import or patch
runtime objects in its own interpreter, decide what HTTP response to return,
and write through its granted persistence context. It can also send packets
directly to the observer's loopback event gateway once it learns the run and
request IDs. The source-free observer B and the parent are trusted for this
assessment; their pinned image, copied declarative plan, and audit result are
outside the imported handler's Python interpreter. This is the same trust
split used by `app_runtime_smoke.py` in `_serve_imported_app`,
`_probe_imported_app`, and `_trusted_event_gateway`.

`ModuleExecutor.execute` creates a mutable `ModuleContext` with `_emit` set to
the executor's callback. `ModuleContext.emit` calls that mutable field, or
returns `None` if it is absent. The smoke executor in A replaces the callback
with a function that forwards packets to B. Consequently, B observes only
packets A chooses to send. Its `trusted_event_audit_v1` can validate every
packet it receives, but it cannot count calls that never reach the callback.
See `mozaiksai/core/runtime/composition/module_context.py` (`ModuleContext.emit`),
`mozaiksai/core/runtime/composition/module_executor.py`
(`ModuleExecutor.execute`, `_build_context_emitter`, `_event_rejection`), and
`factory_app/workflows/AppGenerator/tools/app_runtime_smoke.py`
(`_ObservedModuleExecutor`, `_TrustedEventAudit`).

The observer chooses probe actions and request IDs and sees the HTTP response.
That proves it sent a request and received a response from A; it does not by
itself prove which imported handler lines ran. A also receives the request ID,
so a correlated packet proves receipt by the gateway, not event causality.
Replay, stale-run, late-packet, and malformed-packet checks remain valuable
protocol checks, with this narrower meaning.

## Contract semantics

`module.yaml` `actions[].emits` is an allowlist. `_event_rejection` rejects an
event type outside that list, then validates the payload schema for a submitted
event. It does not require an action to emit any event. A successful action
with zero event packets is therefore valid under the current contract. The
list cannot be reinterpreted as a minimum emission count without changing
runtime and generated-app semantics. `contracts/events.yaml` defines payload
schemas for events that are submitted; it also imposes no per-action count.

The current external evidence can soundly say:

- B sent a particular HTTP probe and observed its response and database effects.
- B validated each event packet it received against the copied declarations.
- B rejected malformed, replayed, stale, or late packets it received.

It cannot soundly say that every `ctx.emit` invocation reached B, that no
invalid invocation was suppressed, or that an `emits` declaration guarantees
at least one event. A trusted action ingress alone would prove that B initiated
dispatch; it would not give B visibility into calls suppressed inside an
untrusted Python handler.

## Counterexamples and decision

Two executions can have the same B-visible HTTP, Mongo, and gateway trace:

1. A handler completes an allowed action without emitting an event.
2. A handler replaces `ctx._emit` with a no-op, calls `ctx.emit` with an invalid
   payload, and completes the same action.

Even an opt-in rule that requires one event does not prove completeness:
the handler can forward one valid event, then replace `ctx._emit` and call
`ctx.emit` with an invalid payload. B sees the required valid packet, while
the invalid call never reaches its validator. The contained adversarial tests
in `tests/test_app_runtime_smoke.py` exercise both traces. The existing
positive, invalid, replay, and stale-run tests prove packet validation, not
complete observation of untrusted Python calls.

Do not implement a new ingress or clear `event_rejection` on the current
contract. An ingress that merely delegates to A, or a sealed-looking Python
callback shared with imported code, would preserve the counterexample while
creating a false acceptance signal. `observer_unverified_checks` must retain
`event_rejection`, and `genesis_import._verified_observer_evidence` must keep
rejecting live imported Genesis acceptance. This ADR does not modify App Zero
lineage, receipts, deployment, or release state.

## Preconditions for a future acceptance claim

A future contract must first define the claim it asks B to prove. If the claim
is only that selected successful probes produce specified events, those
obligations need an explicit per-probe type, count, and success condition;
`emits` is insufficient. B could then attest to presence or absence of those
specified packets under observer-owned action ingress. That would still not
prove that no additional invalid `ctx.emit` attempt was suppressed.

If the claim is that **all** calls to the event API were validated, runtime
execution must make the trusted event channel the exclusive event API across
the untrusted-code boundary and provide a threat model that rules out or
detects handler-side substitution. The present mutable Python context has no
such property. Before any gate change, a new evidence version must specify
its exact scope and bind observer results to the source and validator image
digests; the five proofs in ADR 0018 remain required. No current test or
receipt is evidence that those preconditions hold.
