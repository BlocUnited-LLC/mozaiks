# Tenant Auth And Scope

This document defines the modular target for tenant-aware auth in the OSS
runtime and platform host.

The key separation is simple:

- OSS authenticates a request and carries provider-neutral scope through runtime
  dispatch.
- App or hosted-product code decides what a tenant, workspace, membership, plan,
  or managed provider means.

`mozaiks-app` uses this split for the hosted product: it owns
`tenant_identity`, workspace memberships, MozaiksPay access, hosted provider
defaults, and the branded org/workspace home. The OSS repo should not copy that
product logic.

## Terms

- User: the authenticated person or service principal.
- Tenant: the account or organization boundary for product data.
- Workspace: the working area under a tenant. Hosted products can use it for
  teams, billing assignment, app ownership, and the Studio/workspace shell.
- App: the Mozaiks app bundle or generated customer app being hosted.
- Principal: provider-neutral identity facts decoded from auth.
- Identity scope: the resolved `{app_id, tenant_id, workspace_id, user_id}`
  used by module dispatch, persistence, event metadata, and entitlements.

## Ownership

OSS owns:

- auth adapter registry and token validation
- provider-neutral claim mapping
- `UserPrincipal` and runtime identity scope contracts
- platform module dispatch scope resolution
- `ModuleContext` and `ModuleRequest` scope fields
- `EntitlementPort` inputs and `ConfiguredEntitlementAdapter`
- extension hooks that let apps map a principal to app-local roles or
  memberships

Hosted products and app workspaces own:

- tenant records
- workspace records
- membership records
- product roles and permission grants
- hosted provider defaults such as Mozaiks-hosted auth, hosted AI, MozaiksPay,
  or managed hosting
- whether a workspace can use proprietary managed capabilities
- workspace-level branding and shell composition

Tenant/workspace records are not person profiles. Person profile state belongs
to `/me`; tenant/workspace state belongs to Studio / Workspace Shell.

## Current Contract

The OSS runtime now provides the foundational tenant/auth scope contract:

- `UserClaims`, `UserPrincipal`, websocket users, `ModuleRequest`, and
  `ModuleContext` carry `workspace_id`.
- Generic JWT auth can map configurable `app_id`, `chat_id`, `tenant_id`, and
  `workspace_id` claim names through `AUTH_*_CLAIM` env vars.
- Generic JWT auth can resolve JWKS URL and issuer through OIDC discovery using
  `MOZAIKS_OIDC_AUTHORITY`, `MOZAIKS_OIDC_TENANT_ID`, or
  `MOZAIKS_OIDC_DISCOVERY_URL`. `AUTH_JWKS_URL` and `AUTH_ISSUER` remain
  explicit overrides.
- Keycloak auth can map configurable app, tenant, and workspace claim names
  through `KEYCLOAK_*_CLAIM` env vars.
- When auth is enabled, external HTTP module dispatch requires an authenticated
  principal by default. The only anonymous HTTP module actions are those whose
  `actions[].api_surface` is explicitly `public` or `public_readonly`.
- External HTTP module dispatch always constructs an enforce-mode
  `ModuleDispatchAuthority` carrying the caller's concrete permission set —
  an empty set for anonymous public actions. There is no trusted bypass on
  the HTTP path.
- Authenticated HTTP module dispatch rejects request-supplied `user_id`,
  `tenant_id`, or `workspace_id` values that conflict with token-bound claims.
- `PlatformHookRegistry` exposes `module_scope_resolver`, a provider-neutral
  hook that lets apps return validated `{app_id, user_id, tenant_id,
  workspace_id, permissions}` for module dispatch.
- Module persistence context and emitted module event tenant metadata include
  `workspace_id` when present.
- Entitlement checks receive `workspace_id` and the OSS
  `ConfiguredEntitlementAdapter` honors `workspace_id_field` when an app's
  `config/subscriptions.yaml` declares workspace-scoped assignment records.
  The adapter checks the most specific app/tenant/workspace/user assignment
  before falling back to broader tenant, workspace, user, or app-level records.
- Runtime usage events carry `workspace_id` when the workflow context provides
  it. `TokenUsageGuard` uses the same app/user/tenant/workspace scope to
  resolve the active plan before an LLM call. Token wallet balances remain
  scoped by the declared wallet scope: user, tenant, or app.

## Target Contract

The platform should resolve external requests in this order:

1. Authenticate the token into a `UserPrincipal`, unless the action is
   explicitly declared as `api_surface: public` or `public_readonly`.
2. Build a requested scope from route/query/body context without trusting it.
3. Ask the app/platform extension hook to validate or complete the scope.
4. Return a canonical identity scope plus granted module permissions.
5. Dispatch the module action with that scope in `ModuleRequest`.

For external requests, missing auth or missing permission resolution results
in an empty permission set on an enforce-mode authority or an auth error —
never a trusted bypass.

Internal runtime calls may use trusted dispatch only by constructing one of
the closed server-owned `ModuleDispatchAuthority` kinds
(`framework_internal`, `operator_internal`, contract-declared
`event_reaction`, or auth-disabled `local_development`). That path stays
explicit and separate from HTTP user traffic; internal location alone never
grants bypass.

## Remaining Production Work

1. Update generator contracts.
   If generated modules rely on tenant/workspace scope, AppGenerator file
   contracts and module docs should explicitly describe `ctx.workspace_id`,
   request-scope validation, and the external-versus-internal dispatch
   distinction.

2. Add an end-to-end hosted smoke.
   A production readiness gate should exercise token auth, scope hook
   resolution, module permission enforcement, tenant/workspace persistence
   scope, and event metadata in one hosted-product flow.

## `mozaiks-app` Integration

`mozaiks-app` should continue to own:

- `tenant_identity` tenant/workspace/membership/provider-profile records
- the membership-to-permissions resolver
- MozaiksPay client and connector provisioning
- hosted AI/model access policy
- product plan assignment records in `hosted_billing.subscriptions`

Hosted launch should be preset for tenant workspaces: Mozaiks-hosted auth,
hosted AI, MozaiksPay, and managed hosting may be available as selected managed
providers so builders can create apps without provider setup. The hosted product may also choose which
provider areas can carry bring-your-own metadata. For example, `mozaiks-app`
hosted v1 presets Mozaiks-hosted auth and MozaiksPay, allows secret-free
external auth/payment metadata, and keeps hosted AI plus managed hosting
platform-owned. That is hosted-product policy, not an OSS runtime rule.

The OSS contract remains modular: auth adapters, connector storage, module
permissions, entitlement checks, and generated app facades stay provider-neutral
so self-managed users and future hosted product tiers can support bring-your-own
providers without copying `mozaiks-app` business logic into the framework.
Hosted AI/model access, payments, auth, and hosting should become configurable
only through explicit product or app-workspace features with UI, validation,
secret policy, tests, and rollout docs.

## Related Docs

- [Authentication Setup](../verified/auth-setup.md)
- [Module System](../modules-systems/module-system.md)
- [App Bundle Declaratives](app-bundle-declaratives.md)
- [Core, Product, and App Bundle Boundary](../foundations/core-product-app-bundle-boundary.md)
