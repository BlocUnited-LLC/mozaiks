# Mozaiks Architecture

This is the authoritative architecture reference. If other docs contradict this file, this file wins.

## What This Repo Is

This repo is the **Mozaiks framework** — a runtime for executing AI workflows and serving app backends.

```
mozaiks/                           # This repo
├── mozaiksai/                     # AI workflow runtime (first-class)
├── mozaikscore/                   # App backend REST API (first-class)
├── chat-ui/                       # Chat interface (first-class)
├── platform/                      # Example app bundle / authoring contract
│   ├── workflows/                 # AI workflows (app-level)
│   ├── modules/                   # Business logic (app-level)
│   ├── pages/                     # UI screens (app-level)
│   │   ├── admin/                 # Admin dashboard (NOTE: should be first-class, currently here)
│   │   └── discover/              # Example page
│   ├── brand/                     # Theme and assets (app-level)
│   └── config/                    # Platform settings (app-level)
└── mozaiks-platform/              # Generator product placeholder (git-ignored)
```

**This repo does NOT contain:**
- The "AppGenerator" or "mozaiks-platform" product (it's a separate proprietary product)
- Any code generation system
- The managed platform at mozaiks.ai

If you're working in this repo, you're working on the **framework that runs apps**, not the tool that creates them.

---

## First-Class vs App-Level

| First-Class (framework) | App-Level (platform/) |
|------------------------|----------------------|
| mozaiksai (AI runtime) | workflows |
| mozaikscore (app backend) | modules |
| chat-ui (chat interface) | pages |
| auth system | brand/theme |
| event system | config |
| mozaiks-header | — (events distributed in modules/workflows) |

**First-class = every app gets it automatically. App-level = defined per app in `platform/`.**

### First-Class UI Surfaces

The framework provides these UI surfaces out of the box:

1. **chat-ui** — The main chat interface where users interact with AI workflows
2. **mozaiks-header** — Minimal persistent header with user menu, notifications bell, admin access

### Admin Dashboard (Current State)

**Location:** `platform/pages/admin/` (app-level, currently)

**Should be:** First-class framework component (like chat-ui)

**What it is:** Admin users get a dashboard showing:
- System health, rate limits, token usage
- Event bus stats, workflow triggers
- Entitlement decisions (access denials)
- Subscription/monetization controls

Think of it as the "admin user profile" — authenticated admins see this instead of (or in addition to) normal app pages.

Navigation is primarily **event-driven**. The header provides entry points; everything else opens via events (drawers, modals, page transitions).

---

## Two Backend Services

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

### 2. mozaikscore/ — App Backend

**What it does:** REST API for frontend. Handles everything that isn't AI workflow execution.

**Key responsibilities:**
- User settings, profiles, preferences
- Module discovery and execution (non-AI business logic)
- Subscription/monetization gating
- Notification delivery (in-app, email, WebSocket)
- App configuration (themes, navigation)
- Unified event bus (shared with mozaiksai)

**Core files:**
- `mozaikscore/core_app.py` — FastAPI app factory
- `mozaikscore/core/director.py` — Config, navigation, health routes
- `mozaikscore/core/module_manager.py` — Dynamic module loading
- `mozaikscore/core/event_bus.py` — Unified pub/sub event bus
- `mozaikscore/core/trigger_registry.py` — Workflow trigger subscriptions

**Key routes:**
- `/api/modules/*` — Module listing and execution
- `/api/settings/*` — User settings
- `/api/subscriptions/*` — Subscription management
- `/api/notifications/*` — Notification CRUD
- `/api/pages/*` — Page manifest discovery
- `/api/shell-config` — App shell configuration (navigation, theme)
- `/__mozaiks/admin/*` — Admin endpoints

---

## How They Connect

```
Frontend (chat-ui)
    │
    ├── REST API ──────────► mozaikscore (CRUD, config, modules)
    │                              │
    │                              ▼
    └── WebSocket ─────────► mozaiksai (AI workflows)
                                   │
                                   ▼
                              AG2 GroupChat
                                   │
                                   ▼
                              MongoDB (shared)

┌─────────────────────────────────────────────────────────┐
│                 Unified Event Bus                        │
│         (mozaikscore/core/event_bus.py)                 │
│                                                          │
│   event_bus.publish("set.brief_confirmed", {...})       │
│            ↓                                             │
│   trigger_registry matches workflow triggers             │
│            ↓                                             │
│   Workflow starts/resumes via WebSocket                  │
└─────────────────────────────────────────────────────────┘
```

**Shared database:** Both services use the same MongoDB instance.

**Unified event bus:** Both services import from `mozaikscore.core.event_bus`. No relay or bridge needed.

---

## Distributed Event Model

Events are **distributed**, not centralized. No separate `automations/` directory.

### Where Events Are Declared

| Who | Declares | In File |
|-----|----------|---------|
| Module | Events it **emits** | `module.json` → `events.emits` |
| Module | Events it **handles** | `module.json` → `events.handles` |
| Workflow | Events it **emits** | `orchestrator.yaml` → `events.emits` |
| Workflow | Events that **trigger** it | `orchestrator.yaml` → `triggers` |

### CRUD → AI (workflow triggers)

Workflows declare what events start/resume them:

```yaml
# platform/workflows/WritersRoom/orchestrator.yaml
triggers:
  - event: set.brief_confirmed
    action: run
    when:
      payload.status: approved
    message_template: "Start the writers room for {payload.set_type}."
```

### AI → CRUD (event publishing)

Workflow tools emit events via `event_bus.publish()`:

```python
# platform/workflows/GreenRoom/tools/persist_set_brief.py
from mozaikscore.core.event_bus import event_bus

async def persist_set_brief(context: dict, brief: dict) -> dict:
    # Save to database
    await save_brief(brief)

    # Emit event → triggers WritersRoom (via its triggers declaration)
    event_bus.publish("set.brief_confirmed", {
        "set_id": brief["id"],
        "status": "approved"
    })

    return {"status": "saved"}
```

### Framework Aggregates at Runtime

The framework scans all `module.json` and `orchestrator.yaml` files to build the routing table. No manual `routes.json` needed.

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
│       └── _pack/              # Internal workflow state (do not confuse with triggers)
│           └── workflow_graph.json  # Mid-Flight Journey fan-out/fan-in config
├── modules/                    # Business logic (mozaikscore)
│   └── {module_name}/
│       ├── module.json         # Manifest + events + optional page
│       ├── handler.py          # execute(data) entry point
│       ├── ui/                 # Optional: module's default page UI
│       ├── models.py           # Optional: data models/schemas
│       └── services.py         # Optional: extracted business logic
├── pages/                      # Multi-module pages only
│   └── {page_name}/
│       ├── page.json           # Route, uses multiple modules
│       └── ui/
└── brand/                      # Theme and visual assets
    ├── brand.json              # Logo, colors, fonts
    └── assets/                 # Images, icons
```

### workflow_graph.json vs triggers (DIFFERENT THINGS)

| File | Purpose | Scope |
|------|---------|-------|
| `_pack/workflow_graph.json` | Mid-Flight Journeys: fan-out/fan-in child workflows | Internal to one workflow |
| `triggers` in orchestrator.yaml | Events that START/RESUME a workflow | External, cross-workflow |

**Do not confuse them.** The pack graph defines "when Agent A produces structured output, spawn child workflow B". Triggers are "Event X → Start Workflow Y".

---

## Contracts

### Module Contract

```python
# platform/modules/{name}/handler.py
async def execute(data: dict) -> dict:
    """REQUIRED: The only mandatory contract."""
    action = data.get("action")

    if action == "list":
        return await list_items(data)
    if action == "create":
        return await create_item(data)

    return {"error": f"Unknown action: {action}"}
```

### Module Manifest (module.json)

```json
{
  "name": "lineup_board",
  "display_name": "Lineup Board",
  "description": "Shows which sets are ready",
  "version": "1.0.0",
  "enabled": true,
  "visibility": "internal",
  "required_tier": "free",

  "page": {
    "path": "/lineup",
    "component": "LineupPage",
    "ui": "ui/LineupPage.jsx",
    "show_in_header": true,
    "order": 30
  },

  "events": {
    "emits": [],
    "handles": [
      { "type": "set.finalized", "description": "Refresh when set finalized" }
    ]
  },

  "uses": {
    "workflows": ["MainStage"]
  }
}
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
from mozaikscore.core.event_bus import event_bus
from mozaiksai.core.events.unified_event_dispatcher import emit_ui_tool_event

async def my_tool(context: dict, **kwargs) -> dict:
    # 1. Do work
    result = await do_something()

    # 2. Emit app event (triggers other workflows via their triggers)
    event_bus.publish("set.brief_confirmed", {"set_id": "123", "status": "approved"})

    # 3. Emit UI event (updates frontend in real-time)
    emit_ui_tool_event(context, "show_card", {"title": "Done!"})

    return {"status": "done"}
```

---

## Mapping Traditional Development to Mozaiks

| Traditional Layer | Mozaiks Equivalent | Location |
|-------------------|-------------------|----------|
| Database/Schema | MongoDB | Framework (shared) |
| Config/Middleware | Platform config + framework | `platform/config/` |
| Models | Module models | `platform/modules/{name}/models.py` |
| Services | Module handler | `platform/modules/{name}/handler.py` |
| Controllers (AI) | Workflows | `platform/workflows/{name}/` |
| Controllers (CRUD) | Module execute() | `platform/modules/{name}/handler.py` |
| Routes | Framework | mozaikscore provides |
| Entry Point | Framework | mozaikscore provides |
| Frontend | Module page or standalone page | `module.json` → `page` or `platform/pages/` |

**Key insight:** Modules are your "services layer" — they contain business logic with an `execute(data)` entry point. Workflows are the AI orchestration layer. The framework handles everything else.

---

## Config Files (what stays, what's gone)

| File | Status | Notes |
|------|--------|-------|
| `ai.json` | Keep | LLM provider, model, temperature |
| `theme_config.json` | Keep | Color schemes, fonts, shell chrome |
| `subscription_config.json` | **Removed** | Belongs in greenfield app backend |
| `navigation_config.json` | **Removed** | Shell config derived from ai.json |
| `settings_config.json` | **Removed** | Belongs in greenfield app backend |
| `notifications_config.json` | **Removed** | Belongs in greenfield app backend |
| `admin.json` | **Removed** | Belongs in greenfield app backend |
| `module_registry.json` | **Removed** | Belongs in greenfield app backend |
| `automations/routes.json` | **Removed** | Triggers are in orchestrator.yaml |
| `automations/event_catalog.json` | **Removed** | Events declared in modules/workflows |

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

### Module (mozaikscore)
A unit of business logic. Defined in `platform/modules/`. Has a `handler.py` with an `execute(data)` function. NOT an AI workflow.

Modules can declare:
- `events.emits` — What events they publish
- `events.handles` — What events they react to
- `page` — Optional inline page definition (1:1 module→page)

### Page (frontend)
A UI screen. For modules with a single page, use `module.json` → `page`. For pages using multiple modules or no module, use `platform/pages/`.

### Event
Three kinds exist (don't confuse them):
1. **App events** — Published via `event_bus.publish()`, trigger workflow `triggers`
2. **UI events** — Published via `emit_ui_tool_event()`, update frontend in real-time
3. **Runtime events** — AG2 execution events (`chat.text`, `chat.tool_call`)

---

## Task Decomposition

When building features in Mozaiks, tasks decompose by feature (not by layer):

### Mozaiks Approach (feature-based)
```
1. Define module → platform/modules/{feature}/
   ├── module.json    # Metadata + events + optional page
   ├── handler.py     # execute(data) with actions
   ├── ui/            # Optional: page UI
   └── models.py      # Optional: data schemas

2. Define workflow (if AI needed) → platform/workflows/{Feature}/
   ├── orchestrator.yaml   # Config + triggers + events.emits
   ├── agents.yaml
   ├── tools.yaml
   └── tools/*.py

3. Standalone page (only if multi-module) → platform/pages/{page}/
   ├── page.json      # uses: {modules: [...]}
   └── ui/
```

### When to Use What

| Scenario | Where to Define |
|----------|-----------------|
| Module with its own page | `module.json` → `page` section |
| Page using multiple modules | `platform/pages/` |
| Page with no module (static) | `platform/pages/` |
| Event that triggers workflow | `orchestrator.yaml` → `triggers` |
| Event emitted by module | `module.json` → `events.emits` |
| Event emitted by workflow tool | `orchestrator.yaml` → `events.emits` |

---

## What NOT to Do

- Don't put AI workflow logic in mozaikscore
- Don't put CRUD/user-state logic in mozaiksai
- Don't hardcode workflow behavior in the runtime (use declarative configs)
- Don't add duplicate interfaces or aliases (make canonical changes)
- Don't confuse the framework with the generator
- Don't confuse `workflow_graph.json` (internal handoffs) with `triggers` (external events)

---

## Terminology

| Current Term | Meaning |
|--------------|---------|
| app backend | mozaikscore services and deterministic application logic |
| AI runtime | mozaiksai workflow execution layer |
| unified event bus | shared in-process event transport |
| module | deterministic app capability surface |
| triggers | workflow start or resume declarations in `orchestrator.yaml` |

---

## Quick Reference

**Start a workflow:** WebSocket → mozaiksai → `run_workflow_orchestration()` → AG2 GroupChat

**Execute a module:** REST → mozaikscore → `module_manager.execute_module()` → `handler.execute()`

**User settings:** REST → mozaikscore → `/api/settings/*` → MongoDB

**Stream AI responses:** mozaiksai → `simple_transport.broadcast_event()` → WebSocket → frontend

**CRUD triggers AI:** `event_bus.publish()` → `trigger_registry` matches → workflow `triggers` → run/resume

**AI triggers CRUD:** workflow tool → `event_bus.publish()` → module `events.handles`

**Unified event bus:** Both services import `from mozaikscore.core.event_bus import event_bus`
