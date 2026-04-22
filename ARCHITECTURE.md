# Mozaiks Architecture

This is the authoritative architecture reference. If other docs contradict this file, this file wins.

## What This Repo Is

This repo is the **Mozaiks framework** — the AI runtime, frontend surfaces, and
app authoring contract for Mozaiks apps.

This repo does **not** contain the hosted control plane, and it does **not**
assume one in-repo app backend implementation. App backends integrate through
the generic `AppBackendPort` contract in
`mozaiksai/core/ports/app_backend.py`.

```
mozaiks/                           # This repo
├── mozaiksai/                     # AI workflow runtime (first-class)
├── chat-ui/                       # Chat interface primitives (first-class)
├── app/                           # Web shell / local app host
├── platform/                      # App bundle / authoring contract
│   ├── workflows/                 # AI workflows (app-level)
│   ├── operations/                # Business logic (app-level)
│   ├── pages/                     # UI screens (app-level)
│   ├── brand/                     # Theme and assets (app-level)
│   └── config/                    # Platform settings (app-level)
├── mozaiks_cli/                   # CLI for local project and workflow work
└── mozaiks-platform/              # Local generator/platform workspace (git-ignored, optional)
```

**External repo (paired, not in this repo):**
```
mozaiks-core-public/               # Reference app backend runtime (one deployment per app)
├── platform/modules/{name}/       # Module manifests (6-file YAML system)
│   ├── module.yaml                # Identity + nav routes + capabilities
│   ├── admin.yaml                 # Module admin panels mounted inside /admin
│   ├── events.yaml                # Domain events published + workflow triggers
│   ├── settings.yaml              # User-facing preferences schema
│   ├── notifications.yaml         # Notification rules per event
│   ├── subscriptions.yaml         # Tier gates per feature
│   └── module_surface.yaml        # REST endpoint surface for AI agents
└── backend/core/                  # Auth, module manager, event bus, AI bridge
```

**Primary boundary for this repo:**
- The committed, first-class product in this repo is still the **framework that runs apps**.
- App backends are external deployables that integrate with `mozaiksai` through `AppBackendPort`.
- The managed platform at mozaiks.ai is **not** contained in this repo.
- A local, git-ignored `mozaiks-platform/` workspace may also exist in some clones for development, testing, and possible CLI-driven generator/platform work.
- That local generator/platform workspace is adjacent to the framework, but it is **not** part of the canonical framework contract unless you are explicitly working in it.

If you're working in this repo, you may be working in one of two modes:

1. **Framework mode** — working on `mozaiksai/`, `chat-ui/`, `app/`, or the app authoring contract in `platform/`
2. **Generator/platform mode** — working in the local, git-ignored `mozaiks-platform/` area for AppGenerator/platform development or local CLI-related experimentation

Unless a task explicitly targets `mozaiks-platform/`, default to treating this repo as the **framework that runs apps**.

---

## Runtime Layering & Separation of Concerns (CRITICAL)

This repo now uses layered FastAPI hosts as the canonical runtime architecture.

This section defines the canonical runtime boundaries.
When writing or modifying code, these rules take precedence over legacy patterns.

### Layer Model

The system is composed of four layers:

1. **Factory** — builder / generator layer
2. **Platform** — app shell / app host layer
3. **Runtime** — AI substrate
4. **Dev / CLI / Preview** — local-only convenience layer

Dependency direction should flow downward through stable contracts:

```
Factory (builder / generator)
   -> Platform (app shell / app host)
      -> Runtime (AI substrate)

Dev / CLI / Preview is local-only support code and must not become a dependency of reusable hosts.
```

### 1. Runtime (AI Substrate)

**Purpose:** A workflow-agnostic, app-agnostic execution engine.

**Owns:**
- FastAPI runtime host for the execution substrate
- WebSocket and API transport primitives
- Workflow execution and AG2 orchestration
- Agent runtime and tool execution
- Event dispatch infrastructure
- Persistence access for chat sessions and runtime state
- Auth and principal enforcement at the runtime boundary
- Runtime health, observability, and metrics

**Must not own:**
- Shell config (`/api/shell-config`)
- Pages (`/api/pages`)
- Themes (`/api/theme-config`)
- Transitions (`/api/transitions`)
- Studio or build routes
- Admin app-shell routes or product-shell UI composition
- Platform workflow ordering or app-host composition logic
- Generator or refinement behavior
- Repo-layout assumptions such as `mozaiks-platform/app`
- CLI conveniences, preview helpers, or other local-only behavior

