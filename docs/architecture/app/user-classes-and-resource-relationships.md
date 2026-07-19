# User Classes And Resource Relationships

Status: design target.
Updated: 2026-07-18.
Scope: OSS `mozaiks` framework and generated app contracts.

This document defines how generated Mozaiks apps should model app-specific
user classes such as owner, builder, teacher, student, vendor, buyer,
moderator, member, sponsor, or contributor.

The design is host-agnostic. It gives generated apps deterministic primitives
for classifying users relative to app resources without copying proprietary
Mozaiks App billing, wallet, campaign, hosting, or payout logic.

## Decision Summary

- Authentication identifies the caller. It does not by itself define durable
  app roles, memberships, ownership, voting power, or economic rights.
- App-specific user classes belong in app-owned modules under
  `app/modules/`.
- Class assignments are scoped to a resource such as an app, workspace,
  project, team, community, listing, document, or campaign.
- `contracts/relationships.yaml` remains a resource inventory and routing
  contract. It does not grant rights by itself.
- `contracts/policy_hooks.yaml` is the optional generic hook contract for
  app-owned modules that need classification, access, or decision inputs.
- Authorization remains in module service/policy code using `ctx.user_id`,
  `ctx.tenant_id`, `ctx.workspace_id`, `ctx.permissions`, and module-owned
  records.
- Generated apps that need durable roles or memberships should receive a
  membership/user-class module from AppGenerator instead of ad hoc fields on
  unrelated records.
- Frozen vote, approval, judging, or allocation records are owned by the
  consuming app module. They are not a universal OSS product feature.
- Hosted products can build proprietary policy on top of these primitives, but
  OSS must not hardcode hosted Mozaiks business classes like platform billing
  owner, MozaiksPay payout recipient, campaign backer return rights, or managed
  hosting operator.

## Terms

| Term | Meaning |
| --- | --- |
| User | Authenticated person or service principal. |
| Principal | Provider-neutral identity facts decoded by the auth adapter. |
| User class | App-defined label for a user's relationship to a resource, such as `teacher`, `student`, `vendor`, `buyer`, `moderator`, or `member`. |
| Resource relationship | A current-user row that says which resource the user is connected to and where the shell should route them. |
| Class assignment | Durable app-owned record that gives a user a class for a scoped resource. |
| Capability hint | UI-safe description of what the user may see or open. It is not authorization truth. |
| Policy hook | Module-declared action contract that lets another app module ask for access, classification, or deterministic decision input. |
| Decision record | App-owned immutable process record, such as a vote roster or review roster, created by the consuming module when that domain requires frozen inputs. |

## Existing Foundation

The OSS runtime already provides:

- provider-neutral `UserClaims`
- `UserPrincipal`
- auth adapters for JWT/OIDC-style providers
- `ModuleContext` with `app_id`, `user_id`, `tenant_id`, `workspace_id`, and
  `permissions`
- module action permission checks
- entitlement gates
- app-scoped persistence through `ctx.persistence.collection(...)`
- `contracts/relationships.yaml`
- `contracts/policy_hooks.yaml`
- `/api/me/relationships`
- route authorization metadata that can call app-owned module actions

These are the correct primitives. The missing generator concept is a standard
way to produce app-owned user-class and membership modules when an app needs
durable role semantics.

## Boundary

OSS owns:

- identity and scope contracts
- provider-neutral auth adapter behavior
- module dispatch and permission enforcement
- relationship provider discovery and normalization
- AppGenerator guidance for app-owned class/membership modules
- host-agnostic examples and tests

Apps own:

- which user classes exist
- which resources can have class assignments
- invite/accept/remove flows
- per-resource authorization policy
- policy hook actions, when another module needs classification, access, or
  decision-input answers
- whether class assignments imply voting weight, moderation access, workflow
  approval authority, app-owned allocation eligibility, or other app-specific
  behavior
- decision records or freeze semantics for votes, reviews, approvals, contests,
  or app-owned allocations

Hosted products own:

- platform subscription policy
- managed hosting policy
- wallet and payout execution
- hosted billing records
- concrete payment provider flows
- proprietary marketplace, campaign, or revenue participation policy

## Canonical App Structure

Generated app user-class behavior belongs in normal modules:

```text
app/modules/{membership_module}/
  module.yaml
  contracts/
    events.yaml
    relationships.yaml
    notifications.yaml      # optional
    settings.yaml           # optional
    admin.yaml              # optional
    policy_hooks.yaml       # optional
  backend/
    handler.py
    service.py
    repo.py
    policy.py
    schemas.py
```

