# Persistence and Artifact Storage

Mozaiks is a framework with a durable build pipeline, not a stateless prompt
wrapper. Persistent storage is therefore a first-class runtime contract.

## Core Rule

- Durable persistence is required for Studio and the
  workflow-owned build sequence,
  `factory_app`, multi-stage build workflows, refinement, and revision history.
- In-memory execution is only acceptable for smoke tests, demos, or simple
  non-builder workflows that do not need upstream artifacts.
- MongoDB is the canonical persistence backend today.

## Ownership Layers

Mozaiks persistence is divided into three scopes.

### 1. Runtime State

Framework-owned operational data used by the runtime itself:

- `ChatSessions`
- `AG2StreamEvents`
- `AG2StreamHeads`
- `GeneralChatSessions`
- `GeneralChatCounters`
- `RuntimeUsageEvents`

This data supports session continuity, workflow execution state, AG2 stream
bootstrap/replay, and reconnectable UI state.

Current implemented workflow-run persistence contract:

- `ChatSessions` is run metadata and UI-state projection, not canonical execution history.
- AG2 run history is persisted separately through the AG2 stream storage adapters and is the source of truth for execution re-entry and UI replay.
- While the backend process still owns a paused AG2 workflow channel, user
  replies continue that live AG2 channel first. Persisted AG2 events are the
  canonical replay and restart fallback when the live Hub/channel handle is no
  longer available.
- AG2 agent-turn, LLM-call, tool-call, and HITL telemetry is emitted through AG2
  beta `TelemetryMiddleware` as OpenTelemetry spans.
- LLM token usage is emitted by Mozaiks AG2 1.0 beta usage middleware as
  `chat.usage_delta` events and stored in `RuntimeUsageEvents`. This ledger is
  measurement-only. It does not enforce entitlements, quotas, pricing, or
  hosted billing.
- Reconnectable workflow UI state lives under `ChatSessions.workflow_ui_state` with:
  - `schema_version`
  - `last_artifact`
  - `pending_input_request`
  - `tool_calls`
- On startup, the runtime backfills pre-migration top-level workflow UI fields such as `last_artifact` and `pending_input_request` into `workflow_ui_state` and removes the old top-level fields. Runtime readers should not depend on those pre-migration top-level fields.
- The current source of truth for this runtime contract is `mozaiksai/core/data/persistence/persistence_manager.py`, `mozaiksai/core/workflow/execution/run_bootstrap.py`, `mozaiksai/core/transport/run_replay.py`, `mozaiksai/hosts/runtime.py`, `mozaiksai/hosts/platform.py`, and the focused tests `tests/test_persistence_initial_messages.py`, `tests/test_orchestration_seed_persistence.py`, `tests/test_run_replay.py`, `tests/test_runtime_websocket_contract.py`, and `tests/test_platform_chat_meta_contract.py`.

Runtime usage surfaces:

- OSS app creators query `/api/admin/usage` in Studio/Admin for app-scoped or
  workspace-scoped token totals.
- Hosted products such as Mozaiks App use the same `/api/admin/usage` route and
  may also forward summary events to their Refinement Engine.
- Generated app end users query `/api/me/usage` from the profile surface. The
  response combines measured runtime usage with usage-limit metadata declared
  in `app/config/subscriptions.yaml` when the app is a SaaS app.
- MozaiksPay and other billing providers consume these measurements through
  app-owned facade modules. Runtime usage events are not the billing authority.

### 2. Builder Artifacts

Framework-owned pipeline artifacts produced and consumed by `factory_app`:

- `BuilderConcepts`
- `BuilderBuildPlans`
- `DesignDocuments`
- `ThemeCaptures`
- `DataContracts`
- `DatabaseMigrations`
- `WorkflowExports`
- `LLMConfig`

These collections hold the durable handoff between workflow stages such as
`ValueEngine`, `DesignDocs`, `AgentGenerator`, and `AppGenerator`.

### 2b. Platform Connector Metadata

Platform-owned, app-scoped connector metadata used by the visible
Integrations/Admin surfaces and workflow integration helpers:

- `AppConnectors`

This collection stores sanitized connector state only. Raw API keys, OAuth
client secrets, refresh tokens, and other secrets do not belong in MongoDB
builder artifacts.

### 2c. Connector Secret Vault

Durable connector secrets are a separate framework-owned backend:

