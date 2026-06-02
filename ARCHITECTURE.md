# Mozaiks Architecture

This is the authoritative architecture reference. If other docs contradict this file, this file wins.

## What This Repo Is

This repo is the **canonical Mozaiks repo** — the AI runtime, app host, frontend surfaces, Studio management interface, hosted product contracts, and generated app artifact contracts for Mozaiks apps.

Deterministic app behavior is owned by app bundles hosted by `mozaiksai.hosts.platform`, generated module contracts, or optional external/generated app backends connected through the generic `AppBackendPort` contract in `mozaiksai/core/ports/app_backend.py`.

```text
mozaiks/                           # Canonical Mozaiks repo
├── mozaiksai/                     # AI workflow runtime (first-class)
│   └── control_plane/             # Builder session harness runtime (Studio-only)
├── chat-ui/                       # Chat interface primitives (first-class)
├── web_shell/                     # Local Vite shell host source
├── factory_app/                   # First-party Studio app bundle + shared workflows
│   ├── app/
│   │   ├── app.json
│   │   ├── brand/
│   │   ├── config/
│   │   ├── modules/
│   │   └── ui/
│   ├── control_plane/             # First-party builder/reference app control-plane pack
│   │   ├── config/                # control_plane.yaml, tools.yaml, policies.yaml
│   │   ├── prompts/               # System prompt files per checkpoint
│   │   └── tools/                 # Context-loading Python tool implementations
│   └── workflows/
├── generated/                     # Staged generated apps/workflows awaiting promotion
├── mozaiks_cli/                   # CLI for local project and workflow work
├── platform/                      # Repo-local infrastructure assets only (not an app workspace)
├── mozaiksai/hosts/runtime.py     # Runtime substrate host
├── mozaiksai/hosts/platform.py    # Headless app host
├── mozaiksai/hosts/studio.py      # Local/private Studio management/create host
└── docs/                          # Source-of-truth documentation
```

## Canonical Target

The repo above is the current implementation state, not the desired end-state for Mozaiks distribution.

The canonical target architecture is:

1. **Mozaiks runtime package** — reusable Python dependency
2. **Shared web shell package** — reusable frontend dependency
3. **Factory app workspace** — first-party builder workflows and artifact assembly logic
4. **App workspaces** — standalone app repositories
5. **Hosted product workspaces** — same workspace contract, plus hosted-only capabilities, typically in separate repos

Generated apps should become their own workspaces/repositories. They should not live permanently inside this repo.

Canonical target workspace shape:

```text
my-app/
├── app/
│   ├── app.json
│   ├── config/
│   ├── ui/
│   ├── modules/
│   ├── brand/
│   └── services/
├── control_plane/                 # optional app-local harness pack
│   ├── config/
│   ├── prompts/
│   └── tools/
└── workflows/
```

- The first-party factory layer lives under `factory_app/`
- Hosted product workspaces consume the same self-contained app-root contract
- `app/config/ai.json` is the canonical app-level AI startup contract for `ask`, `chat`, and `workflows`
- `control_plane/config/runtime.yaml` is the optional app-local control-plane runtime policy contract
- `control_plane/config/control_plane.yaml` is the optional app-local declarative harness manifest
- `app/config/data.json` is the canonical app data contract
- `app/services/` is the canonical app-owned service implementation lane

## What Mozaiks Is

Mozaiks is a **persistent intent-orchestration runtime**. It covers the full lifecycle: understanding user intent, routing it to the right execution context, generating artifacts, and managing everything that follows — revision, refinement, continuation, and dependency tracking across the entire system.

Most AI products treat each interaction as isolated. Mozaiks treats every interaction as part of a living system. Users do not move linearly — they revise, branch, change foundational assumptions, ask for local edits, and continue unfinished work. The system needs to know the difference between a small patch and a change that invalidates everything downstream.

This means:
- **Workflows are execution primitives**, not the top-level product abstraction
- **Artifacts are durable, versioned state** — not disposable chat outputs
- **The control plane is the continuity layer** — it interprets intent against current system state and routes to the correct execution context
- **`factory_app` is the first-party builder/reference app workspace** — a Mozaiks app workspace that dogfoods the canonical app contract while hosting the builder experience; it does not define the limits of the runtime

Mozaiks is not primarily an app builder, a group-chat framework, or a workflow engine. It is the runtime layer that lets generated systems evolve.

See [docs/architecture/foundations/distribution-and-workspace-model.md](docs/architecture/foundations/distribution-and-workspace-model.md) for the authoritative target model.

**Primary boundaries:**
- `mozaiksai.hosts.runtime` — reusable execution substrate
- `mozaiksai.hosts.platform` — canonical headless app host for pages, modules, shell config, admin, actions, and app routing
- `mozaiksai.hosts.studio` — Studio management interface host; the shared management layer used by both local and hosted deployments
- `factory_app/app` — first-party Studio app bundle; hosted product workspaces consume the same `app/` contract from outside this repo
- Hosted product workspaces compose app-local hosts on top of Studio; the OSS repo does not own a hosted-product FastAPI host

Customer-facing terminology follows a different layer:

- `Studio` is an internal host/composition term
- visible UX should prefer `Apps`, `Build`, `Operations`, `Integrations`, and
  `Admin`
- `Hub`, `Studio` as a top-level product area, and `Adapters` should not be
  treated as long-term customer-facing IA

**Working modes:**

