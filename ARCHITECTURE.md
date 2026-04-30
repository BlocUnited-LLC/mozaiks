# Mozaiks Architecture

This is the authoritative architecture reference. If other docs contradict this file, this file wins.

## What This Repo Is

This repo is the **canonical Mozaiks repo** — the AI runtime, app host,
frontend surfaces, Studio management/create control plane, hosted product shell, and generated app
artifact contracts for Mozaiks apps.

Deterministic app behavior is owned by app bundles hosted by `mozaiksai.hosts.platform`, generated module contracts, or optional external/generated app backends connected through the generic `AppBackendPort` contract in `mozaiksai/core/ports/app_backend.py`.

```text
mozaiks/                           # This repo (current transitional layout)
├── mozaiksai/                     # AI workflow runtime (first-class)
├── chat-ui/                       # Chat interface primitives (first-class)
├── web_shell/                     # Local Vite shell host source
├── factory_app/                   # First-party factory workspace
│   └── app/
│       ├── app.json
│       ├── brand/
│       ├── config/
│       ├── modules/
│       ├── ui/
│       └── workflows/
├── generated/                     # Staged generated apps/workflows awaiting promotion
├── mozaiks_cli/                   # CLI for local project and workflow work
├── mozaiksai/hosts/runtime.py                 # Runtime substrate host
├── mozaiksai/hosts/platform.py                # Headless app host
├── mozaiksai/hosts/studio.py                  # Local/private Studio management/create host
├── mozaiksai/hosts/mozaiks.py                 # Hosted Mozaiks product host
└── mozaiks-platform/              # App Zero / product workspace
    ├── app/                       # Active App Zero app root
    │   ├── app.json
    │   ├── brand/
    │   ├── config/
    │   ├── modules/
    │   ├── ui/
    │   └── workflows/
    │       └── extended_orchestration/
    │           └── extension_registry.json
    └── app-builder/               # Builder planning/docs; not runtime-loaded
```

## Canonical Target

The repo above is the current implementation state, not the desired end-state
for Mozaiks distribution.

The canonical target architecture is:

1. **Mozaiks runtime package** — reusable Python dependency
2. **Shared web shell package** — reusable frontend dependency
3. **Factory app workspace** — first-party builder workflows and artifact assembly logic
4. **App workspaces** — standalone app repositories
5. **App Zero / Mozaiks product workspace** — same workspace contract, plus
   hosted-only capabilities

Generated apps should become their own workspaces/repositories. They should not
live permanently inside this repo.

Canonical target workspace shape:

```text
my-app/
└── app/
    ├── app.json
    ├── config/
    ├── ui/
    ├── workflows/
    ├── modules/
    └── brand/
```

That means:

- the first-party factory layer now lives under `factory_app/`
- `ui/` and `brand/` belong inside the app workspace in the canonical target
- App Zero now uses the same self-contained app-root contract inside `mozaiks-platform/app`

See [docs/architecture/foundations/distribution-and-workspace-model.md](docs/architecture/foundations/distribution-and-workspace-model.md)
for the authoritative target model.

**Primary boundary for this repo:**
- `mozaiksai.hosts.runtime` is the reusable execution substrate.
- `mozaiksai.hosts.platform` is the canonical headless app host for pages, modules,
  shell config, admin, actions, and app routing.
- `mozaiksai.hosts.studio` is the Studio management interface host — the shared management layer used by both local and hosted deployments.
- `mozaiksai.hosts.mozaiks` is the hosted Mozaiks product host — extends Studio with hosted-only capabilities.
- `mozaiks-platform/app` is the current App Zero app root when running the
  Mozaiks product locally. The parent `mozaiks-platform/` is the product
  workspace around that app root and now mainly owns planning assets such as
  `app-builder/`.

If you're working in this repo, you may be working in one of three modes:

1. **Framework/platform mode** — working on `mozaiksai.hosts.runtime`,
   `mozaiksai.hosts.platform`, `mozaiksai/`, `chat-ui/`, `web_shell/`, or App Zero's
   local app root in `mozaiks-platform/app/`
