# Integrations And Targets

`app/config/integrations.yaml` and `app/config/targets.json` describe what the
app needs from the outside world. They are provider-neutral declarative files.

Use them to carry intent, not provider mechanics.

## `app/config/integrations.yaml`

This file declares required and optional external services for the app. It is
safe to package with generated app bundles because it carries service ids,
setup lanes, required field names, lifecycle points, and `required_by`
references without raw secret values.

Use it for:

- required service ids such as OpenAI, email, storage, payment facade, or search
- whether setup is managed, connected-account, bring-your-own-key, or
  app-specific
- frontend-safe setup metadata
- references to the modules, workflows, pages, or build tasks that need the
  service

Do not put API keys, OAuth tokens, webhook secrets, provider tenant ids,
checkout URLs, SDK client state, or hosted-product policy in this file.

## `app/config/targets.json`

This file declares deployment and runtime target intent.

Use it for:

- runtime shape
- health path
- deployment profile
- allowed deployment lanes
- expected environment variable names
- domain and DNS intent

Do not use it to run deployments, write cloud resources, own DNS state, store
secrets, or embed provider-specific adapters. Direct provider mechanics belong
behind app-owned `app/services/adapters/` only when the app itself owns that
provider integration. Hosted platform mechanics belong in the hosted product
that operates the app.

## Example Shape

```yaml
# app/config/integrations.yaml
schema_version: mozaiks.integrations.v1
requirements:
  - service_id: openai
    purpose: AI workflow execution
    required: true
    setup_lane: bring_your_own_key
    required_fields:
      - OPENAI_API_KEY
    required_by:
      - kind: workflow
        id: SupportIntake
```

```json
{
  "schema_version": "mozaiks.targets.v1",
  "runtime": {
    "kind": "web",
    "health_path": "/health"
  },
  "deployment": {
    "profile": "docker",
    "allowed_lanes": ["local", "self_hosted"]
  },
  "environment": {
    "required": ["OPENAI_API_KEY"]
  },
  "domain": {
    "intent": "custom_domain_optional"
  }
}
```

## Related Files

- Put secret names and env handles in `app/security/secrets.yaml`.
- Put provider-specific clients in `app/services/integrations/` or
  `app/services/adapters/` only when the app owns the integration.
- Put deployment packaging in root files such as `Dockerfile`,
  `docker-compose.yml`, `.env.example`, and `deployment.manifest.json` when
  those artifacts are requested.

See also [Integrations](../integrations/01-overview.md).
