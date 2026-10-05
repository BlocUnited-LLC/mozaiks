# Generated Action Protection

This document states what protects a generated module action at runtime, and
the rule the Factory `SecurityReadiness` workflow uses to report an action that
nothing protects.

The scanner reads declared contracts and module code only. It never invents a
permission grant, and it does not infer intent from action or module names. A
finding means the bundle's own contracts leave a caller able to do more than
the app declares, or the scanner cannot show that they do not.

## What Protects An Action

Five declared contracts decide who can run a module action:

| Contract | Where | Runtime effect |
|---|---|---|
| Action surface | `actions[].api_surface` in `module.yaml` | `public` and `public_readonly` admit anonymous callers on the module route; the surface name does not limit what the action does. An absent or null surface is authenticated HTTP. The module route does not serve `internal` or `admin_internal` actions. `admin_internal` is served on the operator route to a principal with an operator role. Platform surfaces that bind an `internal` or `admin_internal` action, such as a profile panel, tab or page, a relationship provider or ask context, run it for the signed-in user who renders them. |
| Sign-in | `app.json` `authRequired: true` with a valid `config/auth.yaml` | The app loads as a signed-in app. Whether a module call needs a token is the host's auth mode, not this declaration: a host with authentication enabled rejects an unauthenticated call to a non-public action with 401. |
| Declared permission | `actions[].permissions`, resolving to `module.yaml` `permissions[]` | The executor rejects a caller whose granted permissions lack one, with 403. |
| Collection ownership | `tenancy` and `owner_field` per collection in `data/contract.json` | A `per_user` or `per_workspace` collection with an `owner_field` is scoped by persistence to the caller's own user or workspace, whatever the module code queries through `ctx.persistence.collection`. Members of one workspace share its `per_workspace` records. `app_wide` collections are shared by every caller of the app, and a collection that declares no scoped ownership is not scoped at all. |
| Entitlement gate | `actions[].entitlement_gate`, with plans in `config/subscriptions.yaml` | The executor rejects the call with 402 unless entitlement resolution finds a plan that grants the capability to the caller's verified identity: the signed-in user, or the tenant or workspace the caller's credential is bound to. A caller with no assignment of their own holds the default plan. An app-level assignment grants its plan to every caller. Without a subscriptions contract no entitlement adapter is wired and every gate passes. A gate restricts callers by plan. It is not an ownership boundary, and the scanner never treats it as one. |

A host runs without authentication only in local development, test, or an
unconfigured environment. Without authentication the caller's identity is not
verified, module dispatch skips permission and entitlement checks, and
ownership cannot separate callers. The scanner reads the bundle, not the
deployment: rows 5 to 10 below assume a host that authenticates callers.

## What An Action Reaches

Runtime persistence does not bind an app module to its own collections, so an
action's reach is every collection its module can address:

- the collections `data/contract.json` declares under the module's surface;
- collections a `shared_collections` entry shares with the module in
  `shared_with` or `read_by`;
- every collection the module's code addresses:
  `ctx.persistence.collection(module_id, name)` for any module,
  `ctx.persistence.literal_collection(name)`, and
  `app_data_from_context(ctx).collection(alias)` resolved through the
  contract's aliases;
- the same, for code the module imports from another module or from elsewhere
  in the app, including by the package name the runtime gives each module's
  code.

With a data contract present, `collection()` refuses a module and name pair
that the contract does not declare, and a literal or alias name cannot address
a collection that is owned per user or per workspace. A literal or alias name
can address any other collection, declared in the contract or not. An app
without a data contract has no scoped collection, and an app whose contract
the runtime refuses does not load; the scanner treats both as unscoped reach.

The scanner reads every Python file under the module's directory, and the app
files that code imports, with AppGenerator's persistence resolver. It proves
reach only from forms it recognises:

- the persistence calls above, with arguments it reads as string constants.
  A constant counts only if no app file may set an attribute of that name.
  Any other argument stands for every collection the contract lets that call
  reach;
- persistence handles followed through assignments, helper functions that
  return them, and attribute names drawn from known strings.

Anything else that could reach records makes the module's reach unscoped
(row 9):

- a persistence handle passed to another function, kept in a container, or
  used outside its API;
- reflection over objects, namespaces or frames, and module state set at run
  time;
- code imported by a name chosen at run time, loaded through the import
  system, compiled, or run in another process;
- a database driver or the runtime's raw client internals, in any file the
  module's code follows;
- code the AppGenerator persistence guard rejects;
- code too large or too deeply nested to read within the reading's bounds.

The reading is static and bounded, not a sandbox. It follows the code a
module imports, not objects handed to that code at run time, and it does not
read dependencies outside the app bundle. A module without findings has no
unprotected reach the scanner could read. That is not proof that it has none.

## Decision Table

Each row is an action with the declared contracts on the left. "Another user's
records" means records another signed-in user created. "Reaches" means the
module's reach, as defined above.

