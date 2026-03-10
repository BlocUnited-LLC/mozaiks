# Mozaiks Platform — Dual Substrate Architecture

> **The core thesis:** Every serious app needs two things — intelligence (agentic workflows) and infrastructure (user management, notifications, settings, subscriptions, CRUD pages). These are different concerns, different runtimes, different deployment lifecycles. They must be treated as peers, not merged.

---

## Is This a Good Idea?

**Yes — and here's specifically why it holds up:**

The separation is honest about what's actually different. AI workflows have a fundamentally different execution model — async, non-deterministic, long-running, stream-based — vs. CRUD operations, which are synchronous, predictable, and request/response. Mixing them into one runtime means they compete for resources, their error models conflict, and you can't scale or deploy them independently. Keeping them as peers solves all of that without throwing away anything.

The **module-as-bridge** pattern is especially strong. The same `execute(data)` function gets called whether a human clicked a button or an agent invoked a tool. Business logic is written once and works in both worlds. That's not just architecturally clean on paper — it's a real reduction in duplication and a real increase in surface area for AI capability without extra work.

The **UI continuity design** (`layoutMode='view'` keeping the widget alive while a CRUD page renders in the artifact) is the right answer to a problem most AI app builders are currently ignoring. The jarring transition back to "crud world" breaks the experience immersion. Most platforms don't have a designed answer to this. This one does — and it was designed before the problem was widely recognized.

**The one real risk to manage early:**

The `/__mozaiks/admin/` internal API that lets mozaiksai call into mozaikscore needs to be treated as a genuine security boundary, not just a naming convention. The current `X-Internal-API-Key` pattern is a start, but before the platform scales:

- The key must be rotated, scoped per environment, and never exposed client-side
- Agent calls should only be able to touch resources they were explicitly given context for — if a workflow goes rogue or a prompt injection occurs, it should not be able to freely write to any user's data in mozaikscore
- Per-call scoping (agent receives a context token bound to a specific `app_id` + `user_id` + permitted actions) is worth designing early, before it becomes a retrofit

Everything else — the tiered cores idea, AdminPortal as a first-class built-in, the declarative config layer — is directionally correct and will age well.

---

## Table of Contents

