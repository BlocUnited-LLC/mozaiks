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
│   ├── reactions.yaml       # Event reactions (subscriptions) owned by this module
│   ├── notifications.yaml   # Notification rules per event
│   ├── settings.yaml        # User/app settings schema
│   ├── admin.yaml           # Admin panels mounted into /admin/*
│   └── entitlements.yaml    # Capability entitlements (optional)
├── runtime_extensions.yaml  # Optional: api_router / startup_service host hooks
└── backend/
    ├── __init__.py
    ├── handler.py           # Required: thin dispatch, one method per declared action
    ├── service.py           # Recommended: all business logic and event emission
    ├── repo.py              # Recommended: MongoDB access layer, no logic
    ├── policy.py            # Recommended: query scoping for multi-tenancy
    ├── schemas.py           # Recommended: typed request/response + document shapes
    ├── settings.py          # Optional: settings hooks
    └── admin.py             # Optional: admin panel data hooks
```

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
```

---

## `contracts/` Companion Manifests

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
reactions:
  - id: my_module.on_other_event
    event: domain.other_module.something_happened
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
    event: domain.my_module.item_created
    channels: [in_app, email]
    recipients: [admin]
    template:
      subject: "New item: {{name}}"
      body: "An item was created by {{owner_id}}."
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
schema_version: mozaiks.admin.v1
admin_panels: []
```

---

## `runtime_extensions.yaml`

Host-level capabilities registered at server startup — not turn-level hooks.
Use only when the module needs to mount custom routes (e.g., webhook receivers)
or a persistent background service.

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
- `api_router` — mounts a FastAPI `APIRouter` at host startup. Required when the module
  needs unauthenticated or custom-path routes (e.g. Stripe or Slack webhooks).
- `startup_service` — starts a background service for the process lifetime. Required for
  persistent external connections (WebSocket feeds, polling workers).

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

### `repo.py` — MongoDB access only

Pure data access. No business logic, no events, no validation.

### `policy.py` — Query scoping

Pure functions that build scoped MongoDB query dicts from `ctx`. No DB access.

### `schemas.py` — Typed shapes and pure helpers

TypedDicts for document shapes. Pure helper functions (timestamp, coerce). No I/O.

---

## Event Model

Modules emit domain events after committing state changes. The runtime routes these
events to workflow triggers and notification rules.

**Event namespace rules:**
- App modules use `domain.*` — e.g., `domain.orders.order_placed`
- Hosted product modules use `hosted.*` — e.g., `hosted.billing.payment_succeeded`
- Platform events use `platform.*` — owned by the runtime, not generated

Modules must declare every event they emit in `contracts/events.yaml`. The platform
validates that emitted events match declared types on startup.

**Event flow:**
```
service.py → ctx.emit(event_type, payload)
           → UnifiedEventDispatcher
           → ModuleEventRouter
               → reactions.yaml → handler_method on target module
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
| `hosted_pack` | Mozaiks App (proprietary) | Not generated — licensed integration only | No |
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

### `hosted_pack`

Licensed capability packs that depend on private Mozaiks App hosted services.
OSS apps must not copy these.

Examples: `payments_integration`, `investor_distribution_integration`.

**Rule:** Generate the integration facade and wiring for the app; the hosted service
engine lives in the private product repo.

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

Examples: Stripe webhook receiver, Slack notification bridge, C# settlement service adapter.

**Rule:** The real system lives outside Mozaiks. Generate only the facade and event bridge.
Use `runtime_extensions.yaml api_router` for inbound webhooks.

---

## AppGenerator and Modules

AppGenerator generates `generated_module` class capabilities. For each module in the build plan:

1. Outputs `module.yaml` — identity, actions, permissions, event references
2. Outputs `contracts/events.yaml` — domain events the module publishes
3. Outputs `contracts/reactions.yaml` — reactions to events from other modules (if any)
4. Outputs `contracts/notifications.yaml` — notification rules (if any)
5. Outputs `backend/handler.py`, `backend/service.py`, `backend/repo.py`,
   `backend/policy.py`, `backend/schemas.py` — backend stubs

AppGenerator does **not** generate:
- auth, user management, session infrastructure → `host_universal`
- notification delivery infrastructure, admin shell → `host_universal`
- pack internals for framework packs → `framework_pack`
- MozaiksPay, settlement, payout engine → `hosted_pack` / `external_adapter`

---

## Runtime Alignment Status

The following runtime behaviors are not yet implemented. They are listed here so
engineering work is tracked against the canonical contract, not against the current
runtime state.

| Canonical contract | Runtime status |
|--------------------|----------------|
| `contracts/` subdirectory path | Fully wired — `ModuleLoader` loads from `contracts/` subdir |
| `subscriptions.yaml` (was reactions) | Fully wired — canonical filename in loader and router |
| All companion manifests optional | Fully wired — absent files yield `None`, not empty defaults |
| `settings.py` injected into ctx | Not yet injected; add `ctx.settings` |
| `subscriptions.yaml` handler routing | Fully wired — handler, capability, and notification targets all dispatch |
| `notifications.py` audience hooks | Stored but not called by `ModuleEventRouter` |
| Module permissions enforcement | Declared but not enforced by `ModuleExecutor` |
| Input/output schema validation | Declared but not validated by `ModuleExecutor` |

Until the runtime is updated, generated modules should still follow the canonical
contract shape. The runtime loader will be updated to support the new paths.

---

## Cross References

- [module-type-taxonomy.md](module-type-taxonomy.md) — backend conventions by module type
- [capability-pack-model.md](capability-pack-model.md) — reusable capability packs
- [canonical-app-structure.md](canonical-app-structure.md) — full app workspace layout
- [app-bundle-declaratives.md](app-bundle-declaratives.md) — declarative contract reference
