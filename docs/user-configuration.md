# User Configuration

This page answers one question:

**What does a Mozaiks user actually need to configure?**

For most people, the answer is much smaller than the full `.env.example`.

## Minimum Local Builder Setup

If you want to use Studio and build apps through `factory_app`, the minimum
required configuration is:

- one LLM provider key
- one MongoDB connection string

Examples:

```powershell
$env:OPENAI_API_KEY="sk-..."
$env:MONGO_URI="mongodb://localhost:27017"
```

or:

```powershell
$env:ANTHROPIC_API_KEY="sk-ant-..."
$env:MONGO_URI="mongodb://localhost:27017"
```

That is enough for:

- Studio
- `ValueEngine`
- `DesignDocs`
- `AgentGenerator`
- `AppGenerator`
- durable builder artifacts and workflow handoff

## App-Level Control-Plane Configuration

`app/config/ai.json` can also enable the builder-session harness that Mozaiks
uses for refinement classification and future coding-agent refinement.

Example:

```json
{
  "control_plane": {
    "enabled": true,
    "classifier": {
      "enabled": true,
      "llm_config": {
        "model": "gpt-4o-mini",
        "temperature": 0.0
      }
    },
    "coding": {
      "enabled": false,
      "llm_config": {
        "model": "gpt-5.2-codex",
        "temperature": 0.1
      }
    }
  }
}
```

Meaning:

- `enabled`
  - turns the control-plane harness on or off for that app surface
- `classifier`
  - controls the authoritative LLM used for build-affecting request analysis
- `coding`
  - reserves model settings for the refinement worker loop when coding-agent
    refinement is enabled later

Important:

- this config does not replace workflow-local AG2 `llm_config`
- it configures the builder/control-plane loop above workflows
- `llm_config` should describe model/runtime settings, not secrets; secrets
  still belong in environment variables or connector/secret storage

After that, the normal path is:

```bash
mozaiks quickstart --dir ./my-first-mozaiks-app
```

## What Mozaiks Manages Automatically

Users should **not** need to manually create or manage framework collections.

Mozaiks manages:

- runtime persistence collections
- builder artifact collections
- connector metadata collections
- generated app staging locations

Users do **not** need to know or configure collection names like:

- `BuilderConcepts`
- `BuilderBuildPlans`
- `DesignDocuments`
- `AppConnectors`

Those are framework-owned contracts.

## Optional: Durable Connector Secret Storage

By default, Mozaiks can run without a vault backend. In that case:

- connector metadata is stored
- raw secrets are not stored durably
- app connectors may remain `metadata_only`

If you want durable reusable connector secrets, configure the connector vault
backend.

### Minimum Connector Vault Settings

```env
MOZAIKS_CONNECTOR_SECRET_BACKEND=auto
AZURE_KEY_VAULT_NAME=your-vault-name
MOZAIKS_CONNECTOR_SECRET_PREFIX=mozaiks-connector
```

If you need explicit Azure credentials:

```env
AZURE_TENANT_ID=...
AZURE_CLIENT_ID=...
AZURE_CLIENT_SECRET=...
```

Behavior:

- `MOZAIKS_CONNECTOR_SECRET_BACKEND=auto`
  - use Azure Key Vault when `AZURE_KEY_VAULT_NAME` is set
  - otherwise disable durable connector secret storage
- no vault configured
- connector records can still exist as metadata
- vault configured
  - Studio adapters can create, rotate, and delete durable connector secrets

## Two Ways Users Can Provide Connector Access

There are two valid paths:

### 1. Manual Platform Configuration

The user or operator opens the Studio adapters/admin surface and adds the
connector there first.

Use this when:

- you already know which integrations the app will need
- you want to prepare Stripe, OpenAI, SendGrid, Twilio, or similar services ahead of time
- you want the workflow run to reuse existing app-scoped connectors

### 2. Workflow-Time Collection

The `factory_app` workflow can ask for missing API keys/connectors during the
run through the shipped connector UI tools.

Use this when:

- the required integrations are only discovered during planning
- the app/workflow brief evolves during the session
- you want the workflow to ask only for what is still missing

The intended system behavior is:

- active app connectors should be reused automatically
- metadata-only or expired connectors should be treated as not runtime-ready
- missing integrations should be called out explicitly as configuration
  dependencies, not silently assumed to exist

## Optional: Manual Workspace/Host Overrides

Most users do not need these because `quickstart` handles them.

These settings are only for advanced/manual runtime launches:

- `PLATFORM_PATH`
- `MOZAIKS_APP_WORKSPACE_PATH`
- `MOZAIKS_HOST`

Use them when:

- you are launching the runtime manually
- you are targeting a workspace outside the repo
- you are debugging host behavior directly

## Optional: Hosted / Deployment Configuration

If you are deploying Mozaiks or running it in a shared environment, you may
also need:

- `FRONTEND_URL`
- `AUTH_ENABLED`
- auth provider settings
- production MongoDB settings
- optional vault settings

That is operator/deployment configuration, not the normal first-time builder
setup.

## Copy/Paste Scenarios

### Local Builder, No Vault

```powershell
$env:OPENAI_API_KEY="sk-..."
$env:MONGO_URI="mongodb://localhost:27017"
mozaiks quickstart --dir ./my-first-mozaiks-app
```

### Local Builder, With Azure Key Vault For Connectors

```powershell
$env:OPENAI_API_KEY="sk-..."
$env:MONGO_URI="mongodb://localhost:27017"
$env:MOZAIKS_CONNECTOR_SECRET_BACKEND="auto"
$env:AZURE_KEY_VAULT_NAME="your-vault-name"
$env:AZURE_TENANT_ID="..."
$env:AZURE_CLIENT_ID="..."
$env:AZURE_CLIENT_SECRET="..."
mozaiks quickstart --dir ./my-first-mozaiks-app
```

## Smoke-Test The Connector Setup

If you want to verify the connector system directly without opening Studio, use
the smoke script from the repo root.

### Metadata-Only Smoke

This verifies:

- MongoDB is reachable
- connector metadata can be created
- connector status is classified correctly
- cleanup works

```powershell
python .\scripts\run_connector_vault_smoke.py --mode metadata --service smoke_test --display-name "Smoke Test"
```

### Vault-Backed Secret Smoke

This verifies:

- the configured vault backend can store a connector secret
- the connector service can read it back
- cleanup removes the metadata and attempts secret deletion

First set a temporary smoke secret:

```powershell
$env:MOZAIKS_SMOKE_CONNECTOR_SECRET="test-secret-value-123"
```

Then run:

```powershell
python .\scripts\run_connector_vault_smoke.py --mode secret --service smoke_test --display-name "Smoke Test"
```

If the backend is disabled, that second command should fail cleanly and tell you
that durable secret storage is not configured.

## If You Are Unsure

Start with only:

- `OPENAI_API_KEY` or `ANTHROPIC_API_KEY`
- `MONGO_URI`

Then add vault/auth/deployment settings only when you actually need them.

## Related Docs

- [Getting Started](getting-started.md)
- [Install Modes](install-modes.md)
- [Persistence and Artifact Storage](architecture/foundations/persistence-and-artifact-storage.md)
- `./.env.example` in the repo root
