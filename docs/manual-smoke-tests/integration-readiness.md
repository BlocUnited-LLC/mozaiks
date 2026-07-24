# Integration Readiness Smoke

This smoke verifies the generic integration-readiness path without calling any
external provider.

## Run

```powershell
python scripts/smoke_integration_readiness.py
```

For machine-readable output:

```powershell
python scripts/smoke_integration_readiness.py --json
```

## Scenario

The script uses a neutral analytics connector:

- `integration_id`: `analytics_provider`
- `provider`: `managed_analytics`
- purpose: send usage events to an external analytics API
- secret field: `api_key`
- frontend-safe fields: `endpoint_url`, `workspace_id`

## Expected Output

The smoke should print `PASS` and include:

- `blocked_status: needs_configuration`
- `ready_status: ready`
- one `integration.required` request
- `secret_fields[0].name: api_key`
- `non_secret_fields`: `endpoint_url`, `workspace_id`
- connector `public_config` with only frontend-safe fields

The submitted secret must not appear anywhere in stdout.

## What It Validates

1. A required connector starts missing.
2. The readiness checkpoint blocks before prompting.
3. The workflow UI-tool request is shaped as `integration.required`.
4. A simulated UI response submits secret and non-secret setup.
5. The connector service stores the secret through the vault abstraction.
6. The connector store persists only safe `public_config`.
7. A readiness recheck returns `ready`.
8. Generated output references the connector id and contains no raw secret.

## Pre-Chat Workspace Setup

Integrations can be configured before an app-creation chat begins. That state is
workspace or app connector metadata plus vault-backed secret handles, not chat
history and not generated source code.

When AppGenerator starts planning, `check_workspace_integrations` reads the
catalog and overlays the workspace connector inventory when `workspace_id` is
available. A connector is auto-wired only when it is ready. If a host registers a
provider health check for that connector type, ready requires a passing health
check; pending or unhealthy connectors stay as missing configuration
dependencies. If no provider health check exists, OSS falls back to
configuration-only readiness and leaves stricter provider validation to the host
or operator.

This keeps app creation deterministic:

- preconfigured and verified connectors are reused without asking for secrets
- unverified or incomplete connectors are surfaced as setup dependencies
- unknown/custom services are recorded by name and collected inline only when a
  real build step reaches that boundary
- generated app bundles receive connector IDs and env names only, never raw
  credential values

## Troubleshooting

- If the smoke reports `blocked_before_prompt: false`, the required integration
  was not detected from the plan/task fixture.
- If `emitted_integration_required` is false, the UI-tool request path did not
  run.
- If `readiness_ready_after_submit` is false, connector inventory did not see
  the saved connector.
- If the submitted secret appears in output, treat it as a redaction regression
  and do not run a live external-provider smoke until fixed.

This script is intentionally local and deterministic. A separate live AG2 smoke
can layer on top once the full browser/runtime harness is needed.