- default contract: `mozaiksai.core.secrets.connector_vault`
- default provider mode: `MOZAIKS_CONNECTOR_SECRET_BACKEND=auto`

Backends selected by `auto`:

| Condition | Backend |
| --- | --- |
| `AZURE_KEY_VAULT_NAME` is set | `AzureKeyVaultConnectorVaultBackend` |
| No Azure vault configured | `MongoConnectorVaultBackend` (default) |

`MongoConnectorVaultBackend` stores Fernet-encrypted secrets in the `ConnectorSecrets`
collection in the same MongoDB instance. Encryption key priority:

1. `MOZAIKS_CONNECTOR_SECRET_KEY` — explicit 32-byte URL-safe base64 or hex key
2. Derived via HMAC-SHA256 from `SECRET_KEY`
3. Dev-only deterministic fallback with a loud warning (not for production)

To generate an explicit key:
```
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Rules:

- MongoDB (`Connectors` collection) stores connector metadata, status, timestamps,
  and ownership only — never raw secrets.
- `ConnectorSecrets` collection stores encrypted secret values managed by
  `MongoConnectorVaultBackend`. These are framework-internal records, not app data.
- Azure Key Vault remains the recommended backend for production deployments that
  already operate Key Vault infrastructure.
- A connector is `active` when `secret_available: true` (secret stored in vault).
  It is `metadata_only` only when the save itself failed — not simply because no
  external vault is configured.

### 3. App Business Data

App-owned product data managed by generated or hosted modules:

- projects
- tasks
- audit_logs
- notifications
- other module-owned collections

This data is not builder metadata. It belongs to app module boundaries and must
be declared by the app's backend contracts.

## Canonical Namespace

Framework-owned persistence uses a single system database:

- database: `mozaiksai`

Active code should not introduce hardcoded framework database names outside the
canonical `mozaiksai` namespace. Older local database names are not part of the
clean OSS contract.

## Builder Artifact Flow

The build pipeline depends on durable artifact handoff:

1. `ValueEngine` writes concept and planning artifacts.
2. `DesignDocs` reads those artifacts and writes design contracts.
3. `AgentGenerator` and `AppGenerator` read the design artifacts.
4. Refinement and revision flows read prior versions and migration history.

This is why persistence is required for the real builder experience.

## Staged Filesystem Output

Generated app bundles are staged on disk under:

`generated/apps/{app_id}/{build_id}/app`

That staged bundle is separate from Mongo persistence:

- Mongo stores build/runtime metadata and durable workflow handoff artifacts.
- the filesystem stores the generated app bundle itself.

Promotion into a runnable workspace is an explicit later step.

## Data Contracts and Revisions

Database evolution is a first-class generated artifact, not an implicit side
effect of handler code.

- `DesignDocs` owns the typed `data_contract`
- `AppGenerator` stages `data/contract.json`
- refinement runs may stage `data/migrations/{migration_id}.json`
- generated module repos use `backend/schemas.py` for typed document shapes and
  `backend/repo.py` for persistence operations
- the runtime injects `ctx.persistence` into module actions when `app_id` exists;
  generated repo code uses `ctx.persistence.collection(module_id, entity_name)`
  and must not require `ctx.db`
- the runtime loads `data/contract.json` during app load; missing
  intent is allowed for non-persistent apps, while invalid JSON or invalid shape
  fails app loading
- the runtime applies declared indexes idempotently and applies only additive
  migration files from `data/migrations/*.json`
- migration states are recorded in `mozaiksai.AppDatabaseMigrations`

The target contract is:

- additive changes can be applied deterministically
- destructive changes require explicit review
- migration history must stay linked to app artifact versions

Supported generated app migration operations today are:

- `ensure_collection`
- `ensure_index`

The runtime does not execute arbitrary migration code, drop collections, delete
fields, rename fields, or rewrite documents as part of generated app migrations.

Generated-app database startup policy is controlled by
`MOZAIKS_DATABASE_STARTUP_POLICY`:

- `best_effort` is the default. Index and migration failures are logged and
  startup continues.
- `required` is recommended for production persistent generated apps. Index and
  migration failures fail startup.

App business data database names are resolved from an injected adapter value,
then `MOZAIKS_APP_DATABASE_NAME`, then `MOZAIKS_APPS_DATABASE`, then
`mozaiks_apps`.

Migration history records use `in_progress`, `applied`, and `failed`. The
`mozaiksai.AppDatabaseMigrations` collection also acts as the migration lock:
the runtime atomically claims a migration by inserting an `in_progress` record
for `(app_id, migration_id)` before operations run. The collection has a unique
index on `(app_id, migration_id)` so concurrent startup instances cannot both
claim the same migration.

Failed records include error type/message and failed operation details. Existing
`in_progress` or `failed` records block automatic retry until an operator clears
or repairs the history record. `in_progress` means another instance is applying
the migration or a previous instance crashed after claiming it. The first-pass
runtime does not take over expired locks; operators must inspect the history
record and repair or clear it deliberately. Production persistent apps should
run with `MOZAIKS_DATABASE_STARTUP_POLICY=required` so migration lock conflicts
fail startup instead of being treated as healthy.

Migration health is inspectable through the read-only runtime helper
`get_migration_health_report()`. The report returns summary counts, migration
items, `has_blockers`, and `has_unknown_statuses`. `failed` and `in_progress`
records are operational blockers; `applied` records are healthy; unknown
statuses are surfaced for operator review. The helper does not repair, clear,
retry, or mutate migration records. Operators should inspect this report when
startup logs or required-mode startup failures mention migration application or
claim failures.

The CLI exposes the same read-only report:

```powershell
mozaiks migrations status --app-id app_123
mozaiks migrations status --status failed --json
```

Options:

- `--app-id`: filter to one app.
- `--status`: filter to one migration status.
- `--limit`: maximum rows, default `100`.
- `--database-name`: migration history database override for diagnostics.
- `--json`: print the exact report as JSON.

Exit codes:

- `0`: no blockers and no unknown statuses.
- `1`: failed/in-progress blockers or unknown statuses exist.
- `2`: configuration, Mongo connection, or report loading error.

The command does not print Mongo connection strings or credentials. It does not
repair, clear, retry, mutate migration records, or take over locks.

### Real Mongo Smoke

Normal CI does not require MongoDB for generated-app persistence. The real
Mongo-backed smoke is opt-in and validates the production adapter path:

```powershell
$env:MONGO_URI="mongodb://localhost:27017"
$env:MOZAIKS_RUN_REAL_MONGO_TESTS="1"
python -m pytest tests/test_runtime_persistence_real_mongo.py
```

The smoke creates a dedicated test app database named
`mozaiks_persistence_test_{random}` by default and drops it during cleanup. To
use an explicit test database name, set `MOZAIKS_TEST_APP_DATABASE_NAME` to a
dedicated database whose name contains `test`. Do not use production
credentials or production database names for this smoke.

Generated module layering for app business data:

- `handler.py` dispatches only
- `service.py` orchestrates business logic and calls repo methods
- `repo.py` uses `ctx.persistence.collection(module_id, entity_name)`
- `policy.py` builds scope/domain filters
- `schemas.py` defines typed shapes and pure helpers

Generated modules must not call `get_mongo_client()` directly, use `ctx.db`, or
hardcode database names. Do not generate `backend/models.py`,
`backend/database/schema.json`, or `backend/database/seed.json`.

## Implementation Rules

- Framework-owned builder artifact persistence should flow through
  `BuilderArtifactStore`, not raw collection access in workflow tools.
- App-scoped connector metadata should flow through a connector service/store,
  not raw collection access in workflow tools.
- Durable connector secrets should flow through the connector vault backend, not
  MongoDB collections or generated module code.
- Do centralize framework DB and collection names in shared runtime constants.
- Do route workflow tools through artifact-aware persistence helpers where
  possible.
- Do keep runtime state, builder artifacts, and app business data logically
  separate.
- Do keep connector metadata separate from app business collections and builder
  artifact collections.
- Do fail fast when the Studio host or the builder is launched without durable
  persistence configured.
- Do not teach workflows or docs that removed database names are canonical.
- Do not treat persistence as optional for the builder journey.

## Related Contracts

- [Workflow Architecture](../../workflows/workflow-architecture.md)
- [Workflow Authoring Contracts](../../workflows/workflow-authoring-contracts.md)
- [App Bundle Declaratives](../../app/app-bundle-declaratives.md)
- [Event System](event-system.md)
- [Event Contracts](event-contracts.md)
- [Data Contract and Revision Contract](../../builder/data-contract-and-revision-contract.md)



