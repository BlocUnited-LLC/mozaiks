# Authentication Setup

Mozaiks uses a pluggable auth adapter system that works with any auth provider.

## Quick Start

### Demo Mode (No Auth)

For local development or demos, disable auth entirely:

```bash
AUTH_ENABLED=false
```

All requests become anonymous users. No tokens required.

---

## Provider Setup

### Supabase

```bash
SUPABASE_URL=https://xyzcompany.supabase.co
```

That's it. The adapter auto-detects from `SUPABASE_URL` and constructs the JWKS endpoint automatically.

**Optional:** For local development with Supabase, add the JWT secret:
```bash
SUPABASE_JWT_SECRET=your-jwt-secret
```

---

### Keycloak

```bash
KEYCLOAK_URL=https://keycloak.example.com
KEYCLOAK_REALM=myrealm
KEYCLOAK_CLIENT_ID=my-app  # optional, for audience validation
```

---

### Auth0

```bash
AUTH_PROVIDER=jwt
AUTH_JWKS_URL=https://your-tenant.auth0.com/.well-known/jwks.json
AUTH_ISSUER=https://your-tenant.auth0.com/
AUTH_AUDIENCE=your-api-identifier
AUTH_SCOPES_FORMAT=array
```

---

### Generic OIDC (Okta, Azure AD, etc.)

```bash
AUTH_PROVIDER=jwt
AUTH_JWKS_URL=https://your-provider/.well-known/jwks.json
AUTH_ISSUER=https://your-provider/
AUTH_AUDIENCE=your-api
```

---

## Claim Mappings

Different providers put user data in different JWT claims. Configure as needed:

| Variable | Default | Description |
|----------|---------|-------------|
| `AUTH_USER_ID_CLAIM` | `sub` | Claim containing user ID |
| `AUTH_EMAIL_CLAIM` | `email` | Claim containing email |
| `AUTH_NAME_CLAIM` | `name` | Claim containing display name |
| `AUTH_ROLES_CLAIM` | `roles` | Claim containing user roles |
| `AUTH_SCOPES_CLAIM` | `scp` | Claim containing scopes |
| `AUTH_SCOPES_FORMAT` | `space` | `space` (Azure) or `array` (Auth0) |

---

## Auto-Detection

If `AUTH_PROVIDER` is not set, the system auto-detects based on environment variables:

| If this is set... | Provider used |
|-------------------|---------------|
| `AUTH_ENABLED=false` | `none` |
| `SUPABASE_URL` | `supabase` |
| `KEYCLOAK_URL` + `KEYCLOAK_REALM` | `keycloak` |
| `AUTH_JWKS_URL` + `AUTH_ISSUER` | `jwt` |
| Nothing | `none` (demo mode) |

---

## Custom Adapter

Register your own auth provider:

```python
from mozaiksai.core.auth.adapters import register_adapter, UserClaims, BaseAuthAdapter

class MyAuthAdapter(BaseAuthAdapter):
    name = "my-provider"

    async def validate_token(self, token: str) -> UserClaims:
        # Your validation logic
        decoded = my_validate(token)
        return UserClaims(
            user_id=decoded["sub"],
            email=decoded.get("email"),
            roles=decoded.get("roles", []),
            scopes=[],
            raw_claims=decoded,
            provider=self.name,
        )

    def is_enabled(self) -> bool:
        return bool(os.getenv("MY_AUTH_SECRET"))

# Register before app startup
register_adapter("my-provider", MyAuthAdapter)
```

Then set:
```bash
AUTH_PROVIDER=my-provider
MY_AUTH_SECRET=...
```

---

## WebSocket Auth

WebSocket connections extract tokens from query params by default:

```
ws://localhost:8000/ai/ws/workflow?access_token=eyJ...
```

To disable (in production behind reverse proxy):
```bash
MOZAIKS_WS_ALLOW_QUERY_TOKEN=false
```

---