1. **Framework/platform mode** — `mozaiksai.hosts.runtime`, `mozaiksai.hosts.platform`, `mozaiksai/`, `chat-ui/`, `web_shell/`, repo-local infrastructure/packaging
2. **Factory mode** — `factory_app/workflows/`, `factory_app/control_plane/` — the builder/generator workflows, agent configs, structured outputs, and control plane pack
3. **Studio mode** — `mozaiksai.hosts.studio`, `factory_app/app/ui/pages/custom/studio/`, `factory_app/app/admin/`, `factory_app/app/modules/factory_control_plane/`, `chat-ui/src/admin/` — the management interface that surfaces Factory capabilities
4. **Hosted product contract mode** — contracts that external hosted product workspaces consume; concrete hosted-product hosts live in those product workspaces

---

## Runtime Layering & Separation of Concerns (CRITICAL)

This repo uses layered FastAPI hosts as the canonical runtime architecture.

### Layer Model

```
Runtime (AI substrate)
   <- Platform (app shell / app host)
      <- Factory (builder / generator)
         <- Studio (management interface)
            <- Hosted product workspace (external app-local host)
```

**CLI and Studio are parallel interfaces**, not a chain. CLI owns developer tooling (filesystem, scaffolding, process management). Studio owns the management surface (workspace status, build lifecycle, artifacts, run history, config). CLI must not become a dependency of Studio, Platform, or Runtime.

**Hosted products extend Studio** — they do not fork it. Hosted-only capabilities layer on top through an app-local host plus the `@platform/extensions` mechanism.

The visible product model on top of those hosts is:

- `Apps` for the workspace-level directory
- `Build` for app creation and refinement
- `Operations` for health, incidents, and runtime status
- `Integrations` for connected systems

### Universal Substrate vs Framework Capabilities

- **Universal substrate** — code every downstream app runtime depends on: `mozaiksai/`, `mozaiksai.hosts.runtime`, `mozaiksai.hosts.platform`, core `chat-ui/` primitives.
- **Framework-owned optional capabilities** — first-class in the distribution but not mandatory for every downstream app at runtime: `factory_app/`, Studio, CLI, control plane.

See [docs/architecture/modules-systems/framework-capability-classification.md](docs/architecture/modules-systems/framework-capability-classification.md).

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
- Shell config, pages, themes, or transitions
- workspace-studio or Build routes
- Admin app-shell routes or product-shell UI composition
- Generator or refinement behavior
- App manifest loading for app-host composition
- CLI conveniences, preview helpers, or local-only behavior

**Key files:** `mozaiksai/core/workflow/orchestration_patterns.py`, `mozaiksai/core/ports/orchestration.py`, `mozaiksai/core/transport/simple_transport.py`, `mozaiksai/core/events/unified_event_dispatcher.py`

### 2. Platform (App Shell / App Host)

**Purpose:** Hosts and composes an app on top of the runtime substrate.

**Owns:**
- Chat and session APIs (`/api/chats/*`)
- Shell config, theme config, and page serving
- Host-owned account/profile APIs (`/api/me`, `/api/me/preferences`)
- Transitions and routing composition
- Session UX concerns (resume, metadata, lifecycle)
- Module/action execution and app-host integration points
- Admin and app-shell routes
- Workflow discovery, ordering, and app-level composition

**Must not own:**
- Orchestration engine internals
- Transport or event dispatch internals
- Generator or build logic

**Key file:** `mozaiksai.hosts.platform`

### 3. Factory Layer (Builder / Generator)

**Purpose:** The builder/generator intelligence — the declarative workflows, agent configurations, control plane, and artifact assembly logic that generate app workspaces. This is not the management UI; it is what the Studio management UI executes and surfaces.

**Owns:**
- Shared builder/generator workflows: `AppGenerator`, `AgentGenerator`, `DesignDocs`, `ValueEngine`
- Control plane declarative pack: checkpoints, classifier prompts, routing policies, context-loading tools
- Workflow prompts, agent rosters, structured output models, and tool bindings for generation
- Artifact assembly: module contract generation, page schema generation, workflow bundle generation

**AppGenerator pipeline (sequential code-gen path):**

```
InterviewAgent → AppPlanAgent → AppSchemaAgent → AppUIQualityAgent → AdminRegistryAgent → AssemblyAgent
                                                  ↓ (passed)
DatabaseAgent → ConfigMiddlewareAgent → ModuleContractQualityAgent → ModelAgent → ServiceAgent
                                         ↓ (blocked)                → FrontendStubAgent → ControllerAgent
                                         user                       → AppValidationAgent → DownloadAgent
```

Quality gates:
- `AppUIQualityAgent` — validates page schema compliance before assembly (UI path)
- `AdminRegistryAgent` — generates `admin/admin_registry.yaml` (page registry for the admin portal); runs after UI quality passes
- `ModuleContractQualityAgent` — validates module YAML contract compliance before code generation (code-gen path)

**Must not own:**
- Management UI — that belongs in Studio
- Runtime execution logic — that belongs in Runtime
- Studio first-party app bundle — that lives in `factory_app/app/`, loaded by the Studio host

**Key files:**
- `factory_app/workflows/` — shared builder/generator workflow root
- `factory_app/control_plane/` — declarative builder harness pack
- `mozaiksai/control_plane/` — harness runtime implementation (mounted by Studio host)

