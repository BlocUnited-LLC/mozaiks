# Persistence and Artifact Storage

Mozaiks is a framework with a durable build pipeline, not a stateless prompt
wrapper. Persistent storage is therefore a first-class runtime contract.

## Core Rule

- Durable persistence is required for the workspace console and the
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
- `WorkflowStats`
- `GeneralChatSessions`
- `GeneralChatCounters`

This data supports session continuity, workflow execution state, and runtime
telemetry.

### 2. Builder Artifacts

Framework-owned pipeline artifacts produced and consumed by `factory_app`:

- `BuilderConcepts`
- `BuilderBuildPlans`
- `DesignDocuments`
- `ThemeCaptures`
- `DatabaseIntents`
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
- first real provider: Azure Key Vault when `AZURE_KEY_VAULT_NAME` is set
- required package extras for Azure: `mozaiks[azure]`

Rules:

- MongoDB stores connector metadata, status, timestamps, and ownership only.
- Raw API keys and refresh tokens are stored in the connector vault backend or
  remain ephemeral for the current session.
- the workspace console, Build, Integrations, and Admin surfaces may manage
  connector metadata even when no vault is configured.
- when a vault backend is configured, the visible Integrations surface may
  create, rotate, and delete durable connector secrets while keeping MongoDB
  limited to sanitized metadata.
- A connector can therefore be `metadata_only` in local/dev runtimes and
  `active` in vault-backed runtimes.

### 3. App Business Data

App-owned product data managed by generated or hosted modules:

- campaigns
- investors
- payouts
- communications
- other module-owned collections

This data is not builder metadata. It belongs to app module boundaries and must
be declared by the app's backend contracts.

## Canonical Namespace

Framework-owned persistence uses a single system database:

- database: `mozaiksai`

Active code should not introduce new hardcoded framework namespaces such as:

- `autogen_ai_agents`
- `MozaiksAI`
- `mozaiks`

Those names reflect historical evolution and are not part of the clean OSS
contract.

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

## Database Intent and Revisions

Database evolution is a first-class generated artifact, not an implicit side
effect of handler code.

- `DesignDocs` owns the typed `database_intent_bundle`
- `AppGenerator` stages `config/database_intent.json`
- refinement runs may stage `config/database_migrations/{migration_id}.json`

The target contract is:

- additive changes can be applied deterministically
- destructive changes require explicit review
- migration history must stay linked to app artifact versions

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
- Do not teach workflows or docs that legacy database names are canonical.
- Do not treat persistence as optional for the builder journey.

## Related Contracts

- [Workflow Architecture](workflow-architecture.md)
- [Workflow Authoring Contracts](workflow-authoring-contracts.md)
- [App Bundle Declaratives](app-bundle-declaratives.md)
- [Event System](event-system.md)
- [Event Contracts](event-contracts.md)
- `factory_app/docs/database-intent-and-revision-contract.md`
