# mozaikscore Integration Plan

> **Goal:** Bring the application substrate (mozaikscore) into the mozaiks monorepo as a **peer** to mozaiksai — not merged, not absorbed. Two substrates, one repo, shared infrastructure.

---

## 1. Architecture Decision: Single Repo, Two Substrates

The DualSubstrate doc envisions "two repos, two deployment lifecycles." For this project phase we adopt a pragmatic variant: **one monorepo, two independent Python packages, two FastAPI entry points**. The substrates remain architecturally isolated but share the repo, the CI pipeline, and the React shell.

```
mozaiks/                            ← monorepo root
├── mozaiksai/                      ← agentic substrate (exists today)
├── mozaikscore/                    ← application substrate (NEW)
├── shared_app.py                   ← mozaiksai entry point (exists)
├── core_app.py                     ← mozaikscore entry point (NEW)
├── run_server.py                   ← starts mozaiksai (exists)
├── run_core.py                     ← starts mozaikscore (NEW)
├── app/                            ← deployer's template app (exists, unchanged)
│   ├── app.json                    ← per-deployment identity (appName, appId, URLs, auth, engine)
│   ├── App.jsx                     ← root component wiring
│   └── brand/public/               ← declarative branding (colors, fonts, icons, UI structure)
├── chat-ui/                        ← React shell library (exists, enhanced)
├── workflows/                      ← workflow definitions (exists)
├── modules/                        ← module definitions (NEW)
└── config/                         ← platform service config files (NEW)
```

**Why single repo?** Simplifies development, avoids cross-repo version coordination, and lets the chat-ui shell import from both backends without CORS complexity. Deployment can still be independent — each entry point is its own container.

### `app/` vs `config/` — Different Scopes

| Directory | Scope | Who Edits | Examples |
|-----------|-------|-----------|----------|
| `app/` | Per-deployment identity + branding + build shell | The app deployer | `app.json` (appName, appId, apiUrl, auth), `brand/public/brand.json` (colors, fonts), `brand/public/ui.json` (header, footer) |
| `config/` | Platform service declarations read by mozaikscore at runtime | Platform engineers | `navigation_config.json`, `settings_config.json`, `notifications_config.json`, `module_registry.json`, `subscription_config.json` |

`app/` is **not** replaced by `config/`. They are complementary — `app/` defines what the deployed app *looks like and connects to*, while `config/` defines what platform services *offer and enforce*. `app/brand/public/navigation.json` (minimal, deployer-facing) and `config/navigation_config.json` (full, role/tier-filtered, mozaikscore-managed) coexist — the runtime merges navigation from both sources.

---

## 2. What Migrates from mozaiks-core-public

Each source file maps to a target location. Code is adapted, not copy-pasted — APIs stay the same, but Motor client setup, config paths, and import chains are unified with the current repo's conventions.

### 2.1 Core Services (backend/core/ → mozaikscore/core/)

| Source (mozaiks-core-public) | Target (mozaiks) | Lines | Adaptation Notes |
|------------------------------|-------------------|-------|------------------|
| `backend/core/director.py` (~958L) | `mozaikscore/core/director.py` | Rewrite | Strip AI bridge routes. Keep: app-config, navigation, theme, settings, profile, module execute, subscription, notification-prefs routes. Use shared Mongo client from `mozaiksai.core.core_config`. |
| `backend/core/event_bus.py` (~210L) | `mozaikscore/core/event_bus.py` | Near-direct | Thread-safe pub/sub + async queue + retry. Standalone, no external deps. Migrates as-is with minor logging updates. |
| `backend/core/plugin_manager.py` (~519L) | `mozaikscore/core/module_manager.py` | Rename+adapt | Rename class `PluginManager` → `ModuleManager`. Scan dir: `modules/` not `plugins/`. Registry file: `module_registry.json`. Execute contract: `execute(data)` unchanged. Config path from `MOZAIKS_CONFIGS_PATH` env. |
| `backend/core/state_manager.py` (~55L) | `mozaikscore/core/state_manager.py` | Direct copy | Pure in-memory KV with TTL. Zero external deps. |
| `backend/core/notifications_manager.py` (~929L) | `mozaikscore/core/notifications_manager.py` | Adapt | Replace `database` import with shared Motor client. Keep: queue processing, channel dispatch, email sending, WebSocket push, config loading, event handlers. |
| `backend/core/settings_manager.py` (~198L) | `mozaikscore/core/settings_manager.py` | Adapt | Replace `settings_collection` import with shared Motor client. Keep: get/save/validate settings, event_bus publish. |
| `backend/core/subscription_manager.py` (~438L) | `mozaikscore/core/subscription_manager.py` | Adapt | Replace DB imports. Keep: plan checking, feature gating, trial logic, billing events. |
| `backend/core/subscription_stub.py` (~87L) | `mozaikscore/core/subscription_stub.py` | Direct copy | Pure stub, no deps. |
| `backend/core/websocket_manager.py` (~56L) | `mozaikscore/core/websocket_manager.py` | Direct copy | FastAPI WebSocket connection registry. |

