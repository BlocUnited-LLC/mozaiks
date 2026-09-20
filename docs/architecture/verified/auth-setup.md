# Authentication Setup

Mozaiks uses a pluggable auth adapter system that works with any auth provider.

## Quick Start

### Demo Mode (No Auth)

For local development or demos, disable auth entirely:

```bash
AUTH_ENABLED=false
```

All requests become anonymous users. No tokens required.

For local user-to-user testing, such as DM notifications, keep auth disabled
but assign each browser profile a different dev persona. The no-auth dependency
accepts these request-scoped overrides only when `AUTH_ENABLED=false`:

- Header: `X-Mozaiks-Dev-User-Id: dev_alice`
- Cookie: `mozaiks_dev_user_id=dev_alice`
- Query param for one-off API calls: `?dev_user_id=dev_alice`

Optional comma-separated overrides are also supported:

- `X-Mozaiks-Dev-Roles`, `mozaiks_dev_roles`, or `dev_roles`
- `X-Mozaiks-Dev-Scopes`, `mozaiks_dev_scopes`, or `dev_scopes`

Browser workflow:

```js
document.cookie = "mozaiks_dev_user_id=dev_alice; path=/";
document.cookie = "mozaiks_dev_roles=admin,user; path=/";
```

Use another browser profile or incognito window with
`mozaiks_dev_user_id=dev_bob`. Sending a DM from `dev_alice` to `dev_bob`
can create a notification for Bob; sending to yourself will not, because the
messaging module excludes the sender from `recipient_ids`.

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
MOZAIKS_OIDC_AUTHORITY=https://your-tenant.auth0.com
AUTH_AUDIENCE=your-api-identifier
AUTH_SCOPES_FORMAT=array
```

Set `AUTH_JWKS_URL` and `AUTH_ISSUER` only when you want to override OIDC
discovery explicitly.

---

### Generic OIDC (Okta, Azure AD, etc.)

```bash
AUTH_PROVIDER=jwt
MOZAIKS_OIDC_AUTHORITY=https://your-provider
AUTH_AUDIENCE=your-api
```

For providers that require a tenant segment in the discovery URL, set:

```bash
MOZAIKS_OIDC_TENANT_ID=your-tenant-or-directory-id
```

You can also bypass authority composition with:

```bash
MOZAIKS_OIDC_DISCOVERY_URL=https://your-provider/.well-known/openid-configuration
```

Explicit overrides remain supported:

```bash
AUTH_JWKS_URL=https://your-provider/.well-known/jwks.json
AUTH_ISSUER=https://your-provider/
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
| `MOZAIKS_OIDC_AUTHORITY` or `MOZAIKS_OIDC_DISCOVERY_URL` | `jwt` |
| Nothing | `none` (demo mode) |

Resolution is canonical and fails closed. One parser
(`mozaiksai.core.auth.adapters.registry.resolve_auth_config`) interprets the
auth environment into an immutable resolved state; every predicate, the
adapter cache, and startup validation consume that same interpretation:

- `AUTH_ENABLED=true` with no detectable provider is a fatal configuration
  error in every environment (startup refuses to boot; provider resolution
  raises). The runtime never silently falls back to the trusted-bypass
  `none` adapter when auth was explicitly requested.
- Contradictory explicit declarations are fatal instead of silently picking
  one: `AUTH_ENABLED=true` + `AUTH_PROVIDER=none`, and `AUTH_ENABLED=false`
  + a real explicit `AUTH_PROVIDER`. (Passive provider signals such as a
  `SUPABASE_URL` left in a developer `.env` do not contradict an explicit
  disable — the explicit switch wins over passive presence.)
- An unknown `AUTH_PROVIDER` value, or a selected provider whose
  configuration cannot validate tokens (for example `AUTH_PROVIDER=jwt`
  without JWKS/issuer/discovery settings), is fatal.