**Examples:**
- `runtime_app.py`
- `mozaiksai/core/transport/*`
- `mozaiksai/core/workflow/*`
- `mozaiksai/core/events/*`

### 2. Platform (App Shell / App Host)

**Purpose:** Hosts and composes an app on top of the runtime substrate.

**Owns:**
- Chat and session APIs such as `/api/chats/*`
- Shell config, theme config, and page serving
- Transitions and routing composition
- Session UX concerns such as resume, metadata, and lifecycle
- Operation execution and app-host integration points
- Admin and app-shell routes
- Workflow discovery, ordering, and app-level composition

**Must not own:**
- Orchestration engine internals
- Transport internals
- Event dispatch internals
- Generator or build logic

**Examples:**
- `platform_app.py`

### 3. Studio / Mozaiks Product Layer

**Purpose:** Creates, refines, and manages apps and workflows.

**Owns:**
- Studio and build UI
- App generation
- Workflow generation
- Refinement triggers and iteration flows
- Artifact lifecycle from generated to staged to promoted
- Export, download, build, and promotion endpoints

**Must not own:**
- Runtime execution logic
- Generic platform routing unless it is explicitly extending the platform layer through a stable contract

**Examples:**
- `studio_app.py`
- `mozaiks_app.py`
- AppGenerator workflows
- AgentGenerator workflows

### 4. Dev / CLI / Preview Layer

**Purpose:** Local-only convenience and development support.

**Owns:**
- CLI tools
- Local preview servers
- Repo path shortcuts
- Dev-only endpoints and probes
- Temporary or generated file access used only for local development workflows

This layer must not leak into runtime, platform, Studio, or Mozaiks product code.

### Decision Rules (MANDATORY)

When adding code, decide placement in this order:

1. Is this required for every runtime instance?
  - Put it in **Runtime**.
2. Is this about app hosting, routing, sessions, pages, or operations?
  - Put it in **Platform**.
3. Is this about building, generating, refining, or promoting apps or workflows?
  - Put it in **Studio** for local/private builder behavior.
  - Put it in **Mozaiks product** for hosted product behavior.
4. Is this only for local development, CLI usage, preview behavior, or repo structure?
  - Put it in **Dev / CLI / Preview**.

If the answer depends on local repo structure, build-time authoring flows, or preview-only behavior, it does not belong in reusable runtime code.

### Hard Anti-Leak Rules

**Never put these in Runtime:**
- `PLATFORM_PATH` resolution
- `mozaiks-platform/` references
- Shell config logic
- Studio routes
- Transition routing
- App manifest loading for app-host composition
- Generator-specific behavior
- Page or theme serving

**Do not let generators write into active runtime paths.**

Generated artifacts must go to:
- `artifacts/generated/`
- `artifacts/staged/`

Only promotion logic may write into active runtime roots.

**Do not rely on repo layout in reusable hosts.**

Avoid:
- `sys.path.insert(...)`
- Relative-path hacks
- Monorepo assumptions baked into runtime, platform, Studio, or Mozaiks product hosts

### Canonical Host State

Current state:

- `runtime_app.py` is the clean runtime substrate target
- `platform_app.py` is the platform/app-shell layer
- `studio_app.py` is the local/private builder host
- `studio_app.py` is the default local run target
- `mozaiks_app.py` is the hosted Mozaiks product host

### Migration Principle

All new work should:

- Prefer layered hosts (`runtime_app.py`, `platform_app.py`, `studio_app.py`, `mozaiks_app.py`)
- Avoid introducing new cross-layer dependencies

---

## First-Class vs App-Level

| First-Class (framework) | App-Level (platform/) |
|------------------------|----------------------|
| mozaiksai (AI runtime) | workflows |
| `AppBackendPort` + backend bridge tools | operations |
| chat-ui + app shell | pages |
| transport, artifacts, orchestration | brand/theme |
| framework defaults and CLI | config |

**First-class = every app gets it automatically. App-level = defined per app in `platform/`.**

### First-Class UI Surfaces

All five are registered in `chat-ui/src/registry/coreComponents.js` — every app gets them automatically, no platform/extensions.js wiring needed.

