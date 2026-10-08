# ADR 0018: Imported Genesis Event-Rejection Observer Boundary

Date: 2026-10-08

Status: Proposed. No event-rejection observer is implemented by this ADR.

## Finding

The contained imported-app smoke has two processes with different authority.
Container A runs imported Python alongside `ModuleExecutor`. Its
`_ObservedModuleExecutor` collects `DispatchResult.rejected_events` in an
in-memory `_SmokeRun` list. Container B has no imported Python mount; it drives
the app over loopback HTTP and reads the disposable Mongo database. B can
observe a successful write and response after `ctx.emit` was rejected, but it
cannot observe that rejection. See
`factory_app/workflows/AppGenerator/tools/app_runtime_smoke.py`:
`_boot`, `_SmokeRun._request`, `_serve_imported_app`, and `_probe_imported_app`.

`test_contained_smoke_names_its_unverified_rejected_event_check` in
`tests/test_app_runtime_smoke.py` exercises the negative case: an event omits a
required `user_id`, while the write and HTTP response succeed. B reports a
passing functional smoke and `observer_unverified_checks: ["event_rejection"]`.
The in-process counterpart
`test_a_rejected_event_fails_its_own_check_while_the_write_behind_it_passes`
sees the rejection, but A's result is not independent evidence. The contained
good-app case, `test_imported_smoke_isolated_docker_boot_and_cleanup`, also
leaves event rejection unverified. It proves that B can observe boot and normal
CRUD; it is not a positive event-rejection proof.

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

## Minimum Boundary for a Later Implementation

The trusted action ingress and event validator must run outside the process
that imports app Python, and that trusted process must have no path for loading
the imported Python package. Imported handlers may run in a worker, with a
bounded protocol to request runtime operations. The trusted side must own the
event contract, validation, dispatch decision, and audit for each event request.
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
