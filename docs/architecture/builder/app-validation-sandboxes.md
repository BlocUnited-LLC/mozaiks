# App Validation Sandboxes

How generated apps are built, validated, and previewed before deploy — the
four strategies, every environment variable, and hosted activation. The
ownership boundary against AG2's agent-level execution is defined in
[ag2-ownership-boundary.md](../workflows/ag2-ownership-boundary.md)
(Sandbox Execution Boundary).

## Strategies

Resolution precedence: explicit `MOZAIKS_APP_VALIDATION_STRATEGY` environment
setting → tool argument → `app_validation_strategy` context variable → automatic (`docker` when a
daemon is reachable → `local` when npm exists → `skip`). E2B is never selected
automatically: Docker is the default when available. E2B is selected only when
the workflow or operator explicitly selects it. Build validation uses
`MOZAIKS_APP_VALIDATION_STRATEGY=e2b`; artifact preview uses the separate
`MOZAIKS_PREVIEW_PROVIDER=e2b`. Both require `E2B_API_KEY`.
An operator setting is authoritative: generated tool arguments cannot bypass it
by selecting `skip`, `local`, or another provider. Invalid operator values fail.
`AppValidationAgent` copies a supplied context strategy; otherwise its nullable
request defers to this resolver. It does not infer `skip` from a test-like brief.

| Strategy | Runs where | Preview URL | Cost | Intended for |
|----------|-----------|-------------|------|--------------|
| `e2b` | Hosted e2b cloud sandbox | separate Studio session | per sandbox-minute (COGS) | Hosted product — browser-only users |
| `docker` | Local Docker container | separate Studio session | free | OSS self-hosters / local dev |
| `local` | Current machine (npm) | no | free | Quick local checks without Docker |
| `skip` | — | no | — | Deterministic tests only; unverified build blocks export and promotion |

Readiness requires both deterministic acceptance and build execution to pass.
Skipped or pending required checks cannot certify readiness. When no bounded
recovery or repair can run next, the validation tool writes the protected
`app_validation_ends_run` flag and a failure message; the existing AG2 graph ends
the run once rather than asking for a reply that would repeat the same checks.
Messages distinguish incomplete validation from an unavailable environment and
do not assert that an unverified app is defect-free.

Build execution failures use that same approved-task repair policy after acceptance
passes. TypeScript, webpack and supported Vite diagnostics resolve only against the
actual staged app root and build working directory; ANSI formatting grants no path
authority. A repair requires one approved owner and accepted task/prerequisite
evidence, within the existing attempt and no-progress limits. Unknown or unowned
paths, exhausted repair, and incomplete/unavailable validation end as failed with
an explanation, without an empty request for user input. Acceptance remains a
separate passed result when only the later build failed; combined readiness stays
false until both gates pass. A successful static acceptance does not erase build
errors or authorize export.

All sandbox strategies route through the `SandboxPort` seam
(`mozaiksai/core/ports/sandbox.py`, Tier 1 stable) and its adapters.
Sandboxes are **ephemeral workspaces, never truth stores** — outcomes
persist into build records; the sandbox itself is disposable.

The Docker adapter connects only to the local daemon. Its CLI subprocess
ignores inherited `DOCKER_HOST`, `DOCKER_CONTEXT`, Docker CLI config, and model
credentials.
One-shot `app_validation` sessions have no network and publish no ports; the
validation tool sets that purpose itself even when supplied other metadata.
Validation receives only the fixed nonsecret sandbox resource paths. Explicit
`MOZAIKS_PREVIEW_ENV_*` values are reserved for interactive preview sessions and
are never forwarded into build validation.
Canonical compilation and the shared shell build use dependencies already in
the preview image. Interactive Studio preview sessions remain separate and
network-capable, so this isolation does not authorize private repository
candidate previews.

