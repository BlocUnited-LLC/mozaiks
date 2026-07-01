# Module System

This document is the canonical reference for the Mozaiks module system — what a module is,
what files it owns, how it integrates at runtime, and how it relates to capability packs.

## What a Module Is

A module is a self-contained unit of **deterministic business logic** declared in an app workspace's
`modules/` directory. Modules provide:

- an action surface that AI agents call through platform module routes or `AppBackendPort`
- domain event emission after state changes
- optional notification and reaction rules
- optional admin panels mounted inside the unified `/admin` shell

Modules are **not** AI workflows. They run without AI. Workflows call modules; modules do not
contain orchestration or reasoning logic.

---

## Canonical Module Shape

```text
app/modules/{module_id}/
├── module.yaml              # Required: identity, actions, capabilities
├── contracts/               # Optional companion manifests — include only what the module needs
│   ├── events.yaml          # Domain events this module may publish
│   ├── reactions.yaml       # Event reactions owned by this module
│   ├── notifications.yaml   # Notification rules per event
│   ├── settings.yaml        # User/app settings schema
│   ├── admin.yaml           # Admin panels mounted into /admin/*
│   └── profile.yaml         # User profile page panels (optional)
├── runtime_extensions.yaml  # Optional: api_router / startup_service host hooks
└── backend/
    ├── __init__.py
    ├── handler.py           # Required: thin dispatch, one method per declared action
    ├── service.py           # Recommended: all business logic and event emission
    ├── repo.py              # Recommended: MongoDB access layer, no logic
    ├── policy.py            # Recommended: query scoping for multi-tenancy
    ├── schemas.py           # Recommended: typed request/response + document shapes
    ├── {helper_files}.py    # Optional: declared, justified, module-local support files
    ├── settings.py          # Optional: settings hooks
    └── admin.py             # Optional: admin panel data hooks
```

  ### Backend file responsibilities

  The backend layer is intentionally split into small, canonical files:

  - `handler.py` is thin dispatch only. It exposes one method per declared action and delegates immediately.
  - `service.py` owns business logic, validation, and event emission after state is committed.
  - `repo.py` owns persistence access only. It does not validate, branch on product policy, or emit events.
  - `policy.py` owns ownership, scoping, and transition checks such as multi-tenancy filters.
  - `schemas.py` owns typed request/response objects, enums, and normalization helpers.

  Helper files are allowed, but only as explicit module-local extensions of those canonical layers. They must be declared before generation, justified by a specific purpose, imported by a canonical layer or referenced by `runtime_extensions.yaml`, and kept under the module's own `backend/` package. They should not become a catch-all for generic business logic.

  Rules for helper files:

  - declare them explicitly in the module's owned paths or generated stubs
  - keep them module-local
  - import them from a canonical layer, or reference them from `runtime_extensions.yaml`
  - do not use them to bypass the handler/service/repo/policy/schema split

### What is required

Only two things are required to produce a loadable module:

1. `module.yaml` — declares identity, handler class reference, permissions, and actions
2. `backend/handler.py` + `backend/__init__.py` — implements one method per declared action

Everything else is optional. Add a companion manifest under `contracts/` only when the
module actually needs it.

---

## `module.yaml`

Identity, capabilities, permissions, and action declarations.

```yaml
schema_version: mozaiks.module.v1
module:
  id: my_module
  display_name: My Module
  version: 1.0.0
  description: What this module does.
  owner: mozaiks
  visibility: internal          # public | internal | admin
  handler: backend.handler:MyModuleHandler

permissions:
  - id: my_module.read
    description: Read data.
  - id: my_module.manage
    description: Create and update records.

actions:
  - id: list_items
    description: List items.
    handler_method: list_items
    api_surface: public_readonly   # optional metadata; permissions still authorize access
    input_schema: { type: object, properties: { limit: { type: integer } } }
    output_schema: { type: object, required: [items, count] }
    permissions: [my_module.read]

  - id: create_item
    description: Create an item.
    handler_method: create_item
    input_schema: { type: object, required: [name], properties: { name: { type: string } } }
    output_schema: { type: object, required: [success] }
    permissions: [my_module.manage]
    emits: [domain.my_module.item_created]

# Optional: expose named capabilities for reaction routing and managed-capability wiring.
# Omit this section when reactions only target handler_method directly.
capabilities:
  - capability_id: my_module.list
    kind: action      # action | workflow | page | transition | hosted
    target: list_items
    title: List Items
    permissions: [my_module.read]
```

