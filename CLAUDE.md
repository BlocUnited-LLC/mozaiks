# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

**Read [ARCHITECTURE.md](ARCHITECTURE.md) first.** That file is the source of truth for how the system works.

This repo uses layered FastAPI hosts as the canonical server composition:
- `mozaiksai.hosts.runtime`
- `mozaiksai.hosts.platform`
- `mozaiksai.hosts.studio`
- `mozaiksai.hosts.mozaiks`

`mozaiksai.hosts.studio` is the Studio management interface host and the default local run target. Studio is the shared management layer — available in both local and hosted deployments. `mozaiksai.hosts.mozaiks` is the hosted Mozaiks product host — it extends Studio, not replaces it.

**CLI and Studio are parallel interfaces**, not a superset chain. CLI owns developer tooling (filesystem, scaffolding, process management). Studio owns the management interface (workspace status, build lifecycle, artifacts, run history, config). Do not conflate them.

**`mozaiks gen` is a developer convenience**, not the canonical build lifecycle. Do not expand CLI commands to duplicate Studio surfaces (artifact review, diff, run history, promotion, build state). Those belong in Studio. The CLI hands off to Studio — it does not grow a parallel project-management surface.

The current repo layout is transitional. The canonical target architecture is
documented in
[docs/architecture/foundations/distribution-and-workspace-model.md](docs/architecture/foundations/distribution-and-workspace-model.md).
Do not reintroduce a hybrid root that mixes the starter app bundle with shared
factory workflows.

## Repo Boundary

This repo is the canonical runtime/platform/factory repo.

- `platform/` is the current transitional starter/app root.
- `factory_app/app/` is the current first-party factory workspace.
- `factory_app/workflows/` is the shared builder workflow root.
- `mozaiks-platform/app/` is the current App Zero app root.
- App Zero brand/UI assets now live inside `mozaiks-platform/app/`.
- `mozaiks-platform/app-builder/` contains product planning/docs and is not runtime-loaded.
- `generated/` is generator output awaiting validation and promotion; it is not runtime-loaded by default.

Canonical target:

- generated/customer apps become standalone workspaces/repositories
- shared generation core lives outside app workspaces
- app workspaces are self-contained and keep `config/`, `ui/pages/`, `workflows/`,
  `modules/`, `ui/`, and `brand/` together
- App Zero should converge on that same self-contained workspace contract

Working modes:

1. **Framework/platform mode** — work on runtime, platform host, app shell, app bundle contracts, and `platform/`
2. **Studio mode** — work on `mozaiksai/hosts/studio.py`, `factory_app/app/ui/studio/`, `factory_app/app/modules/factory_control_plane/`, `chat-ui/src/admin/`, and the shared factory workflows in `factory_app/workflows/`
3. **Mozaiks App / product mode** — work on `mozaiksai/hosts/mozaiks.py`, `mozaiks-platform/`, App Zero, and hosted product surfaces

## Pre-Production Cleanup Policy

This repo is **not in production**. Optimize for the cleanest canonical implementation, not for legacy preservation.

- Replace outdated logic instead of layering new branches on top of it.
- Remove stale prompts, docs, tests, schema fields, and dead code paths when contracts change.
- Do **not** add backward-compatibility shims, aliases, wrappers, or fallback behavior unless the task explicitly requires it.
- When a contract changes, update the runtime, generators, docs, and tests together.

If you see a conflict between "keep old behavior around" and "make the architecture clean," prefer the clean replacement unless the user explicitly asks for compatibility.

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
uvicorn mozaiksai.hosts.mozaiks:app --reload
```

### Run with Docker Compose (includes MongoDB + Keycloak)

```bash
cd infra/compose
docker compose up             # full stack on :8000 (app), :8080 (keycloak), :27017 (mongo)
```

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
| AI workflow logic | `factory_app/workflows/{name}/` |
| Deterministic module (CRUD/actions) | `app/modules/{name}/` in an app workspace |
| Multi-module page | `app/ui/ui/pages/{name}.yaml` in an app workspace |
| Runtime infrastructure | `mozaiksai/core/` |
| Backend adapter | `mozaiksai/core/adapters/` |
| Port / contract | `mozaiksai/core/ports/` |
| AG2 tool function | `mozaiksai/core/workflow/` |
| App Zero active bundle | `mozaiks-platform/app/` |
| App Zero brand/UI extension | `mozaiks-platform/app/brand/`, `mozaiks-platform/app/ui/` |
| Shared factory workflows | `factory_app/workflows/` |
| App Zero workflow overlay | `mozaiks-platform/app/workflows/extended_orchestration/extension_registry.json` |
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
- module/action discovery — AppGenerator discovers callable capabilities from canonical module contracts

**Module system generated by AppGenerator:**
```text
modules/{pack_name}/
├── module.yaml          ← identity + nav routes + capabilities
├── admin.yaml           ← module admin panels mounted inside /admin
├── events.yaml          ← domain events published + workflow triggers
├── settings.yaml        ← user-facing preferences schema
├── notifications.yaml   ← notification rules per event
├── subscriptions.yaml   ← tier gates
└── backend/
    ├── handler.py       ← canonical action handler implementation
    ├── settings.py      ← optional settings validation/change hooks
    ├── subscriptions.py ← optional subscription reaction hooks
    ├── notifications.py ← optional notification audience/render hooks
    └── admin.py         ← optional custom admin panel data hooks
```

## Generator Output Boundary

Shared factory workflows live in `factory_app/workflows/`. App Zero keeps only a product-specific extended-orchestration overlay under
`mozaiks-platform/app/workflows/extended_orchestration/extension_registry.json`. Generator workflows
generate app bundles and workflow bundles, but they must not write those
outputs into active runtime paths.

Workflow loading is multi-root by default:

- active app root `workflows/` first
- shared `factory_app/workflows/` second
- `MOZAIKS_WORKFLOW_ROOTS` may override that order explicitly

That allows App Zero to reference both shared generator workflows and
product-owned workflows through its local overlay registry without copying
shared workflow directories back into `mozaiks-platform/app/workflows/`.

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
as `platform/` or `mozaiks-platform/app/`.

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

Use this standard for `module.yaml`, `events.yaml`, `subscriptions.yaml`,
`notifications.yaml`, `settings.yaml`, `admin.yaml`, workflow YAMLs, page
schemas, and any future declarative contracts.

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

```
factory_app/workflows/{WorkflowName}/
├── orchestrator.yaml       # Workflow bootstrap config
├── agents.yaml             # Agent roster and prompts
├── handoffs.yaml           # Agent-to-agent routing
├── structured_outputs.yaml # Typed outputs + registry
├── tools.yaml              # Tool bindings + UI metadata
├── context_variables.yaml  # Shared workflow state
├── extended_orchestration/mfj_extension.json # MFJ triggers (optional)
├── tools/                  # Python tool implementations
└── ui/{WorkflowName}/      # Workflow-specific UI components
```

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
- Preserve legacy logic "just in case" when making non-production changes
- Bake app-specific logic into the AI runtime
- Write inference/heuristic logic in tools (let LLMs reason instead)

## Terminology

| Term | Meaning |
|------|---------|
| AI runtime | `mozaiksai` — workflow execution layer |
| app backend | deterministic app service hosted by `mozaiksai.hosts.platform`, generated module handlers, or an optional external/generated backend |
| AppBackendPort | generic contract for runtime ↔ backend communication |
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


