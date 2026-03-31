# Mozaiks Claude Context

**Read [ARCHITECTURE.md](ARCHITECTURE.md) first.** That file is the source of truth for how the system works.

## Core Service

| Service | Purpose | Key Entry Point |
|---------|---------|-----------------|
| `mozaiksai/` | AI workflow runtime | `core/workflow/orchestration_patterns.py` |
| `chat-ui/` | React chat component library | `src/app/MozaiksApp.jsx` |

> **Note:** `mozaikscore` has been removed. App backend functionality is provided by
> external greenfield templates that communicate with the AI runtime through the
> generic `AppBackendPort` adapter (`mozaiksai/core/ports/app_backend.py`).

## Where to Put Code

| If you're adding... | Put it in... |
|---------------------|--------------|
| AI workflow logic | `platform/workflows/{name}/` |
| Business logic module | `platform/modules/{name}/` |
| Multi-module page | `platform/pages/{name}/` |
| Runtime infrastructure | `mozaiksai/core/` |
| Backend adapter | `mozaiksai/core/adapters/` |
| Port / contract | `mozaiksai/core/ports/` |
| AG2 tool function | `mozaiksai/core/workflow/` |

## App Backend Integration

The runtime communicates with external backends via a generic adapter pattern:

| Layer | File | Purpose |
|-------|------|---------|
| Port (contract) | `core/ports/app_backend.py` | `AppBackendPort` — `request()`, `emit()`, `health()` |
| Adapter (impl) | `core/adapters/http_app_backend.py` | `HttpAppBackendAdapter` — generic HTTP client |
| AG2 tools | `core/workflow/app_backend_tools.py` | `backend_request()`, `emit_event()`, `check_backend_health()` |

No hardcoded API paths or verbs in the port or adapter.  Paths are passed as
arguments by the workflow tools or agent context.

## Don't

- Hardcode workflow behavior in the runtime
- Hardcode backend API paths in ports or adapters
- Add duplicate interfaces or aliases (make canonical changes)
- Bake app-specific logic into the AI runtime

## Terminology

| Current Term | Meaning |
|--------------|---------|
| AI runtime | mozaiksai workflow execution layer |
| app backend | external CRUD service (greenfield templates) |
| AppBackendPort | generic contract for runtime ↔ backend communication |
| unified event bus | shared in-process event transport |
| module | deterministic app capability surface |
| triggers | workflow start or resume declarations in `orchestrator.yaml` |

## Rules

Scoped rules live in `.claude/rules/`. Apply them when working in their target directories.

## Markdown Naming

Use lowercase kebab-case: `conversation-modes.md`

Exception: `README.md`, `CLAUDE.md`, `ARCHITECTURE.md`
