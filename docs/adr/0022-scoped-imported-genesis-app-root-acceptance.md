# ADR 0022: Scoped Imported Genesis App-Root Acceptance

Date: 2026-10-14

Status: Accepted. Implemented with the coordinated imported Genesis gate,
receipt, review API, and owner-facing Studio surface.

## Context

[ADR 0016](0016-existing-app-factory-build-target.md) keeps the owner-scoped
Factory AppRegistry as the build-target authority for an existing app. Draft
[PR #871](https://github.com/BlocUnited-LLC/mozaiks/pull/871) and its draft
[PR #889 correction](https://github.com/BlocUnited-LLC/mozaiks/pull/889)
propose an immutable imported `BuildRecord`, owner review, and the
`authored_app_root_acceptance_v1` gate before the record can become a Refinement
Run baseline. That gate requires no unverified observer checks, so the current
contained run cannot create a live accepted imported baseline.

The source-free Docker observer can confirm app HTTP and disposable MongoDB
effects but cannot see an invalid `ctx.emit` suppressed inside imported Python.
Draft [PR #890](https://github.com/BlocUnited-LLC/mozaiks/pull/890) identifies
the trust boundary; draft
[PR #892](https://github.com/BlocUnited-LLC/mozaiks/pull/892) shows why the
mutable emitter and the `actions[].emits` allowlist cannot prove every attempted
event was validated. No event gateway, action response, app log, or empty
rejection list currently closes that gap.

[ADR 0020](0020-generated-app-contained-runtime-acceptance-scope.md) defines a
narrower **generated-app** scope and expressly leaves imported Genesis under a
separate strict admission contract. This ADR proposes the separate imported
decision contemplated by draft ADR 0017. It does not apply the generated scope
to imported source or describe an implemented exception.

## Decision

Define one replacement imported-source acceptance contract,
`authored_app_root_acceptance_v2`, with `validation_scope: app_root_only` and
the sole `excluded_observer_checks` value `event_rejection`. The raw contained
result and the durable validation evidence must continue to say
`observer_unverified_checks: ["event_rejection"]`. An exclusion limits the
claim; it never converts an unverified check into a passed check. Another
unverified check, a missing marker, a changed exclusion list, or a failed
required check blocks acceptance. No caller may opt into a looser scope.

The contract covers the exact imported `app/` source and binary assets in one
owner-reviewed archive. It can establish a validated artifact lineage for
later refinement. It does not validate workspace-root `workflows/`, provider
integrations, deployment, the running hosted app, or a private repository's
commercial approval. App Zero must validate its workspace-root workflows and
source-to-running-host relationship separately before describing its full
repository as accepted or releasing a self-build change. Acceptance neither
promotes bytes into the active app root nor advances the Factory active-build
pointer.

### Evidence and durable receipt

The existing imported `BuildRecord` and AppRegistry receipt remain the two
canonical persistence surfaces. Do not add a second evidence store or private
build-target authority. Before AppRegistry records an acceptance receipt,
server-owned validation must bind all of these facts into one canonical,
versioned evidence digest on the immutable imported record:

1. The Factory owner, execution host, target app ID, registry ID, record ID,
   trusted source locator and available revision/tree provenance, archive
   SHA-256, manifest SHA-256, and the complete per-member digest projection,
   including binary members. Draft #871 requires caller-asserted `source_id`,
   `revision_id`, and `tree_id` for every import; their shape alone does not
   prove Git provenance. A Git-backed import must bind its verified commit and
   tree. An archive or local-source import without such identities needs a
   separately reviewed provenance contract, never fabricated Git IDs. Every
   import binds the archive and manifest digests. The opened archive,
   `app.json` identity, and manifest must be reverified against the reservation;
   browser-supplied digests are comparisons, not authority.
2. The inherited UI registry digest, app-root snapshot digest, required static
   and contained runtime-load results, and the immutable local validator image
   ID preflighted before imported source is staged.
3. One host-bound observer run ID, source digest and image ID matching that
   run, and the trusted external observer origin. Observer B must emit exactly
   one passing `boot.http_ready` outcome before exactly one final `done` event;
   both carry the host-selected run nonce. The host verifies that sequence and
   all applicable HTTP/Mongo outcomes. Every required outcome must pass;
   missing, duplicate, stale, timed-out, truncated, or failed evidence blocks
   acceptance. An optional `not_run` outcome is never presented as tested
   behavior.
4. Host-owned confirmation that both contained processes were removed, with no
   process, output, timeout, or teardown failure. Imported Python cannot attest
   to its own cleanup or manufacture the observer's completion.
5. The exact `authored_app_root_acceptance_v2` scope, its single exclusion, and
   the unchanged `observer_unverified_checks` marker.

The AppRegistry compare-and-swap receipt records the reviewer, acceptance
time, contract and scope, a server-computed digest of the owner disclosure,
and the validation-evidence SHA-256. The evidence digest covers all facts above
and is checked against the reserved source claim and the persisted record on
every baseline read. A retry may finish only the same first-writer evidence
and exact owner/host/target/source claim; it cannot substitute a new observer
run or silently reinterpret an earlier receipt. An accepted receipt can be
rechecked from durable bytes without Docker, but a changed or missing field
closes refinement access. Current `v1` evidence or mock receipts are not
upgraded by read-time fallback.

### Owner decision and downstream claims

The Studio review surface and the acceptance API must show the owner the exact
source identity and a plain-language scope statement before recording consent:
the selected app root passed the listed static and external probes, while
event rejection, event emission completeness, workspace workflows, deployment,
and the running product are outside this acceptance. The owner must explicitly
acknowledge the server-selected disclosure version and exact source digests.
The receipt persists that acknowledgment; a checkbox or client-supplied scope
cannot change the server's validation result. No automatic Genesis acceptance
or bootstrap shortcut is introduced.

The framework's canonical action emitter rejects invalid submitted events in
its tested path. That invariant is not proof that imported code made no
suppressed invalid attempt, emitted every intended event, or produced a
downstream reaction. Consumers must expose the `event_rejection` exclusion
where they display or use the baseline. A requirement, refinement, promotion,
or release decision that depends on event delivery or reactions needs its own
trusted, requirement-specific observed effects. If that evidence is absent,
the event-dependent guarantee remains unverified and the dependent decision
must not pass. The limited app-root receipt cannot serve as that evidence.

## Implementation boundary and activation proof

This decision uses a coordinated replacement of draft `v1`, without an alias
or permissive branch. The imported gate, typed AppRegistry receipt, persisted
validation evidence, owner-facing review/API, baseline reads, and tests move
together. Earlier draft PRs #871 and #889 required an empty unverified list and
did not bind every completion and cleanup fact above; this implementation
supersedes those drafts. Draft PRs #890 and #892 remain analysis of the event
boundary, not event proofs.

Required implementation verification includes:

- Exact archive/member, source locator, applicable revision/tree,
  owner/host/target, inherited UI, image, run, external boot/`done`, and host
  cleanup tampering or absence each refuse the receipt and subsequent baseline
  read.
- A contained invalid-event fixture still reports
  `observer_unverified_checks: ["event_rejection"]`; it may satisfy only the
  explicitly disclosed app-root scope when every other required check passes.
  A passing HTTP response and database write never assert event correctness.
- Missing, extra, changed, skipped, duplicate, or stale observer results and
  any failed required check refuse acceptance. Interrupted first-write and
  concurrent owner/source changes cannot replace saved evidence.
- The owner can inspect the exact bytes and scope; no acknowledgment, changed
  disclosure, foreign owner, or stale source refuses acceptance. Refinement
  hydration and export recheck the same receipt and source binding.
- App Zero can consume the public OSS seam without a private Factory write.
  Its separate workflow-root, live-host, and event-dependent proofs remain
  independently blocking where claimed. A local contained proof does not
  claim live App Zero, Android, marketplace, or release acceptance.

Activation requires the coordinated code/UI tests in the implementing PR. No
release, migration of an existing live lineage, or cloud service follows from
this ADR.

## Architecture fit and alternatives

This keeps deterministic acceptance in OSS Factory/Studio, artifact lineage in
the existing store, the owner-scoped build target in Factory AppRegistry, and
hosted source approval in App Zero. It changes no AG2 execution primitive or
provider adapter. It adopts the deterministic identity and reference direction
raised in [issue #411](https://github.com/BlocUnited-LLC/mozaiks/issues/411),
but defers a semantic-model or decision-ledger redesign. Evidence explains a
validated source; it does not become another authority for app meaning.

Rejected: clearing the unverified marker; trusting imported process logs or a
mutable Python callback; interpreting `actions[].emits` as a required event
count; treating generated-app scope 2.0 as imported acceptance; accepting a
static scan or successful HTTP response as whole-repository proof; making a
browser checkbox override validation; and creating a second private Genesis
registry or receipt store.

This is a medium-risk public contract if implemented: downstream consumers
must distinguish the limited imported scope from complete event proof.
Because no live `v2` receipt exists, the proposal itself is reversible. Once
implemented, changing the scope requires a new version and explicit handling
of existing receipts rather than silent reinterpretation. It preserves
[Architectural Invariants](https://github.com/BlocUnited-LLC/mozaiks/blob/main/ARCHITECTURAL_INVARIANTS.md)
3, 4, 7, and 8:
deterministic validation, versioned public contracts, App Zero consumption of
public seams, and separate operator authority. A docs-only proposal changes
no runtime, workflow sequence, generator output, app workspace, or release
behavior.
