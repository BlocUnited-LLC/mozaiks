# Integrations Workflow

Generated apps and workflows request external integration setup through the
platform connector flow. They must not embed credentials in generated code,
create app-owned secret tables, or expose secret values to frontend read APIs.

## Storage

Connector metadata is app-scoped platform state. It records service id,
provider id, integration id, display name, status, public non-secret config,
source workflow, and whether a vault secret is available. Secret values are
stored only through the configured connector vault backend. If no vault is
configured, metadata can still be saved, but the raw secret is not durable.

## Inline Request

When a workflow reaches a real credential/configuration boundary, it emits an
`integration.required` request:

```yaml
integration_id: email_provider
provider: email_provider
purpose: Send transactional notifications.
required_fields:
  - name: api_key
    label: API Key
    type: secret
    required: true
    frontend_safe: false
  - name: sender_domain
    label: Sender Domain
    type: text
    required: true
    frontend_safe: true
permissions_required:
  - integrations.manage
resume:
  workflow_name: AppGenerator
  run_id: run_123
  step_id: task_notifications
  tool_call_id: connector_bundle_email_provider
```

The UI may render the request inline or link to
`/apps/{appId}/integrations`. Secret fields are write-only. Non-secret fields
marked `frontend_safe: true` may be persisted as connector metadata.

## Workflow Behavior

```text
agent/tool discovers need
  -> record_integration_need / AppBuildPlan.external_integrations
  -> IntegrationReadinessAgent checks app-scoped connector inventory
  -> existing ready connector: continue
  -> missing required setup: emit integration.required inline UI request
  -> user/operator submits write-only secrets + frontend-safe config
  -> connector service stores secret via vault and metadata/public_config via connector store
  -> readiness recheck passes or returns blocked with unresolved service ids
```

1. The agent or tool records an integration need.
2. The readiness checkpoint checks existing app-scoped connectors.
3. Missing required setup is requested inline.
4. The backend saves secrets through the connector service and saves only
   sanitized metadata.
5. The workflow resumes from the correlated UI tool response.
6. If required setup is declined or unavailable, the checkpoint returns
   `blocked` with unresolved service ids.

Generated app code references connector ids/capabilities and reads integration
state through server-side adapters. It never receives raw secrets.

The readiness checkpoint is the task-result merge boundary before validation/download. It
does not require manual preflight setup; it waits until the build has a concrete
missing integration need, asks inline, then rechecks the connector inventory.
Resume is handled by the same correlated `chat.tool_call` response path used by
other workflow UI tools.

## Connector Health

Readiness and health are related but distinct:

- readiness answers: can this workflow/build proceed now?
- health answers: what should an operator know about this connector's current
  configuration state?

Connector health is frontend-safe and never includes secret values:

```yaml
health:
  status: unknown | not_configured | configured | healthy | unhealthy
  last_checked_at: timestamp | null
  message: string | null
  missing_fields: []
  checked_by: readiness | manual | provider_check
  frontend_safe: true
```

Default health is configuration-only and does not call external providers. It
marks a connector `not_configured` when required secret fields have no stored
secret or required frontend-safe fields are missing from `public_config`. It
marks a connector `configured` when all required fields are present and no
provider validation has run. Optional fields never block `configured` status.

`healthy` and `unhealthy` are reserved for future provider-specific checks.
Those checks must be explicitly declared by a connector/provider adapter and
must not run by default.

## Provider Health Plugins

Provider-specific health checks are optional server-side plugins. They are for
operator visibility, not default build readiness. The OSS runtime owns the
generic plugin contract and registry; real provider checks should live in a
hosted/product package or an external package that registers providers at host
startup.

Plugins implement this shape:

```python
class ConnectorHealthProvider:
    provider_id: str
    supported_integration_ids: list[str]

    async def check(
        self,
        *,
        connector,
        secret_reader,
        public_config,
        context,
    ) -> ConnectorHealthResult:
        ...
```

`ConnectorHealthResult` is sanitized before persistence:

```yaml
status: healthy | unhealthy | unknown
message: string | null
checked_at: timestamp | null
safe_details: {}
error_code: string | null
```

Checks run only by explicit server-side request, such as an operator clicking
"Check now" on `/apps/{appId}/integrations`. The frontend calls the Studio
health-check endpoint and never calls provider APIs directly. Provider checks
must not run from `list_connectors`, page load, connector save, or the default
IntegrationReadinessAgent checkpoint.

Secret access is server-only. A plugin receives a `ConnectorSecretReader`, not a
secret value in its constructor or public inputs. The reader can resolve the
vault secret for the current app/service, but the secret handle is not
serialized, logged, returned to the frontend, or stored in connector metadata.

Only safe health fields may be persisted:

- `health_status`
- `health_message`
- `last_checked_at`
- `checked_by`
- `health_details`
- `health_error_code`

`health_details` is recursively redacted before storage and response rendering.
Raw provider responses must not be stored.

Readiness remains configuration-completeness based. An `unhealthy` provider
health result may warn operators, but it does not block AppGenerator readiness
or download by default. A future explicit policy such as
`health_policy: required_for_deployment` may add deployment gating, but that
policy is intentionally not part of the current contract.

## Route Ownership

Integrations are app-scoped in the canonical first version. The registered
Studio route is:

```text
/apps/{appId}/integrations
```

That route owns connector inventory, credential setup, readiness, and health for
one app. Connector readiness is evaluated against an app build/workflow context,
so a bare workspace `/integrations` route is intentionally not a first-class
route unless a future workspace-level integration product is explicitly
designed and registered in `route_manifest.json`.

`admin/admin_registry.yaml` may include app-level navigation metadata for the
integrations page, but it is not a custom route registry. Full-page React route
ownership must remain explicit in `ui/route_manifest.json` and `ui/index.js`
component registration.

## Operator UI

The `/apps/{appId}/integrations` surface renders connector health for operators
without revealing secrets. Each connector should show its display name,
readiness, health status, missing required fields, last checked timestamp when
present, safe public configuration, and credential presence as a boolean state.

When a connector health payload includes `health_check_supported: true`, the UI
may show a manual "Check now" button. Clicking it calls:

```http
POST /api/studio/integrations/connectors/{service}/health-check?app_id={appId}
```

The button must not appear for connectors without a registered health plugin,
and the page must not trigger the endpoint automatically. A successful response
updates only the displayed safe health fields: status, message,
`last_checked_at`, `checked_by`, and redacted `safe_details`. Failures should
show a safe operator-facing error and leave existing connector config intact.

Secret fields are write-only. The UI may show a secret field name and whether it
is configured or missing, but it must never render the stored value. Public
configuration values are shown only from sanitized `public_config`, and
secret-shaped keys such as API keys, tokens, passwords, and secrets must be
filtered defensively even if malformed connector metadata reaches the frontend.

## AppGenerator

`AppBuildPlan.external_integrations`, capability-pack `required_integrations`,
build-task `integration_needs`, and task-agent `record_integration_need` calls
feed the same readiness checkpoint. Agents should declare provider-neutral
service ids, setup fields, purpose, required lifecycle point, and whether the
need is optional. Capability-pack `required_integrations` must be structured
objects with `required_fields`; they should not be reduced to string service
names when a pack declares public config and secret fields.