2. **Studio mode** — working in `mozaiksai.hosts.studio`, `factory_app/app/ui/studio/`,
  `factory_app/app/modules/factory_control_plane/`, or `chat-ui/src/admin/`
  for the shared management interface, refinement control plane, AppGenerator,
  or AgentGenerator workflows
3. **Mozaiks App / product mode** — working in `mozaiksai.hosts.mozaiks` or
   `mozaiks-platform/` for App Zero, hosted product surfaces, and
   product-only extensions

---

## Runtime Layering & Separation of Concerns (CRITICAL)

This repo now uses layered FastAPI hosts as the canonical runtime architecture.

This section defines the canonical runtime boundaries.
When writing or modifying code, these rules take precedence over legacy patterns.

### Layer Model

The system is composed of the following layers:

1. **Runtime** — AI substrate
2. **Platform** — app shell / app host layer
3. **Factory** — builder / generator layer
4. **Studio** — shared management interface (local and hosted)
5. **Mozaiks App** — hosted product layer, extends Studio
6. **CLI** — developer interface, parallel to Studio

Dependency direction flows upward through stable contracts:

```
Runtime (AI substrate)
   <- Platform (app shell / app host)
      <- Factory (builder / generator)
         <- Studio (management interface)
            <- Mozaiks App (hosted product)

CLI and Studio are parallel interfaces over shared system capabilities.
CLI is the developer interface — filesystem, scaffolding, and process management.
Studio is the management interface — workspace status, build lifecycle, artifacts,
run history, and configuration.
Mozaiks App extends Studio with hosted-only capabilities (collaboration, billing,
marketplace, deployment controls).
CLI must not become a dependency of Studio, Platform, or Runtime.

### Universal Substrate Versus Framework Capabilities

Not every first-class capability in this repo is a universal app-runtime
primitive.

- **Universal substrate** is the code every downstream app runtime depends on to
  execute: runtime, platform host, and the core shell primitives.
- **Framework-owned optional capabilities** are shipped by Mozaiks and may be
  first-class in the distribution, but not every downstream app must include
  them at runtime.

In practice:

- `mozaiksai/`, `mozaiksai.hosts.runtime`, `mozaiksai.hosts.platform`, and core `chat-ui/`
  primitives are universal substrate.
- `factory_app/`, including shared create/refinement routing helpers, Studio,
  and CLI are framework-owned capabilities layered above the substrate.
- `mozaiksai.hosts.mozaiks` and `mozaiks-platform/` are product/workspace consumers of
  those lower layers.

That means Studio and `factory_app/` are allowed to be first-class in this
repo without becoming mandatory runtime content for every generated app.

See
[docs/architecture/foundations/framework-capability-classification.md](docs/architecture/foundations/framework-capability-classification.md)
for the stricter classification and dependency rules.
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
- Studio or create routes
- Admin app-shell routes or product-shell UI composition
- Platform workflow ordering or app-host composition logic
- Generator or refinement behavior
- Repo-layout assumptions such as `mozaiks-platform/app`
- CLI conveniences, preview helpers, or other local-only behavior

**Examples:**
- `mozaiksai.hosts.runtime`
- `mozaiksai/core/transport/*`
- `mozaiksai/core/workflow/*`
- `mozaiksai/core/events/*`

### 2. Platform (App Shell / App Host)

**Purpose:** Hosts and composes an app on top of the runtime substrate.

**Owns:**
- Chat and session APIs such as `/api/chats/*`
- Shell config, theme config, and page serving
- Host-owned account/profile APIs such as `/api/me` and `/api/me/preferences`
- Transitions and routing composition
- Session UX concerns such as resume, metadata, and lifecycle
- Module/action execution and app-host integration points
- Admin and app-shell routes
- Workflow discovery, ordering, and app-level composition

**Must not own:**
- Orchestration engine internals
- Transport internals
- Event dispatch internals
- Generator or build logic
- Module-specific business records that only resemble profiles, such as investor
  profiles, seller profiles, creator profiles, or other domain entities

**Examples:**
- `mozaiksai.hosts.platform`

### 3. Studio Layer (Shared Management Interface)

**Purpose:** The management interface for any Mozaiks workspace — local or hosted.
Studio is not the CLI's UI layer. CLI and Studio are parallel interfaces over
shared system capabilities. Studio owns the management surface; CLI owns
developer tooling (filesystem, scaffolding, process management).

**Owns:**
- Workspace status and readiness surfaces
- Build lifecycle: request drafting, workflow routing, generation triggers
- Artifact lifecycle: generated → staged → promoted
- Run history, logs, and validation status
- Diff viewer and promotion controls
- Adapter and API key configuration
- Workflow and module inspection
- Platform-management surfaces including the admin portal
- Local preview controls

**Must not own:**
- Hosted-only assumptions (multi-user collaboration, remote deployment, billing)
- Runtime execution logic
- CLI-only concerns (filesystem scaffolding, process management)

**Examples:**
- `mozaiksai.hosts.studio`
- `factory_app/app/ui/studio/`
- `factory_app/app/modules/factory_control_plane/`
- `chat-ui/src/admin/` (admin portal is a platform-management surface)
- shared factory workflows in `factory_app/app/workflows/`

### 4. Mozaiks App Layer (Hosted Product)

**Purpose:** Extends Studio with hosted-only capabilities. Not a separate product
from Studio — an extension layer on top of it.

**Owns:**
- Collaboration: shared workspaces, comments, presence
- Billing and subscriptions
- Marketplace: template discovery, publishing, install
- Deployment controls: hosted environment management, domain config
- Team and org management
- Hosted preview controls (distinct from local preview because they involve remote infra)
- Any feature that requires a persistent server-side user account beyond session scope

**Must not own:**
- Anything that belongs in Studio — Mozaiks App inherits Studio, it does not fork it
- Runtime execution logic

**Examples:**
- `mozaiksai.hosts.mozaiks`
- `mozaiks-platform/app/ui/` (product UI extensions)

**Bundle generation boundary:**

Shared factory workflows live in `factory_app/app/workflows/`. The
shared build journeys, transitions, and transition UI live under
`factory_app/app/workflows/extended_orchestration/`. App Zero keeps only a
product overlay in
`mozaiks-platform/app/workflows/extended_orchestration/extension_registry.json`.
That overlay may add product-owned workflows, but the shared builder routing
contract and transition UI are not App Zero-owned.

Workflow loading is multi-root by contract:

- active app root `workflows/` first
- shared `factory_app/app/workflows/` second
- `MOZAIKS_WORKFLOW_ROOTS` may override that order explicitly

That is what lets App Zero keep product workflows under
`mozaiks-platform/app/workflows/` while still referencing shared generation-core
workflow IDs in its local launcher graph.

App Zero consumes the factory layer by composition, not by copying shared
builder logic into `mozaiks-platform/`:

- the active app workspace is resolved from `PLATFORM_PATH` or
  `MOZAIKS_APP_WORKSPACE_PATH` when provided
- the active app root's `workflows/` load first
- `factory_app/app/workflows/` loads second as the shared builder layer
- App Zero keeps only its product-owned overlay registry and product workflows

- `AppGenerator` owns deterministic app bundle artifacts: `app.json`,
  `ui/pages/*.yaml`, `config/*`, `brand/*`, and module contract files.
- `AgentGenerator` owns agentic augmentation artifacts:
  `workflows/{WorkflowName}/*.yaml`, workflow-local tools, hooks, and UI tool
  surfaces.
- Generator tools write into `MOZAIKS_GENERATED_ARTIFACTS_PATH`.
- Promotion is the only path from generated artifacts into an active app root
  such as `platform/` or `mozaiks-platform/app`.

**App Zero product module direction:**

`mozaiks-platform/app/modules/` is not a generic app-project bookkeeping area.
It is the hosted Mozaiks product's deterministic business layer. The canonical
App Zero modules are:

- `investor_marketplace` — marketplace listings, investor profiles, and
  investment-interest capture
- `communications` — hosted conversations, announcements, and marketplace/app
  owner messaging

These modules publish hosted product events such as `hosted.marketplace.*` and
`hosted.communication.*`. They may be used by Studio and Mozaiks product
surfaces, but they must not become runtime-kernel assumptions and they must not
be copied into generated OSS app bundles unless explicitly selected as hosted
capability packs.

### 5. CLI / Developer Interface Layer

**Purpose:** Developer tooling. Parallel to Studio, not beneath it. CLI and Studio
are different interfaces to shared system capabilities — they are not a chain where
Studio is the UI for CLI. CLI owns filesystem and process concerns; Studio owns
the management interface.

**Owns:**
- Scaffold generation: `mozaiks init`, `mozaiks onboard`, `mozaiks add`
- Process management: starting/stopping the local server
- Workspace diagnostics: `mozaiks studio` (terminal status, not a Studio replacement)
- Offline generation: `mozaiks gen` (terminal-initiated AI generation)
- Repo path shortcuts and dev-only probes

**Must not own:**
- Management state (run history, artifacts, generation status) — these belong in Studio
- Any UI surface that Studio already provides
- Assumptions about persistent user accounts

**Explicit scope limit on `mozaiks gen`:**
`mozaiks gen` is a developer convenience for bootstrapping from a terminal prompt.
It is not the canonical build lifecycle. The CLI must not expand `gen` or any other
command to duplicate:
- artifact review or diff
- run history or build state
- validation or promotion
- any surface that Studio already owns

If generation output needs review, diffing, or promotion, the path is Studio.
The CLI hands off — it does not grow a parallel project-management surface.

**Must not leak into:** Runtime, Platform, Studio, or Mozaiks App code.

### Decision Rules (MANDATORY)

When adding code, decide placement in this order:

1. Is this required for every runtime instance?
   - Put it in **Runtime**.
2. Is this about app hosting, routing, sessions, pages, or modules?
   - Put it in **Platform**.
3. Is this about workspace management, build lifecycle, artifact review, run history, or configuration?
   - Put it in **Studio** (shared management layer).
4. Is this a hosted-only capability (collaboration, billing, marketplace, deployment)?
   - Put it in **Mozaiks App**.
5. Is this filesystem scaffolding, process management, or a terminal diagnostic?
   - Put it in **CLI**.

Key distinctions:
- Studio and CLI are parallel — a feature is not CLI just because it runs locally.
  If it's management UI, it belongs in Studio regardless of environment.
- Mozaiks App extends Studio. Never fork Studio behavior for hosted mode;
  extend it through the `@platform/extensions` mechanism.
- `chat-ui/` is the substrate. Studio and Mozaiks App register extensions into it;
  they do not add core primitives to it.

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

Generator output must go under `MOZAIKS_GENERATED_ARTIFACTS_PATH`, which
defaults to:

- `generated/`

Current generator outputs:

- AppGenerator app schemas and bundle files:
  `generated/apps/{app_id}/{build_id}/app/`
- AgentGenerator workflow bundles:
  `generated/workflows/{app_id}/{build_id}/{workflow_name}/`

Only promotion logic may write into active runtime roots.

**Do not rely on repo layout in reusable hosts.** Prefer explicit app-root
inputs via `PLATFORM_PATH` or `MOZAIKS_APP_WORKSPACE_PATH` when composing the
core repo against an external app workspace.

Avoid:
- `sys.path.insert(...)`
- Relative-path hacks
- Monorepo assumptions baked into runtime, platform, Studio, or Mozaiks product hosts

### Canonical Host State

Current state:

- `mozaiksai.hosts.runtime` is the clean runtime substrate target
- `mozaiksai.hosts.platform` is the platform/app-shell layer
- `mozaiksai.hosts.studio` is the local/private Studio management/create host
- `mozaiksai.hosts.mozaiks` is the hosted Mozaiks product host

### Migration Principle

All new work should:

- Prefer layered hosts (`mozaiksai.hosts.runtime`, `mozaiksai.hosts.platform`, `mozaiksai.hosts.studio`, `mozaiksai.hosts.mozaiks`)
- Avoid introducing new cross-layer dependencies

---

## First-Class vs App-Level

| First-Class (framework) | App-Level (app workspace) |
|------------------------|----------------------|
| mozaiksai (AI runtime) | workflows |
| `AppBackendPort` + backend bridge tools | optional external/generated backend integration |
| chat-ui + app shell | pages |
| transport, artifacts, orchestration | brand/theme |
| framework defaults and CLI | config |

**First-class = every app gets it automatically. App-level = defined per app in an `app/` workspace root.**

### First-Class UI Surfaces

These are registered in `chat-ui/src/registry/coreComponents.js` — every app gets them automatically, regardless of which platform or Studio is loaded.

| Component | Route | Purpose |
|-----------|-------|---------|
| `ChatPage` | `/chat` | Main AI workflow interface |
| `SchemaPage` | `/{page}` | Renders declarative AppPageSchema from `/api/pages/{name}` |
| `ProfilePage` | `/profile` | User profile view/edit — calls host `/api/me` and `/api/me/preferences` |

### Platform-Management Surfaces

The admin portal and Studio pages are **not** core `chat-ui` primitives. They are
platform-management surfaces registered by Studio and inherited by the Mozaiks App.
Every app that uses Studio gets them; apps that do not use Studio do not.

Registered in `factory_app/app/ui/studio/index.js` via `registerStudioComponents()` and loaded by the shell through `@studio/extensions`:

| Component | Route | Purpose |
|-----------|-------|---------|
| `AdminPortal` | `/admin` | Unified admin shell — app owner panels, module panels, and runtime/operator panels |
| `StudioPage` | `/studio/*` | Studio management interface — workspace status, build, adapters |

`AdminPortal` separates authority internally:
- **App-business admin panels** — optional panels from `app_backend_url/api/admin/*` using `mozaiks.admin.app_backend.v1`
- **Module admin panels** — panels declared by modules and rendered inside `/admin`
- **Runtime panels** — Mozaiks runtime/operator panels such as workflow runs, tokens, cost, and sessions

Panel lists are config-driven. Runtime/operator panels live in
`app/config/admin.json`. In this repo, App Zero keeps that file at
`mozaiks-platform/app/config/admin.json`. A connected app backend may expose app-business panels via
`GET app_backend_url/api/admin/config` using `mozaiks.admin.app_backend.v1`. Modules contribute panels through their
module admin contract and may register custom React components via
the active app root's `ui/index.js` extension barrel.

### Hosted-Product UI Extensions

Hosted-only surfaces are registered via the `@platform/extensions` mechanism in
`mozaiks-platform/app/ui/`. Studio components are inherited automatically; extension
components add Mozaiks-App-specific pages (marketplace, billing, collaboration)
on top without forking the Studio layer.

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

**Multi-tenant scoping:** Every runtime action requires `app_id`, `user_id`, `chat_id`.

---

### 2. App backend integration boundary

**What it does:** Defines how deterministic, non-AI app behavior connects to
the AI runtime. The runtime only depends on the generic `AppBackendPort` contract —
the specific backend may be an app bundle hosted by `mozaiksai.hosts.platform`, generated
module handlers, an existing product backend, or any HTTP service.

The canonical implementation path lives in this repo:
module contracts, app-host routing, and generated app bundles are owned here.

**Typical app-backend responsibilities:**
- User settings, profiles, and preferences (exposed at `/api/me/*`)
- Deterministic business actions and persistence (module `backend/handler.py`)
- REST/action surface for AI agents (declared in `module.yaml`)
- Notifications, subscriptions, settings, and app policy (module YAML manifests)
- Domain event emission → AI runtime workflow triggers (through runtime ingress)

**Core contract files in this repo:**
- `mozaiksai/core/ports/app_backend.py` — `AppBackendPort`
- `mozaiksai/core/adapters/http_app_backend.py` — generic HTTP adapter
- `mozaiksai/core/workflow/app_backend_tools.py` — built-in workflow bridge tools

**Key integration points (app backend or platform host → runtime):**
- runtime ingress endpoint — domain event fires or resumes a workflow
- `AppBackendPort.emit()` — workflow result or app fact is emitted to an
  external/generated backend when one is configured
- module/action discovery — AppGenerator discovers callable module capabilities
  from the in-repo module contract system

**Hard rule:** The runtime never imports app-backend internals or hardcodes
app-specific API paths. Paths are passed as arguments by workflow tools or agent context.

---

## How They Connect

```
Frontend (chat-ui / app shell)
    │
  ├── REST API ──────────────► mozaiksai/hosts/platform.py (pages, modules, admin, shell)
  │                               │
  │                               └─ optional AppBackendPort ─► external/generated backend
  │
  └── WebSocket / HTTP ─────► mozaiksai/hosts/runtime.py / mozaiksai (AI workflows)
                               │
                               ├── AG2 orchestration
                               └── workflow tools ─► module actions or AppBackendPort
```

**app_backend_url:** Optional base URL of an external/generated deterministic
backend. It is not tied to a specific repo. Generated workflows may use it when
the app explicitly chooses a split backend topology.

**Persistence topology:** Deployment-specific. Runtime, platform host, and any
external/generated backend may share infrastructure or run with separate stores.

**Boundary rule:** App facts cross the boundary as API calls or domain events,
not as in-repo imports.

---

## Distributed Event Model

Events are **distributed**, not centralized. No separate `automations/` directory.

### Where Events Are Declared

| Who | Declares | In File |
|-----|----------|---------|
| Module | Events it **publishes** | `modules/{name}/events.yaml` |
| Module | Event reactions/subscriptions | `modules/{name}/subscriptions.yaml` |
| Module | Notification **rules** | `modules/{name}/notifications.yaml` |
| Module | Admin panels | `modules/{name}/admin.yaml` |
| Workflow | Events it **emits** | `orchestrator.yaml` → `events.emits` |
| Workflow | Events that **trigger** it | `orchestrator.yaml` → `triggers` |

### CRUD → AI (workflow triggers)

Workflows declare what app events start or resume them. Modules declare the
domain events they publish and any app-level reactions they own. The platform
host/runtime ingress resolves event facts to workflow triggers; modules do not
encode AG2/groupchat semantics directly.

```yaml
# modules/task_manager/events.yaml
schema_version: mozaiks.events.v1
events:
  - type: domain.task_manager.task_created
    version: 1
    producer: task_manager
```

```yaml
# mozaiks: app/workflows/WritersRoom/orchestrator.yaml
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

The framework scans module contracts and `orchestrator.yaml` files to build the routing table.

---

## App Zero Workspace

`mozaiks-platform/app/` is the repo-local App Zero app workspace.
`mozaiksai.hosts.platform` composes these surfaces, and `mozaiksai.hosts.runtime` executes the
workflow subset through stable contracts.

```
mozaiks-platform/app/
├── config/                     # Platform-wide settings
│   ├── ai.json                 # LLM provider, model, temperature
│   └── shell.json              # Header/footer/profile/notification chrome
├── workflows/                  # AI workflow definitions (mozaiksai)
│   └── {WorkflowName}/
│       ├── orchestrator.yaml   # Config + triggers + events.emits
│       ├── agents.yaml         # Agent definitions
│       ├── tools.yaml          # Tool declarations
│       ├── tools/*.py          # Tool implementations
│       └── extended_orchestration/  # MFJ and pack extension config
│           └── mfj_extension.json   # Mid-Flight Journey fan-out/fan-in config (optional)
├── modules/                    # Deterministic hosted-product capabilities
│   └── {module_name}/
│       ├── module.yaml         # Identity + actions + capabilities
│       ├── events.yaml         # Domain events this module may publish
│       ├── subscriptions.yaml  # Event reactions owned by this module
│       ├── notifications.yaml  # Notification rules
│       ├── settings.yaml       # User/app settings schema
│       ├── admin.yaml          # Admin panels mounted into /admin/*
│       ├── backend/
│       │   ├── handler.py      # Module handler class
│       │   ├── models.py       # Optional: data models/schemas
│       │   └── services.py     # Optional: extracted business logic
│       └── ui/                 # Optional: module-specific UI surfaces
├── pages/                      # Declarative app pages
│   ├── {page_name}.yaml        # Preferred page schema form
│   └── {page_name}/
│       └── page.yaml           # Optional folder form
├── ui/                         # Bounded extension barrel
│   ├── route_manifest.json     # Custom full-page route ownership metadata
│   ├── pages/
│   │   └── custom/             # Optional hand-authored full-page React routes
│   └── index.js                # React registration barrel
└── brand/                      # Theme and visual assets
    ├── theme_config.json       # Logo, colors, fonts
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

### Module Contract

```python
# app/modules/{name}/backend/handler.py
class LineupBoardModule:
    async def list(self, ctx, **params) -> list:
        return await list_items(ctx.app_id, **params)

    async def create(self, ctx, *, name: str, **params) -> dict:
        result = await create_item(name=name, **params)
        await ctx.emit("lineup.created", {"name": name})
        return result
```

### Module Manifest (module.yaml)

```yaml
schema_version: mozaiks.module.v1
module:
  id: lineup_board
  display_name: Lineup Board
  version: "1.0.0"
  description: Shows which sets are ready
  handler: backend.handler:LineupBoardModule

actions:
  - id: list
    type: query
    handler_method: list
    description: List lineup items
  - id: create
    type: mutation
    handler_method: create
    description: Create a lineup item
    emits:
      - domain.lineup_board.created
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
# app/workflows/{Name}/tools/some_tool.py
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
| Config/Middleware | App workspace config + runtime/backend config | `app/config/` + environment |
| Models | Module models | `app/modules/{name}/backend/models.py` |
| Services | Module handler or services | `app/modules/{name}/backend/handler.py`, `app/modules/{name}/backend/services.py` |
| Controllers (AI) | Workflows | `app/workflows/{name}/` or shared generation-core workflows |
| Controllers (CRUD) | Module handler | `app/modules/{name}/backend/handler.py` |
| Routes | Platform host or optional external backend | `mozaiksai.hosts.platform` or configured backend URL |
| Entry Point | Framework/runtime host | `mozaiks serve .` or deployment entrypoint |
| Frontend | Page schema | `app/ui/pages/` |

**Key insight:** Modules are your app's deterministic logic contract. Workflows are the AI
orchestration layer. The framework handles the runtime side of that boundary.

---

## Config Files

| File | Status | Notes |
|------|--------|-------|
| `app/config/ai.json` | Keep | LLM provider, model, temperature |
| `app/brand/theme_config.json` | Keep | Color schemes, fonts, shell chrome |
| `app/config/admin.json` | Keep (app-level) | Declares `admin_emails` and runtime/operator panels for the unified AdminPortal |

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

### Workflow
A multi-agent AI conversation. Defined in `app/workflows/` for app-owned
workflows, or in the shared generation core for builder workflows. Executed by
AG2. Has agents, tools, handoff rules.

Workflows declare:
- `events.emits` — What events their tools publish
- `triggers` — What external events start/resume them

### Module
A unit of deterministic business logic. Defined in `app/modules/`. Has a
`backend/handler.py` with action methods and a `module.yaml` manifest. NOT an
AI workflow.

Modules support workflows — they provide the CRUD/action surface that AI agents
call through platform module routes or, in split deployments, through
`AppBackendPort`. Modules declare:
- `actions` — Named action methods (list, create, update, delete)
- `events` — Domain events the module can emit

### Page (frontend)
A UI screen. Pages can bind to module routes via `/api/modules/<name>/<action>`.
Use `app/ui/pages/` for all declarative page schemas.
Use `app/ui/route_manifest.json` + `app/ui/pages/custom/` only for rare hand-authored full-page React escapes.

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
1. Define module → app/modules/{feature}/
   ├── module.yaml
   ├── events.yaml
   ├── settings.yaml
   ├── subscriptions.yaml
   ├── notifications.yaml
   ├── admin.yaml
   └── backend/
       ├── handler.py      # Handler class with action methods
       └── models.py       # Optional: data schemas

2. Define workflow (if AI needed) → app/workflows/{Feature}/
   ├── orchestrator.yaml   # Config + triggers + events.emits
   ├── agents.yaml
   ├── tools.yaml
   └── tools/*.py

3. Standalone page → app/ui/pages/{page}.yaml
   # Optional folder form: app/ui/pages/{page}/page.yaml
```

### When to Use What

| Scenario | Where to Define |
|----------|-----------------|
| Module with its own page | `module.yaml` capability/page metadata |
| Page using multiple modules | `app/ui/pages/` |
| Page with no module binding (static) | `app/ui/pages/` |
| Event that triggers workflow | `orchestrator.yaml` → `triggers` |
| Event emitted by module | `events.yaml` |
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
| CLI | command/developer interface — filesystem, scaffolding, process management; parallel to Studio, not its predecessor |
| Studio | shared management interface — workspace status, build lifecycle, artifacts, run history, config; available in both local and hosted deployments |
| Mozaiks App | hosted product — extends Studio with collaboration, billing, marketplace, and deployment controls; does not fork Studio |
| `chat-ui` | reusable frontend substrate — chat interface, route rendering, registry, transport; must not accumulate Studio or product assumptions as core primitives |
| `web_shell/` | thin browser host — wires adapters, gates Studio registration via `VITE_MOZAIKS_HOST`, calls `@platform/extensions` |
| `mozaiks-platform/app/ui/` | Studio/Mozaiks product UI extensions — registered via `@platform/extensions`; never imported by `chat-ui` core |
| platform-management surface | UI surface registered by Studio and inherited by Mozaiks App (e.g., `AdminPortal`) — not a core substrate primitive |
| app backend | deterministic app service behind `mozaiksai.hosts.platform`, generated module handlers, or an optional external/generated backend |
| AppBackendPort | generic contract in `mozaiksai` for AI runtime ↔ app backend communication |
| app_backend_url | optional base URL for an external/generated app backend when a split topology is used |
| module | self-contained deterministic capability unit under an app workspace `modules/` root or a generated app bundle |
| module manifest system | YAML manifest family for modules (`module.yaml`, `events.yaml`, `settings.yaml`, `notifications.yaml`, `subscriptions.yaml`, `admin.yaml`) |
| module.yaml | handler/action manifest for a module — identity, capabilities, and action definitions; event declarations live in `events.yaml` |
| admin.yaml (platform) | module admin panels rendered inside unified `/admin` |
| runtime ingress | boundary that accepts validated app/domain events and routes them to workflow triggers |
| triggers | workflow start/resume declarations in `orchestrator.yaml` |
| AppGenerator | workflow that generates deterministic app bundle artifacts: `app.json`, pages, config, brand patches, module manifest families, and backend code |

---

## Quick Reference

**Start a workflow:** WebSocket → mozaiksai → `run_workflow_orchestration()` → AG2 GroupChat

**Execute app logic:** REST → app backend → app endpoint or handler → persistence

**User settings:** REST → app backend → settings API → persistence

**Stream AI responses:** mozaiksai → `simple_transport.broadcast_event()` → WebSocket → frontend

**CRUD triggers AI:** app event emitted → runtime ingress matches → workflow `triggers` → run/resume

**AI triggers app behavior:** workflow agent → `backend_request()` or `emit_event()` → app backend

**App backend boundary:** `mozaiksai` talks to the app backend through `AppBackendPort`

