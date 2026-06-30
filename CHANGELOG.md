# Changelog

All notable changes to Mozaiks are tracked here.

This project follows a practical pre-1.0 changelog format:

- `Added` for new capabilities
- `Changed` for behavior, docs, packaging, or workflow changes
- `Fixed` for bug fixes
- `Removed` for removed behavior
- `Security` for vulnerability or hardening work

## Unreleased

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

- **Azure Key Vault connector error suppression** (`mozaiksai/core/secrets/connector_vault.py`):
  `store_secret`, `get_secret`, and `delete_secret` no longer return raw Azure
  SDK exception text (which can include subscription IDs and vault URLs).
  Generic messages returned to callers; exceptions logged with `exc_info=True`.

- **AG2 stream history bounded** (`mozaiksai/core/adapters/ag2_stream_storage.py`):
  `get_history` now applies `.limit(10_000)` to prevent unbounded MongoDB reads
  on event streams from long-running or abnormally large AG2 tasks.

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

- **`test_mozaikspay_managed_capability_contract.py`** — new OSS test file that the
  production readiness gate requires. Covers context.yaml and contract.yaml
  contract shapes, all `required_outputs` having matching template files, the
  `forbidden_outputs` drift guard, `mozaikspay_client.py` provider-neutrality
  (no `import stripe`, no raw secrets, env-var–only URL resolution), the
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
  tests covering balance calculation, payout request guards (no Stripe account,
  amount exceeds available, zero amount, default-to-full-available), credit
  reactions (`credit_app_earnings`, `credit_investment_return`), Stripe webhook
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

