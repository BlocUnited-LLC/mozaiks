# Step 5 — Wiring & Backend

> **Guide:** Customizing Your Frontend · Step 5 of 5

---

!!! tip "New to Development?"

    **Let AI help you understand wiring!** Copy this prompt into Claude Code:

    ```
    I want to understand how brand configuration is wired in my Mozaiks app.

    Please read the instruction prompt at:
    docs/instruction-prompts/custom-brand-integration/05-wiring.md
    ```

---

## App config — `app.json`

All identity and connection config lives in one shared file at `app/app.json`:

```json
{
  "appName":         "My App",
  "appId":           "my-app",
  "apiUrl":          "http://localhost:8000",
  "wsUrl":           "ws://localhost:8000"
}
```

`App.jsx` imports this file directly — no hard-coded values anywhere else. The active workflow is resolved automatically from backend config (`entry_point: true` in `orchestrator.yaml`):

```jsx
import { MozaiksApp, mockApiAdapter } from '@mozaiks/chat-ui';
import appConfig from '../app.json';

export default function App() {
  return (
    <MozaiksApp
      appName={appConfig.appName}
      defaultAppId={appConfig.appId}
      apiAdapter={mockApiAdapter}
    />
  );
}
```

---

## Navigation config — `navigation.json`

File location: `app/brand/public/navigation.json` — served at `/navigation.json`

This file controls three things: **where** your app goes on load, **what mode** it starts in, and any **extra pages** beyond the core shell.

### Example setup

In our HelloWorld example, we ship with:

```json
{
  "version": "1.0.0",
  "landing_spot": "/",
  "startup_mode": "ask",
  "pages": []
}
```

This means:

- The app opens on the **chat page** — the main conversation screen
- The user starts in **Ask mode** — a full-screen general chat, no workflow running yet
- When the user clicks the workflow toggle (if a workflow is available), the app starts a new workflow session. In our example, HelloWorld has `entry_point: true` in its `orchestrator.yaml`, so it's the workflow that fires up

### What each field does

These three settings work together but each controls something different:

| Setting | Where it lives | What it controls |
|---------|---------------|-----------------|
| `landing_spot` | `navigation.json` | **Which page** opens when the app loads. `"/"` is the chat page. If you add custom pages you could point this elsewhere |
| `startup_mode` | `navigation.json` | **Which chat mode** the chat page starts in. `"ask"` = general Q&A first. `"workflow"` = jump straight into the entry_point workflow |
| `entry_point` | `orchestrator.yaml` | **Which workflow** runs when entering workflow mode. This is set per-workflow in the backend config — not in this file |

Think of it as a chain: **landing_spot** picks the page → **startup_mode** picks the mode → **entry_point** picks the workflow.

### Switching to workflow-first

If you'd rather skip ask mode and have the app jump straight into a workflow when it loads:

```json
{
  "version": "1.0.0",
  "landing_spot": "/",
  "startup_mode": "workflow",
  "pages": []
}
```

Or just remove `startup_mode` entirely — `"workflow"` is the default when it's not set.

With this setup, the app loads and immediately connects to whichever workflow has `entry_point: true`, showing the split layout with the artifact panel. In our example, that's HelloWorld.

### `pages` — adding custom routes

The core shell always mounts the chat page (`/`) and the admin dashboard (`/admin`). Use `pages` to add extra routes:

```json
{
  "pages": [
    {
      "path": "/analytics",
      "component": "AnalyticsPage",
      "showInHeader": true,
      "order": 1,
      "meta": { "title": "Analytics", "requiresAuth": true }
    }
  ]
}
```

The `component` name must be registered in the component registry. Core routes (ChatPage, Admin) are not affected by `pages` — they always mount regardless.

---

## Swapping in a real API adapter

Swap `mockApiAdapter` for a real one once your backend is running:

```jsx
import { MozaiksApp, RestApiAdapter } from '@mozaiks/chat-ui';
import appConfig from '../app.json';

const apiAdapter = new RestApiAdapter({
  baseUrl:      appConfig.apiUrl,
  getAuthToken: async () => await auth.currentUser.getIdToken(),
});

export default function App() {
  return (
    <MozaiksApp
      appName={appConfig.appName}
      defaultAppId={appConfig.appId}
      apiAdapter={apiAdapter}
    />
  );
}
```

| `MozaiksApp` prop | Type | Description |
|-------------------|------|-------------|
| `appName` | string | Display name shown in the header |
| `defaultAppId` | string | App identifier sent to the backend |
| `apiAdapter` | object | Implement `listGeneralChats`, `sendMessage`, etc. |
| `authAdapter` | object optional | Plugs into your auth provider |
| `uiConfig` | object optional | Full config override (replaces individual props) |

---

## The `onAction` contract

Header and profile dropdown actions dispatch through `onAction(action, item)` inside `ChatPage`.  
Handle them in the `handleHeaderAction` function:

```javascript
function handleHeaderAction(action, item) {
  switch (action) {
    case 'navigate':
      navigate(item.href);
      break;
    case 'signout':
      authAdapter.signOut();
      break;
    case 'discover':
      navigate('/discover');
      break;
    default:
      console.warn('[onAction] Unhandled action:', action, item);
  }
}
```

The `action` string comes directly from the `"action"` field of the item in `ui.json`.

---

## User context

Pass a user object to `MozaiksApp` via `authAdapter` or directly when authenticated:

```javascript
const user = {
  id:          'user_abc123',
  firstName:   'Maya',
  userPhoto:   'https://…/photo.jpg',  // null if no photo
  email:       'maya@yourapp.com',
};
```

### Avatar render priority

1. `user.userPhoto` — when present and loads successfully
2. Generated initials — derived from `user.firstName` on photo failure
3. Fallback icon — the `profile.icon` SVG from `ui.json`

---

## Auth provider integration

Map your provider's user object to `{ id, firstName, userPhoto }`:

```javascript
import { useAuthState } from 'react-firebase-hooks/auth';
import { auth } from './firebase';
import { MozaiksApp, RestApiAdapter } from '@mozaiks/chat-ui';
import appConfig from '../app.json';

const apiAdapter = new RestApiAdapter({ baseUrl: appConfig.apiUrl });

export function App() {
  const [firebaseUser] = useAuthState(auth);

  const user = firebaseUser
    ? {
        id:        firebaseUser.uid,
        firstName: firebaseUser.displayName,
        userPhoto: firebaseUser.photoURL,
        email:     firebaseUser.email,
      }
    : null;

  return (
    <MozaiksApp
      appName={appConfig.appName}
      defaultAppId={appConfig.appId}
      apiAdapter={apiAdapter}
    />
  );
}
```

Works with any provider: Supabase, Clerk, Auth0, custom JWT.

---

## Custom action names

Any string in `"action"` fields is forwarded to your handler as-is:

```json
{ "id": "open-feedback", "label": "Send Feedback", "icon": "feedback.svg", "action": "open-feedback" }
```

```javascript
case 'open-feedback':
  setFeedbackDrawerOpen(true);
  break;
```

---

**Prev:** [Step 4 — Assets & Icons](04-assets.md)  
**Back to index:** [Customizing Your Frontend](01-overview.md)
