# Add a Module

A module is backend application logic such as CRUD actions, domain data, and
business rules. It runs without AI. For AI-driven behavior, use a workflow
instead.

Modules and workflows complement each other: modules provide the action surface
that AI agents call.

## When to Add a Module

Add a module when you need:

- a persistent entity (users, orders, projects, messages)
- an API endpoint that pages or workflows call
- business logic that should not require an LLM
- events that other modules or workflows react to

If the behavior requires planning, reasoning, or generation, use a workflow
and have it call module actions for persistence.

## File Structure

```text
app/modules/{name}/
├── module.yaml              ← required: identity, actions, permissions
├── contracts/               ← add only the contracts this module needs
│   ├── events.yaml          ← domain events this module publishes
│   ├── reactions.yaml       ← events from other modules this module handles
│   ├── notifications.yaml   ← notification rules per event
│   ├── settings.yaml        ← user-configurable settings schema
│   ├── admin.yaml           ← optional feature panels for the framework admin shell
│   └── profile.yaml         ← optional user profile page panels
├── runtime_extensions.yaml  ← only for raw webhook routers or background workers
└── backend/
    ├── __init__.py
    ├── handler.py           ← required: thin dispatch, one method per action
    ├── service.py           ← all business logic and event emission
    ├── repo.py              ← MongoDB access only, no logic
    ├── policy.py            ← query scoping for multi-tenancy
    └── schemas.py           ← typed document shapes and pure helpers
```

Only `module.yaml` and `backend/handler.py` are required. Add contracts and
backend files only when the module needs them.

Use `contracts/reactions.yaml` as the canonical event-reaction contract.
The runtime rejects `contracts/subscriptions.yaml`; contributor-facing changes
must author `contracts/reactions.yaml`.

Mozaiks loads modules automatically at startup. No manual registration step is
needed. Routes appear at `/api/modules/{name}/{action_id}`.

## Backend File Responsibilities

| Layer | Do | Don't |
|-------|-----|-------|
| `handler.py` | Receive ctx + kwargs, call service, return result | Business logic, DB access, event emission |
| `service.py` | Validate, call repo, emit events after commit | Direct DB access, HTTP calls |
| `repo.py` | MongoDB queries only | Business logic, validation, events |
| `policy.py` | Build query dicts from ctx | DB access, side effects |
| `schemas.py` | TypedDicts, timestamp helpers | I/O, imports from service or repo |

## Runtime Data Integrity

!!! warning "Never return fake data from module actions"
    Modules own runtime facts. UI primitives render whatever the module returns —
    there is no safety net that catches invented data.

    - Do not return sample, demo, mock, fake, placeholder, or random records
    - Do not hardcode KPI counts, balances, totals, percentages, or status trends
    - Do not leave `TODO`, `NotImplemented`, or "in production" branches in runtime paths
    - Compute `*_summary`, `*_stats`, `*_metrics`, and `get_*_count` from `repo.py` / MongoDB queries
    - Return honest empty values such as `0`, `[]`, or `null` when no data exists
    - Return trend/change fields only when a real historical comparison is queried; otherwise return `null` or omit the field

## Minimum `module.yaml`

```yaml
schema_version: mozaiks.module.v1
module:
  id: {name}
  display_name: {Display Name}
  version: 1.0.0
  description: What this module does.
  owner: mozaiks
  visibility: internal
  handler: backend.handler:{Name}Handler

permissions:
  - id: {name}.read
    description: Read {name} data.
  - id: {name}.manage
    description: Create and update {name} records.

actions:
  - id: list_{name}s
    description: List records.
    handler_method: list_{name}s
    api_surface: public_readonly
    input_schema:
      type: object
      properties:
        limit: { type: integer }
    output_schema:
      type: object
      required: [items, count]
    permissions: [{name}.read]

  - id: create_{name}
    description: Create a record.
    handler_method: create_{name}
    input_schema:
      type: object
      required: [name]
      properties:
        name: { type: string }
    output_schema:
      type: object
      required: [success]
    permissions: [{name}.manage]
    emits: [domain.{name}.record_created]
```

`api_surface` is optional action metadata. Use it to describe the intended
surface for tooling and generated UI/API documentation, for example `public`,
`public_readonly`, `internal`, or `admin_internal`. It is not an authorization
mechanism; backend access remains governed by the action's `permissions`.

Actions declared `internal` or `admin_internal` **cannot be dispatched through
the external HTTP module route** (`/api/modules/{name}/{action}`). The runtime
rejects those requests with 404 regardless of authentication status. This
prevents external callers from triggering event-bus reaction handlers directly.
Use these surfaces for event reactions and trusted internal runtime calls; invoke
them through `ModuleExecutor` with an explicitly constructed server-owned
`ModuleDispatchAuthority` (for example `framework_internal`, or
`event_reaction` built via `event_reaction_authority`) from trusted code paths.

## module.yaml Schema Rules

`module.yaml` is validated by a strict Pydantic model with `extra="forbid"`.
Only the declared top-level keys are permitted: `schema_version`, `module`,
`permissions`, `actions`, `capabilities`. Any other top-level key causes a
validation error at startup.