| Component | Route | Purpose |
|-----------|-------|---------|
| `ChatPage` | `/chat` | Main AI workflow interface |
| `SchemaPage` | `/{page}` | Renders declarative AppPageSchema from `/api/pages/{name}` |
| `AdminPortal` | `/admin` | Unified admin shell — app owner panels, module panels, and runtime/operator panels |
| `ProfilePage` | `/profile` | User profile view/edit — calls `app_backend_url/api/me` |

`AdminPortal` is one visible admin surface. It separates authority internally:
- **App admin panels** — app owner/user/subscription panels from `app_backend_url/api/admin/*`
- **Module admin panels** — panels declared by modules and rendered inside `/admin`
- **Runtime panels** — Mozaiks runtime/operator panels such as workflow runs, tokens, cost, and sessions

Panel lists are config-driven. The runtime reads `platform/config/admin.json` for
runtime/operator panels; the app backend exposes app and module panels via
`GET app_backend_url/api/admin/config`. Modules contribute panels through their
module admin contract and may register custom React components via
`platform/extensions.js`.

---

## Core Runtime and App Backend Boundary

### 1. mozaiksai/ — AI Workflow Runtime

**What it does:** Executes multi-agent AI workflows using AG2 (AutoGen).

**Key responsibilities:**
- Run AI agent conversations (GroupChat orchestration)
- Stream events to frontend via WebSocket
- Persist chat sessions to MongoDB
- Handle tool calls from agents
- Manage workflow state (in-progress, completed)
- Token accounting and observability

**Core files:**
- `mozaiksai/core/workflow/orchestration_patterns.py` — Main execution engine
- `mozaiksai/core/ports/orchestration.py` — Engine-agnostic contract (`OrchestrationPort`)
- `mozaiksai/core/transport/simple_transport.py` — WebSocket connection manager
- `mozaiksai/core/events/unified_event_dispatcher.py` — Event routing hub

**Event types (3 distinct systems):**
1. **Business events** — Observability/logging (`emit_business_event`)
2. **UI tool events** — Agent-to-UI communication (`emit_ui_tool_event`)
3. **AG2 runtime events** — Chat execution (`chat.text`, `chat.tool_call`, `chat.run_complete`)

**Multi-tenant scoping:** Every operation requires `app_id`, `user_id`, `chat_id`.

---

### 2. App backend integration boundary

**What it does:** Defines how deterministic, non-AI app behavior connects to
the AI runtime. The runtime only depends on the generic `AppBackendPort` contract —
the specific backend may be `mozaiks-core-public`, an existing product backend, or
any HTTP service.

**Reference implementation:** `mozaiks-core-public` is the canonical per-app
backend runtime. It provides auth, notifications, subscriptions, settings,
event bus, AI bridge, and a declarative plugin system out of the box.

**Typical app-backend responsibilities:**
- User settings, profiles, and preferences (exposed at `/api/me/*`)
- Deterministic business actions and persistence (plugin `backend/logic.py`)
- REST endpoint surface for AI agents (declared in `module.yaml`)
- Notifications, subscriptions, and app policy (plugin YAML manifests)
- Domain event emission → AI runtime workflow triggers (via `event_bridge`)

**Core contract files in this repo:**
- `mozaiksai/core/ports/app_backend.py` — `AppBackendPort`
- `mozaiksai/core/adapters/http_app_backend.py` — generic HTTP adapter
- `mozaiksai/core/workflow/app_backend_tools.py` — built-in workflow bridge tools

**Key integration points (mozaiks-core-public → mozaiksai):**
- `POST {MOZAIKSAI_RUNTIME_URL}/internal/trigger` — domain event fires a workflow
- `POST app_backend_url/api/ai/events` — mozaiksai pushes workflow results back
- `GET app_backend_url/api/operations/module-surface` — AppGenerator discovers callable endpoints

**Hard rule:** The runtime never imports app-backend internals or hardcodes
app-specific API paths. Paths are passed as arguments by workflow tools or agent context.

---

## How They Connect

```
Frontend (chat-ui / app shell)
    │
  ├── REST API ──────────────► mozaiks-core-public (CRUD, operations, admin)
  │                               │                │
  │                               │ event_bridge   │ /api/me, /api/admin
  │                               │                │ /api/operations/module-surface
  └── WebSocket / HTTP ─────► mozaiksai (AI workflows)
                │                │
                │                └─ POST /internal/trigger ─► mozaiksai
                ├── AG2 orchestration
                │
                └── AppBackendPort ─────► mozaiks-core-public
                                            (plugin routes matching module.yaml)
```

