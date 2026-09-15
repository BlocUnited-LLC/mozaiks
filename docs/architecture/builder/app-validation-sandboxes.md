# App Validation Sandboxes

How generated apps are built, validated, and previewed before deploy — the
four strategies, every environment variable, and hosted activation. The
ownership boundary against AG2's agent-level execution is defined in
[ag2-ownership-boundary.md](../workflows/ag2-ownership-boundary.md)
(Sandbox Execution Boundary).

## Strategies

Resolution precedence: tool argument → `app_validation_strategy` context
variable → `MOZAIKS_APP_VALIDATION_STRATEGY` env → automatic (`docker` when a
daemon is reachable → `local` when npm exists → `skip`). E2B is never selected
automatically: Docker is the default when available. E2B is selected only when
the workflow or operator explicitly sets `MOZAIKS_PREVIEW_PROVIDER=e2b` and
provides `E2B_API_KEY`.

| Strategy | Runs where | Preview URL | Cost | Intended for |
|----------|-----------|-------------|------|--------------|
| `e2b` | Hosted e2b cloud sandbox | yes | per sandbox-minute (COGS) | Hosted product — browser-only users |
| `docker` | Local Docker container | yes (published preview ports, random host binding) | free | OSS self-hosters / local dev |
| `local` | Current machine (npm) | no | free | Quick local checks without Docker |
| `skip` | — | no | — | CI/deterministic tests; integration checks still gate export |

All sandbox strategies route through the `SandboxPort` seam
(`mozaiksai/core/ports/sandbox.py`, Tier 1 stable) and its adapters.
Sandboxes are **ephemeral workspaces, never truth stores** — outcomes
persist into build records; the sandbox itself is disposable.

## Operating Rule

The Factory uses two intentionally separate sandbox paths. AG2
`SandboxShellTool` and `SandboxCodeTool` execute commands or code for an agent's
bounded assignment. Mozaiks `SandboxPort` starts and validates the complete
generated application. An agent's successful command is not application
acceptance, and application preview is not an agent shell. The authoritative
decision and configuration matrix is [ADR 0010](../../adr/0010-agent-and-app-sandbox-execution-boundary.md).

## Live preview sessions (AppWorkbench)

Beyond one-shot validation, the Studio host mounts an artifact preview session
API so the AppWorkbench can boot and restart a saved generated bundle on demand.
A refinement selects a new artifact version and clears the old preview; the
user starts the new version explicitly.

- Manager: `mozaiksai/core/sandbox/preview_sessions.py`
  (`ArtifactPreviewSessionManager` over `SandboxPort`; one session per
  host/user/artifact version, with an absolute `SANDBOX_TTL_MINUTES` deadline).
- Routes (Studio host, `mozaiksai/hosts/routers/sandbox.py`):
  `POST /api/artifacts/{artifactId}/sandbox?build_registry_id=...` (create/reuse),
  `POST /api/sandbox/{id}/sync`, `POST /api/sandbox/{id}/start`,
  `GET /api/sandbox/{id}/status`, `POST /api/sandbox/{id}/stop`,
  `WS /ws/sandbox/{id}` (status stream). Every operation checks the authenticated
  host and owner, including the WebSocket before acceptance. Creation resolves
  the registry's target and verifies the saved app-bundle binding and archive
  digest before allocating a sandbox. Binary assets are retained.
- Provider resolution mirrors the validation ladder's preview-capable rungs:
  e2b only when `MOZAIKS_PREVIEW_PROVIDER=e2b` and its key are configured,
  otherwise local Docker. With neither, the create call returns 503 with a
  clear message (`local`/`skip` builds have no live preview).

The canonical supervisor runs the existing platform host, shared web shell,
and a private MongoDB in the sandbox. App files mount under `app/`, workflows
remain beside `app/`, and deployment support files stay at workspace root.
The preview URL is returned only after backend, frontend, and the frontend's
shell-config proxy pass health and target-identity checks. Status checks clear
the URL after failure or expiry. A health check is not functional acceptance:
exercise the generated screens, actions, permissions, and persistence too.

