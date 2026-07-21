# Brokered OIDC Authentication Architecture

Mozaiks ships with provider-neutral OIDC/JWT authentication primitives for web
apps. Generated apps use an OIDC PKCE browser adapter plus backend JWT
validation; the selected identity provider or broker is an operator decision.

This document explains the current auth model in the repo.

It is specifically about auth ownership and runtime flow.
It does not define chat startup defaults or workflow entry selection.

## Current Ownership

### `app/app.json`

Owns app identity, admin bootstrap, target enablement, and the coarse
`authRequired` signal.

`app/app.json` does not own:

- OIDC provider selection
- callback mechanics
- token storage behavior
- social-login provider mechanics
- `chat.chat_startup_mode`
- `workflows.entry_point`

AI boot settings belong in `app/config/ai.json`. Auth behavior belongs in
`app/config/auth.yaml` for authenticated apps.

### `app/config/auth.yaml`

Owns the provider-neutral generated-app auth behavior contract.

Authenticated generated apps use:

```yaml
schema_version: mozaiks.auth.v1
auth_required: true
strategy: oidc
mode: brokered_oidc
signup_enabled: false
routes:
  login: /login
  callback: /auth/callback
  logout: /login
  post_login_default: /
frontend:
  adapter: oidc_pkce
  client_id_env: VITE_OIDC_CLIENT_ID
  authority_env: VITE_OIDC_AUTHORITY
  discovery_url_env: VITE_OIDC_DISCOVERY_URL
  redirect_uri_env: VITE_OIDC_REDIRECT_URI
  scope_env: VITE_OIDC_SCOPE
  default_scopes: [openid, profile, email]
runtime:
  provider_env: AUTH_PROVIDER
  enabled_env: AUTH_ENABLED
  authority_env: MOZAIKS_OIDC_AUTHORITY
  discovery_url_env: MOZAIKS_OIDC_DISCOVERY_URL
  issuer_env: AUTH_ISSUER
  jwks_url_env: AUTH_JWKS_URL
identity_providers: []
login_methods:
  - id: broker
    kind: oidc_redirect
    label: Sign in
    primary: true
customization:
  login_theme_source: brand/theme_config.json
  upstream_provider_setup: host_or_operator
```

This file carries names and routes only. It must not contain provider URLs,
tenant ids, client secrets, Keycloak admin credentials, Google OAuth secrets, or
hosted-product policy.

`mode` describes the product access model, not a concrete provider:

- `brokered_oidc` — default for authenticated apps; one OIDC redirect and the
  broker handles Google, Microsoft, password, MFA, and other login choices.
- `public_self_signup` — same OIDC flow, with account creation enabled by the
  upstream provider or broker.
- `private_workspace` — invite/admin controlled access; app code still validates
  OIDC/JWT tokens and enforces app policy after login.
- `enterprise_sso` — operator configured enterprise provider behind OIDC.
- `multi_provider` — advanced operator-owned provider routing; do not emit
  provider credentials or direct social login code into the app bundle.

`login_methods` are declarative UI hints. They may label a sign-in, create
account, or enterprise SSO action, but they do not carry provider endpoints,
tenant IDs, client secrets, or OAuth implementation code.

## Adapter Extension Model

The default generated app path is intentionally boring:

```text
app/config/auth.yaml
ui/auth/authAdapter.js
deployment env handles
```

That is enough for Keycloak, Entra, Auth0, Okta, hosted identity, Google via a
broker, Microsoft via a broker, and most enterprise SSO setups because the app
speaks standard OIDC.

OSS/community members can still add auth provider support in two ways:

1. Runtime adapters in `mozaiksai/core/auth/adapters/` when the provider needs
   backend token validation behavior beyond generic OIDC/JWT.
2. Build-context or capability-pack setup guidance when an operator wants a
   repeatable provider setup path, such as a Keycloak realm template, an Entra
   External ID checklist, or an Auth0 application template.

Those extensions must preserve the canonical app contract. They may add
provider setup documentation, env-handle declarations, or optional app-owned
`services/adapters/auth/**` code only when the generated app truly owns custom
auth mechanics. They must not place provider admin credentials, social-provider
secrets, tenant IDs, or hardcoded provider URLs in generated app source.

Keycloak is a good example: a working Keycloak deployment requires a realm URL,
client registration, redirect URI, web origins, optional social identity
providers, and sometimes admin credentials. The generic factory should not
invent those values. It should generate the OIDC contract and readiness checks;
an operator, hosted product, or selected adapter/setup pack supplies the actual
Keycloak URL and registered client values through environment and secret stores.

### `app/config/ai.json`

Owns app-level AI boot defaults such as:

- `chat.chat_startup_mode`
- `workflows.entry_point`

These settings affect how the app boots into chat/workflow mode.
They do not define authentication.

### Environment Variables

Own deployment-time backend configuration, such as:

- `MOZAIKS_OIDC_AUTHORITY` — required; base URL of the OIDC identity provider
- `MOZAIKS_OIDC_TENANT_ID` — optional; appended to authority for discovery URL
- `MOZAIKS_OIDC_DISCOVERY_URL` — optional; explicit `.well-known` URL (overrides authority/tenant)
- `AUTH_AUDIENCE` — expected audience claim for token validation
- `AUTH_REQUIRED_SCOPE` — required scope for user-authenticated endpoints
- `AUTH_ROLES_CLAIM` — JWT claim name for roles (default: `roles`)
- `VITE_OIDC_AUTHORITY` — frontend: OIDC authority for the browser auth flow
- `VITE_MOCK_MODE` — frontend: skip auth for local development

### Login Theme Assets

