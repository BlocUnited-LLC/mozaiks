# Mozaiks Core: Workflow Architecture

**Last updated:** 2026-02-26  
**Status:** Source of truth

Mozaiks is a runtime for building hybrid apps where AI workflows, triggered actions, and standard app pages coexist.

---

## Scope and Terms

This document defines public runtime architecture in the `mozaiks` OSS repository.

Two axes are intentionally separate:

1. **Runtime responsibilities in this repo**: shared runtime (`core`) and execution runtime (`orchestration`)
2. **Execution modes**: `Mode 1`, `Mode 2`, `Mode 3`

Workflow YAML/stubs/components are external runtime inputs, not a repo runtime layer.

Do not treat these as the same thing.

---

## Repository Mapping

| Architecture concept | Primary implementation paths |
|---|---|
| Contracts | `src/mozaiks/contracts/` |
| Shared runtime | `src/mozaiks/core/` |
| Execution runtime | `src/mozaiks/orchestration/` |
| Shared frontend runtime/surfaces | `packages/frontend/chat-ui/src/` |

Import direction:

```text
contracts <- core <- orchestration
```

`core` must not import from `orchestration`.

---

## Runtime Structure

### Shared Runtime (`core`)

Runs once per app deployment and provides shared infrastructure used by all workflows and modes.

Typical responsibilities:

- API composition and lifecycle (`src/mozaiks/core/api/app.py`)
- Persistence ports and managers (`src/mozaiks/core/persistence/`)
- Streaming hub and transport (`src/mozaiks/core/streaming/`)
- Auth/JWT middleware (`src/mozaiks/core/auth/`)
- Workflow config loading (`src/mozaiks/core/workflows/yaml_loader.py`)
- Event dispatch/routing framework (`src/mozaiks/core/events/`)
- Artifact attachments/helpers (`src/mozaiks/core/artifacts/`)

Shared frontend surface semantics are implemented in chat-ui:

- `packages/frontend/chat-ui/src/state/uiSurfaceReducer.js`
- `packages/frontend/chat-ui/src/pages/ChatPage.js`
- `packages/frontend/chat-ui/src/components/chat/FluidChatLayout.jsx`
- `packages/frontend/chat-ui/src/components/chat/MobileArtifactDrawer.jsx`

### Execution Runtime (`orchestration`)

Instantiated for workflow execution (Mode 1 and AI-using Mode 2 paths).

Typical responsibilities:

- Runner contract implementation (`src/mozaiks/orchestration/runner.py`)
- Deterministic scheduling/state machine (`src/mozaiks/orchestration/scheduling/`)
- Vendor adapters (AG2/mock) (`src/mozaiks/orchestration/adapters/`)
- Runtime context, tool policy, resume/checkpoint behavior

### Workflow Inputs (External to Repo Runtime Layers)

Declarative files and stubs consumed by runtime.

```text
workflows/{workflow_name}/
├── backend/
│   ├── orchestrator.yaml
│   ├── agents.yaml
│   ├── tools.yaml
│   ├── events.yaml
│   ├── notifications.yaml or notifications/*.yaml
│   ├── subscription.yaml  or subscription/*.yaml
│   ├── settings.yaml      or settings/*.yaml
│   ├── context_variables.yaml
│   ├── graph_injection.yaml
│   ├── hooks.yaml
│   └── stubs/
│       ├── tools/*.py
│       └── hooks/*.py
└── frontend/
    ├── theme_config.json
    ├── assets/*
    └── components/*.{js,jsx,ts,tsx}
```

---

## AG-UI Boundary

### What AG-UI covers

AG-UI is a protocol boundary between one workflow backend and one workflow frontend.

| Concern | AG-UI relevance |
|---|---|
| Run lifecycle events (`process.*`, `task.*`) | In scope |
| Message/stream deltas | In scope |
| Tool request/response envelopes | In scope |
| Transport semantics for workflow event stream | In scope |

### What is outside AG-UI protocol scope

These are runtime/business capabilities in mozaiks, not AG-UI protocol features.

| Concern | Where it belongs in mozaiks |
|---|---|
| Multi-workflow hosting and routing | Shared runtime (`core`) |
| Event declarations and business dispatchers | Shared runtime (`core/events`) |
| Declarative notifications/subscription/settings | Workflow inputs + shared runtime dispatchers |
| Artifact persistence/query APIs | Shared runtime (`core/persistence`, `core/api`) |
| Auth middleware and policy wiring | Shared runtime (`core/auth`) |

