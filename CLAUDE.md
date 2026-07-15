# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Working Path Constraint

**Always work on `C:\Repos\BlocUnitedRepo\mozaiks` (this repo) and `C:\Repos\BlocUnitedRepo\mozaiks-app`.**
Never read from or write to OneDrive paths (`C:\Users\...\OneDrive\...`). Those are stale copies, not the working repos.

**Read [ARCHITECTURE.md](ARCHITECTURE.md) first.** That file is the source of truth for how the system works.

This repo uses layered FastAPI hosts as the canonical OSS server composition:
- `mozaiksai.hosts.runtime`
- `mozaiksai.hosts.platform`
- `mozaiksai.hosts.studio`

`mozaiksai.hosts.studio` is the Studio management interface host and the default local run target. Studio is the shared management layer — available in both local and hosted deployments. Hosted product repos compose their own app-local hosts on top of Studio; this OSS repo does not own a hosted-product FastAPI host.

**CLI and Studio are parallel interfaces**, not a superset chain. CLI owns developer tooling (filesystem, scaffolding, process management). Studio owns the management interface (workspace status, build lifecycle, artifacts, run history, config). Do not conflate them.

**`mozaiks gen` is a developer convenience**, not the canonical build lifecycle. Do not expand CLI commands to duplicate Studio surfaces (artifact review, diff, run history, promotion, build state). Those belong in Studio. The CLI hands off to Studio — it does not grow a parallel project-management surface.

The current repo layout is transitional. The canonical target architecture is
documented in
[docs/architecture/foundations/distribution-and-workspace-model.md](docs/architecture/foundations/distribution-and-workspace-model.md).
Do not reintroduce a hybrid root that mixes the starter app bundle with shared
factory workflows.

## Repo Boundary

This repo is the canonical runtime/platform/factory repo.

- `factory_app/workflows/` and `factory_app/control_plane/` are the Factory layer — the shared builder/generator workflows, agent configs, and control plane pack.
- Factory-owned build-time catalogs and packs live under
  `factory_app/build_context/{context_name}/` and are declared by that
  context's `context.yaml` `assets[]`. Workflow-local YAML stays under
  `factory_app/workflows/{WorkflowName}/`; generated app bundles do not contain
  factory catalogs.
- `factory_app/app/` is the Studio first-party app bundle — pages, modules, brand, config loaded by the Studio host.
- `factory_app/app/ui/pages/custom/studio/` contains the Studio management UI.
- `factory_app/app/admin/` is the admin portal layer — `admin_registry.yaml` declares pages, `admin/index.js` registers components, `admin/pages/` holds custom admin page React files.
- `factory_app/app/modules/factory_control_plane/` is a Studio identity stub only — no backend, no logic.
- `factory_app/` as a directory co-locates both concerns; it is not a synonym for either.
- `platform/` contains repo-local infrastructure assets only. It is not an app workspace.
- `generated/` is generator output awaiting validation and promotion; it is not runtime-loaded by default.

Canonical target:

- generated/customer apps become standalone workspaces/repositories
- shared generation core lives outside app workspaces
- app workspaces keep app bundle files under `app/` and app-local workflows at
  the workspace root under `workflows/`
- workspace build contexts, when present, live at workspace root
  `build_context/{context_name}/` and project operator/product-specific build
  input into declared workflow context variables
- app-owned service implementations live at `app/services/`
- hosted product workspaces should consume that same contract from their own repos

Working modes:

1. **Framework/platform mode** — work on runtime, platform host, app shell contracts, package/install flows, and repo-local infrastructure
2. **Factory mode** — work on `factory_app/workflows/`, `factory_app/control_plane/` — builder/generator workflows, agent configs, structured outputs, control plane pack
3. **Studio mode** — work on `mozaiksai/hosts/studio.py`, `factory_app/app/ui/pages/custom/studio/`, `factory_app/app/admin/`, `factory_app/app/modules/factory_control_plane/`, `chat-ui/src/admin/` — the management interface that surfaces Factory capabilities
4. **Hosted product contract mode** — work on contracts that external hosted product workspaces consume; concrete hosted-product hosts live in those product workspaces

## Contributor Guidance Operating System

For nontrivial OSS changes:

- choose the closest active task skill before editing. Codex-facing skills live
  under `.agents/skills/`; Claude Code-facing skills live under
  `.claude/skills/`.
- use `oss-contribution-review` when scope spans layers or the right skill is
  unclear
