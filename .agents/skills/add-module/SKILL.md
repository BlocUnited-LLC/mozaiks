---
name: add-module
description: Add a backend module (deterministic CRUD/action handler) to an existing Mozaiks app.
argument-hint: "[module name or description]"
---

**Before starting:** `git fetch origin && gh pr list --state open && git log origin/main --oneline -3` — if another agent has an open PR touching the same files you need, wait for it to merge or branch off it instead of main.

Help the user add a backend **module** to an existing Mozaiks application.

A module is deterministic backend logic: CRUD actions, domain data, business rules.
It runs without AI. For AI-driven behavior, use a workflow instead.
Modules support workflows — they provide the action surface that AI agents call.

---

## What a Module Is

```text
app/modules/{name}/
├── module.yaml              ← required: identity, permissions, actions; optional: capabilities[]
├── contracts/               ← optional companion manifests
│   ├── events.yaml          ← domain events this module may publish
│   ├── reactions.yaml       ← event reactions owned by this module
│   ├── notifications.yaml   ← notification rules derived from events
│   ├── settings.yaml        ← user/app settings schema
│   ├── admin.yaml           ← admin panels (omit if none)
│   └── profile.yaml         ← optional user profile page panels
├── runtime_extensions.yaml  ← optional: api_router / startup_service
└── backend/
    ├── __init__.py
    ├── handler.py        ← required — thin dispatch, one method per action
    ├── service.py        ← recommended — all business logic and event emission
    ├── repo.py           ← recommended — MongoDB access, no logic
    ├── policy.py         ← recommended — multi-tenancy query scoping
    ├── schemas.py        ← recommended — typed shapes + pure helpers
    └── {helper_files}.py ← optional — declared, justified, module-local support
```

Only `module.yaml` and `backend/handler.py` are required. Add companion manifests
under `contracts/` only when the module needs them.

Use `contracts/reactions.yaml` as the canonical event-reaction contract.
The runtime rejects `contracts/subscriptions.yaml`; module changes must author
`contracts/reactions.yaml`.

For SaaS apps: set `actions[].entitlement_gate` to a `capability_id` string on
user-facing actions that require an active plan grant. `ModuleExecutor` checks
`EntitlementPort` before dispatch and returns `ENTITLEMENT_REQUIRED` on denial.
Non-SaaS apps use `NoOpEntitlementAdapter` — no configuration needed. Never set
`entitlement_gate` on `admin_internal` actions.

Event/reaction contract summary:

- `contracts/events.yaml` declares the events this module may emit.
  `module.yaml.actions[].emits` must reference event types declared there.
- `contracts/reactions.yaml` uses `schema_version: mozaiks.reactions.v1`, root
  key `reactions`, `event_type`, and nested `target.kind`.
- Reaction targets use `target.handler_method`, `target.capability_id`, or
  `target.notification_id` depending on `target.kind`.
- `contracts/notifications.yaml` declares notification rules derived from
  events and is separate from reaction routing.

The runtime auto-discovers and registers all modules at startup.
Module routes are auto-mounted at `/api/modules/{name}/{action_id}`.
Pages should call those routes without query strings. Put list limits in
`page_size` and filters or selected-row values in action payloads, form state,
or the module action input schema.

Modules that need persistent app chrome access should expose a real page route
and put navigation intent on that page. For example, a communications module
that owns `/messages` should give the Messages page `navigation.scope: global`
when it is a primary destination, or `navigation.scope: profile` when it is
account-adjacent. Use `app/config/shell.json -> shortcuts` for built-in profile,
auth, notification, and footer chrome rather than hardcoding menu entries.
The page also owns chrome intent through `shell_mode`: use `conversation` for
DM/chat/thread pages so the mobile bottom bar and footer do not compete with the
composer, and `workspace` for inbox, queue, profile, or management surfaces.

Module runtime output must be production-honest. Do not return sample, demo,
mock, fake, placeholder, random, or hardcoded KPI data from module actions.
Summary/stat/metric/count actions must query repo/MongoDB state or return honest
empty values (`0`, `[]`, `null`). Trend/change fields require a real historical
comparison or metrics snapshot; otherwise omit them or return `null`.