- [The Two Substrates](#the-two-substrates)
- [What mozaikscore Already Is](#what-mozaikscore-already-is)
- [The UI Integration Story](#the-ui-integration-story)
- [Modules — The Bridge Between Worlds](#modules--the-bridge-between-worlds)
- [What Every App Ships With](#what-every-app-ships-with)
- [Integration Contracts](#integration-contracts)
- [What Changes Now](#what-changes-now)
- [Future Cores](#future-cores)

---

## The Two Substrates

Two runtimes. Two repos. Two deployment lifecycles. Neither owns the other.

```
┌─────────────────────────────────────────────────────────────────┐
│                      MOZAIKS PLATFORM                           │
│                                                                 │
│  ┌───────────────────────┐    ┌───────────────────────────┐    │
│  │     mozaiksai         │    │      mozaikscore           │    │
│  │  (agentic substrate)  │    │  (application substrate)  │    │
│  │                       │    │                           │    │
│  │  • AG2 engine         │    │  • director.py (routes)   │    │
│  │  • Workflows          │    │  • plugin_manager.py      │    │
│  │  • Fan-out / fan-in   │◄──►│  • event_bus.py           │    │
│  │  • Structured outputs │    │  • state_manager.py       │    │
│  │  • Chat UI shell      │    │  • notifications_manager  │    │
│  │  • Artifact rendering │    │  • settings_manager       │    │
│  │  • Tool execution     │    │  • subscription_manager   │    │
│  │                       │    │  • websocket_manager      │    │
│  └───────────────────────┘    └───────────────────────────┘    │
│                  │                         │                    │
│                  └──────────┬──────────────┘                    │
│                             ▼                                   │
│         ┌───────────────────────────────────────┐              │
│         │         SHARED INFRASTRUCTURE          │              │
│         │  MongoDB  │  Auth (Keycloak/JWT)        │              │
│         │  EventBus │  WebSocket transport        │              │
│         │  APP_ID   │  Multi-tenant isolation     │              │
│         └───────────────────────────────────────┘              │
└─────────────────────────────────────────────────────────────────┘
```

| Concern | mozaiksai | mozaikscore |
|---------|-----------|-------------|
| Primary job | Agentic orchestration, LLM workflows | Application services, CRUD, user infrastructure |
| Entry point | `shared_app.py` | `backend/main.py` |
| API prefix | `/api/chats/`, `/ws/workflow/` | `/api/`, `/__mozaiks/admin/` |
| Data model | Chat sessions, workflow runs, artifacts | Users, settings, notifications, subscriptions |
| Deployment | Can scale/swap independently | Can deploy without AI at all |
| Knows about the other? | Calls mozaikscore REST APIs | Exposes REST APIs that mozaiksai can call |

**The rule:** If it's about making an agent do something — mozaiksai. If it's about the app persisting user data, managing access, or surfacing structured information — mozaikscore.

---

## What mozaikscore Already Is

mozaikscore is not a prototype. The core runtime exists and is production-structured. What follows is what it already provides.

### Runtime Layer (`backend/main.py` + `backend/core/director.py`)

`main.py` is the cloud-native entry point:
- Azure Container Apps health/readiness probes (`/health`, `/ready`, `/info`)
- JWT-authenticated WebSocket routes (`/ws/notifications/{user_id_hint}`)
- Startup lifecycle: DB verification, index creation, plugin registration
- Mounts `director_app` as the application core

`director.py` is the per-app FastAPI application:
- All HTTP API routes registered here
- Per-app identity via `MOZAIKS_APP_ID` env var
- `inject_request_context()` stamps every request with `app_id` + `user_id` from JWT — clients cannot override this
- Config loading via `load_config()` with a central config loader and 5-minute TTL cache
- Background plugin refresh (every 5 minutes, non-blocking)
- `MONETIZATION` env flag switches between full `SubscriptionManager` and `SubscriptionStub`

### Currently Registered API Routes

**Public (JWT required):**
- `GET /api/app-config` — app config, monetization status
- `GET /api/navigation` — user-filtered navigation items
- `GET /api/theme-config` — theme configuration
- `GET /api/settings-config` — filtered settings sections
- `GET /api/user-profile` — profile data
- `POST /api/update-profile` — profile update
- `GET /api/available-plugins` — modules accessible to user
- `POST /api/execute/{plugin_name}` — execute a module
- `GET /api/check-plugin-access/{plugin_name}` — access gate
- `GET|POST /api/plugin-settings/{plugin_name}` — per-module settings
- `POST /api/notification-preferences` — notification preference update
- `GET /api/current-theme` + `POST /api/change-theme`
- `GET /api/subscription-plans` + `GET /api/user-subscription` + `POST /api/update-subscription` + `POST /api/cancel-subscription` _(MONETIZATION=1 only)_

**Internal / Admin (`/__mozaiks/admin/`, require `X-Internal-API-Key` or superadmin JWT):**
- `/__mozaiks/admin/users` — user management
- `/__mozaiks/admin/notifications` — bulk notification dispatch
- `/__mozaiks/admin/analytics` — app-level analytics
- `/__mozaiks/admin/status` — runtime status
- `/__mozaiks/admin/app` — app metadata (for Provisioning Agent)

**Sync (for mozaiksai → mozaikscore communication):**
- `/api/events` — event ingestion endpoint
- `/api/push` — push subscription management (Web Push / PWA)
- Subscription sync router — triggered by billing events from mozaiksai

### Core Services

**`event_bus.py`** — Thread-safe pub/sub with async support, retry logic (3 attempts, exponential backoff), event history (last 100 per type), and delivery stats. Already wired to subscription changes, theme changes, plugin executions.

**`state_manager.py`** — In-memory key/value store with optional TTL. Used for plugin refresh gating, navigation caching, subscription access caching.

**`plugin_manager.py`** — Filesystem scan → registry lookup → `importlib` dynamic load → `execute(data)` dispatch. Supports async plugins. Path configurable via `MOZAIKS_PLUGINS_PATH` env var. Auto-refreshes every 5 minutes.

**`websocket_manager.py`** — Connection registry keyed by `user_id`. Enables any backend service to push to a specific user in real time.

**`notifications_manager.py`** — Queue-based multi-channel delivery (in-app + email). Reads `notifications_config.json` for category/channel definitions. Respects user preferences. Pushes in-app notifications via `websocket_manager`. Max 100 stored per user.

**`settings_manager.py`** — Persists user settings to MongoDB `settings` collection. Validates preferences against `settings_config.json`. Filters plugin notification fields by subscription access. Fires `settings_updated` and `notification_preferences_updated` events.

**`subscription_manager.py` / `subscription_stub.py`** — Full manager when `MONETIZATION=1`. Stub provides unlimited access when off. Both expose the same interface so nothing else changes.

**`core/config/database.py`** — Motor async MongoDB. Connection pooling (max 100, min 10). `with_retry()` decorator. `DBCache` for TTL-based document caching. Enterprise collection for multi-tenant app registry.

### Config Files (the declarative layer)

All behavior is driven by JSON config files. Code doesn't change — only declarations do.

| File | Controls |
|------|----------|
| `navigation_config.json` | Which nav items appear, for which roles/tiers |
| `theme_config.json` | Branding, colors, app name |
| `settings_config.json` | Profile sections, field types, notification toggles |
| `notifications_config.json` | Notification categories, types, channels |
| `plugin_registry.json` | Registered modules, required tier, metadata |
| `subscription_config.json` | Plans, features, price points |
| `.env` | `MOZAIKS_APP_ID`, `MONETIZATION`, `DATABASE_URI`, `JWT_SECRET`, `HOSTING_SERVICE` |

---

## The UI Integration Story

The key insight that makes this seamless is already designed:

**`layoutMode='view'` + `surfaceMode=VIEW`** = fullscreen artifact rendering with the floating chat widget alive at the bottom.

The user never "leaves" the chat. When they click a navigation item that points to a mozaikscore module page, the page renders in the artifact panel — not a new browser tab, not a separate app. The chat widget persists at the bottom. It *feels* like you're still in the chat experience because you are. The chat context (widget state, conversation history) is tied to the widget — the artifact is just what's visible in the large panel.

```
┌──────────────────────────────────────────────────────────┐
│  layoutMode = 'view'  │  surfaceMode = VIEW (fullscreen) │
├──────────────────────────────────────────────────────────┤
│                                                          │
│   ┌──────────────────────────────────────────────────┐  │
│   │                                                  │  │
│   │        mozaikscore Module Page renders here      │  │
│   │        (e.g. Settings, AdminPortal, TaskBoard)   │  │
│   │                                                  │  │
│   │        This is a full CRUD page with its own     │  │
│   │        data, routes, and REST API calls          │  │
│   │                                                  │  │
│   └──────────────────────────────────────────────────┘  │
│                                                          │
│   ┌──────────────────────────────────────────────────┐  │
│   │  💬  Chat widget (floating, always present)      │  │
│   │      Conversation context preserved              │  │
│   └──────────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────────┘
```

A workflow agent CAN surface a mozaikscore page as its output. For example:
- Agent finishes a research workflow → creates a Report record in mozaikscore → returns an artifact pointing to `/m/reports/{id}`
- The artifact renders the TaskBoard module page with the newly created record highlighted
- The user can edit it in-place — pure CRUD, no AI involved — while the chat widget is still live if they want to ask follow-up questions

The transition between "chat" and "app" is invisible to the user. It's one living thing.

---

## Modules — The Bridge Between Worlds

"Plugins" was the old name. The concept was right, the word was wrong. In the platform era, these are **modules** — first-class application capabilities, not optional bolt-ons.

A module is the unit of business logic. It can be:

| Mode | What it means |
|------|---------------|
| **Standalone page** | A CRUD screen the user navigates to directly (via `navigation_config.json`) |
| **AI-callable tool** | The same backend logic, exposed as a tool a workflow agent can invoke via `use_tool()` |
| **Event-driven reactor** | Responds to `event_bus` events (e.g., subscription_updated → provision feature access) |
| **Notification producer** | Fires typed notifications through `notifications_manager` |

**Module directory structure:**

```
backend/plugins/{module_name}/        ← "plugins" dir is the physical location (rename pending)
  ├── plugin.json                     ← module metadata (name, version, required_tier, permissions)
  ├── handler.py                      ← execute(data) entry point
  ├── routes.py                       ← optional: additional REST endpoints registered on startup
  ├── models.py                       ← Pydantic models  
  ├── notifications.json              ← optional: notification types this module emits
  └── tools/
      └── {tool_name}.py              ← optional: AG2-callable tool wrapper (mozaiksai calls this)

src/plugins/{module_name}/            ← frontend (React)
  ├── index.jsx                       ← default page component
  └── settings.jsx                    ← optional: settings panel (rendered by ProfilePage)
```

**Module registration (`plugin_registry.json`):**

```json
{
  "plugins": [
    {
      "name": "task_board",
      "version": "1.0.0",
      "description": "Kanban-style task management",
      "required_tier": "pro",
      "enabled": true,
      "navigation": {
        "label": "Tasks",
        "path": "/m/task_board",
        "icon": "check-square"
      }
    }
  ]
}
```

**The execute contract (unchanged from legacy):**

```python
# backend/plugins/task_board/handler.py

async def execute(data: dict) -> dict:
    """
    data always contains: app_id, user_id, _context (injected by director)
    action: the operation to perform (list, create, update, delete, etc.)
    """
    action = data.get("action", "list")

    if action == "create":
        return await create_task(data)
    elif action == "list":
        return await list_tasks(data)
    # ...
```

**The AI-callable tool wrapper:**

```python
# backend/plugins/task_board/tools/create_task.py
# Called by mozaiksai workflows via POST /__mozaiks/admin/tools/execute

async def create_task_tool(title: str, due_date: str, context: dict) -> dict:
    """AG2-compatible tool. Calls the same module execute() logic."""
    from plugins.task_board.handler import execute
    return await execute({
        "action": "create",
        "title": title,
        "due_date": due_date,
        **context  # app_id, user_id injected by caller
    })
```

The module doesn't care whether it was called by a human via HTTP or by an agent via tool invocation. Same logic, same result.

---

## What Every App Ships With

This is the enterprise core. Every app built on mozaikscore gets these out of the box. They are not optional. They are production-ready on day one.

| Capability | Implementation | Config-driven? |
|-----------|----------------|----------------|
| **Auth** | JWT (local) or Keycloak (external), `MOZAIKS_AUTH_MODE` env | ✅ Auth mode in `.env` |
| **User Management** | Registration, login, profile, password change | ✅ `settings_config.json` field definitions |
| **Settings & Profile** | Per-section declarative forms, plugin settings panels | ✅ `settings_config.json` |
| **Notifications** | In-app + email, real-time WebSocket push, per-type preferences | ✅ `notifications_config.json` |
| **Themes** | Multi-theme support, per-user preference, live switching | ✅ `theme_config.json` |
| **Subscriptions / Entitlements** | Plan management, feature gating, nav filtering | ✅ `subscription_config.json` (MONETIZATION=1) |
| **Navigation** | Declarative nav items, role/tier-filtered at runtime | ✅ `navigation_config.json` |
| **Admin Portal** | User management, notification dispatch, analytics, status | ✅ Routes auto-registered |
| **WebSocket** | Real-time push to any user, connection management | No config — infra layer |
| **EventBus** | Decoupled pub/sub between all services | No config — infra layer |
| **Multi-tenant** | Every record scoped `app_id + user_id`, enforced server-side | ✅ `MOZAIKS_APP_ID` env |
| **Health / Readiness** | Azure probe-compatible `/health` and `/ready` endpoints | No config — always on |
| **Monetization toggle** | Full billing vs unlimited access via env flag | ✅ `MONETIZATION=1/0` |

---

## Integration Contracts

How the two substrates talk to each other. Neither one has a code dependency on the other — all communication is over HTTP or events.

### mozaiksai → mozaikscore

**When a workflow needs to read/write app data:**

```
POST /__mozaiks/admin/tools/execute
X-Internal-API-Key: {internal_key}
Content-Type: application/json

{
  "app_id": "my_app",
  "user_id": "user_123",
  "module": "task_board",
  "action": "create",
  "payload": { "title": "Research complete", "due_date": "2026-03-15" }
}
```

This hits the `admin` route on mozaikscore, which calls `plugin_manager.execute()` with the injected context. The agent gets back structured data.

**When a workflow needs to send a notification:**

```
POST /__mozaiks/admin/notifications/send
X-Internal-API-Key: {internal_key}

{
  "app_id": "my_app",
  "user_id": "user_123",
  "type": "research_complete",
  "title": "Your research is ready",
  "message": "The competitive analysis workflow finished.",
  "metadata": { "artifact_id": "abc123" }
}
```

**When a workflow outcome should appear on a module page:**

The workflow writes a record via the module execute endpoint. The frontend module page (rendered in the artifact) reads from the same data source. No direct coupling needed — they share the same MongoDB collection.

### mozaikscore → mozaiksai

**When a user action should trigger a workflow:**

mozaikscore fires an event on the `event_bus`:

```python
event_bus.publish("user_action", {
    "app_id": APP_ID,
    "user_id": user_id,
    "action": "report_requested",
    "context": { "topic": "market analysis" }
})
```

mozaiksai subscribes to this event and launches the appropriate workflow. No direct dependency.

**When a module page needs to display live workflow status:**

mozaiksai pushes a status update to mozaikscore via WebSocket:

```
POST /__mozaiks/admin/push/user/{user_id}
{ "type": "workflow_status", "workflow_id": "wf_123", "status": "running", "progress": 60 }
```

mozaikscore's `websocket_manager` delivers it to the user's active connection.

### Shared Infrastructure

Both substrates connect to the same MongoDB cluster under different databases or collections, namespaced by `app_id`. Auth tokens issued by the same authority (Keycloak or local JWT) are valid for calls to both.

---

## What Changes Now

The substrate architecture is already real — it just needs a few naming and structural alignments to reflect the new understanding.

### Terminology

| Old term | New term | Reason |
|----------|----------|--------|
| `plugin` | `module` | Plugins imply optional. Modules are first-class. |
| `execute(data)` | Stays the same | The contract is correct |
| `plugin_registry.json` | `module_registry.json` | Naming consistency (migration, not breaking change) |
| `MOZAIKS_PLUGINS_PATH` | `MOZAIKS_MODULES_PATH` | Env var rename (backward-compat alias during transition) |
| Route: `/api/execute/{plugin_name}` | `/api/modules/{module_name}/execute` | Cleaner REST semantics |
| Route: `/api/available-plugins` | `/api/modules/available` | Consistency |
| `PluginDevelopmentGuide.md` | `ModuleDevelopmentGuide.md` | Docs update |

### Directory Structure (target state)

```
backend/
  main.py                          ← entry point (unchanged)
  core/
    director.py                    ← route orchestration (unchanged)
    plugin_manager.py              ← rename to module_manager.py (or keep with alias)
    event_bus.py                   ← unchanged
    state_manager.py               ← unchanged
    websocket_manager.py           ← unchanged
    notifications_manager.py       ← unchanged
    settings_manager.py            ← unchanged
    subscription_manager.py        ← unchanged
    config/
      database.py                  ← unchanged
      module_registry.json         ← was plugin_registry.json
      navigation_config.json       ← unchanged
      settings_config.json         ← unchanged
      notifications_config.json    ← unchanged
      theme_config.json            ← unchanged
      subscription_config.json     ← unchanged
    routes/
      (existing admin/api routes)  ← unchanged
  modules/                         ← was plugins/
    task_board/
      handler.py
      routes.py
      models.py
      notifications.json
      tools/
        create_task.py
    admin_portal/                  ← first-class built-in module
      handler.py
      routes.py
```

### AdminPortal as a First-Class Module

AdminPortal is not a separate app — it's mozaikscore's built-in management module. It ships with every deployment. It is the first example of a fully-integrated non-AI page that:

- Lives at `/__mozaiks/admin/` on the backend
- Renders in the artifact panel under `layoutMode='view'`
- Has its own CRUD operations (user management, notification dispatch, analytics)
- Is NOT a plugin — it's always registered, always available to superadmin roles

This is the pattern every custom module will follow.

---

## Future Cores

The current focus is the **Enterprise Core** — apps with real users, monetization, persistence, and production requirements. But as the platform matures, different core tiers should emerge:

| Core Tier | Description | What's included |
|-----------|-------------|-----------------|
| **Enterprise Core** _(current)_ | Production apps with users and revenue | Auth, users, settings, notifications, subscriptions, themes, admin portal, modules |
| **Lite Core** _(future)_ | Internal tools, prototypes, single-user apps | Auth, settings, basic notifications, no subscription layer |
| **Headless Core** _(future)_ | API-only backends, no UI assumptions | Auth, event bus, module execution, no navigation/theme/settings config |

The substrate architecture supports this naturally — `MONETIZATION`, `HOSTING_SERVICE`, and future flags will activate/deactivate layers without forking the codebase. The same `main.py` → `director.py` → `module_manager.py` chain runs in all tiers. Only the registered modules and active config layers differ.

---

## Summary

mozaikscore is not a legacy system to be replaced. It is the application substrate that mozaiksai runs alongside.

- **mozaikscore** handles everything that makes an app an app: users, state, settings, notifications, access control, CRUD pages, administration
- **mozaiksai** handles everything that makes an app intelligent: agents, workflows, reasoning, structured outputs, tool execution
- **Modules** are the connective tissue: the same business logic callable by humans via REST and by agents via tools
- **The UI shell** makes it seamless: non-AI module pages render in the artifact panel with the chat widget alive — one experience, two substrates underneath
