# Factory Execution And Build Target Identity

Status: Implementation in progress, following operator approval on 2026-09-10.

## Problem

ValueEngine's startup tool attempts an unauthenticated Studio registration and
replaces execution `app_id` with the generated app ID on success. Artifact
writers then use that ID while usage, sessions, and transport require the host
ID. Suppressing registration failures does not establish an isolated target.

## Decision

- Keep execution `app_id`, `user_id`, `chat_id`, and workflow identity immutable.
- Use the existing protected ChatSessions `run_build_binding` field for a
  strict `RunBuildBinding`: registry reference, generated target app ID, build
  ID, and `genesis` or `refinement` phase. It is server-written, not model input.
- Extend the existing platform session hooks and owner-scoped Studio registry
  for registration, reopening, and validation. Required hook failures are fatal.
- Resolve required `runtime` context sources before data-reference queries.
  Runtime values are read-only projections of validated server session facts.
- Address Factory artifacts and generated app identity by the bound target.
  Keep AG2 calls, transport, persistence of execution state, and charging scoped
  to the executing host. Scope routing/revision state by target as well as owner
  and host, and preserve the binding across handoffs and reloads.
- App Zero links its commercial records through the public platform extension
  contracts, including the existing build-event callback and the public
  `mozaiksai.core.studio.build_events.BuildLifecycleEvent` wire model. The
  callback carries distinct execution, target, registry, and build IDs; a
  receiver must not treat the Factory registry ID as its own commercial ID.
  Its AppData `app_id` remains the host scope. Provider deployment,
  billing, and customer identity-provider policy remain proprietary.
- Public registry creation accepts a name and description only. Identity,
  session pointers, artifact paths, and build status are not caller inputs.
  The direct registry status and promotion APIs are removed; artifact acceptance
  and promotion enforce the saved version's validation gate.
- Studio artifact APIs require `build_registry_id`, resolved through the
  authenticated owner and execution host. `app_id` remains the execution-host
  selector and is never an arbitrary generated target selector.
- Promotion materializes a version-specific workspace under
  `MOZAIKS_WORKSPACES_PATH/{target_app_id}/{artifact_version_id}` (default root
  `.local/workspaces`). App files live under `app/`; workflows and deployment
  artifacts stay at workspace root. The running host's app root is forbidden.
  This is local materialization, not paid hosting or production deployment.
- `POST /api/studio/build/restore` materializes an accepted version without
  changing the active registry pointer. The misleading `/build/revert`
  endpoint is removed. Skipped validation is never a validation pass.

The existing registry, session store, artifact store, refinement engine, and
AG2 runner remain canonical. No additional agent engine, registry, billing
ledger, or semantic authority is introduced. The obsolete startup registration
tool and identity-overwrite aliases are migration deletion targets.

## Issue 411

Reviewed issue #411 against the current context-authority, session persistence,
Studio registry, and Factory artifact code. This decision adopts its narrow
principle that deterministic code owns identity and references. It defers the
broader semantic-model/decision-ledger proposal: a run binding is execution
metadata, not application meaning or a second semantic ledger. Existing
accepted semantic compiler decisions are unchanged.

## Acceptance

Registration failures and foreign/stale references stop before model calls.
Two users and two apps of one user remain isolated through approval, handoff,
resume, refinement, artifact access, and export. Host usage identity remains
unchanged. Generated manifests and hosted target records agree on app identity.

Scripted and real-Mongo checks precede a paid end-to-end Factory proof. That
proof must create an authenticated customer tracker, exercise persisted CRUD
across restart, perform a revision, and export the app. A smoke workflow alone
does not satisfy this acceptance. No paid infrastructure or release is needed.

## Verification Status

Implementation remains in progress. On 2026-09-11 the real `RuntimeSmoke`
workflow completed through AG2 with OpenAI and local MongoDB, returning valid
structured output and recording 197 input plus 32 output tokens. Its run was
`chat_runtimesmoke_915069c6` in app scope `live-smoke-9e294e70`.

That run does not prove Factory generation, authentication of a generated app,
CRUD across restart, refinement, or export. The hosted-product integration and
dependency pin must wait for the full acceptance above and a green OSS suite.

The subsequent local OIDC browser test approved and revised a Client Ledger
concept, then verified actual target-scoped persistence for ThemeCapture,
DesignDocs, and SubscriptionContractDesigner. Their recorded outcomes were
`saved`, `saved`, and `confirmed`, respectively. These were real AG2/model calls,
not captured-output fixtures. Recovery used authenticated public launch APIs;
the test did not bypass auth or manually insert generated artifacts.

The test exposed and repaired false completion after failed saves or missing
transitions, read-only structured-output handling, strict index-key schemas,
workflow UI reload registration, and browser handoff/connection races.
AgentGenerator and the complete generated-app CRUD/refinement/export acceptance
remain unverified. Passing the preceding stages is not a completed app build.
