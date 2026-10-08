# ADR 0018: Imported Genesis Event-Rejection Observer Boundary

Date: 2026-10-08

Status: Proposed. A partial event observer is implemented; imported Genesis
acceptance remains closed.

## Finding

At the #890 baseline, the contained imported-app smoke had two processes with
different authority. Container A ran imported Python alongside
`ModuleExecutor`. Its `_ObservedModuleExecutor` collected
`ModuleResult.rejected_events` in an in-memory `_SmokeRun` list. Container B
had no imported Python mount; it drove the app over loopback HTTP and read the
disposable Mongo database. B could observe a successful write and response
after `ctx.emit` was rejected, but could not observe that rejection. See
`factory_app/workflows/AppGenerator/tools/app_runtime_smoke.py`:
`_boot`, `_SmokeRun._request`, `_serve_imported_app`, and `_probe_imported_app`.

The prior contained negative fixture omitted a required `user_id` while the
write and HTTP response succeeded; B reported a passing functional smoke and
`observer_unverified_checks: ["event_rejection"]`. The in-process counterpart
`test_a_rejected_event_fails_its_own_check_while_the_write_behind_it_passes`
sees the rejection, but A's result is not independent evidence. The contained
good-app case left event rejection unverified. It proved that B could observe
boot and normal CRUD, but not event rejection.

## Decision

Keep imported Genesis acceptance closed on the current boundary. In
`app_runtime_smoke.py`, `_imported_observer_scope` must keep naming
`event_rejection` as unverified. In
`factory_app/workflows/_shared/platform/genesis_import.py`,
`_verified_observer_evidence` and `_validation_matches_claim` must keep
rejecting that result. A header, endpoint, stdout record, or Mongo row
produced by A would not close the gap: imported Python shares A's interpreter
and can alter or fabricate A-owned observations. The external observer must
not treat the absence of a rejection report as proof that no rejection
occurred.

This ADR does not alter an imported draft, an acceptance receipt, a historical
App Zero registry row, or deployment state. The `app_root_only` authored-source
gate in ADR 0017 still requires separate validation of workspace-root
`workflows/`, including workflows preserved in an archive, before a full
repository self-build claim.

## Partial Observer Seam

The contained smoke now copies `contracts/events.yaml` into B's read-only
declarative plan. B loads the canonical `ModuleExecutor` event validator
without imported handlers, starts a bounded loopback event gateway, chooses a
request ID for each probe action, and closes each action from the HTTP response
it observes. A's smoke-only `ctx.emit` callback forwards typed BSON event
payloads to B. B validates the declared event type and payload, records
rejections and malformed, stale, duplicate, or late packets, and writes its
own `trusted_event_audit_v1` outcome. The parent checks one matching B-owned
audit and completion record before describing the functional smoke as passed.
The gateway bounds each packet, and the ledger bounds action requests, unique
event IDs, and retained protocol errors; exceeding any limit fails the run.

The fresh-image contained valid fixture completes with zero rejected event
requests. The invalid-event fixture produces an externally observed event
failure even though its create write and HTTP response pass. Contained replay
and stale-run packet fixtures also fail while their create actions pass.
An adversarial fixture that replaces A's callback still passes B's zero-error
audit, demonstrating why that result cannot clear acceptance.
`tests/test_app_runtime_smoke.py` covers these cases and altered or missing
audit records.

This is a useful probe, but it does not establish the full boundary in the
Decision above. Container A still owns the HTTP action router and runs imported
Python in the same interpreter as the smoke-only forwarding callback. Imported
code can suppress that callback; B cannot distinguish suppression from an
action that emitted no event. B can attest to event requests it receives and
validate them independently, but cannot prove every `ctx.emit` invocation
reached it. `_imported_observer_scope` therefore still reports
`observer_unverified_checks: ["event_rejection"]`, and
`genesis_import._verified_observer_evidence` still rejects live acceptance.

## Remaining Boundary for Acceptance

The trusted action ingress and event channel must run outside the process that
imports app Python, and that trusted process must have no path for loading the
imported Python package. B now validates event packets, but A still owns the
HTTP router and event forwarding callback. Imported handlers may run in a
worker, with a bounded protocol to request runtime operations. The trusted
side must own the event contract, validation, dispatch decision, and audit for
each event request.
It must associate every probe action with an observer-selected run ID and
request ID, and return a final, monotonic completion record that includes its
rejection count. Missing, duplicate, out-of-order, crashed, or timed-out
records fail the check. An app-owned endpoint, log line, database collection,
or counter cannot be this authority.

The external probe must match its HTTP action calls to those trusted
completion records and fail when any trusted rejection is recorded, even if
the HTTP response and persistence checks pass. The observer can attest to
events that use the runtime's event channel; it cannot infer an author's
unexpressed intent to emit an event. This scope must be explicit in any future
receipt. The trusted observation should have a new versioned origin and
evidence fields, bound to the existing source and validator-image digests.
Simply changing `observer_unverified_checks` to `[]` under
`trusted_external_probe_v1` would misrepresent the current B probe's reach.

## Required Proof Before Clearing the Gate

1. Build a fresh pinned preview image from the reviewed source. Run the
   contained negative fixture above and demonstrate an externally observed
   failed event check despite HTTP 200 and a successful write.
2. Run the valid fixture and demonstrate an externally observed completed
   event audit with zero rejections. Its `observer_unverified_checks` may then
   be empty only under the new observer contract.
3. Suppress, forge, duplicate, reorder, or omit an app-side report and prove
   that B rejects or times out, rather than treating silence as success.
4. Crash or stall the worker during an action and prove there is no accepted
   completion record. Show cleanup of both containers and the disposable DB.
5. Update the exact evidence schema and digest checks in `genesis_import.py`
   with tests for altered, absent, stale-run, and replayed event evidence.
   Existing positive acceptance tests use explicit future-result mocks; those
   mocks are not a live proof.

Until all five conditions hold, the separate process is a design requirement,
not an implemented observer, and imported Genesis remains a reviewable draft.
