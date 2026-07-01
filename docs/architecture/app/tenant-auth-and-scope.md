# Tenant Auth And Scope

This document defines the modular target for tenant-aware auth in the OSS
runtime and platform host.

The key separation is simple:

- OSS authenticates a request and carries provider-neutral scope through runtime
  dispatch.
- App or hosted-product code decides what a tenant, workspace, membership, plan,
  or managed provider means.

`mozaiks-app` uses this split for the hosted product: it owns
`tenant_identity`, workspace memberships, MozaiksPay access, and hosted provider
defaults. The OSS repo should not copy that product logic.

## Terms

- User: the authenticated person or service principal.
- Tenant: the account or organization boundary for product data.
- Workspace: the working area under a tenant. Hosted products can use it for
  teams, billing assignment, and app ownership.
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

## Current Gaps

The current codebase has the right pieces, but they are not one complete
contract yet.

- External module HTTP dispatch can reach the executor with
  `granted_permissions=None`, which the executor treats as trusted internal
  dispatch. External requests should never use the trusted-internal bypass.
- Request body or query context can override `user_id`, `app_id`, and
  `tenant_id` before app membership policy runs. Authenticated external
  requests should treat those values as requested scope, then validate them
  against the principal and membership policy.
- `workspace_id` exists in persistence helpers but is not first-class in
  `UserPrincipal`, `ModuleRequest`, `ModuleContext`, event tenant metadata, or
  `EntitlementPort`.
- The platform hook currently resolves permissions only. Hosted products also
  need a clean way to return the validated identity scope used for dispatch.
- OIDC discovery config exists, but the active generic JWT adapter path still
  primarily expects `AUTH_JWKS_URL` and `AUTH_ISSUER`.
- Tenant claim extraction is provider-specific and not fully configurable.

## Target Contract

The platform should resolve external requests in this order:

1. Authenticate the token into a `UserPrincipal`.
2. Build a requested scope from route/query/body context without trusting it.
3. Ask the app/platform extension hook to validate or complete the scope.
4. Return a canonical identity scope plus granted module permissions.
5. Dispatch the module action with that scope in `ModuleRequest`.

For external requests, missing auth or missing permission resolution should
result in an empty permission list or an auth error, not the internal
`granted_permissions=None` bypass.

Internal runtime calls may still use trusted dispatch when the caller is already
inside the runtime boundary. That path must stay explicit and separate from
HTTP user traffic.

## Recommended Implementation Sequence

1. Harden external module dispatch.
   Require authentication for protected module actions, preserve explicit public
   surfaces, and ensure external requests never pass `granted_permissions=None`.

2. Add `workspace_id` to the OSS identity scope.
   Extend `UserPrincipal`, `UserClaims`, `ModuleRequest`, `ModuleContext`, event
   tenant metadata, and `EntitlementPort` inputs.

3. Replace permission-only scope hooks with a scope-and-permissions hook.
   Keep the hook provider-neutral. `mozaiks-app` can implement it by reading
   `tenant_identity.memberships`; OSS should not know that schema.

4. Make auth claim mapping provider-neutral.
   Add configurable `app_id`, `tenant_id`, and `workspace_id` claim names for
   the generic JWT path. Keep provider adapters thin and predictable.

5. Wire OIDC discovery into the active JWT adapter path.
   `MOZAIKS_OIDC_AUTHORITY`, `MOZAIKS_OIDC_TENANT_ID`, and
   `MOZAIKS_OIDC_DISCOVERY_URL` should either configure the active JWT adapter
   or be removed from verified setup docs.

6. Extend entitlement checks with workspace scope.
   `ConfiguredEntitlementAdapter` should honor `workspace_id_field` when the
   app's `config/subscriptions.yaml` declares workspace-scoped assignments.

7. Update generator and docs contracts.
   If `ModuleContext` or module HTTP auth semantics change, update AppGenerator
   file contracts, module docs, and tests together.

## `mozaiks-app` Integration

`mozaiks-app` should continue to own:

- `tenant_identity` tenant/workspace/membership/provider-profile records
- the membership-to-permissions resolver
- MozaiksPay client and connector provisioning
- hosted AI/model access policy
- product plan assignment records in `hosted_billing.subscriptions`

Hosted launch should be preset for tenant workspaces: Mozaiks-hosted auth,
hosted AI, MozaiksPay, and managed hosting are available by default so builders
can create apps without provider setup. Auth and payments may expose
bring-your-own provider metadata as hosted product settings, while secrets stay
in the external provider or connector vault. Hosted AI/model access and managed
hosting remain platform-owned by default unless `mozaiks-app` later ships
explicit product features, UI, validation, secret policy, tests, and rollout
plans for those areas.

## Related Docs

- [Authentication Setup](../verified/auth-setup.md)
- [Module System](../modules-systems/module-system.md)
- [App Bundle Declaratives](app-bundle-declaratives.md)
- [Core, Product, and App Bundle Boundary](../foundations/core-product-app-bundle-boundary.md)