Note: `factory_app/` as a directory co-locates the Factory layer (`workflows/`, `control_plane/`) with the Studio first-party app bundle (`app/`). These are separate concerns sharing a monorepo directory — `factory_app/` is not a synonym for either.

### 4. Studio Layer (Shared Management Interface)

**Purpose:** The management interface for any Mozaiks workspace — local or hosted. Studio surfaces and orchestrates the Factory layer; it does not own the builder/generator content itself. Studio and CLI are parallel interfaces; Studio owns the management surface.

**Owns:**
- Workspace status and readiness surfaces
- Build lifecycle: request drafting, routing generation triggers, running Factory workflows
- Artifact lifecycle: generated → staged → promoted
- Run history, logs, and validation status
- Diff viewer and promotion controls
- integration and API key configuration
- Workflow and module inspection
- Platform-management surfaces including the admin portal
- Local preview controls
- Activating and mounting the builder session harness (control plane)

**Must not own:**
- The builder/generator workflow content — that belongs in the Factory layer
- Hosted-only assumptions (multi-user collaboration, remote deployment, billing)
- Runtime execution logic
- CLI-only concerns (filesystem scaffolding, process management)

**Key files:**
- `mozaiksai.hosts.studio`
- `factory_app/app/` (first-party Studio app bundle — pages, modules, brand, config)
- `factory_app/app/ui/pages/custom/studio/`
- `factory_app/app/modules/factory_control_plane/` (module identity plus a stub backend handler only — no actions, capabilities, or behavior)
- `chat-ui/src/admin/`

### 5. Mozaiks App Layer (Hosted Product)

**Purpose:** Extends Studio with hosted-only capabilities. Not a separate product from Studio — an extension layer on top of it.

**Owns:**
- Collaboration: shared workspaces, comments, presence
- Billing and subscriptions
- Marketplace: template discovery, publishing, install
- Deployment controls: hosted environment management, domain config
- Team and org management
- Any feature requiring a persistent server-side user account beyond session scope

**Must not own:**
- Anything that belongs in Studio — Mozaiks App inherits Studio, it does not fork it
- Runtime execution logic

**Key files:** external hosted product workspaces; the OSS repo provides Studio as the base host but does not define a hosted-product FastAPI host.

**Generator and workflow root rules:**

Shared factory workflows live in `factory_app/workflows/`. A running host resolves one workflow root by default. `MOZAIKS_WORKFLOWS_PATH` may override explicitly.

- Studio uses `factory_app/workflows/` as the shared builder workflow root
- Product/app hosts use workspace-root `workflows/` when present
- Build is coordinated by `workflow_sequences` in `factory_app/workflows/extended_orchestration/extension_registry.json`; `ValueEngine`, `ThemeCapture`, `DesignDocs`, `AgentGenerator`, and `AppGenerator` are individual workflows inside those sequences
- `ExistingAppDiscovery` belongs to the brownfield adoption sequence rather than the default greenfield build path
- Refinement today is checkpoint/control-plane re-entry driven by `app/config/ai.json` startup plus `factory_app/control_plane/config/runtime.yaml` policy and `factory_app/control_plane/config/control_plane.yaml`, not a dedicated `RefinementWorkflow`

`AppGenerator` and `AgentGenerator` write all output into `MOZAIKS_GENERATED_ARTIFACTS_PATH` (defaults to `generated/`). Promotion is the only path from `generated/` into an active app root.

**Hosted product module direction:**

Hosted product modules are the deterministic business layer of a hosted product — not generic app-project bookkeeping. They must not be copied into generated OSS app bundles unless explicitly selected as hosted capability packs.

### 6. CLI / Developer Interface Layer

**Purpose:** Developer tooling. Parallel to Studio — not a chain where Studio is the UI for CLI.

**Owns:**
- Scaffold generation: `mozaiks init`, `mozaiks onboard`, `mozaiks add`
- Process management: starting/stopping the local server
- Workspace diagnostics: `mozaiks studio`
- Offline generation: `mozaiks gen` — a developer convenience, not the canonical build lifecycle

**Must not own:**
- Management state (run history, artifacts, generation status) — Studio owns these
- Any UI surface that Studio already provides
- Assumptions about persistent user accounts

**Must not leak into:** Runtime, Platform, Studio, or Mozaiks App code.

### Decision Rules (MANDATORY)

When adding code, decide placement in this order:

1. Is this required for every runtime instance? → **Runtime**
2. Is this about app hosting, routing, sessions, pages, or modules? → **Platform**
3. Is this about workspace management, build lifecycle, artifact review, run history, or configuration? → **Studio**
4. Is this a hosted-only capability (collaboration, billing, marketplace, deployment)? → **Mozaiks App**
5. Is this filesystem scaffolding, process management, or a terminal diagnostic? → **CLI**

Key: a feature is not CLI just because it runs locally — if it's management UI, it belongs in Studio. `chat-ui/` is the substrate; Studio and Mozaiks App register extensions into it, they do not add core primitives to it.

### Hard Anti-Leak Rules

**Never put in Runtime:**
- `PLATFORM_PATH` resolution, repo-local workspace references
- Shell config logic, Studio routes, transition routing
- App manifest loading for app-host composition
- Generator-specific behavior, page or theme serving

**Generator output boundary:**
- All generator output must go under `MOZAIKS_GENERATED_ARTIFACTS_PATH` (defaults to `generated/`)
  - `generated/apps/{app_id}/{build_id}/app/` — AppGenerator app schemas and bundle files
  - `generated/workflows/{app_id}/{build_id}/{workflow_name}/` — AgentGenerator workflow bundles