| # | Declared contracts | Anonymous caller | Signed in, without the plan | Another user's records | SecurityReadiness |
|---|---|---|---|---|---|
| 1 | `public` or `public_readonly` surface | Reaches it | Reaches it | Bounded by collection ownership; an anonymous caller cannot address owned collections | Not reported: public by declaration |
| 2 | `internal` surface, `permissions: []` | Not served by the module route (404) | Not served by the module route (404) | Runs for event reactions, and for the signed-in user who renders a platform surface bound to it | Not reported |
| 3 | `admin_internal` surface, no permission | 401 on the operator route | 403 without an operator role | Operators run it; owned collections still scope it to the operator's own records | High |
| 4 | Authenticated surface, app declares no sign-in | Reaches it on a host without authentication | Not distinguished from anonymous on that host | Not separated on that host | High, whatever the host: the app declares nothing about who may call |
| 5 | Sign-in, declared permission | 401 | 403 without the permission | Bounded by collection ownership | Not reported |
| 6 | Sign-in, no permission, everything the module reaches is `per_user` or `per_workspace` with an owner field | 401 | Own records only | `per_user`: not reachable (404, empty list). `per_workspace`: shared inside the workspace only | Not reported |
| 7 | Sign-in, no permission, the module reaches an `app_wide` collection, gate the default plan does not grant | 401 | 402 | Plan holders read and change shared records | Medium: the gate restricts callers by plan only |
| 8 | Sign-in, no permission, the module reaches an `app_wide` collection, no restricting gate | 401 | Reads and changes shared records | Reads and changes them | High |
| 9 | Sign-in, no permission, the module reaches a collection with no declared ownership, or its reach cannot be resolved | 401 | Reads and changes records that nothing scopes | Reads and changes them | High, with or without a gate |
| 10 | Sign-in, no permission, the module reaches no collection, no restricting gate | 401 | Can call it | Reaches no collection; persistence refuses `collection()` names the contract does not declare | Medium |

Row 9 covers an app without a valid data contract, a collection that declares
neither scoped ownership with an owner field nor `app_wide` tenancy, a literal
or alias name of a collection the contract does not declare, and reach the
scanner cannot resolve.

A restricting gate is an `entitlement_gate` whose capability a valid
`config/subscriptions.yaml` does not grant through its default plan (the
`default_plan_id` of a v1 contract, or any product's default plan in v2). A
gate the default plan grants admits every signed-in user, so it counts as no
gate. The scanner reads the plan catalog, not assignments: an app-level
assignment grants its plan to every caller, and then a gate that plan covers
restricts no one.

Severity follows what the caller can do. High means any signed-in user, or any
caller, can act on records that are not theirs or that nothing declares an
owner for. Medium means the declared contracts bound who calls the action but
not whose records it changes: the gate restricts by plan only (row 7), or
nothing declares what the action reaches (row 10).

## The Scanner Rule

The scanner reads only the app root the runtime binds: `app/` when it holds
`app.json` and the bundle root does not, otherwise the bundle root. Files
outside that root are never loaded.

`module_permissions:missing:{module_id}:{action_id}` is reported for an action
with no permissions, unless one of these holds:

- its surface is exactly `public` or `public_readonly`;
- its surface is exactly `internal` with an explicit `permissions: []`;
- its surface is absent or null, the app declares sign-in, and either
  everything its module reaches is owned (row 6), or its module reaches no
  collection and its `entitlement_gate` is a restricting gate.

Every other surface value, including `admin_internal`, `public_mutation`, a
blank or padded value, and a `permissions` value that is not a list, still
needs a declared permission; the loader rejects the malformed ones, and the
scanner reports them rather than pass a bundle that cannot run.
`module_permissions:undeclared` separately reports permission ids that
`module.yaml` does not declare. Findings remain advisory, as
`factory_app/build_context/SecurityReadiness/baseline_security_controls.yaml`
states, and that file's `module_permissions_declared` check carries this rule.

## Evidence

`tests/test_security_readiness_action_protection.py` builds variants of the
recorded `fdfa818e` bundle. Most change one declared contract; some change
several, add a module, or change module code. Scanner tests assert the
findings and severities of every variant, including malformed contracts,
which return findings rather than fail. Tests of the code reading assert which
persistence uses it resolves and which forms it reports as unresolved.

Eight tests compose a variant's module router and executor against a real
MongoDB and dispatch actions as anonymous and signed-in callers. Signed-in
principals are injected through the `optional_user` dependency rather than
validated from tokens. They cover rows 2, 5, 6 (per user, per workspace and a
paid owner-scoped summary), 7, 8 and 10, reach into another module's
collection, and row 4 on a host without authentication, where every caller
shares one identity. Every dispatched variant loads without a failed module.
Row 3 is covered by `tests/test_admin_module_dispatch.py`. Row 1 and row 9 are
asserted by the scanner tests only.

## Related Docs

- [Tenant Auth And Scope](tenant-auth-and-scope.md)
- [Module System](../modules-systems/module-system.md)
- [Data Contracts](data-contracts.md)
- [Data Contract And Revision Contract](../builder/data-contract-and-revision-contract.md)