### 2.2 Database Layer (backend/config/database.py → mozaikscore/core/database.py)

| Source | Target | Adaptation |
|--------|--------|------------|
| `backend/config/database.py` (~243L) | `mozaikscore/core/database.py` | **Do not duplicate the Motor client.** Instead: import `get_mongo_client()` from `mozaiksai.core.core_config` and derive mozaikscore's database/collections from it. Add mozaikscore-specific collection accessors (`users_collection`, `settings_collection`, `subscriptions_collection`, etc.) and the `DBCache`, `with_retry`, `verify_connection` utilities. |

### 2.3 Routes (backend/core/routes/ → mozaikscore/core/routes/)

| Source | Target | Purpose |
|--------|--------|---------|
| `admin_users.py` | `mozaikscore/core/routes/admin_users.py` | `/__mozaiks/admin/users` — user CRUD, requires superadmin |
| `notifications_admin.py` | `mozaikscore/core/routes/notifications_admin.py` | `/__mozaiks/admin/notifications` — bulk send |
| `analytics.py` | `mozaikscore/core/routes/analytics.py` | `/__mozaiks/admin/analytics` — app-level metrics |
| `status.py` | `mozaikscore/core/routes/status.py` | `/__mozaiks/admin/status` — runtime status |
| `app_metadata.py` | `mozaikscore/core/routes/app_metadata.py` | `/__mozaiks/admin/app` — app metadata |
| `push_subscriptions.py` | `mozaikscore/core/routes/push_subscriptions.py` | `/api/push` — Web Push |
| `events.py` | `mozaikscore/core/routes/events.py` | `/api/events` — event ingestion |
| `subscription_sync.py` | `mozaikscore/core/routes/subscription_sync.py` | Subscription billing sync |
| `notifications.py` | `mozaikscore/core/routes/notifications.py` | `/api/notifications` — user notification endpoints |

### 2.4 Config Files (backend/config/ → config/)

| Source | Target | Notes |
|--------|--------|-------|
| `navigation_config.json` | `config/navigation_config.json` | Add mozaikscore module pages |
| `theme_config.json` | `config/theme_config.json` | Direct copy |
| `settings_config.json` | `config/settings_config.json` | Direct copy |
| `notifications_config.json` | `config/notifications_config.json` | Direct copy |
| `plugin_registry.json` | `config/module_registry.json` | Rename keys: `plugins` → `modules` |
| `subscription_config.json` | `config/subscription_config.json` | Direct copy |

### 2.5 What Does NOT Migrate

| Source | Reason |
|--------|--------|
| `backend/main.py` | mozaikscore gets a new `core_app.py` entry point |
| `backend/shared_app.py` | This is the legacy copy — current repo's `shared_app.py` is the canonical mozaiksai entry |
| `backend/core/ai_bridge/` | AI coupling — mozaiksai handles this natively |
| `backend/core/analytics/` | Will be rebuilt as a module |
| `backend/core/insights/` | Will be rebuilt as a module |
| `backend/core/metrics/`, `telemetry/`, `public_metrics/` | Observability belongs to mozaiksai's existing layer |
| `backend/core/hosting_operator.py` | Azure-specific deployment concern — defer |
| `backend/core/runtime/` | mozaiksai has its own runtime package |
| `backend/security/` | Auth is already handled by `mozaiksai/core/auth/` |

