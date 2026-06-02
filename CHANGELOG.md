# Changelog

All notable changes to Mozaiks are tracked here.

This project follows a practical pre-1.0 changelog format:

- `Added` for new capabilities
- `Changed` for behavior, docs, packaging, or workflow changes
- `Fixed` for bug fixes
- `Removed` for removed behavior
- `Security` for vulnerability or hardening work

## Unreleased

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
- Removed stale AG2 groupchat guidance block (including a private local machine path)
  from `AGENTS.md`, `ARCHITECTURE.md`, and `CLAUDE.md` — groupchat removal is
  complete and the private path was never appropriate in OSS docs.
- Deleted `tests/test_realtime_token_logger.py` and `tests/test_mozaiks_event_handler.py`
  alongside their subject modules.

### Added

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
- Fixed live control-plane classifier calls against models that only support
  the provider default temperature. `SimpleLLMCapabilityService` now omits the
  `temperature` field for JSON completions when no explicit value is configured.

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
- `handoffs.yaml` now routes conditionally: `ecosystem`/`native_migration` goes through `ModuleDecomposerAgent` before the assembler; `embed`/`bridge` skip directly to assembly.
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