Preview pins both `MOZAIKS_APP_DATABASE_NAME` and
`MOZAIKS_APP_DATA_DATABASE_NAME` to `mozaiks_preview` inside its private Mongo.
Generated module `ctx.persistence` reads the first setting; account export and
deletion resolve their database through `app_data_from_context(None, contract={})`,
which prefers the second. Different values silently send account handlers to an
empty database instead of the app's records. This is preview-only composition;
normal host database precedence and explicitly bound app-data contracts remain
unchanged. The standard module executor does not supply `app_slug`, so account
handlers using the same module/entity IDs with `collection_name_for` also use
its default slug. Custom naming inputs are not inferred from app display names.

The preview regression writes through the executor's real scoped persistence
context, then calls the account routes through a registered canonical handler
against an in-memory Mongo substitute. It checks export, owned deletion, repeat
deletion, and preservation of other users/apps. Direct helper tests with a
preselected database do not cover this environment-resolution boundary. Existing
preview images must be rebuilt to pick up supervisor changes; live functional
acceptance must recheck account routes against stored app data.

File synchronization rejects destination aliases before provider writes. Docker
extracts files as its configured sandbox user so later replacement and deletion
work without root privileges. A partial or cancelled sync invalidates the session;
recreate it rather than launching a partially updated app. Cleanup retains state
unless the provider confirms termination or that the sandbox is already absent.

### Local Docker setup

Build the preview image from the same checkout as the Factory host:

```bash
docker build -f infra/docker/Dockerfile.preview -t mozaiks-sandbox:local .
```

The image includes the installed OSS runtime, frontend dependencies, and Mongo.
Generated `requirements.txt` installs against the image's dependency constraints.
Containers use an unprivileged user, dropped capabilities, resource limits, and
random loopback-only frontend/backend ports. No Docker socket, host workspace,
or Factory database is mounted into the app.

### Hosted E2B template setup

The hosted E2B template must be derived from the same canonical preview image,
not a second hand-maintained environment. Run the repository helper in dry-run
mode to inspect the build:

```bash
python scripts/build_e2b_preview_template.py
```

After the operator has approved the hosted-provider cost, submit the explicit
build:

```bash
python scripts/build_e2b_preview_template.py --name mozaiks-preview --confirm-paid-build
```

The helper consumes `infra/docker/Dockerfile.preview`, requires
`E2B_API_KEY`, and prints only template/build identifiers. The resulting name
or ID belongs in `E2B_TEMPLATE` or `SANDBOX_TEMPLATE`; credentials remain in
the operator environment. The helper does not run automatically during app
generation or CI.

Only explicitly configured `MOZAIKS_PREVIEW_ENV_<NAME>` values become preview
environment variables. Factory API keys, credentials, and database URLs are not
inherited. Public apps may declare `authRequired: false`; authenticated apps
cannot silently disable authentication. Configure their existing OIDC provider
and register each target app client with the published preview callback origin.
For local JWT validation, a typical explicit configuration is:

```dotenv
MOZAIKS_PREVIEW_ENV_AUTH_PROVIDER=jwt
MOZAIKS_PREVIEW_ENV_AUTH_ISSUER=http://localhost:8080/realms/local-preview
MOZAIKS_PREVIEW_ENV_AUTH_JWKS_URL=http://host.docker.internal:8080/realms/local-preview/protocol/openid-connect/certs
MOZAIKS_PREVIEW_ENV_VITE_OIDC_AUTHORITY=http://localhost:8080/realms/local-preview
```

These are example addresses, not provisioned services. Audience and frontend
client ID are the generated app ID; callback paths follow its auth contract.
Additional claims and app-owned AI credentials must be explicitly configured
for the chosen provider and app. Test data survives a runtime restart within
one session, but is discarded when that sandbox stops or expires.

