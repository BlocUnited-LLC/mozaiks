# Platform Authoring

This document defines what should be authored in an app workspace and the key
file contracts.

## Core Rule

The canonical target is a self-contained app workspace rooted at `app/`. The
same app-root shape should appear inside hosted product workspaces as well.

It should contain declaratives, assets, and explicit logic stubs. It should not contain framework compiler logic.

The bundle should stay focused on the product-facing pieces:

- app manifest
- pages
- workflows
- modules

Modules exist as support bundles, but they should not dominate the authoring model.

## Active Root vs Workspace Wrapper

The runtime reads an active app root. Canonically, that root is:

- `app/` in a generated/customer app workspace
- `factory_app/app/` for the first-party Console app bundle served by the Studio host in this repo

Current repo note:

- `factory_app/app/` is the repo-local first-party app bundle inside this repo
- hosted product workspaces should follow the same self-contained app-root pattern

## Primary Families

| Family | Purpose | Path |
| --- | --- | --- |
| App manifest | Small app identity and target manifest | `app/app.json` |
| Pages | Normal app screens | `app/ui/pages/*` |
| Workflows | Agentic execution | `app/workflows/*` |
| Workflow triggers | App event to workflow rules | `app/workflows/*/orchestrator.yaml` |

## Support Family

| Family | Purpose | Path |
| --- | --- | --- |
| Modules | Shared backing logic and helpers | `app/modules/*` |

## What Users Should Mostly Author

For most apps, the user or generator should mainly author:

1. app manifest
2. pages
3. workflows
4. modules (when shared support logic appears)

Workflow triggers are declared inline in `orchestrator.yaml`, not as separate files.

## Generator Decision Rules

When the generator needs to add a new capability:

- visible routeable screen -> create a `page`
- shared backend helper -> create a `module`
- agentic execution -> create a `workflow`
- event-triggered workflow handoff -> add `triggers` in `orchestrator.yaml`

Generator outputs must not be written directly into the active app root. The
builder writes to `MOZAIKS_GENERATED_ARTIFACTS_PATH` first:

```text
generated/
├── apps/{app_id}/{build_id}/app/
└── workflows/{app_id}/{build_id}/{workflow_name}/
```

An explicit promotion step copies validated artifacts into the active root.

## Framework-Owned vs Generator-Owned

### Framework owns:

- manifest resolvers
- config compilers
- registry projections
- runtime loaders
- defaulting logic

Those do not belong in an app workspace.

Example:

- `chat-ui/src/platform/appManifest.js` is framework-owned
- `app/app.json` is generator-owned

## File Contracts

### `app/app.json`

Small authoring manifest only.

Expected default shape:

```json
{
  "appName": "My App",
  "targets": {
    "web": true,
    "mobile": false
  },
  "startup": {
    "landing_spot": "/dashboard"
  },
  "authRequired": true,
  "admins": [
    "owner@example.com"
  ]
}
```

The generator should not author low-level runtime plumbing here unless the user explicitly requests advanced overrides.

`app/app.json` is for app identity, startup route, and product auth intent.

It is not the place for shell colors, login theme files, footer links, or header chrome.

It is also not the place for local development shortcuts such as auto-login.

### `app/ui/pages/{page}.yaml`

Required:

- a declarative page schema using the App UI primitive contract

Optional:

- folder form: `app/ui/pages/{page}/page.yaml`
- custom UI extension files when a page intentionally leaves the primitive
  schema path

Shared page-level UI should live under `app/ui/pages/_shared/`, not inside a
module by default.

### `app/workflows/{workflow}/`

Required:

- `orchestrator.yaml`
- `agents.yaml`
- `handoffs.yaml`
- `context_variables.yaml`
- `structured_outputs.yaml`
- `tools.yaml`
- `ui_config.yaml`
- `hooks.yaml`

Optional but common:

- `tools/*.py`
- `ui/index.js`
- `ui/*.{js,jsx}`
- `extended_orchestration/mfj_extension.json`

Event routing and workflow triggers are configured via:
- `app/workflows/{workflow}/orchestrator.yaml` - `triggers` declare which app events start or resume a workflow
- app hosts/backends emit domain events through the runtime ingress boundary; there is no separate app-owned automations catalog file

### `app/modules/{module}/`

Required:

- `module.yaml`
- `backend/handler.py`

Optional contracts:

- `contracts/events.yaml`
- `contracts/reactions.yaml`
- `contracts/notifications.yaml`
- `contracts/settings.yaml`
- `contracts/admin.yaml`
- `contracts/entitlements.yaml`

Recommended for any module with database access:

- `backend/service.py`
- `backend/repo.py`
- `backend/policy.py`
- `backend/schemas.py`

Optional helper files:

- `backend/{helper_files}.py`
- `ui/index.js` for explicit frontend/admin extension registration
- additional `ui/*.{js,jsx}`

#### Backend layer contract

Each file in `backend/` has a single responsibility. Do not mix concerns.

**`handler.py`** — dispatch layer only.

