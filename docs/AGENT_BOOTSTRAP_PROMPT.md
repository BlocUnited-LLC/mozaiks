# Agent Bootstrap Prompt

> Last updated: 2026-02-26

You are starting with zero prior context. Work from repository state and source-of-truth docs only.

## Repository Identity

`mozaiks` is a unified OSS stack:

- `src/mozaiks/contracts` - contracts, schemas, ports
- `src/mozaiks/core` - shared runtime (API, auth, persistence, streaming, events)
- `src/mozaiks/orchestration` - workflow execution runtime (runner, adapters, scheduling)
- `packages/frontend/chat-ui` - shared frontend state, pages, UI primitives

## Mandatory Reading Order

1. `AGENTS.md`
2. `docs/architecture/source-of-truth/README.md`
3. `docs/architecture/source-of-truth/WORKFLOW_ARCHITECTURE.md`
4. `docs/architecture/source-of-truth/UI_SURFACE_AND_LAYOUT_ARCHITECTURE.md`
5. `docs/architecture/source-of-truth/PROCESS_AND_EVENT_MAP.md`
6. `docs/architecture/source-of-truth/EVENT_TAXONOMY.md`
7. `docs/architecture/source-of-truth/EVENT_SYSTEM_ARCHITECTURE.md`
8. `docs/architecture/source-of-truth/GRAPH_INJECTION_CONTRACT.md`
9. `docs/architecture/source-of-truth/LEARNING_LOOP_ARCHITECTURE.md`
10. `docs/architecture/source-of-truth/APP_CREATION_GUIDE.md`

## Core Architectural Constraints

### Runtime responsibilities

- Shared runtime is `core`.
- Execution runtime is `orchestration`.
- Workflow YAML/stubs/components are runtime inputs, not a third runtime layer.

### Execution modes

- Mode 1: AI Workflow
- Mode 2: Triggered Action
- Mode 3: Plain App

Do not confuse runtime responsibilities with execution modes.

### UI surface contract

- `conversationMode`: `ask | workflow`
- `layoutMode`: `full | split | minimized | view`
- `surfaceMode`: `ASK | WORKFLOW | VIEW`
- `view` is a UI surface mode, not sandbox runtime.

## Working Rules

- Reuse existing modules before creating new ones.
- Keep import direction valid (`contracts <- core <- orchestration`).
- Keep docs and implementation aligned in the same change set.
- Use neutral OSS language in docs and comments.

## Validation Commands

```bash
pytest tests/ -v
mypy src/mozaiks/
ruff check src/
```

## Required Change Output

For each completed change, report:

1. Scope (what changed and why)
2. Boundary (what was intentionally not changed)
3. Verification evidence (tests/commands)
4. API impact (imports/exports added/removed/renamed)
5. Doc alignment (which source-of-truth docs were updated)