---

## 3. Shared Infrastructure Contracts

Both substrates share these without duplication:

| Infrastructure | Owner | How the Other Accesses |
|----------------|-------|------------------------|
| **MongoDB Motor client** | `mozaiksai.core.core_config.get_mongo_client()` | mozaikscore imports it. Single connection pool, different databases/collections. |
| **Auth (JWT/Keycloak)** | `mozaiksai.core.auth.*` | mozaikscore imports `jwt_validator`, `dependencies` for FastAPI `Depends()`. Shared secret resolution via `get_secret()`. |
| **Secret resolution** | `mozaiksai.core.core_config.get_secret()` | mozaikscore calls it for `DATABASE_URI`, `JWT_SECRET`, `INTERNAL_API_KEY`. |
| **Multi-tenant isolation** | Both enforce `app_id` + `user_id` | mozaiksai: `core.multitenant.app_ids`. mozaikscore: `director.inject_request_context()`. Same `MOZAIKS_APP_ID` env var. |

**What mozaikscore owns exclusively:**
- `event_bus.py` — mozaikscore's internal pub/sub (not the same as mozaiksai's `UnifiedEventDispatcher`)
- `state_manager.py` — in-memory KV cache
- `module_manager.py` — module scanning, registry, `execute(data)` dispatch
- `notifications_manager.py` — multi-channel notification delivery
- `settings_manager.py` — per-user settings persistence
- `subscription_manager.py` / `subscription_stub.py` — entitlements + gating
- `websocket_manager.py` — notification WebSocket connections

---

## 4. Integration Contracts (Substrate ↔ Substrate)

### 4.1 mozaiksai → mozaikscore (Agent calls app services)

An agent workflow executing in mozaiksai needs to create a task, send a notification, or read user settings. It calls mozaikscore's internal admin API:

```
POST http://localhost:{CORE_PORT}/__mozaiks/admin/modules/execute
X-Internal-API-Key: {INTERNAL_API_KEY}
Content-Type: application/json

{
  "app_id": "my_app",
  "user_id": "user_123",
  "module": "task_board",
  "action": "create",
  "payload": { "title": "Research complete" }
}
```

**Implementation in mozaiksai:** A new `MozaiksCoreClient` in `mozaiksai/core/adapters/core_client.py`:

```python
class MozaiksCoreClient:
    """HTTP client for mozaiksai → mozaikscore communication."""

    def __init__(self, base_url: str, api_key: str):
        self.base_url = base_url
        self.api_key = api_key

    async def execute_module(self, app_id, user_id, module, action, payload=None):
        ...

    async def send_notification(self, app_id, user_id, type, title, message, metadata=None):
        ...

    async def push_to_user(self, user_id, message):
        ...
```

This client is registered as an AG2 tool wrapper so workflow agents can call it:

```python
# mozaiksai/core/workflow/tools/core_bridge.py
async def create_record(module: str, action: str, payload: dict, context: dict):
    """AG2-callable tool that bridges to mozaikscore modules."""
    client = get_core_client()
    return await client.execute_module(
        app_id=context["app_id"],
        user_id=context["user_id"],
        module=module, action=action, payload=payload
    )
```

### 4.2 mozaikscore → mozaiksai (User action triggers workflow)

When a user clicks "Generate Report" on a mozaikscore module page, the module fires an event:

```python
# In a module's handler.py
event_bus.publish("user_action", {
    "app_id": APP_ID,
    "user_id": user_id,
    "action": "report_requested",
    "context": {"topic": "market analysis"}
})
```

mozaikscore exposes a `/api/events` ingestion endpoint. mozaiksai's `UnifiedEventDispatcher` can subscribe to cross-substrate events by polling or WebSocket bridge (Phase 3).

### 4.3 Security Boundary

| Concern | Implementation |
|---------|----------------|
| Internal API auth | `X-Internal-API-Key` header on `/__mozaiks/admin/*` routes |
| Key management | `INTERNAL_API_KEY` env var, resolved via `get_secret()` |
| Scope limitation | Per-call `app_id` + `user_id` — agent cannot access other tenants |
| Future enhancement | Context tokens with per-call permitted actions (Phase 4) |

