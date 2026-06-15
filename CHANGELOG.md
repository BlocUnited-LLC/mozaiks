# Changelog

All notable changes to Mozaiks are tracked here.

This project follows a practical pre-1.0 changelog format:

- `Added` for new capabilities
- `Changed` for behavior, docs, packaging, or workflow changes
- `Fixed` for bug fixes
- `Removed` for removed behavior
- `Security` for vulnerability or hardening work

## Unreleased

### Added

- **Live AppGenerator subscription smoke** (`scripts/smoke_appgenerator_live_subscription.py`)
  now exercises real ConfigMiddlewareAgent LLM calls for SaaS subscription config
  and entitlement-gated module contract generation, then validates wiring,
  acceptance gates, export readiness, strict structured-output conformance, and
  runtime module loading.

- **Live AgentGenerator pack smoke** (`scripts/smoke_agentgenerator_live_pack.py`)
  now emits machine-readable JSON on stdout while still exercising real AG2 task
  batch generation, workflow export, semantic drift checks, and runtime loader
  promotion.

- **Structured hosted-pack connector requirements** for AppGenerator capability
  packs. `capability_packs[].required_integrations` now uses a typed connector
  object with explicit public and secret fields, and hosted pack defaults flow
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
  `workflow/execution/resume.py` (run resume/checkpoint), `workflow/outputs/`
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

- **`test_mozaikspay_hosted_pack_contract.py`** — new OSS test file that the
  production readiness gate requires. Covers context.yaml and contract.yaml
  contract shapes, all `required_outputs` having matching template files, the
  `forbidden_outputs` drift guard, `mozaikspay_client.py` provider-neutrality
  (no `import stripe`, no raw secrets, env-var–only URL resolution), the
  `billing_portal` facade module being app-owned (`owner: app`), page schemas
  routing through the facade rather than hosted modules directly, and a
  pack-wide drift guard (41 tests).

- **`test_wallet_module.py`** (mozaiks-app) — comprehensive wallet service
  tests covering balance calculation, payout request guards (no Stripe account,
  amount exceeds available, zero amount, default-to-full-available), credit
  reactions (`credit_app_earnings`, `credit_investment_return`), Stripe webhook
  processing idempotency (`payout.paid`, `payout.failed`, already-terminal,
  transaction-not-found, unhandled event), hosted wallet provisioning validation,
  `get_hosted_wallet_provisioning_status` with secret stripping, and repo
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

- `docs/architecture/app/control-plane-pack.md` — canonical reference for
  app-local control plane packs. Covers when to use a pack, the full file
  layout, annotated starter templates for all four config files
  (`runtime.yaml`, `control_plane.yaml`, `tools.yaml`, `policies.yaml`), prompt
  starters for all four LLM-backed checkpoints, and route rules. This is the
  authoritative AppGenerator reference for `control_plane_pack` build tasks.

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

- `docs/architecture/workflows/control-plane-harness-architecture.md` updated:
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
  current factory runtime defaults, and `control_plane_pack` outputs now stage
  `control_plane/config/runtime.yaml` alongside `control_plane.yaml` and
  `tools.yaml`. This keeps `ask` / `chat` / `workflows` startup in `ai.json`
  while moving app-local control-plane runtime policy under
  `control_plane/config/`.
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
  `factory_app/control_plane/config/runtime.yaml` — previously set to
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

### Removed

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
  app-specific dependencies (e.g. Stripe, Twilio) to clarify that `mozaiks`
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
- Added `generated_bundle_scanner.py`: detects Stripe SDK usage, refund API
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

