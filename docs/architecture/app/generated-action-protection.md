# Generated Action Protection

This document states what protects a generated module action at runtime, and
the rule the Factory `SecurityReadiness` workflow uses to report an action that
nothing protects.

The scanner reads declared contracts only. It never invents a permission grant,
and it does not infer intent from action or module names. A finding means the
bundle's own contracts leave a caller able to do more than the app declares.

## What Protects An Action

Five declared contracts decide who can run a module action:

| Contract | Where | Runtime effect |
|---|---|---|
| Action surface | `actions[].api_surface` in `module.yaml` | `public` and `public_readonly` admit anonymous callers. An absent or null surface is authenticated HTTP. `internal` is unreachable over HTTP. `admin_internal` is reachable only on the operator route, by a token-validated principal with an operator role. |
| Sign-in | `app.json` `authRequired: true` with a valid `config/auth.yaml` | The app loads as a signed-in app. Authenticated-surface calls without a validated token are rejected with 401. |
| Declared permission | `actions[].permissions`, resolving to `module.yaml` `permissions[]` | The executor rejects a caller whose granted permissions lack one, with 403. |
| Collection ownership | `tenancy` and `owner_field` per collection in `data/contract.json` | `per_user` and `per_workspace` collections are scoped by persistence to the caller's own user or workspace, whatever the module code queries. `app_wide` collections are shared by every caller of the app. |
| Entitlement gate | `actions[].entitlement_gate`, with plans in `config/subscriptions.yaml` | The executor rejects a caller whose plan lacks the capability, with 402. A caller with no assignment holds the default plan. Without a subscriptions contract no entitlement adapter is wired and every gate passes. |

An action's reach is its module's declared collections in `data/contract.json`.
Generated repositories address their own module's collections, and with a data
contract present persistence refuses an undeclared collection.

Without authentication, which the runtime permits only in local development,
test, or an unconfigured environment, the caller's identity is not verified and
module dispatch skips permission and entitlement checks. Ownership cannot
separate callers whose identity is not verified. A configured deployment always
requires a token for a non-public action, but an app that declares no sign-in
declares nothing about who those callers are.

## Decision Table

Each row is an action with the declared contracts on the left. "Another user's
records" means records another signed-in user created.

| # | Declared contracts | Anonymous caller | Signed in, without the plan | Another user's records | SecurityReadiness |
|---|---|---|---|---|---|
| 1 | `public` or `public_readonly` surface | Reaches it | Reaches it | Bounded by collection ownership | Not reported: public by declaration |
| 2 | `internal` surface, `permissions: []` | Not reachable over HTTP (404) | Not reachable over HTTP (404) | Event reactions and trusted runtime calls only | Not reported |
| 3 | `admin_internal` surface, no permission | 401 | 403 without an operator role | Any operator can run it | High |
| 4 | Authenticated surface, app declares no sign-in | Reaches it, unverified | Same as anonymous | Not separated | High |
| 5 | Sign-in, declared permission | 401 | 403 without the permission | Bounded by collection ownership | Not reported |
| 6 | Sign-in, no permission, every module collection `per_user` or `per_workspace` | 401 | Own records only; 402 when gated | Not reachable (404, empty list) | Not reported |
| 7 | Sign-in, no permission, a module collection is `app_wide` or unowned, gate the default plan does not grant | 401 | 402 | Plan holders reach shared records, as the plan declares | Not reported |
| 8 | Sign-in, no permission, a module collection is `app_wide` or unowned, no restricting gate | 401 | Reads and changes shared records | Reads and changes them | High |
| 9 | Sign-in, no permission, module declares no collections, no restricting gate | 401 | Can call it | Not bounded by any declared contract | Medium |

A restricting gate is an `entitlement_gate` whose capability a valid
`config/subscriptions.yaml` does not grant through its default plan (the
`default_plan_id` of a v1 contract, or any product's default plan in v2). A
gate the default plan grants admits every signed-in user, so it counts as no
gate. A module with persistence code and no valid `data/contract.json` has no
declared ownership and is treated as row 8.

Severity follows what the caller can do. High means a caller outside the
declared audience, or any signed-in user, can act on records that are not
theirs. Medium means sign-in bounds the caller, but no declared contract bounds
what the action reaches.

## The Scanner Rule

`module_permissions:missing:{module_id}:{action_id}` is reported for an action
with no permissions, unless one of these holds:

- its surface is `public` or `public_readonly`;
- its surface is exactly `internal` with an explicit `permissions: []`;
- its surface is absent or null, the app declares sign-in, and either every
  collection its module declares is `per_user` or `per_workspace`, or its
  `entitlement_gate` is a restricting gate.

Every other surface, including `admin_internal` and malformed values, still
needs a declared permission. `module_permissions:undeclared` separately reports
permission ids that `module.yaml` does not declare. Findings remain advisory,
as `factory_app/build_context/SecurityReadiness/baseline_security_controls.yaml`
states, and that file's `module_permissions_declared` check carries this rule.

## Evidence

`tests/test_security_readiness_action_protection.py` changes one declared
contract of the recorded `fdfa818e` bundle per case. It dispatches rows 2, 4,
5, 6, 7 and 8 through the module router and executor against a real MongoDB
with two users, and asserts the scanner's verdict on the same bundle. Row 3 is
covered by `tests/test_admin_module_dispatch.py`. Row 9 is asserted by the
scanner tests only, because no declared contract describes what such an action
reaches.

## Related Docs

- [Tenant Auth And Scope](tenant-auth-and-scope.md)
- [Module System](../modules-systems/module-system.md)
- [Data Contract And Revision Contract](../builder/data-contract-and-revision-contract.md)