- include the appropriate impact section from `.claude/rules/testing.md` in the
  final report and always list tests run

## Pre-Production Cleanup Policy

This repo is **not in production**. Optimize for the cleanest canonical implementation, not for preserving outdated behavior.

- Replace outdated logic instead of layering new branches on top of it.
- Remove stale prompts, docs, tests, schema fields, and dead code paths when contracts change.
- Do **not** add shims, aliases, wrappers, or fallback behavior unless the task explicitly requires it.
- When a contract changes, update the runtime, generators, docs, and tests together.

If a stale implementation conflicts with a clean architecture, prefer the clean replacement unless the user explicitly asks for preserving an existing app contract.

## AG2 Ownership Boundary

Mozaiks uses AG2 as the long-term backbone for agentic execution. Do not build
a parallel agent framework in this repo when AG2 already owns the concept or is
the right upstream home for it. See
[docs/architecture/workflows/ag2-ownership-boundary.md](docs/architecture/workflows/ag2-ownership-boundary.md)
for the durable architecture contract and upgrade watchpoints.

AG2 should own:

- agent primitives, model calls, tools, middleware, and task execution
- multi-agent network mechanics, Hub/AgentClient behavior, channels, adapters,
  and workflow state progression
- task delegation, task lifecycle observation, event mirroring, and generic
  agent runtime observability

Mozaiks should own:

- declarative workflow contracts and strict structured-output validation
- canonical generated app, workflow, module, page, persistence, and secret
  artifact shapes
- app/runtime persistence, transport integration, tenant/session boundaries,
  Studio/platform lifecycle, and artifact promotion
- deterministic factory control-plane policy and decomposition contracts that
  define what work must be executed by AG2 agents

When a needed capability is missing from AG2, inspect AG2's current docs, APIs,
and source direction before adding runtime code here. Keep Mozaiks custom logic
as a small adapter or contract layer around AG2, document the divergence, and
track it as an AG2 compatibility watchpoint. If the missing capability is
generic agent orchestration rather than Mozaiks-specific contract enforcement,
prefer communicating the need upstream to the AG2 team instead of growing a
permanent Mozaiks substitute.

Do not introduce Mozaiks-owned replacements for AG2 Hub, AgentClient, network
adapters, task streams, task observation, delegation engines, or generic
agent scheduling unless there is no viable AG2-aligned implementation path and
the boundary is documented before or with the code change.

## Release Hold

Do **not** publish Mozaiks yet.

- Do not create or push release tags like `v0.1.0` or `v1.0.0`.
- Do not trigger `.github/workflows/release.yml`.
- Do not publish to PyPI or create a GitHub release.
- Do not interpret configured PyPI trusted publishing or a GitHub `pypi`
  environment as permission to release.
- Only prepare or execute a public release after the user explicitly says the
  repo is production-ready and wants to publish.

## Development Commands

### Setup

```bash
cp .env.example .env          # fill in OPENAI_API_KEY and MONGO_URI at minimum
pip install -e ".[dev]"       # install Python deps with dev extras
```

### Run the AI runtime (local, no Docker)

```bash
mozaiks serve .               # platform host on :8000 (default)
mozaiks serve . --host studio # Studio management host on :8000
```

Or directly via uvicorn:

```bash
uvicorn mozaiksai.hosts.studio:app --reload
```

### Run with Docker Compose (includes MongoDB + Keycloak)

```bash
cd infra/compose
docker compose up             # full stack on :8000 (app), :8080 (keycloak), :27017 (mongo)
```

`infra/` is repo-local operational scaffolding for running Mozaiks OSS itself
(Studio/builder stack). Generated apps receive their own provider-neutral
deployment artifacts at the app bundle root — they do not inherit `infra/`.
See [docs/architecture/deployment/oss-infra-and-generated-app-deployment.md](docs/architecture/deployment/oss-infra-and-generated-app-deployment.md).

### Lint

```bash
ruff check .                  # lint
ruff check --fix .            # lint + auto-fix
```

### Tests

```bash
pytest                        # run all tests
pytest tests/test_foo.py      # run a single test file
pytest tests/test_foo.py::test_bar  # run a single test
```

### CLI

```bash
mozaiks init                          # scaffold a new app workspace
mozaiks serve .                       # start platform host for current workspace
mozaiks serve . --host studio         # start Studio management host
mozaiks serve . --host platform --port 8001 --reload
```

---

## Core Services

