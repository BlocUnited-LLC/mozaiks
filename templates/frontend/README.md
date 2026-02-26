# Mozaiks frontend template

Runnable starter app for `@mozaiks/chat-ui`. Part of the `templates/` stack.

## Quick start

```bash
# From the repo root:
cd templates/frontend
npm install
npm run dev
# → http://localhost:3000
```

> One-time: also install the chat-ui library deps if you haven't yet:
> `cd ../../packages/frontend/chat-ui && npm install`

## What connects to what

| File | Role |
|------|------|
| `../app.json` | Single config file — `appName`, `appId`, `defaultWorkflow`, `apiUrl` |
| `App.jsx` | Root component — reads `app.json`, renders `<MozaiksApp>` |
| `brand/public/brand.json` | Colors, fonts, shadows, asset filenames |
| `brand/public/ui.json` | Header, profile menu, notifications, footer |
| `brand/public/navigation.json` | Route and nav definitions |
| `brand/public/assets/` | SVG icons and images |
| `brand/public/fonts/` | Self-hosted font files |
| `workflows/` | Your workflow modules — auto-discovered |

## Files you edit

```
../app.json                   ← start here: appName, appId, defaultWorkflow, apiUrl
brand/public/brand.json       ← colors, fonts, shadows, asset filenames
brand/public/ui.json          ← header actions, profile menu, notifications, footer
brand/public/assets/          ← drop SVG icons and images here
workflows/hello_world/        ← copy this folder to add a new workflow
App.jsx                       ← swap mockApiAdapter when backend is ready
```

## Files you don't edit

- `main.jsx` — standard React root
- `vite.config.js` — Vite config, aliases, mock API server
- `tailwind.config.js` / `postcss.config.js` / `styles.css` — Tailwind setup
- `workflows/index.js` — auto-discovers workflow folders (no manual registration)

## Swapping in a real API adapter

```jsx
// App.jsx
import { MozaiksApp, RestApiAdapter } from '@mozaiks/chat-ui';
import appConfig from '../app.json';

const apiAdapter = new RestApiAdapter({ baseUrl: appConfig.apiUrl });

export default function App() {
  return (
    <MozaiksApp
      appName={appConfig.appName}
      defaultAppId={appConfig.appId}
      defaultWorkflow={appConfig.defaultWorkflow}
      apiAdapter={apiAdapter}
    />
  );
}
```

## Icon values must be filenames

`brand.json` and `ui.json` icon values must be filenames — e.g. `"sparkle.svg"`.
Bare token strings like `"sparkle"` are not supported (`ActionIcon` will warn and render nothing).

## Docs

See `docs/guides/customizing-frontend/` for the full step-by-step guide.