Use `app/services/adapters/auth/` only when the generated app directly owns
provider-specific auth mechanics. Most generated apps should rely on OSS auth
adapters and keep app role/class truth in modules.

Use `app/data/contract.json` only when the app needs explicit cross-module
collection authority, aliases, indexes, or migrations. Default generated
modules can use generated-scoped persistence.

## Recommended Generated Module Shape

For an app that needs user classes, AppGenerator should create a module with a
domain-specific name, for example:

- `team_membership`
- `project_membership`
- `classroom_membership`
- `marketplace_participants`
- `community_membership`
- `organization_membership`

The module should declare actions such as:

- `invite_member`
- `accept_invitation`
- `update_member_class`
- `remove_member`
- `list_members`
- `get_my_membership`
- `authorize_resource_route`
- `list_user_relationships`
- `evaluate_policy_hook`

The module should own records shaped like:

```json
{
  "assignment_id": "assignment_123",
  "resource_type": "project",
  "resource_id": "project_abc",
  "user_id": "user_123",
  "class_id": "collaborator",
  "status": "active",
  "permissions": ["project.read", "task.write"],
  "created_by": "user_owner",
  "created_at": "2026-07-18T12:00:00Z",
  "updated_at": "2026-07-18T12:00:00Z"
}
```

The exact fields are app-specific, but these rules are stable:

- include `user_id`
- include a resource scope
- include class or role id
- include status
- include timestamps
- scope reads from `ctx.user_id`, `ctx.tenant_id`, and `ctx.workspace_id`
- reject untrusted request-body role/class claims for authorization
- serialize list responses through allowlist helpers

## Relationship Provider Output

The membership module should expose safe current-user inventory through
`contracts/relationships.yaml`.

Example:

```yaml
schema_version: mozaiks.relationships.v1

providers:
  - id: joined-projects
    label: Joined Projects
    action: list_user_project_relationships
    resource_types: [project]
    relationship_types: [owner, collaborator, reviewer, viewer]
```

Provider rows should answer:

```text
what resource is this user connected to?
what is their relationship type?
where should the shell route them?
what UI-safe capability hints can be displayed?
```

Provider rows must not answer:

```text
should this payment execute?
does this user own equity?
what payout should this user receive?
which private provider secret should this user see?
```

## Policy Hooks

When one app module needs to ask another module how a user or resource should
be classified, it should use a declared policy hook instead of importing service
internals or inventing a global score field.

Example:

```yaml
schema_version: mozaiks.policy_hooks.v1

hooks:
  - id: project-participation
    label: Project Participation
    hook_type: decision_input
    action: evaluate_project_participation
    resource_types: [project]
    deterministic: true
```

The hook action returns an allowlisted, app-owned shape. For example:

```json
{
  "eligible": true,
  "resource_type": "project",
  "resource_id": "project_abc",
  "user_id": "user_123",
  "class_ids": ["collaborator"],
  "capabilities": ["project.read", "task.write"],
  "decision_inputs": {
    "review_weight": 10
  },
  "policy_version": "2026-07-18"
}
```

Policy hook rules:

- hooks point to declared module actions
- hook actions derive scope from `ctx.user_id`, `ctx.tenant_id`,
  `ctx.workspace_id`, `ctx.permissions`, and module-owned records
- outputs are allowlisted DTOs, not raw membership or ledger documents
- app-owned Python evaluators may live in normal module backend code, reviewed
  and deployed with the app bundle
- admin-authored raw Python is not a production-safe OSS feature
- hooks do not execute payments, grant equity, modify subscriptions, or run
  hosted-product provider logic

## Route Authorization Summary

When a generated app has scoped private routes, route authorization should call
an app-owned summary action instead of trusting global role strings.

Example:

```yaml
meta:
  routeAuth:
    module: project_membership
    action: authorize_project_route
    params:
      project_id: ":projectId"
```

The action should return a minimal shape:

```json
{
  "allowed": true,
  "resource_type": "project",
  "resource_id": "project_abc",
  "viewer_class": "collaborator",
  "capabilities": ["project.read", "task.write"]
}
```

It must not return secret fields, payment provider internals, private ledger
rows, or raw membership documents.

## Decision Records

Some generated apps need durable domain decisions that must not change when a
user's class changes later.

