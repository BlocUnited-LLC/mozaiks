# ADR 0020: Generated-App Contained Runtime Acceptance Scope

Date: 2026-10-09

Status: Accepted for generated-app acceptance. Build-validation isolation remains a separate promotion prerequisite.

## Context

The generated-app runtime smoke runs app Python and an image-owned observer in
separate local Docker containers. The observer sees the app's HTTP responses and
disposable MongoDB state. It cannot see an invalid `ctx.emit` rejected inside
the app process before event dispatch. The observer therefore reports
`observer_unverified_checks: [event_rejection]` even when every external check
passes. Treating that list as empty would claim evidence the observer does not
have; treating it as a required check leaves every generated candidate pending.

The platform-wired `ModuleExecutor` validates events submitted through its
action context emitter against the action's `emits` allowlist and the declared
payload schema before dispatch. An invalid submission is rejected without
failing an action whose writes and HTTP response succeeded. That framework
invariant is covered by real-dispatch tests. It does not establish that any
particular candidate attempted no invalid emission or emitted every event its
intended behavior requires. App Python can replace its mutable context emitter,
and other event paths have different guards. `actions[].emits` is an allowlist,
not a minimum event count. A black-box HTTP/Mongo observer cannot distinguish
an internally rejected emission from no emission.

## Decision

Use generated-app contained runtime acceptance scope **2.0**. Its sole
`excluded_observer_checks` value is `event_rejection`. Preserve the observer's
independent `observer_unverified_checks: [event_rejection]` in every persisted
result. This exclusion narrows the generated-app acceptance claim; it is not a
verified event check or a waiver of a reported failure. A generated candidate
may satisfy this scope only when:

- the source digest equals the exact generated file snapshot and the validator
  image ID equals the immutable locally preflighted image;
- the trusted external observer reports one matching HTTP boot and completion
  receipt, a run ID, and all applicable HTTP/Mongo outcomes without a failure;
- the host confirms both contained processes were removed and no bounded-output,
  timeout, process, or teardown check failed;
- the observer's entire unverified-check list is exactly `[event_rejection]`;
- every other required generated-app gate completes, including the contained
  AppLoader worker's host-confirmed cleanup and matching source/image identity.

The host, not candidate Python or observer output alone, attaches the versioned
`acceptance_scope` to a generated smoke result after checking that evidence.
Missing or altered evidence is pending, with no accepted snapshot digest. The
scope is applied only by the generated-app acceptance path. Imported Genesis
retains its separate strict admission contract and must not treat this exclusion
as a verified observer check. An observer result for imported source can say
`passed` about its external probes while still being insufficient for Genesis
admission.

This decision certifies the supported, externally observed generated-app
behavior for the tested snapshot and the framework's canonical action-emitter
guard. It does **not** certify absence of invalid `ctx.emit` attempts, event
emission completeness, downstream reaction behavior, startup services, or every
future input. Where an approved app requirement depends on event-driven effects,
acceptance must observe those effects separately; the generic scope cannot
stand in for them.

Build execution is independently required before promotion. Generated build
validation accepts only Docker, explicit E2B, or blocking `skip`. The Docker
path must inspect the pinned local image ID before source staging and run
offline as a non-root user in a read-only image with bounded writable space.
Candidate build commands and npm scripts have no host execution path.

## Rejected Alternatives

- Clearing `observer_unverified_checks` or trusting the app process's own
  rejection list: the app can forge or omit both.
- Requiring an external observer to attest every `ctx.emit` attempt without
  changing the event API: the candidate can suppress its mutable emitter, so
  the observer sees the same trace as an app that attempted no event. Proving
  all attempts would require an exclusive event channel outside candidate
  control and a separate architecture decision.
- Applying the generated exclusion to imported Genesis: imported source has a
  distinct acceptance boundary and must retain its stricter gate.

## Verification

Generated-scope tests cover the exact exclusion, source/image and observer
receipt/cleanup tampering, failed external outcomes, and persistence of the
unverified marker. A contained invalid-emission fixture demonstrates that the
external observer can pass while honestly reporting `event_rejection` as
unverified. Framework dispatch tests independently prove invalid events
submitted through the platform-wired action emitter are not delivered.
