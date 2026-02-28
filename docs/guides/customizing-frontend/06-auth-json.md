# Step 6 — auth.json

> **Guide:** Customizing Your Frontend · Step 6  
> **Live:** https://docs.mozaiks.ai/guides/custom-frontend/auth-json.html

File location: `brand/public/auth.json` — served at `/auth.json`

Authentication configuration for your Mozaiks app. Controls the Keycloak identity provider, login page branding, social login, roles, and session policy.

---

## How it works

Mozaiks ships with **Keycloak** as the native identity provider. `auth.json` configures both sides:

| Consumer | What it reads |
|---|---|
| **Frontend** (Vite) | `keycloak.*`, `features.*`, `branding.*` — initializes the OIDC client, renders login UI |
| **Backend** (Python) | `keycloak.*`, `roles.*` — derives OIDC authority, audience, and claim mappings for JWT validation |

The file is loaded automatically. No wiring needed — just edit and restart.

---

## Full example

```json
{
  "provider": "keycloak",

  "keycloak": {
    "authority": "http://localhost:8080",
    "realm": "mozaiks",
    "clientId": "mozaiks-app",
    "scopes": ["openid", "profile", "email"],
    "responseType": "code",
    "pkce": true,
    "logoutRedirectUri": "/"
  },

  "features": {
    "registration": true,
    "passwordReset": true,
    "rememberMe": true,
    "socialLogin": false,
    "mfa": false
  },

  "socialProviders": [],

  "roles": {
    "claimPath": "realm_access.roles",
    "default": "user",
    "admin": "admin"
  },

  "session": {
    "accessTokenLifespanMinutes": 5,
    "refreshTokenLifespanMinutes": 30,
    "ssoSessionIdleMinutes": 30,
    "ssoSessionMaxMinutes": 600
  },

  "branding": {
    "loginTitle": "Sign In",
    "registerTitle": "Create Account",
    "logo": "my_logo.svg",
    "favicon": "my_favicon.png",
    "backgroundImage": "login_bg.png",
    "theme": "dark",
    "customCss": null
  }
}
```

---

## Section reference

### `provider`

The identity provider type. Currently only `"keycloak"` is supported as the native default.

| Value | Description |
|---|---|
| `"keycloak"` | Self-hosted Keycloak (default for OSS) |

### `keycloak`

OIDC connection settings for your Keycloak instance.

| Key | Type | Default | Description |
|---|---|---|---|
| `authority` | `string` | `http://localhost:8080` | Keycloak base URL (no realm path) |
| `realm` | `string` | `mozaiks` | Keycloak realm name |
| `clientId` | `string` | `mozaiks-app` | Public client ID registered in Keycloak |
| `scopes` | `string[]` | `["openid", "profile", "email"]` | OIDC scopes to request |
| `responseType` | `string` | `code` | OAuth2 response type (always `code` for PKCE) |
| `pkce` | `boolean` | `true` | Enable PKCE (S256) — required for public clients |
| `logoutRedirectUri` | `string` | `"/"` | Redirect after logout |

!!! tip
    The backend auto-derives the OIDC discovery URL from `authority` + `realm`:
    `http://localhost:8080/realms/mozaiks/.well-known/openid-configuration`

### `features`

Toggle Keycloak login features. These map to Keycloak realm settings.

| Key | Type | Default | Description |
|---|---|---|---|
| `registration` | `boolean` | `true` | Allow self-registration on the login page |
| `passwordReset` | `boolean` | `true` | Show "Forgot Password" link |
| `rememberMe` | `boolean` | `true` | Show "Remember Me" checkbox |
| `socialLogin` | `boolean` | `false` | Enable social identity providers |
| `mfa` | `boolean` | `false` | Require multi-factor authentication |

### `socialProviders`

Array of social login configurations. Only used when `features.socialLogin` is `true`.