| Service | Purpose | Key Entry Point |
|---------|---------|-----------------|
| `mozaiksai/` | AI workflow runtime | `core/workflow/orchestration_patterns.py` |
| `chat-ui/` | React chat component library | `src/app/MozaiksApp.jsx` |

Deterministic app behavior belongs in generated app/module contracts hosted by `mozaiksai.hosts.platform`, or in an optional external/generated backend connected through `AppBackendPort`.

## Where to Put Code

| If you're adding... | Put it in... |
|---------------------|--------------|
| Shared/factory AI workflow logic | `factory_app/workflows/{name}/` |
| Factory workflow-owned prompt catalogs | `factory_app/build_context/{context_name}/context.yaml` `assets[]` with `kind: catalog` |
| Factory build-context path helper | `factory_app/workflows/_shared/hook_utils.py` |
| OSS reusable build pack | `factory_app/build_context/{context_name}/context.yaml` with `pack:` and explicit `assets[]` |
| App/workspace build context | `build_context/{context_name}/` beside the active `app/` root |
| App-local AI workflow logic | `workflows/{name}/` beside the active `app/` root |
| Deterministic module (CRUD/actions) | `app/modules/{name}/` in an app workspace |
| App-owned external client | `app/services/integrations/{service}_client.py` in an app workspace |
| App-owned provider adapter | `app/services/adapters/{area}/{provider}.py` in an app workspace |
| App-specific auth provider mechanic | `app/services/adapters/auth/{provider}.py` in an app workspace |
| Provider-neutral deployment artifacts | bundle-root `Dockerfile`, `docker-compose.yml`, `env.example`, `deployment.manifest.json`, optional `.github/workflows/deploy.yml` |
| Secret manager provider support | OSS `mozaiksai.core.secrets`; app workspaces declare names in `app/security/secrets.yaml` |
| Secret management contract, names only | `app/security/secrets.yaml` in an app workspace |
| SaaS plan/tier catalog (subscriptions) | `app/config/subscriptions.yaml` in an app workspace |
| SaaS entitlement enforcement | OSS `ConfiguredEntitlementAdapter` wired from `app/config/subscriptions.yaml`; apps persist assignments through the configured data alias |
| Data contract and migrations | `app/data/contract.json`, `app/data/migrations/` in an app workspace |
| External database adapter | `app/services/adapters/database/{provider}.py` in an app workspace |
| Multi-module page | `app/ui/pages/{name}.yaml` in an app workspace |
| Runtime infrastructure | `mozaiksai/core/` |
| Framework backend adapter | `mozaiksai/core/adapters/` |
| Framework/runtime auth adapter | `mozaiksai/core/auth/` |
| Port / contract | `mozaiksai/core/ports/` |
| AG2 tool function | `mozaiksai/core/workflow/` |
| First-party Studio bundle | `factory_app/app/` |
| First-party Studio UI (Studio management) | `factory_app/app/ui/pages/custom/studio/` |
| First-party admin/Studio pages | `factory_app/app/admin/pages/` |
| Admin portal registry | `factory_app/app/admin/admin_registry.yaml` |
| Shared factory workflows | `factory_app/workflows/` |
| Generated app/workflow artifacts | `generated/` |

## App Backend Integration

The runtime communicates with external backends via a generic adapter pattern:

| Layer | File | Purpose |
|-------|------|---------|
| Port (contract) | `core/ports/app_backend.py` | `AppBackendPort` — `request()`, `emit()`, `health()` |
| Adapter (impl) | `core/adapters/http_app_backend.py` | `HttpAppBackendAdapter` — generic HTTP client |
| AG2 tools | `core/workflow/app_backend_tools.py` | `backend_request()`, `emit_event()`, `check_backend_health()` |

No hardcoded API paths or verbs in the port or adapter. Paths are passed as
arguments by the workflow tools or agent context.

**External/generated backend integration points:**
- `app_backend_url` — optional context variable for apps that choose a split backend topology
- runtime ingress endpoint — accepts validated domain events and routes matching workflow triggers
- `POST app_backend_url/api/ai/events` — optional push of workflow results back to an external/generated backend
- module/action discovery — factory workflows and contributors rely on canonical module contracts