- Only promotion logic may write into active runtime roots

**Avoid in reusable hosts:**
- `sys.path.insert(...)`, relative-path hacks, monorepo assumptions baked into runtime or platform hosts
- Prefer explicit app-root inputs via `PLATFORM_PATH` or `MOZAIKS_APP_WORKSPACE_PATH`

---

## Control Plane (Builder Session Harness)

The control plane is a **checkpoint-driven semantic harness** that sits above the workflow layer. Its job is to intercept natural-language user intent at defined decision points, classify what kind of change the user is requesting, and route to the correct workflow — rather than letting individual workflows guess context.

A core distinction the harness enforces is **refinement vs revision**:

- **Local refinement** — the request targets a bounded artifact or output. The harness patches it directly without reopening prior workflows. Example: *"Change this button label."*
- **Upstream revision** — the request changes a foundational assumption that invalidates downstream artifacts. The harness reopens the appropriate prior workflow, evaluates dependencies, and determines what must regenerate vs what can be preserved. Example: *"Change the target customer."*

Without this distinction, every user request either re-runs the full workflow unnecessarily or patches blindly without considering downstream impact. The harness is what makes the system behave like a coherent runtime rather than a sequence of isolated group chats.

The control-plane contract is **app-local**, not Studio-private. Studio
dogfoods it through `factory_app/control_plane/`, and generated app workspaces
may stage the same contract under `control_plane/config/` when they need
harnessed lifecycle/refinement/session routing. `app/config/ai.json` remains
the startup contract for `ask`, `chat`, and `workflows`; control-plane runtime
policy lives in `control_plane/config/runtime.yaml`; the declarative harness
manifest lives in `control_plane/config/control_plane.yaml`.

### How It Differs From Other Routing Mechanisms

| Mechanism | Layer | Purpose |
|-----------|-------|---------|
| `triggers` in `orchestrator.yaml` | Workflow | External events that start/resume a specific workflow |
| `task_batches.yaml` | Intra-workflow | AG2 task batches for bounded parallel work inside one workflow |
| `extension_registry.json` transitions | Pre-workflow | Static graph of build-step transitions between workflows |
| **Control plane checkpoints** | **Above all workflows** | **Semantic classification of user intent → dynamic workflow routing** |

The control plane does not execute business logic. It classifies intent and routes. All execution still happens inside workflows. When no checkpoint matches or the harness is not loaded, the system falls back to `extension_registry.json`.

### Directory Structure

**Declarative pack (first-party builder/reference app example):**

```text
factory_app/control_plane/
├── config/
│   ├── runtime.yaml         # Enabled/profile/llm profiles/capability policy
│   ├── control_plane.yaml   # Checkpoints, classifier config, routing rules
│   ├── tools.yaml           # Tool bindings for context-loading tools
│   └── policies.yaml        # Artifact-specific routing policies per change class
├── prompts/
│   ├── intent_classifier.md # System prompt: classify patch|design|feature|core
│   ├── scope_resolver.md    # System prompt: resolve scope from conversation context
│   └── routing_advisor.md   # System prompt: recommend workflow given classification
└── tools/
    ├── load_app_context.py
    ├── load_build_history.py
    ├── load_active_workflow.py
    ├── load_schema_context.py
    ├── load_module_context.py
    └── load_artifact_context.py
```

**Runtime implementation:**

```text
mozaiksai/control_plane/
├── harness.py           # ControlPlaneHarness: checkpoint evaluation + routing
├── classifier.py        # LLM-based intent classifier (patch|design|feature|core)
├── checkpoints.py       # Checkpoint definitions and evaluation logic
├── router.py            # Maps (checkpoint × classification) → target workflow
└── context_loader.py    # Aggregates context tool results for classifier prompts
```

`factory_app/app/modules/factory_control_plane/` contains a module identity plus a stub `backend/handler.py` only — no actions, capabilities, or runtime behavior. It surfaces the control plane as a named entity in the Studio module list only. Do not add logic there.

### Checkpoints

Each checkpoint has a trigger condition, a context-loading step, a classifier prompt, and a routing policy.

| Checkpoint | When it fires | Outcome |
|------------|--------------|---------|
| `session_start` | User opens a new builder session | Routes to AppGenerator refinement, DesignDocs, AppGenerator full, or escalation |
| `intent_unclear` | Classifier confidence below threshold | Launches clarification workflow |
| `mid_build_redirect` | User changes direction mid-generation | Abort + reroute vs continue |
| `post_build_review` | User responds to a completed artifact | Patch vs re-generate vs approve |
| `scope_expansion` | Request implies new module or workflow | AgentGenerator or AppGenerator |

### Change Classification

| Class | Meaning | Default routing |
|-------|---------|-----------------|
| `patch` | Small targeted fix to existing artifact | AppGenerator refinement pass |
| `design` | Visual or structural change to a page | DesignDocs → AppGenerator |
| `feature` | New capability requiring new module or workflow | AppGenerator full + optional AgentGenerator |
| `core` | Architectural change to runtime/platform contracts | Escalate to human review |

The routing policy in `policies.yaml` maps each (artifact type × classification) pair to a concrete workflow name and entry arguments.

### How the Harness Wires In

