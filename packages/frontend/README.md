# Frontend

Core frontend infrastructure for mozaiks.

## `chat-ui/`

`@mozaiks/chat-ui` contains:

| Module | Purpose |
|---|---|
| `components/` | Chat components, artifact panels, layout |
| `core/` | Event dispatching, dynamic UI handler, action utilities |
| `pages/` | `ChatPage`, `MyWorkflowsPage` |
| `adapters/` | API/auth adapter contracts |
| `providers/` | Branding and navigation providers |
| `primitives/` | Core artifact renderers |
| `state/` | `uiSurfaceReducer` surface state machine |
| `styles/` | Theme system and tokens |
| `context/` | `ChatUIProvider` and hook |
| `hooks/` | Widget and mode hooks |
| `widget/` | Global chat widget wrapper |

## Boundary Rule

This package defines portable runtime/state semantics.
Product-specific auth, subscriptions, profile, notifications, and app bootstrap belong in the consuming app repository.

## Guide

- `/docs/guides/CREATE_FRONTEND_WITH_MOZAIKS.md`