`capabilities[]` is optional. Use it when reactions need to route to a
named capability_id (`target.kind: capability`) or when a managed capability must
reference a named integration point. Each entry maps a stable `capability_id`
to an action, workflow, page, or transition `target`. The runtime validates
that `capability_id` values are unique within the module and that `target`
references a declared action.

`actions[].api_surface` declares the intended external HTTP exposure of an
action. Common values include `public`, `public_readonly`, `internal`, and
`admin_internal`. When auth is enabled, the platform requires an authenticated
principal for every external module HTTP action unless the action is explicitly
declared `public` or `public_readonly`. Missing or different values are
token-required by default. Local development with auth disabled still permits
anonymous calls, but those calls use a concrete empty permission list.

`api_surface` does not replace authorization. Runtime permission checks still
use `actions[].permissions`, entitlement checks still use
`actions[].entitlement_gate`, and the caller's granted permissions still come
from the authenticated principal plus the platform `module_scope_resolver`.
Anonymous public calls receive an empty permission list unless the app hook
intentionally grants permissions, so a public action with declared permissions
will still be denied for anonymous callers. Use `public` or `public_readonly`
only for actions that are safe to expose without a user token, such as a plan
catalog or landing-page read model. Use `internal` with `permissions: []` for
event-bus reactions and trusted runtime calls, not for anonymous HTTP traffic.

The platform host starts with the authenticated principal's auth scopes as the
default granted permission list. Deployers can register a host-agnostic
`module_scope_resolver` platform hook through `RUNTIME_PLATFORM_EXTENSIONS` to
map principals to app-local tenants, workspaces, memberships, teams, or roles.
The hook returns the canonical `{app_id, user_id, tenant_id, workspace_id,
permissions}` scope for the module request; the OSS runtime does not know about
any specific tenant product. The resolved scope is available to module policies
as `ctx.tenant_id`, `ctx.workspace_id`, and `ctx.permissions` for
resource-scoped checks inside handlers and services. The older
`module_permission_resolver` hook remains supported as a compatibility layer
after scope resolution.

`actions[].entitlement_gate` is optional. Set it to a `capability_id` string
when the action belongs to the generated app's own SaaS feature-gate model and
requires an active plan grant before executing. The `ModuleExecutor` checks
`EntitlementPort.check(capability_id, app_id=..., user_id=..., tenant_id=...,
workspace_id=...)` and returns `ENTITLEMENT_REQUIRED` on denial. Non-SaaS apps
use `NoOpEntitlementAdapter` and are entirely unaffected — no configuration
needed. Never set `entitlement_gate` on `admin_internal` actions.

```yaml
actions:
  - id: view_dashboard
    description: View the analytics dashboard.
    handler_method: view_dashboard
    api_surface: public_readonly
    entitlement_gate: analytics.dashboard   # requires active analytics plan grant
    permissions: [analytics.read]
    input_schema: { type: object }
    output_schema: { type: object, required: [data] }
```

For SaaS apps, `app/config/subscriptions.yaml` declares the plan catalog and,
when runtime enforcement needs persisted assignments, an `assignment_store`
data-contract alias. The platform wires the OSS `ConfiguredEntitlementAdapter`
at startup and the adapter checks active assignment records before dispatch.
When `assignment_store.workspace_id_field` is declared, assignment lookup is
workspace-aware: exact app/tenant/workspace/user records are checked before
broader tenant, workspace, user, or app-level fallback records. For non-SaaS
apps, no subscription config is needed.

This OSS primitive is not hosted-product licensing. Hosted products such as
Mozaiks App may decide whether an app/workspace can use a proprietary hosted
pack such as MozaiksPay, but that operator entitlement is enforced by the
hosted product and its APIs. Generated apps only use `entitlement_gate` for
their own end-user feature gates.

---

## `contracts/` Companion Manifests

### Module Event/Reaction Contract