`mozaiksai.hosts.studio` loads the control plane pack from `factory_app/control_plane/` at startup and registers `ControlPlaneHarness` on the session lifecycle hook. When a builder session event matches a checkpoint condition, the harness:

1. Loads context via the declared tools
2. Runs the classifier prompt against the loaded context
3. Selects the routing policy for the resulting classification
4. Launches or resumes the target workflow with pre-populated context variables

---

## First-Class vs App-Level

| First-Class (framework) | App-Level (app workspace) |
|------------------------|---------------------------|
| mozaiksai (AI runtime) | workflows |
| `AppBackendPort` + backend bridge tools | optional external/generated backend integration |
| chat-ui + app shell | pages |
| transport, artifacts, orchestration | brand/theme |
| framework defaults and CLI | config |

**First-class = every app gets it automatically. App-level = defined per app in an `app/` workspace root.**

### First-Class UI Surfaces

Registered in `chat-ui/src/registry/coreComponents.js` — every app gets them automatically, regardless of which platform or Studio is loaded.

| Component | Route | Purpose |
|-----------|-------|---------|
| `ChatPage` | `/chat` | Main AI workflow interface |
| `SchemaPage` | `/{page}` | Renders declarative AppPageSchema from `/api/pages/{name}` |
| `ProfilePage` | `/profile` | User profile view/edit — identity panel (framework), module-declared panels via `contracts/profile.yaml`, app preferences panel |

### Platform-Management Surfaces

The admin portal and Studio pages are **not** core `chat-ui` primitives. They are platform-management surfaces registered by Studio and inherited by Mozaiks App. Components are registered through `factory_app/app/admin/index.js` (imported by `factory_app/app/ui/index.js`) and declared in the Studio route manifest and `admin/admin_registry.yaml`:

| Component | Route | Purpose |
|-----------|-------|---------|
| `AdminPortal` | `/admin` and `/apps/:appId/*` | Unified admin shell — app-business panels, module panels, runtime/operator panels |
| Studio pages (`StudioPage`, `AppsPage`, etc.) | `/apps/*` | First-party workspace and per-app Studio surfaces in `factory_app/app/admin/pages/` |

`AdminPortal` separates authority internally:
- **App-business admin panels** — from `app_backend_url/api/admin/*` using `mozaiks.admin.app_backend.v1`
- **Module admin panels** — declared via `admin.yaml`, rendered inside `/admin`
- **Runtime panels** — workflow runs, tokens, cost, sessions

### Hosted-Product UI Extensions

Registered via the active app root's `app/ui/index.js` extension barrel. Studio components are inherited automatically; extension components add hosted-product-specific pages on top without forking the Studio layer.

#### Platform shell runtime guarantees

**Admin Portal profile-menu injection** — `build_shell_config()` always injects an `admin-portal` entry into the profile menu after the full shell pipeline runs, regardless of which shortcuts the app configures. The entry is inserted before signout when present, appended otherwise, and the injection is idempotent. This is implemented in `mozaiksai/hosts/platform.py::_inject_admin_portal`.

**`appShell` auto-inference** — When a route manifest entry declares `navigation.group`, `build_shell_config()` auto-sets `appShell: true` on that page's meta via `setdefault` so layout-aware components (e.g. `WorkspaceLayout`) can discover the page. An explicit `appShell: false` is never overridden.

**Vite JSX transform for active app workspaces** — `web_shell/vite.config.js` uses a `platformAppDirForward` prefix check (`id.startsWith(platformAppDirForward + '/')`) so JSX inside `.js` files is correctly transformed for any workspace directory name (e.g. `customer-portal`).

**React/router singleton deduplication** — `web_shell/vite.config.js` declares `resolve.dedupe: ['react', 'react-dom', 'react-router-dom', 'react-router']`. This is required because:

1. `chat-ui/` ships its own `node_modules/` (including React and react-router). When the Vite dev server resolves modules for files served via `@fs/` (active app workspace code or chat-ui source outside the Vite root), it may discover and load the chat-ui copy instead of the shell's copy.
2. Two distinct React instances in one browser session break all hook state: `useCallback`, `useContext`, and router hooks depend on a shared dispatcher bound to the single `createRoot()` instance.
3. `resolve.dedupe` forces Vite to always use the web_shell's single copy regardless of which `node_modules` directory is found during resolution.

The companion `resolve.alias` entries (`react`, `react-dom`, `react-router-dom`) are belt-and-suspenders: they pin explicit imports to `web_shell/node_modules/react` so the resolved path is deterministic even outside the pre-bundled dep cache.

App workspaces and generated app output must not bundle their own copies of React, react-dom, or react-router. Those packages are always provided by the shell.

---

## Core Runtime and App Backend Boundary

### mozaiksai/ — AI Workflow Runtime

**What it does:** Executes multi-agent AI workflows using AG2 (AutoGen).

**Key responsibilities:**
- Run AI workflow executions with AG2 beta agents and transition graphs
- Stream events to frontend via WebSocket
- Persist workflow-run metadata to MongoDB and AG2 run history through a persistent per-run stream
- Handle tool calls from agents
- Manage workflow state (in-progress, completed)
- Token accounting and observability

**Event types (3 distinct systems):**
1. **Business events** — Observability/logging (`emit_business_event`)
2. **UI tool events** — Agent-to-UI communication (`emit_ui_tool_event`)
3. **AG2 runtime events** — Chat execution (`chat.text`, `chat.tool_call`, `chat.run_complete`)

