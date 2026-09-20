# Factory security

Factory uses the same app security contracts as generated apps and hosted app
workspaces. Its declarations live under `factory_app/app/`:

| File | Purpose |
| --- | --- |
| `app.json` | Declares authentication intent through `authRequired`. |
| `config/auth.yaml` | Declares OIDC browser behavior, local routes, and public environment handles. |
| `security/secrets.yaml` | Declares runtime secret names and environment-backed provider policy. |
| `ui/route_manifest.json` | Registers the shared sign-in and callback pages. |

The OSS host enforces authentication and scope. App files configure those
mechanisms; they do not implement a separate security service.

## Local development

Use the explicit local settings from the repository's `.env.example`:

```dotenv
ENV=development
ENVIRONMENT=development
AUTH_ENABLED=false
AUTH_PROVIDER=
AUTH_ANON_ROLES=admin,user
```

The shared shell first loads `/api/shell-config`. Only a backend response
confirming explicitly disabled authentication in a permitted local environment
enables the development identity. The backend supplies that identity and its
configured roles. A failed bootstrap, missing OIDC authority, or frontend mock
flag does not enable it.

Factory keeps `authRequired: true` in its authored manifest. Local development
is an explicit operator mode resolved by the shared runtime, rather than a
different app bundle. Staging and production reject unauthenticated startup.

## Authenticated Studio

Use an existing OIDC provider and configure the backend for its issuer and
audience. For discovery-based JWT validation:

```dotenv
AUTH_ENABLED=true
AUTH_PROVIDER=jwt
MOZAIKS_OIDC_AUTHORITY=https://identity.example/realm
AUTH_AUDIENCE=your-runtime-audience
VITE_OIDC_AUTHORITY=https://identity.example/realm
VITE_OIDC_CLIENT_ID=your-public-browser-client
```

These are illustrative public identity settings, not provisioned services.
Register the browser callback URL with the provider: the shell's origin followed
by `/auth/callback`. The public browser client uses authorization code flow with
PKCE; a browser client secret is never required or exposed. Operator settings
also determine which user roles may access Studio management routes.

`config/auth.yaml` names the supported frontend and backend environment handles.
The shell uses the backend's effective authentication mode. Public frontend
settings may be supplied at runtime through the named handles or compiled into
the SPA through those same `VITE_*` handles. Missing required browser settings
produce a configuration error. An explicit discovery URL can be used through
the corresponding declared discovery handle.

The shared browser adapter owns discovery, PKCE transactions, callback handling,
and browser session storage. Generated `ui/auth/authAdapter.js` files delegate to
`@mozaiks/chat-ui/auth`; they do not carry another implementation. Apps can still
register their own sign-in page or explicit auth integration through the existing
app UI extension contract. Backend token verification and permissions remain
authoritative for every request.

The browser checks identity claims from the configured HTTPS token endpoint,
following the direct-response validation allowed by
[OpenID Connect Core](https://openid.net/specs/openid-connect-core-1_0.html#IDTokenValidation).
It does not implement browser JWKS signature verification. The backend verifies
access tokens independently. Browser sessions expire and require sign-in again;
this adapter does not implement token refresh.

Public apps declare `authRequired: false` and omit the optional auth contract.
Their public routes render with no signed-in identity or bearer token, even when
the backend protects its APIs. CLI presets that enable authentication emit the
same validated contract and public sign-in routes as generated apps.

## Runtime secrets

Factory's checked-in manifest uses `provider.type: env`. It lists MongoDB and
the model-key handles consumed by the runtime, including optional model-provider
keys and explicit primary/fallback key settings. Set actual values in the
process environment or an ignored local `.env`; never put them in the manifest.

Startup validates the selected secret manifest. MongoDB and model configuration
use the shared resolver and canonical environment names. An explicit environment
provider ignores ambient vault references. Invalid configured policy is an error
even when optional startup checks use warning mode.

The `required` field documents an entry's requirement. It does not require every
listed provider key at startup. The selected consumer determines which secret is
needed; choosing one model provider does not require credentials for another.
This is not automatic environment hydration for arbitrary third-party SDKs.

An operator using a supported vault can provide a names-only policy through
`MOZAIKS_SECRETS_CONFIG_PATH`. Keep it outside the generated app when it contains
operator-specific vault configuration. The same shared resolver consumes it;
Factory does not ship a hosted-product vault name or provision infrastructure.

Connector credential storage has its own existing encrypted backend contract.
Listing model/runtime secret names does not configure connector encryption,
payment approvals, production deployments, or data-retention policy.

## Validation and migration

AppLoader validates `config/auth.yaml` against the shared typed
`mozaiks.auth.v1` contract and checks agreement with `app.json`. Generated-app
validation consumes that same model. Invalid or missing required declarations
fail before the app is served.

Final bundle composition adds the shared login and callback entries to the normal
route manifest after app-schema and auth-scaffold output are combined. Authored
custom pages can occupy those routes. Validation requires each route to resolve
once and permit unauthenticated access; it rejects missing or protected bindings.

Pre-1.0 migration replaces the implicit shell demo identity and generated copies
of the OIDC adapter. Regenerate an old auth scaffold to use the shared facade.
Internal runtime secret lookups use canonical environment handles, replacing
retired `OpenAIApiKey` and `MongoURI` lookup names. Vault users must name their
existing stored secrets explicitly in the selected manifest.

See [app bundle declaratives](../architecture/app/app-bundle-declaratives.md)
and [stability and compatibility](stability-and-compatibility.md).
