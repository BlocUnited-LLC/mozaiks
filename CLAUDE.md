# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

**Read [ARCHITECTURE.md](ARCHITECTURE.md) first.** That file is the source of truth for how the system works.
Read `ARCHITECTURE_BOUNDARIES.md` before making structural changes.

This repo uses layered FastAPI hosts as the canonical server composition:
- `runtime_app.py`
- `platform_app.py`
- `studio_app.py`
- `mozaiks_app.py`

`studio_app.py` is the local/private builder host and the default local run target. `mozaiks_app.py` is the hosted Mozaiks product host.

## Repo Boundary

This repo is the canonical runtime/platform/factory repo.

- `platform/` is the default OSS/sample active app root.
- `mozaiks-platform/app/` is the active App Zero app root.
- `mozaiks-platform/brand/` and `mozaiks-platform/ui/` are product workspace
  assets loaded relative to `mozaiks-platform/app/`.
- `mozaiks-platform/generated/` is generator output awaiting validation and
  promotion; it is not runtime-loaded by default.

Working modes:

1. **Framework/platform mode** — work on runtime, platform host, app shell, app bundle contracts, and `platform/`
2. **Studio/product mode** — work on `studio_app.py`, `mozaiks_app.py`, App Zero, AppGenerator, AgentGenerator, and product builder surfaces

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
python run_server.py          # starts FastAPI + Uvicorn on :8000
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
mozaiks init                  # scaffold a new project
```

---

## Core Services

| Service | Purpose | Key Entry Point |
|---------|---------|-----------------|
| `mozaiksai/` | AI workflow runtime | `core/workflow/orchestration_patterns.py` |
| `chat-ui/` | React chat component library | `src/app/MozaiksApp.jsx` |

Deterministic app behavior belongs in generated app/module contracts hosted by `platform_app.py`, or in an optional external/generated backend connected through `AppBackendPort`.

## Where to Put Code

| If you're adding... | Put it in... |
|---------------------|--------------|
| AI workflow logic | `platform/workflows/{name}/` |
| Deterministic module (CRUD/actions) | `platform/modules/{name}/` |
| Multi-module page | `platform/pages/{name}.yaml` |
| Runtime infrastructure | `mozaiksai/core/` |
| Backend adapter | `mozaiksai/core/adapters/` |
| Port / contract | `mozaiksai/core/ports/` |
| AG2 tool function | `mozaiksai/core/workflow/` |
| App Zero active bundle | `mozaiks-platform/app/` |
| App Zero brand/UI extension | `mozaiks-platform/brand/`, `mozaiks-platform/ui/` |
| Generator/platform work | `mozaiks-platform/app/workflows/` |
| Generated app/workflow artifacts | `mozaiks-platform/generated/` |

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

Builder workflows live in `mozaiks-platform/app/workflows/` because App Zero is
itself a Mozaiks app. They generate app bundles and workflow bundles, but they
must not write those outputs into active runtime paths.

Use `MOZAIKS_GENERATED_ARTIFACTS_PATH`, defaulting to:

```text
mozaiks-platform/generated/
```

Canonical generated paths:

```text
mozaiks-platform/generated/apps/{app_id}/{build_id}/app/
mozaiks-platform/generated/workflows/{app_id}/{build_id}/{workflow_name}/
```

Promotion is the only path from generated artifacts into active app roots such
as `platform/` or `mozaiks-platform/app/`.

## Workflow Authoring Patterns

### File Structure

```
platform/workflows/{WorkflowName}/
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
| app backend | deterministic app service hosted by `platform_app.py`, generated module handlers, or an optional external/generated backend |
| AppBackendPort | generic contract for runtime ↔ backend communication |
| app_backend_url | optional base URL of an external/generated backend for split deployments |
| module | self-contained deterministic capability unit declared in `platform/modules/` or a generated app bundle |
| module.yaml | handler/action manifest — identity, capabilities, and action definitions; event declarations live in `events.yaml` |
| admin.yaml | optional module admin panel declarations rendered inside the unified `/admin` shell |
| unified event bus | shared in-process event transport |
| triggers | workflow start/resume declarations in `orchestrator.yaml` |

## Rules

Scoped rules live in `.claude/rules/`. Apply them when working in their target directories.

## Markdown Naming

Use lowercase kebab-case: `conversation-modes.md`

Exception: `README.md`, `CLAUDE.md`, `ARCHITECTURE.md`
