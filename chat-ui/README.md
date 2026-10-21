# @mozaiks/chat-ui

The single frontend package for the mozaiks stack — UI primitives, state machine, pages, theming, event dispatch, and technical adapters (API/auth).

## Structure

The package has no starter app directory. In this repo, `web_shell/` is the
host that mounts chat-ui (see [web_shell/README.md](../web_shell/README.md)),
and `src/main.jsx` is a small standalone demo.

```
chat-ui/
├── src/                    Package source (layout below)
├── index.html              Page that loads src/main.jsx for the standalone demo
├── vite.demo.config.js     Vite config for the standalone demo
├── vite.embed.config.js    Vite config for the self-contained embed bundle (npm run build:embed)
├── package.json            Dependencies and the package `exports` map
├── tailwind.config.js      Tailwind configuration
└── postcss.config.js       PostCSS configuration

src/
├── index.js           Full web entrypoint (`@mozaiks/chat-ui`): app shell, chat components, page renderer, auth adapter and pages, providers, hooks
├── runtimeBridge.js   Runtime WebSocket URL and auth-protocol helpers for host apps
├── main.jsx           Standalone demo entry (onboarding tour with no-op callbacks)
├── demo.css           Tailwind stylesheet that nothing imports; the demo does not use it
├── @chat-workflows/   Workflow UI registry fed by the host (see Workflow UI Components below)
├── adapters/          API, WebSocket auth, and UI tool response adapters
├── admin/             Admin portal sections, panels, and app studio model
├── app/               MozaiksApp root shell (router, providers, workflow registration)
├── assets/            Static images
├── auth/              Browser auth adapter and login/callback pages (`@mozaiks/chat-ui/auth`)
├── components/        Chat components (ChatInterface, ArtifactPanel, FluidChatLayout), layout, actions, profile
├── config/            Environment config, config validation overlay, workflow discovery
├── context/           ChatUIProvider + useChatUI hook
├── core/              Event dispatching, dynamic UI handler, WorkflowUIRouter, interactive UI cards; `core/index.js` is the `@mozaiks/chat-ui/core` entrypoint
├── embed/             Embeddable widget entry and stubs (`@mozaiks/chat-ui/embed`)
├── hooks/             Chat, session, WebSocket, and widget hooks
├── navigation/        Navigation cache, nav sections, and shell action hooks
├── pages/             ChatPage, AdminPage, AppAdminDashboard, ProfilePage, page hooks
├── platform/          Platform bridge for non-browser hosts (`@mozaiks/chat-ui/platform`)
├── primitives/        Core artifact renderers
├── providers/         Config-driven BrandingProvider, NavigationProvider
├── registry/          Generic component registry
├── services/          Service initialization (API adapter selection)
├── session/           Chat session storage and workflow chat resolution
├── shared/            Portable core exports re-exported by `core/index.js`
├── state/             uiSurfaceReducer (surface FSM)
├── styles/            Theme system, design tokens, shell CSS
├── theme/             Brand loading and BrandProvider
├── types/             TypeScript declarations
├── ui/                Web UI primitives, page renderer, screens (`@mozaiks/chat-ui/ui`)
├── utils/             Small helpers (debug flags, workflow resolution, support links)
├── widget/            GlobalChatWidgetWrapper
└── workspace/         WorkspaceLayout (`@mozaiks/chat-ui/workspace`)
```

## Workflow UI Components (`src/@chat-workflows/`)

chat-ui keeps workflow UI registration inside its own package, but the set of
workflows is supplied by the host build.

### How it works

1. **In chat-ui (this package):** `src/@chat-workflows/index.js` imports the
   Vite virtual module `virtual:mozaiks-workflow-ui`. Its default export maps
   each workflow name to a lazy import of that workflow's `ui/index.js` (or
   `ui/index.jsx`) barrel.
2. **In the host build:** the host provides that virtual module. In this repo,
   `web_shell/vite.config.js` registers `workflowUiPlugin` from
   `web_shell/workflowUi.js`, which reads the active workflow root. When the
   app's `extended_orchestration/extension_registry.json` declares
   `extends: mozaiks.default_workflow_registry`, the factory workflows in
   `factory_app/workflows/` are included first; an app workflow with the same
   name replaces the inherited one, and `{id, remove: true}` registry entries
   drop it.
3. **In standalone embed builds:** `vite.embed.config.js` aliases
   `virtual:mozaiks-workflow-ui` to `src/embed/workflowUiModulesStub.js`, so no
   workflow UI is registered.

`initializeWorkflows()` loads each barrel and registers every named export
twice: as `<WorkflowName>:<ExportName>` (the key workflow UI tools resolve) and
as the plain export name.

### Creating a workflow UI module

Put a UI barrel in the workflow folder, next to its `orchestrator.yaml` (a
folder without `orchestrator.yaml` is skipped):

```text
workflows/MyWorkflow/
├── orchestrator.yaml
├── tools.yaml
└── ui/
    ├── index.js
    └── MyComponent.jsx
```

```js
// workflows/MyWorkflow/ui/index.js
export { default as MyComponent } from './MyComponent.jsx';
```