**Canonical module system used by AppGenerator and app contributors:**
```text
modules/{module_id}/
├── module.yaml              ← required: identity, actions, capabilities
├── contracts/               ← optional companion manifests
│   ├── events.yaml          ← domain events this module may publish
│   ├── reactions.yaml       ← event reactions owned by this module
│   ├── notifications.yaml   ← notification rules per event
│   ├── settings.yaml        ← user-facing preferences schema
│   ├── admin.yaml           ← module admin panels mounted inside /admin
│   └── profile.yaml         ← optional: user profile panel declarations
├── runtime_extensions.yaml  ← optional: api_router / startup_service
└── backend/
    ├── __init__.py
    ├── handler.py           ← required: thin dispatch, one method per declared action
    ├── service.py           ← recommended: all business logic and event emission
    ├── repo.py              ← recommended: MongoDB access layer, no logic
    ├── policy.py            ← recommended: query scoping for multi-tenancy
    ├── schemas.py           ← recommended: typed request/response and document shapes
    ├── settings.py          ← optional: settings hooks
    └── admin.py             ← optional: admin panel hooks
```

**Canonical service support lane used by AppGenerator and app contributors:**
```text
services/
├── __init__.py              ← optional Python package marker
├── config.py                ← optional app-owned support config, no secrets
├── integrations/            ← thin clients for external or hosted APIs
│   ├── __init__.py
│   └── {service}_client.py
├── adapters/                ← provider-specific implementation mechanics
│   ├── __init__.py
│   └── {area}/
│       ├── __init__.py
│       └── {provider}.py
└── routes/                  ← explicit app-level routes only when required
    ├── __init__.py
    └── {route}.py
```

`app/services/` is not a module system, product service layer, persistence
plane, security plane, or entitlement authority. It holds implementation support
that modules, workflows, or host contracts call. Service files must not own
durable app facts, lifecycle transitions, user-facing actions, permissions,
emitted events, or persistence authority.

Use `services/integrations/{pack_id}_client.py` for managed-capability API clients and
generate an app-owned facade module when pages or actions need that capability.
Use `services/adapters/{area}/{provider}.py` for provider mechanics such as SDK
calls, protocol translation, signing, retries, and response normalization only
when the app itself directly owns that provider integration.
Common adapter areas include `auth/`, `source_control/`, `deployment/`, `dns/`,
`registrar/`, `cloud/`, `storage/`, `search/`, `email/`, `database/`,
`secrets/`, and `payments/`.

Do not generate hosted platform provider adapters into customer app bundles.
Mozaiks-hosted deployment, DNS/domain, billing, wallet, and platform operations
are consumed through hosted API clients/facade modules and host-owned records;
provider adapters stay in the hosted product.

Do not generate `app/services/data/`, `app/services/security/`, or entitlement
grant adapters. Data contracts live under `app/data/`; secret policy lives at
`app/security/secrets.yaml`; SaaS plans live in `app/config/subscriptions.yaml`;
runtime entitlement enforcement is handled by the OSS `ConfiguredEntitlementAdapter`.

## Generated Deployment Artifact Contract

Generated deployment artifacts are provider-neutral app-bundle root files:

- `Dockerfile`
- `docker-compose.yml`
- `env.example`
- `deployment.manifest.json`
- `.github/workflows/deploy.yml`

These files describe how the app runs and which env/CI secret names are
expected. They must never contain raw secrets, cloud tenant ids, hosted product
policy defaults, or provider execution code.

AppBuildPlan build tasks must not own these paths. They are emitted by the
DownloadAgent through the `generate_and_download` deployment contract renderer
using `deployment_profile`, `include_dockerfiles`, `include_workflow`, and
`include_compose`. Hosted products consume the manifest and apply provider
policy, secret delivery, DNS, and deployment adapters outside the generated app
bundle.

## Generated Persistence Contract

Generated app persistence is expressed as staged data-contract artifacts:

- `data_contract` is the canonical machine-readable planning object.
- `AppGenerator` writes it to `data/contract.json` when present.
- additive refinement migrations are staged under
  `data/migrations/{migration_id}.json`.
- generated modules use `backend/schemas.py` for typed document/request shapes,
  `backend/repo.py` for persistence operations, and `backend/policy.py` for
  scoping helpers.
- do not generate `backend/models.py`, `backend/models/*.py`,
  `backend/database/schema.json`, or `backend/database/seed.json`.
- the runtime injects `ctx.persistence` into `ModuleContext` when `app_id`
  exists; generated `backend/repo.py` uses
  `ctx.persistence.collection(module_id, entity_name)`.
- `ctx.db` remains absent and non-canonical; generated code must not require or
  emit it.

## Generator Output Boundary

Shared factory workflows live in `factory_app/workflows/`. Generator workflows
generate app bundles and workflow bundles, but they must not write those
outputs into active runtime paths.

