# Template - mozaiks frontend starter

This folder is a runnable starter app for `@mozaiks/chat-ui`.

## Quick start

```bash
cp -r packages/frontend/chat-ui/template my-app
cd my-app
npm install
npm run dev
```

Open `http://localhost:3000`.

## What is pre-wired

- `App.jsx`: providers + `ChatUIProvider` + router
- `adapters/mockApiAdapter.js`: no-backend demo adapter
- `workflows/hello_world`: example workflow UI module
- `brands/public/brand.json`: declarative theme/header config
- `vite.config.js`: `@chat-workflows` alias and dev fallback behavior

## First files to edit

1. `brands/public/brand.json` for theme, logos, header actions, header icons.
2. `workflows/index.js` to register your workflow modules.
3. `workflows/<your_workflow>/index.js` for `name`, `label`, `artifactComponent`.
4. `workflows/<your_workflow>/<Artifact>.jsx` for UI rendering.
5. `App.jsx` to replace `mockApiAdapter` with your real API/auth adapters.

## Declarative header customization

`brand.json` supports:

- `header.actions`: adds/removes top-bar action buttons
- `header.showNotifications`: toggles notification button
- `header.showProfile`: toggles profile dropdown
- `header.icons.profile`: profile fallback image
- `header.icons.notifications`: notification button icon

Example:

```json
{
  "header": {
    "actions": [
      { "id": "discover", "label": "Discover", "icon": "sparkle", "visible": true },
      { "id": "docs", "label": "Docs", "icon": "docs.svg", "visible": true }
    ],
    "icons": {
      "profile": "profile.svg",
      "notifications": "notify.svg"
    },
    "showNotifications": true,
    "showProfile": true
  }
}
```

Place icon files in `brands/public/assets/` and reference them by filename.
Built-in icon tokens are also supported: `sparkle`, `discover`, `bell`, `profile`, `settings`, `plus`, `search`.

## Hooking action behavior

Header actions emit `onAction(actionId, action)` into `ChatPage`.
Handle them in `src/pages/ChatPage.js` (`handleHeaderAction`).

## Next

See `/docs/guides/CREATE_FRONTEND_WITH_MOZAIKS.md` for a full end-to-end frontend build guide.