```json
"socialProviders": [
  {
    "provider": "google",
    "clientId": "your-google-client-id",
    "clientSecret": "your-google-client-secret"
  },
  {
    "provider": "github",
    "clientId": "your-github-client-id",
    "clientSecret": "your-github-client-secret"
  }
]
```

!!! warning
    Social provider secrets in auth.json are for **development only**. In production, configure social providers directly in the Keycloak Admin Console and leave this array empty.

### `roles`

JWT role claim configuration.

| Key | Type | Default | Description |
|---|---|---|---|
| `claimPath` | `string` | `realm_access.roles` | Dot-path to roles array in the JWT |
| `default` | `string` | `user` | Default role for new users |
| `admin` | `string` | `admin` | Admin role name |

### `session`

Token and session lifespans. These are advisory — actual enforcement is in Keycloak realm settings.

| Key | Type | Default | Description |
|---|---|---|---|
| `accessTokenLifespanMinutes` | `number` | `5` | Access token TTL |
| `refreshTokenLifespanMinutes` | `number` | `30` | Refresh token TTL |
| `ssoSessionIdleMinutes` | `number` | `30` | SSO session idle timeout |
| `ssoSessionMaxMinutes` | `number` | `600` | Maximum SSO session duration |

### `branding`

Login page branding. Asset filenames reference files in `brand/public/assets/`.

| Key | Type | Default | Description |
|---|---|---|---|
| `loginTitle` | `string` | `"Sign In"` | Login page heading |
| `registerTitle` | `string` | `"Create Account"` | Registration page heading |
| `logo` | `string` | `"mozaik_logo.svg"` | Logo shown on login page (from `assets/`) |
| `favicon` | `string` | `"mozaik.png"` | Browser tab icon for auth pages |
| `backgroundImage` | `string\|null` | `"chat_bg_template.png"` | Background image (from `assets/`) |
| `theme` | `string` | `"dark"` | `"dark"` or `"light"` — Keycloak theme variant |
| `customCss` | `string\|null` | `null` | Path to a custom CSS file for login page styling |

!!! tip
    Drop your logo SVG into `brand/public/assets/` and reference the filename here.
    The same logo is served at `/assets/my_logo.svg` for the frontend and used by
    the Keycloak realm setup script for login page branding.

---

## Config resolution order

The backend resolves auth config with this priority:

```
Environment variables  →  auth.json  →  Built-in Keycloak defaults
    (highest)                              (lowest)
```

This means:

1. **auth.json** is your app-level config (committed to your repo)
2. **Environment variables** override auth.json at deployment time
3. **Built-in defaults** ensure everything works out of the box

| Setting | env var | auth.json key | Built-in default |
|---|---|---|---|
| Authority | `MOZAIKS_OIDC_AUTHORITY` | `keycloak.authority` + `keycloak.realm` | `http://localhost:8080/realms/mozaiks` |
| Audience | `AUTH_AUDIENCE` | `keycloak.clientId` | `mozaiks-app` |
| Scope | `AUTH_REQUIRED_SCOPE` | — | `openid` |
| Roles claim | `AUTH_ROLES_CLAIM` | `roles.claimPath` | `realm_access` |

---

## File layout

```
brand/public/
├── auth.json          →  /auth.json      ← NEW
├── brand.json         →  /brand.json
├── ui.json            →  /ui.json
├── navigation.json    →  /navigation.json
├── assets/
│   ├── my_logo.svg    →  /assets/my_logo.svg    (referenced in branding.logo)
│   ├── my_favicon.png →  /assets/my_favicon.png  (referenced in branding.favicon)
│   └── login_bg.png   →  /assets/login_bg.png    (referenced in branding.backgroundImage)
└── fonts/
```

---

## Quick start

1. Copy the example above into `brand/public/auth.json`
2. Drop your logo into `brand/public/assets/`
3. Update `branding.logo` with your filename
4. Start Keycloak: `docker compose up keycloak`
5. Start your app: `python run_server.py`

