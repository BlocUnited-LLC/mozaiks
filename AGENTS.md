# mozaiks - Agent Operating Guide

> Last updated: 2026-02-26
> Repo identity: Unified Mozaiks Stack

You are a stateless coding agent inside the `mozaiks` repository.
Work from repository state and source-of-truth docs.

## 0 - Identity

`mozaiks` is the unified stack:

- `mozaiks.contracts`
- `mozaiks.core`
- `mozaiks.orchestration`

Principle:

> Contracts + Runtime + Orchestration live here. Consuming apps build product UX on top.

## 1 - Repository Shape

```text
mozaiks/
├── src/mozaiks/
│   ├── contracts/
│   ├── core/
│   └── orchestration/
├── packages/frontend/chat-ui/
├── docs/architecture/source-of-truth/
└── tests/
```

## 2 - Layer Rules

| Layer | Contains | Depends on | Never contains |
|---|---|---|---|
| `contracts` | envelopes, types, ports, schemas | nothing | runtime side effects |
| `core` | API, persistence, auth, streaming, runtime services | `contracts` | orchestration execution logic |
| `orchestration` | runner, scheduler, adapters, execution state machine | `contracts`, `core` | HTTP route ownership |

Import direction:

```text
contracts <- core <- orchestration
```

Hard rule: `core` must never import from `orchestration`.

## 3 - Ownership Boundaries

### Stack-owned

1. Contracts and event model
2. Runtime lifecycle, persistence, streaming, tool execution
3. Orchestration runtime/scheduling/adapters
4. Shared frontend runtime semantics in `packages/frontend/chat-ui`

### App-owned

1. Product page composition and domain UX
2. App-specific workflows/business logic
3. App bootstrap, routing, release operations
4. Sandbox provider operations/policies

Rule:

> `mozaiks` defines portable runtime/state semantics; consuming apps define product UX.

## 4 - Execution Modes

- Mode 1: AI Workflow (chat -> agent -> artifact)
- Mode 2: Triggered Action (button/API -> function or mini-run)
- Mode 3: Plain App (pages/CRUD/settings without AI orchestration)

Artifacts bridge the modes.

## 5 - Canonical UI Surface Contract

Source: `docs/architecture/source-of-truth/UI_SURFACE_AND_LAYOUT_ARCHITECTURE.md`

- `conversationMode`: `ask | workflow`
- `layoutMode`: `full | split | minimized | view`
- `surfaceMode`: `ASK | WORKFLOW | VIEW`

Boundary:

- `view` is a UI mode
- `view` is not sandbox runtime

Key paths:

- `packages/frontend/chat-ui/src/state/uiSurfaceReducer.js`
- `packages/frontend/chat-ui/src/components/chat/FluidChatLayout.jsx`
- `packages/frontend/chat-ui/src/components/chat/MobileArtifactDrawer.jsx`
- `packages/frontend/chat-ui/src/pages/ChatPage.js`

## 6 - Source-of-Truth Precedence

Authoritative docs:

- `docs/architecture/source-of-truth/README.md`
- `docs/architecture/source-of-truth/UI_SURFACE_AND_LAYOUT_ARCHITECTURE.md`
- `docs/architecture/source-of-truth/WORKFLOW_ARCHITECTURE.md`
- `docs/architecture/source-of-truth/PROCESS_AND_EVENT_MAP.md`
- `docs/architecture/source-of-truth/EVENT_TAXONOMY.md`
- `docs/architecture/source-of-truth/EVENT_SYSTEM_ARCHITECTURE.md`
- `docs/architecture/source-of-truth/GRAPH_INJECTION_CONTRACT.md`
- `docs/architecture/source-of-truth/LEARNING_LOOP_ARCHITECTURE.md`
- `docs/architecture/source-of-truth/APP_CREATION_GUIDE.md`

If code and docs diverge, update both in one change set.

## 7 - Drift Prevention

Before adding modules, verify the capability does not already exist:

```bash
rg -n "symbol_or_feature" src packages docs
```

Prefer extending existing modules over introducing duplicates.

## 8 - Development Verification

Run:

```bash
pytest tests/ -v
mypy src/mozaiks/
ruff check src/
```

When touching public APIs, add import smoke coverage in `tests/test_public_api_contract.py`.

## 9 - Required Output for Changes

Every completed change should include:

1. Scope (what changed and why)
2. Boundary (what was intentionally not changed)
3. Verification evidence (tests/commands)
4. API impact (imports/exports added/removed/renamed)
5. Doc alignment notes

## 10 - Common Failure Modes

| Failure | Symptom | Fix |
|---|---|---|
| Split-repo assumptions reintroduced | old package boundaries used as if still split | use unified `src/mozaiks/*` layout |
| Layer boundary drift | `core` starts owning orchestration behavior | move execution logic to `orchestration` |
| UI boundary drift | `view` treated as sandbox runtime | keep UI mode and sandbox lifecycle separate |
| Declarative/runtime drift | YAML contracts no longer match runtime behavior | update docs + runtime together |
| Duplicate primitives | new module reimplements existing behavior | search and extend existing modules |