Practical rule: comparing billing/limits directly to AG-UI protocol is a category error. Billing/limits are app/runtime features, not wire-protocol primitives.

---

## Three Execution Modes

Execution modes are invocation flows.

### Entry Point Resolution

Each app may designate at most **one** workflow as the entry point by setting `entry_point: true` in its `orchestrator.yaml`. The frontend uses this to determine which workflow to activate on load. If no workflow has `entry_point: true`, the frontend falls back to ask-mode or a workflow picker.

**Rules for setting `entry_point`:**

| Scenario | Rule |
|---|---|
| Single workflow | Always `entry_point: true` |
| Multi-workflow with journey (`workflow_graph.json`) | First step in the journey gets `entry_point: true` |
| Multi-workflow with a clear primary | The user-facing starting workflow gets it |
| Multi-workflow, all equal peers | No workflow gets `entry_point` (frontend shows picker) |

The frontend resolution chain (implemented in `resolveWorkflow.js`) is:

1. **Explicit** — URL path or resume target (always wins)
2. **Backend entry_point** — the workflow with `entry_point: true` from `/api/workflows`
3. **Singleton auto-select** — if exactly one workflow exists, use it
4. **null** — no resolution; frontend enters ask-mode or shows a workflow picker

This field is frontend-facing metadata only. The backend orchestration layer (`UniversalOrchestrator`, `WorkflowPackCoordinator`) ignores it — they execute whatever workflow they're told to run.

### Mode 1: AI Workflow

- Entry: chat/user prompt
- Runtime: full orchestration runtime
- Transport: WebSocket/event stream
- Output: persisted artifacts and run history

### Mode 2: Triggered Action

- Entry: button/API trigger
- Runtime: either direct function call or short AI mini-run
- Transport: HTTP or WebSocket (if streamed)
- Output: updated artifact, side effect, or state change

### Mode 3: Plain App

- Entry: page navigation/CRUD actions
- Runtime: app routes/components without AI orchestration
- Transport: HTTP
- Output: app data views/forms and artifact reads

---

## Artifact Bridge Across Modes

Artifacts are the shared data contract between AI and non-AI surfaces.

Typical lifecycle:

1. Mode 1 creates artifact
2. Artifact is persisted
3. Mode 3 renders artifact from persistence APIs
4. Mode 2 updates artifact (mini-run or direct action)
5. Mode 3 re-reads updated artifact

---

## Capability Decomposition (OSS Checklist)

Use this checklist when building apps on mozaiks:

1. Decompose intent into capabilities (verb + noun)
2. Classify each capability into Mode 1/2/3
3. Identify artifacts vs app-owned CRUD data
4. Route to implementation paths:
- Mode 1 -> workflow YAML/stubs/components
- Mode 2 -> trigger/action endpoint and optional mini-workflow
- Mode 3 -> page/routes/components
5. Add cross-references:
- pages read artifacts workflows write
- settings feed workflow context
- triggers start intended run/function
6. Derive graph + orchestration structures:
- `graph_injection.yaml` from context and write paths
- `_pack/workflow_graph.json` from inter-workflow dependencies

---

## Event and Config Contract

### Declaration vs consumption

- `events.yaml` declares workflow-specific events
- `notifications.yaml`, `subscription.yaml`, `settings.yaml` consume declared/core events

### Monolithic vs modular support

Supported as single file or directory for:

- `notifications`
- `subscription`
- `settings`
- `auth`

`ModularYAMLLoader` auto-detects monolithic vs modular structure.

---

## Two Different Graph Concepts

Do not conflate these:

1. `_pack/workflow_graph.json`: workflow-to-workflow journey/gating orchestration
2. `graph_injection.yaml`: FalkorDB context injection/mutation for agent memory

---

## Ownership Boundaries

| Concern | Owned by |
|---|---|
| Contracts/events/runtime execution/streaming/persistence | `mozaiks` |
| Product pages, app-specific CRUD, domain UX, release ops | Consuming app |
| Workflow content authoring (manual or automated) | Consuming app |

---

## Summary

1. `core` and `orchestration` are the repo runtime responsibilities; workflow files are external runtime inputs.
2. `Mode 1/2/3` are execution flows.
3. AG-UI is protocol scope, not a full app-runtime scope.
4. Artifacts bridge AI and non-AI user experiences.
5. `events.yaml` declares; business YAML consumes; runtime executes.
6. `workflow_graph.json` and `graph_injection.yaml` solve different graph problems.
