# AGENTS.md

Repository-level guidance for coding agents working in this repo.

Read [ARCHITECTURE.md](ARCHITECTURE.md) and [CLAUDE.md](CLAUDE.md) first.
Read `ARCHITECTURE_BOUNDARIES.md` before making structural changes.

This repo uses layered FastAPI hosts as the canonical server composition:

- `runtime_app.py`
- `platform_app.py`
- `studio_app.py`
- `mozaiks_app.py`

`runtime_app.py` is the execution substrate. `platform_app.py` is the headless
app host. `studio_app.py` is the local/private builder host. `mozaiks_app.py`
is the hosted Mozaiks product host.

## Repo Status

This codebase is **not in production**.

That means optimization goals are different from a legacy enterprise codebase:

- Prefer the cleanest canonical implementation.
- Prefer replacement over preservation.
- Remove stale logic when a better contract or architecture is introduced.
- Do not keep compatibility shims, aliases, wrappers, fallback branches, or duplicate schemas unless explicitly requested.

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
| `runtime_app.py` / `mozaiksai` | AI execution substrate, sessions, transport, persistence, workflow execution |
| `platform_app.py` | Headless app host: modules, pages, shell config, admin, actions, routing |
| `studio_app.py` | Local/private builder UX used by CLI and local Studio |
| `mozaiks_app.py` | Hosted Mozaiks product layer |
| `platform/` | Default OSS/sample active app root |
| `mozaiks-platform/app/` | Active App Zero app root |
| `mozaiks-platform/brand/` | App Zero product brand/theme assets |
| `mozaiks-platform/ui/` | App Zero product UI extension |
| `mozaiks-platform/generated/` | Generator output awaiting validation/promotion |

## Module Contract Rule

When working in or generating modules:

- Every capability pack that needs deterministic app behavior must emit the
  canonical module contract files: `module.yaml`, `events.yaml`,
  `subscriptions.yaml`, `notifications.yaml`, `settings.yaml`, `admin.yaml`,
  and `backend/handler.py`.
- YAML declares contracts, capabilities, events, settings, notification rules,
  subscriptions, and admin panels.
- Python stubs implement behavior and hooks: `backend/handler.py` is required;
  `backend/settings.py`, `backend/subscriptions.py`, `backend/notifications.py`,
  and `backend/admin.py` are optional.
- Generic modules may publish `domain.*` events. Workflow starts/resumes are
  resolved by runtime/platform trigger contracts, not by hardcoded workflow
  names in module code.
- AppGenerator produces these files through structured output models. Keep the
  generated shapes aligned with runtime loaders, docs, and tests.

## Generator Output Rule

Builder workflows live in `mozaiks-platform/app/workflows/` because App Zero is
itself a Mozaiks app. Generator output must not land directly in active runtime
paths.

Use `MOZAIKS_GENERATED_ARTIFACTS_PATH`, defaulting to:

```text
mozaiks-platform/generated/
```

Canonical generated paths:

```text
mozaiks-platform/generated/apps/{app_id}/{build_id}/app/
mozaiks-platform/generated/workflows/{app_id}/{build_id}/{workflow_name}/
```

Only explicit promotion may copy validated artifacts into active roots such as
`platform/` or `mozaiks-platform/app/`.

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
