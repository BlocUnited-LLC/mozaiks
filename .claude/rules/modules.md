---
paths:
  - "app/modules/**"
  - "factory_app/workflows/AppGenerator/**"
  - "docs/guides/adding-modules/**"
  - "docs/architecture/modules-systems/**"
---

# Module Rules

Use these rules when authoring module contracts, generated module backend code,
or builder prompts that produce modules.

## Runtime Facts

Modules own runtime truth. UI primitives render module output; they do not
invent product facts.

## Reaction Contract

Use `contracts/reactions.yaml` as the canonical module event-reaction manifest.

- `contracts/events.yaml` declares module-emitted event types, and
  `module.yaml.actions[].emits` must reference declared events there
- `schema_version` must be `mozaiks.reactions.v1`
- the root collection key is `reactions`
- each reaction uses `event_type`
- targets use `target.kind` plus exactly one of `target.handler_method`,
  `target.capability_id`, or `target.notification_id`
- `contracts/notifications.yaml` declares notification rules derived from
  events and is not a substitute for `contracts/reactions.yaml`

Do not author `contracts/subscriptions.yaml` in module work. Runtime rejects it
so `contracts/reactions.yaml` remains the single reaction-routing contract.

## Persistence Contract

Persistent modules use `backend/repo.py`, `backend/policy.py`, and
`backend/schemas.py` as the canonical backend support files.

- `backend/schemas.py` is the typed shape layer; do not introduce
  `backend/models.py` or `backend/models/*.py`
- `config/data.json` and
  `config/data_migrations/{migration_id}.json` are the canonical collection
  planning artifacts
- generated repo code must use `ModuleContext.persistence`
  (`ctx.persistence.collection(module_id, entity_name)`), not `ctx.db`
- `repo.py` owns persistence operations only; `service.py` owns business logic
  and event emission; `handler.py` remains thin dispatch

Do not ship module runtime actions that return:

- sample/demo/mock/fake/placeholder/dummy/random records
- hardcoded KPI counts, totals, balances, percentages, or status changes
- TODO, `NotImplemented`, or "in production" branches

For `*_summary`, `*_stats`, `*_metrics`, `get_*_count`, dashboard, or overview
actions:

- compute values from `repo.py` / MongoDB queries
- return `0`, `[]`, or `null` only as honest empty data
- use trend/change fields only when a real historical comparison or metrics
  snapshot is queried
- otherwise omit trend/change fields or return `null`

Keep the module layer deterministic: handler delegates, service owns business
logic/events, repo owns persistence, policy owns scoping, and schemas owns pure
shape helpers.

## Helper Files

Backend helper files are allowed only when they are declared before generation,
module-local under `backend/`, justified by a specific purpose, and imported by a
canonical layer or referenced by `runtime_extensions.yaml`.

App-level support code is a separate lane. Use `services/integrations/` for
external or hosted API clients and `services/adapters/` for provider-specific
implementation boundaries that modules or workflows call. Do not create a module
just to house a provider adapter, and do not put module business state or actions
in app-level backend support code.

Allowed generic helper examples:

- external provider client
- webhook receiver helper
- startup service helper
- audit event subscriber
- notification delivery client
- complex pure domain helper that would bloat `service.py`

Do not create helper files for:

- generic business logic that belongs in `service.py`
- persistence/query access that belongs in `repo.py`
- auth or scope logic that belongs in `policy.py`
- DTOs or typed shapes that belong in `schemas.py`
- transport or WebSocket infrastructure
- workflow orchestration
- random file splitting

## Profile Panels

`contracts/profile.yaml` is optional. Use it when a module has user-scoped data
worth surfacing on the user profile page (account summary, notification prefs,
usage stats). Do not add profile.yaml to every module.

- `schema_version` must be `mozaiks.profile.v1`
- panels bind to module actions via `action:` — the platform calls the action at
  `/api/me/profile-panels` request time and attaches the result as `data`
- valid `kind` values: `metrics`, `list`, `component` — `form` is reserved and rejected at load time
- `kind: component` requires a `component:` field and no `fields:` list
- `kind: metrics` and `kind: list` require a non-empty `fields:` list
- profile panels must not expose admin-only actions or secrets
- profile panels do not replace or override `/api/me` identity

## Runtime Extensions

`runtime_extensions.yaml` is optional and module-level. Use only:

- `api_router` for a module-local generic external webhook receiver or callback route
- `startup_service` for a module-local audit/event subscriber or polling worker

Entrypoints must be module-local backend files and must be represented in
generated backend outputs or Python stubs. Do not use runtime extensions for
generic business logic, persistence, auth/scope helpers, transport
infrastructure, or workflow orchestration.