Examples:

- proposal vote weight
- approval quorum
- contest judging weight
- grant allocation review
- revenue share, when an app explicitly implements its own compliant
  monetization policy

For these cases, the consuming module should store its own decision record at
the moment the process becomes active. The membership or policy module can
provide policy-hook inputs, but it should not own every downstream product's
freeze semantics.

Required rules:

- freeze app-owned inputs before votes, approvals, reviews, allocations, or
  other decision calculations are counted
- store the frozen decision rows in the consuming module or a declared data
  contract alias
- store policy version/hash or source metadata where relevant
- never mutate prior decision records to reflect later membership changes
- create a new process record if the policy needs to change

## AppGenerator Work Required

### Module Archetype

Add a host-agnostic membership/user-class archetype to:

```text
factory_app/build_context/AppGenerator/module_archetypes.yaml
```

The archetype should apply when an app requires:

- team members
- organization roles
- project collaborators
- communities
- moderators
- invite flows
- private resource routes
- role-based workflow approvals
- deterministic vote or review weights
- app-owned policy hook inputs for decisions

### File Contracts

Update:

```text
factory_app/build_context/AppGenerator/file_contracts.yaml
```

Required guidance:

- do not put durable app classes only in JWT claims
- do not trust request body user/class/role fields for authorization
- define class assignment records in module schemas
- keep route authorization summaries minimal
- use `contracts/relationships.yaml` for current-user resource inventory
- use `contracts/policy_hooks.yaml` when another module needs deterministic
  access/classification/decision inputs
- keep decision records and freeze semantics in the consuming feature module

### Structured Outputs

Update AppGenerator structured outputs only if planning needs a first-class
field such as:

```yaml
user_class_model:
  required: true
  resource_types: [project]
  classes: [owner, collaborator, reviewer, viewer]
  invitation_required: true
  route_authorization_required: true
```

The field should describe app-level needs, not hosted Mozaiks product policy.

### Validation And Tests

Add tests that prove generated membership modules:

- use canonical module structure
- read `ctx.user_id`, `ctx.tenant_id`, and `ctx.workspace_id`
- scope queries in `backend/policy.py`
- serialize list responses through allowlist helpers
- expose relationships through `contracts/relationships.yaml`
- expose policy hooks through `contracts/policy_hooks.yaml` when another
  module needs deterministic policy input
- do not trust request body role/class fields
- do not hardcode hosted Mozaiks App classes or MozaiksPay concepts

## Example Patterns

### Classroom App

Classes:

- `teacher`
- `student`
- `guardian`

Teacher can create assignments. Student can submit work. Guardian can view
progress. No hosted-product monetization policy is implied.

### Marketplace App

Classes:

- `seller`
- `buyer`
- `moderator`

Seller owns listings. Buyer owns orders. Moderator reviews disputes. Payment
provider mechanics are app-owned only if the generated app directly integrates
payments; otherwise hosted products can provide a facade.

### Project Collaboration App

Classes:

- `owner`
- `collaborator`
- `reviewer`
- `viewer`

Owner manages membership. Collaborator edits tasks. Reviewer approves
milestones. Viewer reads only.

### Community App

Classes:

- `owner`
- `admin`
- `contributor`
- `member`
- `viewer`

Governance, vote weights, and any economic participation must be explicit
app module policy. They are not automatic consequences of auth.

## Acceptance Criteria

The OSS user-class model is ready when:

- AppGenerator can intentionally create a membership/user-class module for
  apps that need durable roles.
- Generated modules store class assignments as app-owned records.
- Generated route guards use module summary actions instead of global role
  strings alone.
- `/api/me/relationships` can list user resources without granting rights.
- Authorization remains in module policy/service code.
- Optional `contracts/policy_hooks.yaml` lets generated modules expose
  deterministic policy inputs without hardcoding governance or economics.
- Modules that need frozen outcomes store their own decision records.
- No proprietary Mozaiks App billing, wallet, campaign, or payout policy
  appears in OSS templates, prompts, generated examples, or tests.

## Related Docs

- [Tenant Auth And Scope](tenant-auth-and-scope.md)
- [Canonical App Structure](canonical-app-structure.md)
- [Platform Authoring](platform-authoring.md)
- [Relationship Provider Contract](../foundations/relationship-provider-contract.md)
- [Module Authoring Patterns](../modules-systems/module-authoring-patterns.md)
