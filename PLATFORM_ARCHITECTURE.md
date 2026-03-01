# Mozaiks Platform Architecture: From Old World to New World

---

## The #1 Concept: mozaiks-core vs mozaiks-platform

**This is the most important distinction in the entire architecture.** Everything else flows from it.

### Think of it like a phone

| Concept | Phone Analogy | Mozaiks Analogy |
|---|---|---|
| **mozaiks-core** (OSS) | Android (the operating system) | The open-source runtime that ANY app runs on — including mozaiks.ai itself |
| **mozaiks-platform** | Samsung's layer on top of Android (Bixby, Samsung Pay, Galaxy Store) | The proprietary layer that adds hosting, billing, themes, journeys, marketing tools |
| **A workflow** | An app you install on the phone | A specific AI application (e.g., a customer support bot, a hiring tool) |

**Key insight: mozaiks.ai (your product) is ALSO an app running on mozaiks-core.** It's not separate from the core — it runs ON the core, just like every customer's app does. The platform layer is just extra features bolted on top.

### How this already works in your code today

Your ecosystem already has this exact separation across repos:

```
mozaiks/                         ← LAYER 0: THE STACK (OSS)
  mozaiks.contracts/             ← Ports, types, protocol, event envelope shapes
  mozaiks.core/                  ← Runtime authority
    auth/                        ← JWT, user scoping
    workflow/                    ← UnifiedWorkflowManager, orchestration_patterns
      pack/                      ← ⭐ Workflow dependencies, gating, journeys
        config.py                ← Global pack config (workflow_graph.json)
        gating.py                ← Prerequisite enforcement
        graph.py                 ← Per-workflow _pack/workflow_graph.json
        journey_orchestrator.py  ← Auto-advance between journey steps
        workflow_pack_coordinator.py ← Nested workflow spawning
    transport/                   ← SimpleTransport (WebSocket)
    multitenant/                 ← app_id scoping (app_ids.py)
    runtime/                     ← platform_hooks.py (the plugin system)
    artifacts/                   ← artifact persistence
    data/                        ← MongoDB models
    events/                      ← event dispatcher
    observability/               ← performance tracking
    tokens/                      ← token counting
    capabilities/                ← capability registry
  mozaiks.orchestration/         ← AI engine wrapper (create_ai_workflow_runner)
  core/factory.py                ← build_runtime() — application factory for platform/self-hosted
  shared_app.py                  ← Legacy standalone app (being replaced by factory.py)
  workflows/                     ← OSS example workflows
    HelloWorld/                  ← Reference implementation
      _pack/workflow_graph.json  ← ⭐ Pack config (core feature, not platform!)
      config/                    ← YAML files (orchestrator, agents, tools, etc.)
      tools/                     ← Python tool functions
    _pack/workflow_graph.json    ← ⭐ Global workflow dependency graph
  chat-ui/                       ← The React shell (MozaiksApp.jsx)

mozaiks-platform/                ← LAYER 1: FIRST-PARTY APP (proprietary)
  app/
    main.py                      ← Imports mozaiks.core.build_runtime, registers workflows
    workflows/                   ← Platform-specific workflows
      AppGenerator/              ← ⭐ WHERE PROJECT-AID LOGIC IS MOVING
      AgentGenerator/            ← Agent creation workflow
      ProvisioningManager/       ← Tenant provisioning
      SubscriptionManager/       ← Billing/plans
      ValueEngine/               ← Value proposition
      LearningLoop/              ← Learning/adaptation
      CampaignOrchestrator/      ← Marketing campaigns
      _shared/                   ← Shared tools/agents across platform workflows
    plugins/                     ← Re-exports from mozaiks.*
    services/                    ← Platform-specific backend services
  frontend/                      ← Platform UI (shell + workflow UIs)
  services/                      ← .NET microservices (API gateways, etc.)

project-aid-v2/                  ← BEING DEPRECATED → merging into mozaiks-platform
```

**Important: `_pack/` is a CORE feature, not a platform feature.** The `_pack/` directories you see inside workflows are part of the UniversalOrchestrator's dependency system — they define which workflows must complete before others can start, nested workflow spawning, and journey step sequencing. This lives in `mozaiks.core/workflow/pack/` (6 files). The platform merely calls these core functions.

The **magic switch** is one environment variable:

```bash
# OSS mode (self-hosters, open-source users):
# Don't set RUNTIME_PLATFORM_EXTENSIONS at all
# → platform_hooks.py says "all hooks are no-ops"
# → platform/routers.py never loads
# → pure AG2 runtime, no mozaiks-proprietary features

# Platform mode (mozaiks.ai production):
RUNTIME_PLATFORM_EXTENSIONS=mozaiksai.platform.extensions:get_bundle
# → Themes, journey gating, OAuth webhooks, build exports all activate
# → Platform routes mount into the same FastAPI app
```

