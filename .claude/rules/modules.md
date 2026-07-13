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
- `data/contract.json` and
  `data/migrations/{migration_id}.json` are the canonical collection
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

## Profile Surface

`contracts/profile.yaml` is optional. Use it when a module has user-scoped data
worth surfacing on the social profile page (`/me`, `/u/:username`). Do not add
`profile.yaml` to every module — only when the tab content is meaningfully
user-facing (messages inbox, contacts roster, wallet summary, usage stats).

`schema_version` must be `mozaiks.profile.v1`.

### Tabs (preferred)

Tabs appear in the horizontal tab bar on the profile hero. The platform hydrates
each tab by calling the declared `action` and passing the result as `data` to the
React component.

```yaml
schema_version: mozaiks.profile.v1
tabs:
  - id: messages
    label: Messages
    order: 10
    action: list_threads        # module action id — must be declared in module.yaml
    component: MessagingTab     # React component registry key — must be in js_stubs
```

- `id` and `label` are required and must be unique within the manifest
- `component` is required — the registered React component receives `{ tab, data }` props
- `action` is optional — hydrates `data` at `/api/me/profile-tabs` request time
- `order` defaults to 100; use low values (10, 20) for core social tabs
- tabs must not expose admin-only actions or secrets
- the component must be declared in `js_stubs` so the generator knows to register it

### Panels (panel mode)

Stacked-card panels are the older format. Prefer tabs for new modules.

- panels bind to module actions via `action:` at `/api/me/profile-panels` request time
- valid `kind` values: `metrics`, `list`, `component` — `form` is reserved and rejected
- `kind: component` requires `component:` and no `fields:`
- `kind: metrics` and `kind: list` require a non-empty `fields:` list
- panels must not expose admin-only actions or secrets

Profile tabs and panels do not replace or override `/api/me` identity.

## Entitlement Gating

`ActionDef.entitlement_gate` is optional. Set it to a capability_id string when
the action must require an active SaaS plan grant before executing.

- `ModuleExecutor` calls `EntitlementPort.check(capability_id, ...)` before dispatch
- On denial returns `error_code: ENTITLEMENT_REQUIRED`
- Non-SaaS apps omit `app/config/subscriptions.yaml` — `NoOpEntitlementAdapter` is wired and all checks pass automatically
- Only set `entitlement_gate` on public user-facing actions; never on `admin_internal` actions
- The enforcement mechanism lives in the runtime; generated apps must not re-implement it
- capability_ids used in `entitlement_gate` MUST appear in `app/config/subscriptions.yaml` under at least one plan
- For SaaS apps, AppGenerator may emit `app/config/subscriptions.yaml` as the
  plan catalog (`schema: mozaiks.subscriptions.v1`) and optional app-owned
  facade modules for UI-facing capability status queries.
- Do not generate app-local entitlement adapter files. The platform wires the
  OSS `ConfiguredEntitlementAdapter` from the loaded subscriptions config.

**Runtime enforcement flow:**
1. Platform loads `app/config/subscriptions.yaml`.
2. If config is present, platform wires `ConfiguredEntitlementAdapter`; otherwise
   it wires `NoOpEntitlementAdapter`.
3. On action dispatch, executor calls `EntitlementPort.check(capability_id, ...)`.
4. `ConfiguredEntitlementAdapter` checks the app's subscriptions config and any
   declared assignment store before returning `EntitlementResult`.

Example in `module.yaml`:
```yaml
actions:
  - id: list_transactions
    handler_method: list_transactions
    entitlement_gate: wallet.view   # must appear in subscriptions.yaml under ≥1 plan
    permissions: [wallet.read]
    ...
```

Do not confuse entitlement gating with:
- `permissions[]` — auth-level access control (role/permission checks)
- `contracts/reactions.yaml` — pub/sub event routing (not SaaS subscriptions)

## Runtime Extensions

`runtime_extensions.yaml` is optional and module-level. Use only:

- `api_router` for a module-local generic external webhook receiver or callback route
- `startup_service` for a module-local audit/event subscriber or polling worker

Entrypoints must be module-local backend files and must be represented in
generated backend outputs or Python stubs. Do not use runtime extensions for
generic business logic, persistence, auth/scope helpers, transport
infrastructure, or workflow orchestration.