---

## 5. Frontend Integration

### 5.1 Existing Mechanisms (Already Built)

The chat-ui shell already has every mechanism needed:

| Mechanism | File | Status |
|-----------|------|--------|
| `layoutMode='view'` + `surfaceMode=VIEW` | ChatUI state machine | Exists — renders full artifact with floating chat |
| `componentRegistry` | `chat-ui/src/registry/componentRegistry.js` | Exists — `registerComponent(name, Component)` |
| `coreComponents.js` | `chat-ui/src/registry/coreComponents.js` | Exists — registers `ChatPage`, `AdminPortal` |
| `RouteRenderer.jsx` | `chat-ui/src/components/RouteRenderer.jsx` | Exists — core routes + dynamic routes from `navigation.json` |
| `NavigationProvider` | `chat-ui/src/context/NavigationProvider.jsx` | Exists — loads `navigation_config.json` at runtime |
| `runtimeBridge.js` | `chat-ui/src/runtimeBridge.js` | Exists — builds WS/HTTP URLs to AI runtime |

### 5.2 New Frontend Work

| Task | Description | Files |
|------|-------------|-------|
| **coreBridge.js** | HTTP client for mozaikscore REST APIs (`/api/navigation`, `/api/settings-config`, `/api/modules/*/execute`, etc.). Mirrors `runtimeBridge.js` pattern. | `chat-ui/src/coreBridge.js` (NEW) |
| **Module page components** | React components for built-in mozaikscore pages: `SettingsPage`, `NotificationsPage`. Registered in `coreComponents.js`. | `chat-ui/src/pages/SettingsPage.jsx`, etc. (NEW) |
| **Navigation config integration** | `NavigationProvider` currently reads navigation from a static config. Enhance to fetch from mozaikscore's `/api/navigation` endpoint (role/tier-filtered). | Modify `NavigationProvider.jsx` |
| **Module page rendering** | When a nav item's `component` resolves to a mozaikscore page, render it in artifact panel via `layoutMode='view'`. The mechanism exists — we just need pages to render. | No new mechanism needed |

### 5.3 How a Module Page Flows

```
User clicks "Settings" in nav
  → NavigationProvider resolves component: "SettingsPage"
  → componentRegistry.getComponent("SettingsPage") → <SettingsPage />
  → RouteRenderer renders at /m/settings
  → layoutMode switches to 'view', surfaceMode to VIEW
  → SettingsPage renders in artifact panel
  → SettingsPage fetches from mozaikscore: GET /api/settings-config, GET /api/user-profile
  → Chat widget floats at bottom, context preserved
```

---

## 6. Package Structure: mozaikscore/

```
mozaikscore/
├── __init__.py
├── core/
│   ├── __init__.py
│   ├── database.py              ← collection accessors, DBCache, with_retry
│   ├── director.py              ← FastAPI sub-app, all public routes
│   ├── event_bus.py             ← thread-safe pub/sub + async queue
│   ├── module_manager.py        ← scan, load, execute modules
│   ├── state_manager.py         ← in-memory KV with TTL
│   ├── websocket_manager.py     ← WS connection registry
│   ├── notifications_manager.py ← multi-channel notifications
│   ├── settings_manager.py      ← per-user settings persistence
│   ├── subscription_manager.py  ← entitlements + gating
│   ├── subscription_stub.py     ← unlimited-access stub
│   └── routes/
│       ├── __init__.py
│       ├── admin_users.py
│       ├── notifications.py
│       ├── notifications_admin.py
│       ├── analytics.py
│       ├── status.py
│       ├── app_metadata.py
│       ├── push_subscriptions.py
│       ├── events.py
│       └── subscription_sync.py
│
config/
├── navigation_config.json
├── theme_config.json
├── settings_config.json
├── notifications_config.json
├── module_registry.json
└── subscription_config.json
│
modules/
├── admin_portal/
│   ├── handler.py               ← execute(data) for admin operations
│   ├── routes.py                ← optional extra routes
│   └── tools/
│       └── admin_query.py       ← AG2-callable tool wrapper
└── (future modules: task_board, reports, etc.)
│
core_app.py                       ← mozaikscore FastAPI entry point
run_core.py                       ← uvicorn launcher for mozaikscore
```

