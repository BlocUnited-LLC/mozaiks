# ADR 0017: Accept an Imported Genesis Source Without Deployment

Date: 2026-10-07

Status: Proposed in draft PR #871. Live App Zero lineage remains undecided.

## Decision

An imported existing-app source starts as an immutable, `DRAFT` `BuildRecord`
and an exact `reserved` claim on the owner-scoped Factory AppRegistry. It can
become a Refinement Run baseline only after the authenticated Factory owner
reviews the exact source and explicitly accepts it. Acceptance marks the
`BuildRecord` `CURRENT` in its artifact lineage and stores a durable
`genesis_import.acceptance` receipt on AppRegistry. It does not copy bytes to a
runtime workspace, update AppRegistry's active artifact pointer, promote the
bundle, deploy, or replace the already running app.

This closes the acceptance choice left open by [ADR 0016](0016-existing-app-factory-build-target.md).
`CURRENT` here describes artifact lineage. AppRegistry remains in `draft`
until a later, independently authorized build lifecycle transition. The
accepted receipt remains attached to the original Genesis source after later
build runs and artifacts change their current pointers.

## Owner Review and Validation

The owner uses the existing Studio artifact review and verified download to
inspect the imported archive and its recorded source commit/tree. The review
response includes the owner-scoped reserved digest facts needed for the distinct
`POST /api/studio/build/artifacts/{build_record_id}/accept-genesis` route
requires a selected Factory `build_registry_id`, an authenticated owner, the
explicit `confirm_exact_source_review: true` assertion, and the exact
`reviewed_bundle_sha256` and `reviewed_manifest_sha256` values. Its digest
arguments are checks against the server-held reservation, not content
authority supplied by the browser.

Before recording acceptance, OSS reopens the persisted `content_digest` blob
through the canonical verified artifact reader. It checks the canonical
archive entry and every source member against the recorded file manifest,
verifies the root `app.json` target ID, and runs the canonical app-bundle
acceptance gate, including runtime contract checks. For imported source,
`AppLoader.load()` must run only in a contained imported-source smoke. The
ordinary generated-app smoke is insufficient: its child inherits local
filesystem/network access and receives the host's configured Mongo URI on
stdin. An app handler could choose another database with that credential;
the smoke's disposable database name is not a permission boundary. The
imported-source path therefore fails closed while a contained runner is
unavailable. Its staged workspace includes the verified binary assets.
The contained runner and its database isolation need independent review before
live use. A failed, skipped, or
pending result cannot become an accepted source. Validation evidence is tied
to the exact archive and manifest digests on the `BuildRecord`.

AppRegistry accepts the exact reserved claim with a single owner/host/target
compare and swap. Its receipt records reviewer, time, validation contract,
and evidence digest. The artifact store then conditionally projects the
validated draft to `CURRENT`. If the process stops between these writes,
launch and hydration remain closed until an exact owner retry finishes the
projection. A competing source, reviewer, digest, or changed target cannot
reuse the receipt.

Generic artifact accept, reject, and promote routes cannot mutate this
imported Genesis record. The generic accept override and promotion workspace
restore are different lifecycle operations. No route is added here for
changing the pinned source after reservation; that requires a separate
reviewed reset/migration decision.

## Refinement Boundary

Studio checks the receipt, exact Factory owner/host/target binding, validated
record status, manifest digest, and immutable archive bytes before launching
an imported source as a refinement baseline. Failed-workflow retry repeats
the same check against its saved source. AppGenerator checks again before
hydration or binary asset carry-forward. Ordinary revision drafts retain their
existing behavior and do not require this Genesis receipt.

App Zero still owns its tenant/workspace/repository approval and the match
between its pinned source and this accepted OSS baseline. A Factory receipt
does not assert that the source commit is deployed, merged, or approved by App
Zero's commercial control plane. Existing local App Zero registry rows need
an explicit lineage choice before any live import or acceptance.

## Rejected Alternatives

- Treat a complete imported `DRAFT` as a baseline: it skips explicit owner
  acceptance and makes a first source indistinguishable from work in review.
- Call generic accept or promote: generic acceptance lacks the source-claim
  compare and swap, and promotion restores bytes to a workspace. Neither is
  evidence that the running brownfield app matches this source.
- Put the accepted fact in `current_build_run`: later refinements replace that
  pointer, losing the original source acceptance evidence.
