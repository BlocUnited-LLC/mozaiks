# Keycloak Authentication Architecture

Mozaiks ships with Keycloak as the default web authentication path.

This document explains the current auth model in the repo.

It is specifically about auth ownership and runtime flow.
It does not define chat startup defaults or workflow entry selection.

## Current Ownership

### `platform/app.json`

Owns the app-facing auth declaration:

- auth provider
- Keycloak authority
- realm
- client ID
- web auth provider selection
- mobile auth settings
- native redirect configuration
- requested native scopes

In the current manifest shape, this means:

- `auth.*` owns the primary app auth declaration for web/backend defaults
- `platforms.mobile.auth.*` owns mobile-native auth behavior

`platform/app.json` does not own:

- `chat.startup_mode`
- `workflows.entry_point`

Those belong in `platform/config/ai.json`.

### `platform/config/ai.json`

Owns app-level AI boot defaults such as:

- `chat.startup_mode`
- `workflows.entry_point`

These settings affect how the app boots into chat/workflow mode.
They do not define authentication.

### Environment Variables

Own the deployment-time backend overrides, such as:

- `MOZAIKS_OIDC_AUTHORITY`
- `AUTH_AUDIENCE`
- `AUTH_REQUIRED_SCOPE`
- `AUTH_ROLES_CLAIM`

### `platform/brand/login-theme/`

Owns Keycloak login-theme assets and templates.

This is separate from the in-app shell assets under `platform/brand/assets/`.

## Runtime Flow

```text
platform/app.json
    -> host app boot config
    -> web shell creates Keycloak auth adapter
    -> user logs in through Keycloak
    -> keycloak-js obtains tokens
    -> frontend passes Bearer token to backend
    -> backend validates JWT via OIDC discovery + JWKS
```

  Separately:

  ```text
  platform/config/ai.json
    -> app-level chat startup mode
    -> app-level default workflow selection
    -> frontend boot selection only
  ```

## Frontend

The web shell uses:

- `chat-ui/src/adapters/keycloakAuth.js`

Key points:

- config is passed in from the host app
- no separate `auth.json` is used
- Keycloak uses Authorization Code + PKCE flow in the browser
- native/mobile auth selection is declared in `platform/app.json -> platforms.mobile.auth`

## Backend

The backend auth layer lives under:

- `mozaiksai/core/auth/`

Key pieces:

- config resolution
- OIDC discovery
- JWKS lookup
- JWT validation
- FastAPI route protection
- WebSocket authentication

The backend should be configured to validate against the same Keycloak realm and client that the frontend uses.

## Current Configuration Rule

Use this order of operations:

1. declare app-facing auth in `platform/app.json`
2. declare app-level chat/workflow boot defaults in `platform/config/ai.json`
3. use environment variables for deployment overrides
4. keep Keycloak login-theme assets under `platform/brand/login-theme/`

Do not reintroduce:

- `auth.json`
- `brand/public/auth.json`
- split frontend/backend auth files for the same app

Do not move `entry_point` or `chat.startup_mode` into `platform/app.json`.

## Minimal Example

```json
{
  "auth": {
    "provider": "keycloak",
    "keycloak": {
      "authority": "http://localhost:8080",
      "realm": "mozaiks",
      "clientId": "mozaiks-app",
      "themeName": "mozaiks"
    }
  }
}
```

Separate app-level boot example:

```json
{
  "chat": {
    "startup_mode": "ask"
  },
  "workflows": {
    "entry_point": "GreenRoom"
  }
}
```

That example belongs in `platform/config/ai.json`, not in `platform/app.json`.

For mobile:

```json
{
  "platforms": {
    "mobile": {
      "auth": {
        "provider": "token",
        "redirectScheme": "myapp",
        "redirectPath": "oauthredirect",
        "scopes": ["openid", "profile", "email"]
      }
    }
  }
}
```

## Verification

After auth changes:

1. verify `platform/app.json` parses
2. verify `platform/config/ai.json` still contains only app-level AI boot settings
3. verify the web shell can initialize the Keycloak adapter
4. verify login redirects to the intended Keycloak realm
5. verify backend-protected routes accept the resulting token