```python
class {Name}Handler:
    def __init__(self):
        self.service = {Name}Service()

    async def action_name(self, ctx, *, param: str) -> dict:
        return await self.service.action_name(ctx, param=param)
```

Rules: never access `ctx.db` directly, never contain conditional business logic,
never call `ctx.emit()`. One method per action declared in `module.yaml`.

**`service.py`** — all business logic lives here.

```python
class {Name}Service:
    def __init__(self, repo=None):
        self.repo = repo or {Name}Repo()

    async def action_name(self, ctx, *, param: str) -> dict:
        # validate → repo calls → ctx.emit() → return
        record = await self.repo.get(ctx, query={...})
        await ctx.emit("hosted.{name}.{event}", {...})
        return {"success": True}
```

Rules: never access `ctx.db` directly, delegates all DB operations to repo,
calls `ctx.emit()` only after state is committed.

**`repo.py`** — database access only.

```python
COLLECTION = "hosted_{name}_records"

class {Name}Repo:
    async def _collection(self, ctx):
        db = getattr(ctx, "db", None)
        if db is not None:
            return db[COLLECTION]
        from mozaiksai.core.core_config import get_mongo_client
        return get_mongo_client()["mozaiks"][COLLECTION]

    async def get(self, ctx, *, query: dict) -> dict | None: ...
    async def insert(self, ctx, *, record: dict) -> None: ...
    async def update(self, ctx, *, query: dict, update: dict) -> int: ...
    async def list(self, ctx, *, query: dict, limit: int) -> list[dict]: ...
    async def count(self, ctx, *, query: dict) -> int: ...
```

Rules: no business logic, no event emission, no validation — pure data access.

**`policy.py`** — multi-tenancy query scoping.

```python
def owner_id_from_context(ctx, user_id=None) -> str:
    return user_id or getattr(ctx, "user_id", None) or ""

def scoped_owner_query(ctx) -> dict:
    owner_id = owner_id_from_context(ctx)
    return {"owner_id": owner_id} if owner_id else {}

def scoped_record_query(ctx, *, record_id: str) -> dict:
    query = {"record_id": record_id}
    owner_id = owner_id_from_context(ctx)
    if owner_id:
        query["owner_id"] = owner_id
    return query
```

Rules: pure functions only, no DB access, no side effects.

**`schemas.py`** — typed document definitions and pure helpers.

```python
from typing import TypedDict

class {Name}Record(TypedDict):
    record_id: str
    owner_id: str
    status: str
    created_at: str
    updated_at: str

def timestamp_now() -> str:
    from datetime import UTC, datetime
    return datetime.now(UTC).isoformat()

def coerce_limit(value, default: int = 20, maximum: int = 100) -> int:
    try:
        return max(1, min(int(value), maximum))
    except Exception:
        return default
```

Rules: TypedDicts for MongoDB document shapes, no Pydantic (framework handles
dispatch validation), no I/O, no imports from service or repo.

Modules are backing capability bundles. The generator should not create module UI unless the module truly exports reusable UI.

Modules do not own page routing by default. Routeable surfaces should be pages.

### Active app-root frontend extension barrel

When a generated app needs bounded frontend customization, the active app root
may also contain:

- `app/ui/index.js` in app workspaces

This file is the deterministic registration barrel loaded by `@platform/extensions`.
Generators may create it only to register contract-declared UI stubs such as
custom admin components declared through `modules/{module}/contracts/admin.yaml` and
materialized from `module_contract.js_stubs`.

Do not treat `ui/index.js` as a second page system. It is only a bounded
extension entrypoint for declared customization stubs.

### `app/brand/`

Generator-owned when the app needs shell branding.

Use:

- `app/brand/assets/` for logos and icons
- `app/brand/fonts/` for local fonts
- `app/brand/login-theme/` only when auth login theme assets change

Do not use `app/brand/` for page route logic.

## What Mozaiks Should Default

Mozaiks should default anything that most apps share, including:

- shell behavior
- auth plumbing
- backend URLs
- mobile defaults
- version defaults
- dev defaults
- registry projections

Those should not be the main declarative burden on the user.

## Simple Compiler View

The platform should think in this order:

```text
app idea
  -> small app manifest
  -> pages
  -> workflows (with inline triggers)
  -> app events
  -> support modules if needed
```

That is enough to generate useful apps without turning the config layer into a second programming language.

## Derived or Generated Artifacts

These should not be generator-authored:

- derived module catalog projections
- manifest resolvers
- generated realm exports
- system support files such as silent SSO helpers

Current examples:

- `app/brand/realm-export.json` is a generated deployment artifact
- `app/brand/_system/silent-check-sso.html` is framework support

## Anti-Drift Rule

If a file exists only to repeat information already declared elsewhere, the generator should not author it unless the runtime still requires it as a true input.

That means:

- do not duplicate module metadata into a separate catalog file
- do not duplicate app defaults into authored manifests
- do not add compiler helpers to the app workspace

## Cross References

- [canonical-app-structure.md](canonical-app-structure.md)
- [Frontend Architecture](../mozaiksai/index.md)
- [surface-model.md](surface-model.md)