Workflow resolution is single-root by default. A running host binds to one
workflow root via `MOZAIKS_WORKFLOWS_PATH` rather than auto-merging app and
factory roots. Studio defaults to `factory_app/workflows/`; app/product hosts
prefer the workspace root's `workflows/`.

Use `MOZAIKS_GENERATED_ARTIFACTS_PATH`, defaulting to:

```text
generated/
```

Canonical generated paths:

```text
generated/apps/{app_id}/{build_id}/app/
generated/workflows/{app_id}/{build_id}/{workflow_name}/
```

Promotion is the only path from generated artifacts into active app roots such
as an app workspace's `app/` bundle.

## Structured-Output-First Contract Rule

Canonical YAML contracts in Mozaiks are **structured-output-first contracts**.
They are not prose-first configuration files that agents happen to write.

- Every canonical YAML shape must be representable as a strict structured
  output model before it is treated as a runtime or generator contract.
- If a YAML shape cannot be generated repeatably and validated
  deterministically from typed structured output, it is not ready to become a
  canonical Mozaiks contract.
- Shared taxonomies such as event namespaces, target kinds, capability kinds,
  setting types, and admin panel kinds must use explicit reusable fields/enums,
  not ad hoc freeform strings scattered across prompts.
- When a contract changes, update the structured output model, generator
  prompts, runtime validation/loaders, docs, and tests together.

Use this standard for `module.yaml`, `contracts/events.yaml`, `contracts/reactions.yaml`,
`contracts/notifications.yaml`, `contracts/settings.yaml`, `contracts/admin.yaml`,
workflow YAMLs, page schemas, and any future declarative contracts.

## Contract-Declared Customization Rule

Mozaiks must allow customization, but customization is an extension of the
contract, not an escape hatch from it.

- YAML may reference bounded helper/customization stubs only through explicit
  contract fields with a defined schema and loader behavior.
- Python stubs extend backend/runtime-adjacent behavior; JS/TS stubs extend UI,
  admin, or workflow-facing frontend behavior.
- Referenced stubs must stay local to the declared app/module/workflow
  boundary and must not invent undeclared fields, side channels, or alternate
  schemas.
- Agents generating these contracts must understand both halves of the shape:
  the YAML contract and the stub entrypoint it references.
- If a customization point is generator-facing, its prompt and structured
  output model must define the exact allowable reference shape and when the
  stub is required vs optional.

## Workflow Authoring Patterns

### File Structure

For shared/factory workflows, use `factory_app/workflows/{WorkflowName}/`.
For app-local workflow overlays, use `workflows/{WorkflowName}/` beside the
active `app/` root.

```
workflows/{WorkflowName}/
├── orchestrator.yaml       # Workflow bootstrap config
├── agents.yaml             # Agent roster and prompts
├── transition_graph.yaml           # Agent-to-agent routing
├── structured_outputs.yaml # Typed outputs + registry
├── tools.yaml              # Tool bindings + UI metadata
├── context_variables.yaml  # Shared workflow state
├── ui_config.yaml          # Frontend exposure metadata (visual_agents)
├── middleware.yaml              # Lifecycle hooks (optional)
├── extended_orchestration/ # Task batch contracts (optional)
│   └── task_batches.yaml
├── tools/                  # Python tool implementations
└── ui/{WorkflowName}/      # Workflow-specific UI components
```

`ui_config.yaml` is the visibility boundary for workflow agents. Only `visual_agents`
listed there have messages and artifact-bearing outputs forwarded through the websocket
for user rendering. Agents omitted from that list run as background/silent agents.

### UI Artifact: Structured Output → Auto-Invoke Tool → UI Artifact

When an agent needs to produce a UI artifact:

**1. structured_outputs.yaml** - Define model and register to agent:
```yaml
registry:
  MyAgent: MyOutputModel  # Agent outputs this schema

models:
  MyOutputModel:
    type: model
    fields:
      field1: { type: str }
      items: { type: optional_list, items: str }
```

**2. agents.yaml** - Agent outputs structured JSON:
```yaml
- name: MyAgent
  structured_outputs_required: true
  prompt_sections:
    - id: output_format
      content: "Output ONLY valid MyOutputModel JSON..."
```

**3. tools.yaml** - Auto-invoke tool when agent outputs:
```yaml
- agent: MyAgent
  function: save_my_output
  auto_tool_call: true  # Called after agent speaks
  ui:
    component: MyComponent
    mode: artifact
```