**This is already built and working.** The `platform_hooks.py` file you pointed me to IS the plugin system that makes this possible. When a self-hoster runs the core, they get a clean AG2 runtime. When mozaiks.ai runs it, the platform extensions load automatically.

### What "multi-tenant" actually means (no jargon version)

When I said "tenant slot" and "shared runtime," here's what I really mean:

Imagine a **building** (the mozaiks-core server). Each **floor** is a different customer's app. They all share the same elevator (WebSocket transport), the same front desk (auth), the same electrical system (MongoDB). But each floor has its own rooms (workflow directory), its own furniture (tools, agents), and its own key cards (app_id).

The `app_id` in `app_ids.py` is literally just a string that says "this request belongs to this customer's app." Every API call, every WebSocket connection, every database query includes the `app_id` so data never leaks between apps.

```
GET /api/chats/{app_id}/MyWorkflow/start    ← app_id = "customer-123"
GET /api/chats/{app_id}/MyWorkflow/start    ← app_id = "customer-456"
```

Same server. Same code. Different data. That's multi-tenancy.

### What about mozaiks.ai itself?

mozaiks.ai IS an app running on this same core. It just has the platform extensions turned on. Conceptually:

```
The mozaiks.ai production server:
  ├── mozaiks stack (Layer 0)              ← the OSS engine
  ├── mozaiks-platform (Layer 1)           ← the product app, imports from stack
  ├── workflows/
  │   ├── HelloWorld/                    ← demo/sample app
  │   │   └── _pack/                     ← workflow dependencies (CORE feature)
  │   ├── AppGenerator/                  ← the AI builder (was project-aid)
  │   ├── ProvisioningManager/           ← tenant provisioning
  │   ├── SubscriptionManager/           ← billing
  │   ├── CustomerApp1/                  ← a paying customer's workflow
  │   └── _pack/workflow_graph.json       ← global dependency graph (CORE feature)
  └── chat-ui/                             ← the React shell
```

### How many repos / servers?

Here's the simple answer:

| Thing | Repo | Runs Where |
|---|---|---|
| **mozaiks stack** (OSS) | `mozaiks` repo | Layer 0. The runtime engine — contracts, core, orchestration. This is what self-hosters get. |
| **mozaiks-platform** (proprietary) | `mozaiks-platform` repo | Layer 1. A first-party app that runs ON the stack. Contains AppGenerator, ProvisioningManager, billing, etc. This IS mozaiks.ai's product. |
| **project-aid-v2** | `project-aid-v2` repo | **Being deprecated.** Its app-generation logic is moving into `mozaiks-platform/app/workflows/AppGenerator/`. |
| **chat-ui** | Inside the `mozaiks` repo (`chat-ui/`) | Static build deployed to CDN or served by the core. All apps share the same React shell. |
| **A customer's app** | NOT a separate repo. It's a `workflows/` directory. | On mozaiks.ai: lives inside the platform's managed workflows. Self-hosted: lives in the stack's `workflows/` folder. |

**For self-hosters** (two options):

**Option A — Own repo (recommended):**
1. Create a new repo
2. `pip install mozaiks` (or `pip install -e ../mozaiks` for local dev)
3. Create `workflows/` directory with their own workflow(s)
4. Write a minimal `main.py`:
   ```python
   from mozaiks.core import build_runtime
   from mozaiks.orchestration import create_ai_workflow_runner
   app = build_runtime(ai_engine=create_ai_workflow_runner())
   ```
5. Run: `uvicorn main:app`