**app_backend_url:** Base URL of the paired mozaiks-core-public instance. Injected
into AppGenerator as a context variable. ServiceAgent and ControllerAgent include it
so generated code knows where to call.

**Persistence topology:** Deployment-specific. mozaiksai and mozaiks-core-public
may share MongoDB or run with separate databases.

**Boundary rule:** App facts cross the boundary as API calls or domain events,
not as in-repo imports.

---

## Distributed Event Model

Events are **distributed**, not centralized. No separate `automations/` directory.

### Where Events Are Declared

| Who | Declares | In File |
|-----|----------|---------|
| Operation (mozaiks-core-public) | Events it **publishes** | `operations/{name}/events.yaml` → `publishes` |
| Operation (mozaiks-core-public) | Workflow **triggers** | `operations/{name}/events.yaml` → `triggers` |
| Operation (mozaiks-core-public) | Notification **rules** | `operations/{name}/notifications.yaml` |
| Operation (mozaiksai platform) | Events it **emits** | `operation.yaml` → `events` |
| Operation (mozaiksai platform) | Events it **handles** | `operation.yaml` → `events` |
| Workflow | Events it **emits** | `orchestrator.yaml` → `events.emits` |
| Workflow | Events that **trigger** it | `orchestrator.yaml` → `triggers` |

### CRUD → AI (workflow triggers)

Workflows declare what app events start or resume them. In mozaiks-core-public,
the operation's `events.yaml` `triggers:` block is the source of truth — `event_bridge`
aggregates all triggers at startup and subscribes to the event bus automatically.
When an event fires, `event_bridge` calls `POST {MOZAIKSAI_RUNTIME_URL}/internal/trigger`.

```yaml
# mozaiks-core-public: operations/task_manager/events.yaml
publishes:
  - { name: task_created, aggregate_type: task }
triggers:
  - { event: task_created, workflow: TaskCreated }
```

```yaml
# mozaiks: platform/workflows/WritersRoom/orchestrator.yaml (mozaiksai platform side)
triggers:
  - event: set.brief_confirmed
    action: run
    when:
      payload.status: approved
    message_template: "Start the writers room for {payload.set_type}."
```

### AI → CRUD (event publishing)

Workflow agents talk back to the app backend through the built-in backend tools
or custom tools that use `AppBackendPort`.

```yaml
# tools.yaml
- name: backend_request
  type: Agent_Tool
  module: mozaiksai.core.workflow.app_backend_tools
  function: backend_request

- name: emit_event
  type: Agent_Tool
  module: mozaiksai.core.workflow.app_backend_tools
  function: emit_event
```

### Framework Aggregates at Runtime

The framework scans all `operation.yaml` and `orchestrator.yaml` files to build the routing table.

---

## platform/ Directory

Declarative configuration for the app. Both services read from it.

```
platform/
├── config/                     # Platform-wide settings
│   ├── ai.json                 # LLM provider, model, temperature
│   └── theme_config.json       # Color schemes, fonts, shell chrome
├── workflows/                  # AI workflow definitions (mozaiksai)
│   └── {WorkflowName}/
│       ├── orchestrator.yaml   # Config + triggers + events.emits
│       ├── agents.yaml         # Agent definitions
│       ├── tools.yaml          # Tool declarations
│       ├── tools/*.py          # Tool implementations
│       └── extended_orchestration/  # MFJ and pack extension config
│           └── mfj_extension.json   # Mid-Flight Journey fan-out/fan-in config (optional)
├── operations/                 # Deterministic CRUD/action operations
│   └── {operation_name}/
│       ├── operation.yaml      # Manifest + actions + events
│       ├── handler.py          # Operation handler class
│       ├── ui/                 # Optional: operation's default page UI
│       ├── models.py           # Optional: data models/schemas
│       └── services.py         # Optional: extracted business logic
├── pages/                      # Multi-operation pages only
│   └── {page_name}/
│       ├── page.json           # Route, uses multiple operations
│       └── ui/
└── brand/                      # Theme and visual assets
    ├── brand.json              # Logo, colors, fonts
    └── assets/                 # Images, icons
```

### workflow_graph.json vs triggers (DIFFERENT THINGS)