- An unrecognized `AUTH_ENABLED` value (for example a typo like `tru`) is
  fatal instead of silently disabling auth.
- **Unauthenticated operation is allowlisted, not denylisted.** No-auth
  operation — explicit `AUTH_ENABLED=false`, explicit `AUTH_PROVIDER=none`,
  or implicit demo mode — is permitted **only** when the resolved environment
  is one of the recognized local names `development`, `local`, `test`
  (`dev` normalizes to `development`), or when no environment is configured
  at all. Every other explicit value rejects it, fatally, at startup and at
  provider resolution, independent of `MOZAIKS_STARTUP_CHECKS` mode. That
  includes known deployments (`production`, `staging`, and the `prod`/`stage`
  aliases) **and** unknown or regional names such as `prod-us`,
  `staging-eu`, `production-east`, `preview`, `qa`, or any custom value — an
  unrecognized environment never inherits development privilege. Unknown
  environments may still boot normally with authentication configured.
- Demo mode (`none` without explicit disablement) applies only when no auth
  configuration is present at all, in an environment that permits no-auth.
  Security-sensitive bypasses (such as the billing fulfillment ingress)
  additionally require *explicit* disablement — `AUTH_ENABLED=false` or
  `AUTH_PROVIDER=none` — and are not available in implicit demo mode.

### ENV / ENVIRONMENT resolution

`ENV` is the primary signal and `ENVIRONMENT` the fallback, but the two are
resolved canonically rather than by precedence alone:

- both values are trimmed first; empty or whitespace-only means *absent*, so a
  blank `ENV` never masks a non-blank `ENVIRONMENT`
- recognized aliases (`dev`/`prod`/`stage`) normalize before comparison
- if both are non-blank and resolve to **different** environments (for example
  `ENV=development` with `ENVIRONMENT=production`), that is a fatal
  configuration error — the runtime refuses to silently choose one
- if both resolve to the same environment, that is accepted
- both absent keeps the documented implicit local default

### Adapter cache coherence

The cached adapter is keyed by a fingerprint derived from the same immutable
configuration snapshot the adapter is constructed from, covering the
**complete** set of inputs for the resolved provider — not just provider
selection. Changing any meaning-bearing value (issuer, JWKS/discovery URL,
audience, any claim mapping, scope format, clock skew, algorithms, JWKS or
discovery cache TTL, Keycloak claim mappings, Supabase secret, anonymous
persona settings) rebuilds the adapter, so request-time validation always uses
the configuration startup validated.

An adapter is bound to its snapshot for its whole lifetime, including the
discovery and JWKS clients it creates lazily on first use. Those clients
receive the adapter's own URLs and cache TTLs and are constructed with
environment consultation disabled, so an adapter created under one
configuration can never begin validating tokens against another after the
environment changes. A configuration change produces a *new* adapter; the
previous one stays internally consistent. Explicit constructor input to
`OIDCDiscoveryClient` and `JWKSClient` is authoritative and is never
overridden by live environment or global `AuthConfig`.

`AUTH_JWKS_CACHE_TTL` (default `3600`) and `AUTH_DISCOVERY_CACHE_TTL`
(default `86400`) must be integer seconds, zero or greater, where `0` means
always refetch and is never treated as "unset". Absent, empty, and
whitespace-only values all normalize to the same canonical default, resolved
once by `mozaiksai.core.auth.cache_ttl` and reused by configuration
resolution, the adapter's own config, and `AuthConfig` — the layers can never
disagree. Malformed values fail startup rather than surfacing later during
lazy client construction on a request path. Cache expiry compares elapsed time
against the TTL rather than adding it to a timestamp, so any accepted value —
including a very large one — stays valid during real cache use.

### Custom adapter contracts

Custom adapters registered via `register_adapter` declare their own cache
identity:

```python
register_adapter("my-custom", MyCustomAdapter, config_identity=lambda: cfg.revision)
```

