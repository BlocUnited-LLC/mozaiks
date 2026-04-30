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
platform/modules/<name>/
├── module.yaml    ← metadata (name, version, actions, events)
├── handler.py     ← handler class with action methods
├── models.py      ← Pydantic request/response schemas (optional)
└── service.py     ← business logic (optional, recommended for complex cases)
```

The runtime auto-discovers and registers all modules at startup.

---

## Steps to Add a Module

### 1. Create the directory

```bash
mkdir platform/modules/<name>
```

### 2. Write `module.yaml`

```yaml
name: <name>
version: "1.0"
description: What this module does.

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
class <Name>Module:
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
mozaiks serve .
```

Modules are loaded at startup. No registration step needed.

---

## Connecting a Module to a Page

Once the module is loaded, reference it in an `AppPageSchema`:

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
    api_endpoint: /api/modules/<name>/list
```

---

## Connecting a Module to a Workflow

Workflows call modules via the AppBackendPort adapter:

```python
# In a workflow tool
from mozaiksai.core.workflow.app_backend_tools import backend_request

result = await backend_request(
    method="POST",
    path="/api/modules/<name>/create",
    body={"name": "example"},
    context_variables=context_variables,
)
```

---

## Rules

- Modules are **dumb**: data in, data out. No AI calls inside a module.
- Keep business logic in `service.py`, action wiring in `handler.py`.
- Use Pydantic models for all request/response shapes.
- Module routes are auto-mounted at `/api/modules/<name>/<action>`.

---

## When to Use This Skill

- User wants to add CRUD endpoints for a new entity
- User says "add a customers module" or "I need an API for orders"
- User wants to wire a page to real data
- User needs backend logic that doesn't require AI
