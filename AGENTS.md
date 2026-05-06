# AGENTS.md

Repository-level guidance for coding agents working in this repo.

Read [ARCHITECTURE.md](ARCHITECTURE.md) and [CLAUDE.md](CLAUDE.md) first.

This repo uses layered FastAPI hosts as the canonical server composition:

- `mozaiksai.hosts.runtime`
- `mozaiksai.hosts.platform`
- `mozaiksai.hosts.studio`
- `mozaiksai.hosts.mozaiks`

`mozaiksai.hosts.runtime` is the execution substrate. `mozaiksai.hosts.platform`
is the headless app host. `mozaiksai.hosts.studio` is the Studio management
interface host — the shared management layer for both local and hosted
deployments. `mozaiksai.hosts.mozaiks` is the hosted Mozaiks product host —
extends Studio, does not replace it.

Start via the CLI:

```
mozaiks serve ./my-app                  # platform host (no factory dependency)
mozaiks serve ./my-app --host studio    # Studio management host (requires factory_app)
```

Or directly via uvicorn:

```
uvicorn mozaiksai.hosts.studio:app --reload
```

CLI and Studio are **parallel interfaces** over shared system capabilities, not a
superset chain. Studio is not the CLI's UI. CLI owns developer tooling (filesystem,
scaffolding, process management). Studio owns the management interface (workspace
status, build lifecycle, artifacts, run history, config).

The current repo layout is transitional. The canonical target is documented in
[docs/architecture/foundations/distribution-and-workspace-model.md](docs/architecture/foundations/distribution-and-workspace-model.md).
Do not reintroduce a hybrid root that mixes the starter app bundle with shared
factory workflows.

## Repo Status

This codebase is **not in production**.

That means optimization goals are different from a legacy enterprise codebase:

- Prefer the cleanest canonical implementation.
- Prefer replacement over preservation.
- Remove stale logic when a better contract or architecture is introduced.
- Do not keep compatibility shims, aliases, wrappers, fallback branches, or duplicate schemas unless explicitly requested.

## Release Hold

Do **not** publish this repo yet.

- Do not create or push `v*` Git tags.
- Do not trigger `.github/workflows/release.yml`.
- Do not publish to PyPI or create a GitHub release.
- Do not treat trusted-publisher or GitHub environment setup as approval to
  release.
- Do not bump `mozaiksai/version.py` for a public release unless the user
  explicitly says the repo is production-ready and wants to publish.

Normal code pushes are fine. Public release actions are not.

## Replacement Policy

When adjusting behavior:

- Replace outdated logic instead of layering new logic on top of it.
- Delete obsolete prompt guidance, docs, tests, config fields, and dead code paths that no longer match the current contract.
- Do not leave "temporary" legacy branches behind.
- Do not preserve old shapes "just in case" unless the user explicitly asks for backward compatibility.

If a new contract is introduced, update all affected layers together:

- runtime behavior
- generator prompts/hooks
- declarative schemas
- validation
- docs
- tests

## Clean Code Standard

Avoid "AI slop":

- no speculative abstractions
- no duplicate helpers with overlapping purpose
- no verbose compatibility code for non-production paths
- no stale comments describing removed behavior
- no split source of truth when one canonical source will do

Prefer:

- tight contracts
- explicit validation
- small, named abstractions with clear ownership
- removing drift at the source

## Canonical Repo Boundary

Canonical ownership:

| Layer | Owns |
|-------|------|
| `mozaiksai.hosts.runtime` / `mozaiksai` | AI execution substrate, sessions, transport, persistence, workflow execution |
| `mozaiksai.hosts.platform` | Headless app host: modules, pages, shell config, admin, actions, routing |
| `mozaiksai.hosts.studio` | Studio management interface host — shared management layer (local and hosted) |
| `mozaiksai.hosts.mozaiks` | Hosted Mozaiks product host — extends Studio with hosted-only capabilities |
| `mozaiksai.hosts.bootstrap` | Repo-local path defaults (CWD-relative; no-ops when not in repo checkout) |
| `mozaiks_cli/` | CLI / developer interface — parallel to Studio, not a subset of it |
| `factory_app/app/` | First-party Studio app bundle — shared control-plane routes, default brand, and factory app contract |
| `factory_app/app/ui/pages/custom/studio/` | Studio UI components — management interface layer |
| `factory_app/app/modules/factory_control_plane/` | First-party Studio control-plane module |
| `chat-ui/src/admin/` | Platform-management surfaces — registered by Studio, inherited by Mozaiks App |
| `platform/` | Repo-local infrastructure assets only — not an app workspace |
| `generated/` | Generator output awaiting validation/promotion |