| File | Purpose | Scope |
|------|---------|-------|
| `extended_orchestration/mfj_extension.json` | Mid-Flight Journeys: fan-out/fan-in child workflows | Internal to one workflow |
| `triggers` in orchestrator.yaml | Events that START/RESUME a workflow | External, cross-workflow |

**Do not confuse them.** The pack graph defines "when Agent A produces structured output, spawn child workflow B". Triggers are "Event X → Start Workflow Y".

---

## Contracts

### Operation Contract

```python
# platform/modules/{name}/handler.py
class LineupBoardOperation:
    async def list(self, ctx, **params) -> list:
        return await list_items(ctx.app_id, **params)

    async def create(self, ctx, *, name: str, **params) -> dict:
        result = await create_item(name=name, **params)
        await ctx.emit("lineup.created", {"name": name})
        return result
```

### Operation Manifest (operation.yaml)

```yaml
name: lineup_board
version: "1.0"
description: Shows which sets are ready

actions:
  - name: list
    type: query
    description: List lineup items
  - name: create
    type: mutation
    description: Create a lineup item
    emits:
      - lineup.created

events:
  - lineup.created
  - lineup.updated
```

### Workflow Manifest (orchestrator.yaml)

```yaml
workflow_name: WritersRoom
max_turns: 30
human_in_the_loop: true
workflow_startup_mode: AgentDriven
initial_agent: WritersHostAgent

# Events this workflow emits (via tools)
events:
  emits:
    - type: set.direction_selected
      description: "Selected a comedic direction"
      payload: { set_id: string, direction: string }

# External triggers that start/resume this workflow
triggers:
  - event: set.brief_confirmed
    action: run
    when:
      payload.status: approved
    message_template: "Start writing for {payload.set_type}."
```

### Workflow Tool Contract

```python
# platform/workflows/{Name}/tools/some_tool.py
from mozaiksai.core.events.unified_event_dispatcher import emit_ui_tool_event

async def my_tool(context_variables=None, **kwargs) -> dict:
    # 1. Do work
    result = await do_something()

  # 2. Emit UI event (updates frontend in real-time)
  emit_ui_tool_event(context_variables or {}, "show_card", {"title": "Done!"})

  return {"status": "done", "result": result}
```

For app-backend mutations or app-domain event emission, prefer the built-in
tools in `mozaiksai.core.workflow.app_backend_tools` so workflow code stays
backend-agnostic.

---

## Mapping Traditional Development to Mozaiks

| Traditional Layer | Mozaiks Equivalent | Location |
|-------------------|-------------------|----------|
| Database/Schema | App-backend persistence | Deployment-specific |
| Config/Middleware | Platform config + runtime/backend config | `platform/config/` + environment |
| Models | Module models | `platform/modules/{name}/models.py` |
| Services | Module handler | `platform/modules/{name}/handler.py` |
| Controllers (AI) | Workflows | `platform/workflows/{name}/` |
| Controllers (CRUD) | Module handler | `platform/modules/{name}/handler.py` |
| Routes | App backend or runtime | external backend or `mozaiksai` |
| Entry Point | Framework/runtime host | `run_server.py` or deployment entrypoint |
| Frontend | Page schema | `platform/pages/` |

**Key insight:** Modules are your app's deterministic logic contract. Workflows are the AI
orchestration layer. The framework handles the runtime side of that boundary.

---

## Config Files

| File | Status | Notes |
|------|--------|-------|
| `platform/config/ai.json` | Keep | LLM provider, model, temperature |
| `platform/config/theme_config.json` | Keep | Color schemes, fonts, shell chrome |
| `platform/config/admin.json` | Keep (app-level) | Declares `admin_emails` and runtime/operator panels for the unified AdminPortal |

---

## Bundle Visibility

Every bundle (module, workflow, page) has a `visibility` field:

| Visibility | Who sees it | Example |
|------------|-------------|---------|
| `public` | All users | Chat workflows, public pages |
| `internal` | Authenticated users | User dashboard, settings |
| `admin` | Admin users only | Admin portal, observability |

---

## Key Concepts

### Workflow (mozaiksai)
A multi-agent AI conversation. Defined in `platform/workflows/`. Executed by AG2. Has agents, tools, handoff rules.

Workflows declare:
- `events.emits` — What events their tools publish
- `triggers` — What external events start/resume them