For a provider whose JWT carries permissions in the space-delimited `scope`
claim, set `MOZAIKS_PREVIEW_ENV_AUTH_SCOPES_CLAIM=scope`; the generic JWT
adapter otherwise defaults to `scp`. Configure the role and app-identity claim
names to match the actual token too. Register only the target app's declared
permissions on its test client. A successful sign-in does not prove module
authorization: test an ordinary app user, a second user, and anonymous requests.

Keep live Factory acceptance and automated regression tests on separate MongoDB
instances, not merely different URI database suffixes: runtime system collections
use their canonical database name. Supply `MONGO_URI` explicitly and disable
implicit dotenv loading for tests. Do not point a broad test suite at a development
database containing builds or user records.

## What persists

- The validation result (status, strategy, errors, trimmed build output,
  `sandbox_session_id`, `sandbox_provider`, `preview_url`) lands in workflow
  context and in the build record's `commit_metadata.metadata`.
- `BuildRecord` carries first-class queryable fields:
  `app_validation_status`, `app_validation_strategy`, `sandbox_session_id`,
  `sandbox_provider`.
- Provider sandboxes are created with identity metadata
  (`purpose`, `app_id`/`chat_id` or `artifact_id`) and a provider-side kill
  deadline, so orphans are attributable and self-terminating.

## Environment variables

| Variable | Default | Used by |
|----------|---------|---------|
| `MOZAIKS_APP_VALIDATION_STRATEGY` | auto | strategy resolution (`e2b`/`docker`/`local`/`skip`); `e2b` must be explicit |
| `E2B_API_KEY` | unset | makes the e2b strategy available; never selects it automatically |
| `E2B_TEMPLATE` | provider default | e2b adapter template |
| `E2B_TIMEOUT` | `300` (seconds) | e2b adapter session/default validation timeout |
| `SANDBOX_PREVIEW_PORT` | `3000` | additional provider port; canonical app previews use frontend `3000` and backend `8000` |
| `DOCKER_SANDBOX_IMAGE` | `mozaiks-sandbox:local` | locally built Docker adapter image |
| `DOCKER_SANDBOX_TIMEOUT` | `300` | docker container lifetime (seconds) |
| `SANDBOX_TTL_MINUTES` | `30` | artifact preview-session TTL (also the e2b kill deadline) |
| `SANDBOX_TEMPLATE` | provider default | artifact preview-session e2b template |
| `MOZAIKS_PREVIEW_PROVIDER` | auto | `docker` (default) or explicit `e2b`; a key alone never selects E2B |
| `SANDBOX_WORKDIR` | `/home/user/app` | e2b workspace root; Docker uses `/workspace` |
| `MOZAIKS_PREVIEW_ENV_<NAME>` | unset | explicit preview-only environment, never implicit host inheritance |
| `APP_VALIDATION_BUILD_OUTPUT_MAX_CHARS` | `20000` | persisted build-output trim |

## Hosted provider boundary

Paid hosted sandboxes are not a pre-launch prerequisite and are not provisioned
by this setup. An operator choosing e2b must supply a compatible template with
the canonical runtime, frontend, private database, and dependency constraints;
setting an API key alone is insufficient. Hosted isolation, authenticated
ingress, per-user concurrency quotas, and billing admission need verification
before allowing external users. A local Docker preview is not a hardened
multi-tenant execution service.

## Non-goals

- Sandboxes are not a hosting runtime. Deployment goes through the
  provider-neutral deployment artifacts and the hosting pipeline (see
  [generated-app-deployment-contract.md](../deployment/generated-app-deployment-contract.md),
  E2B Role).
- Agent-level code/shell execution is AG2's job (`SandboxShellTool`,
  `sandbox_shell: true` in agents.yaml), not `SandboxPort`'s.
