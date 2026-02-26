# Create a Frontend With Mozaiks

**Audience:** OSS app developers using `@mozaiks/chat-ui`  
**Goal:** Get a production-ready frontend shell running fast, then customize it declaratively.

## 1) Start from the template

```bash
cp -r packages/frontend/chat-ui/template my-app
cd my-app
npm install
npm run dev
```

Open `http://localhost:3000`.

## 2) Know the key files

| File | Purpose |
|---|---|
| `template/App.jsx` | Root providers + `ChatUIProvider` + router |
| `template/adapters/mockApiAdapter.js` | No-backend demo adapter |
| `template/workflows/index.js` | Workflow registry |
| `template/workflows/hello_world/*` | Example workflow UI module |
| `template/brands/public/brand.json` | Declarative theme/header config |
| `template/brands/public/assets/*` | Logos and UI icons |
| `template/vite.config.js` | Alias `@chat-workflows` and dev server config |

## 3) Brand the UI (no code)

Edit `template/brands/public/brand.json`.

```json
{
  "name": "My App",
  "assets": {
    "logo": "logo.svg",
    "wordmark": "wordmark.svg",
    "backgroundImage": "bg.png",
    "profileFallback": "profile.svg"
  },
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

Add icon/image files under `template/brands/public/assets/`.

## 4) Header actions and icons

Supported by default:

- `header.actions[]` renders header action buttons.
- `header.actions[].icon` accepts:
  - built-in tokens: `sparkle`, `discover`, `bell`, `profile`, `settings`, `plus`, `search`
  - asset filename in `/assets` (example `docs.svg`)
  - absolute path/URL
- `header.icons.profile` sets the profile fallback image.
- `header.icons.notifications` sets notification icon.
- `header.showProfile` and `header.showNotifications` toggle each control.

Behavior hook:

- Header action clicks are handled in `packages/frontend/chat-ui/src/pages/ChatPage.js` via `handleHeaderAction`.
- Add your app-specific action routing there (open page, trigger tool, etc.).

## 5) Add workflows

1. Copy `template/workflows/hello_world` to `template/workflows/<your_workflow>`.
2. Update `name` in `template/workflows/<your_workflow>/index.js`.
3. Implement your artifact renderer in `template/workflows/<your_workflow>/<Artifact>.jsx`.
4. Register it in `template/workflows/index.js`.

Important: workflow `name` must match backend `orchestrator.yaml`.

## 6) Connect a real backend

`template/App.jsx` currently uses `mockApiAdapter`.

Replace it with your real adapter wiring (for example `RestApiAdapter`) and add auth adapter if needed.

## 7) Optional navigation config

`NavigationProvider` loads `/navigation.json` if present.

To add top-nav items declaratively, create `template/brands/public/navigation.json` and include `topNav.items`.

## 8) Artifact UX boundary in OSS

- The OSS UI keeps the **artifact panel** (`ArtifactPanel`) embedded in chat workflows.
- The standalone `ArtifactPage` route is intentionally removed.
- You keep open/close artifact functionality without a dedicated artifact page.

## 9) Final checklist

1. `npm run dev` loads with your branding and workflow list.
2. Header actions render from `brand.json`.
3. Profile/notification icons render from `assets`.
4. Workflow names match backend workflow names.
5. Mock adapter is replaced before production deployment.
