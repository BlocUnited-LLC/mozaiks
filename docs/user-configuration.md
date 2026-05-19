# User Configuration

Most users need only two settings:

- one LLM provider key
- one MongoDB connection string

## Minimum Setup

PowerShell:

```powershell
$env:OPENAI_API_KEY="sk-..."
$env:MONGO_URI="mongodb://localhost:27017/mozaiks"
mozaiks quickstart --dir .\mozaiks-workspace
```

macOS or Linux:

```bash
export OPENAI_API_KEY="sk-..."
export MONGO_URI="mongodb://localhost:27017/mozaiks"
mozaiks quickstart --dir ./mozaiks-workspace
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

## Contributing

Want to contribute? See the [Contributing guide](https://docs.mozaiks.ai/contributing/).