**Multi-tenant scoping:** Every runtime action requires `app_id`, `user_id`, `chat_id`.

### App Backend Integration Boundary

**What it does:** Defines how deterministic, non-AI app behavior connects to the AI runtime. The runtime only depends on the generic `AppBackendPort` contract — the backend may be platform-hosted module handlers, an existing product backend, or any HTTP service.

**Typical app-backend responsibilities:**
- User settings, profiles, and preferences (`/api/me/*`)
- Deterministic business actions and persistence (module handler → service → repo)
- REST/action surface for AI agents (declared in `module.yaml`)
- Notifications, event reactions, settings, and app policy (module contracts)
- Domain event emission → AI runtime workflow triggers (through runtime ingress)

**Core contract files:**
- `mozaiksai/core/ports/app_backend.py` — `AppBackendPort`
- `mozaiksai/core/adapters/http_app_backend.py` — generic HTTP adapter
- `mozaiksai/core/workflow/app_backend_tools.py` — built-in workflow bridge tools

**Hard rule:** The runtime never imports app-backend internals or hardcodes app-specific API paths. Paths are passed as arguments by workflow tools or agent context.

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

`app_backend_url` is an optional base URL for an external/generated deterministic backend. Generated workflows may use it when the app explicitly chooses a split backend topology.

**Persistence topology:** Deployment-specific. Runtime, platform host, and any external/generated backend may share infrastructure or run with separate stores.

**Boundary rule:** App facts cross the boundary as API calls or domain events, not as in-repo imports.

---

## Distributed Event Model

Events are **distributed**, not centralized. No separate `automations/` directory.

### Where Events Are Declared

| Who | Declares | In File |
|-----|----------|---------|
| Module | Events it **publishes** | `modules/{name}/contracts/events.yaml` |
| Module | Event reactions | `modules/{name}/contracts/reactions.yaml` |
| Module | Notification rules | `modules/{name}/contracts/notifications.yaml` |
| Module | Admin panels | `modules/{name}/contracts/admin.yaml` |
| Workflow | Events it **emits** | `orchestrator.yaml` → `events.emits` |
| Workflow | Events that **trigger** it | `orchestrator.yaml` → `triggers` |

### Module Event/Reaction Contract

- `modules/{name}/contracts/events.yaml` declares the events a module may emit.
  Event types must use a valid namespace owned by the emitting layer such as
  `domain.*`, `platform.*`, or `hosted.*`. For app modules, emitted events in
  `module.yaml.actions[].emits` must be declared here and are normally `domain.*`.
- `modules/{name}/contracts/reactions.yaml` is the canonical reaction contract.
  It uses `schema_version: mozaiks.reactions.v1`, root key `reactions`,
  `event_type` for the incoming event, and nested `target.kind` for routing.
- Reaction targets use one of three canonical target kinds:
  `handler` via `target.handler_method`, `capability` via
  `target.capability_id`, or `notification` via `target.notification_id`.
- `modules/{name}/contracts/notifications.yaml` declares notification rules
  derived from events. It is separate from `reactions.yaml`; notification rules
  describe delivery policy, while reactions describe event routing.
- `modules/{name}/contracts/subscriptions.yaml` is not supported. Runtime
  rejects it so modules have one reaction-routing source of truth.

### CRUD → AI (workflow triggers)

Workflows declare what app events start or resume them. Modules declare the domain events they publish and any app-level reactions they own. The platform host/runtime ingress resolves event facts to workflow triggers; modules do not encode AG2 workflow-run semantics directly.

### AI → CRUD (event publishing)

Workflow agents talk back to the app backend through the built-in backend tools (`backend_request`, `emit_event` in `mozaiksai.core.workflow.app_backend_tools`) or custom tools that use `AppBackendPort`.

### Framework Aggregates at Runtime

The framework scans module contracts and `orchestrator.yaml` files to build the routing table at startup.

---

## App Workspace Contract

`mozaiksai.hosts.platform` composes an active app workspace. In this repo, `factory_app/app/` is the first-party Studio app bundle using the same top-level contract.

```text
workspace/
├── app/
│   ├── app.json                    # App identity + startup intent + admins bootstrap
│   ├── config/
│   │   ├── ai.json                 # Runtime startup for ask/chat/workflows
│   │   ├── shell.json              # Header/footer/profile/notification chrome
│   │   ├── data.json               # Unified data contract
│   │   ├── secrets.yaml            # Names-only secret contract
│   │   ├── integrations.yaml       # External/hosted capability requirements
│   │   └── targets.json            # Runtime/deployment/domain target intent
│   ├── modules/
│   │   └── {module_name}/
│   │       ├── module.yaml
│   │       ├── contracts/
│   │       ├── runtime_extensions.yaml
│   │       └── backend/
│   │           ├── handler.py
├── control_plane/
│   ├── config/
│   │   ├── runtime.yaml            # Optional app-local control-plane runtime policy
│   │   ├── control_plane.yaml      # Optional app-local control-plane manifest
│   │   ├── tools.yaml
│   │   └── policies.yaml
│   ├── prompts/
│   └── tools/
│   │           ├── service.py
│   │           ├── repo.py
│   │           ├── policy.py
│   │           └── schemas.py
│   ├── ui/
│   │   ├── route_manifest.json
│   │   ├── pages/
│   │   ├── pages/custom/
│   │   └── index.js
│   ├── brand/
│   └── services/
│       ├── integrations/
│       ├── adapters/
│       ├── security/
│       ├── routes/
│       └── data/
└── workflows/
    └── {WorkflowName}/
        ├── orchestrator.yaml
        ├── agents.yaml
        ├── context_variables.yaml
        ├── structured_outputs.yaml
        ├── handoffs.yaml
        ├── tools.yaml
        ├── ui_config.yaml
        ├── hooks.yaml
        ├── tools/
        └── ui/
```

