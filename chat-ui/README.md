# @mozaiks/chat-ui

The single frontend package for the mozaiks stack — UI primitives, state machine, pages, theming, event dispatch, and technical adapters (API/auth).

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

chat-ui keeps workflow UI registration inside its own package, but the actual workflow root is still a **host-owned injection seam**.

The registry module reads `<workflow>/ui/index.js` barrels from a build-time alias named `@chat-workflows-root`.
That keeps chat-ui decoupled from repo layout and artifact paths while letting the host decide which app bundle is active.

### How it works

1. **In chat-ui (this package):** the internal registry module scans `@chat-workflows-root/*/ui/index.{js,jsx}`.
2. **In a consuming app:** the bundler alias `@chat-workflows-root` is configured to point at the active app bundle's `workflows/` directory.
3. **In standalone/embed builds:** `@chat-workflows-root` points at an empty stub directory, so no workflow UI is registered.

### Consuming app setup (Vite example)

The consuming app must configure its bundler to resolve the injected workflow root:

```js
// vite.config.js
import { defineConfig } from 'vite';
import path from 'path';

export default defineConfig({
  resolve: {
    alias: {
      '@chat-workflows-root': path.resolve(__dirname, '../platform/workflows'),
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

## Import Surfaces

Use the package entrypoint that matches the host you are building.

- `@mozaiks/chat-ui` — full web entrypoint; exports browser UI, pages, routing helpers, browser auth adapters, and app shell components.
- `@mozaiks/chat-ui/core` — portable shared-core entrypoint; exports transport, state, adapters, providers, and hooks intended for non-browser hosts such as React Native.
- `@mozaiks/chat-ui/platform` — platform bridge; lets a non-browser host inject synchronous storage, auth token lookup, runtime config overrides, and base URLs.

These paths are declared explicitly in `package.json` via the package `exports` map. The portable surface is now formalized there rather than relying on extra top-level re-export files.

### React Native / non-browser hosts

Import the portable core surface and configure the platform bridge before mounting the provider.

```js
import { configurePlatform } from '@mozaiks/chat-ui/platform';
import { ChatUIProvider, useChatUI, useConversation } from '@mozaiks/chat-ui/core';

configurePlatform({
  storage: {
    getItem: (key) => mmkv.getString(key) ?? null,
    setItem: (key, value) => mmkv.set(key, value),
    removeItem: (key) => mmkv.delete(key),
  },
  auth: {
    getAccessToken: () => tokenStore.currentToken ?? null,
  },
  getBaseUrls: () => ({
    httpUrl: 'https://api.example.com',
    wsUrl: 'wss://api.example.com',
  }),
});
```

Use a synchronous store for `storage`. `AsyncStorage` is not suitable for the current shared core because some reads happen synchronously during initialization.

Web-only auth adapters (e.g. `mockAuthAdapter`) should not be imported from a native host. Auth is host-injected via the `authAdapter` prop.

## Dev Demo

```bash
npm run dev
```

Starts a lightweight app with mock auth/API adapters for testing without a full platform.

## Frontend Build Guide

See `/docs/guides/CREATE_FRONTEND_WITH_MOZAIKS.md` for step-by-step frontend setup and declarative `brand.json` customization.