---

## Steps to Add a Module

### 1. Write `module.yaml`

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

### 2. Write `contracts/events.yaml`

Only needed when this module publishes domain events.

```yaml
schema_version: mozaiks.events.v1
events:
  - type: domain.{name}.record_created
    version: 1
    description: Emitted when a {name} record is created.
    producer: {name}
    payload_schema:
      type: object
      required: [record_id, owner_id]
```

### 3. Write `contracts/reactions.yaml`

Only needed when this module reacts to events from other modules.

```yaml
schema_version: mozaiks.reactions.v1
reactions: []
# Add entries when this module reacts to events from other modules.
# Each reaction routes an event to a handler method on this module's handler class.
#
# Example:
#   - id: {name}.on_other_event
#     event_type: domain.other_module.something_happened
#     target:
#       kind: handler
#       handler_method: handle_something
```

### 4. Write `contracts/notifications.yaml`

Only needed when this module sends notifications on its events.

```yaml
schema_version: mozaiks.notifications.v1
notifications:
  - id: {name}.record_created.admin
    event_type: domain.{name}.record_created
    channels: [in_app, email]
    audience:
      roles: [admin]
    template:
      title: "New {name}"
      body: "{payload.name}"
```

### 5. Write `contracts/settings.yaml`

Only needed when this module exposes user/app configurable settings.

```yaml
schema_version: mozaiks.settings.v1
settings: []
```

### 6. Write `contracts/admin.yaml`

Only needed when this module contributes panels to the unified admin shell.

```yaml
schema_version: mozaiks.admin.v2
panels:
  - id: {name}.overview
    label: {Display Name}
    section: overview
    renderer: schema
    layout: full-width
    sections:
      - id: {name}-table
        primitive: DataTable
        config:
          api_endpoint: /api/modules/{name}/list_{name}s
          columns:
            - { key: name, label: Name }
hooks: []
```

Use one of these section names: `overview`, `users`, `billing`, `usage`,
`activity`, `operations`, `settings`, `integrations`, or `support`.

### 7. Write `contracts/profile.yaml`

Only needed when this module contributes panels to the user profile page.
Use this when the module has user-scoped data worth showing on the account/profile
surface — for example, account activity summaries, notification preference
sections, or usage stats. Do not add profile.yaml to every module.

Profile panels must bind to module actions declared in `module.yaml`.
Do not expose admin-only actions or secrets. Do not use `kind: form` (reserved,
not yet implemented). Profile panels do not replace or override `/api/me` identity.

```yaml
schema_version: mozaiks.profile.v1
panels:
  - id: {name}-summary
    title: {Display Name} Summary
    description: Account-level summary for {Display Name}.
    order: 50                   # 1–998; identity=0, preferences=999
    kind: metrics               # metrics | list | component
    action: get_{name}_summary  # module action that hydrates the panel
    fields:
      - { id: total, label: Total, type: number }
      - { id: status, label: Status, type: status }
```

Supported `kind` values:

| Kind | When to use |
|------|-------------|
| `metrics` | Grid of KPI/metric tiles from `fields` |
| `list` | Key/value list from `fields` |
| `component` | App-registered React component (use `component:` instead of `fields:`) |

Supported `type` values for `fields`: `string`, `number`, `currency`, `date`, `boolean`, `status`.

The platform calls the declared `action` at `/api/me/profile-panels` request time
and attaches the result as `data` on the panel. Panel action failures are returned
as safe error metadata — they do not crash the profile page.

### 8. Write `runtime_extensions.yaml`

Only needed when the module must extend the host lifecycle with a raw webhook
router or a process-lifetime background service. Entrypoints are module-local;
do not use `modules.*`, `app.modules.*`, or `mozaiksai.*` import paths.
Use this only when normal module actions are insufficient.

```yaml
schema_version: mozaiks.runtime_extensions.v1
extensions:
  - kind: api_router
    entrypoint: backend.router:get_router
    prefix: /webhooks/{name}
  - kind: startup_service
    entrypoint: backend.worker:{Name}Worker
```

Most modules should omit this file.

Rules:

