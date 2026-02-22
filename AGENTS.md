# mozaiks — Agent Operating Guide

> **Last updated:** 2026-02-22  
> **Repo identity:** The Mozaiks Stack (unified)

You are a **stateless** coding agent inside the `mozaiks` repository.

---

## 1 — Identity

`mozaiks` is the **unified Mozaiks stack** — contracts, runtime, and orchestration in one package.

This repo was created by collapsing three separate repos:
- `mozaiks-kernel` (contracts) → `mozaiks.contracts`
- `mozaiks-core` (runtime) → `mozaiks.core`
- `mozaiks-ai` (orchestration) → `mozaiks.orchestration`

---

## 2 — Package Structure

```
mozaiks/
├── src/mozaiks/
│   ├── __init__.py           # Package root, re-exports
│   │
│   ├── contracts/            # Pure data models, no I/O
│   │   ├── events.py         # EventEnvelope, DomainEvent
│   │   ├── ports/            # Abstract interfaces (AIWorkflowRunnerPort, etc.)
│   │   ├── artifacts.py
│   │   ├── replay.py
│   │   ├── sandbox.py
│   │   └── tools.py
│   │
│   ├── core/                 # Runtime infrastructure
│   │   ├── api/              # FastAPI routes
│   │   ├── auth/             # JWT validation, WebSocket auth
│   │   ├── bootstrap/        # Startup initialization
│   │   ├── config/           # Settings (pydantic-settings)
│   │   ├── context/          # RuntimeContext
│   │   ├── db/               # SQLAlchemy async
│   │   ├── engine/           # EngineFacade
│   │   ├── events/           # Event handling
│   │   ├── logging/          # Structured logging
│   │   ├── main.py           # create_app() factory
│   │   ├── persistence/      # EventStore, checkpoints
│   │   ├── plugins/          # Plugin system
│   │   ├── registry/         # Workflow registry
│   │   ├── runtime/          # Runtime context management
│   │   ├── secrets/          # Secrets vault
│   │   ├── streaming/        # WebSocket transport
│   │   ├── tools/            # Tool execution
│   │   └── workflows/        # Workflow execution
│   │
│   └── orchestration/        # AI workflow execution
│       ├── adapters/         # AG2, mock runners
│       ├── domain/           # TaskDAG, AgentSpec
│       ├── interfaces/       # Orchestration interfaces
│       ├── runner.py         # KernelAIWorkflowRunner
│       ├── scheduling/       # Deterministic scheduler, state machine
│       └── tools/            # Tool registry, auto_invoke
│
├── tests/
├── docs/
├── pyproject.toml
└── README.md
```

---

## 3 — Layer Rules

| Layer | Contains | Depends On | Never Contains |
|-------|----------|------------|----------------|
| `contracts` | EventEnvelope, DomainEvent, ports, schemas | Nothing (pure) | I/O, database, HTTP |
| `core` | FastAPI, persistence, auth, streaming | `contracts` | Orchestration logic, AG2 |
| `orchestration` | Scheduler, AG2 adapters, tool registry | `contracts`, `core` | HTTP endpoints |

### Import Direction

```
contracts  ←  core  ←  orchestration
    ↑          ↑           ↑
    └──────────┴───────────┘
         Applications
```

---

## 4 — What This Repo Owns

- Event envelope and domain event schemas
- Abstract port interfaces (AIWorkflowRunnerPort, SandboxPort, etc.)
- FastAPI application factory (`create_app`)
- Event persistence (EventStore, checkpoints)
- WebSocket streaming (transport layer)
- Authentication (JWT, WebSocket auth)
- Workflow registry and plugin system
- AI workflow runner (`KernelAIWorkflowRunner`)
- Deterministic scheduler
- AG2 adapters
- Tool registry and auto-invoke

---

## 5 — What This Repo Does NOT Own

| Responsibility | Where It Belongs |
|----------------|------------------|
| Application workflows | `mozaiks-platform` or other apps |
| Feature domains (billing, hosting) | `mozaiks-platform` |
| UI components | `mozaiks-platform` |
| Platform config | `mozaiks-platform` |

---

## 6 — Consumers

The primary consumer is `mozaiks-platform`:

```python
# mozaiks-platform imports
from mozaiks.contracts import EventEnvelope, DomainEvent
from mozaiks.core.auth import get_user_context
from mozaiks.core.persistence import EventStore
from mozaiks.orchestration import KernelAIWorkflowRunner
```

---

## 7 — Development

```bash
# Create venv
python -m venv .venv
.venv\Scripts\Activate.ps1  # Windows
source .venv/bin/activate   # Unix

# Install in editable mode with dev deps
pip install -e ".[dev]"

# Run tests
pytest tests/ -v

# Type check
mypy src/mozaiks/

# Lint
ruff check src/
```

---

## 8 — Common Agent Failure Modes

| Failure | Prevention |
|---------|------------|
| Adding app-level code | This is the stack. Apps go in `mozaiks-platform`. |
| Breaking layer dependencies | `contracts` must stay pure. Check imports. |
| Duplicating what applications should own | Workflows, UI, config belong in consumer apps. |