**4. tools/my_tool.py** - Read from context, emit UI:
```python
async def save_my_output(context_variables=None):
    data = context_variables.get("structured_output")
    await transport.send_ui_tool_event(
        component_name="MyComponent",
        display_type="artifact",
        payload=transform_for_ui(data),
    )
```

## Tool Design Philosophy

**Tools are dumb. LLMs reason.**

| Do | Don't |
|----|-------|
| Save/load data | Keyword matching or heuristics |
| Validate schemas | Inference logic ("if feature contains X...") |
| Emit events | Decision trees or rule engines |
| Call external APIs | Classification logic |
| Read from `context_variables` | Hardcode business logic |

**Why?** The LLM is better at reasoning than any keyword matching or heuristic code.
Put intelligence in agent prompts + structured outputs, not in tool implementations.

```python
# BAD - Tool does reasoning
def extract_features(manifest):
    for feature in manifest["scope"]:
        if "automat" in feature.lower():  # Heuristic!
            needs_ai = True
    return {"tasks": inferred_tasks}

# GOOD - Tool reads structured output, persists/emits
async def save_my_output(context_variables=None):
    data = context_variables.get("structured_output")
    await persist(data)
    return {"success": True}
```

Use `structured_outputs.yaml` to define what the LLM should output.
The tool receives already-reasoned data and just persists/emits it.

## Don't

- Hardcode workflow behavior in the runtime
- Hardcode backend API paths in ports or adapters
- Add duplicate interfaces or aliases (make canonical changes)
- Preserve only behavior that still belongs in the current contract when making non-production changes
- Bake app-specific logic into the AI runtime
- Write inference/heuristic logic in tools (let LLMs reason instead)

## Terminology

| Term | Meaning |
|------|---------|
| AI runtime | `mozaiksai` — workflow execution layer |
| app backend | deterministic app service hosted by `mozaiksai.hosts.platform`, generated module handlers, or an optional external/generated backend |
| AppBackendPort | generic contract for runtime ↔ backend communication |
| EntitlementPort | generic contract for capability entitlement checks at module action dispatch time; default is no-op (non-SaaS apps unaffected) |
| entitlement_gate | optional `ActionDef` field — capability_id the executor checks via `EntitlementPort` before dispatching the action |
| subscriptions.yaml | SaaS-only plan catalog at `app/config/subscriptions.yaml` — declares plan_ids and the capability_ids each plan grants; loaded at startup and passed to `ConfiguredEntitlementAdapter` |
| ConfiguredEntitlementAdapter | OSS `EntitlementPort` implementation that reads `app/config/subscriptions.yaml` and its assignment-store data alias; app payment providers create or update assignment records, but do not replace the runtime entitlement adapter |
| app_backend_url | optional base URL of an external/generated backend for split deployments |
| module | self-contained deterministic capability unit declared in an app workspace `modules/` root or a generated app bundle |
| module.yaml | handler/action manifest — identity, capabilities, and action definitions; event declarations live in `events.yaml` |
| admin.yaml | optional module admin panel declarations rendered inside the unified `/admin` shell |
| unified event bus | shared in-process event transport |
| triggers | workflow start/resume declarations in `orchestrator.yaml` |

## Rules

Scoped rules live in `.claude/rules/`. Apply them when working in their target directories.

## Markdown Naming

Use lowercase kebab-case: `conversation-modes.md`

Exception: `README.md`, `CLAUDE.md`, `ARCHITECTURE.md`

## Decision Rules

When adding code, decide placement in this order:

1. Is this required for every runtime instance and independent of app semantics? → **Runtime**.
2. Is this generic harness/control-plane behavior over execution contexts, state, events, and routing? → **`mozaiksai/control_plane`**.
3. Is this app hosting, routing, sessions, pages, modules, shell config, or app workspace composition? → **Platform**.
4. Is this workspace management, build lifecycle, artifact review, run history, or configuration UI? → **Studio**.
5. Is this first-party builder behavior, app generation logic, or builder-specific harness configuration? → **`factory_app`**.
6. Is this hosted-only capability such as collaboration, billing, marketplace, deployment, or org management? → **Mozaiks App**.
7. Is this filesystem scaffolding, process management, or terminal diagnostics? → **CLI**.

Key: a feature is not CLI just because it runs locally. If it is management UI, it belongs in Studio. If it is generic intent routing across execution contexts, it belongs in the harness implementation. If it is builder-specific policy, it belongs in the factory harness pack.

