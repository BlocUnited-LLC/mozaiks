---
name: add-operation
description: Add a backend operation (deterministic CRUD/action handler) to an existing Mozaiks app.
argument-hint: "[operation name or description]"
---

Help the user add a backend **operation** to an existing Mozaiks application.

An operation is deterministic backend logic: CRUD actions, domain data, business rules.
It runs without AI. For AI-driven behavior, use a workflow instead.
Operations support workflows — they provide the action surface that AI agents call.

---

## What an Operation Is

```
platform/operations/<name>/
├── operation.yaml    ← metadata (name, version, actions, events)
├── handler.py        ← handler class with action methods
├── models.py         ← Pydantic request/response schemas (optional)
└── service.py        ← business logic (optional, recommended for complex cases)
```

The runtime auto-discovers and registers all operations at startup.

---

## Steps to Add an Operation

### 1. Create the directory

```bash
mkdir platform/operations/<name>
```

### 2. Write `operation.yaml`

```yaml
name: <name>
version: "1.0"
description: What this operation does.

actions:
  - name: list
    type: query
    description: List all items
  - name: create
    type: mutation
    description: Create an item
    emits:
      - <name>.created

events:
  - <name>.created
  - <name>.updated
  - <name>.deleted
```

### 3. Write `models.py`

```python
from pydantic import BaseModel
from typing import Optional

class CreateItemRequest(BaseModel):
    name: str
    description: Optional[str] = None

class ItemResponse(BaseModel):
    id: str
    name: str
    description: Optional[str] = None
```

### 4. Write `handler.py`

```python
from .models import CreateItemRequest, ItemResponse

class <Name>Operation:
    async def list(self, ctx, *, limit: int = 20) -> list:
        # Replace with real data source
        return []

    async def create(self, ctx, *, name: str, description: str = None) -> dict:
        result = {"id": "new-id", "name": name, "description": description}
        await ctx.emit("<name>.created", result)
        return result
```

### 5. Restart the backend

```bash
python run_server.py
```

Operations are loaded at startup. No registration step needed.

---

## Connecting an Operation to a Page

Once the operation is loaded, reference it in an `AppPageSchema`:

```yaml
# platform/pages/items.yaml
name: items
title: Items
layout: full-width
sections:
  - id: items_table
    title: All Items
    primitive: DataTable
    config:
      columns:
        - { key: name,        label: Name }
        - { key: description, label: Description }
    api_endpoint: /api/operations/<name>/list
```

---

## Connecting an Operation to a Workflow

Workflows call operations via the `AppBackendPort` adapter:

```python
# In a workflow tool
from mozaiksai.core.workflow.app_backend_tools import backend_request

result = await backend_request(
    method="POST",
    path="/api/operations/<name>/create",
    body={"name": "example"},
    context_variables=context_variables,
)
```

---

## Rules

- Operations are **dumb**: data in, data out. No AI calls inside an operation.
- Keep business logic in `service.py`, action wiring in `handler.py`.
- Use Pydantic models for all request/response shapes.
- Operation routes are auto-mounted at `/api/operations/<name>/<action>`.

---

## When to Use This Skill

- User wants to add CRUD endpoints for a new entity
- User says "add a customers operation" or "I need an API for orders"
- User wants to wire a page to real data
- User needs backend logic that doesn't require AI