Provider/broker login-theme assets are optional and provider-specific. Keycloak,
Entra External ID, Auth0, Okta, and similar systems all handle these assets
differently. Generated apps may style their in-app `/login` route through
`app/brand/theme_config.json`, but that file does not own auth behavior,
provider selection, callback mechanics, token storage, or secret handles.

## Current Repo Note

The canonical target for generated/customer apps is a self-contained app
workspace with `app/app.json`, `app/config/ai.json`, and `app/brand/*`. In
this repo, the first-party Studio bundle follows that same contract through
`factory_app/app/app.json`, `factory_app/app/config/ai.json`, and
`factory_app/app/brand/*`.

## Runtime Flow

```text
app/app.json
    -> coarse authRequired signal
app/config/auth.yaml
    -> provider-neutral auth behavior and env-handle contract
    -> host app boot config
    -> web shell creates OIDC PKCE auth adapter
    -> user logs in through the selected OIDC provider
    -> browser adapter obtains tokens
    -> frontend passes Bearer token to backend
    -> backend validates JWT via OIDC discovery + JWKS
```

  Separately:

  ```text
  app/config/ai.json
    -> app-level chat startup mode
    -> app-level default workflow selection
    -> frontend boot selection only
  ```

## Frontend

The web shell receives an app-owned `createAuthAdapter()` export. Generated web
apps use the OIDC PKCE adapter scaffolded from
`factory_app/build_context/webapp_builder/templates/ui/auth/authAdapter.js`.

Key points:

- config is declared in `app/config/auth.yaml` and environment variables
- the adapter uses Authorization Code + PKCE with OIDC discovery
- login transactions are keyed by OIDC `state`
- startup user checks must not clear pending PKCE transaction state
- Keycloak, hosted identity, Google, Microsoft, GitHub, or other social login
  choices are upstream provider setup, not generated app OAuth code

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

The backend should be configured to validate tokens from the same OIDC
issuer/audience that the frontend uses for login.

## Current Configuration Rule

Use this order of operations:

1. declare coarse app auth requirement in `app/app.json`
2. declare provider-neutral auth behavior in `app/config/auth.yaml`
3. declare app-level chat/workflow boot defaults in `app/config/ai.json`
4. use environment variables for deployment-specific provider values
5. keep provider-specific broker setup outside generated app source unless the
   app explicitly owns a custom auth integration under `app/services/adapters/auth/`

Do not introduce:

- `auth.json`
- raw provider URLs or secrets in `app/config/auth.yaml`
- direct Google OAuth implementation code in generated apps
- split frontend/backend auth behavior files that contradict `auth.yaml`

Do not move `entry_point` or `chat.chat_startup_mode` into `app/app.json`.

## Minimal Example

```json
{
  "authRequired": true,
  "admins": ["owner@example.com"]
}
```

Provider-neutral auth behavior example belongs in `app/config/auth.yaml`:

```yaml
schema_version: mozaiks.auth.v1
auth_required: true
strategy: oidc
mode: public_self_signup
signup_enabled: true
routes:
  login: /login
  callback: /auth/callback
  logout: /login
  post_login_default: /dashboard
frontend:
  adapter: oidc_pkce
  client_id_env: VITE_OIDC_CLIENT_ID
  authority_env: VITE_OIDC_AUTHORITY
  discovery_url_env: VITE_OIDC_DISCOVERY_URL
  redirect_uri_env: VITE_OIDC_REDIRECT_URI
  scope_env: VITE_OIDC_SCOPE
  default_scopes: [openid, profile, email]
runtime:
  provider_env: AUTH_PROVIDER
  enabled_env: AUTH_ENABLED
  authority_env: MOZAIKS_OIDC_AUTHORITY
  discovery_url_env: MOZAIKS_OIDC_DISCOVERY_URL
  issuer_env: AUTH_ISSUER
  jwks_url_env: AUTH_JWKS_URL
identity_providers:
  - id: google
    label: Google
    provider_role: upstream_oidc_provider
  - id: microsoft
    label: Microsoft
    provider_role: upstream_oidc_provider
login_methods:
  - id: sign-in
    kind: oidc_redirect
    label: Sign in
    primary: true
  - id: create-account
    kind: create_account
    label: Create account
    primary: false
customization:
  login_theme_source: brand/theme_config.json
  upstream_provider_setup: host_or_operator
```

Separate app-level boot example:

```json
{
  "chat": {
    "chat_startup_mode": "ask"
  },
  "workflows": {
    "entry_point": "AppGenerator"
  }
}
```

That example belongs in `app/config/ai.json`, not in `app/app.json`.

For mobile:

```json
{
  "mobile": {
    "auth": {
      "provider": "token",
      "redirectScheme": "myapp",
      "redirectPath": "oauthredirect",
      "scopes": ["openid", "profile", "email"]
    }
  }
}
```

For web, keep the target declaration minimal:

```json
{
  "targets": {
    "web": true
  }
}
```

For desktop, do not over-specify config until a real desktop client exists.
Today this is usually enough:

```json
{
  "targets": {
    "desktop": false
  }
}
```

That field currently expresses intent, not a full desktop runtime contract.

For the broader manifest model, read
[App Manifest And Platform Targets](app/app-manifest-and-platform-targets.md).

## Verification

After auth changes:

1. verify `app/app.json` parses
2. verify `app/config/auth.yaml` has schema_version `mozaiks.auth.v1`
3. verify `app/config/ai.json` still contains only app-level AI boot settings
4. verify the web shell can initialize the OIDC PKCE adapter
5. verify login redirects to the intended OIDC provider
6. verify `/auth/callback` completes token exchange and returns to an app-local route
7. verify backend-protected routes accept the resulting token
