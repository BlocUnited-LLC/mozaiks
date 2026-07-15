# Generated App Deployment Contract

## Purpose

This document defines the provider-neutral deployment artifact contract for generated apps.

For the separate repo-local OSS `infra/` boundary and the first-party
`factory_app/` deployment story, see
`docs/architecture/deployment/oss-infra-and-generated-app-deployment.md`.

The OSS `mozaiks` repo owns this contract.
Hosted-product policy, approvals, provider adapters, and secret orchestration remain outside this repo.

The contract answers "how does this generated app run?" It does not answer
"where should the hosted product operate it?" or "which provider adapter should
mutate infrastructure?" Hosted products convert the contract into their own
records, approvals, provider calls, and status surfaces.

## Production Boundary

Generated apps use a repo-per-app (workspace-per-app) boundary.

```text
one generated app == one repository/workspace
```

This keeps deployment ownership, environments, CI history, and secret boundaries isolated per app.

## Contract Models

The contract is represented by two typed models in AppGenerator structured outputs.

1. `DeployTargetSpec`
2. `DeploymentTemplateManifest`
3. `DeploymentBuildOutput`

### DeployTargetSpec

Provider-neutral target definition.

Required shape:

- `target_id`
- `target_kind`: `container | compose | external_adapter`
- `runtime`
  - `container_port`
  - `health_path`
  - `start_command` (optional)
- `artifact_outputs`
  - `Dockerfile` (optional)
  - `docker-compose.yml` (optional)
  - `.github/workflows/deploy.yml` (optional)
  - `env.example`
  - `deployment.manifest.json`
- `environment`
  - `required_variables`
  - `optional_variables`
  - `secret_variables`
  - `public_variables`
- `image`
  - `image_name`
  - `tag_strategy`
- `checks`
  - `build`
  - `smoke`
  - `health`
- `provider_profile`
  - generic metadata only in OSS
- `readiness_requirements`
  - provider-neutral checks that name required runtime env and evidence stamps

Optional shape:

- `ci_secret_requirements`
  - `required`
  - `optional`
  - `workflow_inputs`

### DeploymentTemplateManifest

Deterministic output manifest for generated deployment artifacts.

Required shape:

- `schema_version`
- `app_id`
- `deployment_profile`
- `generated_files`
- `required_env`
- `secret_env`
- `exposed_ports`
- `healthcheck`
- `ci_workflow`
- `ci_secret_requirements` (optional)
- `readiness_requirements`
- `dockerfile`
- `compose`
- `validation_status`
- `deploy_target_spec`
- `build_output_contract` (optional)

### Readiness Requirements

Generated deployment manifests carry a names-only readiness section under
`readiness_requirements`. This is the OSS first-class contract for production
evidence without coupling generated apps to a hosted product, cloud provider, or
payment provider.

Suggested shape:

- `checks`
  - `id`
  - `category`
  - `label`
  - `implemented_score`
  - `required_env`
  - `required_evidence`
  - `canonical_paths`
  - `notes` (optional)

Default generated checks are provider-neutral:

- `runtime_environment`
  - requires `OPENAI_API_KEY` and `MONGO_URI`
- `container_smoke`
  - requires evidence stamp `APP_IMAGE_SMOKE_VERIFIED_AT`
- `healthcheck`
  - requires evidence stamp `APP_HEALTHCHECK_VERIFIED_AT`

Rules:

1. Readiness requirements carry names only, never values.
2. `required_env` and `required_evidence` use uppercase env-style names.
3. Evidence stamps should contain an ISO timestamp, run id, ticket URL, or
   change record only after the named check has passed.
4. Readiness checks are provider-neutral; they must not mention Azure, AWS,
   payment provider, MozaiksPay, Cloudflare, registrar adapters, or hosted-product policy.
5. Hosted products may add product-specific checks outside generated app
   bundles, then evaluate both layers with the OSS
   `mozaiksai.core.runtime.readiness` primitive.

### CI Workflow Secret Requirements

Generated deployment workflows may declare a names-only CI contract under
`ci_secret_requirements`.

Suggested shape:

- `required`
  - `name`
  - `purpose` (optional)
  - `used_by` (optional)
- `optional`
  - `name`
  - `purpose` (optional)
  - `used_by` (optional)
- `workflow_inputs`
  - `name`
  - `required`
  - `purpose` (optional)

Rules:

1. Entries carry names only, never values.
2. Secret names must use safe identifiers.
3. Workflow input names must use safe identifiers.
4. Generated workflow files may reference those names, for example
   `${{ secrets.NAME }}` or `${{ inputs.name }}`, but must never embed secret
   values.
5. Hosts and adapters provision real secret values outside generated artifacts.
   The generated app bundle only describes expected names.

### Build Output Contract

`DeploymentBuildOutput` is the provider-neutral handoff contract from
repo/export/build systems to deployment providers/hosts.

It carries build/export output metadata and does not execute builds.

Suggested shape:

