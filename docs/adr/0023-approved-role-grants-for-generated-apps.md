# ADR 0023: Approved Role Grants for Generated Apps

Date: 2026-11-02

Status: accepted (implementation in progress)

The operator approved this direction on 2026-11-06, including verified-email
promotion, explicit non-holder denial, fail-closed omitted grants, and
preservation of persistence ownership. This records the decision, not a claim
that implementation or live acceptance is complete.

## Context

A live traversal on `main` at `8174b191`
([#1005](https://github.com/BlocUnited-LLC/mozaiks/issues/1005)) built a
tool-lending library. Coordinators add tools and approve borrow requests. The
`tools` collection is `app_wide`. AppGenerator rejected the module contract
twice, and the build failed:

> app_wide collection 'tools' has protected actions [create_tool
> (permissions=['tool.create']) …]; … restricted by api_surface internal or
> admin_internal, an approved entitlement gate, or permissions declared in
> config/auth.yaml (declared grants=['email','openid','profile'])

"Staff manage shared records; members request" is one of the most common app
shapes, and today no admitted contract can express it.

- **Only token scopes grant permissions.** Module dispatch checks an action's
  `permissions` against `principal.scopes`, optionally replaced by a
  deployer-level scope hook (`mozaiksai/hosts/routers/modules.py`
  `_resolve_module_dispatch_scope`; `ModuleExecutor._build_permission_check`).
  Roles never feed module permissions.
- **The auth contract has nowhere to grant one.** `AppAuthContract`
  (`mozaiksai/core/runtime/app/auth_contract.py`) is strict, and its only
  permission-shaped field is `frontend.default_scopes`, the OIDC scopes the
  browser requests for *every* user.
- **The model must not grant permissions.** No build task may own
  `config/auth.yaml` (AppGenerator `agents.yaml`, `app_build_plan.py`,
  `module_write_actions.py`), and a candidate cannot approve its own
  permission. That rule is correct and stays.
- **Admission can only see default scopes.** `auth_contract_scopes`
  (`mozaiksai/core/workflow/generator_support/module_write_actions.py`) returns
  `default_scopes`. Its own docstring states that plan roles and module
  permission catalogues are not grant mappings.
- **The only admitted alternative is unusable from the app.** `api_surface:
  admin_internal` returns 404 on `/api/modules`, is skipped by page bindings,
  and is reachable only through `/api/admin/modules`. That route requires a
  `platform_operator` role, which an app's own admins never hold.
- **Nothing approved says who may do what.** ValueEngine personas are free
  text. DesignDocs `DesignSurface` has no role fields. `AppBuildPlan.roles` is
  AppPlanAgent output, not approved design.
- **A precedent exists.** The SubscriptionContract's approved
  `selected_features_by_plan` is compiled into action `entitlement_gate`s by
  code (`module_entitlement_gates.py`), and the model is told not to author
  gates.

## Decision

Generated apps grant permissions to roles. The grants come only from the
**approved design**, and **code** compiles and enforces them, never a model
task.

1. **Approved source.** DesignDocs emits a typed `access_policy` as part of the
   design the user approves:
   - `roles[]`: `{role_id, label, description}`
   - `grants[]`: `{role_id, collection, actions[]}`, where `actions` lists
     canonical write ids, exact `get_{collection}` / `list_{collection}` ids,
     and approved `owned_mutations` / `custom_reads` for that collection.

   `save_design_docs_bundle` validates every reference deterministically. A
   nonempty grant policy is reviewed through the shipped ApprovalCard and UI
   tool response boundary before saving its approval receipt. A saved or
   CURRENT summary alone is not approval: genesis summaries previously became
   CURRENT without human review. The receipt binds the reviewed policy digest,
   app, build and responding user. An empty policy grants no app role authority.
2. **Code-derived permission ids.** For each granted action, code sets the
   action's `permissions` to `["{collection}.{action_id}"]`, the same way
   approved entitlement gates are compiled onto actions. The model authors
   neither the ids nor their assignment.
   Each module action has one collection anchor across every role's grants,
   even when its implementation writes several collections. Validation reports
   every conflicting action and collection alongside missing canonical grants;
   it never chooses an anchor or changes the proposed authority.
3. **Compiled auth contract.** `config/auth.yaml` moves to
   `mozaiks.auth.v2`, which adds `authorization.roles[]: {id, permissions[]}`.
   The code-owned auth scaffold renders it from the approved `access_policy`,
   and it is re-rendered when the approved policy changes. Build tasks still
   cannot own the file.
4. **Admission.** The granted set used during task admission, repair, final
   assembly, and runtime smoke becomes `default_scopes ∪ every permission an
   approved role grants`. The existing restriction checks then admit
   role-granted app_wide writes unchanged.
5. **Runtime.** One shared resolver computes a principal's module permissions
   as `token scopes ∪ permissions of the roles the principal holds`, read from
   the loaded app's auth contract. Every module dispatch and profile hydration
   site uses that resolver instead of `principal.scopes` alone. Role
   membership comes from:
   - the verified identity provider's role claim (`principal.roles`);
   - for the reserved role `admin` only, the `app.json` `admins` email
     allowlist the admin shell already honors, using the same ASCII-normalized
     matching and verified-email requirement (or already admitted local
     development access). A raw, unverified email never grants a role.

   Product-defined tenant memberships stay with the existing deployer scope
   hook.
6. **Verification.** Runtime smoke mints role-bearing and role-less principals
   and must observe both allow and deny through authenticated dispatch. The
   admission union is not assigned to test principals: a caller receives only
   its token scopes and held-role grants. SecurityReadiness treats a
   role-granted permission as a declared permission.
7. **Incomplete grants and ownership.** Missing grants never make a canonical
   shared-record write public. A role-managed collection must explicitly cover
   each canonical write before promotion; an incomplete policy fails validation.
   Role permissions do not bypass per-user or per-workspace persistence
   ownership. Access to another user's records requires a compatible approved
   data contract, not merely a role.
   For cross-user review, an app-wide authoritative collection can expose
   explicitly protected administrative reads and writes plus separate member
   submission and owner-filtered reads. Domain authorization in the service
   derives the member identity from the authenticated context and clamps input
   fields and initial state. A shared projection cannot authorize updates to a
   different user's per-user source record.

### Explicit read grants (2026-11-07 implementation extension)

A live baseline exposed a gap in the original mutation-only policy: its design
promised member-only reads, but owner-scoped read construction removed read
permissions. Authentication and ownership cannot enforce role removal for a
person's existing records.

The same reviewed grant shape now admits read action ids. Selecting either
canonical read requires explicit coverage of both `get_{collection}` and
`list_{collection}` across the declared roles; an omitted raw read must not
remain an alternate way to retrieve the records. Existing canonical-write
completeness still applies to role-managed mutable collections. A custom read
grant alone does not grant or restrict canonical reads. Without explicit read
grants, owner-scoped reads retain their authenticated, owner-filtered behavior;
write roles never imply read roles.

Canonical action ids remain anchored to their declared collection. Repeating
another collection's canonical action in a custom-action inventory cannot
reassign its grant or evade canonical action coverage.

The read compiler derives permissions from the reviewed policy at typed-output,
file-materialization and repeated-closure boundaries. It keeps owner-scoped read
implementations code-owned, and preserves explicitly authored app-wide read
schemas and API exposure. `ModuleExecutor` already enforces the resulting
permissions; there is no new role source, global host gate, ownership bypass,
auth schema version or subscription entitlement behavior. Changed read grants
require a fresh policy review. This extension does not claim completed live
generated-app acceptance.

## Reason

This unblocks the most common shape of staff-managed app without weakening
the rule that a candidate cannot grant itself access. DesignDocs adds an
enforced policy review using the existing UI tool approval mechanism; its
previous HITL prompt flag was not an approval gate. The compilation pattern
already exists (subscription entitlement gates). The change connects them through
the one contract, `config/auth.yaml`, that both generation and runtime already
read.

## Alternatives Considered

- **`admin_internal` for staff actions.** Admitted today, but the app's own
  pages cannot call the actions, and `/api/admin/modules` requires
  `platform_operator`. Coordinators could not approve requests in the app.
- **Let a build task write role grants into `config/auth.yaml`.** A candidate
  would approve its own permissions. This reverses the current
  self-grant ban.
- **Add role permissions to `frontend.default_scopes`.** Every signed-in user
  would request them, and the identity provider, not the app, would decide
  who receives them. This is not role-based.
- **Leave it to the hosted scope hook.** The hook is deployer-level and
  cannot be set by a generated bundle, so OSS and self-hosted apps would stay
  unbuildable. It remains the extension point for tenant memberships.
- **Per-user tenancy.** It makes records owner-scoped, which is a different
  meaning from staff authority over shared records.

## Consequences

- **Easier:** staff-managed app_wide collections generate, pass admission,
  and work from the app's own pages.
- **Harder:**
  - DesignDocs output grows by one typed field and one review concern.
  - The auth contract takes a versioned schema change.
  - The auth scaffold must re-render on approved policy changes. Today it
    never overwrites `config/auth.yaml`.
- **Security boundary:** runtime gains a second permission source, roles. It
  is bounded by the approved policy and by verified role claims. Local
  development roles (`AUTH_ANON_ROLES`) grant role permissions only where
  development access is already granted.

## Reversibility

Medium risk: `mozaiks.auth.v2` and the DesignDocs `access_policy` become
contracts that generated apps carry, and reversing them needs a migration.

## Affected Invariants

- **3. Agents Produce Candidates; Deterministic Code Validates and Promotes.**
  Strengthened: grants come from an approved artifact and are compiled by
  code.
- **4. Public Schemas and Contracts Are Classified and Versioned.** The auth
  contract moves to `mozaiks.auth.v2`.
- **6. Authority Bypass Semantics Must Not Expand Casually.** Unchanged: role
  grants are ordinary permissions checked by the existing permission check,
  not a trusted bypass.

## OSS Boundary

Keep OSS: foundational. Every generated app with staff roles needs this.

## Relationship to Issue #411

This ADR **adopts** #411's narrow direction that an approved, typed decision
is compiled deterministically into generated artifacts. It does **not**
introduce a decision ledger. The approved DesignDocs artifact remains the only
authority for the access policy.

## Validation

- **DesignDocs:** a model test for `access_policy`, and save-time rejection of
  grants that name an unknown collection, a non-approved action, or an unknown
  role.
- **Compilation:** golden tests showing that an approved policy produces the
  action permissions and `authorization.roles` in `config/auth.yaml`, and that
  an empty policy changes nothing.
- **Admission:** the #1005 tool-lending module contract is admitted with
  role-granted writes. A candidate that names a permission no approved role
  grants is still rejected, including one that edits `config/auth.yaml`.
- **Runtime:** a role holder is allowed, a non-holder is denied, and an
  `app.json` admin email is mapped to `admin` only. The resolver is used at
  every dispatch site, guarded by a test that enumerates the sites.
- **Live:** the #1005 traversal reaches the app-bundle accept step.

## Implementation Slices

1. `access_policy` in DesignDocs (structured output, prompt, save validation).
2. `mozaiks.auth.v2` plus scaffold rendering and compiled action permissions.
3. The admission granted set at all four stages.
4. The runtime resolver at every dispatch site, plus smoke and
   SecurityReadiness.
5. Docs: data-contract-and-revision-contract, generated-action-protection,
   module-system, admin-system, and the CHANGELOG.
