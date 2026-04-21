---
name: add-capability
description: Add a backend capability (routes + models) to an existing Mozaiks app.
argument-hint: "[capability name or description]"
---

Help the user add a backend **capability** to an existing Mozaiks application.

A capability is deterministic backend logic: CRUD routes, domain data, business rules.
It runs without AI. For AI-driven behavior, use a workflow instead.

---

## What a Capability Is

```
platform/capabilities/<name>/
├── capability.yaml   ← metadata (name, version, actions, events)
├── handler.py        ← FastAPI router with HTTP endpoints
├── models.py         ← Pydantic request/response schemas
└── service.py        ← business logic (optional, recommended)
```

The runtime auto-discovers and mounts all capabilities at startup.

---

## Steps to Add a Capability

### 1. Create the directory

```bash
mkdir platform/capabilities/<name>
```

### 2. Write `capability.yaml`

```yaml
name: <name>
version: "1.0"
description: What this capability does.

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

router = APIRouter(prefix="/api/capabilities/<name>", tags=["<name>"])

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

Capabilities are loaded at startup. No registration step needed.

---

## Connecting a Capability to a Page

Once the capability exposes routes, reference them in an `AppPageSchema`:

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
    api_endpoint: /api/capabilities/<name>/list
```

The `SchemaPage` route fetches this YAML and `PageRenderer` wires the `api_endpoint`
to the `DataTable` automatically.

---

## Connecting a Capability to a Workflow

Workflows call capabilities via the `AppBackendPort` adapter — no direct imports:

```python
# In a workflow tool
from mozaiksai.core.workflow.app_backend_tools import backend_request

result = await backend_request(
    method="POST",
    path="/api/capabilities/<name>/create",
    body={"name": "example"},
    context_variables=context_variables,
)
```

---

## Rules

- Capabilities are **dumb**: data in, data out. No AI calls inside a capability.
- Keep business logic in `service.py`, HTTP wiring in `handler.py`.
- Use Pydantic models for all request/response shapes.
- Capability routes must be prefixed `/api/capabilities/<name>/` to avoid collisions.

---

## When to Use This Skill

- User wants to add CRUD endpoints for a new entity
- User says "add a customers capability" or "I need an API for orders"
- User wants to wire a page to real data
- User needs backend logic that doesn't require AI
