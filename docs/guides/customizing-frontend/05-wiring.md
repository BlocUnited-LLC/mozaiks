# Step 5 — Wiring & Backend

> **Guide:** Customizing Your Frontend · Step 5 of 5  
> **Live:** https://docs.mozaiks.ai/guides/custom-frontend/wiring.html

---

## App config — `app.json`

All identity and connection config lives in one shared file at `templates/app.json`:

```json
{
  "appName":         "My App",
  "appId":           "my-app",
  "defaultWorkflow": "hello_world",
  "apiUrl":          "http://localhost:8000"
}
```

`App.jsx` imports this file directly — no hard-coded values anywhere else:

```jsx
import { MozaiksApp, mockApiAdapter } from '@mozaiks/chat-ui';
import appConfig from '../app.json';

export default function App() {
  return (
    <MozaiksApp
      appName={appConfig.appName}
      defaultAppId={appConfig.appId}
      defaultWorkflow={appConfig.defaultWorkflow}
      apiAdapter={mockApiAdapter}
    />
  );
}
```

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
      defaultWorkflow={appConfig.defaultWorkflow}
      apiAdapter={apiAdapter}
    />
  );
}
```

| `MozaiksApp` prop | Type | Description |
|-------------------|------|-------------|
| `appName` | string | Display name shown in the header |
| `defaultAppId` | string | App identifier sent to the backend |
| `defaultWorkflow` | string | Workflow to activate on load |
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
      defaultWorkflow={appConfig.defaultWorkflow}
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