Canonical app bundles do not own an npm project. Build validation stages bundle
members into the existing standalone workspace layout, compiles generated Python,
and builds the packaged shared web shell against that app workspace. Agent-provided
commands cannot replace these checks. The same checks run for Docker, E2B, and
explicit local validation; local validation requires installed shared shell dependencies.
Compilation does not bind or invent app identity before export. Static acceptance
still checks schemas, references, module implementation, and runtime loading.
Interactive runtime/browser acceptance is a separate step, not implied by a build.
AppGenerator's runtime smoke seeds subscription assignments in its disposable
database from the configured assignment store and the declared data-contract
fields and unique indexes. This gives each synthetic user a distinct valid
assignment without relaxing the app's index checks. Unknown required fields
without declared defaults fail the smoke instead of receiving invented values.
Restore and activation read the owned artifact through the canonical content store,
verify its archive identity and SHA-256, and consume those same verified bytes.
Records without this identity must be validated and saved as a new canonical
artifact before activation. There is currently no persisted-draft revalidation
endpoint; the build workflow must produce that new artifact. Source checks on a
refinement alone do not certify the whole app build.
One-shot validation always terminates its sandbox and returns `preview_url: null`.
The shared shell bundles its fallback logo and does not require undeclared
app-owned background images. Existing preview templates must be rebuilt to pick
up frontend changes; host-side lifecycle updates do not update template contents.

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

### Preview and brand context

The build workspace keeps the management app's branding while the generated
app runs in its own iframe with its own shell, theme, fonts, and assets.
Selecting a build does not apply the generated app's theme to the workspace.
The shared theme loader applies declared fonts and colors from that app's
`brand/theme_config.json`. It accepts an additional theme overlay only from a
`source: custom` response whose `app_id` matches the active app. A standalone
host returning its full declarative config from `/api/themes/{app_id}` does
not replace those explicit brand values with the config's shorthand defaults.
Omitted brand tokens inherit the app's selected light/dark appearance and font
presets so a partial color override keeps readable background and text colors.
The preview header says **Draft app preview**, identifies the saved version,
and explains that the preview is temporary. Starting or interacting with a
preview does not publish the app.

**Open draft preview** opens the running app in a separate tab, leaving the
build workspace open in the original tab. The shared shell shows a small
**Draft preview** indicator in the app, including after navigation or while
sign-in settings load. Sandbox sessions set `VITE_MOZAIKS_PREVIEW=true` through
their resource environment; preview environment overrides cannot turn it off.
The indicator uses a neutral style and does not intercept clicks. Normal app
and management launches do not display it.

**Stop preview** confirms sandbox teardown and releases its capacity. Starting
a different saved version from the same workbench stops its previous preview
first. Leaving the workspace alone does not stop a preview opened separately;
its absolute lifetime still applies.
When a saved-review chat opens another app, the preview hook is recreated. If
the owner's preview quota is full, Start reads the owner's durable preview
handles, stops the older session, then retries allocation once. An unavailable
recovery read or failed stop leaves the quota occupied and shows an error.

App Zero follows the same rule: its preview can share the Mozaiks logo while
the draft indicator identifies the separate runtime. Its own admin pages do
not serve as a return link to the original build workspace. Existing sandbox
templates must be rebuilt to include the indicator in the shared shell.

Hosted management apps can reuse the Studio app directory and dashboard with
explicit navigation and Factory identity inputs. Management links retain the
hosted app record's route identity; build reads validate the target app and
registry binding, and workflow launches use that registry's execution host.
These identities do not change the management shell's branding.

### Session ownership

- Manager: `mozaiksai/core/sandbox/preview_sessions.py`
  (`ArtifactPreviewSessionManager` over `SandboxPort`; one session per
  host/user/artifact version, with an absolute `SANDBOX_TTL_MINUTES` deadline).