- `api_router` is for a module-local generic external webhook receiver or callback route.
- `startup_service` is for a module-local audit/event subscriber or polling worker.
- The entrypoint file must be declared in generated backend outputs or Python stubs.
- Do not use runtime extensions for generic business logic, persistence, auth/scope logic, transport infrastructure, or workflow orchestration.

### 8a. Declare helper files only when justified

Helper files are allowed only when they are:

- explicitly declared before generation
- module-local under `backend/`
- justified by a specific purpose
- imported by canonical layers or referenced by `runtime_extensions.yaml`

Allowed generic examples:

- external provider client
- webhook receiver helper
- startup service helper
- audit event subscriber
- notification delivery client
- complex pure domain helper that would bloat `service.py`

Do not create helper files for business logic that belongs in `service.py`,
persistence that belongs in `repo.py`, auth/scope logic that belongs in
`policy.py`, DTOs that belong in `schemas.py`, transport infrastructure,
workflow orchestration, or random file splitting.

### 9. Write `backend/schemas.py`

TypedDicts for MongoDB document shapes. Pure helpers. No I/O.

```python
from __future__ import annotations
from datetime import UTC, datetime
from typing import Any, TypedDict


class {Name}Record(TypedDict):
    record_id: str
    owner_id: str
    name: str
    status: str
    created_at: str
    updated_at: str


def timestamp_now() -> str:
    return datetime.now(UTC).isoformat()


def coerce_limit(value: Any, default: int = 20, maximum: int = 100) -> int:
    try:
        return max(1, min(int(value), maximum))
    except Exception:
        return default
```

### 10. Write `backend/policy.py`

Pure functions that turn `ctx` into scoped MongoDB queries. No DB access.

```python
from __future__ import annotations
from typing import Any


def owner_id_from_context(ctx, user_id: str | None = None) -> str:
    return user_id or getattr(ctx, "user_id", None) or ""


def scoped_owner_query(ctx) -> dict[str, Any]:
    owner_id = owner_id_from_context(ctx)
    return {"owner_id": owner_id} if owner_id else {}


def scoped_record_query(ctx, *, record_id: str) -> dict[str, Any]:
    query: dict[str, Any] = {"record_id": record_id}
    owner_id = owner_id_from_context(ctx)
    if owner_id:
        query["owner_id"] = owner_id
    return query
```

### 11. Write `backend/repo.py`

MongoDB access only. No business logic, no event emission, no validation.

```python
from __future__ import annotations
from typing import Any


class {Name}Repo:

    async def _collection(self, ctx):
        persistence = getattr(ctx, "persistence", None)
        if persistence is None:
            raise RuntimeError("Persistence is not available for this app context.")
        return persistence.collection("{name}", "{name}")

    async def get(self, ctx, *, query: dict[str, Any]) -> dict[str, Any] | None:
        col = await self._collection(ctx)
        return await col.find_one(query)

    async def insert(self, ctx, *, record: dict[str, Any]) -> None:
        col = await self._collection(ctx)
        await col.insert_one({**record})

    async def update(self, ctx, *, query: dict[str, Any], update: dict[str, Any]) -> int:
        col = await self._collection(ctx)
        result = await col.update_one(query, {"$set": update})
        return int(result.matched_count)

    async def list(self, ctx, *, query: dict[str, Any], limit: int) -> list[dict[str, Any]]:
        col = await self._collection(ctx)
        return await col.find_many(query, limit=limit, sort=[("created_at", -1)])

    async def count(self, ctx, *, query: dict[str, Any]) -> int:
        col = await self._collection(ctx)
        return await col.count(query)
```

`repo.py` must use `ctx.persistence.collection(module_id, entity_name)` with
module/entity values that match `app/data/contract.json`. Do not use
`ctx.db`, do not call `get_mongo_client()`, and do not hardcode database names.

### 12. Write `backend/service.py`

All business logic. Validates inputs, calls repo, emits events. Never touches DB directly.

