# App Validation Sandboxes

How generated apps are built, validated, and previewed before deploy — the
four strategies, every environment variable, and hosted activation. The
ownership boundary against AG2's agent-level execution is defined in
[ag2-ownership-boundary.md](../workflows/ag2-ownership-boundary.md)
(Sandbox Execution Boundary).

## Strategies

Resolution precedence: tool argument → `app_validation_strategy` context
variable → `MOZAIKS_APP_VALIDATION_STRATEGY` env → automatic (`e2b` when
`E2B_API_KEY` is set → `docker` when a daemon is reachable → `local` when
npm exists → `skip`).

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
| `MOZAIKS_APP_VALIDATION_STRATEGY` | auto | strategy resolution (`e2b`/`docker`/`local`/`skip`) |
| `E2B_API_KEY` | unset | enables the e2b strategy |
| `E2B_TEMPLATE` | provider default | e2b adapter template |
| `E2B_TIMEOUT` | `300` (seconds) | e2b adapter session/default validation timeout |
| `SANDBOX_PREVIEW_PORT` | `3000` | dev-server port started + published for previews |
| `DOCKER_SANDBOX_IMAGE` | `node:20-alpine` | docker adapter image |
| `DOCKER_SANDBOX_TIMEOUT` | `300` | docker container lifetime (seconds) |
| `SANDBOX_TTL_MINUTES` | `30` | artifact preview-session TTL (also the e2b kill deadline) |
| `SANDBOX_TEMPLATE` | provider default | artifact preview-session e2b template |
| `SANDBOX_WORKDIR` | `/home/user/app` | artifact preview-session workdir |
| `APP_VALIDATION_BUILD_OUTPUT_MAX_CHARS` | `20000` | persisted build-output trim |

## Hosted e2b activation

For the hosted product (users have no local Docker):

1. Set `E2B_API_KEY` on the platform environment. Strategy auto-resolves to
   `e2b`; the Studio conversation renders the preview iframe
   (`E2BPreviewArtifact`) from the validation result's preview URL.
2. **Cost posture:** e2b bills per sandbox-minute. Sessions carry a kill
   deadline (`E2B_TIMEOUT` for validation runs, `SANDBOX_TTL_MINUTES` for
   artifact preview sessions) and identity metadata — audit orphans by
   listing provider sandboxes and matching `purpose`/`app_id` tags. Review
   spend after the first month; introduce per-user session caps before
   opening to outside users.
3. Local/OSS development needs none of this: a running Docker daemon gives
   the same preview for free.

## Non-goals

- Sandboxes are not a hosting runtime. Deployment goes through the
  provider-neutral deployment artifacts and the hosting pipeline (see
  [generated-app-deployment-contract.md](../deployment/generated-app-deployment-contract.md),
  E2B Role).
- Agent-level code/shell execution is AG2's job (`SandboxShellTool`,
  `sandbox_shell: true` in agents.yaml), not `SandboxPort`'s.
