---
name: add-module
description: Add a backend module (deterministic CRUD/action handler) to an existing Mozaiks app.
argument-hint: "[module name or description]"
---

Help the user add a backend **module** to an existing Mozaiks application.

A module is deterministic backend logic: CRUD actions, domain data, business rules.
It runs without AI. For AI-driven behavior, use a workflow instead.
Modules support workflows — they provide the action surface that AI agents call.

---

## What a Module Is

```
app/modules/{name}/
├── module.yaml           ← actions, permissions, capabilities, emits, type
├── events.yaml           ← domain events this module may publish
├── subscriptions.yaml    ← event reactions owned by this module
├── notifications.yaml    ← notification rules derived from events
├── settings.yaml         ← user/app settings schema (empty list if none)
├── admin.yaml            ← admin panels (omit file if none)
│
│   # type: messaging only
├── channels.yaml         ← transport/delivery channel definitions (WebSocket, push)
│
│   # type: workflow only
├── states.yaml           ← named states + initial state
├── transitions.yaml      ← valid transitions, required roles, emitted events
│
└── backend/
    ├── __init__.py
    ├── handler.py        ← required — thin dispatch, one method per action
    ├── service.py        ← recommended — all business logic and event emission
    ├── repo.py           ← recommended — MongoDB access, no logic
    ├── policy.py         ← recommended — multi-tenancy query scoping
    └── models.py         ← recommended — typed shapes + pure helpers
```

The `type` field in `module.yaml` selects the scaffold pattern:
`standard` (default), `messaging`, `workflow`, or `transactional`.
Type-specific YAML files and backend conventions are documented in
`docs/architecture/foundations/module-type-taxonomy.md`.

The runtime auto-discovers and registers all modules at startup.
Module routes are auto-mounted at `/api/modules/{name}/{action_id}`.

---

## Steps to Add a Module

### 1. Write `module.yaml`

```yaml
schema_version: mozaiks.module.v1
module:
  id: {name}
  display_name: {Display Name}
  version: 1.0.0
  type: standard   # standard | messaging | workflow | transactional
  description: What this module does.
  owner: mozaiks
  visibility: hosted
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
    emits: [hosted.{name}.record.created]
```

### 2. Write `events.yaml`

```yaml
schema_version: mozaiks.events.v1
events:
  - type: hosted.{name}.record.created
    version: 1
    description: Emitted when a {name} record is created.
    producer: {name}
    payload_schema:
      type: object
      required: [record_id, owner_id]
```

### 3. Write `subscriptions.yaml`

```yaml
subscriptions: []
# Add entries when this module reacts to events from other modules.
# Three target kinds:
#   notification  → create a notification intent from notifications.yaml
#   capability    → trigger a workflow via orchestrator.yaml capability_id
#   handler       → route event payload to a handler method (module-to-module)
#
# Example handler target:
#   - id: {name}.on_something
#     event: hosted.other_module.something_happened
#     handler: hosted.{name}.handle_something
```

### 4. Write `notifications.yaml`

```yaml
schema_version: mozaiks.notifications.v1
notifications:
  - id: {name}.record_created.admin
    event: hosted.{name}.record.created
    channels: [in_app, email]
    recipients: [admin]
    template:
      subject: "New {name}: {{name}}"
      body: >
        A new {name} record has been created by {{owner_id}}.
```

### 5. Write `settings.yaml`

```yaml
schema_version: mozaiks.settings.v1
settings: []
```

### 6. Write `backend/models.py`

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

### 7. Write `backend/policy.py`

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

### 8. Write `backend/repo.py`

MongoDB access only. No business logic, no event emission, no validation.

```python
from __future__ import annotations
from typing import Any

COLLECTION = "hosted_{name}_records"


class {Name}Repo:

    async def _collection(self, ctx):
        db = getattr(ctx, "db", None)
        if db is not None:
            return db[COLLECTION]
        from mozaiksai.core.core_config import get_mongo_client
        return get_mongo_client()["mozaiks"][COLLECTION]

    async def get(self, ctx, *, query: dict[str, Any]) -> dict[str, Any] | None:
        col = await self._collection(ctx)
        return await col.find_one(query, {"_id": 0})

    async def insert(self, ctx, *, record: dict[str, Any]) -> None:
        col = await self._collection(ctx)
        await col.insert_one({**record})

    async def update(self, ctx, *, query: dict[str, Any], update: dict[str, Any]) -> int:
        col = await self._collection(ctx)
        result = await col.update_one(query, {"$set": update})
        return int(result.matched_count)

    async def list(self, ctx, *, query: dict[str, Any], limit: int) -> list[dict[str, Any]]:
        col = await self._collection(ctx)
        cursor = col.find(query, {"_id": 0}).sort("created_at", -1).limit(limit)
        return await cursor.to_list(length=limit)

    async def count(self, ctx, *, query: dict[str, Any]) -> int:
        col = await self._collection(ctx)
        return int(await col.count_documents(query))
```

### 9. Write `backend/service.py`

All business logic. Validates inputs, calls repo, emits events. Never touches DB directly.

```python
from __future__ import annotations
from typing import Any
from uuid import uuid4

from .models import {Name}Record, coerce_limit, timestamp_now
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
            "hosted.{name}.record.created",
            {"record_id": record["record_id"], "owner_id": owner_id, "name": name},
        )
        return {"success": True, "record": dict(record)}
```

### 10. Write `backend/handler.py`

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

### 11. Write `backend/__init__.py`

Empty file — makes `backend/` a Python package.

### 12. Restart the backend

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
| `models.py` | TypedDicts, timestamp_now, coerce_limit | I/O, imports from service/repo |

---

## Subscription Handler Target (module-to-module)

When this module needs to react to an event from another module without starting
a workflow, use the `handler` target in `subscriptions.yaml`:

```yaml
subscriptions:
  - id: {name}.on_other_event
    event: hosted.other_module.something_happened
    handler: hosted.{name}.handle_something
```

Add `handle_something` as a method on `{Name}Handler` (and delegate to service):

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