Canonical target:

- generated/customer apps become standalone workspaces/repositories
- shared generation core lives outside any individual app workspace
- app workspaces are self-contained and keep `config/`, `ui/pages/`, `workflows/`,
  `modules/`, `ui/`, and `brand/` together under the active app root
- hosted product workspaces should consume that same contract from their own repos

## Module Contract Rule

When working in or generating modules:

- Every capability pack that needs deterministic app behavior must emit the
  canonical module contract files: `module.yaml`, `events.yaml`,
  `subscriptions.yaml`, `notifications.yaml`, `settings.yaml`, `admin.yaml`,
  and `backend/handler.py`.
- YAML declares contracts, capabilities, events, settings, notification rules,
  subscriptions, and admin panels.
- Python stubs implement behavior and hooks: `backend/handler.py` is required
  (thin dispatch — one method per declared action, no business logic, no ctx.db,
  no ctx.emit); `backend/service.py`, `backend/repo.py`, `backend/policy.py`,
  and `backend/schemas.py` are the canonical support files for any module with
  database access;
  `backend/settings.py`, `backend/subscriptions.py`, `backend/notifications.py`,
  and `backend/admin.py` are optional hooks.
- Generic modules may publish `domain.*` events. Workflow starts/resumes are
  resolved by runtime/platform trigger contracts, not by hardcoded workflow
  names in module code.
- AppGenerator produces these files through structured output models. Keep the
  generated shapes aligned with runtime loaders, docs, and tests.

## Structured-Output-First Contract Rule

When introducing or changing YAML contracts:

- Treat canonical YAML files as structured-output-first contracts, not loose
  configuration blobs.
- Every canonical YAML shape must map cleanly to a strict structured output
  model that agents can produce repeatably and runtime code can validate
  deterministically.
- If a taxonomy is used by agents or loaders, define it explicitly as reusable
  typed fields/enums. Do not rely on prompt prose or naming conventions alone.
- Prefer shared submodels and finite namespaces over freeform nested objects.
- When a contract changes, update prompts, structured outputs, runtime
  validators/loaders, docs, and tests together so generators do not drift from
  execution.

This applies to `module.yaml`, `events.yaml`, `subscriptions.yaml`,
`notifications.yaml`, `settings.yaml`, `admin.yaml`, workflow YAMLs, and page
schemas.

## Contract-Declared Customization Rule

Customization is allowed, but only as a bounded extension of a strict
contract.

- YAML may reference helper/customization stubs only through explicit
  contract-defined fields.
- Python stubs are for backend/runtime-side extensions. JS/TS stubs are for
  frontend/admin/workflow UI extensions.
- Stubs must remain contract-bound: they implement declared hooks or entry
  points, not alternate schemas or undeclared behavior paths.
- Generator prompts must understand both the declarative contract and the stub
  shape they are allowed to emit.
- If a stub reference is optional, the contract must say when it is omitted and
  what the canonical no-customization behavior is.

## Generator Output Rule

Shared factory workflows live in `factory_app/workflows/`. Generator output must
not land directly in active runtime paths.

Workflow resolution is single-root by contract. A running host binds to one
workflow root via `MOZAIKS_WORKFLOWS_PATH` rather than auto-merging app and
factory roots. Studio defaults to `factory_app/workflows/`; product/app hosts
use the active app root's `workflows/` when present. The first-party
`factory_app/app` bundle should not check in `factory_app/app/workflows/`
until it owns a real app-local workflow. External hosted product workspaces may
define their own app-local workflows under their active app root, but those are
not canonical source code for this repo.

Use `MOZAIKS_GENERATED_ARTIFACTS_PATH`, defaulting to:

```text
generated/
```

Canonical generated paths:

```text
generated/apps/{app_id}/{build_id}/app/
generated/workflows/{app_id}/{build_id}/{workflow_name}/
```

Only explicit promotion may copy validated artifacts into an active app root.

## UI System Rule

Treat the UI system as separate surface contracts sharing one primitive/design foundation:

1. `App UI` — schema-driven page primitives rendered by `SchemaPage`
2. `Agent UI tools` — event-driven React surfaces that compose shipped primitives
3. `Transition UI` — router/session components with routing-specific props
4. `Core shell pages` — first-class framework pages registered in `coreComponents.js`

Do not collapse these into one generic contract.

## Validation Rule

For runtime, generator, orchestration, or contract changes:

- run targeted tests
- update docs
- prefer at least one real runtime smoke when practical
