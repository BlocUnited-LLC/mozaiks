# @mozaiks/chat-ui

The single frontend package for the mozaiks stack — UI primitives, state machine, pages, adapters, theming, and event dispatch.

## Structure

```
src/
├── components/        Chat components (ChatInterface, ArtifactPanel, FluidChatLayout), layout, actions
├── core/              Event dispatching, dynamic UI handler, action utilities, WorkflowUIRouter
├── pages/             ChatPage, MyWorkflowsPage
├── adapters/          API and auth adapter contracts
├── providers/         Config-driven BrandingProvider, NavigationProvider
├── primitives/        Core artifact renderers
├── state/             uiSurfaceReducer (surface FSM)
├── styles/            Theme system, design tokens
├── context/           ChatUIProvider + useChatUI hook
├── hooks/             useWidgetMode
├── widget/            GlobalChatWidgetWrapper
├── config/            Environment config, workflow discovery
├── registry/          Generic component registry
├── navigation/        Navigation cache and action hooks
├── services/          Service initialization
├── @chat-workflows/   Alias entry point (see Workflow UI section below)
├── workflows_stub/    No-op stub used when no real workflows are registered
└── main.jsx           Minimal dev demo (mock adapters)

template/
├── App.jsx            Starter app shell using ChatUIProvider + providers
├── adapters/          Mock API adapter for local dev without backend
├── workflows/         Example workflow modules (hello_world)
└── brands/public/     Declarative branding config + assets (brand.json)
```

## Workflow UI Components (`@chat-workflows` alias)

`ChatPage.js` and `WorkflowUIRouter.js` import workflow UI components via the `@chat-workflows` alias. This is an **injection seam** — chat-ui ships a no-op stub so it works standalone, but consuming apps override it at build time.

### How it works

1. **In chat-ui (this package):** `@chat-workflows` → `workflows_stub/index.js` → returns empty arrays / no-ops.
2. **In a consuming app:** The bundler alias `@chat-workflows` is configured to point at the app's `frontend/workflows/` directory, which contains real per-workflow UI components.

### Consuming app setup (Vite example)

The consuming app must configure its bundler to resolve the alias:

```js
// vite.config.js
import { defineConfig } from 'vite';
import path from 'path';

export default defineConfig({
  resolve: {
    alias: {
      '@chat-workflows': path.resolve(__dirname, '../workflows'),
    },
  },
});
```

### Creating a workflow UI module

Use the template pattern:

```text
template/workflows/
├── index.js
└── my_workflow/
    ├── index.js
    └── MyWorkflowArtifact.jsx
```

- `my_workflow/index.js` exports `{ name, label, artifactComponent, suggestions }`.
- `name` must match backend `orchestrator.yaml`.
- Register each module in `template/workflows/index.js`.
- `artifactComponent` receives `{ data, status, onAction }`.

## Canonical Paths

- `src/state/uiSurfaceReducer.js` — `ask/workflow/view` surface FSM
- `src/components/chat/FluidChatLayout.jsx` — adaptive layout
- `src/context/ChatUIContext.jsx` — provider + hook
- `src/pages/ChatPage.js` — full chat page composition

## Dev Demo

```bash
npm run dev
```

Starts a lightweight app with mock auth/API adapters for testing without a full platform.

## Frontend Build Guide

See `/docs/guides/CREATE_FRONTEND_WITH_MOZAIKS.md` for step-by-step frontend setup and declarative `brand.json` customization.
