# Agent Bootstrap Prompt

> Last updated: 2026-03-12

You are starting with zero prior context. Build your understanding from the current repository state and the architecture foundations docs.

## Repository Identity

`mozaiks` is a modular app runtime and app-bundle platform:

- `platform/` - declarative app bundle consumed by the runtime
- `mozaiksai/` - AI runtime, orchestration, workflow execution, transport
- `mozaikscore/` - shared platform services such as modules, subscriptions, notifications, and config loading
- `chat-ui/` - shared web UI shell and workflow rendering layer
- `clients/mobile/` - native client implementation

## Mandatory Reading Order

1. `AGENTS.md`
2. `docs/architecture/foundations/overview.md`
3. `docs/architecture/foundations/canonical-app-structure.md`
4. `docs/architecture/foundations/app-bundle-declaratives.md`
5. `docs/architecture/foundations/workflow-architecture.md`
6. `docs/architecture/foundations/ui-surface-and-layout-architecture.md`
7. `docs/architecture/foundations/process-and-event-map.md`
8. `docs/architecture/foundations/event-taxonomy.md`
9. `docs/architecture/foundations/event-system-architecture.md`
10. `docs/architecture/foundations/workflow-authoring-contracts.md`

If the task is for an AI coding agent workflow, also read the matching file under `docs/instruction-prompts/`.

## Core Architectural Constraints

### Runtime responsibilities

- `mozaiks core` provides reusable runtime capabilities.
- `platform/` provides the app bundle consumed by the runtime.
- Workflows, modules, views, and configuration are runtime inputs, not part of the runtime implementation.

### Execution modes

- Mode 1: AI Workflow
- Mode 2: Triggered Action
- Mode 3: Plain App

Do not confuse execution modes with runtime boundaries.

### UI surface contract

- `conversationMode`: `ask | workflow`
- `layoutMode`: `full | split | minimized | view`
- `surfaceMode`: `ASK | WORKFLOW | VIEW`
- `view` is a UI surface mode, not a separate execution engine

## Working Rules

- Reuse existing modules and patterns before introducing new abstractions.
- Keep docs and implementation aligned in the same change set.
- Prefer typed contracts over prose-driven behavior.
- Keep platform semantics out of core unless the capability is clearly reusable.
- When an AI coding agent is involved, use the matching prompt pack under `docs/instruction-prompts/`.

## Validation Commands

```bash
pytest tests/ -v
```

Use narrower test selections when the change is scoped.

## Required Change Output

For each completed change, report:

1. Scope: what changed and why
2. Boundary: what was intentionally not changed
3. Verification: tests or commands run
4. API impact: imports, exports, config contracts, or file moves
5. Doc alignment: which architecture or guide docs were updated
