# Keycloak Authentication Architecture

> Mozaiks ships **Keycloak** as its native identity provider.  
> Every app built on the stack gets OIDC authentication out of the box.

---

## Overview

```
┌─────────────────────────────────────────────────────────┐
│                    Mozaiks App                           │
│                                                         │
│  ┌──────────┐   /auth.json    ┌──────────────────────┐  │
│  │ Frontend  │◄──────────────►│  brand/public/        │  │
│  │ (Vite)    │                │    auth.json          │  │
│  │           │                │    assets/logo.svg    │  │
│  └─────┬─────┘                └──────────────────────┘  │
│        │ OIDC                         ▲                  │
│        │ code+PKCE                    │ read at startup  │
│        ▼                              │                  │
│  ┌──────────┐   JWKS/discovery  ┌─────┴──────────────┐  │
│  │ Keycloak │◄─────────────────►│ Python Backend     │  │
│  │ :8080    │   validate JWT    │ (mozaiksai.core.   │  │
│  │          │                   │  auth)              │  │
│  └──────────┘                   └────────────────────┘  │
└─────────────────────────────────────────────────────────┘
```

**Frontend** uses `auth.json` to initialize the OIDC client. It redirects users to Keycloak for login and receives a JWT access token via Authorization Code + PKCE flow.

**Backend** reads `auth.json` at startup to derive the OIDC discovery URL, audience, and claim mappings. It validates every request's JWT against Keycloak's JWKS endpoint.

**Keycloak** is the single source of truth for identity: user accounts, sessions, roles, MFA, and social login federation.

---

## Configuration Layers

```
┌──────────────────────────────┐  Priority: HIGHEST
│  Environment Variables       │  (deployment overrides)
├──────────────────────────────┤
│  auth.json                   │  (app-level defaults)
│  brand/public/auth.json      │
├──────────────────────────────┤
│  Built-in Defaults           │  Priority: LOWEST
│  (Keycloak @ localhost:8080) │
└──────────────────────────────┘
```

| Layer | Purpose | Example |
|---|---|---|
| **Built-in defaults** | Zero-config local dev | `http://localhost:8080/realms/mozaiks` |
| **auth.json** | App branding, realm name, client ID, features | Committed to repo |
| **Environment variables** | Production overrides, secrets | `MOZAIKS_OIDC_AUTHORITY=https://auth.myapp.com/realms/prod` |

---

## Module Map

```
mozaiksai/core/auth/                     # Backend auth modules
├── __init__.py                          # Public API (re-exports everything)
├── config.py                            # AuthConfig dataclass + layered resolution
├── auth_config_loader.py                # Reads auth.json, derives OIDC values
├── discovery.py                         # OIDC discovery document fetching + caching
├── jwks.py                              # JWKS client for signature verification
├── jwt_validator.py                     # JWT validation (signature, issuer, audience, expiry)
├── dependencies.py                      # FastAPI Depends() for route protection
└── websocket_auth.py                    # WebSocket connection authentication

chat-ui/src/adapters/                    # Frontend auth modules
├── auth.js                              # AuthAdapter base + Token/External adapters
├── keycloakAuth.js                      # KeycloakAuthAdapter (keycloak-js wrapper)
└── api.js                               # Auto-injects Bearer tokens via window.mozaiksAuth

app/                                     # Template app
├── App.jsx                              # Initializes Keycloak adapter, passes to MozaiksApp
├── brand/public/auth.json               # Per-app Keycloak config
└── brand/public/_system/silent-check-sso.html  # Silent SSO redirect target
```

### What each module does

| Module | Responsibility |
|---|---|
| **Backend** | |
| `config.py` | Loads `AuthConfig` from env vars + auth.json. Cached after first call. |
| `auth_config_loader.py` | Parses `auth.json` from `brand/public/`, derives OIDC values, extracts Keycloak branding and realm import config. |
| `discovery.py` | Fetches `/.well-known/openid-configuration` from Keycloak. Caches for 24h. |
| `jwks.py` | Fetches JSON Web Key Set for JWT signature verification. Caches for 1h. |
| `jwt_validator.py` | Validates access tokens: signature (RS256), issuer, audience, expiry, scopes. |
| `dependencies.py` | FastAPI `Depends()` functions: `require_user`, `require_role("admin")`, `optional_user`, etc. |
| `websocket_auth.py` | Authenticates WebSocket connections using query param token. |
| **Frontend** | |
| `keycloakAuth.js` | `KeycloakAuthAdapter` — wraps `keycloak-js`, fetches `/auth.json` for config, sets `window.mozaiksAuth.getAccessToken()`. |
| `auth.js` | `AuthAdapter` base class + `ExternalAuthAdapter` (embedded mode) + `TokenAuthAdapter` (localStorage). |
| `api.js` | `authFetch()` injects `Authorization: Bearer` headers. WebSocket appends `?access_token=`. |
| `App.jsx` | Template entry — calls `createKeycloakAuthAdapter()` and passes the adapter to `<MozaiksApp>`. Auth is always on; mock mode via `VITE_MOCK_MODE=true`. |

---

## JWT Flow