- Routes (Studio host, `mozaiksai/hosts/routers/sandbox.py`):
  `POST /api/artifacts/{artifactId}/sandbox?build_registry_id=...` (create/reuse),
  `POST /api/sandbox/{id}/sync`, `POST /api/sandbox/{id}/start`,
  `GET /api/sandbox/{id}/status`, `POST /api/sandbox/{id}/stop`,
  `GET /api/sandbox` (owner's sessions across builds),
  `GET /api/sandbox?build_registry_id=...` (one owned build),
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

### Preview auth posture

A public app (no auth contract) is previewed as its visitors see it. The
preview environment sets `AUTH_ENABLED=false`, `AUTH_PROVIDER=none` and
`AUTH_ANON_ACCESS=public` unconditionally and drops `AUTH_ANON_ROLES`, so a
forwarded `MOZAIKS_PREVIEW_ENV_*` value can neither give the preview
development access nor stop it starting. `AUTH_ANON_SCOPES` is kept, so a
preview matches its deployment: pages and module actions run with the
visitor's scopes and admin pages stay closed. An app with an auth contract
keeps authentication on in its preview.

This matters most on E2B: the SDK makes a sandbox's ports public by default,
so anyone who holds a preview URL can reach the preview. The posture is set
inside the sandbox by the packaged runtime, so it takes effect only in a
`mozaiks-sandbox:local` image and an E2B template rebuilt from a revision that
includes it; rebuild both after upgrading.

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
The install runs in the sandbox user's site directory with pip's
`--break-system-packages` option because the disposable image uses Debian's
externally managed Python. It does not install into the operator's Python.
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
`E2B_API_KEY`, and prints build progress and template/build identifiers. The
resulting name or ID belongs in `E2B_TEMPLATE` or `SANDBOX_TEMPLATE`; credentials remain in
the operator environment. The helper does not run automatically during app
generation or CI.

The staged upload includes only the framework source and packaging files.
It excludes local `.env` files (retaining `.env.example`), `node_modules`,
virtual environments, generated Tailwind source links, caches, build output,
browser reports, and runtime logs.
The `logs` Python package remains included; its generated output directories
are excluded.

Only explicitly configured `MOZAIKS_PREVIEW_ENV_<NAME>` values become preview
environment variables. Factory API keys, credentials, and database URLs are not
inherited. The manager also supplies the canonical image's three nonsecret
frontend/Factory resource paths, because E2B template-build ENV is not retained
as session environment. Public apps may declare `authRequired: false`; authenticated apps
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

One-shot validation always stops its sandbox and clears `preview_url`; its
result is not an interactive preview. `sandbox_terminated` records confirmed
cleanup. Unconfirmed cleanup fails validation rather than reporting success.
Interactive artifact previews are owned separately by Studio. Failed starts
stop immediately and expired sessions are swept every 15 seconds. Graceful
worker shutdown closes its status sockets and preserves active previews for
another worker to recover. Provider outages retain durable session identity
for cleanup retries; the provider-side deadline is the final
backstop after a process crash. Polling and normal file/command operations do
not renew the E2B deadline. Supervisor launch allows only the exact
provider-published hostname via Vite's
`__VITE_ADDITIONAL_SERVER_ALLOWED_HOSTS`, not arbitrary hosts or all provider
subdomains. Explicit `E2B_TIMEOUT` caps one-shot validation even when a tool
requests a longer timeout.

`MongoPreviewStore` owns immutable host/user/artifact/build/target bindings in
the framework system database. `PreviewCoordination` holds bounded admission
reservations; `PreviewSessions` holds provider IDs, status, operation leases,
and synced manifest/path metadata. File contents and environment credentials
are not copied into these records. Admission uses an atomic compare-and-swap
on one bounded coordinator document, so standalone MongoDB is supported.
MongoDB failure rejects new allocation; there is no process-local fallback.

`SANDBOX_MAX_SESSIONS` and `SANDBOX_MAX_OWNER_SESSIONS` apply across workers
sharing that database. A bounded FIFO queue waits up to
`SANDBOX_QUEUE_TIMEOUT_SECONDS`; it skips owners already at their limit so
they cannot block other owners. `SANDBOX_MAX_PARALLEL_CREATES` bounds provider
allocation calls. Runtime installation/start calls remain bounded by total
active sessions, not that allocation limit. Exhaustion returns HTTP `429`
with `Retry-After: 15`; an operation already in progress returns `409` with
`Retry-After: 2`. The create request waits for its queue turn; no detached
workflow or agent run is created. Queued requests abandoned by a crashed
worker expire, and retrying the same artifact reuses the shared reservation.

All workers must agree on limits and provider settings. Limits are positive;
replace former zero/unlimited values before upgrading. Capacity can be
reconfigured after reservations drain. An unconfirmed remote allocation
retains its reservation through the provider lifetime plus the bounded
allocation allowance. Failed cleanup never silently frees capacity. These
limits cover interactive artifact previews; one-shot validation and hosted
billing budgets retain their separate owners.

For the first upgrade from process-local preview tracking, stop existing
previews and upgrade all Studio workers before relying on shared admission.
Old in-memory session records cannot be imported, and older workers do not
participate in the shared limits. Subsequent worker restarts preserve sessions
created with the durable store.

If deleting session metadata fails after admission removal, a closed metadata
row can remain without an admission entry. It cannot revive a preview or
consume capacity, but automated reconciliation of these orphan rows is not
implemented. Monitor this collection before substantially increasing limits;
repeated deletion failures can accumulate metadata.

Sync, start, health checks, and stop acquire a renewed per-preview lease.
State writes require the current token and an unexpired lease. An interrupted
operation requires teardown before another mutation; a successor cannot
resume a partially written bundle. Status health checks are coalesced across
workers for ten seconds. WebSocket status observes durable state without
issuing a provider health call per socket. Rolling workers may reconnect to
an E2B session by its persisted provider ID. Local Docker workers must share
the same daemon; a localhost preview URL is only useful on that machine.

This enables shared preview coordination, not an end-to-end capacity claim
for the whole product. Exercise the actual deployment's authentication,
artifact storage, provider limits, and expected traffic before broad rollout.

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
| `SANDBOX_MAX_SESSIONS` | `20` | shared active artifact preview limit, including uncertain allocations |
| `SANDBOX_MAX_OWNER_SESSIONS` | `2` | shared active previews per host app/user |
| `SANDBOX_MAX_PENDING` | `20` | shared pending admission queue bound |
| `SANDBOX_MAX_PARALLEL_CREATES` | `4` | simultaneous provider allocation calls |
| `SANDBOX_QUEUE_TIMEOUT_SECONDS` | `15` | maximum wait for an admission turn |
| `SANDBOX_OPERATION_LEASE_SECONDS` | `60` | renewed mutation lease; expired operations require teardown |
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

## Opt-in live smoke

```bash
MOZAIKS_RUN_GENERATED_APP_E2B_SMOKE=1 python -m pytest tests/test_generated_app_e2b_smoke.py -q -s --no-cov
```

This runs a Factory-materialized fixture through the real E2B supervisor, frontend proxy, backend, and module
action, then confirms the provider no longer knows the sandbox. It requires
`E2B_API_KEY` and `E2B_TEMPLATE`, spends provider credits, and is never enabled
by ordinary CI. This is runtime coverage, not a live-LLM Factory journey.
Optional `MOZAIKS_E2B_SMOKE_PLAYWRIGHT_MODULE` (an installed Playwright module
path) and `MOZAIKS_E2B_SMOKE_SCREENSHOT_DIR` enable desktop/mobile UI checks.

## Non-goals

- Sandboxes are not a hosting runtime. Deployment goes through the
  provider-neutral deployment artifacts and the hosting pipeline (see
  [generated-app-deployment-contract.md](../deployment/generated-app-deployment-contract.md),
  E2B Role).
- Agent-level code/shell execution is AG2's job (`SandboxShellTool`,
  `sandbox_shell: true` in agents.yaml), not `SandboxPort`'s.