---

## 7. Entry Point: core_app.py

```python
"""mozaikscore — Application substrate entry point."""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from mozaiksai.core.core_config import get_mongo_client  # shared infra
from mozaikscore.core.director import create_director_app
from mozaikscore.core.module_manager import ModuleManager
from mozaikscore.core.event_bus import event_bus
from mozaikscore.core.websocket_manager import websocket_manager

app = FastAPI(title="mozaikscore", version="1.0.0")

# CORS — same origins as mozaiksai
app.add_middleware(CORSMiddleware, ...)

@app.on_event("startup")
async def startup():
    # 1. Verify MongoDB
    client = get_mongo_client()
    await client.admin.command("ping")

    # 2. Init module manager
    module_manager = ModuleManager()
    await module_manager.init_async()

    # 3. Start event bus background processing
    await event_bus.start_background_processing()

    # 4. Mount director sub-app (all routes)
    director_app = create_director_app(module_manager)
    app.mount("/", director_app)

@app.on_event("shutdown")
async def shutdown():
    await event_bus.stop_background_processing()

# Health probes
@app.get("/health")
async def health(): return {"status": "ok"}

@app.get("/ready")
async def ready(): return {"status": "ready"}
```

---

## 8. pyproject.toml Updates

```toml
[tool.setuptools.packages.find]
include = ["mozaiksai*", "mozaikscore*", "logs"]

[project.optional-dependencies]
core = [
    "aiohttp>=3.9.0",   # email sending in notifications_manager
]
```

The `mozaikscore` package is added to the setuptools discovery. Its dependencies are a subset of mozaiksai's (FastAPI, Motor, PyJWT are already in the main deps).

---

## 9. Environment Variables (New)

| Variable | Default | Purpose |
|----------|---------|---------|
| `MOZAIKS_CORE_PORT` | `8001` | Port for mozaikscore uvicorn |
| `MOZAIKS_CORE_URL` | `http://localhost:8001` | Base URL for mozaiksai → mozaikscore calls |
| `MOZAIKS_CONFIGS_PATH` | `./config` | Path to declarative config JSON files |
| `MOZAIKS_MODULES_PATH` | `./modules` | Path to module directories |
| `MONETIZATION` | `0` | `1` enables full subscription manager |
| `INTERNAL_API_KEY` | (required) | Auth key for `/__mozaiks/admin/*` routes |
| `EMAIL_SERVICE_URL` | (optional) | URL for email dispatch |

---

## 10. Phased Execution Plan

### Phase 1: Foundation (This Sprint)
**Create the mozaikscore package skeleton and migrate core services.**

| # | Task | Priority | Depends On |
|---|------|----------|------------|
| 1.1 | Create `mozaikscore/` package with `__init__.py`, `core/__init__.py` | P0 | — |
| 1.2 | Create `mozaikscore/core/database.py` — import shared Motor client, define mozaikscore collection accessors, port `DBCache` + `with_retry` | P0 | 1.1 |
| 1.3 | Migrate `event_bus.py` → `mozaikscore/core/event_bus.py` | P0 | 1.1 |
| 1.4 | Migrate `state_manager.py` → `mozaikscore/core/state_manager.py` | P0 | 1.1 |
| 1.5 | Migrate `websocket_manager.py` → `mozaikscore/core/websocket_manager.py` | P0 | 1.1 |
| 1.6 | Migrate `plugin_manager.py` → `mozaikscore/core/module_manager.py` (rename all references) | P0 | 1.2, 1.3 |
| 1.7 | Migrate `settings_manager.py` → `mozaikscore/core/settings_manager.py` | P0 | 1.2, 1.3 |
| 1.8 | Migrate `notifications_manager.py` → `mozaikscore/core/notifications_manager.py` | P0 | 1.2, 1.3, 1.5 |
| 1.9 | Migrate `subscription_manager.py` + `subscription_stub.py` | P1 | 1.2, 1.3 |
| 1.10 | Create `mozaikscore/core/director.py` with public + admin routes | P0 | 1.6–1.9 |
| 1.11 | Create `core_app.py` entry point + `run_core.py` launcher | P0 | 1.10 |
| 1.12 | Migrate all config JSON files to `config/` directory | P0 | — |
| 1.13 | Create `modules/admin_portal/handler.py` — first module | P1 | 1.6 |
| 1.14 | Update `pyproject.toml` to include `mozaikscore` package | P0 | 1.1 |
| 1.15 | Write tests: module_manager, event_bus, settings_manager, notifications_manager | P1 | 1.6–1.8 |