That's it. The backend reads `auth.json` automatically and configures OIDC validation against your Keycloak realm.

---

## Frontend wiring

Keycloak auth is **always on**. The template `App.jsx` initializes Keycloak on every mount:

```
App mounts → fetch /auth.json → init keycloak-js → render MozaiksApp
```

### How it works

1. `App.jsx` calls `createKeycloakAuthAdapter()` which fetches `/auth.json` for Keycloak config
2. `keycloak-js` initializes with Authorization Code + PKCE flow
3. The adapter is passed to `<MozaiksApp authAdapter={authAdapter} />`
4. `chat-ui` injects `Authorization: Bearer <token>` headers on every API and WebSocket call via `window.mozaiksAuth.getAccessToken()`
5. If Keycloak is unreachable, an error screen is shown with a Retry button

### Template App.jsx (default)

```jsx
import { useState, useEffect } from 'react';
import { MozaiksApp, WebSocketApiAdapter, createKeycloakAuthAdapter } from '@mozaiks/chat-ui';
import appConfig from '../app.json';

const apiAdapter = new WebSocketApiAdapter({
  baseUrl: appConfig.apiUrl,
  wsUrl: appConfig.wsUrl,
});

export default function App() {
  const [authAdapter, setAuthAdapter] = useState(null);
  const [authReady, setAuthReady] = useState(false);
  const [authError, setAuthError] = useState(null);

  useEffect(() => {
    createKeycloakAuthAdapter()
      .then((adapter) => { setAuthAdapter(adapter); setAuthReady(true); })
      .catch((err) => { console.error(err); setAuthError(err); });
  }, []);

  if (authError) return <div>Authentication Unavailable — is Keycloak running?</div>;
  if (!authReady) return null;

  return (
    <MozaiksApp
      appName={appConfig.appName}
      defaultAppId={appConfig.appId}
      defaultWorkflow={appConfig.defaultWorkflow}
      defaultUserId={appConfig.defaultUserId}
      apiAdapter={apiAdapter}
      authAdapter={authAdapter}
    />
  );
}
```

### Disabling auth (local dev only)

Set `AUTH_ENABLED=false` in your `.env` file. The backend skips JWT validation and stamps `user_id="anonymous"`. On the frontend, remove `auth.json` from `brand/public/` — the adapter will fall back to unauthenticated mode.

!!! warning
    This is a **development-only** escape hatch. Never deploy without auth.

### Silent SSO

The template includes `brand/public/silent-check-sso.html` which enables Keycloak's silent login check. If a user has an active Keycloak session in another tab, they'll be logged in automatically without a redirect.

### Token flow

```
┌──────────────────────┐
│   keycloak-js        │  OIDC Authorization Code + PKCE
│   (keycloakAuth.js)  │─────────────────────────────────►  Keycloak
│                      │◄─────────────────────────────────  (access_token)
└──────────┬───────────┘
           │ sets window.mozaiksAuth.getAccessToken()
           ▼
┌──────────────────────┐
│   api.js             │  Authorization: Bearer <token>
│   (chat-ui adapter)  │─────────────────────────────────►  FastAPI backend
│                      │  ?access_token= (WebSocket)        (jwt_validator.py)
└──────────────────────┘
```

### Custom auth adapter

To use a different identity provider, implement the `AuthAdapter` interface:

```jsx
import { AuthAdapter } from '@mozaiks/chat-ui';

class MyAuthAdapter extends AuthAdapter {
  async getCurrentUser() { /* return { user_id, email, name, roles } */ }
  async login()          { /* redirect to your IdP */ }
  async logout()         { /* end session */ }
  async refreshToken()   { /* refresh access token */ }
  getAccessToken()       { /* return current token string */ }
  onAuthStateChange(cb)  { /* subscribe to auth state changes */ }
}
```

Then pass it to `<MozaiksApp authAdapter={new MyAuthAdapter()} />`.
