# App-Backend Admin Contract

This document defines the optional contract for a connected app backend that
supplies app-business panels into the unified Mozaiks `/admin` shell.

This is not the host admin shell contract and not the module `admin.yaml`
contract. It is the app-backend extension lane for app-specific business admin
surfaces.

## Rule

- The visible shell remains framework-owned at `/admin`.
- The app backend may contribute business panels only through
  `GET {app_backend_url}/api/admin/config`.
- The response must use one canonical shape.
- Do not invent alternate panel arrays, nested `panels.app/modules/runtime`
  groups, or implicit built-in panel ids.

Recommended generated file ownership for split backends:

- `app/services/admin_config.py` — returns the canonical `mozaiks.admin.app_backend.v1` payload as a plain dict (validated at codegen time by AppGenerator)
- `app/services/routes/admin.py` — exposes `GET /api/admin/config` as a self-contained FastAPI `APIRouter`

For AppGenerator, the typed source of truth is `ControllerOutput.app_backend_admin_config`.
The codegen step validates the config at generation time and materialises these two files
from the typed object — no runtime Mozaiks import is required in the generated code.

Generated example:

```python
# app/services/admin_config.py
_ADMIN_CONFIG = {
    "schema_version": "mozaiks.admin.app_backend.v1",
    "panels": [
        {
            "id": "app.users",
            "label": "Users",
            "section": "users",
            "order": 10,
            "renderer": "builtin",
            "builtin_panel": "users",
        }
    ],
}


def get_admin_config():
    return _ADMIN_CONFIG
```

```python
# app/services/routes/admin.py
from fastapi import APIRouter, HTTPException
from inspect import isawaitable

from app.services.admin_config import get_admin_config

router = APIRouter(prefix="/api/admin", tags=["app-backend-admin"])


@router.get("/config")
async def _get_admin_config():
    try:
        result = get_admin_config()
        if isawaitable(result):
            result = await result
        return result
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
```

## Response Shape

```json
{
  "schema_version": "mozaiks.admin.app_backend.v1",
  "panels": [
    {
      "id": "app.users",
      "label": "Users",
      "section": "users",
      "order": 10,
      "renderer": "builtin",
      "builtin_panel": "users",
      "permissions": ["admin.users.read"]
    },
    {
      "id": "billing.summary",
      "label": "Billing",
      "section": "billing",
      "order": 20,
      "renderer": "schema",
      "layout": "full-width",
      "sections": [
        {
          "id": "billing-table",
          "primitive": "DataTable",
          "config": {
            "api_endpoint": "/api/admin/billing",
            "columns": [
              { "key": "plan", "label": "Plan" },
              { "key": "status", "label": "Status", "type": "badge" }
            ]
          }
        }
      ],
      "permissions": ["admin.billing.read"]
    }
  ]
}
```

## Fields

Top-level:

- `schema_version`: must be `mozaiks.admin.app_backend.v1`
- `panels`: array of app-backend admin panels

Shared panel fields:

- `id`: stable panel id
- `label`: human-facing label
- `section`: one of
  `overview | users | billing | usage | activity | settings | integrations | support`
- `order`: sort order within section
- `renderer`: one of `builtin | schema | custom_component`
- `permissions`: optional permission or role ids enforced by the backend

## Renderer Modes

### `renderer: "builtin"`

Use this only for shipped app-backend admin surfaces already implemented by
`AppAdminDashboard`.

Required:

- `builtin_panel`: one of `stats | users | subscriptions`

Not allowed:

- `layout`
- `sections`
- `component`

### `renderer: "schema"`

Use this for the normal deterministic path.

Required:

- `sections[]`

Optional:

- `layout`: one of `grid | sidebar | full-width | split`

Not allowed:

- `builtin_panel`
- `component`

Schema panels reuse the same section primitive contract as `ui/pages/*.yaml`,
but they render inside the admin shell instead of creating routes.

`config.api_endpoint` values are app-backend relative paths. The shell prefixes
them with `app_backend_url` at runtime.

### `renderer: "custom_component"`

Use this only when the shipped primitive system cannot represent the panel
cleanly.

Required:

- `component`: registered component key

Not allowed:

- `builtin_panel`
- `sections`

The component must already be registered in the active app root's `ui/index.js`
barrel.

## Built-In vs Module Panels

Use this contract for app-business panels owned by the connected app backend.

Use `modules/{module}/contracts/admin.yaml` for feature-owned panels that live with a
Mozaiks module contract.

Do not duplicate the same surface in both places.

## Non-Goals

This contract does not:

- define the `/admin` shell
- replace `app/app.json` `admins`
- replace `modules/{module}/contracts/admin.yaml`
- allow app backends to generate standalone admin routes or React shells