**Deliverable:** `python run_core.py` starts mozaikscore on port 8001. Health endpoint responds. Module execute endpoint works with admin_portal module.

### Phase 2: Integration Bridge (Next Sprint)
**Wire mozaiksai ↔ mozaikscore communication.**

| # | Task | Priority | Depends On |
|---|------|----------|------------|
| 2.1 | Create `mozaiksai/core/adapters/core_client.py` — MozaiksCoreClient HTTP adapter | P0 | Phase 1 |
| 2.2 | Create `mozaiksai/core/workflow/tools/core_bridge.py` — AG2 tool wrappers for module execution + notifications | P0 | 2.1 |
| 2.3 | Migrate admin routes (`admin_users`, `notifications_admin`, `analytics`, `status`, `app_metadata`) | P0 | Phase 1 |
| 2.4 | Migrate notification routes | P1 | Phase 1 |
| 2.5 | Migrate push subscription + subscription sync routes | P2 | Phase 1 |
| 2.6 | Wire `INTERNAL_API_KEY` validation middleware on `/__mozaiks/admin/*` | P0 | 2.3 |
| 2.7 | Test: agent workflow calls mozaikscore module via core_bridge tool | P0 | 2.2 |

**Deliverable:** A workflow agent can call `create_record(module="admin_portal", action="list_users")` and get results from mozaikscore.

### Phase 3: Frontend Pages (Sprint After)
**Build mozaikscore module pages in the React shell.**

| # | Task | Priority | Depends On |
|---|------|----------|------------|
| 3.1 | Create `chat-ui/src/coreBridge.js` — HTTP client for mozaikscore APIs | P0 | Phase 2 |
| 3.2 | Enhance `NavigationProvider` to fetch from mozaikscore `/api/navigation` | P0 | 3.1 |
| 3.3 | Create `SettingsPage.jsx` — renders settings from mozaikscore | P1 | 3.1 |
| 3.4 | Create `NotificationsPage.jsx` — notification center | P1 | 3.1 |
| 3.5 | Register new page components in `coreComponents.js` | P0 | 3.3, 3.4 |
| 3.6 | Update `navigation_config.json` with new pages | P0 | 3.5 |
| 3.7 | Enhance `AdminPortal` to call mozaikscore admin APIs (real data) | P1 | 3.1 |

**Deliverable:** User can click "Settings" in nav, see a CRUD settings page in the artifact panel with the chat widget floating below.

### Phase 4: Security Hardening + Demo (Future)
**Production security and the HelloWorld Research Report demo.**

| # | Task | Priority |
|---|------|----------|
| 4.1 | Add context tokens (scoped `app_id` + `user_id` + permitted actions per agent call) | P1 |
| 4.2 | Key rotation mechanism for `INTERNAL_API_KEY` | P2 |
| 4.3 | Rate limiting on `/__mozaiks/admin/*` routes | P2 |
| 4.4 | Build `modules/reports/` — a module with CRUD page + AG2 tool wrapper | P1 |
| 4.5 | Build "Research Report" workflow pack (fan-out/fan-in) that writes results to reports module | P1 |
| 4.6 | Cross-substrate event bridge: mozaikscore `event_bus` ↔ mozaiksai `UnifiedEventDispatcher` | P2 |
| 4.7 | Docker Compose config for dual-substrate deployment | P2 |

**Deliverable:** Full Research Report demo — 3 agent workflows fan out, results merge, agent writes report to mozaikscore module, user views/edits report in artifact panel while chat is live.

---

## 11. What Already Exists (No Migration Needed)

These capabilities in the current mozaiks repo already cover shared infrastructure:

| Capability | Current Location | mozaikscore Usage |
|------------|-----------------|-------------------|
| MongoDB Motor client | `mozaiksai/core/core_config.py` | Import `get_mongo_client()` |
| Secret resolution | `mozaiksai/core/core_config.get_secret()` | Import directly |
| JWT validation | `mozaiksai/core/auth/jwt_validator.py` | Import for route dependencies |
| OIDC discovery | `mozaiksai/core/auth/discovery.py` | Shared Keycloak integration |
| WebSocket auth | `mozaiksai/core/auth/websocket_auth.py` | For notification WS endpoint |
| Multi-tenant app_ids | `mozaiksai/core/multitenant/app_ids.py` | Import `get_app_id()` |
| Theme management | `mozaiksai/core/data/themes/theme_manager.py` | Already in mozaiksai — mozaikscore's theme routes delegate here |
| Platform hooks | `mozaiksai/core/runtime/platform_hooks.py` | mozaikscore can register hooks via `RUNTIME_PLATFORM_EXTENSIONS` |
| Component registry | `chat-ui/src/registry/componentRegistry.js` | Register mozaikscore page components |
| Route rendering | `chat-ui/src/components/RouteRenderer.jsx` | Auto-routes from `navigation.json` |

---

## 12. MongoDB Collection Topology

```
MozaiksDB (shared)
├── Enterprises                    ← enterprise registry (mozaikscore owns)
│
MozaiksCore (mozaikscore owns)
├── users                          ← user profiles, embedded notifications
├── settings                       ← per-user settings (user_id + plugin_name compound index)
├── subscriptions                  ← active subscription docs
├── subscription_history           ← plan change log
├── billing_history                ← payment events
│
mozaiks_{app_id} (mozaiksai owns)
├── chat_histories                 ← conversation turns
├── chat_sessions                  ← session metadata
├── workflow_runs                  ← execution records
├── artifacts                      ← structured outputs
├── token_usage                    ← LLM token tracking
│
mozaiks_{app_id}_modules (mozaikscore module data)
├── task_board_tasks               ← per-module collections
├── reports                        ← created by modules, read by modules
```

**Isolation rule:** mozaiksai never writes to `MozaiksCore` collections directly. mozaikscore never writes to `mozaiks_{app_id}` workflow collections. Cross-substrate data access goes through REST APIs only.

---

## 13. Risks and Mitigations

| Risk | Mitigation |
|------|------------|
| Motor client contention (two FastAPI apps sharing one client) | Motor's connection pool (100 max) is designed for this. Monitor pool utilization. |
| `INTERNAL_API_KEY` exposure | Never in client-side code. Env-only. Rotate per deployment. Future: scoped context tokens. |
| Import coupling (mozaikscore importing from mozaiksai) | Limit to `core_config` + `auth` — leaf utilities only. No workflow/transport imports. |
| Config file conflicts (both substrates reading from `config/`) | Each file is owned by one substrate. No shared writes. |
| Startup ordering (mozaikscore must be up before mozaiksai calls it) | Health check in `MozaiksCoreClient.execute_module()` with retry. Docker Compose `depends_on`. |

---

## 14. Success Criteria

After Phase 1:
- [ ] `python run_core.py` starts mozaikscore on port 8001
- [ ] `GET /health` returns `{"status": "ok"}`
- [ ] `POST /api/modules/admin_portal/execute` returns module response
- [ ] `GET /api/navigation` returns navigation config
- [ ] `GET /api/settings-config` returns settings schema
- [ ] All existing mozaiksai tests still pass

After Phase 2:
- [ ] A workflow agent can call `core_bridge.create_record()` and get a response from mozaikscore
- [ ] `/__mozaiks/admin/` endpoints require `X-Internal-API-Key`
- [ ] Integration test: agent → core_bridge → mozaikscore → MongoDB → response

After Phase 3:
- [ ] User sees "Settings" in navigation
- [ ] Clicking "Settings" renders SettingsPage in artifact panel
- [ ] Chat widget remains visible and functional
- [ ] AdminPortal shows real user/notification data from mozaikscore

After Phase 4:
- [ ] Research Report workflow creates a report via mozaikscore module
- [ ] User views/edits report in artifact panel while chat is live
- [ ] Full fan-out/fan-in demo with both substrates working together
