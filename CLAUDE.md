# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Shared Engineering Contract

The rules below are canonical for every agent and live in one place:
[docs/agent-engineering-contract.md](docs/agent-engineering-contract.md). Read it before
changing code. Do not restate its rules here — a second copy drifts, and two agents then
follow two different contracts.

It covers: working path constraint, ADR authoring context, AG2 ownership boundary, release
hold, contributor guidance operating system, generated deployment artifact contract,
generated persistence contract, structured-output-first contract rule, contract-declared
customization rule, and decision rules.
## Repo Boundary

This repo is the canonical runtime/platform/factory repo.

- `factory_app/workflows/` and `factory_app/refinement_harness/` are the Factory layer — the shared builder/generator workflows, agent configs, and refinement harness.
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
- app/product workflow registries may explicitly extend
  `mozaiks.default_workflow_registry`; those overlays declare only product
  deltas and `{id, remove: true}` tombstones, not copied factory registry
  entries or copied factory workflow folders
- workspace build contexts, when present, live at workspace root
  `build_context/{context_name}/` and project operator/product-specific build
  input into declared workflow context variables
- app-owned service implementations live at `app/services/`
- hosted product workspaces should consume that same contract from their own repos

Working modes:

1. **Framework/platform mode** — work on runtime, platform host, app shell contracts, package/install flows, and repo-local infrastructure
2. **Factory mode** — work on `factory_app/workflows/`, `factory_app/refinement_harness/` — builder/generator workflows, agent configs, structured outputs, refinement harness
3. **Studio mode** — work on `mozaiksai/hosts/studio.py`, `factory_app/app/ui/pages/custom/studio/`, `factory_app/app/admin/`, `factory_app/app/modules/factory_control_plane/`, `chat-ui/src/admin/` — the management interface that surfaces Factory capabilities
4. **Hosted product contract mode** — work on contracts that external hosted product workspaces consume; concrete hosted-product hosts live in those product workspaces

## Pre-Production Cleanup Policy

This repo is **not in production**. Optimize for the cleanest canonical implementation, not for preserving outdated behavior.

- Replace outdated logic instead of layering new branches on top of it.
- Remove stale prompts, docs, tests, schema fields, and dead code paths when contracts change.
- Do **not** add shims, aliases, wrappers, or fallback behavior unless the task explicitly requires it.
- When a contract changes, update the runtime, generators, docs, and tests together.

If a stale implementation conflicts with a clean architecture, prefer the clean replacement unless the user explicitly asks for preserving an existing app contract.

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
| Shared factory workflow helpers | `factory_app/workflows/_shared/` for cross-workflow Python helpers and reusable workflow UI components |
| Factory build-context path helper | `factory_app/workflows/_shared/hook_utils.py` |
| OSS reusable build pack | `factory_app/build_context/{context_name}/context.yaml` with `pack:` and explicit `assets[]` |
| App/workspace build context | `build_context/{context_name}/` beside the active `app/` root |
| App-local AI workflow logic | `workflows/{name}/` beside the active `app/` root |
| Deterministic module (CRUD/actions) | `app/modules/{name}/` in an app workspace |
| App-owned external client | `app/services/integrations/{service}_client.py` in an app workspace |
| App-owned provider adapter | `app/services/adapters/{area}/{provider}.py` in an app workspace |
| App-specific auth provider mechanic | `app/services/adapters/auth/{provider}.py` in an app workspace |
| Provider-neutral generated app auth behavior | `app/config/auth.yaml` in an authenticated app workspace |
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
| Factory bundle-quality scorers and regression eval | `factory_app/eval/` |
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
schema_version: mozaiks.structured_outputs.v1
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

`structured_output` is reserved runtime vocabulary: auto tools receive it as a
transient read-only projection. Never declare it in `context_variables.yaml`
(workflow validation rejects it); persist chosen data under your own declared
context keys.

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

## MozaiksPay for OSS Self-Hosted Apps

MozaiksPay is a **hosted service** operated by BlocUnited's `mozaiks-app`
platform. OSS self-hosters can use it as their subscription billing provider
without hosting their app on the mozaiks-app platform.

### How it works

1. Developer signs in to the mozaiks-app platform and calls
   `mozaikspay_developers.register_developer_app`.
2. They receive `mpc_...` (client_id), `mps_...` (client_secret), and
   `api_base` (the hosted platform URL).
3. They set three env vars in their self-hosted app:

   ```
   MOZAIKSPAY_API_BASE=<hosted platform URL>
   MOZAIKSPAY_CLIENT_ID=mpc_...
   MOZAIKSPAY_CLIENT_SECRET=mps_...
   ```

4. The OSS `services/integrations/mozaikspay_client.py` (generated by the
   `mozaikspay` capability pack) reads these and calls the hosted
   `/api/mozaikspay/v1/` endpoints for subscription status, billing portal,
   checkout sessions, and token top-up.

Stripe remains invisible throughout. The developer's app only ever sees the
MozaiksPay abstraction.

### What lives where

| Concern | Location |
|---------|----------|
| Developer registration + credential issuance | `mozaiks-app/app/modules/mozaikspay_developers/` |
| Provider API routes (`/api/mozaikspay/v1/`) | `mozaiks-app/app/modules/hosted_billing/backend/provider_api.py` |
| Auth middleware (checks developer creds as fallback) | `authenticate_mozaikspay_client` in provider_api.py |
| OSS client that reads credentials + calls API | `factory_app/build_context/mozaikspay/templates/services/integrations/mozaikspay_client.py` |
| Capability pack that wires a generated app to MozaiksPay | `factory_app/build_context/mozaikspay/` |

### What NOT to build in OSS for this

- Do not add a Stripe adapter in OSS. Stripe lives in `mozaiks-app` only.
- Do not build credential issuance in OSS. It requires the hosted billing
  platform to create and manage the underlying Stripe Connect infrastructure.
- Do not build a second wallet or subscription state store for self-hosted
  apps. The hosted MozaiksPay platform owns billing state; the OSS runtime
  owns token wallets and entitlement state after fulfillment commands arrive.

## Rules

Scoped rules live in `.claude/rules/`. Apply them when working in their target directories.

## Markdown Naming

Use lowercase kebab-case: `conversation-modes.md`

Exception: `README.md`, `CLAUDE.md`, `ARCHITECTURE.md`

## Autonomy

Infer reasonable implementation details from the repository and continue. The repo — its
canonical implementations, tests, ADRs, and git history — is the authority on architecture,
not the prompt that sent you. When the prompt and the repo disagree about how something is
built, read the code and believe the code.

Stop and ask ONLY when the ambiguity would materially change one of:

- a public contract or API shape
- a security or authorization boundary
- a persistence format or migration path
- an architectural decision that is expensive to reverse

Everything else: decide, implement, and report the decision in the deliverable. A question
that the repository can answer is not a question for the requester.

## Deliverable

Report these, in this order, on any change that touches code:

1. Verdict — done / blocked / partial
2. Base SHA the work started from
3. Branch and PR
4. Files changed
5. Architectural decisions made, and what was rejected
6. Tests and checks run, with exact results — not "tests pass"
7. Unresolved risks
8. Whether the PR is safe to merge, and why

Claims must be checkable from the diff or from pasted command output. A green suite that
was never run against the changed surface is not evidence.