`config_identity` must be a **non-blank string**, or a callable returning one,
that changes whenever the adapter's configuration changes. Malformed
identities fail closed rather than being coerced: a non-string value, an empty
or whitespace-only string, or a callable that raises is rejected with an
`AuthError` (literal values are validated at registration; callable results at
each resolution). Without a `config_identity` the adapter is deliberately
never cached — it is rebuilt on every resolution — so a changed custom
configuration can never silently reuse an adapter validated under an older
one. Re-registering a provider also invalidates any cached adapter.

Adapter constructors receive the configuration snapshot as `settings=`. The
constructor contract is established **positively** at registration, before any
construction attempt, and registration fails rather than assuming an adapter
takes no configuration:

A mode is accepted only when the runtime's exact invocation —
`Adapter(settings=...)` or `Adapter()` — binds successfully against the
complete signature. Naming a usable `settings` parameter is necessary but not
sufficient: a constructor that also demands arguments the runtime cannot
supply is rejected when it registers, not at first use.

| Constructor | Outcome |
|---|---|
| `(settings)` / `(*, settings)` | snapshot supplied |
| `(settings=None, optional=None)` | snapshot supplied |
| `(**kwargs)` | snapshot supplied |
| `()` / all parameters optional / `(*args)` | constructed with no arguments |
| `(settings, required)` / `(settings, *, required)` | **rejected** — the call cannot bind |
| `(required, **kwargs)` | **rejected** — the call cannot bind |
| `(settings)` positional-only | **rejected** — make it keyword-accessible |
| a required parameter the runtime cannot supply | **rejected** |
| signature cannot be inspected | **rejected** unless `constructor_mode` is declared |

For constructors that genuinely cannot be inspected (C extensions, exotic
callables), declare the contract explicitly:

```python
register_adapter("my-custom", MyAdapter, config_identity="v1",
                 constructor_mode="settings_keyword")  # or "no_settings"
```

When the signature *is* inspectable the declaration is verified against it, so
explicit metadata cannot claim an invocation that provably would not work.

An exception raised inside a constructor body — including `TypeError` — is a
real construction failure and fails closed. The runtime never retries
construction without the snapshot.

Privileged HTTP surfaces can additionally require authenticated provenance:
`UserPrincipal.is_authenticated` is true only for principals produced by
validating a real bearer token against the configured adapter. Anonymous
demo principals and request-scoped dev personas may carry admin-looking
roles/scopes, but they are never authenticated provenance.

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

Browsers cannot set request headers on a WebSocket handshake, so the access token is
carried in the `Sec-WebSocket-Protocol` header — the one handshake header the browser
WebSocket API exposes. This is the normal production path and requires no opt-in.

Clients offer two subprotocol values, the marker followed by the base64url-encoded
(unpadded) token:

```js
new WebSocket(url, ['mozaiks.bearer.v1', base64url(accessToken)]);
```

The shared browser adapter does this for you:

```js
import { openAuthenticatedWebSocket } from '@mozaiks/chat-ui/adapters/websocketAuth.js';

const socket = openAuthenticatedWebSocket(wsUrl, accessToken);
```

The runtime decodes the token, validates it through the configured auth adapter — the
same adapter and the same validation as HTTP routes — binds the resulting
`WebSocketUser`, and selects only `mozaiks.bearer.v1` on accept. The credential is never
echoed back and never appears in the URL.

Missing or invalid credentials close the connection with code 1008 before accept.

### Query-param tokens (local development only)

A query-string token (`?access_token=...`) is rejected by default. Tokens in URLs land in
server access logs, browser history, `Referer` headers, and shared links. It remains
available as an explicit opt-in for local development and for non-browser clients that
cannot use the subprotocol path:

```bash
MOZAIKS_WS_ALLOW_QUERY_TOKEN=true   # never set this in production or staging
```

Leave it `false` (the default) everywhere else. Production browser clients do not need
it.

---