`app/brand/theme_config.json` is the visual authority for generated apps.
`app/config/shell.json` owns shell/navigation/chrome behavior only. Generated
pages and custom route React must consume shared primitives and semantic
tokens/classes instead of hardcoded colors, literal font families, or
page-local visual systems.

`app/services/` is optional app-owned support code. `app/services/integrations/`
contains thin clients for external or hosted APIs. `app/services/adapters/` contains
provider-specific implementation boundaries such as auth/OIDC/OAuth,
source-control, deployment, DNS, registrar, cloud, storage, search, email, or
payment provider mechanics. These files are not modules: they must not own
runtime actions, lifecycle state, emitted events, permissions, or persistence
authority. Modules own business behavior and may call these support files.
Generic runtime auth adapters remain framework code under `mozaiksai/core/auth/`.

Every bundle (module, workflow, page) declares a `visibility`: `public` (all users), `internal` (authenticated only), or `admin` (admin only).

---

## Key Concepts

### Workflow
A multi-agent AI conversation executed by AG2. Declared in workspace-root `workflows/` for app-owned workflows, or `factory_app/workflows/` for shared builder workflows. Declares `events.emits` (what its tools publish) and `triggers` (what external events start or resume it).

Current runtime note: one workflow run owns one persistent AG2 `MemoryStream` keyed by `app_id + chat_id`. `ChatSessions` stores run metadata and projections such as status, usage, artifacts, and session/journey linkage; AG2 run-stream history is the canonical execution record used for resume and replay.

### Lifecycle Tools (`lifecycle_tools` in `tools.yaml`)

Turn-level and run-level hooks declared inside a workflow's `tools.yaml`. Executed by the `LifecycleToolManager` at specific points in the workflow execution lifecycle.

```yaml
# tools.yaml
lifecycle_tools:
  - trigger: before_chat    # fires before the chat session starts
  - trigger: after_chat     # fires after the chat session ends
  - trigger: before_agent   # fires before a specific agent turn
  - trigger: after_agent    # fires after a specific agent turn
  - trigger: on_start       # fires when the workflow run begins (platform build tracking)
  - trigger: on_complete    # fires when the workflow run completes successfully
  - trigger: on_fail        # fires when the workflow run fails
```

Each entry declares `trigger`, `file` (path relative to workflow root), `function`, and optionally `agent` (for `before_agent`/`after_agent`) and `description`. This is the only lifecycle hook mechanism for workflows — all hook points are expressed here.

### Module Runtime Extensions (`runtime_extensions.yaml` in `modules/{name}/`)

Host-level capabilities that a module needs registered at server startup — not run-level hooks. Declared in `modules/{name}/runtime_extensions.yaml`.

```yaml
# modules/{name}/runtime_extensions.yaml
schema_version: mozaiks.runtime_extensions.v1
extensions:
  - kind: api_router
    entrypoint: backend.router:get_router
    prefix: /webhooks          # optional

  - kind: startup_service
    entrypoint: backend.worker:MyService
```

Two kinds:
- `api_router` — mounts a FastAPI `APIRouter` at host startup. Use for module-local generic external webhook receivers or custom callback routes.
- `startup_service` — instantiates and starts a background service that lives for the process lifetime. Use for module-local audit/event subscribers or polling workers.

Entrypoints must be module-local backend files and must be declared in generated
backend outputs or Python stubs. Do not use runtime extensions for generic
business logic, persistence, auth/scope helpers, transport infrastructure, or
workflow orchestration.

The platform loader scans `modules/*/runtime_extensions.yaml` at startup alongside the other module companion manifests. AppGenerator generates this file when a module's capabilities require it.

### Module
A unit of deterministic business logic declared in `app/modules/`. Has a `module.yaml` manifest and a `backend/` with a four-layer implementation contract. NOT an AI workflow — modules provide the CRUD/action surface that AI agents call through platform module routes or `AppBackendPort`.

Backend layer contract:
- `handler.py` — thin dispatch only; one method per declared action; no logic, no ctx.db, no ctx.emit
- `service.py` — all business logic; validates, calls repo, calls ctx.emit after commit
- `repo.py` — pure MongoDB access; no logic, no events
- `policy.py` — pure functions; builds scoped query dicts from ctx
- `schemas.py` — typed request/response and document shapes plus pure helpers
- helper files — declared, justified, module-local support imported by canonical layers or referenced by `runtime_extensions.yaml`

### Page (frontend)
A UI screen. Declarative pages live in `app/ui/pages/*.yaml` and are rendered by `SchemaPage` via `/api/pages/{name}`. Hand-authored full-page routes live in `app/ui/pages/custom/` with a corresponding entry in `route_manifest.json` and `index.js`.

### Four UI Surfaces

