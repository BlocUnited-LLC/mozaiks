# ADR 0016: Existing-App Factory Build Target

Date: 2026-10-07

Status: Accepted. Runtime implementation and live acceptance require their own verification.

## Decision

Keep the owner-scoped OSS Factory AppRegistry as the one build-target authority
for Factory workflows. Add a narrow, server-authorized registration path for a
pre-existing app whose canonical `app.json` ID is already fixed. App Zero may
link its separate, tenant/workspace-scoped managed-app record to that Factory
target. It must not replace the Factory registry with a second build-target
provider or treat a managed repository snapshot ID as a `BuildRecord` ID.

This extends [ADR 0009](0009-factory-execution-and-build-target-identity.md):
the Factory registry ID and App Zero commercial registry ID remain distinct.
The existing `RunBuildBinding` continues to hold Factory registry, target app,
build, and phase IDs. No tenant or workspace identifier is added to it for this
use case because Factory never reads or writes App Zero's private registry.

## Current Source and Reason

App Zero's running `app.json` declares `mozaiks-platform`. Its managed
brownfield record also uses that app ID and stores the operator owner, tenant,
workspace, and repository references. The public Studio
`POST /api/studio/apps` and Factory `app_registry.create_app_record` action
accept only a name and description; they allocate a different target app ID.
The internal `AppRegistryService.create_app_record` can accept an explicit
`app_id`, but calling that method directly from a private app module would
bypass a reviewed public authorization seam. Its current upsert also updates
lifecycle state on repeat calls, so it is not an adoption operation.

App Zero's current `scripts/bootstrap_app_zero.py` also writes a Factory
registry row directly. That row uses target ID `mozaiks-platform-app-zero`,
execution host `mozaiks-platform`, and the commercial registry ID as its
`_id`. It cannot serve as the complete-source target described here because
the unchanged brownfield `app/app.json` has ID `mozaiks-platform`. The
registration path must not silently create a second usable lineage beside an
earlier bootstrap row. App Zero must stop that direct write and inspect
existing local rows before linking the new target. An active build or artifact
on that earlier row requires an explicit migration decision; an empty draft
can be retired under a checked operator procedure.

Once a Factory target exists, the current Studio route, `bind_factory_session`,
build lifecycle hook, and AppGenerator all resolve and compare the same Factory
registry ID, owner, execution host, target app ID, and build ID. App Zero already
has a distinct `factory_binding` projection for Factory event correlation.
Replacing only Studio's target lookup would leave the other three Factory
callers on the old registry and would conflict with ADR 0009.

## Registration Contract

The new OSS operation should register **the executing app itself** as an
existing Factory target. It is a trusted host operation, unavailable as a
general browser action. Its target ID comes from the loaded, validated
`app/app.json` and must match the authenticated execution app ID; neither an
app ID nor a local source path from a request body is authority. The caller
must have an authenticated operator capability and an App Zero managed-app
ownership check before invoking it. The implementation should expose this
through a narrow public OSS host API rather than importing a private Factory
module from App Zero.

The operation returns a typed target with exactly these identity facts:
`build_registry_id`, `target_app_id`, `execution_app_id`, and `owner_user_id`.
The Factory registry owns build lifecycle state and its current build/run
pointer. Repository, tenant, workspace, and commercial state remain in App
Zero; immutable source provenance belongs on the imported `BuildRecord`.

Registration is insert-once and idempotent for an exact owner/host/target
match. It must never reset an existing lifecycle state, overwrite another
owner's record, allocate a build run, mark an artifact current, or treat an
unverified App Zero registry ID as a Factory registry ID. The existing global
unique target-app-ID index means `mozaiks-platform` can have one Factory owner;
App Zero's singleton registration and a designated operator owner must agree
before first registration. Multi-owner access to the same target is a separate
authorization decision, not an implicit consequence of this operation.

App Zero then links the returned Factory identity to its own managed-app
record with an owner, tenant, workspace, app ID, and current-record compare
and swap. A partial failure is recoverable by repeating the exact registration
and link; it cannot leave a differently bound target usable. App Zero's
approved plan and handoff must reference both registry IDs by their distinct
names. An App Zero event consumer resolves its own record under the trusted
tenant/workspace context and checks that the Factory event's owner, host,
target, registry, and build IDs match the stored link. Factory never performs
a tenant/workspace lookup in the App Zero database.

## Workflow and Artifact Path

The first exact source import creates a **draft** OSS `BuildRecord` under the
bound Factory target. The imported app must pass complete-content and canonical
identity validation, then receive explicit review and acceptance as its initial
brownfield artifact lineage before a later request is called a Refinement Run.
Plan approval alone cannot admit the baseline. If existing promotion cannot
accept an intact brownfield app without replacing its live repository, that
acceptance contract needs its own explicit decision; a draft-to-refinement
shortcut is not an acceptable default.

