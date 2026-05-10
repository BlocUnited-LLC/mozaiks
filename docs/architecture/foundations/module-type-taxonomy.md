# Module Type Taxonomy

Every module in Mozaiks follows the same base contract — `module.yaml`, `contracts/`,
`backend/`, and the handler → service → repo → schemas → policy stack. But not every
module has the same *shape* of problem.

This document defines the three canonical module types. The type governs backend layer
conventions and how the factory scaffolds the module. Type-specific YAML additions are
not used — the distinction is entirely in naming conventions and implementation patterns.

---

## The `type` Field

Every module may declare its type in `module.yaml`:

```yaml
# app/modules/my_module/module.yaml
module:
  id: my_module
  # ...
  type: standard   # standard | messaging | transactional
```

The type is a scaffolding and convention signal, not a runtime enforcement. The
platform does not gate behavior on the type field. The factory uses it to select
the right backend template and apply type-aware prompts. Treat it as a required
declaration from day one.

---

## Type Overview

| Type | Core Nature | Signature Problem |
|------|-------------|-------------------|
| `standard` | Request/response CRUD | Shared logic, listings, profiles, settings |
| `messaging` | Stateful, real-time, event-driven | Threads, DMs, presence, unread tracking |
| `transactional` | Atomic, ledger-like, audit trail | Wallets, payments, asset issuance |

All three types share the same module shape and Python layer structure. The differences
are in backend conventions and how events are shaped.

---

## Base Contract (all types)

```text
{module_name}/
├── module.yaml              # Required: identity, actions, capabilities
├── contracts/               # Optional companion manifests
│   ├── events.yaml          # Domain events this module may publish
│   ├── reactions.yaml       # Event reactions owned by this module
│   ├── notifications.yaml   # Notification rules per event
│   ├── settings.yaml        # User/app settings schema
│   ├── admin.yaml           # Admin panels mounted into /admin/*
│   └── entitlements.yaml    # Optional capability entitlements
├── runtime_extensions.yaml  # Optional: api_router / startup_service
└── backend/
    ├── __init__.py
    ├── handler.py     # thin dispatch, one method per declared action
    ├── service.py     # all business logic and event emission
    ├── repo.py        # MongoDB access only, no logic
    ├── schemas.py     # TypedDict shapes + helper functions
    └── policy.py      # query scoping for multi-tenancy
```

No type-specific YAML files are added. All companion manifests live under `contracts/`
and are optional for every type.

---

## `standard`

The default type. Use it unless one of the other two is a clear fit.

### When to use

- Shared page backing logic (listings, directories, registries)
- User-facing CRUD (profiles, preferences, content)
- Integration bridges that expose read/write actions
- Any module whose actions are one-shot request/response

### Backend conventions

- `service.py` calls `ctx.emit()` after every state-changing action
- `repo.py` methods accept only primitives; no domain objects cross the repo boundary
- `schemas.py` uses TypedDict for all document shapes; no ORM

### Examples

`app_registry`, `hosting`, `investor_marketplace`

---

## `messaging`

Use for modules whose core concern is communication between participants — threads,
direct messages, group channels, or any surface where read/unread state matters.

### When to use

- User-to-user or user-to-group communication
- Thread-based discussion where read state is per-participant
- Any feature where a sent message needs to *reach* a recipient, not just be stored

### Backend conventions

- Every message document carries a `thread_id` and a `sender_id`
- Thread documents carry a `participants` array with per-member read state:
  `last_read_message_id`, `last_read_at`, `unread_count`
- `repo.py` provides an `upsert_read_state` method; unread counts are never
  computed inline in the service
- Events emitted by `service.py` include enough payload for real-time rendering
  without a follow-up fetch (sender name, avatar, body preview, timestamps)
- Soft delete is per-participant, not global

### Module boundary rule

If the discussion is *attached to* another entity (e.g., comments on a governance
proposal), that belongs in the module that owns the entity — not in a `messaging`
module. A `messaging` module owns standalone communication surfaces.

### Examples

`communications` (DMs, threads, announcements)

---

## `transactional`

Use for modules whose core concern is moving value — money, tokens, credits, or
any asset where the history of movements is as important as the current balance.

### When to use

- Wallet and balance management
- Payment processing and payout flows
- Any feature where a record must be append-only or double-entry

### Additional YAML

None beyond the base contract. The distinction is entirely in backend conventions.

### Backend conventions

- **Append-only ledger**: state is derived from a ledger of entries, not a mutable
  balance field. `repo.py` provides `append_entry`; it never provides
  `update_balance`
- **Atomic operations**: all writes that touch more than one document use MongoDB
  sessions or are structured so partial writes are detectable and safe to replay
- **No direct mutations**: `service.py` never issues a `$set` on a balance or
  value field directly. All changes go through ledger entries or atomic `$inc`
  operations
- **Audit trail**: every entry carries `actor_id`, `action`, `amount`, `reference_id`,
  and timestamps. No entry is ever deleted
- `schemas.py` defines separate TypedDicts for the balance projection (derived) and
  the ledger entry (source of truth)

### Examples

`wallet`, `subscriptions`, `token_ledger`

---

## Decision Guide

Start here when deciding which type a new module should be:

```
Does the module manage communication between participants
where read state and real-time delivery matter?
  → messaging

Does the module move money, tokens, or assets where
the history of movements is the source of truth?
  → transactional

Everything else
  → standard
```

When in doubt, start with `standard`. Promoting to a more specific type later is
straightforward — it means tightening backend conventions, not restructuring the shape.

---

## Capability Ownership and Module Types

Module type is an *implementation convention* — it does not change whether a
capability is generated, selected from a pack, or hosted.

See [module-system.md](module-system.md) for capability ownership classification:
`host_universal`, `framework_pack`, `hosted_pack`, `generated_module`, `external_adapter`.

Most `messaging` and `transactional` capabilities in practice are `framework_pack`
or `hosted_pack` — not regenerated from scratch per app. AppGenerator generates
`standard` modules most frequently; it uses `messaging` and `transactional` types
when the app explicitly requires communication or ledger behavior that does not
overlap with an existing framework pack.

---

## Factory Behavior

The factory reads `type` from `module.yaml` and:

- selects the matching scaffold template for `backend/`
- uses type-aware prompts for action and event generation

Structured output validation and CI enforcement per type are roadmap items.
For now, the type declaration is the contract — honor it in the implementation
even when the tooling does not yet enforce it.

---

## Cross References

- [module-system.md](module-system.md)
- [canonical-app-structure.md](canonical-app-structure.md)
- [app-bundle-declaratives.md](app-bundle-declaratives.md)
- [capability-pack-model.md](capability-pack-model.md)
