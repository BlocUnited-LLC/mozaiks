# AGENTS.md

Repository-level guidance for coding agents working in this repo.

Read [ARCHITECTURE.md](ARCHITECTURE.md) and [CLAUDE.md](CLAUDE.md) first.

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

## Two-Repo Boundary

This repo (`mozaiks`) and `mozaiks-core-public` are separate deployables. Do not cross-contaminate them:

| This repo (`mozaiks`) | `mozaiks-core-public` |
|-----------------------|----------------------|
| AI runtime (`mozaiksai`) | App backend runtime |
| chat-ui components | No UI — backend only |
| AppBackendPort contract | Plugin manifests + routes |
| Platform operations (`platform/operations/`) | Plugins (`platform/plugins/`) |
| Workflow authoring | Event bus, notifications, subscriptions |

**Naming:**
- "operation" belongs to the mozaiksai platform layer (`platform/operations/`). Declared via `operation.yaml` + `handler.py`. These are deterministic CRUD/action surfaces with no AI. They support workflows by providing the CRUD actions that AI agents call.
- "plugin" belongs to mozaiks-core-public (`platform/plugins/`). Declared via 6 YAML manifests + `backend/logic.py` + `backend/routes.py`. These are full app feature packs with event bus integration.
- `module.yaml` is a file name inside a mozaiks-core-public plugin — it declares the REST endpoint surface that AI agents call. It is unrelated to the mozaiksai operation system.

Do not conflate these. Do not rename one to match the other.

## UI System Rule

Treat the UI system as three separate surface contracts sharing one primitive/design foundation:

1. `App UI` — schema-driven page primitives (AppPageSchema YAML rendered by SchemaPage)
2. `Agent UI tools` — event-driven React surfaces that compose shipped primitives
3. `Transition UI` — router/session components with routing-specific props
4. `Core shell pages` — first-class framework pages registered in `coreComponents.js` (ChatPage, AdminPortal, ProfilePage, AppAdminDashboard, SchemaPage)

Do not collapse these into one generic contract.

## Plugin Manifest Rule (mozaiks-core-public)

When working in or generating for `mozaiks-core-public`:

- Every capability pack needs all 6 YAML files — no partial manifests
- Event names in `events.yaml` must match `notifications.yaml` and `logic.py` exactly
- Endpoint paths in `module.yaml` must match `routes.py` exactly
- `plugin_manager` discovers everything automatically — no edits to `director.py` needed for new plugins
- AppGenerator produces these files via `ConfigMiddlewareAgent` in `platform_config` task mode

## Validation Rule

For runtime, generator, orchestration, or contract changes:

- run targeted tests
- update docs
- prefer at least one real runtime smoke when practical