[ADR 0017](0017-accepted-imported-genesis-without-deployment.md) proposes the
dedicated, no-deployment acceptance and durable Factory receipt for this gate.

An approved nonpatch request keeps the effective refinement-harness route:
`build_family`, change class, `workflow_sequence`, first workflow, and affected
families. App Zero verifies approval and its pinned repository snapshot against
that accepted baseline. Only its bound BuildRecord ID may enter Studio's
existing `trigger_source=refinement` path. Studio rechecks the Factory owner/host target,
the saved artifact, and the route; the session hook writes the same
server-owned `RunBuildBinding`. Resume, each lifecycle event, AppGenerator's
status update, and export continue to use the Factory registry's build-ID
compare and swap. A workflow completion, PR, or merge is not promotion.

Exact private Git fetching and repository approval stay in App Zero. OSS must
still provide a provider-neutral immutable content import and complete
app-workspace-to-bundle projection before that launch path can run. App Zero's
tracked `app/` plus `workflows/` tree is 899 files and about 12.9 MiB. Two
assets, a 2.55 MiB SVG and a 2.03 MiB PNG, exceed the current
`read_artifact_bundle` 2,000,000-byte per-file limit; an `.otf` font is not in
its binary asset taxonomy. Its revision hydrator currently reads text files
without `include_binary=True`, and AppGenerator writes `generated_files` with
`write_text(str(content))` before zipping only those written files. An
imported ZIP would therefore lose unchanged binary assets during revision.
The implementation needs an explicit immutable binary carry-forward outside
the text-only `generated_files` context, with a complete baseline manifest
whose every admitted path appears exactly once in the validated output or an
approved deletion. Source import, hydration, assembly, and validation must
agree on the manifest and byte digests. An importer must preserve every
unmodified asset byte or fail before creating a usable baseline. Raising one
limit alone does not solve binary retention through revision and materialization.
The 32 MiB total and 4,096-file limits also remain explicit admission checks.

## Alternatives Considered

- **Generic Factory build-target provider backed by App Zero's registry:**
  rejected for this use case. Studio HTTP has verified tenant/workspace claims,
  but `_resolve_studio_artifact_scope`, session hooks, `RunBuildBinding`, and
  lifecycle currently carry only owner/host/Factory IDs. A provider swap would
  need a new server-owned scope contract across trigger, session creation,
  resume, lifecycle, and AppGenerator. Without that contract, its private
  AppData lookup could cross scope; with it, two registries would own one
  Factory build lifecycle. A future provider proposal must first prove why the
  canonical Factory owner cannot serve it and amend ADR 0009 explicitly.
- **Use public Studio creation with a random target ID, then rewrite App Zero's
  app ID during import:** rejected because it changes the identity of the app
  being dogfooded and breaks the complete baseline identity check.
- **Call Factory's internal service from App Zero or launch a raw workflow
  chat:** rejected because those calls bypass the reviewed registration or
  refinement approval boundary.

## Consequences and Reversibility

OSS gains one exact existing-app registration contract, not a second registry
or agent engine. App Zero remains responsible for managed owner, tenant,
workspace, source-control, and approval authority; Factory owns build target,
artifact, validation, and promotion authority. This is a medium-risk public
contract: target registration and its identity checks need compatibility care,
but no existing `RunBuildBinding` data migration is required. Existing Factory
records and generated-app flows continue with their current IDs. An App Zero
record with no verified Factory link remains ineligible for Factory reentry.

## Validation Before Implementation Acceptance

- Registration tests prove exact owner/host/target idempotency without
  lifecycle reset, and reject foreign ownership, mismatched `app.json`,
  missing operator authority, or competing singleton claims.
- Existing Studio, session hook, resume, lifecycle, AppGenerator, artifact
  access, and export tests still prove Factory registry owner/host/build CAS.
- App Zero link and handoff tests reject changed owner, tenant, workspace,
  repository, pinned commit/tree, approval digest, route, or Factory identity.
- Import/revision tests prove complete exact bytes, including oversize assets,
  `.otf`, ordinary binary files, unsafe paths, symlinks, changed digests,
  missing blobs, and wrong `app.json` identity. Any unsupported input fails
  before a launchable BuildRecord is created.
- A local end-to-end run records the App Zero and Factory registry IDs,
  immutable source commit/tree, draft BuildRecord ID, approved route, chat and
  build IDs, validation result, and PR checks. It does not claim live success
  from an offline agent test.

## Architecture Fit

This follows [ADR 0005](0005-reference-factory-and-proprietary-build-intelligence-boundary.md)
and ADR 0009: generic lifecycle and contracts stay in OSS; provider credentials
and managed operation stay in App Zero. It adopts issue #411's deterministic
identity/reference principle and defers a new semantic ledger. No workflow
sequence, transition, entrypoint, or generated schema changes are approved by
this ADR alone.
