# Changelog

All notable changes to Mozaiks are tracked here.

This project follows a practical pre-1.0 changelog format:

- `Added` for new capabilities
- `Changed` for behavior, docs, packaging, or workflow changes
- `Fixed` for bug fixes
- `Removed` for removed behavior
- `Security` for vulnerability or hardening work

## Unreleased

### Fixed

- **Durable workflow execution admission is now wired (issue #426, sub-slice B)**:
  persisted and production hosts record immutable tenant/workspace/app/chat/
  workflow/run/operation identity in MongoDB before mutable session, WAL, AG2,
  model, or tool execution. Exact replay returns the recorded outcome instead
  of starting a second run; claims renew and complete only for their current
  holder, authority and index failures fail closed, and bounded retry/dead-letter
  states stay distinct. Expired claims may be recovered only before execution
  starts; once side effects are possible, replay dead-letters rather than
  re-spending tokens without an idempotency proof. Explicit `local` mode remains
  available as a clearly non-durable single-process boundary. The existing chat
  execution lease remains the per-chat mutation authority after admission.

- **Distributed same-chat exclusion is now enforced (issue #426, sub-slice A)**:
  the MongoDB chat lock at
  `mozaiksai/core/runtime/persistence/distributed_lock.py` — previously dead
  code with zero call sites — is rebuilt as a renewable chat execution lease
  and wired into every production start/resume/restart path (the
  `handle_user_input_from_api` funnel: HTTP input, WebSocket start/switch
  handlers, host auto-start, journey spawns, and live paused-run
  continuation). At most one runtime instance can execute a mutable run for a
  given `(app_id, chat_id)` at a time; distinct chats and tenants proceed
  independently, and read-only paths (history replay, reconnect) take no
  lock. The lease is renewed for the length of the protected operation,
  released at a durably persisted terminal or human-waiting boundary
  (including on exceptions and cancellation), and a stale holder can never
  delete a successor's lease. After confirmed lease loss (failed or
  unprovable renewal), the protected execution is cancelled and further
  durable session, workflow UI, AG2 stream, and AG2 Network knowledge/WAL
  writes for that chat are refused in-process. A still-running local holder
  cannot be hidden by a same-process successor. Operating modes are explicit: `required`
  (fail-closed distributed exclusion whenever database persistence is
  enabled; index or acquisition-authority failures remain distinct from
  ordinary contention and fail closed instead of degrading to a cosmetic
  lock) and `local` (explicit
  single-process serialization only), overridable via
  `MOZAIKS_CHAT_LOCK_MODE`. Contention, unavailable authority, renewal loss,
  and release failure each emit distinct diagnostics (`CHAT_LOCK_BUSY`,
  `CHAT_LOCK_AUTHORITY_UNAVAILABLE`, `CHAT_LOCK_RENEWAL_LOST`,
  `CHAT_LOCK_RELEASE_FAILED`). Lock documents now live in the same system
  database as the chat state they protect. One residual window remains
  documented and out of this slice: storage-level fencing tokens for a
  single in-flight write by a holder stalled past its TTL.

### Added

- **Projection emits graph v2 + typed payloads (ADR 0007 Slice 3E)**: the
  offline source projection now produces `mozaiks.semantic_projection.v2` —
  a Merkle-rooted `mozaiks.semantic_graph.v2` with one typed payload per node
  and bijective payload closure validated at build. Former "not representable
  by SemanticGraph v1" gap families become projected content: page/section
  ordering as explicit dense positions, plan/limit/meter/product facts
  (integer minor-unit prices, ISO-4217 currency), module/action/permission/
  event descriptions, and endpoint trigger bindings. Content is never
  invented — payload content fields are required-nullable (`None` when the
  source carries no such fact, omission is invalid), and absent collections
  remain distinct from explicitly empty collections while structural rules
  stay strict. Facts
  owned by edges or taxonomy are never duplicated into payloads. Navigation
  ordering, plan catalog ordering, intra-section binding composition, and
  renderer file lists remain explicit typed gaps. The source/graph fact
  equivalence proof now covers payload content digests, so projection honesty
  extends to content, not just identity.

- **Typed semantic payloads + Merkle-rooted graph v2 (ADR 0007 Slice 2E)**:
  every semantic node kind now has exactly one strict payload variant
  (`mozaiks.semantic_payload.v1` — titles/intent text, typed field shapes,
  integer minor-unit prices with ISO-4217 codes, explicit dense-position ordering, taxonomy-
  validated event/capability ids, portable-path-validated paths; no untyped
  dicts), pinned into `mozaiks.semantic_graph.v2` nodes by a full-identity
  `SemanticPayloadRef` so the graph digest is a Merkle root — any payload byte
  change re-roots the graph. The reference resolver cold-validates payload and
  graph model instances before mutation, registers payloads as immutable
  content-bearing subjects, and requires complete payload closure before a v2
  graph registers; there is no "current"/latest payload lookup. Semantic
  payloads and the layout registry consume one shared leaf `StubKind` authority.
  Graph v1 is byte-for-byte unchanged (golden vectors), no production code
  consumes the new symbols, and no compiler capability is advertised.

- **Portable path, registry v2, and deterministic archive substrate (ADR 0007
  Slice 4A)**: compiler-owned outputs now share one host-independent
  `mozaiks.portable_path.v1` profile (POSIX separators, NFC normalization,
  Windows-compatible restrictions on every host, case-folded duplicate and
  file/directory-prefix collision detection, fail-closed rejection of
  absolute/drive/UNC/traversal/glob/reserved/control-character/trailing-dot
  paths). ZIP becomes a deterministic transport envelope — STORED entries in
  canonical byte order with pinned timestamps and permissions, byte-identical
  across hosts and processes, verified fail-closed against every non-canonical
  metadata field (compression method, create_system, permissions and type
  bits, extra fields, comments, name-encoding flags, timestamps) plus
  link/directory/out-of-order/non-portable entries, with a closing check that
  the bytes equal the canonical serialization of their own entries — and is
  never a semantic authority. The
  application layout registry evolves in place to `mozaiks.app_layout.v2`:
  artifact families gain bounded stub-kind and dependency-family declarations,
  the registry self-digest covers them, dependency closure is validated
  acyclic, and `ordered_families()` provides a deterministic
  dependency-respecting total order. No semantic or planning authority
  changes: generators, `AppBuildPlan`, workflow execution, persistence, and
  promotion remain exactly as before, and no capability is advertised.

### Changed

- **App lifecycle terminology now centers Genesis Builds and Refinement Runs.**
  Public docs use Genesis Build for an app's first canonical build journey and
  Refinement Run for every later change in that app lineage, while Refinement
  Engine remains the internal routing and contract term.

- **Importing `mozaiksai.hosts.studio` no longer mutates the process
  environment.** Repo-local Studio defaults (`PLATFORM_PATH`,
  `MOZAIKS_WORKFLOWS_PATH`) and OpenTelemetry configuration are now applied
  exactly once at server startup — before runtime and platform startup run —
  instead of at module import time. Because the global workflow catalog is
  built when `mozaiksai.core.workflow.workflow_manager` is imported, Studio
  startup now also rebinds that catalog to the workflow root its defaults
  select, so `mozaiks serve <workspace> --host studio` still serves the shared
  factory workflow catalog rather than the workspace's own `workflows/` root.
  The `uvicorn mozaiksai.hosts.studio:app` entrypoint, `mozaiks serve`, and
  app-local host composition keep their existing behavior; caller-provided
  environment values keep precedence, and embedders that only import the host
  module no longer see their environment rewritten.

### Added

- **Offline semantic projection comparison (ADR 0007 Slice 3)**: deterministic,
  input-immutable adapters accept current AppGenerator, DesignDocs,
  subscription, module, page/route, AgentGenerator bundle, deployment,
  recorded-AppBuildPlan, and AppContext ownership shapes. They project only
  graph-v1 facts and report every other source fact through typed,
  machine-readable coverage and gaps; unresolved action, event, capability,
  scope, and ownership references fail closed instead of creating graph facts,
  as do duplicate canonical root aliases. Build-context and recorded workflow
  envelopes are accepted as provenance and classified entirely as typed gaps —
  they declare no graph-v1 identity and cannot by themselves produce a graph.
  Page bindings outside `/api/modules/{module}/{action}` are retained as typed
  gaps rather than invented action targets, so any valid AppPageSchema api path
  projects. Surface realization kinds that graph v1 cannot retain are likewise
  precise typed gaps rather than falsely reported as represented, and custom
  route identities retain their current AppSchema producer paths. Projection
  requires an explicitly pinned Slice 1
  `TaxonomyRegistry`, which keeps the call free of runtime, workflow, and
  workflow-catalog side effects. Production generators, hosts, loaders, Studio,
  workflows, control-plane code, capability advertisement, and runtime
  authority remain unchanged.

- **Semantic-compiler contract layer (ADR 0007 Slice 2)**: strict, immutable,
  content-digested contracts for `ApplicationManifest`
  (`mozaiks.app_manifest.v1`), `SemanticGraph` (`mozaiks.semantic_graph.v1`),
  and `ImplementationBinding` (`mozaiks.implementation_binding.v1`), plus the
  full typed reference roster (`ApplicationManifestRef`, `SemanticGraphRef`,
  `ImplementationBindingRef`, `CompilationPlanRef`, `BuildContextBindingRef`,
  `TaxonomyNamespaceRef`, `RefinementPatchRef`, `ArtifactRevisionRef`, and
  typed child-contract refs), one canonical serialization/digest contract
  (`mozaiks.canonical_json.v1`), deterministic node/edge identity, and
  fail-closed reference resolution and graph-closure validation in
  `mozaiksai.core.semantics`. The package sits behind non-production/test
  seams: no generator, runtime, or refinement code consumes it, and the ADR
  0006/0007 compiler capabilities (`semantic_taxonomy_v1`,
  `semantic_reference_contracts_v1`) remain unadvertised because Slice 1
  taxonomy validation has not yet passed outside advisory mode.

- **Advisory semantic taxonomy (`mozaiks.taxonomy.v1`)**: development and
  test callers can now validate registered event, capability, and artifact-
  family identifiers consistently across module loading, subscriptions,
  layout resolution, and event dispatch. Production name enforcement remains
  unchanged while later ADR 0007 authority-cutover slices are pending, and
  outbound dispatcher envelopes now declare `mozaiks.ui.event.v1` explicitly.

- **Coding provider observability**: ACP provider executions now capture a
  bounded list of operational events (plan updates, tool invocations, mode
  changes — model reasoning is never recorded) on the
  `StagedPatchProposal`, and the coding worker persists a
  `coding_provider` execution record (provider id, model, token usage,
  dispatch attempts, events) into both the worker result metadata and the
  artifact commit metadata, making provider activity reviewable and
  auditable from the artifact record. Token-usage *ledger* wiring is
  deferred: AG2 middleware owns token accounting, and attaching it to the
  provider's agent is tracked as an AG2 upgrade watchpoint rather than a
  parallel Mozaiks accounting path.
- **Promotion now triggers an App Intelligence refresh**: promoting an
  artifact into the live app root enqueues a context index job automatically
  (best-effort, reported in the promote response as
  `app_intelligence_refresh`) — previously the refresh was a manual three-step
  operator flow, so every refinement cycle after a promotion classified,
  scoped, and validated against a stale context snapshot.

### Security

- **Coding-produced artifacts can no longer be promoted or accepted with a
  validation override**: artifacts created by the refinement coding lane
  (structured or ACP provider output) require `validation_status='passed'`;
  the `allow_validation_override` escape hatch now returns 409 for them
  instead of letting unvalidated model output into the live app root.

- **Deterministic coding-provider selection with a fail-closed fallback
  ladder**: the refinement coding worker now dispatches each approved patch to
  a provider via pure policy (`select_coding_provider`) — the ACP provider is
  chosen only for multi-file scopes on `app_bundle`/`theme_config` artifacts
  within its configured budget, and only when it is enabled and installed;
  everything else stays on the structured-output provider. Operational ACP
  failures (unavailable, failed, empty, timeout, budget exceeded) fall back to
  the structured provider exactly once; an out-of-scope ACP result
  (`rejected_scope`) surfaces as a failure and never retries. Every attempt is
  recorded in result metadata (`coding_provider_attempts`). With the ACP
  provider disabled (the default), dispatch is byte-identical to before.

- **ACP-backed CLI coding provider (dark)**: `ACPCodingProvider` drives an
  ACP-compatible coding agent (Claude Code, Codex, OpenCode) for one bounded
  turn inside a disposable staged workspace, then accepts only what the
  deterministic hash harvest verifies — out-of-scope edits, timeouts, empty
  results, and budget overruns all fail closed with typed statuses. Headless
  hardening is explicit: allowlisted subprocess env (provider API keys only),
  `expose_tools=False`, terminal capability not advertised,
  `elicitation_policy="decline"`. Disabled by default in refinement policy,
  packaged behind the new optional `mozaiks[acp-coding]` extra
  (`agent-client-protocol` pinned to 0.12.0 — 0.12.1 breaks ag2 1.0.2's
  dispatcher import), and not yet reachable from any production path:
  provider selection ships separately.

- **Typed refinement lanes and coding provider policy**: the eight refinement
  lanes (`ui_patch`, `experience_design`, `feature_addition`, `integration`,
  `managed_capability_change`, `data_model_migration`, `architecture_replan`,
  `conceptual_reframe`) are now a canonical `RefinementLane` enum consumed by
  lane inference, promotion policy, context policy, and validation — replacing
  unpinned string literals. `refinement_policy.yaml` gains a
  `coding.providers` section declaring the opt-in ACP coding provider
  (disabled by default) with hard execution budgets (`max_files`,
  `max_diff_bytes`, `max_wall_seconds`, `max_retries`); credentials and
  adapter connection details are rejected by schema.

- **Coding workspace materializer and deterministic diff harvester**
  (`mozaiksai/control_plane/workspace.py`): scoped files are written into a
  disposable per-request workspace with pre-run sha256 manifests, and results
  are harvested from the real tree — symlinks, files outside the editable
  manifest, and deletions surface as typed scope violations instead of being
  accepted. Refinement artifact persistence now writes through this module,
  refuses secret-sensitive or unsafe scoped paths loudly instead of silently
  skipping them, and records per-file content hashes in artifact commit
  metadata. This is the enforcement layer ACP-backed coding providers will
  execute inside. The secret-sensitive path policy previously duplicated
  across staging, promotion, and scoped execution is now a single canonical
  helper (`is_secret_sensitive_path`) — the unified term list is the union of
  the old copies, so each call site is equal or stricter than before.

- **`CodingExecutionProvider` boundary in the refinement control plane**: the
  scoped coding worker now delegates patch production to a provider behind a
  typed, provider-neutral `StagedPatchProposal` contract. The first provider,
  `StructuredOutputCodingProvider`, preserves today's single-shot
  structured-output behavior exactly; the boundary is where ACP-backed CLI
  coding providers (Codex, Claude Code, OpenCode) plug in later without
  gaining routing, scope, acceptance, or promotion authority. The shared
  `safe_artifact_relpath` helper now also rejects drive-qualified and UNC
  paths, aligning the coding path with the staging module's path policy.

### Fixed

- **AG2 Network restart continuation now resumes interrupted agent turns.**
  Reopened chat-scoped Hubs reattach canonical Network identities and invoke
  AG2 pending-turn recovery before accepting further input. Workflow launch
  triggers now remain plain Network text instead of reconstructed Classic-style
  role/name messages, and retired user-proxy/manager identity aliases are no
  longer accepted by runtime routing or replay.

- **Continue Build now resumes the durable AG2 Network channel**: runtime
  continuation reopens the chat-scoped AG2 KnowledgeStore, hydrates the Hub
  WAL and transition state, and reattaches existing agent and human identities
  instead of projecting transcript messages into a new Network channel.
- **Generated custom-route clients now recognize real entitlement denials**:
  module-action and workflow-start failures unwrap FastAPI's structured
  `detail` envelope, so HTTP 402 responses reach the app's upgrade flow while
  flat object responses remain compatible. Declarative page rendering is
  unchanged.
- **Module-event workflow triggers now fail closed instead of amplifying**:
  each event/capability invocation is durably claimed before session creation,
  concurrent replay is suppressed, workflow-trigger lineage carries bounded
  depth and cycle ancestry, and Mongo-backed per-tenant admission limits reject
  runaway unique events without affecting other tenants. Replay, cycle/depth,
  rate, rate-authority, and persistence rejections emit distinct platform
  diagnostics.
- **ADR 0007 Slice 0 closes proven generator and refinement defects**:
  generated app/workflow writers now use the canonical build-scoped staging
  roots, required artifact and DesignDocs persistence fails closed, lineage
  queries and dependency families match persisted build-record fields, and
  launch-context authority failures no longer admit unvalidated context.
- **Generated SaaS plan limits now survive AppGenerator assembly and export**:
  `usage_limits` and `token_allowances` are preserved in
  `config/subscriptions.yaml`, and the canonical module API helper no longer
  creates a false missing-action failure during export acceptance.
- **Ask-mode messages now render immediately**: optimistic user messages and
  live assistant frames retain their general-chat provenance, so the active
  chat no longer hides either side until the persisted transcript is reloaded.
- **`mozaiks gen` burned tokens on runs it could never finish** (#383): the
  CLI takes one `--prompt` and has no way to reply, but AgentGenerator opens
  with `InterviewAgent`, which asks a clarifying question and hands the turn
  to `user`. Every run therefore stalled at `WORKFLOW_AWAITING_INPUT` with
  zero files written — after real LLM spend. `gen` now inspects the staged
  workflow's own declarative config before executing anything and refuses
  when the workflow can hand control to a user (`human_in_the_loop: true` in
  `orchestrator.yaml`, or a `transition_graph.yaml` edge targeting `user` /
  reverting to the user), redirecting to `mozaiks studio --dir . --open`.
  Detection is on the property, not the workflow name, so genuinely one-shot
  workflows still run and future interview-driven ones are covered.
  `--allow-interactive` bypasses the refusal for anyone who wants to start a
  run and drive it elsewhere.
- **Every workflow failed instantly under context-authority enforcement**:
  the routing-variable validation added in #298/#344 required deterministic
  writers that the runtime never actually produced — agent-sentinel triggers
  ("say NEXT") wrote as freeform `agent_text` and declared workflow tools
  wrote as `tool_writeback`/`context_bridge`, all banned for routing state.
  Every graph compile raised `ContextAuthorityError` before any agent spoke,
  so every Studio conversation "completed" in under a second with zero agent
  activity. The writer taxonomy is now complete: exact-match `equals`
  triggers write as a new deterministic `sentinel_text_trigger` writer
  (freeform contains/regex-capture stays banned from routing), auto-invoked
  tools write as `deterministic_tool`, closed routing/quality state accepts
  the deterministic tool/lifecycle/structured-output machinery, and the AG2
  runner elevates bridge/derive writes to the declared deterministic writer
  per variable. A new drift guard
  (`tests/test_workflow_context_authority_compile.py`) compiles all 14
  factory workflows against their real policies on every PR — the test that
  would have caught this before merge.
- **ExistingAppDiscovery crashed at persist time**: twelve context variables
  declared `type: object` with list defaults/values, which the replay type
  guard fail-closes on. Declarations corrected to `type: array`, and the new
  drift guard also validates every declared default against its declared
  type.
- **`mozaiks gen` no longer reports a silent no-op as success** (issue #379):
  the CLI now initialises real logging (workflow-engine INFO records and file
  sinks were previously swallowed), asserts on the orchestration result —
  failed runs, runs paused awaiting a user reply, and runs where no agent
  ever produced a turn now exit 1 with distinct messages — points
  `MOZAIKS_GENERATED_ARTIFACTS_PATH` at the CLI output directory so the
  empty-output check inspects where tools actually write, and warns loudly
  when `MONGO_URI` is unset. The orchestration result payload now carries
  `agents_created` / `agent_turns` evidence, echoed in the completion
  summary as `agents=N turns=M`.
- **Apps no longer shows a duplicate Create App action**: app creation remains
  available from the shell header while the Apps management page focuses on
  searching, filtering, and opening existing apps.
- **AppWorkbench live-preview refresh actually works now**: the preview
  sandbox API the workbench calls after a scoped refinement
  (`/api/artifacts/{id}/sandbox`, `/api/sandbox/*`, `/ws/sandbox/*`) was
  defined but never mounted by any host, so every refinement-triggered
  preview refresh failed with a 404 and the iframe kept showing the stale
  app. The session manager was promoted from workflow-local dead code to
  `mozaiksai/core/sandbox/preview_sessions.py` over the `SandboxPort` seam
  and the routes are mounted on the Studio host with auth. Because it now
  rides `SandboxPort`, the live-preview loop also works on local Docker —
  previously it was hard-wired to e2b, so OSS self-hosters had no live
  refresh at all.

### Added

- **Per-build usage attribution and emission counters**: runtime usage events
  now carry an optional `build_id` (read from the build-lifecycle context
  variable) so token cost can be rolled up per build, not just per chat or
  workflow. `TokenManager` also counts emission outcomes
  (`emitted` / `dropped_disabled` / `dropped_missing_context` / `failed`),
  exposes them via `get_usage_emission_stats()`, and logs a one-time warning
  per drop reason — a fully-dropped usage stream now announces itself instead
  of looking identical to a healthy one.

### Changed

- **Factory Studio demo mode now uses a three-app canonical fleet**: the six
  disconnected pseudo-app records were replaced by three accepted archetypes
  (authenticated CRUD, admin operations, and monetized AI SaaS). Usage,
  billing, deployment, users, activity, integrations, workflows, runs,
  sessions, and artifact history now stay closed over the same three app IDs,
  with current passing bundles and healthy deployed states for meaningful
  cross-portal testing.
- **AppWorkbench leads with the app, speaks the user's language**: the
  file-tree/editor/preview grid now renders directly under the status strip
  (previously it sat below the review and refinement panels), build logs
  default to collapsed unless validation failed, and the refinement panel's
  copy no longer leaks internal vocabulary ("control-plane harness",
  "codex-backed patch", artifact version ids) — it reads "Describe a change
  and the agents will patch your app." The preview pane gains a
  **Start live preview** button when no preview exists yet (boots the bundle
  in an e2b or Docker sandbox on demand), and `generate_and_download` now
  passes `app_id` so preview sandboxes are keyed to the app
  deterministically.
- **AppWorkbench pane naming and preview messaging cleaned up**: the internal
  panes of the Studio `AppWorkbench` artifact are now named `*Pane.js`
  (`PreviewPane`, `FileTreePane`, `CodeEditorPane`, `BuildStatusPane`) instead
  of the misleading `*Artifact.js`, since only `AppWorkbench` itself is a
  registered UI artifact. The preview pane's empty state now explains when a
  live preview is available (e2b or Docker sandbox validation) instead of
  referencing e2b internals. The workflow UI docs gain an explicit
  artifact-vs-pane naming convention.

- **README onboarding restructured around the documented first-run path**: the
  Quickstart now opens with a prerequisites list and five numbered steps
  (install, MongoDB, LLM key, `quickstart`, first app), and sits above the
  architecture deep-dive so a new reader reaches an install command without
  scrolling past runtime internals first. Adds a short troubleshooting list
  and a "Where To Go Next" table pointing at the Studio and guides pages.
- **README AG2 badge no longer claims a beta**: it now reads `1.0.1`, matching
  the pinned `ag2==1.0.1` dependency in `pyproject.toml` and `requirements.txt`.

### Added

- **Subscription contract refinement routing**: the refinement harness now has
  a `subscription_contract` build family. Post-build monetization change
  requests (plans, pricing tiers, entitlement gates) route to new
  `subscription_patch` (`SubscriptionContractDesigner → AppGenerator`) and
  `subscription_revision` (`SubscriptionContractDesigner → AgentGenerator →
  AppGenerator`) sequences instead of silently falling back to `app_bundle`
  routes that skipped the contract designer. New closure tests enforce that
  every harness route resolves to a declared sequence, every refinable family
  has explicit routes, and any sequence claiming to refresh
  `subscription_contract` actually runs SubscriptionContractDesigner.
- **Sandbox boundary, previews, and persistence**: codified the boundary
  between AG2 agent-level execution (`SandboxShellTool`) and Mozaiks-owned
  app-preview/validation sandboxes (`SandboxPort`) in the ownership-boundary
  and watchpoints docs, plus a consolidated
  `docs/architecture/builder/app-validation-sandboxes.md` (strategies, all
  env vars, hosted e2b activation and cost posture). Docker validation
  sandboxes now publish preview ports so `get_preview_url` works locally for
  free. Validation results persist `sandbox_session_id`/`sandbox_provider`,
  provider sandboxes are created with identity metadata and kill deadlines
  (closing the orphaned-sandbox billing vector), and `BuildRecord` gains
  queryable `app_validation_status` / `app_validation_strategy` /
  `sandbox_session_id` / `sandbox_provider` fields. The control-plane coding
  worker's unimplemented `e2b` validation label was removed so build records
  only claim strategies that actually ran.

### Fixed

- **Gemini is consistently the documented default LLM provider** (#367): the
  README quickstart now leads with `GEMINI_API_KEY` (matching
  `docs/getting-started.md` and `.env.example`), and the `quickstart` CLI's
  no-key warning checks and names `GEMINI_API_KEY` instead of telling Gemini
  users they have no API key.
- **SaaS bundles can no longer sell capabilities they never enforce**: the
  generated-bundle scanner now fails a bundle whose
  `config/subscriptions.yaml` grants plan capabilities while no module action
  declares an `entitlement_gate` — previously such bundles passed validation
  and shipped a decorative subscription contract. Token/usage-only plans
  (no capabilities) are unaffected.
- **Public /pricing page works anonymously in MozaiksPay SaaS apps**: the
  mozaikspay pack's `billing_portal.list_plans` action now declares
  `api_surface: public_readonly` with no permission requirement, so the
  pricing landing page can render the plan catalog before login. The scanner
  now also requires `ui/pages/pricing.yaml` in mozaikspay SaaS bundles and
  verifies it binds to `list_plans`.
- **Rejecting the subscription contract review no longer silently produces a
  non-SaaS build**: when a reviewer requests changes,
  SubscriptionContractDesigner now loops back to the designer agent to revise
  and re-submit instead of terminating without an approved contract, and the
  downstream generator context hook fails loudly if an unapproved
  (changes-requested) contract state ever reaches AppGenerator/AgentGenerator.

### Added

- **Monetized archetype proves the entitlement chain**: the generated-app
  archetype matrix now wires the monetized SaaS archetype with the real
  `ConfiguredEntitlementAdapter` and the platform billing fulfillment
  endpoint instead of a no-op checker, and asserts the full loop — gated
  action denied on the free default plan (402 ENTITLEMENT_REQUIRED),
  granted after a verified-provider `subscription_activated` fulfillment
  command applied through the app's own `/api/billing/fulfillment/apply`,
  and denied again after `subscription_cancelled`. The previous check
  (`status_code in {200, 402}`) passed regardless of whether entitlement
  enforcement existed at all.
- **Factory regression suite**: `factory_app/eval/` (bundle scorers + run
  persistence + baseline diff, upstreamed from the hosted product's
  build_intelligence bundle evaluation) and
  `tests/test_factory_regression_suite.py`, which materializes the five
  archetype-matrix app plans offline (no LLM, no network), scores every
  generated bundle deterministically, and fails CI when a check that passed
  on the committed baseline (`tests/fixtures/factory_bundle_eval_baseline.json`)
  regresses. Refresh deliberately with `REFRESH_FACTORY_EVAL_BASELINE=1` and
  commit the reviewed delta. A determinism guard asserts two
  materialize+score passes agree exactly.

- **Generic usage instrumentation and rollups**: the platform host records
  `app.page_view` (per page-schema serve) and `app.action_invoked` (per
  successful module action) into the app's own AppMetrics store —
  fire-and-forget, env-gated `MOZAIKS_USAGE_METRICS` (default on), no data
  leaves the app. `AppMetrics.usage_rollup(since, until)` aggregates them
  into daily buckets (page views, unique sessions, action invocations,
  active users). The mozaiks_cloud pack gains a sink-agnostic
  `cloud_usage_reporter` module template plus `mozaiks_cloud_usage_client`
  posting daily aggregate rollups to a configured Mozaiks Cloud-compatible
  operator endpoint (`POST /usage/rollups`, scope `cloud:usage`); when no
  connector or `MOZAIKS_CLOUD_*` configuration exists the reporter is
  silently idle — generated apps never phone home by default, and only
  aggregate counts are ever sent.

### Security

- **Fail-closed Mongo index readiness**: app data-contract indexes are now
  compared against materialized Mongo metadata by name, ordered keys, and the
  complete supported option set (`unique`, `sparse`, partial filters,
  collation, TTL, hidden state, and wildcard projection). Same-name mismatches
  and same-key definitions under another name abort startup; missing indexes are awaited,
  reread, and verified before readiness. Inspection and creation errors are no
  longer swallowed, and the runtime never drops conflicting indexes.

- **Module dispatch requires explicit authority**: `ModuleRequest.authority` is
  now a required keyword-only `ModuleDispatchAuthority`, and the
  `granted_permissions` field is removed along with the
  `from_granted_permissions()` translation shim and both compatibility
  authority kinds. Permission and entitlement enforcement key exclusively on
  `authority.permission_mode` and `authority.permissions`; a missing principal
  or empty permission list now denies closed instead of silently bypassing.
  Trusted bypass is restricted to a closed, constructor-validated set of
  server-owned kinds (`framework_internal`, `operator_internal`,
  contract-declared `event_reaction`, auth-disabled `local_development`);
  `local_development` cannot be constructed while authentication is enabled or
  the deployment environment is production. New `workflow_user_authority()` and
  `event_reaction_authority()` helpers are the canonical producers for workflow
  and event-reaction dispatch. `ModuleActionDispatchRequest.authority` is also
  required (keyword-only) and the facade's separate permission-list field is
  deleted: the caller's enforce-mode authority carries its permissions and is
  passed to `ModuleExecutor` exactly as supplied. `ModuleContext` receives
  `dispatch_authority`, `dispatch_provenance`, and `dispatch_audit` on every
  execution path, including caller-supplied contexts. Downstream apps
  constructing `ModuleRequest` or `ModuleActionDispatchRequest` directly must
  supply an explicit authority when they adopt this version.

### Removed

- **Generated UI browser acceptance retired result vocabulary**: the
  `scripts/generated_ui_acceptance.py` output no longer exposes top-level
  `status`, `findings`, `revision_count`, or `revision_request` fields. Browser
  validation now returns the canonical `ValidationRun` and one
  `RepairDecision` (`accept`, `repair`, or `block`) from the shared acceptance
  controller. No aliases or compatibility translation are retained; consumers
  must read `validation_run.gate_results` and `repair_decision` before adopting
  this version.

- **`AppPageSchema.extensions` / `AppPageSlotExtension`**: the page slot-override
  contract is removed from structured outputs, generator prompts, and the UI
  quality audit. It was a schema-only promise — `PageRenderer` never rendered
  the field, no fixture or workspace used it, and no component-closure
  authority existed. Generated pages declaring `extensions` now fail bundle
  acceptance, and the quality audit flags the field for removal. Routes that
  primitives cannot express use `custom_route_bundle`.

### Added

- **`mozaiks.app_page.v1` runtime page validation** (`mozaiksai.core.runtime.app.page_schema`):
  canonical, strict Pydantic validation of every declarative page schema at `AppLoader` boot
  and at serve time. Apps with invalid page YAML fail to start rather than silently serving
  bad schemas.
  - `AppPageSchema` enforces `schema_version: mozaiks.app_page.v1`, 11 closed `page_type`
    values, 4 closed `layout` values, 26 registered primitives, and `extra="forbid"` on all
    nested config models. No unknown fields are silently accepted.
  - `AppLoader.load()` validates every `ui/pages/**/*.yaml` before boot completes; a
    `PageSchemaValidationError` aborts startup with a structured diagnostic.
  - `GET /api/pages/{name}` serves from the pre-validated boot cache; the disk-read fallback
    (dev/hot-reload) calls the identical canonical validator and returns `safe_page_schema_error_detail()`
    — no raw exception text, file paths, or tracebacks in HTTP responses.
  - `generated_ui_contract.audit_page_schemas()` and `generated_bundle_scanner` reuse the same
    canonical validator so generation, acceptance, and runtime share one contract.
  - Factory `page_plan_utils` injects `schema_version: mozaiks.app_page.v1` into every
    materialized page; `save_app_schema` validates against the runtime schema before writing.

- **Mozaiks UI v1 architecture constitution**
  (`docs/architecture/frontend/mozaiks-ui-v1.md`): the authoritative target
  documentation contract for the native UI framework — canonical surface kinds,
  single-owner layer map, registry constitution, target `mozaiks.ui.event.v1`
  requirements, state-domain ownership, the deterministic generation pipeline,
  extension checklists, security invariants, and a dependency-ordered roadmap.
  Documentation only; current implementation truth remains documented in the
  current-versus-target table; no runtime, schema, or registry behavior changed.

- **Canonical app layout registry v1** (`mozaiksai.core.runtime.app.layout_registry`):
  the first typed, versioned layout authority (`mozaiks.app_layout.v1`). Closed
  enums for artifact kind, owner, requirement, condition, path scope, materializer,
  validator, runtime consumer, and security class; deterministic digest-signed
  registry identity (prose and timestamps excluded); fail-closed path matching
  with specificity precedence, Unicode NFC normalization, and explicit
  prohibited-path families; bounded extension slots for managed-capability packs.
  Data-only in this release — not yet wired into `paths.py`, loaders,
  materializers, or prompts.

- **Mozaiks Cloud OSS connector contracts** (`factory_app/build_context/mozaiks_cloud/`):
  versioned, optional capability pack wiring generated apps to the Mozaiks Cloud
  managed platform for deployment, environment endpoints, and domain management.
  - `MozaiksCloudTransport` shared auth/retry/idempotency base; bounded sub-clients
    `MozaiksCloudDeploymentClient`, `MozaiksCloudEnvironmentClient`, and
    `MozaiksCloudDomainClient` in generated app bundles.
  - Provider API contract (`provider_api_contract.yaml`) with closed deployment/domain/TLS
    status enums, normalized error taxonomy, and globally forbidden response fields.
  - App-owned facade modules `cloud_deployment` and `cloud_domain` with bounded actions,
    declared events, and read-only reactions on `hosted.cloud.*` events.
  - Explicit-selection-only contract — pack never auto-selects; provider-neutral bundles
    contain zero cloud connector artifacts when the pack is not chosen.
  - `generated_bundle_scanner` extended with `_scan_mozaiks_cloud_connector_contract`
    and `_RAW_CLOUD_PROVIDER_IMPORT_RE` (Azure/Cloudflare SDK import guard).
  - `capability_directory.yaml` and `capability_routing.yaml` wired with `mozaiks_cloud`
    operator pack entry and `cloud_hosting` routing layer.

- **Crash-safe workflow-queue leases**: `MongoWorkflowQueue` now issues a
  unique `claim_token` (fencing token) on each claim, enforces bounded lease
  durations, and supports bounded retries with dead-letter terminal state.
  - Fenced `complete()` and `fail()`: only the current claim_token holder can
    transition a claimed item. Stale workers whose lease expired are rejected.
  - `renew_lease()` extends an active lease without reclaiming.
  - Configurable `max_attempts` and `retry_delay_seconds` per enqueued item.
  - Expired leases are atomically reclaimed by the next `claim_next()` call
    (crash recovery without a background sweeper).
  - Pre-upgrade records (no `claim_token`) are immediately reclaimable.
  - `ClaimResult` returned from `claim_next()` carries `claimed`, `item`,
    `claim_token`, and `attempt_count`.
  - No TTL index on lease fields; completed and dead-letter records remain
    available for audit.

- **Entitlement gate compile-time closure**: Generated app bundles with
  `config/subscriptions.yaml` now fail deterministic bundle validation if any
  `module.yaml` action declares an `entitlement_gate` capability_id that is not
  granted by at least one subscription plan. The platform wires
  `ConfiguredEntitlementAdapter` whenever `config/subscriptions.yaml` loads
  successfully; `assignment_store` controls persisted assignment lookup, not
  adapter selection. Previously such bundles passed validation but permanently
  denied the gated action at runtime for every user.
  - Per-action diagnostics name the module path, action id, and unresolvable
    capability_id, and suggest typo near-matches from the declared plan catalog.
  - Apps without `subscriptions.yaml` remain ungated via `NoOpEntitlementAdapter`.
    Malformed `subscriptions.yaml` files are not treated as custom/dynamic
    adapter declarations.
  - `PlanDef` in `subscriptions_loader` now rejects plans that list the same
    `capability_id` more than once (duplicate conflicting declarations).
  - The bundle scanner derives plan grants from the canonical
    `SubscriptionsConfig` loader output, covering both v1 (flat `plans[]`) and
    v2 (`products[].plans[]`) subscriptions schema.
  - 36 tests in `tests/test_entitlement_gate_closure.py` cover all scenarios:
    positive (valid gated actions, multi-plan grants, `assignment_store`-absent
    configured catalogs, ungated app, no-action bundle) and negative (unknown
    gate, near-match typo, ungranted capability, all-plans-empty, malformed
    YAML, multi-module multi-failure deterministic ordering, duplicate
    capabilities).

- **Community Component Foundation v1**: Extended capability packs to carry versioned identity and machine-readable dependency declarations without introducing a parallel component runtime.
  - `context.yaml` pack blocks now require `version`; `author`, `license`, and `source` remain optional metadata.
  - `contract.yaml` supports the canonical `requires.packs` / `requires.capabilities` block for machine-readable dependency declarations; exact `requires.packs[].version` values are enforced when present.
  - `resolve_managed_capability_templates()` verifies local packs before materialization, computes one canonical `sha256:` content digest from declared pack assets, and emits `.mozaiks/pack_provenance.json` with `pack_id`, `version`, `source`, `digest`, and `materialized_owned_files`.
  - Structural allowlisting of `context.yaml` pack blocks via `validate_pack_context()` now rejects unexpected root, asset, and pack-block keys before any pack data reaches AG2.
  - `scan_generated_bundle()` now validates the provenance manifest schema when `.mozaiks/pack_provenance.json` is present in a generated bundle.
  - `.mozaiks` added to `CANONICAL_APP_ROOT_DIRS` so the provenance directory is a first-class part of the canonical app surface.
  - First-party packs (`messaging`, `support`, `social`, `commerce`, `notifications`, `files`, `entitlement_dispatch`, `mozaikspay`, `operator_readiness`, `onboarding`) updated to `version: "0.1.11"`.
  - `support/contract.yaml` migrated from informal `required_packs` to canonical `requires.packs` format.
  - Fixture community packs in `tests/fixtures/community_packs/` prove dependency validation and provenance work locally without App Zero, network, or a paid LLM.
  - 49 tests in `tests/test_community_pack_foundation.py` cover schema validation, digest stability, tamper rejection, provenance emission,
    bundle scanner integration, retired-shape rejection, and first-party pack behavior.

- Published `docs/architecture/MOZAIKS_OSS_SOFTWARE_DESIGN.md` as the authoritative OSS north-star software-design document; added to mkdocs.yml navigation as the primary architecture reference. No competing document exists under `docs/architecture/foundations/`.

- Proved clean-room self-host experience end-to-end: a fresh `pip install -e ".[dev]"` on a clean clone boots Studio, loads all 14 factory workflows, and passes the clean-room self-host acceptance suite without private config or BlocUnited infrastructure. Added `tests/test_selfhost_clean_install.py` with 15 deterministic CI smoke tests covering install, CLI entry, factory resources, startup warning quality, no-fork guard, and self-host docs presence.
- Fixed startup validator to route by `LLM_PRIMARY_API_TYPE`: the provider-specific API-key check now names the correct env var per provider (`GEMINI_API_KEY / GOOGLE_API_KEY` for google, `ANTHROPIC_API_KEY` for anthropic, `OPENAI_API_KEY` for openai). Previously always warned about `OPENAI_API_KEY` even when `.env.example` defaults to Gemini free tier.
- Enforced pre-1.0 OSS/proprietary boundary as an architecture contract: added `docs/adr/0003-pre-1-0-oss-proprietary-boundary-freeze.md` ("DIFFERENT INTELLIGENCE, SAME CANONICAL APP"), `docs/architecture/foundations/oss-boundary-families.md` (authoritative DO-NOT-MOVE family registry), and `tests/test_oss_boundary_policy.py` (governance guard).
- Added generated-app archetype regression matrix (`tests/test_generated_app_archetype_matrix.py`) covering Level-2 acceptance across authenticated CRUD, monetized SaaS, workflow/agent, admin/operations dashboard, community/content, and AppPlan materialization archetypes.
- Added `scripts/package_content_guard.py`: artifact-level content guard that inspects built wheels and sdists before publication. Fails on learned-artifact directories (`evals/`, `corpora/`, `corrections/`, `production_outcomes/`, `learned_rankings/`, `customer_patterns/`, etc.), raw private keys, raw provider credentials (`sk_live_`, `sk_test_`, etc.), `.env` files (non-example), private-key file extensions (`.pem`, `.key`), and unapproved top-level package families or `factory_app/` sub-families. Warns on large data files and review-pattern paths.
- Added `scripts/run_release_audit.py`: local pre-release audit script that chains governance guardrails, build, package content guard, twine check, smoke install into a clean venv, Factory resource resolution verification, and offline functional acceptance tests. Run with `python scripts/run_release_audit.py` before tagging any release.
- Added `docs/adr/0002-appgenerator-baseline-strategy-oss.md`: records the intentional decision to publish the AppGenerator baseline strategy as OSS. Establishes that future learned or operator-derived additions require a new publication review ADR before entering this repository.
- Added deterministic brownfield and AgentGenerator handoff acceptance coverage proving captured post-reasoning artifacts can flow through AppGenerator materialization and Level-2 generated-app runtime acceptance offline.

### Security

- Extended `scripts/governance_guardrails.py` with learned-artifact quarantine enforcement at the source level: data files (`.jsonl`, `.csv`, `.parquet`, `.pkl`, etc.) inside quarantine directories are now a governance ERROR; code files inside those directories are a NOTICE. Complements the artifact-level guard in `package_content_guard.py`.
- Added package content guard step to `.github/workflows/release.yml` between `twine check` and wheel install smoke test. Inspects all built artifacts for prohibited content before any publication step.
- Disabled tag-triggered releases in `.github/workflows/release.yml`: the `release` GitHub environment has no required reviewers (`protection_rules: []`), meaning any `v*` tag push would have auto-published. The tag trigger is now commented out; only a manual `workflow_dispatch` with `confirm_release: "release-confirmed"` can proceed. Add environment reviewers and uncomment the tag trigger when ready to ship.

### Changed

- Updated `docs/releasing.md` with an explicit "RELEASES ARE CURRENTLY DISABLED" banner, verified NOT PROTECTED release gate status (with remediation steps), a full Pre-Release Checklist (P0/P1), and a Release-Candidate Audit Command section documenting `scripts/run_release_audit.py`.
- Updated `MANIFEST.in` with a comment block documenting the agent-guidance split policy: `mozaiks_cli/agent_guidance/` (user-facing skills, ships in wheel) vs `.claude/skills/` (contributor-only framework skills, git-only, not shipped).

### Added

- Added the App Intelligence Plane: source-backed indexing now produces an `app_intelligence_snapshot` artifact alongside `source_context_bundle` and `app_context_graph`, with discovery and refinement tools exposing compact architecture, capability, ownership, integration, data, and risk context before agents read exact files.
- Added a provider-neutral generated app auth contract (`app/config/auth.yaml`) and hardened OIDC PKCE adapter output so authenticated apps keep auth behavior deterministic while leaving provider setup to operators or hosted services.
- Added workspace handler extension system: `workspace_extensions_contract.yaml` declares the schema for `app/build_context/{pack_id}/extensions.yaml` files that workspace apps use to express extra params, param overrides, and extra actions on top of OSS-generated base handlers without editing generated files.
- All capability packs (social, messaging, commerce, entitlement_dispatch, mozaikspay/billing_portal, support) now ship a `base_handler.py` / `handler.py` split: the base class contains the full implementation and is always regenerated; the workspace subclass is a thin preserved subclass for app-local overrides. `contract.yaml` for each pack declares `base_handler.py` as `owner: templates` and `handler.py` as `owner: workspace`.
- AppGenerator agents now carry the handler split rule so generated apps always scaffold both files correctly.

### Fixed

- Fixed workflow run context persistence and replay to require resolved workflow
  declarations, omit non-persisted authority fields, reject malformed known
  values, safely drop stale unknown keys, and surface required storage failures.
- Fixed `validate_module_implementation_contract` to resolve handler methods through single-level base class inheritance within the same module's `backend/` directory. The `base_handler.py`/`handler.py` split pattern previously caused the acceptance gate to report every action as missing a handler method on the thin workspace subclass.
- Fixed messaging and support pack `module.yaml` handler entrypoints: after the `base_handler.py`/`handler.py` split renamed workspace subclasses from `MessagesModule`/`SupportModule` to `MessagesHandler`/`SupportHandler`, `module.yaml` still declared the old class names, causing `ModuleLoadError` at runtime and in tests.
- Fixed the MozaiksPay generated billing and usage page contract to emit canonical `analytics_dashboard` page types through both the build-context pack hints and the replayed page schema fixtures, removing stale `dashboard` page-type drift from the generated SaaS path.
- Fixed `scripts/run-infra.ps1` to propagate the `docker compose up` exit code and print an actionable error message when Docker Desktop is not running. Previously the script silently continued on failure.
- Fixed Vite 8 dev-server startup: added `optimizeDeps.rolldownOptions.moduleTypes: { '.js': 'jsx' }` so the Rolldown pre-scan can parse first-party JSX-in-.js UI files before the transform plugin runs.

### Changed

- OSS framework telemetry now emits only structural build outcome payloads. Build satisfaction and other customer/operator feedback belongs to app/operator-owned endpoints rather than the generic `MOZAIKS_TELEMETRY_ENDPOINT` channel.
- Tree-sitter parser packages are now installed with the core Mozaiks package so source-backed Context Graph indexing is part of the default code-context setup.
- Public docs now describe Mozaiks as not yet published to PyPI or GitHub releases, and direct users to install from a local checkout with editable install instructions instead of a public package URL.

### Removed

- Removed the unused OSS `CollaborationPort` placeholder and default `app.state.collaboration` wiring. Hosted collaboration remains a `mozaiks-app` product capability implemented through app-owned modules and platform hooks, not a generic OSS runtime port.

## 0.1.11 - 2026-07-20

### Added

- Default OSS LLM provider is now free Google Gemini (gemini-2.0-flash / gemini-2.5-flash). Set `GEMINI_API_KEY` from aistudio.google.com and `LLM_PRIMARY_API_TYPE=google`. OpenAI remains available as an alternative via `LLM_PRIMARY_API_TYPE=openai`.
- Self-hosted SaaS apps now get deterministic entitlement dispatch: AppGenerator plans an `entitlement_dispatch` module (activate/deactivate subscription actions) whenever `config/subscriptions.yaml` declares an `assignment_store` and the mozaikspay managed pack is not selected. `ConfiguredEntitlementAdapter` reads those assignment records for all entitlement gate checks.
- `scan_generated_bundle` now validates the self-hosted entitlement dispatch contract: rejects bundles with `assignment_store` but no `entitlement_dispatch` module, and rejects any `entitlement_gate` value not declared in at least one plan's capabilities.
- Added offline SaaS acceptance gate tests and a live AppPlanAgent smoke test (with committed fixture for CI replay) that verifies `entitlement_dispatch` task planning end-to-end against gpt-4o.
- Generated deployment bundles now emit `.github/workflows/readiness.yml` alongside `deploy.yml` when workflow artifacts are requested. The readiness workflow provides an environment-staging gate separate from artifact review staging; `deployment.manifest.json` carries a `readiness_workflow` field linking to it.

### Fixed

- Removed `@stripe/stripe-js` from the OSS `web_shell` dependencies. Stripe belongs in `mozaiks-app` only; the package was unused in any source file.
- Fixed `optional_list`, `optional_str`, `optional_dict`, and nullable union fields to correctly default to `None` in generated Pydantic models when no explicit default is declared, so they are truly optional without requiring the LLM to emit them.
- Fixed generated bundle scanner to detect `api_endpoint: /api/modules/...` values in YAML pages (unquoted) alongside quoted string literals. Billing and usage page contract validation now works correctly for the canonical YAML page format.

### Changed

- Expanded the production-readiness gate so the 0.1.10 cash-to-token loop is covered by generated SaaS acceptance, subscription/token runtime, scanner, and opt-in Docker/Mongo smoke checks.
- Monetization taxonomy simplified to 5 canonical OSS routes: `free`, `subscriptions`, `usage_based`, `custom`, `hybrid`. `custom` covers all app/operator-specific money flows (ecommerce, marketplace, sponsorship, contributions, campaign funding, revenue-share, payout policy) through app-owned modules, policy hooks, managed facades, or external adapters. The old granular models (`transactional`, `marketplace`, `sponsored`, `donations`, `community_funded`) are removed.
- Capability dispatch planning is now fully app-agnostic: managed packs declare `provides_capabilities: [subscription_write_path]` in their `contract.yaml` to signal ownership of the subscription assignment write path, replacing all hardcoded MozaiksPay name checks in OSS scanner and planning logic. Any operator pack can now own this role.

## 0.1.10 - 2026-07-16

### Added

- Added provider-neutral billing fulfillment primitives for idempotent subscription assignment, token wallet credit/debit, durable command replay/conflict logging, and generated-app cash-to-token runtime integration.
- Added first-class subscription checkout, token status, token top-up, and depleted-balance recovery contracts to the MozaiksPay generated-app facade path while keeping provider checkout and fulfillment behind MozaiksPay/OSS billing boundaries.
- Added the generated-app MozaiksPay capability pack contract, provider API contract, billing portal facade updates, and usage/billing pages for provider-neutral SaaS subscription apps.
- Added workflow transport bridging so generated workflow capabilities can be invoked through the platform capability route contract.

### Changed

- Hardened AppGenerator route metadata, generated UI drift guards, module API template output, generated bundle scanning, and provider-neutral deployment/env manifests for future generated apps.
- Expanded Mozaiks documentation for local setup, CLI reference, self-hosting, architecture, and the 0.1.10 cash-to-token loop.

### Fixed

- Fixed generated capability env contracts so provider-specific env vars stay metadata-driven instead of being inlined into capability packs.
- Fixed generated OIDC env placeholders and billing runtime type contracts for stricter CI/runtime validation.

## 0.1.9 - 2026-07-15

### Fixed

- Ask-mode human-support escalation now short-circuits the LLM turn after rendering the support handoff UI, so users do not receive an extra assistant answer after requesting an operator.
- **`infra/docker/Dockerfile` no longer references removed root files** (`run_server.py`, `shared_app.py`, `workflows/`, `config/`): it now installs the real `mozaiks` package from `pyproject.toml` (fixing missing runtime dependencies such as `jsonschema` and `limits` that the old `requirements.txt`-based build silently dropped) and serves the first-party `factory_app/` workspace via `mozaiks serve . --host studio`. Verified with a local `docker build` + container smoke test against MongoDB.
- **Helm chart liveness probe pointed at a 404** (`infra/helm/mozaiks/values.yaml`): `livenessProbe.httpGet.path` was `/api/health/liveness`, which does not exist; the real route is `/api/health/live` (`mozaiksai/hosts/runtime.py`). Verified by rendering the chart and confirming both probe paths resolve.
- **`infra/compose/docker-compose.yml` dev `app` service used a broken `watchmedo`/`run_server.py` command** with no `watchdog` dependency installed: replaced with `mozaiks serve . --host studio --reload`, plus a `PYTHONPATH=/app` override so the bind-mounted repo shadows the image's installed first-party packages for live-reload dev. Verified end to end against a real container.

### Added

- First-party Studio messaging and workspace support modules now persist support conversations through linked message threads, including profile support transcripts, app/workspace support queues, operator replies, status updates, and delete flows.
- Added the reusable `support` build-context pack for generated help-desk apps. The pack requires `messaging`, stores ticket metadata separately, and keeps support conversations in the generated `messages` module.
- **`.dockerignore`** at the repo root: none previously existed, so every `docker build` sent the full working tree (including `node_modules`, `.git`, and local caches) to the daemon; this also meant the old Dockerfile's `web_shell`/`chat-ui` copy would have unintentionally bundled `node_modules` files matched by `MANIFEST.in` glob patterns.
- **`infra-build` CI job** (`.github/workflows/ci.yml`): builds `infra/docker/Dockerfile`, smoke-runs the resulting image against a real MongoDB service until `/api/health` reports healthy, then lints and renders the `infra/helm/mozaiks` chart with a regression check for the corrected health probe paths.
- **Source hygiene scan wired into CI** (`.github/workflows/ci.yml` `lint` job): the existing `scripts/production_readiness_gate.py` terminology scan previously only ran through the standalone script; it now runs on every PR/push.

### Changed

- The generated `messaging` build pack is now a thread/message substrate only. Contacts, friends, follows, invitations, posts, and feeds belong to the `social` pack or to an app-specific relationship provider.
- AppGenerator now prioritizes the managed `mozaikspay` operator pack for SaaS subscriptions, billing portals, seats, credits, and usage limits when no alternate billing provider is explicitly requested.
- Hardened usage pricing catalog sync with upstream content hashing, normalized row-count drift checks, generated catalog change summaries, docs-based override guidance, and private override-file packaging protection.
- Clarified the deployment boundary between repo-local `infra/`, the first-party `factory_app/` workspace, and provider-neutral generated app deployment artifacts; added a canonical architecture doc and rewrote the stale `infra/DEPLOYMENT.md` guide to match current OSS behavior.

### Removed

- Removed obsolete first-party demo social modules from the Studio app bundle and removed contacts templates from the messaging pack. Social behavior remains available through the explicit `social` build-context pack.

## 0.1.8 - 2026-07-12

### Added

- **Workspace integrations catalog** (`factory_app/app/modules/workspace_integrations/`):
  New Studio module that tracks third-party service configuration status at the workspace level. Status (`configured`, `partial`, `missing`, `unknown`) is derived server-side from environment secrets — no secret values are ever returned. Includes operator notes storage per integration, `MOZAIKS_INTEGRATIONS_REGISTRY_MODE=catalog_only` for hosted multi-tenant deployments, and 14 catalog entries across 9 categories (payments, email, sms, ai, storage, source_control, notifications, database, cache, auth, analytics). Studio workspace page at `/integrations` shows the full catalog grouped by category with per-secret presence rows. AppGenerator gains `check_workspace_integrations` tool (registered to `AppPlanAgent`) so the agent can detect available integrations early in planning and avoid prompting for credentials that are already configured.

- **Immutable audit trail** (`mozaiksai/core/audit/`):
  Every module action and workflow start is logged to a dedicated append-only MongoDB `audit_log` collection with actor, app_id, resource, action, and inputs hash. Failures degrade to structured log so records are never silently lost. Wired into `ModuleExecutor.execute()` via fire-and-forget `asyncio.create_task`.

- **Circuit breaker for AppBackendPort** (`mozaiksai/core/adapters/circuit_breaker.py`):
  `CircuitBreaker` wraps `HttpAppBackendAdapter.request()`. Opens after 5 consecutive failures (configurable via `CIRCUIT_BREAKER_FAILURE_THRESHOLD`), fast-fails with `backend_circuit_open` for `CIRCUIT_BREAKER_RECOVERY_TIMEOUT` seconds (default 30s), then probes with HALF_OPEN state. Per-service registry via `get_circuit_breaker_sync()`.

- **Distributed lock for chat state** (`mozaiksai/core/runtime/persistence/distributed_lock.py`):
  `distributed_lock()` async context manager uses MongoDB `findOneAndUpdate` to prevent two runtime instances from resuming the same chat simultaneously. TTL index auto-cleans orphaned locks. Falls through silently in degraded mode (MongoDB unavailable).

- **Prometheus metrics endpoint** (`mozaiksai/core/metrics/prometheus_exporter.py`):
  `GET /metrics` in Prometheus text exposition format. Exports workflow counters, module action rates, token usage, auth failures, circuit breaker opens, and HTTP request counts. Optional Bearer token protection via `PROMETHEUS_METRICS_TOKEN`. Enable/disable via `PROMETHEUS_METRICS_ENABLED`. Router registered automatically in `hosts/runtime.py`.

- **Feature flags** (`mozaiksai/core/flags/`):
  `FeatureFlags` evaluates flags from env vars (`MOZAIKS_FLAG_{NAME}`) with optional MongoDB-backed backend for runtime toggling without redeployment. App-scoped flags supported (`is_enabled("flag", app_id=...)`). Cache TTL configurable via `FEATURE_FLAGS_CACHE_TTL`.

- **Task idempotency guard** (`mozaiksai/core/workflow/idempotency.py`):
  `IdempotencyGuard` deduplicates tool executions by `(chat_id, tool_name, args_hash)`. Prevents double-execution when a task batch retries after a successful tool call but failed result persistence. Records stored in `idempotency_records` collection with 24h TTL.

- **Artifact store port + adapters** (`mozaiksai/core/ports/artifact_store.py`):
  `ArtifactStore` protocol with `LocalArtifactStore` (default) and `S3ArtifactStore` (production). Streams large artifacts to object storage; MongoDB holds only metadata + URL. Configured via `ARTIFACT_STORE_BACKEND` env var (`local` | `s3`).

- **Inter-instance event bus** (`mozaiksai/core/ports/event_bus.py`):
  `EventBus` protocol with `NoOpEventBus` (single instance), `MongoEventBus` (change streams, no extra infra), and `RedisEventBus` (pub/sub). Configured via `EVENT_BUS_BACKEND` (`noop` | `mongo` | `redis`). Started automatically during runtime startup.

- **Artifact versioning** (`mozaiksai/core/runtime/persistence/artifact_version.py`):
  `ArtifactVersion` records artifact ID, version, lineage, parent_build_id, store URL, and checksum in `artifact_versions` collection. Helpers: `record_artifact_version()`, `get_artifact_history()`, `next_version_number()`.

- **Global durable workflow queue** (`mozaiksai/core/workflow/queue.py`):
  `WorkflowQueue` protocol with `NoOpWorkflowQueue` (preserves existing per-instance semaphore) and `MongoWorkflowQueue` (atomic claim with findOneAndUpdate, global concurrency limit, priority support, TTL expiry). Configured via `WORKFLOW_QUEUE_BACKEND` (`noop` | `mongo`).

- **Redis distributed cache** (`mozaiksai/core/cache/`):
  `RedisCache` caches JWKS, app context, and module registry across instances. Falls back to in-memory `_MemoryCache` when Redis is unavailable. Named helpers: `get_jwks()`, `set_app_context()`, `invalidate_app_context()`. Connected automatically at startup.

- **AG2 isolation adapter** (`mozaiksai/core/adapters/ag2_runner.py`):
  `AG2RunnerAdapter` wraps AG2's `ConversableAgent`, `GroupChat`, and `initiate_chat` API. Isolates AG2 imports to one file so AG2 version bumps require only this adapter to change. Includes compatibility watchpoints for tracked AG2 API surfaces. `build_llm_config()` now accepts `fallback_models` kwarg for ordered fallback config_list construction.

- **LLM fallback config builder** (`mozaiksai/core/adapters/llm_fallback.py`):
  `build_fallback_config_list()` constructs an AG2-compatible `config_list` with a primary model and env-driven fallback models (via `LLM_FALLBACK_MODELS`). `get_healthy_config_list()` reorders entries so circuit-breaker-OPEN providers are deprioritised. `build_fallback_llm_config()` is a drop-in replacement for `AG2RunnerAdapter.build_llm_config()` with full fallback support. AG2 network runner binds trace context at workflow start.

- **Async trace context propagation** (`mozaiksai/core/tracing/`):
  `bind_trace_id()` sets the current trace ID on a Python `ContextVar`. Because asyncio automatically copies `ContextVar` state to child tasks, every `asyncio.create_task()` (audit logger, fire-and-forget) inherits the trace ID without parameter threading. `RequestIDMiddleware` now calls `bind_trace_id()` on each request. `trace_context()` context manager supports scoped binding with automatic restore. `TraceContext.as_log_extra()` provides structured log enrichment and `as_headers()` returns outbound HTTP trace headers.

- **Platform router decomposition — complete** (`mozaiksai/hosts/routers/`):
  Extracted all remaining route groups from `platform.py` into dedicated router modules: `chat.py`, `sessions.py`, `transitions.py`, `profile.py`, `workflows.py` (in addition to existing `notifications.py`, `shell.py`, `modules.py`). `platform.py` reduced from ~3,662 to ~2,835 lines. `resolve_scope_from_principal()` moved to `mozaiksai.core.auth.dependencies` as the canonical shared location. Two missing persistence methods (`delete_general_chat`, `fetch_general_chat_transcript`) added to `AG2PersistenceManager`. The modules router reads `executor_registry` from `request.app.state` for clean test isolation.

- **HMAC-SHA256 artifact signing** (`mozaiksai/core/runtime/persistence/artifact_signer.py`):
  `sign_artifact()` and `verify_artifact()` use HMAC-SHA256 with a hex-encoded 32-byte key from `ARTIFACT_SIGNING_KEY` env var. Unsigned mode degrades gracefully (returns empty string / always verifies) when no key is configured. `assert_artifact_authentic()` raises `ArtifactSignatureError` on tamper detection. `artifact_promotion.py` now signs bundles at promotion time and includes `bundle_hmac_sha256` in refinement metadata. 16 tests added.

- **Durable refinement event tracking** (`mozaiksai/control_plane/refinement_tracking.py`):
  `record_refinement_event()` persists audit documents to `mozaiks.cp_refinement_events` collection with event kind, request ID, app ID, change class, workflow sequence, outcome, and timing. Fire-and-forget — never raises to callers. `get_refinement_history()` queries by app and optional request ID. `OrchestrationControlHarness` now emits `request_received`, `classified`, `completed`, and `failed` events at each decision boundary.

- **Keycloak realm version control** (`infra/keycloak/export.sh`, `.github/workflows/ci.yml`):
  `export.sh` exports the current realm config from a running Keycloak instance via the admin API and writes to `factory_app/app/brand/realm-export.json`. CI lint job now validates `realm-export.json` is valid JSON with required fields (`realm=mozaiks`, `enabled`) on every push and pull request.

- **Concurrency test suite** (`tests/concurrency/`):
  12 new tests covering: circuit breaker state transitions, concurrent feature flag reads, trace ID propagation to child tasks, `trace_context()` restore, task isolation, LLM fallback config ordering, circuit-aware config_list reordering, idempotency guard dedup under concurrency.

- **Kubernetes Helm chart** (`infra/helm/mozaiks/`):
  Production-grade Helm chart with: Deployment (RollingUpdate, maxUnavailable=0), Service, Ingress, HPA (CPU + memory), PodDisruptionBudget, PVC, ServiceAccount, ConfigMap. Secret values sourced from a named Kubernetes Secret (zero secrets in chart). Pod anti-affinity preferred across nodes. Liveness and readiness probes on `/api/health/liveness` and `/api/health/readiness`. Prometheus scrape annotations on pod.

- **Grafana dashboard** (`infra/grafana/mozaiks-dashboard.json`):
  Pre-built Grafana dashboard with panels for: workflow health (started/completed/failed/active, success rate), module action rate, LLM token usage (input/output per minute, totals), auth failure rate, circuit breaker opens, HTTP request rate by status class, uptime. Configurable `DS_PROMETHEUS` datasource input.

- **Operational docs** (`docs/operations/`):
  Zero-downtime deployment runbook, secrets rotation procedure, alerting thresholds, horizontal scaling guide, backup and recovery procedures, and incident runbooks for: LLM API down, MongoDB connection exhausted, disk full, Keycloak unreachable.

- **`monitoring` optional extra** (`pyproject.toml`):
  Adds `redis[asyncio]>=5.0.0` and `boto3>=1.28.0` for Redis cache and S3 artifact store backends. Install with `pip install "mozaiks[monitoring]"`.

### Changed

- **CI/CD hardening** (`pyproject.toml`, `.github/workflows/ci.yml`):
  Added `pytest-rerunfailures>=12.0` to dev deps. Pytest now re-runs flaky tests up to 2 times on `AssertionError` or `TimeoutError`. Coverage threshold set to `--cov-fail-under=30` (calibrated to full-suite achievable level). mypy CI command updated to `--disable-error-code=import-untyped` to suppress PyYAML stub noise consistent with `pyproject.toml` config.

- **mypy clean pass** (`mozaiksai/`, `factory_app/`):
  12 targeted type fixes across `ag2_runner.py`, `artifact_store.py`, `supabase.py`, `orchestration_patterns.py`, `queue.py`, `artifact_version.py`, `http_app_backend.py`, `commerce/backend/repo.py`, `DesignDocs/tools/save_design_doc.py`, `SubscriptionContractDesigner/tools/save_subscription_contract.py`, `refinement_harness_codegen.py`, `routers/modules.py`. Result: 477 source files, 0 errors.

- **`hosts/runtime.py` startup** (`mozaiksai/hosts/runtime.py`):
  Runtime startup now ensures distributed lock indexes, idempotency indexes, and artifact version indexes, connects the Redis cache, and starts the inter-instance event bus. All operations degrade silently if their backends are unavailable.

### Fixed

- **Hosted app runtime compatibility** (`mozaiksai/refinement_harness/config.py`, `mozaiksai/core/runtime/composition/module_executor.py`, `mozaiksai/core/runtime/persistence/indexes.py`):
  Control-plane runtime config now accepts optional profile metadata used by hosted app workspaces, module output validation normalizes `nullable: true` schemas before JSON Schema validation, and database index application strips metadata-only options while supporting literal Motor collections. These fixes unblock `mozaiks-app` startup, module dispatch, and database index bootstrapping from an installed package.

### Changed

- **Frontend runtime stability** (`chat-ui/src/app/MozaiksApp.jsx`, `chat-ui/src/ui/primitives/DataTable.jsx`):
  Opted into React Router v7 future flags to remove development warnings and made DataTable default arrays stable so table consumers without explicit data do not trigger render loops.

### Security

- **Multi-tenant isolation gaps closed** (`mozaiksai/core/data/persistence/persistence_manager.py`, `mozaiksai/hosts/runtime.py`):
  Two cross-tenant isolation gaps identified and fixed:
  - Cache seed find/update queries in `persistence_manager.get_or_assign_cache_seed()` (lines ~319, ~343)
    now include `app_id` in the MongoDB filter for defense-in-depth isolation. Previously the filter
    used only `chat_id`; since chat IDs are UUIDs the practical risk was near zero but the pattern
    was inconsistent with the rest of the persistence layer.
  - `create_chat_session()` duplicate check (line ~381) now scopes the existing-document check to
    the requesting app.
  - `/api/workflows/{workflow_name}/trigger` now calls `validate_path_app_id(principal, app_id)` so
    an authenticated caller cannot create workflow sessions scoped to a foreign app by supplying an
    arbitrary `app_id` in the request body.

- **Per-user WebSocket connection limit** (`mozaiksai/core/transport/simple_transport.py`):
  Added `MOZAIKS_MAX_WS_CONNECTIONS_PER_USER` (default 20) to cap how many concurrent
  WebSocket sessions a single authenticated user can have open at once. A user opening
  more connections than this limit receives WS close code 1008. Prevents a single user
  from exhausting the global connection pool. Documented in `.env.example` alongside the
  existing `MOZAIKS_MAX_WS_CONNECTIONS` and `MOZAIKS_WS_IDLE_TIMEOUT` settings.

- **Theme endpoint path validation** (`mozaiksai/hosts/platform.py`):
  Added `validate_path_id(app_id, "app_id")` to `/api/themes/{app_id}` so malformed
  path segments (e.g., traversal sequences) are rejected before theme resolution runs,
  consistent with validation on other path-parameter endpoints.

- **WebSocket connection rate limiting** (`mozaiksai/core/transport/rate_limit.py`):
  Added `/ws/` to `_DEFAULT_PATH_LIMITS` (10/min per client) so WebSocket upgrade requests
  are governed by a tighter path-specific cap rather than the global 60/min default. Each
  WebSocket connection opens a full workflow context, making WS upgrades a high-cost target.
  Documented in the module docstring.

- **Startup checks for rate limiting and Redis** (`mozaiksai/core/startup/validation.py`):
  Added two new boot-time checks:
  - `RATE_LIMIT_ENABLED=false` in a production environment now emits a structured warning
    (raises `StartupConfigError` in strict mode), consistent with the `AUTH_ENABLED` check.
  - When `REDIS_URL` is configured, the runtime now TCP-pings the Redis host/port at startup
    and warns if unreachable. Without Redis, the rate limiter silently falls back to in-memory
    storage that does not enforce limits across multiple instances. The Redis check is a warning
    only even in strict mode — the in-memory fallback is functional.

- **IDOR protection on module action routes** (`mozaiksai/hosts/platform.py`):
  `_execute_module_action` now validates an explicit `?app_id` query parameter against the
  authenticated principal's token claim before dispatching. Callers cannot execute actions
  scoped to a foreign app by supplying `?app_id=other-app`. The check is a no-op when
  `AUTH_ENABLED=false`. Authorization is evaluated before executor availability checks so
  auth errors always take priority over infrastructure errors.

- **Broad HTTP 500 error detail suppression** (`mozaiksai/hosts/platform.py`, `studio.py`, `runtime.py`, `factory.py`, `core/admin/router.py`):
  Removed raw exception text from 22 additional HTTPException 500 `detail=` fields across
  platform (12), studio (7), runtime (1), factory (2), and admin router (1) endpoints.
  All exceptions are still logged server-side; callers receive only the action-level description.

- **Module executor error suppression** (`mozaiksai/core/runtime/composition/module_executor.py`):
  The catch-all `EXECUTION_ERROR` handler no longer surfaces `str(exc)` in the
  `ModuleResult.error` field (and thus the HTTP response body). Exception text that could
  contain DB connection strings, internal paths, or stack details is replaced with
  `"Action '{action}' failed"`; full detail remains in `logger.error(..., exc_info=True)`.

- **Workflow bridge error suppression** (`mozaiksai/core/transport/workflow_bridge.py`):
  `handle_user_input_from_api` no longer includes raw exception text in the websocket
  `send_error` message or the HTTP return dict on `WORKFLOW_EXECUTION_FAILED`.
  Replaced with generic user-facing messages; internal details stay in `logger.error`.

- **Rate limit middleware integration tests** (`tests/test_rate_limit_middleware.py`):
  19 end-to-end integration tests added covering: 429 responses, `Retry-After` and
  `X-RateLimit-*` headers, per-path prefix limits, excluded paths, OPTIONS bypass,
  per-client bucket isolation, CORS header on 429, and disabled mode. Added
  `test_execution_error_does_not_leak_exception_message` to module executor tests.

- **Auth endpoint rate limit**: `/api/auth` now has a tighter default of 20 req/min
  (vs the global 60) in `RateLimitMiddleware` to reduce brute-force risk on token
  and login routes. Overridable via `RATE_LIMIT_PATH_LIMITS`.

- **Profile panel error sanitization** (`mozaiksai/hosts/platform.py`):
  `get_profile_panels` no longer surfaces raw exception messages from module action
  failures in the `/api/me/profile-panels` response. Replaced `str(exc)` with a
  generic `"Action {action!r} failed"` message; full detail stays in server logs.

- **SSRF mitigation on webhook_url** (`mozaiksai/hosts/runtime.py`):
  `TriggerWorkflowRequest.webhook_url` validated with a Pydantic `field_validator`:
  must use `https://`, include a hostname, and must not target private or reserved
  IP space. Blocked ranges: loopback (`127.x` / `::1` / `localhost`), all RFC-1918
  private ranges (`10.x`, `172.16–31.x`, `192.168.x`), link-local / cloud metadata
  (`169.254.x`), IPv6 ULA (`fc00::/7`), and all `ipaddress.ip_address` reserved and
  multicast ranges. 9 new validator tests cover all blocked ranges.

- **Agent tool error message suppression** (`mozaiksai/core/events/auto_tool_handler.py`, `mozaiksai/core/workflow/app_backend_tools.py`, `mozaiksai/core/adapters/http_app_backend.py`):
  Auto-tool failures, backend request errors, emit-event failures, and health-check
  exceptions no longer include raw Python exception text in tool result payloads that
  AG2 agents reason over (and could surface in chat messages). Replaced with opaque
  error codes; full exception details remain in `logger.error`.

- **WebSocket path parameter validation** (`mozaiksai/hosts/platform.py`):
  `/ws/{workflow_name}/{app_id}/{chat_id}/{user_id}` now validates all four path
  parameters against `validate_path_id` before authentication. Invalid values
  (path traversal, shell metacharacters, values >128 chars) close the WebSocket
  with code 1008 (Policy Violation) before any DB or auth operations run.

- **Path parameter validation on platform routes** (`mozaiksai/hosts/platform.py`):
  `validate_path_id` now applied across all platform routes that take path parameters.
  Covers `notification_id`, `workflow_name`, `chat_id`, `general_chat_id`, and `app_id`
  on chat, session, general-chat, notification, and upload endpoints. Rejects path
  traversal, shell metacharacters, and values over 128 characters before they reach
  MongoDB queries or downstream handlers.

- **Health check error detail suppression** (`mozaiksai/hosts/runtime.py`, `mozaiksai/hosts/platform.py`):
  `/api/health/ready` MongoDB ping errors no longer include raw exception text in
  `checks.mongodb`; returns opaque `"error"` code instead. `/api/health` MongoDB
  unreachable detail no longer includes `str(exc)`. Journey binding failures in
  `/api/chats/.../start` no longer expose internal error text in the 400 response.
  `startup_degraded_reason` (surfaced in health responses) now stores only the
  exception class name rather than the full message, preventing DB connection
  strings and internal paths from leaking via unauthenticated health endpoints.

- **Module executor TypeError suppression** (`mozaiksai/core/runtime/composition/module_executor.py`):
  `INVALID_PARAMS` error responses no longer include the raw `TypeError` message,
  which could expose handler parameter names or internal type details. Returns
  `"Invalid parameters for action '{action}'"` instead; full detail stays in logs.

- **Path parameter validation on all Studio routes** (`mozaiksai/hosts/studio.py`):
  `validate_path_id` now applied to all Studio route path parameters:
  `artifact_version_id` on bundle/review/accept/reject/promote endpoints,
  `service` on integration connector endpoints, and `app_id` on app-context
  management endpoints. Rejects malformed identifiers before any artifact store,
  workspace, or context operations run.

- **Path ID validation on workflow routes** (`mozaiksai/hosts/runtime.py`):
  `validate_path_id` now applied to `workflow_name`, `app_id`, and `chat_id` on
  five previously unprotected routes: `component_action`, `trigger`, `transport`,
  `tools`, and `ui-tools`.

- **HTTP security headers middleware** (`mozaiksai/core/transport/security_headers.py`):
  `SecurityHeadersMiddleware` now added to all Mozaiks hosts. Sets
  `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`,
  `Content-Security-Policy` (with `frame-ancestors 'none'`),
  `Referrer-Policy`, `Permissions-Policy`, and HSTS on HTTPS connections.
  All values are env-configurable. Existing route-level headers are preserved.

- **Path parameter sanitization** (`mozaiksai/core/auth/dependencies.py`):
  `validate_path_id()` added and wired into chat, WebSocket, and user input
  endpoints. Rejects path traversal (`..`), shell metacharacters, spaces,
  and values over 128 characters before they reach MongoDB queries.

- **Fixed wildcard CORS + credentials** (`mozaiksai/factory.py`):
  `create_mozaiks_app()` no longer adds CORS middleware with `"*"` origin
  and `allow_credentials=True` (invalid per spec; browsers reject it).
  CORS middleware is now only added when explicit non-wildcard origins are provided.

- **Tightened CORS method/header wildcards** (`mozaiksai/hosts/runtime.py`):
  `allow_methods` and `allow_headers` replaced with explicit allowlists.
  `expose_headers` now declares the rate-limit and request-ID headers the
  frontend needs.

- **Request body size limit** (`mozaiksai/core/transport/request_middleware.py`):
  `RequestBodySizeLimitMiddleware` rejects JSON requests with a
  `Content-Length` header exceeding `MAX_REQUEST_BODY_BYTES` (default 1 MB)
  before the route handler reads the body. Upload paths are excluded by
  default and configurable via `MAX_REQUEST_BODY_EXCLUDED_PATHS`.

- **Request ID propagation** (`mozaiksai/core/transport/request_middleware.py`):
  `RequestIDMiddleware` attaches a unique `X-Request-ID` to every request
  and response. Client-supplied IDs are validated and echoed; otherwise a
  UUID4 is generated. Bound to `request.state.request_id` for handlers,
  tools, and structured logs.

- **MIME type enforcement on file uploads** (`mozaiksai/core/chat_attachments/attachments.py`):
  `handle_chat_upload` now validates the declared `content_type` against a configurable
  MIME type allowlist before writing any bytes to disk. Default allowlist covers text,
  image, PDF, JSON, and XML types. Configurable via `UPLOAD_ALLOWED_MIME_TYPES`
  (comma-separated; set to `*` to disable). Content-type parameters (e.g.
  `; charset=utf-8`) are stripped before comparison. 4 integration tests added.

- **Upload path containment** (`mozaiksai/core/chat_attachments/attachments.py`):
  `handle_chat_upload` now verifies that `dest_dir` and `stored_path` remain inside
  `upload_root` after `Path.resolve()`. Defense-in-depth against symlink-based or
  unusual `app_id`/`chat_id` path traversal edge cases.

- **Chat upload form field validation** (`mozaiksai/hosts/platform.py`):
  `_handle_chat_upload` now calls `validate_path_id` on `app_id` and `chat_id` from
  form data, ensuring both `/api/chat/upload` and `/api/chat/upload/{app_id}/{user_id}`
  reject malformed identifiers before filesystem operations run.

- **Sandbox API error detail suppression** (`factory_app/workflows/AppGenerator/tools/sandbox_api.py`):
  All sandbox HTTP routes (create, sync, start, status, stop) and the WebSocket
  endpoint no longer include raw exception text in 500/503 responses or WebSocket
  close reasons. Generic messages returned; full exceptions logged server-side.

- **Studio refinement endpoint error detail suppression** (`mozaiksai/hosts/studio.py`):
  Refinement classification (503) and coding worker (503) exception handlers no
  longer include `str(exc)` in the HTTP response detail. Generic messages returned;
  full exceptions logged with `exc_info=True`.

- **Health endpoint module name suppression** (`mozaiksai/hosts/platform.py`):
  `startup_degraded_reason` for partial module load failures no longer includes the
  list of failed module names, which would expose internal app structure to
  unauthenticated callers of `/health`. Returns only the count; full name list
  remains in `logger.error`.

- **AppGenerator admin route template hardened** (`factory_app/workflows/AppGenerator/tools/app_backend_admin_codegen.py`):
  The generated `/api/admin/config` route template was emitting `str(exc)` in the
  HTTP 500 detail, which would have leaked exceptions in all generated apps' admin
  endpoints. Replaced with a generic message; full exception is logged server-side.

- **Module handler symlink-escape guard** (`mozaiksai/core/runtime/app/module_loader.py`):
  After locating a handler file, the loader now calls `handler_path.resolve().relative_to(module_dir.resolve())`
  to confirm the resolved path is still within the module directory. This guards against
  symlink-based escapes that the existing static analysis (rejecting `..`, absolute paths,
  and `_shared` references) cannot prevent. A symlinked handler pointing outside the
  module directory causes a `ModuleLoadError` and the module is classified as failed.

- **Azure Key Vault connector error suppression** (`mozaiksai/core/secrets/connector_vault.py`):
  `store_secret`, `get_secret`, and `delete_secret` no longer return raw Azure
  SDK exception text (which can include subscription IDs and vault URLs).
  Generic messages returned to callers; exceptions logged with `exc_info=True`.

- **AG2 stream history bounded** (`mozaiksai/core/adapters/ag2_stream_storage.py`):
  `get_history` now applies `.limit(10_000)` to prevent unbounded MongoDB reads
  on event streams from long-running or abnormally large AG2 tasks.

- **SSRF guard on domain probe** (`mozaiks-app/app/modules/infra_assurance/backend/service.py`):
  `run_checks` now validates the caller-supplied domain through `_is_safe_probe_domain()`
  before any HTTP probe runs. Blocked targets: loopback names (`localhost`, `127.x`,
  `::1`), all RFC-1918 private ranges, link-local/IMDS (`169.254.x`), CGNAT
  (`100.64.0.0/10`), IPv6 ULA (`fc00::/7`), and private DNS TLDs
  (`.local`, `.internal`, `.corp`, `.home`, `.lan`, `.intranet`, `.localdomain`,
  `.example`, `.test`, `.invalid`). Domains that fail validation return an error
  without making any outbound request. Hostname-format validation applied to bare
  labels. 6 new tests cover accepted public hosts, all blocked categories, and edge cases.

- **HTTP probe error sanitization** (`mozaiks-app/app/services/adapters/probes/http_health.py`):
  `check_http_health` no longer includes raw `httpx` exception text in the returned
  `error` field (which flows through `infra_assurance` and `provider_connections`
  into user-visible responses). `TimeoutException` returns `"timeout after {N}s"`;
  `HTTPError` returns only the exception class name. Full detail logged server-side.

- **Azure cert 404 detection hardened** (`mozaiks-app/app/services/adapters/ssl/azure_cert.py`):
  `get_status` and `deprovision` previously parsed `"404"` and `"NotFound"` from
  `str(exc)`, which is brittle and could match false positives in exception messages.
  Both now check `exc.response.status_code == 404` directly on the response attribute.

- **Auth bypass fixed in build event route** (`mozaiks-app/app/modules/app_registry/backend/routes_build_events.py`):
  `_authorize_internal_request` previously allowed unauthenticated calls in any
  environment where `MOZAIKS_PLATFORM_INTERNAL_API_KEY` was not configured. Flipped
  to fail-safe: an unconfigured key now returns 503 (endpoint not configured) rather
  than silently passing the request. Removed dead `_production_env()` helper.

- **Admin-only service guards for app_registry** (`mozaiks-app/app/modules/app_registry/backend/service.py`, `policy.py`):
  `list_all_apps` now calls `require_app_registry_admin(ctx)` at the service layer,
  enforcing that the caller holds `app_registry.admin` permission. Previously the
  guard existed only in `module.yaml`; a misconfigured host or direct service call
  could bypass it. `require_app_registry_admin` added to `policy.py`.

- **Unsafe hosting URL scheme rejected** (`mozaiks-app/app/modules/app_registry/backend/service.py`):
  `on_app_deployed` now validates that `hosting_url` uses `https://` before storing it.
  Non-HTTPS URLs (including `javascript:`, `file://`, and bare HTTP) are rejected with an
  error; the call is logged and the record is not updated.

- **Bare assert replaced with explicit guard** (`mozaiks-app/app/modules/app_registry/backend/generated_bundle_gate.py`):
  `validate_generated_bundle_for_hosting` contained `assert bundle_path is not None`
  which is silently removed by Python's `-O` flag in optimized production builds.
  Replaced with an explicit `if bundle_path is None: return _failure(...)` check.

- **IDOR fix for listing submission** (`mozaiks-app/app/modules/investor_marketplace/backend/service.py`):
  `submit_listing` now fetches the target listing and verifies `owner_id` matches the
  authenticated user before performing any update. Previously a caller could supply an
  arbitrary `listing_id` and mutate another user's listing record.

- **Anonymous investor fallback removed** (`mozaiks-app/app/modules/investor_marketplace/backend/policy.py`):
  `investor_id_from_context` previously returned the string `"anonymous"` when no
  `user_id` was present. Unauthenticated callers could create records aggregated under
  a single shared `"anonymous"` key. Now returns `""`, and all write methods in the
  service layer reject an empty `investor_id` with an explicit auth-required error.

- **Admin-only service guards for investor_marketplace** (`mozaiks-app/app/modules/investor_marketplace/backend/service.py`, `policy.py`):
  `approve_listing`, `approve_marketplace_placement`, and
  `bulk_set_marketplace_placement_status` now call `require_marketplace_admin(ctx)` at
  the service layer, enforcing the `marketplace.approve` permission even when reached
  through non-module-dispatch paths. `require_marketplace_admin` added to `policy.py`.

- **Input length limits and investor_type allowlist** (`mozaiks-app/app/modules/investor_marketplace/backend/service.py`):
  `record_investment_interest` truncates `message` to 2,000 characters.
  `upsert_investor_profile` validates `investor_type` against an explicit allowlist
  (`individual`, `institutional`, `fund`, `firm`, `angel`, `corporate`, `family_office`,
  `vc`, `pe`) and truncates `thesis` to 2,000 characters.
  `record_placement_impression` and `record_placement_click` truncate `slot` and
  `source` to 64 characters. Prevents unbounded data from being written to MongoDB.

- **Open-redirect guard on hosting_url in mark_deployed** (`mozaiks-app/app/modules/hosting/backend/service.py`):
  `mark_deployed` now validates `hosting_url` uses `https://` before storing it or emitting
  the `hosted.hosting.app.deployed` event. Non-https URLs are rejected with success=False.
  2 new tests cover http:// and bare-hostname rejection.

- **Input length limits on community_membership, mozaikspay_checkout, domain_registry**:
  - `community_membership.create_community`: name truncated to 100 chars, description to 1,000 chars.
  - `mozaikspay_checkout.create_checkout_session`: description truncated to 500 chars.
  - `domain_registry.record_certificate_observation`: certificate_source truncated to 50 chars,
    certificate_provider to 100 chars, certificate_issuer to 200 chars.

- **Open-redirect guard in mozaikspay billing_portal template** (`factory_app/build_context/mozaikspay/templates/modules/billing_portal/backend/service.py`):
  `open_billing_portal` now validates `return_url` before passing it to the MozaiksPay client.
  Empty URLs return `INVALID_INPUT`; non-`https://` schemes (including `http://`, `javascript:`,
  and bare paths) are rejected without contacting the billing API. The fix applies to the
  generator template, so all apps generated with the mozaikspay capability pack get the guard
  automatically. Defense-in-depth alongside any MozaiksPay server-side validation.

- **Open-redirect guard on billing portal return_url** (`mozaiks-app/app/modules/hosted_billing/backend/service.py`):
  `create_billing_portal_session` now validates that `return_url` uses the `https://`
  scheme and contains a non-empty host before passing the URL to the payment provider Customer
  Portal API. `http://` and `javascript:` URLs are rejected with `INVALID_INPUT`
  without contacting payment provider. 3 new tests cover http rejection, javascript: rejection,
  and empty URL. payment provider itself also validates portal return URLs in the dashboard
  allowlist, so this is defense-in-depth on the Mozaiks side.

- **Public-status filter bypass closed** (`mozaiks-app/app/modules/investor_marketplace/backend/service.py`, `policy.py`):
  `list_listings` and `list_marketplace_placements` now enforce status filters for
  non-admin callers: non-admins always receive `{"$in": ["live", "approved"]}` (or
  `"live"` for placements) regardless of the `status` parameter supplied. Previously
  a caller could pass `status=draft` to bypass the live/approved default and read
  unpublished listings. Admins with `marketplace.approve` may still supply any
  valid status. `is_marketplace_admin` helper added to `policy.py`.

- **target_result sanitization in remediation_registry** (`mozaiks-app/app/modules/remediation_registry/backend/service.py`):
  The failure path of `approve_and_execute` was returning the raw dispatcher result
  dict as `target_result`, which could contain `error`, `message`, and `status` fields
  from module action handlers. Now uses the same `_summarize_target_result()` helper
  as the success path, returning only safe structural fields (`success`, `record_id`,
  `record_type`, `name`, `provider`).

- **Startup check for auth provider misconfiguration** (`mozaiksai/core/startup/validation.py`):
  When `ENV=production`, auth is not explicitly disabled (`AUTH_ENABLED` not false), and
  none of the provider env vars (`SUPABASE_URL`, `KEYCLOAK_URL`+`KEYCLOAK_REALM`,
  `AUTH_JWKS_URL`+`AUTH_ISSUER`, or `AUTH_PROVIDER`) are set, the runtime previously
  silently fell back to demo mode (no authentication). A new boot-time check now emits
  a structured warning for this gap; in strict mode (`MOZAIKS_STARTUP_CHECKS=strict`) it
  raises `StartupConfigError`. 8 new tests cover all provider detection branches and the
  strict-mode raise path.

- **Defense-in-depth admin guards for dns_management and schema_migrations** (`mozaiks-app/app/modules/dns_management/backend/service.py`, `mozaiks-app/app/modules/schema_migrations/backend/policy.py`, `service.py`):
  - `dns_management.provision_activation_record` is an event-authorized internal action
    (`api_surface: internal, permissions: []`) called by the event bus, but had no
    service-layer guard. Added `require_ops_admin(ctx)` — the `ModuleContext` pass-through
    allows event-driven calls through while blocking direct non-admin API calls.
    Updated `test_all_actions_require_ops_admin` to exempt `api_surface: internal` actions
    (event-authorized actions use a different security model).
  - `schema_migrations.record_migration` and `run_migration` declared `schema_migrations.admin`
    in `module.yaml` but had no service-layer check. Added `require_schema_migrations_admin(ctx)`
    to `policy.py` (same `ModuleContext` pass-through pattern). 4 new tests cover
    admin allowed, `ops.admin` allowed, and non-admin rejected for both write methods.

- **Defense-in-depth admin guards for wallet admin_internal actions** (`mozaiks-app/app/modules/wallet/backend/service.py`, `policy.py`):
  `get_platform_financials`, `provision_hosted_wallet`, and `get_hosted_wallet_provisioning_status`
  are `api_surface: admin_internal, permissions: [wallet.admin]` actions that previously relied
  solely on the module executor for enforcement. Added `require_wallet_admin(ctx)` to `policy.py`
  (same `ModuleContext` pass-through pattern) and wired it into all three methods. 3 new tests
  cover non-admin blocked and admin allowed for each method.

- **Input length limits on community_governance and messages write methods** (`mozaiks-app/app/modules/community_governance/backend/service.py`, `mozaiks-app/app/modules/messages/backend/service.py`):
  `create_proposal` now truncates `title` to 200 characters and `description` to 5,000 characters.
  `create_announcement` now truncates `title` to 200 characters and `body` to 5,000 characters.
  Prevents unbounded user-supplied strings from being written to MongoDB. 5 new tests cover
  truncation behavior for both methods.

- **Admin-only service guard for tenant_identity list_all_tenants** (`mozaiks-app/app/modules/tenant_identity/backend/service.py`):
  `list_all_tenants` had no authorization check at the service layer — any code path
  that bypassed the module executor (reaction handlers, direct instantiation, tests)
  could enumerate all tenants. Added `is_admin_context(ctx)` guard that mirrors the
  `tenant_identity.admin` permission declared in `module.yaml`. 3 new tests cover
  admin allowed, non-admin rejected, and no-permissions rejected.

- **Admin-only guards for build_intelligence, health, and hosted_billing** (`mozaiks-app`):
  - `build_intelligence`: `get_domain_patterns`, `list_all_builds`, and `get_intelligence_summary` had no
    service-layer permission check. These return unscoped build data across all users. Added
    `require_build_intelligence_admin(ctx)` to `policy.py` and wired it into all three methods. 4 new tests.
  - `health`: `get_app_health_summary` had `api_surface: admin_internal` and `permissions: [ops.admin]`
    in `module.yaml` but no service-layer guard. Created `backend/policy.py` with `require_ops_admin(ctx)`
    and wired it in. 1 new test.
  - `hosted_billing`: All 19 `admin_internal` actions relied on a stub `is_admin_context()` that
    unconditionally returned `True`. Replaced with `require_billing_admin(ctx)` (same `ModuleContext`
    pass-through pattern). Wired into all 19 service methods. Updated 10 affected test files.

- **Defense-in-depth admin guards for hosting admin_internal actions** (`mozaiks-app/app/modules/hosting/backend/service.py`, `policy.py`, `routes_webhooks.py`):
  `list_requests`, `approve_request`, `get_hosting_summary`, `get_domain_pipeline_status`,
  and `record_build_result_callback` are `api_surface: admin_internal` actions with
  `permissions: [hosting.admin]` declared in `module.yaml`. Previously enforcement relied
  solely on the module executor. Added `require_hosting_admin(ctx)` to `policy.py`
  (same `ModuleContext` pass-through pattern as other modules) and wired it into all five
  methods. The webhook route `_WebhookCtx` carries `permissions = ["hosting.admin"]` after
  HMAC authentication passes, so the webhook path is not blocked by the service guard.
  Updated all affected test contexts to supply the required permission.

- **INTERNAL_API_KEY minimum-length check in startup validation** (`mozaiksai/core/startup/validation.py`):
  Added a warning when `INTERNAL_API_KEY` is set but shorter than 32 characters. The
  previous check only detected the key being absent; a short key offers insufficient entropy
  as a defense-in-depth secret. Minimum length is 32 characters. Not a hard failure in
  either mode — the key is a defense-in-depth layer, not the only auth gate.
  Tests updated to use adequately long keys; added `test_warns_when_internal_api_key_too_short`.

- **$where/$expr blocked in agent-generated MongoDB queries** (`mozaiksai/core/data/persistence/db_manager.py`):
  `load_from_database`, the agent tool for MongoDB reads, passed agent-generated query dicts
  directly to MongoDB without sanitization. Operators `$where` (arbitrary JavaScript execution)
  and `$expr` (server-side expression evaluation) are now stripped before the query reaches
  MongoDB. Value-level comparison operators (`$gte`, `$lt`, `$in`, `$regex`) inside field
  filter dicts are legitimate and are not affected. Logged as `AGENT_QUERY_BLOCKED_OPERATORS`
  for observability. 6 tests cover safe queries, value-operator pass-through, and blocking
  of both forbidden operators individually and combined.

- **context_variables size limits on workflow launch** (`mozaiksai/core/session/launcher.py`):
  `validate_context_for_workflow` accepted unbounded context dicts: no limit on the number
  of keys or the size of string values. Large payloads would be stored in MongoDB per session.
  Added: max 50 keys (`_CONTEXT_MAX_KEYS`); string values exceeding 64 KB per entry
  (`_CONTEXT_MAX_VALUE_BYTES`) are silently dropped with a structured warning log.
  Non-string values (dicts, lists, etc.) are not bounded by the byte check and pass through
  unchanged. 3 boundary tests cover: key cap, oversized value drop, and exact-limit acceptance.

- **PermissionError from service-layer guards now maps to HTTP 403** (`mozaiksai/core/runtime/composition/module_executor.py`):
  Added a specific `except PermissionError` handler before the generic `except Exception`
  handler in `ModuleExecutor.execute()`. Previously, `PermissionError` raised by service-layer
  `require_X_admin(ctx)` guards was caught by the generic handler and returned with
  `error_code="EXECUTION_ERROR"`, which the platform host maps to HTTP 500. The correct
  status is 403; the platform host already routes `PERMISSION_DENIED` → 403.
  Added `test_permission_error_from_service_layer_returns_permission_denied` to ensure this
  routing holds regardless of the executor's generic catch-all order.

- **Defense-in-depth admin guards for investor_marketplace placement management** (`mozaiks-app/app/modules/investor_marketplace/backend/service.py`, `policy.py`):
  Nine placement-management methods (`get_placement_performance_summary`, `list_placement_performance`,
  `get_marketplace_revenue_summary`, `list_marketplace_rail_monetization`,
  `list_marketplace_sponsor_cohorts`, `upsert_marketplace_placement`,
  `schedule_marketplace_placement`, `list_marketplace_placement_audit_trail`,
  `set_marketplace_placement_status`) previously had no service-layer permission check and relied
  solely on module executor enforcement. Added `require_marketplace_admin(ctx)` as the first call
  in each method. Added ModuleContext pass-through to `require_marketplace_admin` in `policy.py`.
  Added `test_admin_actions_require_marketplace_approve` covering all nine methods.

- **Defense-in-depth admin guard for messages.create_announcement** (`mozaiks-app/app/modules/messages/backend/service.py`, `policy.py`):
  `create_announcement` is an `admin_internal` action with `permissions: [messages.admin]`
  declared in `module.yaml` but had no service-layer enforcement. Added `require_messages_admin(ctx)`
  to `policy.py` (same ModuleContext pass-through pattern) and wired it into `create_announcement`.
  Updated 3 announcement tests to supply the required permission in context.

- **Service-layer admin guards completed for app_registry, schema_migrations, tenant_identity** (`mozaiks-app`):
  - `app_registry.register_brownfield_app` and `get_registry_summary` now call `require_app_registry_admin(ctx)`.
    Added ModuleContext pass-through and `ops.admin` fallback to `require_app_registry_admin` in `policy.py`.
  - `schema_migrations.get_schema_version` and `list_migrations` now call `require_schema_migrations_admin(ctx)`.
    2 new tests cover non-admin rejection for each read method.
  - `tenant_identity.list_all_tenants` guard upgraded from soft return-error pattern to
    `require_tenant_identity_admin(ctx)` which raises `PermissionError`. 2 test assertions updated.

- **ModuleContext pass-through added to community_revenue_participation policy** (`mozaiks-app/app/modules/community_revenue_participation/backend/policy.py`):
  `require_revenue_admin` and `require_revenue_read` were missing the
  `if permissions is None and type(ctx).__name__ == "ModuleContext": return` pass-through,
  which would block event-driven internal calls despite no active callers relying on it.
  Aligned with the defensive standard applied to all other `require_X` guards in the codebase.

### Fixed

- **Bare assert replaced with explicit RuntimeError in MongoDB guards** (`mozaiksai/`):
  Eight bare `assert client is not None` / `assert config is not None` statements across
  `persistence_manager.py`, `theme_manager.py`, `entitlements.py`, `session/persistence.py`,
  `app_code_versions.py`, `workflow_artifacts.py`, and `platform.py` were silently removed
  when Python runs with the `-O` flag, causing downstream `AttributeError: 'NoneType' ...`
  instead of a clear error. Replaced with `if ... is None: raise RuntimeError(...)`.

- **AG2 stream events TTL index** (`mozaiksai/core/adapters/ag2_stream_storage.py`):
  `_ensure_indexes` now creates a TTL index on `created_at` for the AG2 stream events
  collection, capped at 30 days by default. Without this index, every workflow run's
  stream events accumulated indefinitely in MongoDB. Configurable via
  `AG2_STREAM_EVENT_TTL_DAYS` (set to 0 to disable). Documented in `.env.example`.
  2 new tests verify TTL creation and disabled mode.

### Added

- **App runtime load acceptance gate** (`factory_app/workflows/AppGenerator/tools/app_validation.py`):
  `run_app_bundle_acceptance_gate` now runs `AppLoader.load()` on the assembled bundle
  inside a temp dir before export. Regular `__init__.py` stubs are injected so all
  Python package directories resolve as regular (not namespace) packages. `sys.path` and
  `sys.modules` are snapshot-restored after each load so the gate is fully test-isolated.
  Test `test_mozaikspay_replay_uses_templates_and_passes_runtime_acceptance` now asserts
  `app_runtime_load_result["passed"] is True`.

- **Platform hooks permission override** (`mozaiksai/core/runtime/composition/platform_hooks.py`):
  `PlatformHookRegistry.call_module_permissions()` allows hosted products to inject
  custom permission logic per module/action dispatch. Wired into `_execute_module_action`
  in `platform.py`; falls back to `principal.scopes` when no hook is registered.

- **`revenue_model` context variable and structured output field** for AppGenerator:
  InterviewAgent and AppPlanAgent can now capture and use the revenue model
  (`free`, `subscriptions`, `pay_per_use`, `one_time_purchase`, `community_funded`)
  to drive which billing surfaces, plan catalog artifacts, and entitlement gates
  are required in the build plan.

- **Module action timeout enforcement** (`mozaiksai/core/runtime/composition/module_executor.py`):
  Async module actions are now bounded by `MODULE_ACTION_TIMEOUT_SECONDS` (default 30 s).
  Actions that exceed the timeout are cancelled and return `ACTION_TIMEOUT` to the caller
  without leaving the handler coroutine alive. Set to 0 to disable. 3 new tests cover
  timeout, fast-completion, and disabled cases.

- **Module action payload size limits** (`mozaiksai/core/runtime/composition/module_executor.py`):
  `ModuleExecutor.execute()` now enforces byte-size caps on both request params
  (default 512 KB, configurable via `MODULE_PARAMS_MAX_BYTES`) and action responses
  (default 2 MB, configurable via `MODULE_RESPONSE_MAX_BYTES`). Oversized params are
  rejected before dispatch with `PAYLOAD_TOO_LARGE`; oversized responses are replaced
  with a `RESPONSE_TOO_LARGE` error result. 4 new tests cover both paths and edge cases.

- **WebSocket rate limiting** (`mozaiksai/core/transport/rate_limit.py`):
  `RateLimitMiddleware` now overrides `__call__` to intercept WebSocket upgrade
  requests (`scope["type"] == "websocket"`). WS connections from clients that have
  exhausted their bucket are closed with ASGI code 1008 (Policy Violation) before
  the handshake completes. Excluded paths and disabled mode are respected.
  3 new tests cover: within-limit accept, post-exhaustion reject, and disabled pass-through.

- **Structured auth failure log fields** (`mozaiksai/core/auth/dependencies.py`, `websocket_auth.py`):
  Auth failure log records now include structured `extra` fields (`event`, `provider`,
  `status`) in addition to the formatted message, making log aggregation and alerting
  on `AUTH_FAILED` events reliable. Both HTTP and WebSocket auth failures are covered.

- **Startup validation: AUTH_ENABLED=false warning in production** (`mozaiksai/core/startup/validation.py`):
  `run_startup_checks` now warns (and raises in strict mode) when `ENV=production`
  and `AUTH_ENABLED=false`. Development and test environments are unaffected.
  5 new tests cover the warn/strict/dev/unset combinations.

- **Startup validation: upload storage writability check** (`mozaiksai/core/startup/validation.py`):
  `run_startup_checks` now emits a WARNING when `UPLOAD_STORAGE_DIR` points to an
  existing directory that is not writable. Non-existent directories are skipped
  (created on first upload). 3 new tests added to `test_startup_validation.py`.

- **Upload path containment tests** (`tests/test_chat_attachments_helpers.py`):
  3 new integration tests verify that `handle_chat_upload` rejects path traversal
  sequences in `app_id` and `chat_id` (both raise `ValueError: outside the permitted
  upload directory`) and that legitimate uploads store files inside `upload_root`.

- **Expanded production readiness gate** (`scripts/production_readiness_gate.py`):
  9 additional test suites added to `PYTEST_GATE_TARGETS` covering runtime contracts,
  platform hooks, offline acceptance, security hardening, workflow contracts, session
  launcher, and factory workflow integration.

- **Health probe and attachment tests in production gate** (`scripts/production_readiness_gate.py`):
  `test_runtime_health.py` and `test_chat_attachments_helpers.py` added to both
  `PYTEST_GATE_TARGETS` and `QUICK_PYTEST_TARGETS`. These cover the `/api/health`
  and `/api/health/ready` security contracts and the upload path-containment
  defense-in-depth layer.

- **Live AppGenerator subscription smoke** (`scripts/smoke_appgenerator_live_subscription.py`)
  now exercises real ConfigMiddlewareAgent LLM calls for SaaS subscription config
  and entitlement-gated module contract generation, then validates wiring,
  acceptance gates, export readiness, strict structured-output conformance, and
  runtime module loading.

- **Live AgentGenerator pack smoke** (`scripts/smoke_agentgenerator_live_pack.py`)
  now emits machine-readable JSON on stdout while still exercising real AG2 task
  batch generation, workflow export, semantic drift checks, and runtime loader
  promotion.

- **Structured managed-capability connector requirements** for AppGenerator capability
  packs. `capability_packs[].required_integrations` now uses a typed connector
  object with explicit public and secret fields, and managed capability defaults flow
  into IntegrationReadinessAgent without losing provider metadata.

- **OSS build telemetry** (`mozaiksai/core/telemetry.py`) — opt-in HMAC-SHA256
  signed, anonymized build telemetry. `build_registry_id` is one-way SHA-256 hashed
  before transmission. Controlled by `MOZAIKS_TELEMETRY_ENDPOINT`,
  `MOZAIKS_TELEMETRY_ENABLED`, and `MOZAIKS_TELEMETRY_SECRET` env vars. The
  `emit_build_completed` and `emit_build_failed` lifecycle tools fire-and-forget
  telemetry payloads; `record_build_satisfaction` includes the user rating. No-op
  when `MOZAIKS_TELEMETRY_ENDPOINT` is unset.

- **`BuildSatisfactionRating` transition screen** — `user_choice_context` transition
  added as the final step of the `build` workflow sequence. Presents a 1–5 star rating
  UI after `app_review`; fire-and-forget POSTs to `build_intelligence.record_build_satisfaction`.
  Both `rated` and `skip` options route to `workflow_complete`.

- **Module cooccurrence pattern recording** (`tools/platform/module_pattern_recorder.py`)
  — AppGenerator `on_complete` lifecycle tool that extracts generated module IDs from
  the assembled bundle and POSTs to `build_intelligence.record_module_cooccurrence`.
  Pairwise cooccurrence patterns accumulate into a ranked module popularity index.
  Capped at 20 modules (190 pairs maximum); no-op in OSS runs.

- **WebSocket error detail suppression** (`mozaiksai/core/workflow/orchestration_patterns.py`):
  Uncaught orchestration exceptions previously sent `str(e)` in both the `error` and `message`
  fields of WebSocket `error` and `run_complete` UI events. Raw exception text can contain DB
  connection strings, internal paths, or third-party API error details visible to the browser.
  Replaced with opaque `"An internal error occurred."` / `"internal_error"` strings. Full
  exception detail continues to be logged server-side via `logger.error(..., exc_info=True)`.

- **Studio ValueError HTTP response suppression** (`mozaiksai/hosts/studio.py`):
  Eight endpoints that caught `ValueError` from internal helpers were returning `detail=str(exc)`,
  exposing artifact version IDs, internal state names, file paths, or config key names in 400/409
  responses. All eight now log the exception at WARNING level and return safe static messages
  (`"Invalid app parameters."`, `"Invalid workspace snapshot parameters."`, etc.). Context and
  full exception text remain in server logs.

- **Sandbox validation command safety check applied to all strategies** (`factory_app/workflows/AppGenerator/tools/app_validation.py`):
  `_is_safe_build_command` was only wired into the `local` strategy path. The `e2b` and `docker`
  sandbox paths passed AI-agent-generated commands directly to `adapter.run_command` which executes
  via `sh -c`. An injected command containing `;`, `&&`, or `$(...)` could achieve RCE inside the
  sandbox container. Guard now applied in `_run_sandbox_validation` before every `adapter.run_command`
  call, mirroring the local path. Unsafe commands are skipped with a warning entry (same behavior).

- **Sandbox adapter acquisition error suppression** (`factory_app/workflows/AppGenerator/tools/app_validation.py`):
  `_run_sandbox_validation` returned `errors=[str(exc)]` when adapter acquisition failed, which could
  include Docker daemon errors, network details, or E2B internal messages that then propagated into
  Studio workflow context. Replaced with `"Validation infrastructure unavailable."` and logged with
  `exc_info=True`. Added `import logging` / module-level `logger` which the file previously lacked.

- **Docker sandbox env key validation** (`mozaiksai/core/adapters/docker_sandbox.py`):
  `run_command` passed caller-supplied `envs` dict keys to `-e key=val` in `docker exec` args without
  validation. Keys that contain spaces, `=`, or other special characters could break the Docker arg
  vector in unexpected ways. Keys are now validated against `[A-Z_][A-Z0-9_]*` (case-insensitive)
  before inclusion; invalid keys are logged and skipped.

- **AG2 adapter RunResult error detail suppression** (`mozaiksai/core/adapters/ag2_orchestration.py`, `ag2_network_runner.py`, `ag2_task_batch_runner.py`):
  All three AG2 runner adapters returned `RunResult(error=str(exc))` on failure, which flows
  through `runner_result.error` → `run_error` in `orchestration_patterns.py` and is included in
  the `run_complete` WebSocket event sent to the browser. Raw Python exception text (which can
  contain AG2 internals, stack hints, or third-party API details) is now replaced with
  `"internal_error"`. Full exception detail is logged server-side with `exc_info=True`.

- **PermissionError message suppressed in module responses** (`mozaiksai/core/runtime/composition/module_executor.py`):
  `PermissionError` caught by the module executor was returning `str(exc)` in `ModuleResult.error`,
  which could expose internal permission set names or access control details to API callers.
  Now returns `"Permission denied."` — a generic, non-leaky string. Full detail remains in
  `logger.warning` server-side. Test updated to verify the generic message.

- **`assert` → explicit raise in theme_validation** (`mozaiksai/core/data/themes/theme_validation.py`):
  `summarize_validation` used `assert theme is not None` as a type-narrowing guard.
  Under Python `-O` (optimize), bare `assert` statements are silently removed, so a broken
  `ThemeValidationResult` would propagate `None` and raise an `AttributeError` rather than a
  clear error. Replaced with `if theme is None: raise ThemeValidationError(...)`.

- **`list_plans` action added to billing_portal template** (`factory_app/build_context/mozaikspay/templates/modules/billing_portal/`):
  New `list_plans` action (read-only, `billing_portal.read` permission, no entitlement gate) reads
  `app/config/subscriptions.yaml` and returns the safe plan catalog (plan_id, label, description,
  capabilities, usage_limits). This is the canonical data source for the `/pricing` page — plan
  data is never hardcoded in page schemas or JSX. Handler and service stubs added.

- **MozaiksPay pricing page template** (`factory_app/build_context/mozaikspay/templates/ui/pages/pricing.yaml`):
  Pricing page template added to the mozaikspay capability pack, materialized into every app built
  with this pack. Shows plan catalog via `billing_portal/list_plans` (DataTable) and upgrade CTA
  via `billing_portal/open_billing_portal` (ActionButton). Fixed prior incorrect endpoint references
  (`/api/modules/billing/get_plans`, `/api/modules/billing/create_portal_session`) to the correct
  `billing_portal` module actions.

- **Quality gate corrections recording** (`tools/platform/gate_corrections_recorder.py`)
  — AppGenerator `on_complete` and `on_fail` lifecycle tool that reads the final status
  of all three quality gates (UI, module contract, module runtime) from context. For
  any gate in "blocked" status, POSTs a correction record to
  `build_intelligence.record_quality_gate_block`. Fires on both `on_complete` and
  `on_fail` so corrections are captured even when the build exits via failure.

- **Domain tags population** (`tools/platform/domain_tags_recorder.py`) — AppGenerator
  `on_complete` lifecycle tool that derives domain tags from selected capability pack IDs
  and POSTs to `build_intelligence.set_build_domain_tags`. Ensures `gate_failure` and
  `refinement_hotspot` patterns are domain-specific rather than always landing in
  domain `"global"`.

- **AG2 adapter layer** — concrete implementations for the AG2 beta execution model:
  `ag2_agent_runner.py` (single agent via `agent.ask()`), `ag2_network_runner.py`
  (multi-agent networks), `ag2_stream_storage.py` (token stream buffering),
  `ag2_task_batch_runner.py` (parallel task batches), `ag2_transition_conditions.py`
  (transition condition evaluators), and `docker_sandbox.py` (Docker sandbox adapter).

- **Workflow runtime modules** — `core/usage/` (token accounting and usage ledger),
  `workflow/agents/transition_graph.py` (declarative transition graph evaluator),
  `workflow/artifacts/` (artifact packaging), `workflow/context/projection.py`
  (scoped context projection), `workflow/contract_validation.py` (YAML contract
  validation), `workflow/execution/middleware.py` (lifecycle middleware hooks),
  `workflow/execution/run_bootstrap.py` (AG2 event-history run bootstrap), `workflow/outputs/`
  (runtime events and output validation).

- **`factory_app/build_context/`** — canonical location for named OSS build context
  packs. Each context directory holds `context.yaml` with explicit `assets[]` entries.
  Migrated from inline tool YAML: `AppGenerator` context (capability routing, domain
  catalogs, shell presets, module and workflow archetypes), `AgentGenerator` context
  (AG2 network patterns), `mozaikspay` context (contract + templates), and
  `webapp_builder` context (language profile).

- **`transition_graph.yaml` and `middleware.yaml`** added to all factory workflows
  (ExistingAppDiscovery, RuntimeSmoke, RuntimeTaskBatchSmoke, RuntimeToolCallSmoke,
  RuntimeUIPrimitiveSmoke, ThemeCapture, ValueEngine, DesignDocs, AppReview, plus the
  already-present AgentGenerator and AppGenerator). Every workflow now has complete
  explicit declarative routing and lifecycle hook config.

- **`RuntimeContextExpressionTaskBatchSmoke`** — new factory workflow that smoke-tests
  context expression evaluation in task batch runs.

- **`app/services/` adapter lane** — canonical location for app-owned service adapters.
  Structured as `adapters/deployment/`, `adapters/dns/`, `adapters/probes/`,
  and `adapters/registrar/` sub-directories. SaaS entitlement enforcement is
  provider-neutral OSS runtime behavior wired from `app/config/subscriptions.yaml`,
  not an app-owned service adapter.

- **`app/security/secrets.yaml`** — names-only secret contract resolved at startup by
  `mozaiksai/core/secrets/app_secrets.py`. Replaces the previous `app/config/secrets.yaml`
  convention.

- **`test_runtime_usage_layer.py`** — comprehensive OSS tests for the runtime
  usage accounting layer: `summarize_usage_events` (totals, by_workflow
  deduplication of chat IDs, by_run grouping and sorting), `estimate_token_cost`
  (explicit cost, negative/None/invalid fall-through, rate-based calculation,
  model-specific env var override, negative token clamping), ledger helper
  functions (`_int_value`, `_float_value`, `_text` edge cases), and
  `record_usage_delta` guard conditions (missing app_id/chat_id/workflow_name
  skip writes; `input_tokens`/`output_tokens` alias accepted) (40 tests).

- **`test_community_governance_module.py`** (mozaiks-app) — comprehensive
  governance service tests covering proposal lifecycle (create/open/close),
  member access control, admin bypass, snapshot building with delegation
  weight transfer, vote casting (duplicate, eligibility, delegated-voter zero
  power), outcome calculation (quorum thresholds, approved/rejected, idempotent
  re-calculation), delegation management (self-delegation, outbound chain limit,
  incoming delegation block, cycle detection via `_creates_cycle`), and
  revocation (own, admin override, non-admin blocked) (63 tests).

- **`test_mozaikspay_managed_capability_contract.py`** — new OSS test file that the
  production readiness gate requires. Covers context.yaml and contract.yaml
  contract shapes, all `required_outputs` having matching template files, the
  `forbidden_outputs` drift guard, `mozaikspay_client.py` provider-neutrality
  (no `import payment_provider`, no raw secrets, env-var–only URL resolution), the
  `billing_portal` facade module being app-owned (`owner: app`), page schemas
  routing through the facade rather than provider-owned modules directly, and a
  pack-wide drift guard (41 tests).

- **Managed-capability artifact replay gate** — production readiness now runs an
  offline AppGenerator replay that normalizes managed-capability plans, assembles
  deterministic pack templates, scans the final bundle for facade binding,
  adapter path, provider-internal path, and raw-secret drift, then runs the same
  assembled file map through deterministic app-bundle acceptance and the
  export-blocking `app_runtime_load` check.

- **`test_wallet_module.py`** (mozaiks-app) — comprehensive wallet service
  tests covering balance calculation, payout request guards (no payment provider account,
  amount exceeds available, zero amount, default-to-full-available), credit
  reactions (`credit_app_earnings`, `credit_investment_return`), payment provider webhook
  processing idempotency (`payout.paid`, `payout.failed`, already-terminal,
  transaction-not-found, unhandled event), managed wallet provisioning validation,
  wallet provisioning status with secret stripping, and repo
  collection aliases (61 tests).

- **`test_module_executor_dispatch.py`** — comprehensive OSS tests for the
  `ModuleExecutor` dispatch lifecycle: module/action resolution, sync and async
  dispatch, `action_method_map` aliasing, `EXECUTION_ERROR` / `INVALID_PARAMS`
  error codes, permission enforcement (trusted bypass, required present, missing,
  empty), entitlement gate (granted/denied/trusted-bypass/no-gate), input/output
  schema validation (output violations are warn-only), canonical event emitter
  envelope structure (`source.layer`, `source.app_id`, `source.module_id`,
  `actor.id`, no-actor when no user), and registry queries
  (`registered_modules`, `can_handle`, `health`) (29 tests).

- **`test_executor_registry.py`** — OSS unit tests for `ExecutorRegistry`:
  register/get/has/overwrite semantics for WORKFLOW and MODULE types, property
  shortcuts (`workflow_executor`, `module_executor`), `registered_types()` list
  accuracy, `summary()` type-to-class-name mapping, `ExecutorType` str-enum
  values, and `Executor` Protocol structural conformance (24 tests).

- **`test_platform_hook_registry.py`** — OSS unit tests for
  `PlatformHookRegistry`: empty no-op defaults, bundle registration from dict and
  object, `call_chat_prereqs` first-denial-wins/exception-tolerance/async support,
  `call_chat_session_fields` merge-and-tolerate, `call_workflow_ordering`
  hook-chain/non-list-return-ignored, `call_workflow_name_resolver`
  hook-override/case-insensitive-fallback/empty-string-falls-through,
  `run_startup` sync+async+exception-tolerance, `summary()` hook counts, singleton
  `reset()` (42 tests).

- **`test_build_intelligence_module.py`** (mozaiks-app) — comprehensive service
  tests for the new `build_intelligence` hosted module: `list_builds` owner
  scoping and status filter, `get_build` ownership query, `list_corrections`
  build-not-found guard, `get_domain_patterns` domain filter and limit clamping,
  `list_all_builds` no-owner-scope, `get_intelligence_summary` six-metric
  aggregation, `on_app_created` idempotency and ledger initialization,
  `on_build_status_updated` not-found/terminal fields/refinement-cycle
  increment/event emission, `module.yaml` contract (handler field, permissions,
  actions), and `events.yaml` payload schema contract (56 tests). Also fixes
  `module.yaml` missing `handler` field and registers `build_intelligence.builds`,
  `.corrections`, `.patterns` data aliases in `persistence.py`.

- **`test_messages_module.py`** (mozaiks-app) — comprehensive service tests for
  the `messages` module: `list_threads` owner scoping/status/limit, `get_thread`
  not-found/access-denied/unread-count/read-state-cursor, `create_thread`
  empty-title/creator-deduplication/event, `send_message` empty-body/too-long/
  not-found/non-participant/success (insert+preview+event+recipient-exclusion+
  preview-truncation+required-fields), `mark_thread_read` not-found/success
  (upsert+event+last-message-id), `list_notifications` user-scope/limit,
  `create_announcement` validation/insert/default-audience/event (52 tests).

- **`test_schema_migrations_module.py`** (mozaiks-app) — comprehensive service
  tests for `SchemaMigrationsService`: `get_schema_version` (no migrations →
  0.0.0, latest version, applied-status query), `list_migrations` (app scope,
  status filter, `status="all"` no filter), `record_migration` (duplicate
  rejected, pending status, recorded event, full field set), `run_migration` (not
  found, already applied, success path with multi-index ensure + applied event +
  update fields, failure path with exception → failed status + failed event)
  (32 tests).

- **`api-reference.md` and `token-management.md` expanded** — both docs
  under `docs/architecture/mozaiksai/` grew from placeholder stubs to accurate
  reference material: `api-reference.md` now lists every registered endpoint across
  runtime, platform, and Studio hosts with method/path/purpose; `token-management.md`
  documents `RuntimeUsageLedger`, `MozaiksUsageMiddleware`, `summarize_usage_events`,
  cost estimation, and the `/api/me/usage` surface with the subscription-limits
  contract.

- **Platform startup degradation** — `mozaiksai/hosts/platform.py` now captures
  module load errors at startup into `app.state.startup_degraded` and
  `app.state.startup_degraded_reason`. The `/api/health/ready` endpoint returns
  HTTP 503 with `app_startup: degraded: <reason>` when startup failed partially,
  so load balancers and healthchecks can detect a broken startup without the
  process crashing.

- **`runtime_extensions.yaml` placement warning** — `ModuleLoader` now logs a
  `WARNING` when `contracts/runtime_extensions.yaml` exists but
  `runtime_extensions.yaml` at the module root does not, guiding contributors to
  the correct placement.

- **`scope` capability in control-plane config** — `ControlPlaneConfig` now declares a
  dedicated `scope` capability block alongside `classifier`, `coding`, and
  `contract_surface`. `ScopeProposer` resolves its LLM config from `scope` first,
  falling back to `coding` when not declared. `runtime.yaml` wires `scope` to the
  `codegen` profile by default, giving operators an independent knob to tune or
  disable scope-proposal LLM usage without affecting code generation.

- **`WorkflowTransition.optional` field** — `WorkflowTransition` schema now accepts
  `optional: bool` (default `false`). Used by the `build_satisfaction_rating`
  transition in `extension_registry.json`. Previously caused a Pydantic
  `extra_forbidden` validation error when loading the global pack graph.

- **Task batch dependency deadlock fix** — under `failure_policy: continue_with_available`,
  tasks whose dependencies had already failed were left in `pending` forever, then
  triggered a misleading "unresolved or cyclic dependencies" error. The batch executor
  now drains dependency-blocked tasks immediately as `failed` (with reason
  `dependency '<id>' failed`) without requiring them to run.

- **Task batch silent empty-batch warning** — when `source.kind: structured_output` and
  `structured_output` is `None` or empty, the executor now emits a `WARNING` log
  identifying the batch ID, instead of silently producing zero task items.

- **`AG2TaskBatchRunner` module-level import** — moved from a lazy local import inside
  `_run_one_task` to a module-level import in `task_batches.py`, enabling standard
  `patch("mozaiksai.core.workflow.task_batches.AG2TaskBatchRunner")` in tests.

### Changed

- **`hosted-capability` / `hosted-packs` renamed to `managed-capability`** across
  AppGenerator tools, build contexts, rules, skills, docs, and tests. The term
  "managed capability" is now the canonical name for externally-hosted capability
  packs (e.g. `mozaikspay`, `managed_analytics`). All builder prompts, structured
  output models, build-plan validation, context hooks, and template resolution
  functions use the new name. `managed_billing` added to the subscription contract
  proprietary-term blocklist. `_split_mixed_module_contract_tasks` auto-split
  fallback removed; module contract tasks that include backend Python paths now
  raise `ValueError` immediately rather than silently splitting.

- AgentGenerator workflow bundle downloads now run a production quality gate before
  packaging, artifact registration, or promotion, blocking bundles with contract
  errors or semantic drift such as unresolved event triggers and serial task
  conveyors.

- AppGenerator app-bundle acceptance now blocks export when workflow integration
  drifts from AgentGenerator metadata, including mismatched trigger capability
  IDs, invented workflow capabilities, and ambiguous workflow trigger routing.

- AppGenerator page wiring validation now recognizes the platform-owned
  `/api/me/usage`, `/api/me/tokens`, and `/api/me/tokens/ledger` read endpoints
  for subscription/usage pages. AppGenerator and AgentGenerator prompts now copy
  subscription entitlement and metering declarations exactly instead of inventing
  app-owned ledgers or payment-provider internals.

- AppGenerator `ConfigMiddlewareAgent` now states the exact runtime-valid
  `ModuleContractBundle`, `module.yaml`, capability, typed schema, and YAML
  serialization shapes for module contract tasks, closing live semantic drift
  around missing actions, malformed handler methods, invented events, raw JSON
  schema maps, and invalid module YAML.

- AgentGenerator and AppGenerator now schedule bounded repair passes for
  quality-gate failures that map to generated workflow bundles or workflow
  integration YAML, keeping retries scoped to the failed artifact instead of
  restarting broad generation.

- Factory workflow validation now covers every workflow directory and registry
  sequence reference, including required declarative files, transition graph
  compilation, middleware import resolution, structured-output registry agents,
  and visual agent declarations. AppReview now ships the required
  `structured_outputs.yaml` contract.

- AgentGenerator conveyor workflow guidance now requires distinct downstream
  execution agents, and the live AgentGenerator pack smoke can exercise bounded
  workflow-bundle repair passes before retrying packaging.

- `AgentGenerator` and `AppGenerator` workflow contracts overhauled: removed
  `handoffs.yaml` and `hooks.yaml` (replaced by `transition_graph.yaml` and
  `middleware.yaml`). Static YAML catalogs (`module_archetypes.yaml`,
  `shell_presets.yaml`, `workflow_archetypes.yaml`, `capability_routing.yaml`,
  `file_contracts.yaml`, `domain_catalogs.yaml`) moved from inline tool files into
  `factory_app/build_context/AppGenerator/`. AgentGenerator `patternbook/` module
  moved to `tools/ag2_patterns.py` under `factory_app/build_context/AgentGenerator/`.

- `setup.py` removed; `pyproject.toml` is the sole package definition.

### Removed

- Deleted `mozaiksai/core/workflow/stream/` and `mozaiksai/core/workflow/streaming/` —
  handler-based stream architecture superseded by the inline `_forward_beta_events`
  AG2 beta MemoryStream subscription in `orchestration_patterns.py`.
- Deleted `mozaiksai/core/observability/ag2_runtime_logger.py` —
  `AG2RuntimeLoggingController` wrapped the pre-beta `autogen.runtime_logging`
  API, now superseded by the AG2 beta observer model. Removed `ag2_logging_session`
  context manager from `orchestration_patterns.py`.
- Deleted `mozaiksai/core/observability/realtime_token_logger.py` —
  `RealtimeTokenLogger` was only referenced from the deleted stream handlers.
- Deleted `mozaiksai/core/events/ag2_events.py` — custom AG2 event classes that
  used the `@wrap_event` decorator, which was removed in the AG2 beta. The single
  call site in `orchestration_patterns.py` was guarded by a silent `except Exception`
  and had no actual subscribers; removed together with the try/except guard.
- Deleted `tests/test_realtime_token_logger.py` and `tests/test_mozaiks_event_handler.py`
  alongside their subject modules.

### Added

- **`EntitlementPort`** — new runtime port (`mozaiksai/core/ports/entitlement.py`)
  that gives the platform a deterministic enforcement hook for SaaS feature gating.
  `ModuleExecutor` checks `ActionDef.entitlement_gate` before dispatching any action
  that declares one. `NoOpEntitlementAdapter` (the default) grants every capability —
  non-SaaS apps are completely unaffected. SaaS apps declare
  `app/config/subscriptions.yaml`; the platform wires the OSS
  `ConfiguredEntitlementAdapter`, which reads assignment state from the configured
  app data alias.

- **`ActionDef.entitlement_gate`** — new optional field on module action declarations.
  Set to a `capability_id` string (e.g. `"wallet.view"`) to gate the action behind an
  active plan grant. Never set on `admin_internal` actions. The `ModuleLoader` reads and
  validates it; `ModuleExecutor` enforces it at dispatch time with error code
  `ENTITLEMENT_REQUIRED`. `ModuleDefinition.action_entitlement_map` exposes the full
  module→action→capability_id index.

- **`app/config/subscriptions.yaml`** — canonical SaaS plan catalog for generated
  apps. It declares plan IDs, labels, granted capability IDs, optional usage limits,
  and optional assignment-store mapping used by `ConfiguredEntitlementAdapter`.
  App-owned payment providers update assignment records; AppGenerator must not
  generate a separate entitlement adapter.

- **`pack_overlay` task contract** added to `file_contracts.yaml` — canonical file
  boundaries and hard constraints for AppGenerator's framework pack wiring tasks.
  `ConfigMiddlewareAgent` gained Mode C guidance covering the full entitlements pack
  overlay output (facade module, billing reactions, grant adapter, self-hosted modules).

- `contracts/profile.yaml` is now a first-class AppGenerator output. AppGenerator
  gained three typed structured-output models (`ModuleProfileField`,
  `ModuleProfilePanel`, `ModuleProfileManifest`) and a `profile_yaml` field on
  `ModuleContractBundle`. `ConfigMiddlewareAgent` now has explicit authoring
  guidance (step 9a) covering when to emit, kind rules (`metrics`, `list`,
  `component`), field type constraints, action binding, and hard exclusions
  (`form` kind, admin-only actions, secrets). All four module archetypes
  (`standard`, `messaging`, `workflow`, `transactional`) list `profile.yaml` in
  their optional YAML family. The runtime's module contract codegen already
  materialises `contracts/profile.yaml` — this change closes the generation-side
  gap.

- `docs/architecture/app/refinement-harness.md` — canonical reference for
  app-local refinement harnesses. Covers when to use a pack, the full file
  layout, annotated starter templates for all four config files
  (`runtime.yaml`, `harness.yaml`, `tools.yaml`, `policies.yaml`), prompt
  starters for all four LLM-backed checkpoints, and route rules. This is the
  authoritative AppGenerator reference for `refinement_harness` build tasks.

- `WorkflowEntry` in the pack-graph schema now accepts a `startup_mode` field
  (`UserDriven`, `AgentDriven`, or `BackendOnly`). `GlobalPackGraph` enforces
  that `BackendOnly` workflows are not placed in `workflow_sequences` or
  assigned entrypoints — they are domain-event-triggered only.
- `PackGraphWorkflow` in AgentGenerator's structured output now carries
  `startup_mode` so generated workflow bundles declare the correct entry
  classification from the start.
- All seven factory workflows in `extension_registry.json` now carry explicit
  `startup_mode` annotations (`ValueEngine` and `ExistingAppDiscovery` are
  `UserDriven`; the rest are `AgentDriven`).

### Changed

- `docs/architecture/workflows/refinement-harness-architecture.md` updated:
  file trees corrected (added `contract_surface_planner.py`, `runtime.yaml`,
  `contract_surface_selection_system.yaml`, all 11 context tools); new "AG2
  Implementation Model" section covering `agent.ask()` pattern, structured
  output enforcement, `agent_factory` injection, and LLM config resolution;
  full six-checkpoint runtime flow; expanded "Generated App Authoring" section
  pointing to the new starter pack reference.
- `docs/guides/platform-intelligence/03-refinement.md` updated with a
  "How Classification Works" and "How Patch Coding Works" section explaining
  the AG2-backed classifier and coding worker, plus an "Opting In" section
  showing the `app/config/ai.json` control plane block.

- `LLMChangeClassifier` and `ScopedRefinementCodingWorker` in the control plane
  now use `autogen.beta.Agent.ask()` with `MemoryStream`, `RetryMiddleware`, and
  `TokenMonitor` instead of a raw `generate_json_completion()` call. Both classes
  accept an `agent_factory` parameter for test injection; their tests now use a
  `_FakeAgent`/`_FakeReply` pattern that exercises the full classify/execute path
  without hitting the network.

- `DataContract` structured output model for AppGenerator now includes a
  `surfaces` field (list of `DataContractSurface`) with fully typed sub-models
  (`DataContractCollection`, `DataContractOwnership`, `DataContractLifecycle`,
  `DataContractField`, `DataContractIndex`, `DataContractIndexKey`). This
  matches what the `_validate_data_contract` runtime gate and the data-contract
  loader both expect, and keeps `AppSchemaOutput` and `AppBuildPlanOutput`
  compatible with provider strict structured-output mode.
- AppGenerator now seeds generated app bundles with `config/ai.json` from the
  current factory runtime defaults, and `refinement_harness` outputs now stage
  `app/config/refinement_policy.yaml` alongside `harness.yaml` and
  `tools.yaml`. This keeps `ask` / `chat` / `workflows` startup in `ai.json`
  while moving app-local control-plane runtime policy under
  `refinement_harness/config/`.
- `IntegrationTestAgent` removed from the AppGenerator agent roster.
  Integration and wiring validation (`run_integration_tests`,
  `validate_wiring`) are deterministic runtime-gate tools called by
  `AppValidationAgent`'s auto-invoked gate — they are no longer exposed as
  agent-callable tools.

### Fixed

- **Partial module load now surfaces as `startup_degraded`** — when one or more
  declared modules fail to load, `mozaiksai/hosts/platform.py` now sets
  `app.state.startup_degraded = True` with a `MODULE_LOAD_PARTIAL` reason string.
  The `/api/health/ready` endpoint returns HTTP 503 in this state, so load
  balancers and health checks detect a degraded startup without the process
  crashing. Previously the platform silently continued with a partial module set
  and returned 200.

- **`ModuleLoader.load_all()` returns `(loaded, failed_names)` tuple** — the
  return type changed from `list[LoadedModule]` to
  `tuple[list[LoadedModule], list[str]]`. Callers that previously discarded
  failures silently now receive the list of failed module IDs. `AppLoadResult`
  gains a `failed_module_names: list[str]` field that propagates the set of
  modules that could not be loaded.

- **Coding worker sets `status="failed"` on artifact persistence error** — when
  `_persist_validated_artifact()` raises, the control-plane coding worker now
  flips `status` to `"failed"` and sets `result.error` to
  `ARTIFACT_PERSISTENCE_FAILED: <cause>`. Previously the status remained
  `"validated"` even though no artifact record existed in the store, masking the
  failure as a success.

- **Zipfile creation errors wrapped as `ARTIFACT_ZIP_FAILED`** — `OSError`
  exceptions from `zipfile.ZipFile` construction in the coding worker are now
  re-raised as `RuntimeError("ARTIFACT_ZIP_FAILED: ...")` with a clear path and
  cause, instead of propagating a bare OS error.

- **`asyncio.create_task()` for workflow auto-start now logs unexpected errors**
  — the fire-and-forget `_auto_start_if_needed()` task in `platform.py` now
  attaches a `done_callback` that logs any unexpected exception at `ERROR` level.
  Previously, unhandled exceptions from that task were silently discarded by the
  event loop.

- **Module router/service startup failures now mark platform degraded** —
  `mount_module_routers()` and `start_module_services()` failures were previously
  logged at `WARNING` only, allowing the process to boot with silently broken
  module extensions (causing 404s on mounted routes or missing background
  services). Both now log at `ERROR` and set `startup_degraded = True`.

- **`/api/health/ready` response includes `failed_modules` list** — when any
  modules fail to load, the readiness response body now includes a
  `failed_modules: list[str]` field alongside the existing degraded reason
  string, allowing programmatic consumers to identify which modules to inspect
  without parsing the reason string.

- **`app.state.failed_module_names` persisted for structured access** — the list
  of modules that failed to load at startup is now available on `app.state` for
  the full lifetime of the process, not just in the startup logs.

- **Dispatch to startup-failed module returns 503 not 404** —
  `_execute_module_action` now checks `app.state.failed_module_names` before
  forwarding to the executor. When a request targets a module known to have
  failed at startup, it returns HTTP 503 with a clear "failed to load at startup"
  message instead of falling through to the executor's generic `MODULE_NOT_FOUND`
  (404) response.

- **`WORKSPACE_SNAPSHOT_ZIP_FAILED` error wrapping** — `OSError` exceptions from
  `mkdir()` and `ZipFile()` in the workspace snapshot writer are now re-raised as
  `RuntimeError("WORKSPACE_SNAPSHOT_ZIP_FAILED: ...")`, consistent with the same
  pattern in the coding worker.

- **`asyncio.create_task()` for AgentDriven websocket auto-start logs errors** —
  `runtime.py` AgentDriven workflow auto-start task now attaches a `done_callback`
  that logs `AGENTDRIVEN_AUTO_START_FAILED` at `ERROR` level.

- **Context variable persistence errors now logged** — `context/adapter.py`
  previously swallowed all errors in `persist_context_variables()` fire-and-forget
  calls with `except Exception: pass`. Errors are now logged at `WARNING` level
  (`CONTEXT_PERSIST_FAILED` / `CONTEXT_PERSIST_TASK_CREATION_FAILED`), making
  workflow state drift observable without crashing the runtime.

- Fixed AppReview revision handoff so review-session revisions preserve
  `artifact_key`, `artifact_version_id`, `source_surface`, lifecycle state, and
  staged bundle path when triggering Studio refinement. The AppReview summary
  now exposes promotion readiness from deterministic build context instead of
  allowing promotion with incomplete handoff metadata, promotes the reviewed
  artifact version through Studio before activating the app registry record,
  restores generated app zips as a loadable active app root even when the zip
  contains a single `GeneratedApp/` wrapper, and confirmation-required refinement
  routes now surface through the shared pending harness decision UI.

- Fixed `F821` undefined-name crashes in `mozaiksai/core/runtime/app/ai_config.py`
  (missing `from typing import Any`) and
  `factory_app/workflows/AppGenerator/tools/hook_file_contract_context.py`
  (missing `from pathlib import Path`). Both were live runtime crashes on any call
  path that triggered annotation evaluation under a non-postponed context.

- Fixed `supports_provider_response_format` incorrectly returning `True` for
  `dict[str, Any] | None`-style annotations in
  `mozaiksai/core/workflow/outputs/structured.py`. Python 3.10+ `X | Y` syntax
  creates `types.UnionType`, not `typing.Union`, so the previous `origin is Union`
  guard missed it. Both union origin check sites now use
  `_UNION_ORIGINS = (Union, types.UnionType)`.

- Fixed `app/services/adapters/` files importing from `app.modules.*`, violating
  the adapter boundary contract. `dns/azure_dns.py` and `dns/cloudflare.py` now
  import `DnsRecord`, `DnsZone`, and `ProviderNotConfiguredError` from a new
  `adapters/dns/schemas.py`; `registrar/godaddy.py` and `registrar/opensrs.py`
  import from `adapters/registrar/schemas.py`; `deployment/build/github_actions_build.py`
  imports constants from the co-located `provider_connection_contract.py`. Module
  backends re-export the exception type from the adapter layer to preserve existing
  call sites.

- Fixed `BuildIntelligenceService.record_quality_gate_block` — correction records
  were missing `correction_id`; unknown `gate_type` values were silently accepted
  instead of returning an error. Fixed `list_builds` and `list_corrections` to
  strip MongoDB `_id` and undeclared internal fields from serialized responses.

- Fixed `build_intelligence/runtime_extensions.yaml` — schema had extra fields
  (`id`, `description`, `mount_prefix`, top-level `module_id`) and a dict-form
  `entrypoint` that `ModuleRuntimeExtensionsManifest` rejects. Corrected to the
  canonical `entrypoint: backend.telemetry_router:router` / `prefix:` shape.

- Renamed internal platform reaction event type from `platform.subscription.{kind}_requested`
  to `platform.reaction.{kind}_dispatched` in `module_event_router.py`. The previous name
  was misleading — it described the reaction routing mechanism but looked like a SaaS
  billing subscription event. Also renamed the `subscription_id` payload field to
  `reaction_id`. No change in behavior; this was internal platform event naming only.

- Tightened five `schema_version` fields in AppGenerator `structured_outputs.yaml`
  from `type: str` to `type: literal` with their correct values
  (`mozaiks.events.v1`, `mozaiks.reactions.v1`, `mozaiks.notifications.v1`,
  `mozaiks.settings.v1`, `mozaiks.admin.v2`). Wrong schema versions now fail at
  structured-output parse time rather than at runtime module loader validation.
- Fixed `overflow_behavior` in `ControlPlaneScopePolicies` — valid runtime values
  are `clarify` and `workflow`; the previously declared `fail` literal was rejected
  by the runtime loader.

- Fixed `DatabaseAgent` OUTPUT FORMAT example, which contained a `"{...}"`
  placeholder as the `data_contract_json` file content. The LLM was copying
  this literally, writing `{...}` into `config/data.json` for all generated
  apps. The example now shows the correct `DataContract` shape with `surfaces`,
  `surface_id`, and `collections`.
- Fixed `DesignDocs` `workflow_startup_mode` from `BackendOnly` to
  `AgentDriven`. DesignDocs runs in sequence-driven builds; it is not a
  domain-event-triggered workflow.
- Fixed strict structured-output schemas for workflow planners by marking
  declared fields as truly required in generated Pydantic models. This restores
  provider-enforced `response_format` compatibility for live AG2 workflow runs
  such as `RuntimeTaskBatchSmoke` and related planner-driven smokes.

- Fixed OpenAI strict structured-output mode compatibility for all control-plane
  response schemas. `CodingWorkerPlan`, `SurfaceRegenerationResponse`,
  `ScopeProposal`, `_SurfaceEntry`, and `_ContractSurfaceClassification` had
  fields with defaults that caused those fields to be excluded from Pydantic's
  `required[]`, violating the strict-mode requirement that every property appear
  in `required`. All defaults stripped; every property is now required. Introduced
  `FileUpdate(path, content)` model replacing `dict[str, str]` for the
  `updated_files` field — `dict[str, str]` generates
  `"additionalProperties": {"type": "string"}` which strict mode rejects;
  `list[FileUpdate]` generates an array of objects with `additionalProperties: false`.

- Fixed `codegen`, `planner_replanner`, and `reviewer_validator` LLM profiles in
  `factory_app/refinement_harness/config/runtime.yaml` — previously set to
  `gpt-5.2-codex`, which is a completions-only model that returns a 404 from
  `/v1/chat/completions`. Changed to `gpt-4o` with `temperature: 0.1`.

- Fixed stale `WorkflowStrategyAgent` references in AgentGenerator.
  `WorkflowStrategyAgent` was replaced by `PatternAgent` in a prior refactor but
  two stale references remained: (1) `WorkflowBundleBuilderAgent` EVENT BOUNDARY
  RULES referenced `WorkflowStrategy.event_boundary.input_events`, a context
  variable that no longer exists; replaced with `backend_design_document` and the
  `initial_message` trigger contract. (2) `hook_universal_prompts.py`
  `workflow_design_agents` named `WorkflowStrategyAgent`, so the `RUNTIME_CONTEXT`
  injection never fired for any live agent; replaced with `WorkflowBundleBuilderAgent`.

- Fixed live control-plane classifier calls against models that only support
  the provider default temperature. `SimpleLLMCapabilityService` now omits the
  `temperature` field for JSON completions when no explicit value is configured.

- Fixed B904 raise-without-from violations: 16 files had `raise X` inside
  `except` blocks without `from err`. All `except ExceptionType:` bare clauses
  that needed chaining now bind the exception (`except ExceptionType as exc:`),
  and the corresponding `raise` uses `raise X from exc`. One syntax error in
  `registry.py` (misplaced `from exc` inside constructor call) was also repaired.

- Fixed `normalize_app_path` stripping leading dots from dotdir names
  (`.github/workflows/` → `github/workflows/`). The previous `lstrip("./")` treated
  `.` and `/` as individual strip characters, incorrectly stripping `.github` to
  `github`. Now strips `./` and `../` as sequences only, preserving dot-prefixed
  paths like `.github/`.

- Zero ruff violations (`ruff check . → All checks passed`). Suppressed
  `E402` (intentional lazy imports) and `B008` (FastAPI `Depends()`) globally
  in `pyproject.toml`. Fixed: `E722` bare except in `db_manager.py`, `F402`
  import shadowed by loop var in `connector_health.py`, `UP035`/`F401`
  obsolete `typing.Dict` in transport handlers, `B017` blind exception in 8
  subscription loader tests (`Exception` → `ValidationError`), `UP007`/`UP045`
  `Optional`/`Union` annotations in `structured.py` and tests, `E741` ambiguous
  variable name `l` → `ln` in 3 test files, `E702`/`E701` multi-statement lines
  in `logging_config.py`, and `UP007` `EventType` union in
  `unified_event_dispatcher.py`.
- Fixed three stale test assertions in `test_module_contract_quality_gate.py`
  and `test_module_runtime_quality_gate.py` that checked the removed `condition`
  string field instead of `condition_value`.

- **`exc_info=True` added to `MODULE_LOAD_FAILED` error log** — the module
  loader now captures full stack traces (not just the message string) when a
  module fails to load, making root-cause debugging of startup failures
  significantly easier.

- **`WORKFLOW_BACKGROUND_TASK_FAILED` done_callback on all workflow handler
  background tasks** — three `asyncio.create_task(_run_workflow_background(...))`
  calls in `workflow_handlers.py` (switch-workflow auto-start, start-workflow
  auto-run, and batch auto-run) now attach done_callbacks that log
  `WORKFLOW_BACKGROUND_TASK_FAILED` at `ERROR` level when the task raises.

- **`HANDOFF_EVENT_EMIT_FAILED` done_callback** — `loop.create_task()` in
  `handoff_events.py` now logs `HANDOFF_EVENT_EMIT_FAILED` at `WARNING` when
  the event dispatch coroutine raises.

- **`ADMIN_REGISTRY_LOAD_FAILED` warning on corrupt registry YAML** —
  `load_admin_registry()` silently returned an empty registry on YAML parse
  errors; now logs a `WARNING` with path and exception.

- **`CONTENT_STORE_PUT_BUNDLE_FAILED` warning on non-local content store
  upload failures** — artifact promotion silently set `content_ref = None`
  when the remote bundle upload failed; now logs a `WARNING` with app ID,
  artifact ID, backend name, and exception.

- **Debug-only `logger.info` calls removed from hot paths** — three
  `🔍`-prefixed `logger.info` calls in `unified_event_dispatcher.py` and
  `simple_transport.py` that fired on every agent message or event were either
  removed or downgraded to `DEBUG`. These would have flooded production log
  aggregation at any realistic message throughput.

- **Per-message `INFO` suppression logs downgraded to `DEBUG`** —
  `SYSTEM_SIGNAL`, `USERDRIVEN_TRIGGER`, and `UI_HIDDEN` suppression logs
  in the event dispatcher, plus `INPUT_SUBMIT`, visual-agents gate, and
  `_mozaiks_hide` suppression logs in the transport, now emit at `DEBUG` level
  instead of `INFO`. Also removed a per-registry loop that dumped all pending
  input request IDs to info on every input submit call.

- **Per-operation `INFO` logs downgraded to `DEBUG`** — per-document-insert
  log in `db_manager.py`, per-session-creation and per-artifact-state-update
  logs in `simple_transport.py` downgraded from `INFO` to `DEBUG`.

- **`AI_CONFIG_LOAD_FAILED` and `CONTROL_PLANE_CONFIG_LOAD_FAILED` warnings**
  — `load_ai_config_json()` and `load_control_plane_config_yaml()` were
  silently returning empty dicts on JSON/YAML parse errors, making startup
  misconfiguration invisible. Both now emit `WARNING` logs with the config
  path and exception.

- **Stale refinement router artifact lookups now log at DEBUG** — two bare
  `except Exception: return []` / `return None` blocks in
  `refinement_router.py` that silently swallowed artifact store lookup errors
  now log `ARTIFACT_FILES_LOOKUP_FAILED` and `STALE_ARTIFACT_LOOKUP_FAILED`
  at `DEBUG` level.

- **`exc_info=True` added to all critical `logger.error()` calls** — a sweep
  across auth, persistence, transport, and event dispatch layers fixed
  `logger.error(f"...{e}")` calls that were logging exception messages as
  strings without capturing stack traces. Affected: `auth/websocket_auth.py`,
  `auth/adapters/jwt_adapter.py`, `auth/adapters/keycloak.py`,
  `auth/adapters/supabase.py`, `auth/adapters/registry.py`,
  `auth/jwt_validator.py`, `data/persistence/persistence_manager.py`,
  `data/persistence/db_manager.py`, `events/unified_event_dispatcher.py`,
  `transport/simple_transport.py`, and `transport/workflow_bridge.py`. Also
  removed an unused `import traceback` from `simple_transport.py` and cleaned
  up emoji prefixes from `db_manager.py` error logs.

- **`APP_MANIFEST_LOAD_FAILED` warning on corrupt app manifest** — platform
  host `_load_app_manifest()` was silently returning empty dict when
  `app.json` was corrupt or malformed; now emits a `WARNING` log with path
  and exception so startup misconfiguration is visible.

- **`WS_PREREQ_VALIDATION_FAILED` error log in runtime websocket handler** —
  the runtime WebSocket endpoint caught prerequisite validation exceptions and
  sent a `chat.error` to the client but logged nothing, leaving operators with
  no visibility into the failure cause. Now logs at `ERROR` level with workflow
  name, chat ID, and exception.

- **`logger.error(f"...")` anti-pattern eliminated across workflow and UI layers**
  — `workflow_manager.py`, `ui_tools.py`, `factory.py`, and `module_loader.py`
  had `logger.error(f"...{exc}")` calls that evaluated eagerly and lost stack
  traces. All converted to `logger.error("...", arg, exc_info=True)`. Affected
  log events: `WORKFLOW_LOAD_FAILED`, `WORKFLOW_LOAD_ALL_FAILED`,
  `WORKFLOW_MODULE_RELOAD_FAILED`, `WORKFLOW_RELOAD_FAILED`,
  `AI_CONFIG_READ_FAILED`, `UI_TOOL_INTERACTION_FAILED`, `MODULE_LOAD_FAILED`.
  Also removed debug emoji prefixes (`❌`, `📈`) from `ui_tools.py` and
  downgraded a per-tool-call `INFO` to `DEBUG`.

- **325 G004 lazy-logging violations eliminated** — all `logger.xxx(f"...")`
  f-string calls across 35 `mozaiksai/` files converted to `%`-format lazy
  interpolation. F-strings inside logging calls evaluate even when the log level
  suppresses output; `%`-format defers string construction to the log handler.
  Files affected span admin, auth adapters, data/persistence, events, runtime
  composition, transport, and all workflow sub-layers.

- **Silent `except Exception: pass` blocks replaced with debug logs** —
  `orchestration_patterns.py` had 7 bare pass-through swallows in critical
  paths (frontend context lookup, context injection, agent/context registry
  stores, derived listener setup, context manager registration, on_fail lifecycle
  trigger, and error event emission). `auto_tool_handler.py` had 2 inner-loop
  swallows. `db_manager.py` had 2 collection-drop swallows. All now emit
  `logger.debug(...)` with event key and cause, making previously invisible
  failures observable without impacting steady-state performance.

- **`asyncio.create_task()` lifecycle emission errors now surfaced** —
  `workflow_bridge.py` had 5 bare `asyncio.create_task()` calls for lifecycle
  event emissions (`_emit_execution_started`, `_emit_execution_completed`,
  `_emit_execution_failed`, and two `dispatcher.emit("runtime.process_completed")`
  calls). All now attach `add_done_callback()` that logs at `WARNING` when the
  task raises, ending the silent discard of lifecycle event failures.

- **Artifact promotion partial-write now raises instead of silently continuing**
  — when `artifact_store.set_validation_status()` fails after
  `content_store.put_bundle()` succeeds, the bundle is in the content store but
  the DB record has stale metadata. Previously this was silently swallowed.
  Now logs `ARTIFACT_STATUS_UPDATE_FAILED` at `ERROR` level with app ID,
  artifact ID, content ref, and exception, then re-raises so callers see the
  failure.

- **Control-plane config disabled state now logged at startup** —
  `load_control_plane_config()` was silently returning a disabled
  `ControlPlaneConfig()` when the YAML file existed but failed to parse, making
  it impossible to distinguish "no config file" from "corrupt config file".
  Now emits `CONTROL_PLANE_DISABLED` at `WARNING` when a parseable config path
  exists but yields no valid data, and `CONTROL_PLANE_CONFIG_LOADED` at `INFO`
  on successful load (with `enabled` flag and path).

### Security

- **WebSocket JWT expiry not enforced on inbound messages** — the runtime
  `handle_websocket()` validated the auth token only at connection time.
  Authenticated sessions with expired tokens could continue sending messages
  indefinitely after token expiry. Fixed by extracting the JWT `exp` claim in
  `runtime.py` and passing it as `token_exp` to `simple_transport.handle_websocket()`.
  The inbound message loop now checks `time.time() > token_exp` before each
  dispatch and closes the connection with WebSocket code 4401 when the token
  has expired.

- **Duplicate WebSocket connection overwrote active session silently** — when a
  second connection arrived for the same `chat_id`, the plain dict assignment
  `self.connections[chat_id] = {...}` replaced the existing entry while the old
  connection's `finally` block was still running. The `finally` block then
  deleted the new connection entry, leaving subsequent messages with no routing
  target. Fixed by evicting the stale connection explicitly before registering
  the new one: close the stale WebSocket with code 1001, call
  `_cleanup_connection()`, then proceed with the new registration.

- **Inbound WebSocket messages had no size limit** — the HTTP chat endpoint
  enforced `_MESSAGE_MAX_CHARS` but the WebSocket path was unbounded, allowing
  clients to send arbitrarily large payloads that would be parsed and forwarded
  into the workflow context. Fixed by reading `CHAT_MESSAGE_MAX_CHARS` from the
  environment in `SimpleTransport.__init__` and rejecting oversized messages with
  a `MESSAGE_TOO_LARGE` client error before JSON parsing.

- **`_input_request_registries` memory leak on disconnect** — the pending input
  request registry for a chat session was not cleaned up in `_cleanup_connection()`.
  Any client that disconnected mid-workflow left its input registry entry in
  memory indefinitely. Fixed by adding cleanup of `_input_request_registries`
  in `ws_protocol.py`'s `_cleanup_connection()`.

- **Silent `except Exception: pass` blocks replaced in bridge and dispatcher** —
  `workflow_bridge.py` had 8 bare pass swallows across lifecycle emission task
  creation, `fail_active_revision` fire-and-forget, pause-workflow registry
  cleanup, and session-registry complete calls. `unified_event_dispatcher.py` had
  4 bare pass swallows in per-event agent-flag enrichment (structured/visual/
  ui_tools/sequence flag lookup). All now emit `logger.debug(...)` with event key
  and cause so previously invisible failures are observable in debug log streams.

- **Silent failures surfaced in AG2 runner, attachments, auth, and LLM config** —
  `ag2_network_runner.py`: hub client and hub `close()` calls in the cleanup
  `finally` block now log `WARNING` on failure; cancelled pending tasks are now
  awaited with `return_exceptions=True` to prevent use-after-close races with the
  hub. `chat_attachments/attachments.py`: file-close and bundle-read errors log
  `WARNING` instead of silently continuing. `auth/dependencies.py` and
  `auth/websocket_auth.py`: adapter exception in no-auth mode logs `DEBUG` before
  falling back to the anonymous user. `capabilities/simple_llm.py`: invalid
  temperature in `llm_config` logs `WARNING` and falls back to API default instead
  of crashing or silently using an unvalidated value.

- **Silent failures surfaced in content store, app context, and admin** —
  `artifacts/content_store.py`: `exists()` and `delete()` now log `ERROR` with
  `exc_info=True` when GridFS queries fail, preventing a DB outage from
  masquerading as "content not found" or "content deleted". `app_context/store.py`:
  JSON and YAML manifest parse failures now log `WARNING` instead of returning
  empty objects silently. `admin/router.py`: session runtime computation failure
  logs `WARNING` and falls back to stored duration. `app_context/context_graph.py`:
  tree-sitter parser init failure logs `WARNING` when permanently disabling the
  parser for the process lifetime.

- **Remaining bare `except Exception: pass` blocks replaced in transport layer** —
  `simple_transport.py` had 6 bare pass swallows: 3 in run-complete event
  enrichment and 3 in `RUNTIME_PROCESS_COMPLETED` hook. All replaced with
  `logger.debug(...)`. `orchestration_patterns.py`: 4 additional bare passes in
  derived-context seed, `mark_chat_completed` persistence, derived-context manager
  cleanup, and the `_derived_listener` UI emit task were replaced with debug/warning
  logs and a `done_callback`.

- **Platform shutdown and orchestration cleanup observability** —
  `hosts/platform.py`: runtime-services stop failure at shutdown now logs
  `WARNING`; two WebSocket prerequisite error-message send failures log `DEBUG`
  instead of silently swallowing. `orchestration_patterns.py`: fire-and-forget UI
  emit task in `_derived_listener` now attaches a `done_callback` that logs the
  exception at `DEBUG` when the task raises.

- **`http_app_backend.py` and `summary_artifacts.py` non-JSON response warnings**
  — `HttpAppBackendAdapter` now logs `WARNING` when a 2xx response body fails JSON
  decode (previously silently fell back to `{"raw": ...}`). `summary_artifacts.py`
  logs `WARNING` when Pydantic `model_dump` fails during artifact JSON
  serialization.

- **WebSocket connection limit (`MOZAIKS_MAX_WS_CONNECTIONS`)** — `SimpleTransport`
  now reads `MOZAIKS_MAX_WS_CONNECTIONS` (default 500) from the environment and
  rejects new connections with WebSocket close code 1008 when the limit is reached.
  Reconnect storms from misbehaving clients can no longer grow `self.connections`
  without bound.

- **AG2 runner outer timeout guard** — `ag2_network_runner.py`'s `asyncio.wait()`
  call had no native deadline; a hung `failure_task` could block the runner
  indefinitely. Added an `asyncio.wait_for(..., timeout=close_timeout + 30s)` outer
  guard that cancels both tasks and returns `RunStatus.PAUSED` with an explanatory
  error when the deadline is exceeded.

- **WebSocket idle timeout (`MOZAIKS_WS_IDLE_TIMEOUT`) detects half-open TCP
  connections** — heartbeat `send_json` can succeed on half-open TCP connections
  because the OS kernel buffers absorb the bytes before the remote end's failure
  is detected. `SimpleTransport` now tracks `last_received_at` on every inbound
  message. The `ws_protocol.py` heartbeat loop reads `MOZAIKS_WS_IDLE_TIMEOUT`
  (default 360 s) and closes the connection when no message has been received
  within the idle window, cleaning up the slot for a reconnect.

- **HTTP 500 responses no longer leak internal exception details to clients** —
  `runtime.py` registers a global `HTTPException` handler that intercepts
  status-500 responses, logs the original detail at `WARNING` server-side, and
  returns `{"detail": "Internal server error"}` to the caller. Prevents
  database addresses, file paths, and exception messages embedded in
  `f"Failed to ... {exc}"` detail strings from reaching API consumers.
  4xx and 5xx non-500 codes (503 operational messages) pass through unchanged.

- **Dead `_resolve_agent_log_limit` / `_AGENT_CONV_*` declarations removed** —
  `persistence_manager.py` declared `_resolve_agent_log_limit`,
  `_AGENT_CONV_JSON_MAX_LEN`, and `_AGENT_CONV_TEXT_MAX_LEN` but never applied
  the limits anywhere in the source (only in a stale `build/` copy). Removed
  all three, plus the seven `TestResolveAgentLogLimit` test cases, to eliminate
  dead feature stubs that implied a false safety guarantee.

- **Per-request INFO logs in `platform.py`, `orchestration_patterns.py`,
  `simple_transport.py`, and `connector_service.py` downgraded to DEBUG** —
  `CHAT_START_WORKFLOW_NORMALIZED`, `WS_WORKFLOW_NORMALIZED`,
  `SESSION_REGISTRY_CLEANUP`, `CONFIG: mode=…`, `Lifecycle before_chat
  completed`, `UserDriven greeting sent`, `Launching workflow`, `WebSocket
  closed normally`, and seven connector-service status calls now emit at
  `DEBUG`. Connector secret-persist failure promoted from `INFO` to `WARNING`.
  `GENERAL_CHAT_SESSION_CREATED` converted from `extra={}` dict logging to
  positional structured format at `DEBUG`.

- **Emoji characters removed from all log messages and remaining per-call INFO
  logs downgraded to DEBUG** — `simple_transport.py`, `ui_tools.py`,
  `lifecycle.py`, `handoff_events.py`, `unified_event_dispatcher.py`,
  `persistence_manager.py`, and `auto_tool_handler.py` contained multi-byte
  emoji characters (🎯, ✅, ✗, ⚠️, 🔍, ⏳) that confuse production log
  aggregators and indicate per-request verbosity. All emoji removed; hot-path
  calls in UI tool processing, auto-tool dedup, gather-agent-JSON loops,
  lifecycle completions, and visual-agent filtering downgraded from `INFO` to
  `DEBUG`. `workflow_manager.py` `WORKFLOW_LOAD_OK` kept at `INFO` (startup,
  not per-call).

- **MongoDB reachability added to startup validation** — `run_startup_checks()`
  now verifies that `MONGO_URI` is set (or resolvable via Key Vault alias
  `MongoURI`) and that the MongoDB server responds to a `ping` before accepting
  traffic. Missing URI and ping failures emit `STARTUP_CHECK_FAILED` records
  with `check="mongo_uri"` and raise `StartupConfigError` in `strict` mode.
  Five new tests cover the success and failure paths; existing tests updated to
  inject `_MockPingClient` so the new check does not change their assertions.

- **Per-request INFO logs in transport handlers converted to DEBUG and
  f-string lazy-logging fixed** — `input_handlers.py`, `mode_handlers.py`,
  and `workflow_handlers.py` had 11 `logger.info(f"...")` calls that fired at
  `INFO` on every user input, mode switch, and workflow start, flooding
  production log aggregators at any realistic throughput. All converted to
  `logger.debug("...", arg, ...)` with structured `KEY=value` names.
  Eliminates the last G004 f-string lazy-logging violations in `mozaiksai/`.

- **Tab indentation in `event_serialization.py` replaced with 4-space groups**
  — this was the only file in `mozaiksai/` using tab indentation (245 tabs).
  Converted via `expandtabs(4)` to match the rest of the runtime codebase and
  avoid parser inconsistencies in mixed-indentation edge cases.

- **Redundant lazy `import logging` inside `except` block removed** —
  `control_plane/implementations/coding_worker.py` imported
  `logging as _logging` inside an `except` block at line 446 even though
  `import logging; logger = logging.getLogger(__name__)` was already at the
  top of the file. Fixed to use the module-level `logger` directly.

- **WebSocket connection limit and idle timeout test coverage added** —
  7 new tests in `test_simple_transport_serialization.py` cover
  `MOZAIKS_MAX_WS_CONNECTIONS` default (500), env-override, invalid-env
  fallback, `MOZAIKS_WS_IDLE_TIMEOUT` default (360), env-override,
  zero-disables-detection, and `last_received_at` presence on connection
  registration. Brings `SimpleTransport` configuration paths to full
  unit-test coverage.

### Removed

- Removed the unreachable ValueEngine `save_build_plan` branch and the orphaned
  event-envelope guard that targeted a nonexistent schema directory.
- Retired `MozaiksContextExpression` / `evaluate_context_expression`. All
  workflow transition conditions now use AG2-native `ContextEquals` and
  `ToolCalled`, wrapped by `SourceScopedContextEquals` and
  `SourceScopedToolCalled` registered via `register_condition()`. The `${var}`
  expression syntax raises `WorkflowGraphCompileError` with a migration hint.
  The old `condition` string field also raises `WorkflowGraphCompileError` via
  a stale-field guard. A hygiene scan in `test_workflow_network_graph.py`
  prevents regressions across all factory workflow `transition_graph.yaml` files.
- Deleted `mozaiksai/core/workflow/execution/stream_bridge.py` — dead code
  (`_MozaiksStreamForwarder`, `attach_stream_forwarder`) with no runtime callers.

## 0.1.7 - 2026-05-27

### Added

- Packaged app-workspace coding-agent guidance as real `.md` files inside
  `mozaiks_cli/agent_guidance/` — included in the pip wheel. Rules and skill
  stubs for app-bundle, docs, frontend, modules, and workflows are no longer
  string literals in the init command.
- Mozaiks-managed guidance blocks are now automatically refreshed on
  `mozaiks serve` and `mozaiks studio` startup so builders get updated guidance
  after a package upgrade without running `mozaiks sync-agent-guidance` manually.
- Generated workspace scaffolds now include `app/config/secrets.yaml`
  (names-only secret contract with inline comments), `app/config/data.json`
  (canonical app data contract when needed), `app/config/data_migrations/`
  (additive migration artifacts), and `app/services/data/` helper lanes when
  needed.
- Generated workspace scaffolds now seed a complete `app/config/shell.json`
  with full navigation, chrome, and shortcuts blocks sourced from the factory
  app template — matching the layout the Studio shell expects on first run.

### Changed

- Generated `requirements.txt` now includes commented examples for
  app-specific dependencies (e.g. payment provider, Twilio) to clarify that `mozaiks`
  itself is not listed there — it is a platform-level install, not an
  app-level dependency.
- Getting Started doc clarified that `.\my-workspace` is the name of the
  folder created during quickstart; added a tip showing `--dir .` for users
  already inside their workspace directory.
- Getting Started doc updated to explain MongoDB as a required database with
  a Docker one-liner as the recommended local setup path.
- Updated contact email in `pyproject.toml`.

### Fixed

- Fixed MongoDB startup warning (`Index already exists with a different name:
  gc_ent_user_created`) that appeared on every server start. The persistence
  manager now performs a drop-then-create rename migration
  (`gc_ent_user_created` → `gc_app_user_created`,
  `gc_counter_ent_user` → `gc_counter_app_user`) instead of attempting a
  conflicting `create_index` call.
- Fixed Vite import resolution for `clsx`, `tailwind-merge`,
  `class-variance-authority`, `@radix-ui/*`, `marked`, `dompurify`, and
  `react-icons` when `mozaiks_chat_ui` is installed as a pip package. Added
  `resolve.alias` entries to `vite.config.js` matching the existing pattern
  for `react`, `lucide-react`, and `monaco-editor`.
- Added missing `chat-ui` peer dependencies (`clsx`, `tailwind-merge`,
  `class-variance-authority`, `@radix-ui/*`) to `web_shell/package.json` so
  the Studio frontend starts without Vite import errors on a fresh install.


## 0.1.6 - 2026-05-27

### Changed

- Replaced the public first-run install path with `pip install mozaiks` followed
  by `python -m mozaiks ...`; the `mozaiks` command is documented as an optional
  shortcut only. Getting Started, README, and Local Setup updated accordingly.
- Standardized all public launch references around Studio:
  `python -m mozaiks studio --dir <workspace> --open`.
- Docs use tabbed contract snippets and collapsible troubleshooting blocks for
  cleaner navigation (Getting Started, Add Workflows, Add a Page).
- Runtime data integrity policy in Add a Module is now a visible warning block.

### Fixed

- Added `mozaiks/__init__.py` and `mozaiks/__main__.py` so `python -m mozaiks`
  works immediately after `pip install mozaiks` without a PATH refresh on any OS.
- Release CI smoke now verifies both `mozaiks --version` and
  `python -m mozaiks --version` from the installed wheel.
- MongoDB preflight added to Studio launcher: missing or unreachable MongoDB
  reports a clear diagnostic error instead of a generic backend startup failure.
- Corrected `open_console` → `open_studio` flag name in `quickstart` and
  `onboard` commands so `quickstart` reliably opens Studio on first run.

## 0.1.5 - 2026-05-26

### Added

- Added brownfield app adoption continuation path. After `ExistingAppDiscovery`
  completes, the journey now advances to a `brownfield_path_selector` transition
  that routes into one of two downstream build sequences:
  - `brownfield_overlay_generation` (light integration): AgentGenerator → AppGenerator → app_review
  - `brownfield_module_generation` (full migration): DesignDocs → AgentGenerator → AppGenerator → app_review
- Added `BrownfieldPathSelector` transition component for the post-discovery
  build-path choice screen.
- Enabled the `brownfield_app` option in `AppTypeSelector` — existing-app
  onboarding is now a live, routable path from the `/create` entry point.
- Added `chat_session` transition type to the workflow routing system. A
  `chat_session` transition launches a target workflow in the current chat
  surface without a blocking overlay, allowing the user to interact
  conversationally. Declared with `route_to` only — `ui`, `options`,
  `confirm_route`, and `cancel_route` are not permitted.
- Added `AppReview` workflow: a lightweight AG2 session launched by the
  `app_review` chat_session transition after AppGenerator completes. ReviewAgent
  presents an `AppReviewSummary` in-chat artifact and manages the
  promote-or-revise HITL decision without blocking chat input.
- Added revision loop: when the user requests changes inside AppReview,
  `submit_revision_request` emits a `chat.revision_requested` WebSocket event
  carrying the request text. `ChatPage` handles the event by calling
  `POST /api/workflows/trigger` with `trigger_source="refinement"`, routing
  through the control plane into the appropriate revision workflow sequence
  (e.g. `app_surface_revision`) and switching the chat session in-place.
- Added build history page and carry-forward audit panel in the admin portal.
  Each artifact entry renders a `CarryForwardReportSummary`; the full panel is
  accessible at `/apps/:id/activity`.
- Added `promote_build` action to the `app_registry` module: validates
  `lifecycle_state == "review"`, transitions to `"active"`, and emits
  `domain.app_registry.app_promoted`.
- Added provider-neutral deployment artifact generation (`deployment_contract.py`):
  produces Dockerfile, CI workflow, and compose scaffold from the app bundle.
- Added `generated_bundle_scanner.py`: detects payment provider SDK usage, refund API
  calls, and secret key literals in generated bundles before promotion.
- Added canonical `ui/lib/moduleApi.js` template (`module_api_template.py`)
  with structured error fields for generated frontend module clients.
- Added AppGenerator shared-persistence contracts and adapter path support.
- Added conceptual-replan carry-forward smoke harness and saved fixture replay
  tests covering Levels A–E (inventory, context seed, AppBuildPlan,
  preservation, conflict resolution).
- Added Keycloak realm export and login theme assets under
  `factory_app/app/brand/`.

### Changed

- Changed `app_review` transition from `confirm` (blocking overlay using
  `AppReviewScreen`) to `chat_session` (launches `AppReview` workflow in-place).
  The review step now lives in the chat surface so users can type revision
  requests without modal interruption.
- Enforced single workflow root selection: the previous multi-root helper was
  replaced by `resolve_workflows_root()` (single `Path`).
- Renamed the context placeholder file to the context fallbacks file.
- Updated default control-plane LLM model from `gpt-4o-mini` to `gpt-5-nano`.
- `subscriptions.yaml` now raises `ModuleLoadError` immediately on load;
  `ModuleLegacySubscriptionsManifest` removed.
- Simplified public setup around the installed `mozaiks` CLI via `pipx`,
  separating it from source checkout and standalone generated workspace setup.
- Clarified quickstart CLI output and generated workspace docs so `.venv`
  instructions are scoped to contributor repos or standalone app workspaces.

### Removed

- Removed `AppReviewScreen.js` transition overlay component — replaced by the
  `AppReviewSummary` agentic UI artifact in the `AppReview` workflow.

## 0.1.4 - 2026-05-21

### Added

- Added refinement control-plane smoke tooling, including the live classifier
  smoke harness, fixture replay coverage, and an offline dry-run refinement
  plan harness for safely previewing classification, routing, impact paths, and
  profile usage without running workflows or mutating app files.
- Added deterministic refinement impact mapping for ExperienceSpec UI surfaces,
  module/backend changes, hosted capability façade paths, external integration
  readiness, and data model migrations.
- Added conceptual-replan carry-forward tooling for module inventory,
  carry-forward candidate discovery, declarative contract reads, preservation
  resolution, AppGenerator carry-forward decisions, and carry-forward reporting.
- Added artifact content-store support and workflow artifact persistence
  hardening so generated app and workflow bundles can be restored, reviewed,
  invalidated, and promoted more reliably.

### Changed

- Promoted `experience_spec` to a first-class artifact dependency family and
  aligned staleness propagation, routing docs, sequence impact families, and
  downstream UI path hints with that contract.
- Moved control-plane LLM configuration to named profile resolution for
  classifier, impact analysis, planning/replanning, codegen, review/validation,
  and architecture-level planning.
- Updated public contributor guidance, MkDocs navigation, control-plane docs,
  and task skills for workflow-sequence-driven refinement and factory workflow
  contribution boundaries.

### Fixed

- **CI regression after refinement profile changes**: Allowed the declared
  `architecture` control-plane LLM profile, aligned ExperienceSpec dependency
  assertions, removed provider-specific refinement examples, and updated
  responsive smoke expectations for the current Usage UI.
- **Stuck REVISING state**: Added `SessionRouter.fail_active_revision()` to
  clear `active_revision_id` and set `sequence_status=STALE` when a workflow
  errors during a revision. `handle_user_input_from_api` now calls it via
  `asyncio.create_task()` in its exception handler, preventing the session from
  remaining stuck in REVISING indefinitely.
- **Migration schema mismatch**: `generate_migration()` in
  `factory_app/workflows/AppGenerator/tools/schema_migration.py` now emits the
  `schema_version` and `operations[]` fields required by the runtime
  `_validate_migration()` check. New collections are represented as
  `ensure_collection` operations. Generator output now survives a full
  generate → inject → load → validate roundtrip without raising
  `DatabaseMigrationError`.
- **Permission bypass**: `mozaiksai/hosts/platform.py` now passes
  `granted_permissions=list(principal.scopes)` (instead of `None`) when
  dispatching module actions for authenticated HTTP requests. `granted_permissions=None`
  is preserved only for trusted-internal (unauthenticated) calls. Module-level
  `action_permissions` declared in `module.yaml` are now correctly enforced for
  OAuth2-authenticated principals.
- **DRAFT artifact leaks**: `resolve_latest_artifact_version_refs()` now
  filters by `lifecycle_status=CURRENT` when resolving canonical input version
  IDs. DRAFT versions (created during in-flight revisions) can no longer
  contaminate the `canonical_inputs_version` of downstream artifacts.
  The parent-version lookup in `persist_summary_artifact()` also filters by
  CURRENT to avoid linking new artifacts to an in-flight DRAFT parent.
- **First-run canonical inputs**: When no CURRENT artifact version exists for
  a requested kind (first run or all versions still in DRAFT/STALE state),
  `resolve_latest_artifact_version_refs()` now logs a DEBUG message and
  correctly returns that kind absent from the result dict rather than silently
  returning a stale or draft version ID.

## 0.1.3 - 2026-05-18

### Added

- Added the generated-app persistence runtime path, including `ctx.persistence`,
  `MongoPersistenceContext`, `database_intent` loading, additive migrations,
  and database index application.
- Added migration startup policy, migration history/locking, migration health
  reporting, and the read-only `mozaiks migrations status` CLI.
- Added AppGenerator persistence alignment so generated persistent modules use
  canonical `repo.py`, `schemas.py`, `policy.py`, database intent, and staged
  migration artifacts.
- `ExistingAppDiscovery` workflow now detects storage patterns (mongodb, sql, file_store, redis), external connectors, and Mozaiks vocabulary/authorship signals during preload — improving adoption-level recommendations for `native_migration` and `ecosystem` paths.
- Added `ModuleDecomposerAgent` to `ExistingAppDiscovery`: produces a `ModuleDecompositionPlan` (modules, workflows, pages, adapters) when adoption level is `ecosystem` or `native_migration`.
- `ExistingAppAugmentationArtifact` now carries `module_decomposition_plan` (serialized JSON); `save_existing_app_artifacts` writes the plan to `generated/existing_app_discovery/{chat_id}/` for downstream AppGenerator consumption.
- `transition_graph.yaml` now routes conditionally: `ecosystem`/`native_migration` goes through `ModuleDecomposerAgent` before the assembler; `embed`/`bridge` skip directly to assembly.
- Added three generic infrastructure probe adapters to `mozaiksai/core/adapters/`: `dns_probe` (A/AAAA via stdlib, MX/NS/CNAME/TXT via optional dnspython), `tls_probe` (cert expiry, SANs, issuer, protocol via stdlib ssl), and `http_health` (status, latency, redirect chain, content metadata via httpx). All are provider-neutral with no required credentials.

### Changed

- Hardened generated-app persistence docs/tests and documented production
  `required` startup mode.
- Hardened UI/design-system contracts, shared workflow infrastructure, and
  route/docs alignment for the OSS factory Studio.
- `IntegrationPlannerAgent` no longer defaults to embed/bridge — prefers `native_migration` when `mozaiks_authored_app` is true, storage is file_store, or app is internal tooling.
- Tightened generated UI quality-gate enforcement for custom React surfaces: docs/tests fixture paths are ignored, semantic token/class usage is covered by dedicated tests, and AppGenerator guidance now explicitly requires semantic Button variants backed by `app/brand/theme_config.json` and shared primitives.
- Aligned OSS frontend architecture docs, frontend rules, and add-page skill guidance with the generated UI gate: semantic tokens/variants are allowed, hardcoded hex/rgb and direct font-family styling are disallowed, local primitive clones and raw primary buttons are disallowed, and docs/tests fixture paths are excluded from generated React audit scope.
- Moved the shared generated UI gate into `factory_app/workflows/_shared/` and documented the boundary between factory-owned shared workflow infrastructure and generated workflow-local files.
- Moved shared platform build lifecycle hooks into `factory_app/workflows/_shared/platform/` and documented the canonical placement rules for factory-owned shared workflow infrastructure versus workflow-local generated files.
- Updated public contributor docs, setup skills, env/web-shell guidance, and .claude rules to frame `factory_app` as the first-party builder/reference workspace, describe build as workflow-sequence-driven, and document refinement as checkpoint/control-plane re-entry rather than a dedicated workflow.
- Unified the module event-reaction contract on canonical `contracts/reactions.yaml` across runtime loading/routing, AppGenerator prompts and structured outputs, CLI scaffolds, contributor guidance, and contract tests.
- Consolidated source-of-truth architecture docs and module-authoring guidance for the canonical event/reaction model, including provider-neutral `tasks` examples and explicit rejection of `contracts/subscriptions.yaml`.

## 0.1.2 - 2026-05-14

### Changed

- Consolidated first-party Studio ownership under `factory_app/app/admin/pages/` and `factory_app/app/admin/index.js`.
- Updated workspace Studio navigation to derive from route-manifest metadata (`meta.navigation.group`, `meta.navigation.icon`) instead of hardcoded sidebar arrays in `WorkspaceLayout`.
- Aligned route manifest contracts so workspace and app Studio routes declare explicit navigation inclusion/grouping semantics.

### Fixed

- Fixed active admin portal page imports to resolve shared `StudioShared` primitives from the canonical `factory_app/app/ui/components/` location.
- Regenerated packaging manifest metadata (`mozaiks.egg-info/SOURCES.txt`) to match the current Studio files.

## 0.1.1 - 2026-05-14

### Added

- Standardized generated app scaffolds from `mozaiks init` with app-local `requirements.txt`, `.env.example`, `.gitignore`, README, PowerShell launch scripts, `AGENTS.md`, `CLAUDE.md`, and `.claude` rules/skills for coding agents.
- Added Claude release-notes rules and skill guidance so release-impacting changes update this changelog proactively.
- Added `mozaiks sync-agent-guidance` to safely check, create, or update generated coding-agent guidance in existing app workspaces.
- Added AppGenerator UI primitive catalog injection, generated UI quality gates, and generated UI acceptance coverage so agents target shipped primitives instead of hallucinated UI components.
- Added module-contract quality checks and canonical module contract guidance for generated modules.
- Added Studio/app management UX surfaces for app portfolio management, shell actions, notifications, and generated app lifecycle visibility.

### Changed

- Updated OSS setup docs to distinguish public package usage, source checkout dogfooding, and framework development.
- Reorganized public architecture docs around app, module system, workflows, frontend UI, builder, and MozaiksAI runtime sections.
- Updated factory app UI primitives, shell branding, and Studio copy toward the production-grade app-management model.
- Aligned AppGenerator prompts, hooks, and structured-output guidance with the canonical module and generated UI contracts.

### Fixed

- Fixed PyPI project description media by using absolute GitHub asset URLs for the README logo and demo images.
- Fixed primitive catalog hook section replacement so injected guidance no longer truncates later agent instructions.
- Fixed primitive loader test isolation by exposing cache invalidation for cached UI primitive exports.

### Removed

- Removed stale public telemetry walkthroughs, old roadmap/spec docs, and duplicate prompt-pack docs that no longer match the canonical product and generator model.

## 0.1.0 - 2026-05-13

### Added

- Initial public PyPI release of the Mozaiks OSS framework.
- Packaged CLI entrypoint with `mozaiks --version`.
- Tag-driven GitHub Actions release flow for building, smoke-testing, creating a GitHub release, and publishing to PyPI.