- Export each component as a named export; the export name is the component
  name.
- Reference it from the tool's `ui` block in the workflow's `tools.yaml`
  (`component: MyComponent`, alongside the block's other fields such as
  `mode`).
- A workflow may declare only one barrel: `ui/index.js` or `ui/index.jsx`, not
  both.
- Components shared by several factory workflows live in
  `factory_app/workflows/_shared/ui/` and are re-exported from each consuming
  workflow's own barrel.

## Canonical Paths

- `src/state/uiSurfaceReducer.js` — `ask/workflow/view` surface FSM
- `src/components/chat/FluidChatLayout.jsx` — adaptive layout
- `src/context/ChatUIContext.jsx` — provider + hook
- `src/pages/ChatPage.js` — full chat page composition

## Import Surfaces

Use the package entrypoint that matches the host you are building. The package
is private and not published to npm; in this repo, `web_shell/vite.config.js`
aliases `@mozaiks/chat-ui` to `chat-ui/src`.

- `@mozaiks/chat-ui` — full web entrypoint; exports the app shell (`MozaiksApp`), chat components, the page renderer, the browser auth adapter and auth pages (`LoginPage`, `AuthCallbackPage`), providers, and hooks. Other pages, such as `ChatPage` and `AdminPage`, are not exported from the root.
- `@mozaiks/chat-ui/core` — portable shared-core entrypoint; exports transport, state, adapters, providers, and hooks intended for non-browser hosts such as React Native.
- `@mozaiks/chat-ui/platform` — platform bridge; lets a non-browser host inject synchronous storage, auth token lookup, runtime config overrides, and base URLs.
- `@mozaiks/chat-ui/ui` — web-safe UI primitives for Studio, app pages, and custom routes.
- `@mozaiks/chat-ui/workspace` — `WorkspaceLayout`.
- `@mozaiks/chat-ui/embed` — the embeddable widget (`MozaiksEmbed`).
- `@mozaiks/chat-ui/auth` — browser auth adapter plus login and callback pages.

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

React Native hosts inject their own `authAdapter` prop; the shared auth pages and
adapter require browser APIs and are not React Native components.

### Capacitor authorization transport

Capacitor renders the existing browser shell. Its app-owned `ui/index.js` may
export `createAuthAdapter(options)` and call the shared adapter with an
`authorizationTransport`:

```js
import { createAuthAdapter as createSharedAuthAdapter } from '@mozaiks/chat-ui/auth';

export function createAuthAdapter(options) {
  return createSharedAuthAdapter({
    ...options,
    authorizationTransport: {
      async open({ url, callbackUri }) {
        // Open the system browser, await the exact app callback, and return
        // its complete URL. Reject if the user cancels or the operation expires.
        return nativeBrowser.authorize({ url, callbackUri });
      },
    },
  });
}
```

`nativeBrowser` above represents the app's platform integration, not a package
export. Register callback listeners before opening the browser, handle warm and
cold launch delivery, remove owned listeners when finished, and reject duplicate
opens. Authorization must use the system browser rather than the app WebView.

The validated backend auth projection remains authoritative, including
`frontend.redirect_uri`. Native transport requires an explicitly configured
reverse-domain callback such as `org.example.app:/auth/callback`, with no
authority, query, or fragment and a path exactly matching `routes.callback`.
Register the exact URI for both sign-in and post-logout return with the issuer.
The current projection supplies one public client configuration; use an
appropriate host configuration for this native client, rather than overriding
the browser client's projected settings inside the transport.

The shared adapter owns PKCE, state, nonce, identity validation, expiry, and
session storage. Native `login()` awaits the callback and returns the validated
`returnPath`; the shared login page restores it. `handleCallback(absoluteUrl)`
also accepts a callback delivered by the host, verifies its complete base URI,
and consumes an existing transaction once. Without transport, browser navigation
and the same-origin callback requirement remain in effect. Native logout clears
the local session immediately and verifies a fresh state before returning to
the local logout route. Cancellation removes the unfinished login transaction
and permits retry. A cold callback with no surviving session transaction fails
closed; this seam does not add durable credentials or refresh-token storage.

See [OAuth for Native Apps](https://www.rfc-editor.org/info/rfc8252/),
[Capacitor Browser](https://capacitorjs.com/docs/apis/browser), and
[Capacitor App callbacks](https://capacitorjs.com/docs/apis/app).

## Dev Demo

The package has no `dev` script. Start the standalone demo with Vite directly
(the same command in every shell):

```bash
cd chat-ui
npm install
npx vite --config vite.demo.config.js
```

Then open `http://localhost:5173/demo/onboarding`. The demo renders the shared
onboarding tour with deterministic step data and no-op callbacks, so it needs
no backend. CI's onboarding demo smoke test runs the same config.

Build the self-contained embed bundle (`dist/embed.js`) with:

```bash
npm run build:embed
```

## Frontend Guides

- [Add a Page](../docs/guides/adding-pages/01-overview.md) for app pages and routes.
- [App Shell & Branding](../docs/guides/custom-brand-integration/01-overview.md) for themes, navigation, logos, and shell behavior.