**Option B — Clone the core repo:**
1. Clone the `mozaiks` repo
2. Delete `workflows/HelloWorld/` (it's just an example)
3. Add their own workflows to `workflows/`
4. Run: `uvicorn shared_app:app` (or `docker-compose up`)

**For mozaiks.ai production:** `mozaiks-platform` imports `mozaiks.core.build_runtime()`, registers its workflows (AppGenerator, ProvisioningManager, etc.), and runs as a single FastAPI app. All customer workflows are managed by the platform.

---

## What the Old System Does Today

The current `AD_DevDeploy.py` pipeline:

1. **User clicks "create app"** on mozaiks.ai
2. `MozaiksDB.Enterprises` + `autogen_ai_agents.Design` + `autogen_ai_agents.Concepts` documents define the app spec (design documents, tech stack, monetization)
3. 12 hardcoded agents run in sequence:
   ```
   DatabaseAgent → ConfigMiddleware → Model → Service → Controller → Route →
   EntryPoint → FrontendConfig → Utilities → Components → Pages → App
   ```
4. Each agent emits `code_files[]` JSON — raw file content — which `FileManager` writes to `output_files/`
5. `DBManager` creates a per-enterprise MongoDB database (`appdb_{enterprise_id}`) on the Atlas cluster, applies schema + seed
6. `DeploymentManager` pulls from **6 DB collections**:
   - `FrameworkConfig` → Dockerfile templates (runtime, ports, build commands, directory structure)
   - `PythonBuiltins` → stdlib names to filter from requirements
   - `LLMConfig` → API keys, model name, Azure creds, DockerHub creds, GitHub token
   - `DeployableServices` → 3rd party service env vars/secrets (e.g. Stripe, SendGrid)
   - `ConnectionStrings` → per-enterprise MongoDB connection strings
   - `APIKeys` → (referenced but not directly used in current code)
7. `GitHubOperations` creates a private repo, pushes code + Dockerfiles + `deploy.yaml` GitHub Action, sets GitHub Secrets
8. GitHub Action builds Docker images → pushes to DockerHub → deploys to Azure Container Apps
9. Frontend URL + Backend URL written back to `Design` document

**The output is a standalone full-stack app** (React frontend + FastAPI backend + per-tenant MongoDB) deployed to Azure Container Apps via a user-specific GitHub repo.

---

## What Changes in the New World

In the new world, **mozaiks IS the core**. We are **NOT** generating standalone apps anymore. We are generating **workflow plugins** that run inside the mozaiks runtime. This changes everything about what gets generated, what gets deployed, and what those 6 DB collections map to.

---

## The Four Hosting Scenarios

### Scenario 1: "Create App → Keep Building (no host yet)"

**User journey:** User on mozaiks.ai defines their app, agents generate workflow YAML + tool files, user iterates.

**What happens:**
- Agents generate the workflow directory (`orchestrator.yaml`, `agents.yaml`, `tools.yaml`, `tools/*.py`, JSX components)
- Files exist **only in a sandbox** — E2B sandbox, or a staging area in platform storage (S3/Blob/Mongo GridFS)
- **No real database** is needed yet. Tools that need data use a mock/sandbox MongoDB (E2B has this, or we provision an ephemeral container)
- User can test inside the sandbox via a preview mode that runs the mozaiks runtime + their workflow
- **We charge tokens** for LLM calls during generation. Storage is cheap.

**What's needed from the system:**
| Component | Status |
|---|---|
| `FileManager` | Still writes files, but to a sandbox/staging path instead of `output_files/` |
| `DBManager` | **NOT NEEDED** during this phase. No real DB provisioning. |
| `DeploymentManager` | **NOT NEEDED**. No Dockerfiles, no GitHub Actions. |
| `GitHubOperations` | **NOT NEEDED**. |
| `FrameworkConfig` collection | **DEPRECATED**. The framework is always mozaiks. No more "what's your frontend framework?" — it's always the mozaiks React shell + the workflow's JSX components. |
| `PythonBuiltins` | Still useful for filtering agent-generated requirements, but can be a static list shipped with project-aid (no DB dependency) |
| `LLMConfig` | Still needed for API keys/model, but should be env-var driven for the platform, not read from per-tenant DB |

---

### Scenario 2: "Deploy Now → Host with Mozaiks"

**User journey:** User is satisfied with their workflow, clicks "deploy", mozaiks.ai hosts it for them.

**What happens (no jargon version):**
- The workflow directory (the YAML files + Python tools + JSX components) gets copied into the `workflows/` folder on the mozaiks.ai production server
- Mozaiks provisions:
  - An **app_id** for this customer (like giving them their own floor in the building)
  - A **MongoDB database** for their app's data (schema.json + seed.json get applied)
  - A URL: `{app-name}.mozaiks.ai` or `mozaiks.ai/app/{app_id}`
- The `UnifiedWorkflowManager` discovers the new workflow automatically (it scans the `workflows/` directory)
- The customer's users connect via WebSocket to the same server, but their `app_id` keeps their data separate
- **No per-app Dockerfiles. No per-app GitHub repos. No per-app servers.** Everyone shares the same core.

**What's needed from the system:**
| Component | Status |
|---|---|
| `DBManager` | Simplified. Only creates the per-app database + applies schema. No more connection string management for deployment — the platform's shared runtime already has the Atlas connection. |
| `DeploymentManager` | **REPLACED** with a `TenantProvisioner` that does: create app DB, register workflow in platform registry, assign subdomain/app-id |
| `GitHubOperations` | **NOT NEEDED for per-app deploys**. The platform's own repo holds all workflows. Or we push to a monorepo workflow dir. |
| Dockerfiles | **NOT NEEDED per app**. The mozaiks runtime is a single Docker image that runs all workflows. |
| GitHub Actions | **NOT NEEDED per app**. The platform has its own CI/CD. |

**What the platform owns (not project-aid):**
- The mozaiks runtime Docker image + deployment
- Multi-tenant routing (app_id in path)
- Auth (JWT, user scoping)
- WebSocket transport
- Orchestration engine (`orchestration_patterns.py`)
- The React shell + component system
- Billing/token tracking
- SSL, domains, scaling

**What project-aid owns:**
- Generating the workflow YAML files
- Generating the tool Python functions
- Generating the JSX components
- Generating `models.py` (Pydantic schemas for the app's MongoDB collections)
- Generating `schema.json` + `seed.json` for the app's database
- Optionally generating a thin `router.py` (APIRouter for custom REST endpoints)

---

### Scenario 3: "Self-Host → Leave the Platform"

**User journey:** Enterprise outgrows mozaiks.ai, wants to run on their own infrastructure.

**What they get:**
- The **mozaiks OSS core** (the open-source runtime: `shared_app.py`, `UnifiedWorkflowManager`, `orchestration_patterns.py`, the React shell, etc.)
- Their **workflow directory** (all the YAML + tools + JSX that project-aid generated)
- A **single Dockerfile** for the mozaiks runtime (NOT per-app — one image for the core)
- A **docker-compose.yaml** or Helm chart that includes: mozaiks runtime + MongoDB + (optional) Redis
- Their **database export** (schema + data)
- Environment variable configuration for their own LLM keys, MongoDB connection, etc.

**What changes:**
| Old Artifact | New World |
|---|---|
| `backend.Dockerfile.j2` / `frontend.Dockerfile.j2` | **REPLACED** with a single `mozaiks-runtime.Dockerfile` that's part of the OSS core, not generated per-app |
| `deploy.yaml` GitHub Action | **REPLACED** with a generic self-host deployment guide / docker-compose |
| `LLMConfig` | They bring their own API keys (env vars) |
| `DeployableServices` | They configure their own 3rd-party integrations (env vars) |
| `ConnectionStrings` | They point to their own MongoDB (env var: `MONGO_URI`) |

**What project-aid needs to produce for self-host export:**
A zip/bundle containing:
```
workflows/{AppName}/          # The full workflow directory
docker-compose.yaml           # Templated for the mozaiks core
.env.example                  # All required env vars
schema.json + seed.json       # For DB initialization
README.md                     # Setup instructions
```

---

### Scenario 4: "OSS Builder → Onboard to Mozaiks Platform"

**User journey:** Developer builds on the OSS mozaiks core locally, decides to host with us.

**What happens:**
- They already have a `workflows/{Name}/` directory that works locally
- Onboarding = uploading that workflow directory to the platform
- Platform validates it (the `validate_workflow()` method in `UnifiedWorkflowManager` already does this)
- Platform provisions their tenant (app DB, subdomain, billing)
- Their workflow is now hosted

**What's needed:**
- An onboarding API endpoint on the platform: `POST /api/onboard` that accepts a workflow bundle (zip)
- Validation pipeline (YAML schema checks, tool function checks, JSX compilation check)
- Tenant provisioner (same as Scenario 2)

---

## Mapping Old Collections to New World

| Old Collection | Old Purpose | New World |
|---|---|---|
| `FrameworkConfig` | Dockerfile templates, ports, directory structure per framework | **DEAD.** The framework is always mozaiks. One Dockerfile for the core. |
| `PythonBuiltins` | Filter stdlib from agent-generated requirements | **Static list** shipped with project-aid. No DB needed. |
| `LLMConfig` | API keys, model, Azure/DockerHub/GitHub creds | **Split:** LLM keys = env vars on platform. Azure/DockerHub/GitHub = platform-level CI/CD config, not per-app. |
| `DeployableServices` | 3rd party service env vars/secrets | **Per-app config.** Stored as env vars on the tenant, not in a global "services" collection. UI for the user to enter their own keys. |
| `ConnectionStrings` | Per-enterprise MongoDB URIs | **Platform-managed.** Platform provisions the DB and injects `MONGO_URI` into the tenant's env. Self-hosters set their own. |
| `APIKeys` | Misc API keys | **Merged with DeployableServices** or env vars. |

---

## What Stays, What Goes, What Changes

| File | Verdict | Why |
|---|---|---|
| `config.py` | **REWRITE** | Rip out all 6 DB collection reads. Config becomes: env vars for LLM keys + platform connection. No Azure/DockerHub/GitHub creds at the project-aid layer. |
| `db_manager.py` | **SIMPLIFY** | Keep: `create_database`, `apply_schema`, `seed_database`. Remove: connection string management (platform handles this), `map_data_type` stays. |
| `file_manager.py` | **SIMPLIFY** | Keep: `process_group_outputs`, `write_agent_outputs`, `clean_agent_content`. Remove: `get_framework_directory_structure` (no more framework configs), `_ensure_init_files` (framework-specific), `fix_jsx_syntax` (mozaiks owns the shell). |
| `github_operations.py` | **REMOVE for platform hosting** | Only needed for self-host export scenario. For platform hosting, the platform's own deploy pipeline handles this. Could become an optional "export to GitHub" feature. |
| `deployment_manager.py` | **REPLACE** | The entire Docker/Azure/GitHub Actions generation is irrelevant. Replace with a `TenantProvisioner` that registers workflows in the platform. |
| `templates/*.Dockerfile.j2` | **REMOVE** | No per-app Dockerfiles. The mozaiks core has one Dockerfile. |
| `AD_DevDeploy.py` | **REWRITE agents** | The 12 hardcoded agents generating full-stack apps → replace with agents generating workflow YAML + tool files. The agent chain is dramatically shorter. |
| `agent_roles.py` | **REWRITE** | Agent roles no longer map to "build models/services/controllers/routes" — they map to "define orchestrator config / design agent personas / generate tool functions / design UI components". |
| `shared_app.py` (project-aid) | **KEEP** | This is project-aid's own server. It's fine. |
| `build_pipeline/` | **KEEP + EVOLVE** | The DAG/decomposition pipeline is the right abstraction. TaskTypes need to map to the new YAML-generation vocabulary. |

---

## Ownership Boundaries

### mozaiks-core Owns (OSS — everyone gets this, including mozaiks.ai itself)
- Runtime engine (FastAPI, WebSocket, AG2 orchestration)
- Auth system (JWT, user scoping)
- Transport layer (SimpleTransport for WebSocket)
- React shell + component system (`MozaiksApp.jsx`, `ShellUIToolRenderer`)
- Workflow discovery + loading (`UnifiedWorkflowManager`)
- Multi-tenant data isolation (`app_ids.py`, `build_app_scope_filter`)
- **Pack system** — workflow dependencies, gating, journeys, nested workflow spawning (`core/workflow/pack/`)
- Plugin system (`platform_hooks.py` — the env-var switch)
- Performance/observability
- The single Dockerfile + docker-compose for self-hosting

### mozaiks-platform Owns (Proprietary — Layer 1 app running on the stack)

**Workflows (the actual product features):**
- AppGenerator (the AI builder — absorbing project-aid's logic)
- AgentGenerator (agent creation)
- ProvisioningManager (tenant provisioning, DB creation, DNS)
- SubscriptionManager (billing, plans, token metering)
- ValueEngine / LearningLoop / CampaignOrchestrator
- `_shared/` (common tools, agents, lifecycle hooks across platform workflows)

**Platform services:**
- Theme system (`/api/themes/{app_id}`, `ThemeManager`)
- OAuth webhooks (`/api/realtime/oauth/completed`)
- Build export (`/api/apps/{app_id}/builds/{build_id}/export`)
- General chat / Ask Mode (`/api/general_chats/*`)
- Billing, token metering, subscription management
- Hosted infrastructure (scaling, SSL, custom domains)
- Marketing / analytics features
- Platform frontend (shell + workflow-specific UIs)
- .NET microservices (API gateways, admin)

**Hook registration (thin layer in mozaiks repo):**
- `mozaiksai/platform/extensions.py` + `routers.py` — pass-through wrappers that register platform features into the stack via `platform_hooks.py`

### project-aid-v2 (BEING DEPRECATED → merging into mozaiks-platform)
- Legacy: 12-agent pipeline generating full-stack apps
- Its code-generation logic is being absorbed into `mozaiks-platform/app/workflows/AppGenerator/`
- The `build_pipeline/` DAG decomposition concept carries forward

### The relationship visualized
```
┌─────────────────────────────────────────────────────────┐
│                   mozaiks.ai (production)                │
│                                                         │
│  ┌──────────────────────────────────────────────────┐   │
│  │  mozaiks-platform (proprietary)                  │   │
│  │  themes, billing, journeys, OAuth, marketing     │   │
│  │                                                  │   │
│  │  ┌────────────────────────────────────────────┐  │   │
│  │  │  mozaiks-core (OSS)                        │  │   │
│  │  │  FastAPI, WebSocket, AG2, auth, workflows  │  │   │
│  │  │                                            │  │   │
│  │  │  ┌──────────┐ ┌──────────┐ ┌──────────┐   │  │   │
│  │  │  │ App A    │ │ App B    │ │ App C    │   │  │   │
│  │  │  │ workflow/ │ │ workflow/ │ │ workflow/ │   │  │   │
│  │  │  └──────────┘ └──────────┘ └──────────┘   │  │   │
│  │  └────────────────────────────────────────────┘  │   │
│  └──────────────────────────────────────────────────┘   │
│                                                         │
│  project-aid (separate service, generates workflows)    │
└─────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────┐
│  Self-hoster's server (OSS only)        │
│                                         │
│  ┌───────────────────────────────────┐  │
│  │  mozaiks-core (OSS)               │  │
│  │  (NO platform layer)              │  │
│  │                                   │  │
│  │  ┌──────────┐                     │  │
│  │  │ Their App│                     │  │
│  │  │ workflow/ │                     │  │
│  │  └──────────┘                     │  │
│  └───────────────────────────────────┘  │
└─────────────────────────────────────────┘
```

---

## Generated Workflow Structure (What Project-Aid Produces)

### Minimum Viable Workflow
```
workflows/
  {AppName}/
    orchestrator.yaml       ← REQUIRED (discovery trigger)
    agents.yaml             ← Agent personalities + system prompts
    tools.yaml              ← Tool manifest: file → function → UI
    tools/
      {tool_name}.py        ← Pure Python functions (no framework imports)
    {ToolName}.jsx          ← One React component per UI tool
```

### Full Workflow (All Optional Files)
```
workflows/
  {AppName}/
    orchestrator.yaml       ← startup config, pattern, turns
    agents.yaml             ← agent list with prompts
    tools.yaml              ← tool + lifecycle_tool manifests
    handoffs.yaml           ← speaker selection rules
    context_variables.yaml  ← domain state schema + triggers
    structured_outputs.yaml ← agent → Pydantic model mapping
    ui_config.yaml          ← visual_agents, chat_pane_agents
    hooks.yaml              ← lifecycle hooks
    tools/
      {tool_name}.py        ← pure Python tool functions
    {ToolName}.jsx          ← UI components per tool
    models.py               ← Pydantic models for MongoDB collections
    router.py               ← thin APIRouter (optional)
    __init__.py              ← optional module init
```

### Key YAML Schemas

**orchestrator.yaml** (required fields):
```yaml
workflow_name: MyApp
startup_mode: AgentDriven          # or UserDriven
orchestration_pattern: auto        # AG2 pattern
max_turns: 20
human_in_the_loop: true
initial_message: "Welcome! How can I help?"
initial_agent: GreeterAgent
```

**agents.yaml**:
```yaml
agents:
  - name: GreeterAgent
    prompt_sections:
      - id: role
        heading: Role
        content: "You are a helpful assistant..."
    max_consecutive_auto_reply: 3
    auto_tool_mode: false
    structured_outputs_required: false
```

**tools.yaml**:
```yaml
tools:
  - agent: GreeterAgent
    file: greet.py
    function: greet_user
    description: "Greet the user by name"
    tool_type: Agent_Tool
    auto_invoke: true

  - agent: GreeterAgent
    file: show_dashboard.py
    function: show_dashboard
    description: "Display the user dashboard"
    tool_type: UI_Tool
    ui:
      component: Dashboard
      mode: inline

lifecycle_tools:
  - agent: GreeterAgent
    file: on_start.py
    function: initialize_session
    trigger: on_start
```

---

## Self-Host Export Bundle (Scenario 3)

When a user chooses to self-host, project-aid generates:

```
{app-name}-self-host/
  workflows/{AppName}/             ← Their workflow directory (as above)
  docker-compose.yaml              ← mozaiks core + MongoDB + their workflow
  .env.example                     ← MONGO_URI, OPENAI_API_KEY, etc.
  init-db/
    schema.json                    ← MongoDB collection schemas
    seed.json                      ← Initial data
  README.md                        ← Setup + run instructions
```

**docker-compose.yaml** (conceptual):
```yaml
services:
  mozaiks:
    image: mozaiksai/core:latest   # Or build from OSS repo
    ports: ["8000:8000"]
    environment:
      - MONGO_URI=mongodb://mongo:27017/myapp
      - OPENAI_API_KEY=${OPENAI_API_KEY}
    volumes:
      - ./workflows:/app/workflows
    depends_on: [mongo]

  mongo:
    image: mongo:7
    volumes:
      - mongo_data:/data/db
      - ./init-db:/docker-entrypoint-initdb.d

volumes:
  mongo_data:
```

---

## FAQ: Questions From a Non-Technical Founder

### "Is mozaiks-core the same as mozaiks runtime?"

**Yes.** They're the same thing. "Runtime" just means "the software that's running." mozaiks-core IS the runtime. When someone says "the mozaiks runtime," they mean the mozaiks-core server that's actively processing requests.

### "Does mozaiks.ai itself run on the OSS core?"

**Yes, 100%.** mozaiks.ai production = mozaiks-core + the platform extensions turned on. If you deleted the entire `platform/` folder and removed the `RUNTIME_PLATFORM_EXTENSIONS` env var, you'd still have a fully working app server — you'd just lose themes, journeys, billing, and the other premium features.

### "So do I have one version of mozaiks hosted for mozaiks.ai, and then separate versions for each customer?"

**No. One server runs everything.** Think of it like Gmail. There aren't separate Gmail servers for each person — one system serves everyone, and your email address (= `app_id`) keeps your data separate. 

On mozaiks.ai production:
- One server running mozaiks-core + platform extensions
- Customer A's workflow lives in `workflows/CustomerA/`
- Customer B's workflow lives in `workflows/CustomerB/`
- Both connect to the same server, but `app_id` keeps their data in separate MongoDB databases
- Both use the same React shell (`MozaiksApp.jsx`), but with different `defaultWorkflow` and `defaultAppId`

For self-hosters: they run their OWN copy of mozaiks-core (just core, no platform), with only THEIR workflow in it.

### "What repo do I manage?"

**Three repos, already cleanly separated:**

| Repo | Layer | What's in it | Who sees it |
|---|---|---|---|
| `mozaiks` | Layer 0 (OSS) | The stack: `mozaiks.contracts` + `mozaiks.core` + `mozaiks.orchestration` + `chat-ui/` + example workflows (HelloWorld) | **Everyone.** This is the open-source engine. Self-hosters, third-party developers, and mozaiks.ai itself all run on this. |
| `mozaiks-platform` | Layer 1 (proprietary) | First-party app: AppGenerator, AgentGenerator, ProvisioningManager, SubscriptionManager, ValueEngine, LearningLoop, CampaignOrchestrator + frontend + .NET services | **You only.** This IS the mozaiks.ai product. It imports `mozaiks.core.build_runtime()` and registers its workflows. Self-hosters never see this repo. |
| `project-aid-v2` | Deprecated | The old AI builder. Being merged into `mozaiks-platform/app/workflows/AppGenerator/`. | Going away. |

**The separation is already at the repo level, not just the directory level.** `mozaiks-platform` is a separate codebase that imports from `mozaiks.*` as a dependency. This is the cleanest possible architecture — the stack doesn't know the platform exists, and the platform is just another app.

### "How does mozaiks-platform actually talk to mozaiks core? The files aren't in the same repo."

**Through a standard Python editable install (`pip install -e`).** Here's exactly how it works:

1. The `mozaiks-platform/requirements.txt` contains the line: `-e ../mozaiks`
2. When you run `pip install -r requirements.txt` in the platform's venv, pip installs the mozaiks repo as an **editable package** — meaning Python can `import mozaiks` and it resolves directly to the source files in your local `mozaiks/` repo folder
3. This creates a `.pth` file in site-packages that adds the mozaiks repo's source directory to Python's import path
4. After that, all standard Python imports work:

```python
# mozaiks-platform/app/main.py
from mozaiks.core import build_runtime as core_build_runtime
from mozaiks.orchestration import create_ai_workflow_runner

app = core_build_runtime(ai_engine=create_ai_workflow_runner())
```

**No files are copied between repos.** The platform just imports from the stack the same way you'd import any pip package (like `import fastapi`). The only difference is that during development, it's an *editable* install — so when you change code in the `mozaiks/` repo, the platform picks up the changes instantly (no reinstall needed).

The platform also has an `app/plugins/` directory that provides thin re-exports for convenience:

```python
# app/plugins/persistence.py
from mozaiks.core.persistence import AG2PersistenceManager, PersistenceManager

# app/plugins/workflow_runner.py
from mozaiks.orchestration import KernelAIWorkflowRunner as WorkflowRunner

# app/plugins/transport.py
from mozaiks.core.streaming import SimpleTransport
```

**For production deployment:** Instead of `-e ../mozaiks`, you'd pin to a published version (`mozaiks>=1.0.0`) or a git reference (`mozaiks @ git+https://github.com/BlocUnited-LLC/mozaiks@v1.0.0`).

**✅ Fixed:** The `pyproject.toml` now correctly maps both namespaces:
- `mozaiks.*` → `mozaiksai/` (public API for platform / self-hosted consumers)
- `mozaiksai.*` → `mozaiksai/` (internal backward compat while migrating)

The editable install creates a finder that resolves both import paths to the same physical `mozaiksai/` directory. All platform imports (`from mozaiks.core import build_runtime`, `from mozaiks.orchestration import create_ai_workflow_runner`) now resolve correctly.

**What about `mozaiksai/platform/` inside the mozaiks repo?** That thin directory (`extensions.py`, `routers.py`) contains pass-through wrappers that call core functions. It's essentially the "hook registration" that lets the stack know platform features exist when the env var is set. The actual platform logic (workflows, provisioning, billing, UI) lives in the `mozaiks-platform` repo.

### "When a customer deploys, what actually happens on the server?"

1. The AppGenerator workflow (inside mozaiks-platform) generates a workflow directory (YAML + Python + JSX files)
2. Those files get placed in `workflows/{CustomerAppName}/` on the mozaiks.ai server
3. The `UnifiedWorkflowManager` discovers the new directory (it scans `workflows/` automatically)
4. A `_pack/workflow_graph.json` entry is added if the workflow has dependencies on other workflows
5. A new MongoDB database is created for the customer's app data
6. An `app_id` is assigned and their frontend connects with that ID
7. Done. No new Docker containers, no new servers, no new repos. Just files in a folder + a database.

### "How does the platform_hooks.py thing work in simple terms?"

Think of `platform_hooks.py` as a **light switch**:

- **Switch OFF** (env var not set): The core runs clean. No themes, no billing, no journey gating. Perfect for OSS / self-hosting.
- **Switch ON** (`RUNTIME_PLATFORM_EXTENSIONS=mozaiksai.platform.extensions:get_bundle`): The platform features activate. Themes load, journeys enforce prerequisite workflows, OAuth webhooks work, etc.

The core never imports the platform directly. The platform plugs INTO the core via this switch. That's why self-hosters can delete the entire `platform/` folder and nothing breaks.

### "Are we set up to realize this vision?"

**Yes, the foundation is already there.** Your code already has:
- ✅ `mozaiksai/core/` — the OSS engine
- ✅ `mozaiksai/platform/` — proprietary extensions, cleanly separated
- ✅ `platform_hooks.py` — the env-var plugin switch
- ✅ `app_ids.py` — multi-tenant data isolation
- ✅ `UnifiedWorkflowManager` — auto-discovers workflows from directories
- ✅ `MozaiksApp.jsx` — a single React shell that works for any app via props
- ✅ `extensions.py` + `routers.py` — platform features that mount at startup

**What's NOT built yet (the gaps):**
- ❌ Migrating project-aid's code generation logic into `mozaiks-platform/app/workflows/AppGenerator/`
- ❌ A "deploy to mozaiks" flow (put files in `workflows/`, create DB, assign app_id)
- ❌ A self-host export bundle generator
- ❌ An onboarding flow for OSS users (validate + import existing workflows)
- ❌ The `config.py` / `db_manager.py` / `deployment_manager.py` refactor (kill the 6 DB collection dependencies)
- ❌ An advanced OSS example workflow that demonstrates `_pack/` dependencies (HelloWorld has an empty `nested_chats` array)
- ❌ Route extraction from `shared_app.py` into `mozaiksai.core.routes` APIRouter modules (so the `build_runtime()` factory includes core routes — health, chat, websocket, sessions, etc.)

### HelloWorld Safety

The core repo ships a `workflows/HelloWorld/` example. **This does NOT pollute other apps.**

**Why it's safe by design:**
1. `UnifiedWorkflowManager` discovers workflows via `Path("workflows")` **relative to the working directory** (CWD), not relative to the package installation.
2. The core now respects the `MOZAIKS_WORKFLOWS_PATH` env var — if set, it uses that path instead of the CWD-relative default.
3. When mozaiks-platform runs, its CWD is the `mozaiks-platform/` directory, which does NOT have a top-level `workflows/` folder (platform workflows live at `app/workflows/`). So HelloWorld never loads.
4. Self-hosters create their own repo with their own `workflows/` dir — HelloWorld only exists in the OSS repo as a reference implementation.

**For self-hosters who clone the OSS repo directly:** They can simply delete `workflows/HelloWorld/` and add their own workflows. HelloWorld is an example, not a dependency.
