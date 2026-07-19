# Capability Pack Extension Pattern

Status: framework design target.
Updated: 2026-07-19.

This document explains how mozaiks stays app-agnostic while allowing any app
built on the factory to adopt complex capabilities like governance, community,
economics, and participation — without those concepts leaking into the OSS core.

---

## The Core Rule

**mozaiks OSS owns mechanisms. Apps own meaning.**

The OSS provides:
- Module loading and action dispatch
- Event bus (emit / react)
- Capability pack scaffolding
- Hook contracts (`policy_hooks.yaml`) — empty slots that modules fill
- `app_context` — an uninterpreted extensible metadata field on app records
- Module archetypes — blueprints for common module shapes

The OSS does NOT own:
- What `governance_mode` means for a specific app product
- Who "founder" vs "community" is
- What "MozaiksScore" is
- Any revenue, payout, or wallet concept
- Any voting weight formula
- Any domain-specific vocabulary

If a concept makes sense only for one kind of app, it belongs in that app's
modules or in a capability pack, not in the OSS core.

---

## The `app_context` Field

`create_app_record` accepts an `app_context: object` field. The `app_registry`
module does not interpret, validate, or act on this field. It stores it and
returns it.

Capability packs that need app-scoped state declare their own keys under
`app_context` in their pack manifest (`app_context_keys`). The modules that
implement the pack own the read/write contract for those keys.

Example: the `community_governance_pack` declares:

```yaml
app_context_keys:
  - key: governance.mode
    description: Current governance mode. Values are app-defined.
    owned_by: governance module
  - key: governance.policy_id
    description: Active weight policy record ID.
    owned_by: governance module
```

The governance module reads and writes `app_context.governance.mode`. It
decides what values are valid and what transitions are allowed. The OSS
never sees those values.

---

## Capability Packs

A capability pack is a named bundle of modules, contracts, events, and
generator guidance that adds a coherent capability to any mozaiks app.

Packs live in `factory_app/build_context/`. Each pack:
- Declares `app_context_keys` it owns
- Declares `capabilities_provided` (namespaced action capabilities)
- Provides module templates under `templates/`
- Provides generator notes that tell AppGenerator what to scaffold and what not to

The `capability_directory.yaml` is the registry. AppGenerator reads it to
recommend packs based on product intent signals.

### Existing packs

| Pack | What it adds |
|------|--------------|
| `mozaikspay` | Managed billing, subscriptions, checkout, wallet |
| `messaging_pack` | DM threads, inbox, read state |
| `social_pack` | Social graph, posts, feed, reactions |
| `commerce_pack` | Product catalog, cart, checkout, orders |
| `community_governance_pack` | Proposals, voting, governance mode, weight hooks |

### Pack isolation rule

Packs must not depend on each other at the module level. If `community_governance_pack`
needs vote weight inputs from a participation policy module, it does that through
a `policy_hooks.yaml` hook contract — not by importing or directly calling the
policy module. The governance module calls the hook; whatever module is wired
to answer that hook is the app's choice.

---

## The Hook Contract Pattern (`policy_hooks.yaml`)

A module can declare hook slots in `contracts/policy_hooks.yaml`. A hook slot
is a named input point where another module provides data.

Example from a governance module:

```yaml
schema_version: mozaiks.policy_hooks.v1
hooks:
  - id: governance-weight-inputs
    description: >
      Called when a proposal opens to snapshot vote weight inputs.
      The answering module returns user/weight rows. If no module answers,
      the governance module falls back to role-weight defaults.
    input: { proposal_id: string, community_id: string }
    output: { rows: [{ user_id: string, weight: integer }], total_power: integer }
```

The hook is a contract. The governance module defines the slot. A separate
policy or participation module provides the implementation. The OSS runtime
dispatches the hook call to whichever module is registered to answer it.

This is how complex capabilities compose without creating circular module
dependencies or baking product logic into the framework.

---

## What mozaiks-app Does Differently

`mozaiks-app` is the reference hosted product built on mozaiks. It uses the
`community_governance_pack` scaffolding as a starting point and then adds
hosted-product-specific behavior on top:

| Concept | Where it lives | Why |
|---------|---------------|-----|
| Generic proposal/vote lifecycle | `community_governance_pack` templates | Any governance app needs this |
| `governance.mode` key in `app_context` | Governance pack's `app_context_keys` | Generic — any app may have a mode |
| `founder_led` / `community_dao` vocabulary | `mozaiks-app` module, not OSS | mozaiks-app product terms |
| One-way DAO lock rule | `mozaiks-app` module service | Product policy, not framework |
| MozaiksScore | `mozaiks-app` `app_participation_policy` module | Hosted product concept |
| Revenue settlement via governance | `mozaiks-app` `community_revenue_participation` | Hosted product |
| Investor Marketplace | `mozaiks-app` `investor_marketplace` module | Hosted product |

The layering is:

```
OSS framework          —  event bus, module loader, app_context field, hook contracts
capability pack        —  generic governance scaffold, app_context keys, hook slots
mozaiks-app modules    —  concrete semantics, MozaiksScore, revenue, product UX
```

---

## How to Add a New Capability

If a new capability is needed that might apply to multiple apps:

1. **Create a capability pack** in `factory_app/build_context/<pack_name>/`
2. **Declare `app_context_keys`** your pack needs in the pack manifest
3. **Add module templates** under `templates/` for the modules the pack installs
4. **Declare hook slots** via `contracts/policy_hooks.yaml` in the module templates
5. **Register the pack** in `capability_directory.yaml` with intent signals and generator notes
6. **Do not implement the business semantics** — only the shape. The app that uses the pack decides the values.

If the capability is unique to one hosted product and would never make sense
for another app built on mozaiks:

- It belongs in that product's own modules, not in a capability pack or the OSS

---

## What NOT to Put in OSS

These patterns are wrong and should be caught in review:

```yaml
# WRONG — product vocabulary in OSS app_registry
governance_mode:
  enum: [founder_led, community_dao]

# WRONG — product action in OSS module
- id: set_governance_mode
  emits: domain.app_registry.governance_mode_changed

# WRONG — revenue concept in generic module
revenue_split_bps: integer

# WRONG — hosted product event namespace in OSS
emits: hosted.app_registry.app.visibility_changed
```

Correct approach for each:

```yaml
# CORRECT — generic extensible field in OSS
app_context:
  type: object
  description: Capability-pack-owned metadata. Not interpreted by app_registry.

# CORRECT — app product action in mozaiks-app module override
- id: set_app_visibility   # in mozaiks-app's app_registry module.yaml

# CORRECT — economics in a capability pack hook slot
hooks:
  - id: revenue-distribution-inputs  # in community_governance_pack/policy_hooks.yaml

# CORRECT — hosted event in mozaiks-app module contracts
emits: hosted.app_registry.app.visibility_changed  # in mozaiks-app only
```