```python
from __future__ import annotations
from typing import Any
from uuid import uuid4

from .schemas import {Name}Record, coerce_limit, timestamp_now
from .policy import owner_id_from_context, scoped_owner_query, scoped_record_query
from .repo import {Name}Repo


class {Name}Service:

    def __init__(self, repo: {Name}Repo | None = None) -> None:
        self.repo = repo or {Name}Repo()

    async def list_{name}s(self, ctx, *, limit: int = 20) -> dict[str, Any]:
        query = scoped_owner_query(ctx)
        items = await self.repo.list(ctx, query=query, limit=coerce_limit(limit))
        return {"items": items, "count": len(items)}

    async def create_{name}(self, ctx, *, name: str) -> dict[str, Any]:
        owner_id = owner_id_from_context(ctx)
        now = timestamp_now()
        record: {Name}Record = {
            "record_id": str(uuid4()),
            "owner_id": owner_id,
            "name": name.strip(),
            "status": "active",
            "created_at": now,
            "updated_at": now,
        }
        await self.repo.insert(ctx, record=record)
        await ctx.emit(
            "domain.{name}.record_created",
            {"record_id": record["record_id"], "owner_id": owner_id, "name": name},
        )
        return {"success": True, "record": dict(record)}
```

### 13. Write `backend/handler.py`

Thin dispatch only. One method per action. Delegates everything to service.

```python
from __future__ import annotations
from typing import Any

from .service import {Name}Service


class {Name}Handler:

    def __init__(self) -> None:
        self.service = {Name}Service()

    async def list_{name}s(self, ctx, *, limit: int = 20) -> dict[str, Any]:
        return await self.service.list_{name}s(ctx, limit=limit)

    async def create_{name}(self, ctx, *, name: str) -> dict[str, Any]:
        return await self.service.create_{name}(ctx, name=name)
```

### 14. Write `backend/__init__.py`

Empty file — makes `backend/` a Python package.

### 15. Restart the backend

```bash
mozaiks serve .
```

Modules are loaded at startup. No registration step needed.

---

## Layer Rules — Enforce These

| Layer | Allowed | Not allowed |
|-------|---------|-------------|
| `handler.py` | Receive ctx + kwargs, call service, return result | ctx.db, ctx.emit, business logic, conditionals |
| `service.py` | Validate, call repo, call ctx.emit after commit | ctx.db direct access, HTTP calls |
| `repo.py` | MongoDB queries, cursor iteration | Business logic, event emission, validation |
| `policy.py` | Build query dicts from ctx | DB access, side effects |
| `schemas.py` | TypedDicts, timestamp_now, coerce_limit | I/O, imports from service/repo |
| helper files | Declared external clients, runtime extension entrypoints, complex pure helpers | Undeclared logic, persistence, policy, DTOs, transport infrastructure, workflow orchestration |

---

## Event Reactions (module-to-module)

When this module needs to react to an event from another module without starting
a workflow, declare it in `contracts/reactions.yaml` and add the handler method:

```yaml
# contracts/reactions.yaml
schema_version: mozaiks.reactions.v1
reactions:
  - id: {name}.on_other_event
    event_type: domain.other_module.something_happened
    target:
      kind: handler
      handler_method: handle_something
```

Add `handle_something` as a method on `{Name}Handler` (delegate to service):

```python
async def handle_something(self, ctx, *, field_from_event: str) -> dict[str, Any]:
    return await self.service.handle_something(ctx, field_from_event=field_from_event)
```

The event payload fields are unpacked as keyword arguments.

---

## Connecting a Module to a Page

```yaml
# app/ui/pages/items.yaml
name: items
title: Items
layout: full-width
shell_mode: workspace
sections:
  - id: items_table
    title: All Items
    primitive: DataTable
    config:
      columns:
        - { key: name, label: Name }
      api_endpoint: /api/modules/{name}/list_{name}s
```

---

## Connecting a Module to a Workflow

```python
# In a workflow tool
from mozaiksai.core.workflow.app_backend_tools import backend_request

result = await backend_request(
    method="POST",
    path="/api/modules/{name}/create_{name}",
    body={"name": "example"},
    context_variables=context_variables,
)
```

---

## When to Use This Skill

- User wants to add CRUD endpoints for a new entity
- User says "add a customers module" or "I need an API for orders"
- User wants to wire a page to real data
- User needs backend logic that doesn't require AI

