# UI Surface and Layout Architecture

**Status:** Source of truth  
**Last updated:** 2026-02-26

## Purpose

This document defines the canonical UI surface state model for the shared chat UI runtime.

It is authoritative for:

- `conversationMode`: `ask | workflow`
- `layoutMode`: `full | split | minimized | view`
- `surfaceMode`: `ASK | WORKFLOW | VIEW`
- artifact-driven layout transitions

## Implementation Paths

- `packages/frontend/chat-ui/src/state/uiSurfaceReducer.js`
- `packages/frontend/chat-ui/src/pages/ChatPage.js`
- `packages/frontend/chat-ui/src/components/chat/FluidChatLayout.jsx`
- `packages/frontend/chat-ui/src/components/chat/MobileArtifactDrawer.jsx`
- `packages/frontend/chat-ui/src/context/ChatUIContext.jsx`

## Canonical State Model

| Domain | Allowed values | Notes |
|---|---|---|
| `conversationMode` | `ask`, `workflow` | User intent mode |
| `layoutMode` | `full`, `split`, `minimized`, `view` | Layout strategy |
| `surfaceMode` | `ASK`, `WORKFLOW`, `VIEW` | Derived rendering mode |
| `artifact.status` | `inactive`, `active`, `stale` | Artifact lifecycle hint |

### Derivation

1. If `layoutMode === 'view'`, then `surfaceMode = 'VIEW'`.
2. Else if `conversationMode === 'ask'`, then `surfaceMode = 'ASK'`.
3. Else `surfaceMode = 'WORKFLOW'`.

## Hard Invariants

1. Ask mode forces `layoutMode='full'`.
2. `ARTIFACT_EMITTED` moves conversation to workflow semantics.
3. `display=view|fullscreen` maps to `layoutMode='view'`.
4. Artifact panel open/close actions normalize layout into allowed values.

## Layout Semantics

### Desktop

| `layoutMode` | Chat | Artifact | Intended use |
|---|---|---|---|
| `full` | 100% | 0% | Ask-first or no active artifact |
| `split` | 50% | 50% | Workflow editing and discussion |
| `minimized` | narrow | wide | Artifact-focused work |
| `view` | hidden | full | Full artifact presentation |

### Mobile

Mobile uses drawer states in `MobileArtifactDrawer`.

- Drawer states: `hidden`, `peek`, `expanded`
- `view` mode bypasses drawer and renders fullscreen artifact

## Event-to-Surface Mapping

`mapSurfaceEventToAction` maps runtime events into reducer actions. Core mappings:

- tool events with artifact display -> `ARTIFACT_EMITTED`
- `agui.lifecycle.RunStarted` -> `WORKFLOW_STATUS(running)`
- `agui.lifecycle.RunFinished` -> `WORKFLOW_STATUS(completed)`
- `agui.lifecycle.RunError` -> `WORKFLOW_STATUS(error)`

## View vs Sandbox Boundary

`view` is a UI mode only.

- `view` controls fullscreen rendering behavior.
- Sandbox runtime (for example preview execution infra) is a separate subsystem.
- Correct model: sandbox content can be rendered inside `VIEW`, but `VIEW` does not create or manage sandbox lifecycle.

## Ownership Boundary

### Mozaiks owns

- surface state semantics
- reducer/action rules
- event-to-surface mapping contracts

### Consuming app owns

- page composition and routing
- product-specific artifact components
- deployment/runtime operations

## Cross References

- [WORKFLOW_ARCHITECTURE.md](WORKFLOW_ARCHITECTURE.md)
- [PROCESS_AND_EVENT_MAP.md](PROCESS_AND_EVENT_MAP.md)
