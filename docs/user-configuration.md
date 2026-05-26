# User Configuration

This is a compact reference page, not part of the main first-run path. Start
with [Getting Started](getting-started.md) if you are setting up Mozaiks for
the first time.

## Minimum Setup

PowerShell:

```powershell
$env:OPENAI_API_KEY="sk-..."
$env:MONGO_URI="mongodb://localhost:27017/mozaiks"
```

macOS or Linux:

```bash
export OPENAI_API_KEY="sk-..."
export MONGO_URI="mongodb://localhost:27017/mozaiks"
```

Use `ANTHROPIC_API_KEY` instead of `OPENAI_API_KEY` if that is your provider.

## Optional Connector Secrets

Only configure durable connector secret storage when apps need reusable
third-party credentials such as email, analytics, CRM, storage, payment, webhook,
or model-provider API keys.

```env
MOZAIKS_CONNECTOR_SECRET_BACKEND=auto
AZURE_KEY_VAULT_NAME=your-vault-name
MOZAIKS_CONNECTOR_SECRET_PREFIX=mozaiks-connector
```

If no vault is configured, connector metadata can still exist, but raw secrets
are not stored durably.

## Optional Deployment Settings

Deployment and shared-hosting environments may also need:

- `FRONTEND_URL`
- `AUTH_ENABLED`
- auth provider settings
- production MongoDB settings
- vault settings for connector secrets

These are operator settings. They are not required for the first local Console
run.