| Surface | Files | Renderer |
|---------|-------|----------|
| App UI (generated pages) | `app/ui/pages/*.yaml` | PageRenderer ← SchemaPage |
| Agentic UI (workflow artifacts) | `ui/{WorkflowName}/*.jsx` + `tools/*.py` | useAppEventBus ← WebSocket |
| Custom Route UI | `app/ui/pages/custom/*.jsx` + `route_manifest.json` | `@platform/extensions` registry |
| Transition UI | `extension_registry.json` | LauncherScreen / TransitionScreen |

Canonical contract: [docs/architecture/frontend/ui-system/generated-frontend-surface-contract.md](docs/architecture/frontend/ui-system/generated-frontend-surface-contract.md)

### Artifact
A durable, versioned output produced by a generator workflow. Artifacts are not disposable chat responses — they are addressable state objects that the harness can target for local patch or upstream revision. An artifact knows which workflow produced it, what inputs it depends on, and what downstream artifacts reference it. Examples: generated app bundle, page schema, module contract family, workflow definition, design document.

Artifacts live in `generated/` until promoted into an active app root. The harness identifies the relevant artifact when classifying a user request and either patches it directly (`patch` class) or reopens the originating workflow (`design`, `feature`, `core` classes).

### Event
Three kinds — do not confuse them:
1. **App events** — Emitted by the app backend or runtime bridge tools; trigger workflow `triggers`
2. **UI events** — Published via `emit_ui_tool_event()`; update frontend in real-time
3. **Runtime events** — AG2 execution events (`chat.text`, `chat.tool_call`, `chat.run_complete`)

---

## Architectural Invariants

These invariants guide every implementation decision in this repo.

1. **The current chat is not necessarily the owner of the next user request.** The harness decides which execution context handles it.
2. **Raw chat history is not the source of truth.** Events and artifact state are. Chat history is useful context, not authoritative state.
3. **Generated artifacts must be addressable and revisable without full regeneration.** A local patch must not rerun the whole workflow sequence.
4. **The system must distinguish local refinement from upstream revision.** A bounded artifact change is different from a foundational assumption change that invalidates downstream outputs.
5. **A workflow sequence must be able to pause, resume, branch, or reopen a prior step.** Linear-only execution is insufficient for real users.
6. **The harness must be deterministic where the system has enough structure.** LLM reasoning is used for intent classification; routing, state loading, and event commitment are deterministic.
7. **`factory_app` can define builder behavior but must not own the runtime abstraction.** The harness and runtime must be agnostic enough to support any generated system — not just app builders.
8. **Execution contexts (agents, workflows, tools) are modular workers.** The harness selects which one acts. They do not select themselves.

---

## What NOT to Do

- Don't put AI workflow logic in the app backend
- Don't put CRUD/user-state logic in mozaiksai
- Don't hardcode workflow behavior in the runtime (use declarative configs)
- Don't add duplicate interfaces or aliases (make canonical changes)
- Don't confuse `task_batches.yaml` (workflow-local parallel task work), `triggers` (external events), `extension_registry.json` (static transitions), and control plane (semantic routing) — see the comparison table in the Control Plane section
- Don't let generators write into active runtime paths — all output goes to `generated/`
- Don't grow CLI into Studio concerns (management state, artifacts, run history) or Studio into CLI concerns (filesystem, scaffolding)

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
| `app/ui/index.js` | App-owned extension barrel for custom routes/admin components — registered via `@platform/extensions`; never imported by `chat-ui` core |
| platform-management surface | UI surface registered by Studio and inherited by Mozaiks App (e.g., `AdminPortal`) — not a core substrate primitive |
| app backend | deterministic app service behind `mozaiksai.hosts.platform`, generated module handlers, or an optional external/generated backend |
| AppBackendPort | generic contract in `mozaiksai` for AI runtime ↔ app backend communication |
| app_backend_url | optional base URL for an external/generated app backend when a split topology is used |
| module | self-contained deterministic capability unit under an app workspace `modules/` root or a generated app bundle |
| module manifest system | `module.yaml` (required) + optional `contracts/` companion manifests: `events.yaml`, `reactions.yaml`, `notifications.yaml`, `settings.yaml`, `admin.yaml`, `profile.yaml`, `entitlements.yaml` |
| module.yaml | handler/action manifest — identity, capabilities, and action definitions; event declarations live in `events.yaml` |
| admin.yaml (platform) | module admin panels rendered inside unified `/admin` |
| profile.yaml (platform) | module-contributed panels on the user profile page (`/profile`) — kinds: `metrics`, `list`, `component`; hydrated via `GET /api/me/profile-panels` |
| runtime ingress | boundary that accepts validated app/domain events and routes them to workflow triggers |
| triggers | workflow start/resume declarations in `orchestrator.yaml` |
| AppGenerator | workflow that generates deterministic app bundle artifacts: `app.json`, pages, config, brand patches, module manifest families, and backend code |
| control plane | checkpoint-driven semantic harness above the workflow layer — classifies user intent into `patch\|design\|feature\|core` and routes to the correct workflow; Studio-only |
| checkpoint | named decision point where the control plane intercepts before launching or resuming a workflow; has a trigger condition, context-loading step, classifier prompt, and routing policy |
| change classification | intent class assigned by the control plane classifier: `patch` (small fix), `design` (visual/structural), `feature` (new capability), `core` (architectural) |
| `factory_app/control_plane/` | declarative builder harness pack: checkpoint config, classifier prompts, routing policies, and context-loading tool implementations |
| `mozaiksai/control_plane/` | runtime implementation of the control plane harness: `ControlPlaneHarness`, classifier, checkpoint evaluation, and router |