1. `contracts/events.yaml` declares the events this module may emit.
  Event types must use a valid namespace owned by the emitting layer such as
  `domain.*`, `platform.*`, or `hosted.*`. For ordinary app modules, emitted
  events in `module.yaml.actions[].emits` must be declared here and are
  normally `domain.*`.
2. `contracts/reactions.yaml` is the canonical reaction contract.
  It uses `schema_version: mozaiks.reactions.v1`, root key `reactions`,
  `event_type` for the incoming event, and nested `target.kind` for routing.
3. Reaction targets use one of three canonical kinds:
  `handler` calls `target.handler_method` on this module's handler class;
  `capability` invokes `target.capability_id` (must be declared in the
  receiving module's `module.yaml capabilities[]` array); and `notification`
  links `target.notification_id` to a rule in `contracts/notifications.yaml`.
4. `contracts/notifications.yaml` declares notification rules derived from
  events. It is not a reaction file and should not be confused with
  `contracts/reactions.yaml`.
5. `contracts/subscriptions.yaml` is not supported. Runtime rejects it so
  modules have one reaction-routing source of truth.

#### Canonical Example

`app/modules/tasks/contracts/events.yaml`

```yaml
schema_version: mozaiks.events.v1
events:
  - type: domain.tasks.task.completed
    version: 1
    description: Emitted when a task is completed.
    producer: tasks
```

`app/modules/tasks/contracts/reactions.yaml`

```yaml
schema_version: mozaiks.reactions.v1
reactions:
  - id: update_project_progress
    event_type: domain.tasks.task.completed
    description: Update project progress when a task completes.
    target:
      kind: handler
      handler_method: update_project_progress
```

Add `update_project_progress` to `backend/handler.py` and delegate to
`service.py`.

### `contracts/events.yaml`

Declare events this module may publish. Use `domain.*` namespace for app modules.
Hosted product modules may use `hosted.*`.

```yaml
schema_version: mozaiks.events.v1
events:
  - type: domain.my_module.item_created
    version: 1
    description: Emitted when an item is created.
    producer: my_module
    payload_schema:
      type: object
      required: [item_id, owner_id]
      properties:
        item_id: { type: string }
        owner_id: { type: string }
```

### `contracts/reactions.yaml`

Declare reactions to events published by other modules. Each reaction routes an
event to a handler method on this module's handler class.

```yaml
schema_version: mozaiks.reactions.v1
reactions:
  - id: my_module.on_other_event
    event_type: domain.other_module.something_happened
    target:
      kind: handler
      handler_method: handle_something
```

Add the matching method to `handler.py` and delegate to service.

### `contracts/notifications.yaml`

Declare notification rules. Each entry maps an event to a set of recipients,
channels, and a message template.

```yaml
schema_version: mozaiks.notifications.v1
notifications:
  - id: my_module.item_created.admin
    event_type: domain.my_module.item_created
    channels: [in_app, email]
    audience:
      roles: [admin]
    template:
      title: "New item"
      body: "{{name}}"
```

### `contracts/settings.yaml`

Declare user or app-level configurable settings owned by this module.
Omit the file when the module has no settings.

```yaml
schema_version: mozaiks.settings.v1
settings: []
```

### `contracts/admin.yaml`

Declare admin panels this module contributes to the unified `/admin` shell.
Omit the file when the module has no admin panels.

```yaml
schema_version: mozaiks.admin.v2
panels: []
hooks: []
```

### `contracts/profile.yaml`

Declare panels this module contributes to the user profile page (`/profile`).
Only add this file when the module has user-scoped account data worth surfacing
there — for example, activity summaries, notification preferences, or usage
stats. Do not add it to every module.

Valid `kind` values: `metrics` (grid of labelled tiles), `list` (key/value
list), `component` (registered React component). `form` is reserved and not yet
implemented — the validator rejects it at load time.

Profile panels bind to module actions via `action:`. The platform calls that
action at `/api/me/profile-panels` request time and attaches the result as
`data`. Panel action failures return safe error metadata — they do not crash the
profile page.

Profile panels must not expose admin-only actions or secrets. They do not
replace or override `/api/me` identity.

```yaml
schema_version: mozaiks.profile.v1
panels:
  - id: activity-summary
    title: Activity Summary
    description: Account-level summary.
    order: 30          # 1–998; identity=0, preferences=999
    kind: metrics
    action: get_activity_summary
    fields:
      - { id: total, label: Total, type: number }
      - { id: status, label: Status, type: status }
```

See [profile-panel-contract.md](../foundations/profile-panel-contract.md) for
the full contract reference.

---

## `runtime_extensions.yaml`

Optional module-level host extension file.

Use `runtime_extensions.yaml` only when normal module actions are insufficient
and the module needs to mount a custom route (`api_router`) or start a
process-lifetime background service (`startup_service`) at host startup. It is
not a turn-level hook file.

```yaml
schema_version: mozaiks.runtime_extensions.v1
extensions:
  - kind: api_router
    entrypoint: backend.router:get_router
    prefix: /webhooks

  - kind: startup_service
    entrypoint: backend.worker:MyService
```

Two kinds:
- `api_router` — mounts a FastAPI `APIRouter` at host startup. Use for a
  module-local generic external webhook receiver or custom inbound callback route.
- `startup_service` — starts a background service for the process lifetime. Use
  for a module-local audit/event subscriber or polling worker.

Runtime extension rules:

- entrypoints must be module-local backend files such as
  `backend.webhooks:get_router` or `backend.audit_subscriber:AuditSubscriber`
- entrypoint files must also be declared in generated backend outputs or Python stubs
- do not use runtime extensions for generic business logic; put that in `service.py`
- do not use runtime extensions for persistence/query code; put that in `repo.py`
- do not use runtime extensions for auth, tenancy, or scope helpers; put that in `policy.py`
- do not use runtime extensions for transport or WebSocket infrastructure
- do not use runtime extensions for workflow orchestration

---

## Backend Layer Contract

Every module backend follows the same four-layer pattern.

### `handler.py` — Thin dispatch only

One method per declared action. No logic, no `ctx.db`, no `ctx.emit`.
Delegates everything to service.

```python
from __future__ import annotations
from typing import Any
from .service import MyModuleService


class MyModuleHandler:

    def __init__(self) -> None:
        self.service = MyModuleService()

    async def list_items(self, ctx, *, limit: int = 20) -> dict[str, Any]:
        return await self.service.list_items(ctx, limit=limit)

    async def create_item(self, ctx, *, name: str) -> dict[str, Any]:
        return await self.service.create_item(ctx, name=name)
```

### `service.py` — All business logic

Validates inputs, calls repo, emits events via `ctx.emit()` after commits.
Never accesses `ctx.db` directly.

```python
from __future__ import annotations
from typing import Any
from uuid import uuid4
from .schemas import MyModuleRecord, coerce_limit, timestamp_now
from .policy import owner_id_from_context, scoped_owner_query
from .repo import MyModuleRepo


class MyModuleService:

    def __init__(self, repo: MyModuleRepo | None = None) -> None:
        self.repo = repo or MyModuleRepo()

    async def list_items(self, ctx, *, limit: int = 20) -> dict[str, Any]:
        query = scoped_owner_query(ctx)
        items = await self.repo.list(ctx, query=query, limit=coerce_limit(limit))
        return {"items": items, "count": len(items)}

    async def create_item(self, ctx, *, name: str) -> dict[str, Any]:
        owner_id = owner_id_from_context(ctx)
        now = timestamp_now()
        record: MyModuleRecord = {
            "item_id": str(uuid4()),
            "owner_id": owner_id,
            "name": name.strip(),
            "status": "active",
            "created_at": now,
            "updated_at": now,
        }
        await self.repo.insert(ctx, record=record)
        await ctx.emit(
            "domain.my_module.item_created",
            {"item_id": record["item_id"], "owner_id": owner_id},
        )
        return {"success": True, "record": dict(record)}
```

### `repo.py` — Persistence access only

Pure data access. No business logic, no events, no validation.

Generated repo code uses `ctx.persistence.collection(module_id, entity_name)`
with module/entity values aligned to `data_contract` and staged
`data/contract.json`. It must not use `ctx.db`, call
`get_mongo_client()`, or hardcode database names.

```python
class ProjectsRepo:
    async def _collection(self, ctx):
        persistence = getattr(ctx, "persistence", None)
        if persistence is None:
            raise RuntimeError("Persistence is not available for this app context.")
        return persistence.collection("projects", "projects")

    async def list_projects(self, ctx, *, query=None, limit=50):
        collection = await self._collection(ctx)
        return await collection.find_many(query or {}, limit=limit)
```

The collection pair must match `data/contract.json`, for example
`module_id: projects` and `entity_name: projects`. Non-persistent modules should
not invent database logic.

### `policy.py` — Query scoping

Pure functions that build scoped MongoDB query dicts from `ctx`. No DB access.

### `schemas.py` — Typed shapes and pure helpers

TypedDicts for document shapes. Pure helper functions (timestamp, coerce). No I/O.

### Helper files — Declared module-local support only

Helper files are allowed when they keep the canonical layers clear. They must be
declared before generation, live under the module's `backend/` package, and have
a specific purpose that is imported by a canonical layer or referenced by
`runtime_extensions.yaml`.

Allowed generic examples:

- `backend.external_client.py` for a generic external provider client
- `backend.webhooks.py` for a runtime extension router
- `backend.audit_subscriber.py` for a startup service helper
- `backend.notification_client.py` for a notification delivery client
- `backend.domain_rules.py` for complex pure domain helpers that would bloat `service.py`

Prohibited helper files:

- generic business logic that belongs in `service.py`
- persistence/query code that belongs in `repo.py`
- auth, tenancy, or scope logic that belongs in `policy.py`
- DTOs, typed shapes, or pure serialization helpers that belong in `schemas.py`
- transport or WebSocket infrastructure
- workflow orchestration
- arbitrary file splitting

---

## Event Model

Modules emit domain events after committing state changes. The runtime routes these
events to workflow triggers and notification rules.

**Event namespace rules:**
- App modules use `domain.*` — e.g., `domain.orders.order_placed`
- Hosted product modules use `hosted.*` — e.g., `hosted.analytics.metric_recorded`
- Platform events use `platform.*` — owned by the runtime, not generated

Modules must declare every event they emit in `contracts/events.yaml`. The platform
validates that emitted events match declared types on startup.

**Event flow:**
```
service.py → ctx.emit(event_type, payload)
           → UnifiedEventDispatcher
           → ModuleEventRouter
         → reactions.yaml → target.kind handler/capability/notification
         → notifications.yaml → notification stored in platform_notifications
               → orchestrator.yaml triggers → workflow start/resume
```

Modules do not know which workflows they trigger. The trigger contract is owned by
the workflow's `orchestrator.yaml`.

---

## Capability Ownership Classification

Every module or feature capability belongs to one of five ownership classes.
This determines who generates it, who consumes it, and whether OSS apps may include it.

| Class | Owner | Generation | OSS apps |
|-------|-------|------------|----------|
| `host_universal` | Runtime/Platform | Never generate — always present | Yes, automatic |
| `framework_pack` | Mozaiks framework | Select from pack catalog — don't regenerate | Yes, opt-in |
| `managed_capability` | Mozaiks App (proprietary) | Not generated — licensed integration only | No |
| `generated_module` | App-specific | AppGenerator generates contracts + stubs | Yes, per app |
| `external_adapter` | External service | AppGenerator generates wiring + facade only | Adapter yes; engine no |

### `host_universal`

Built into the runtime or platform. Every app gets it automatically.

Examples: WebSocket transport, event dispatch, session management, AG2 orchestration,
admin shell, notification storage.

**Rule:** Never generate these. If an AppGenerator plan includes auth, websocket,
notifications infrastructure, or user management as a module to build — that plan is wrong.

### `framework_pack`

Optional reusable capability packs published by the Mozaiks framework. Apps select them
from the catalog; AppGenerator does not regenerate the pack internals.

Examples: `notifications` pack, `messaging` pack, `files` pack, `audit` pack.

**Rule:** Reference the pack; expand pack-specific app overlay only (app-specific wiring,
page composition, event flow declarations).

### `managed_capability`

Licensed capability packs that depend on private Mozaiks App hosted services.
OSS apps must not copy these.

Examples: `generic_managed_analytics`, `managed_search`.

**Rule:** Generate the integration facade and wiring for the app; the hosted service
engine lives in the private product repo.

Managed capability access is operator-managed. The generated app may receive a facade
module or thin service client for a managed capability, but it must not copy or enforce
the hosted product's pack-access entitlement rules. If the generated app is
itself a SaaS product, its end-user feature gates still use the OSS
`entitlement_gate` and `EntitlementPort` contract described above.

### `generated_module`

App-specific deterministic business logic. AppGenerator generates the full module contract
and backend stubs for each one.

Examples: `orders`, `inventory`, `profiles`, `campaigns`.

**Rule:** Generate `module.yaml`, `contracts/events.yaml`, `contracts/reactions.yaml`,
`contracts/notifications.yaml`, `backend/handler.py`, `backend/service.py`,
`backend/repo.py`, `backend/policy.py`, `backend/schemas.py`.

### `external_adapter`

A facade to an outside system. Generate the integration wiring (adapter, `runtime_extensions.yaml`
for webhook receivers, event bridge) — not the external system itself.

Examples: generic external webhook receiver, notification bridge, search index sync adapter.

**Rule:** The real system lives outside Mozaiks. Generate only the facade and event bridge.
Use `runtime_extensions.yaml api_router` for inbound webhooks.

---

## AppGenerator and Modules

AppGenerator generates `generated_module` class capabilities. For each module in the build plan:

1. Outputs `module.yaml` — identity, actions, permissions, event references
2. Outputs `contracts/events.yaml` — domain events the module publishes
3. Outputs `contracts/reactions.yaml` — reactions to events from other modules (if any)
4. Outputs `contracts/notifications.yaml` — notification rules (if any)
5. Outputs `contracts/profile.yaml` — user profile page panels, when the module has user-scoped account data worth surfacing (activity summaries, usage stats, notification preferences). Do not emit for every module.
6. Outputs `backend/handler.py`, `backend/service.py`, `backend/repo.py`,
   `backend/policy.py`, `backend/schemas.py` — backend stubs

AppGenerator does **not** generate:
- auth, user management, session infrastructure → `host_universal`
- notification delivery infrastructure, admin shell → `host_universal`
- the user profile page or `/api/me` identity — both are `host_universal`
- pack internals for framework packs → `framework_pack`
- hosted service engines or external systems → `managed_capability` / `external_adapter`
- `contracts/profile.yaml` with `kind: form` — `form` is reserved and rejected by the validator

---

## Runtime Alignment Status

The following runtime behaviors are not yet implemented. They are listed here so
engineering work is tracked against the canonical contract, not against the current
runtime state.

| Canonical contract | Runtime status |
|--------------------|----------------|
| `contracts/` subdirectory path | Fully wired — `ModuleLoader` loads from `contracts/` subdir |
| `contracts/reactions.yaml` | Canonical event reaction contract |
| All companion manifests optional | Fully wired — absent files yield `None`, not empty defaults |
| `settings.py` injected into ctx | Settings manifest data is injected into `ModuleContext.settings`; module-local Python settings hooks remain future work |
| `contracts/reactions.yaml` handler routing | Fully wired — `ModuleEventRouter` resolves handler, capability, and notification targets from canonical reactions |
| `notifications.py` audience hooks | Stored but not called by `ModuleEventRouter` |
| Module permissions enforcement | Fully wired in `ModuleExecutor` for external calls with concrete granted permissions |
| Auth-enabled HTTP module default | Fully wired — external HTTP calls require auth unless `actions[].api_surface` is `public` or `public_readonly` |
| Input/output schema validation | Fully wired in `ModuleExecutor`; input failures block dispatch and output failures warn |

Until the runtime is updated, generated modules should still follow the canonical
contract shape. The runtime loader will be updated to support the new paths.

---

## Cross References

- [module-authoring-patterns.md](module-authoring-patterns.md) — backend authoring patterns
- [appgenerator-capability-planning.md](appgenerator-capability-planning.md) — AppGenerator capability planning
- [../app/canonical-app-structure.md](../app/canonical-app-structure.md) — full app workspace layout
- [../app/app-bundle-declaratives.md](../app/app-bundle-declaratives.md) — declarative contract reference

