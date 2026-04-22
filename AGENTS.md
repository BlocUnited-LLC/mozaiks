# AGENTS.md

Repository-level guidance for coding agents working in this repo.

Read [ARCHITECTURE.md](ARCHITECTURE.md) and [CLAUDE.md](CLAUDE.md) first.

Read `ARCHITECTURE_BOUNDARIES.md` before making structural changes.

This repo uses layered FastAPI hosts as the canonical server composition:
- `runtime_app.py`
- `platform_app.py`
- `studio_app.py`
- `mozaiks_app.py`

`studio_app.py` is the local/private builder host and the default local run target. `mozaiks_app.py` is the hosted Mozaiks product host.


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
| AppBackendPort contract | Module manifests + routes |
| Platform modules (`platform/modules/`) | Modules (`platform/modules/`) |
| Workflow authoring | Event bus, notifications, subscriptions |

**Naming:**
- "module" in the mozaiksai platform layer (`platform/modules/`) — declared via `module.yaml` + `handler.py`. Deterministic CRUD/action surfaces with no AI. Support workflows by providing the CRUD actions that AI agents call.
- "module" in mozaiks-core-public (`platform/modules/`) — declared via 6 YAML manifests + `backend/logic.py` + `backend/routes.py`. Full app feature packs with event bus integration.
- Both repos use "modules" but the file shapes differ. The mozaiksai `module.yaml` is a simple handler manifest (name, version, actions, events). The mozaiks-core-public `module.yaml` (one of six files) declares the REST endpoint surface that AI agents call.

Do not conflate these shapes — they differ by file structure and purpose even though both use the word "module".

## UI System Rule

Treat the UI system as three separate surface contracts sharing one primitive/design foundation:

1. `App UI` — schema-driven page primitives (AppPageSchema YAML rendered by SchemaPage)
2. `Agent UI tools` — event-driven React surfaces that compose shipped primitives
3. `Transition UI` — router/session components with routing-specific props
4. `Core shell pages` — first-class framework pages registered in `coreComponents.js` (ChatPage, AdminPortal, ProfilePage, AppAdminDashboard, SchemaPage)

Do not collapse these into one generic contract.

## Operation Manifest Rule (mozaiks-core-public)

When working in or generating for `mozaiks-core-public`:

- Every capability pack needs all 6 YAML files — no partial manifests
- Event names in `events.yaml` must match `notifications.yaml` and `logic.py` exactly
- Endpoint paths in `module.yaml` must match `routes.py` exactly
- `operation_manager` discovers everything automatically — no edits to `director.py` needed for new operations
- AppGenerator produces these files via `ConfigMiddlewareAgent` in `platform_config` task mode

## Validation Rule

For runtime, generator, orchestration, or contract changes:

- run targeted tests
- update docs
- prefer at least one real runtime smoke when practical