### Module (mozaiksai platform)
A unit of deterministic business logic. Defined in `platform/modules/`. Has a `handler.py`
with action methods and a `module.yaml` manifest. NOT an AI workflow.

Modules support workflows — they provide the CRUD surface that AI agents call through
the `AppBackendPort`. Modules declare:
- `actions` — Named action methods (list, create, update, delete)
- `events` — Domain events the module can emit

### Page (frontend)
A UI screen. Pages can bind to module routes via `/api/modules/<name>/<action>`. Use `platform/pages/` for all page schemas.

### Event
Three kinds exist (don't confuse them):
1. **App events** — Emitted by the app backend or runtime bridge tools, trigger workflow `triggers`
2. **UI events** — Published via `emit_ui_tool_event()`, update frontend in real-time
3. **Runtime events** — AG2 execution events (`chat.text`, `chat.tool_call`)

---

## Task Decomposition

When building features in Mozaiks, tasks decompose by feature (not by layer):

### Mozaiks Approach (feature-based)
```
1. Define module → platform/modules/{feature}/
   ├── module.yaml     # Metadata + actions + events
   ├── handler.py      # Handler class with action methods
   └── models.py       # Optional: data schemas

2. Define workflow (if AI needed) → platform/workflows/{Feature}/
   ├── orchestrator.yaml   # Config + triggers + events.emits
   ├── agents.yaml
   ├── tools.yaml
   └── tools/*.py

3. Standalone page (only if multi-operation) → platform/pages/{page}/
   └── ui/
```

### When to Use What

| Scenario | Where to Define |
|----------|-----------------|
| Operation with its own page | `operation.yaml` → `page` section |
| Page using multiple operations | `platform/pages/` |
| Page with no operation (static) | `platform/pages/` |
| Event that triggers workflow | `orchestrator.yaml` → `triggers` |
| Event emitted by operation | `operation.yaml` → `events` |
| Event emitted by workflow tool | `orchestrator.yaml` → `events.emits` |

---

## What NOT to Do

- Don't put AI workflow logic in the app backend
- Don't put CRUD/user-state logic in mozaiksai
- Don't hardcode workflow behavior in the runtime (use declarative configs)
- Don't add duplicate interfaces or aliases (make canonical changes)
- Don't confuse the framework with the generator
- Don't confuse `workflow_graph.json` (internal handoffs) with `triggers` (external events)

---

## Terminology

| Term | Meaning |
|------|---------|
| AI runtime | `mozaiksai` — workflow execution layer |
| app backend | external deterministic app service; reference implementation is `mozaiks-core-public` |
| AppBackendPort | generic contract in `mozaiksai` for AI runtime ↔ app backend communication |
| app_backend_url | base URL of the paired mozaiks-core-public instance; injected as a context variable |
| module (mozaiks-core-public) | self-contained capability unit — module manifests + `backend/logic.py` + `backend/routes.py` |
| module (mozaiksai platform) | deterministic CRUD/action surface declared in `platform/modules/` with `module.yaml` + `handler.py` |
| module manifest system | YAML manifest family for modules (`module.yaml`, `events.yaml`, `settings.yaml`, `notifications.yaml`, `subscriptions.yaml`, `admin.yaml`) |
| module.yaml (platform) | handler manifest for a mozaiksai platform module — identity, capabilities, actions, events |
| admin.yaml (platform) | module admin panels rendered inside unified `/admin` |
| event_bridge | component in mozaiks-core-public that wires module domain events to mozaiksai workflow triggers |
| triggers | workflow start/resume declarations — in `orchestrator.yaml` (mozaiksai) or `events.yaml` (modules) |
| AppGenerator | workflow that generates 6-file module manifests + backend code for each capability pack |

---

## Quick Reference

**Start a workflow:** WebSocket → mozaiksai → `run_workflow_orchestration()` → AG2 GroupChat

**Execute app logic:** REST → app backend → app endpoint or handler → persistence

**User settings:** REST → app backend → settings API → persistence

**Stream AI responses:** mozaiksai → `simple_transport.broadcast_event()` → WebSocket → frontend

**CRUD triggers AI:** app event emitted → runtime ingress matches → workflow `triggers` → run/resume

**AI triggers app behavior:** workflow agent → `backend_request()` or `emit_event()` → app backend

**App backend boundary:** `mozaiksai` talks to the app backend through `AppBackendPort`