```
1. User visits app
2. Frontend reads /auth.json → gets Keycloak realm + client ID
3. Frontend redirects to Keycloak login → Authorization Code + PKCE
4. User authenticates (password, social, MFA)
5. Keycloak issues access_token (JWT) + refresh_token
6. Frontend sends requests with: Authorization: Bearer <access_token>
7. Backend validates JWT:
   a. Fetch JWKS from Keycloak (cached)
   b. Verify RS256 signature
   c. Check issuer matches Keycloak realm
   d. Check audience matches client ID
   e. Check expiry + clock skew
   f. Extract claims (sub, email, realm_access.roles)
8. Request proceeds with UserPrincipal attached
```

---

## Keycloak Realm Setup

`auth.json` can be used to bootstrap a Keycloak realm via the setup script:

```python
from mozaiksai.core.auth import get_keycloak_realm_config

# Generate Keycloak realm import JSON from auth.json
realm_config = get_keycloak_realm_config()
# → Use with Keycloak Admin REST API or import at startup
```

The generated config includes:

- Realm name and settings (registration, password reset, etc.)
- Public client with PKCE enabled
- Default roles (user, admin)
- Session lifespans
- Social identity providers (if configured)

---

## Login Page Branding

Keycloak login pages can be branded using assets from `brand/public/assets/`:

```python
from mozaiksai.core.auth import get_keycloak_branding

branding = get_keycloak_branding()
# {
#     "logo": "my_logo.svg",         → served at /assets/my_logo.svg
#     "favicon": "my_favicon.png",   → served at /assets/my_favicon.png
#     "backgroundImage": "login_bg.png",
#     "theme": "dark",
#     "loginTitle": "Sign In",
#     ...
# }
```

These values are used by:

1. **Keycloak theme templates** — inject logo/colors into the login page
2. **Frontend login component** — if using an embedded login form
3. **Realm setup scripts** — configure branding via Keycloak Admin API

---

## Route Protection Quick Reference

```python
from mozaiksai.core.auth import require_user, require_role, optional_user

# Any authenticated user
@app.get("/api/profile")
async def get_profile(user: UserPrincipal = Depends(require_user)):
    return {"user_id": user.user_id}

# Admin only
@app.get("/api/admin/stats")
async def admin_stats(user = Depends(require_role("admin"))):
    return {"stats": "..."}

# Optional auth (anonymous allowed)
@app.get("/api/public")
async def public_endpoint(user = Depends(optional_user)):
    if user:
        return {"greeting": f"Hello {user.email}"}
    return {"greeting": "Hello anonymous"}
```

### WebSocket Authentication

```python
from mozaiksai.core.auth import authenticate_websocket

@app.websocket("/ws/chat")
async def chat_ws(websocket: WebSocket):
    user = await authenticate_websocket(websocket)
    if not user:
        return  # Connection closed with 1008

    await websocket.accept()
    # user.user_id, user.roles, user.email available
```

---

## Infrastructure

Keycloak runs as part of the standard `docker compose` stack. No separate setup.

### What `docker compose up` starts

| Service | Image | Port | Purpose |
|---|---|---|---|
| `keycloak-db` | `postgres:16-alpine` | — (internal) | User/realm/session persistence |
| `keycloak` | `quay.io/keycloak/keycloak:26.0` | `8080` | Identity provider (OIDC) |
| `mongo` | `mongo:7` | `27017` | Chat/workflow persistence |
| `app` / `runtime` | `mozaiksai-app` | `8000` | FastAPI backend |

### Realm bootstrap

On first start, Keycloak auto-imports `infra/keycloak/realm-export.json`:

- **Realm:** `mozaiks`
- **Client:** `mozaiks-app` (public, PKCE, Authorization Code flow)
- **Roles:** `user` (default), `admin`
- **Dev user:** `dev` / `dev` (admin + user roles, for local dev)
- **Redirect URIs:** `localhost:5173`, `localhost:3000`, `localhost:8000`

After import, you can manage users at [http://localhost:8080/admin](http://localhost:8080/admin) (login: `admin` / `admin`).

### Data persistence

| Volume | Data |
|---|---|
| `mozaiksai_keycloak_db` | Postgres — Keycloak users, realms, sessions, credentials |
| `mozaiksai_mongo_data` | MongoDB — chat sessions, workflows, artifacts |

`docker compose down` preserves volumes. `docker compose down -v` deletes them (resets all users).

---

## Local Development

For local dev without Keycloak, set in `.env`:

```dotenv
AUTH_ENABLED=false
```

All routes accept unauthenticated requests and stamp `user_id="anonymous"`.

To develop **with** Keycloak locally (default when using docker compose):

```bash
# Start everything including Keycloak
docker compose up

# Keycloak admin console
open http://localhost:8080/admin    # admin / admin

# Dev user login
# username: dev   password: dev
```

---

## Production Deployment

In production, set strong passwords and your auth domain:

```dotenv
AUTH_ENABLED=true
KC_ADMIN_PASSWORD=<strong-password>
KC_DB_PASSWORD=<strong-password>
KC_HOSTNAME=auth.yourapp.com
MOZAIKS_OIDC_AUTHORITY=https://auth.yourapp.com/realms/yourapp
AUTH_AUDIENCE=yourapp-client
```

Everything else (JWKS URL, issuer, discovery) is auto-derived from the authority URL.