**Do not add a `changelog:` field** — it is not a declared key and will break
module loading with an `extra_forbidden` error. Record version promotion notes
as a YAML block comment before the `schema_version` line instead:

```yaml
# v1.0.0 (2026-07-01): Promoted to stable. Wired settlement reaction.
schema_version: mozaiks.module.v1
module:
  id: {name}
  version: 1.0.0
  ...
```

Bump `module.version` (a semver string inside the `module:` block, not
`schema_version`) when promoting a module to stable or making a breaking
contract change.

## Events

Declare domain events in `contracts/events.yaml` when other modules or
workflows need to react to this module's actions.

```yaml
schema_version: mozaiks.events.v1
events:
  - type: domain.{name}.record_created
    version: 1
    description: Emitted when a record is created.
    producer: {name}
    payload_schema:
      type: object
      required: [record_id, owner_id]
```

Add reactions in `contracts/reactions.yaml` when this module needs to handle
events from other modules. The event payload fields are unpacked as keyword
arguments into the handler method.

## Connect to a Page

Point a page's `DataTable` directly at the module action endpoint:

```yaml
# app/ui/pages/items.yaml
sections:
  - id: items_table
    primitive: DataTable
    config:
      api_endpoint: /api/modules/{name}/list_{name}s
      columns:
        - { key: name, label: Name }
```

## Connect to a Workflow

Call module actions from workflow tools using the backend request helper:

```python
from mozaiksai.core.workflow.app_backend_tools import backend_request

result = await backend_request(
    method="POST",
    path="/api/modules/{name}/create_{name}",
    body={"name": "example"},
    context_variables=context_variables,
)
```

## Track App Metrics

Use `ctx.metrics.track(...)` when a module needs durable usage telemetry such as
views, clicks, conversions, or feature usage. Keep payment, payout, and campaign
pricing logic in the owning app module.

```python
await ctx.metrics.track(
    "app.viewed",
    subject_type="app",
    subject_id=app_id,
    session_id=session_id,
    attribution_id=attribution_id,
    dimensions={"surface": "discover"},
)
```

Use `ctx.metrics.record_snapshot(...)` when the module owns a real numeric KPI
value such as active users, revenue, retention, usage, or conversion totals for
a reporting period. Use `ctx.metrics.summarize_values(...)` behind a
module-owned facade action for UI or admin reads. Do not use metric snapshots as
payment, billing, entitlement, or payout truth; those facts belong in the
module that owns the business state.

## Admin Panel

A module may include `contracts/admin.yaml` when it needs feature-owned panels
inside the framework admin shell. AppGenerator can derive these panels from
declared actions, or you can write the contract by hand.

**Derivation rules (AppGenerator)**:

| Action pattern | Panel type | Default section |
|----------------|-----------|-----------------|
| `list_*`, `search_*` (collection) | `ResourceTable` / `DataTable` | `overview` |
| `*_stats`, `*_summary`, `*_metrics` | `SummaryStrip` | same as record panel |
| `approve_*`, `reject_*`, `publish_*` | row action on primary table | `operations` |

Section is inferred from the module's entity domain:
- billing / subscription / payment → `billing`
- user / account / member / profile → `users`
- request / queue / approval / job → `operations`
- usage / quota / limit / metric → `usage`
- setting / preference / config → `settings`
- everything else → `overview`

**Example** — a `hosting_requests` module with `list_requests` and `get_summary` actions
produces:

```yaml
schema_version: mozaiks.admin.v2
panels:
  - id: hosting_requests.summary
    label: Hosting Overview
    section: operations
    order: 1
    renderer: schema
    layout: full-width
    sections:
      - id: summary-strip
        primitive: SummaryStrip
        config:
          api_endpoint: /api/modules/hosting_requests/get_summary
          items:
            - { label: Pending, value_key: pending_count }

  - id: hosting_requests.requests
    label: Hosting Requests
    section: operations
    order: 2
    renderer: schema
    layout: full-width
    sections:
      - id: requests-table
        primitive: ResourceTable
        config:
          api_endpoint: /api/modules/hosting_requests/list_requests
          columns:
            - { key: app_name, label: App }
            - { key: status,   label: Status, type: status }
            - { key: requested_at, label: Requested }
```

**To add a panel manually** when the generator didn't derive one:

```yaml
schema_version: mozaiks.admin.v2
panels:
  - id: {name}.overview
    label: {Display Name}
    section: overview
    order: 10
    renderer: schema
    layout: full-width
    sections:
      - id: {name}-table
        primitive: DataTable
        config:
          api_endpoint: /api/modules/{name}/list_{name}s
          columns:
            - { key: name, label: Name }
```

Valid `section` values: `overview`, `users`, `billing`, `usage`, `activity`,
`operations`, `settings`, `integrations`, `support`.

The admin runtime auto-discovers `contracts/admin.yaml` at startup — no registration
step needed. Panels appear inside the framework-owned admin surface under the
declared section. They are not Studio app portfolio pages.

## Read Next

- [Module System](../../architecture/modules-systems/module-system.md)
- [Module Authoring Patterns](../../architecture/modules-systems/module-authoring-patterns.md)
- [AppGenerator Capability Planning](../../architecture/modules-systems/appgenerator-capability-planning.md)