- `repo_url` (optional)
- `commit_sha` (optional)
- `build_status`: `pending | running | succeeded | failed`
- `image_ref` (required when status is `succeeded`)
- `artifact_digest` (optional, `sha256:` prefix)
- `workflow_run_url` (optional)
- `logs_url` (optional)
- `safe_details` (optional, non-secret metadata only)
- `error_code` (optional)

Provider-neutral examples:

- `repo_url`: `https://repo.example.invalid/demo-app`
- `image_ref`: `registry.example.invalid/demo-app:abc123`
- `artifact_digest`: `sha256:0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef`

Validation rules:

1. `build_status` must be one of pending/running/succeeded/failed.
2. `image_ref` is required when `build_status` is succeeded.
3. `artifact_digest`, if present, uses `sha256:` prefix.
4. URL fields are URL-shaped when present.
5. Raw secrets and secret-shaped keys are not allowed in `safe_details`.
6. No provider-specific required fields.

## Artifact Contract

When deployment artifacts are requested by scaffold/export flags, AppGenerator emits deterministic outputs from the provider-neutral contract:

- `Dockerfile` (optional)
- `docker-compose.yml` (optional)
- `.github/workflows/deploy.yml` (optional)
- `env.example`
- `deployment.manifest.json`

These artifacts live at the generated app bundle root. They are not emitted
from `app/services/`, and AppGenerator build tasks must not claim them as
`service_foundation`, `api_surface`, or helper-file outputs.

When `.github/workflows/deploy.yml` is emitted, the manifest may also carry a
concrete `ci_secret_requirements` section describing the names-only secret and
workflow-input contract used by that generated workflow.

## Env and Secrets Rules

1. `env.example` may include variable names and placeholders only.
2. Secret variables are declared by name only and must not contain real values.
3. `ci_secret_requirements` is names-only and must not contain real values.
4. Generated workflow files may reference secret names but never secret values.
3. Generated artifacts must not include:
   - cloud tenant ids
   - provider credentials
   - registry passwords
   - GitHub tokens
   - hosted-product policy secrets
5. Real secrets are injected by deployment adapters (CI secret stores / cloud secret stores), not committed in app repos.

## AppGenerator and Adapter Split

OSS AppGenerator emits and validates:

- deployment contract schema
- deterministic artifact rendering
- artifact validation rules
- provider-neutral build output handoff contract
- names-only CI workflow secret requirements contract
- provider-neutral readiness requirements contract

Adapter layers outside AppGenerator handle:

- provider-specific deployment execution
- hosted policy/approval gates
- hosted lifecycle state transitions
- secret delivery and rotation
- persistence of build outputs in host deployment records
- mapping names-only CI secret requirements to provider-specific secret stores
- async deployment status polling (see below)

### Async Provider Deployment Status

Some deployment providers (for example Azure Container Apps) return
`status="deploying"` with no `hosted_url` on the initial `deploy()` call.
The hosted product must poll the provider until the deployment completes or fails.

The canonical `DeploymentProviderAdapter` protocol for hosted products exposes two
lifecycle methods:

```
deploy(request) → DeploymentProviderResult
  status: "hosted" | "deploying" | "failed"
  hosted_url: str | None        # present only when status == "hosted"
  provider_deployment_id: str | None

get_status(provider_deployment_id, ...) → DeploymentProviderResult
  (same shape as deploy())
```

Rules:

1. When `deploy()` returns `status="deploying"`, do **not** mark the app as hosted.
   Persist the `provider_deployment_id` and let the polling loop call `get_status()`.
2. When `get_status()` returns `status="hosted"`, mark the app hosted and record the URL.
3. When `get_status()` returns `status="failed"`, record the failure and stop polling.
4. The polling loop is owned by the hosted product module (a startup service helper),
   not by the adapter. Adapters are stateless per-call; the hosted module owns lifecycle state.
5. Adapters that deploy synchronously may return `status="hosted"` directly from `deploy()`.
   The hosted module must handle both synchronous and asynchronous adapters uniformly.

For Mozaiks-hosted apps, the generated bundle may include `Dockerfile`,
`env.example`, `.github/workflows/deploy.yml`, and
`deployment.manifest.json`, but it must not include hosted product provider adapters
such as DNS, registrar, cloud deployment, wallet, billing, or hosted
policy implementations. The hosted product owns those adapters and consumes the
generated contract through platform records/APIs.

## E2B Role

E2B in AppGenerator is pre-deploy validation/preview only.

It is not a production runtime or hosting target.

## Self-Host Mode

This contract supports self-hosting through the same generic artifact outputs:

- local Docker
- local Compose
- generic container platform deployment

Self-host users consume generated artifacts and provide their own infrastructure and secret stores.
They can evaluate the manifest readiness section with
`mozaiksai.core.runtime.readiness.evaluate_readiness_requirements`.

## Hosted Product Handoff

Hosted products (for example `mozaiks-app`) consume this contract but own hosted-specific behavior:

- Host With Us UX
- policy/approval gates
- provider adapters and defaults
- deployment status and operations surfaces

App Zero dogfooding follows the same handoff as any tenant app: the
`mozaiks-app` bundle can be registered and operated through hosted product
records, but infrastructure changes still flow through the product modules and
provider adapters rather than direct adapter imports from the app bundle.
