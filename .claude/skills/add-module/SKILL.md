---
name: add-module
description: Add a backend module (routes + models) to an existing Mozaiks app.
argument-hint: "[module name or description]"
---

Help the user add a backend **module** to an existing Mozaiks application.

A module is deterministic backend logic: CRUD routes, domain data, business rules.
It runs without AI. For AI-driven behavior, use a workflow instead.

---

## What a Module Is

```
platform/modules/<name>/
├── module.json       ← metadata (name, category, author, description)
├── handler.py        ← FastAPI router with HTTP endpoints
├── models.py         ← Pydantic request/response schemas
└── service.py        ← business logic (optional, recommended)
```

The runtime auto-discovers and mounts all modules at startup.

---

## Steps to Add a Module

### 1. Create the directory

```bash
mkdir platform/modules/<name>
```

### 2. Write `module.json`

```json
{
  "name": "<name>",
  "displayName": "<Human Name>",
  "category": "data",
  "author": "you",
  "description": "What this module does.",
  "version": "0.1.0"
}
```

### 3. Write `models.py`

```python
from pydantic import BaseModel
from typing import Optional, List

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
from fastapi import APIRouter, HTTPException
from .models import CreateItemRequest, ItemResponse

router = APIRouter(prefix="/api/modules/<name>", tags=["<name>"])

@router.get("/list", response_model=List[ItemResponse])
async def list_items():
    # Replace with real data source
    return []

@router.post("/create", response_model=ItemResponse)
async def create_item(request: CreateItemRequest):
    # Replace with real persistence
    return {"id": "new-id", **request.model_dump()}
```

### 5. Restart the backend

```bash
python run_server.py
```

Modules are loaded at startup. No registration step needed.

---

## Connecting a Module to a Page

Once the module exposes routes, reference them in an `AppPageSchema`:

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

The `SchemaPage` route fetches this YAML and `PageRenderer` wires the `api_endpoint`
to the `DataTable` automatically.

---

## Connecting a Module to a Workflow

Workflows call modules via the `AppBackendPort` adapter — no direct imports:

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
- Keep business logic in `service.py`, HTTP wiring in `handler.py`.
- Use Pydantic models for all request/response shapes.
- Module routes must be prefixed `/api/modules/<name>/` to avoid collisions.

---

## When to Use This Skill

- User wants to add CRUD endpoints for a new entity
- User says "add a customers module" or "I need an API for orders"
- User wants to wire a page to real data
- User needs backend logic that doesn't require AI
