# Module Type Taxonomy

Every module in Mozaiks follows the same base contract — `module.yaml`, `events.yaml`,
`backend/`, and the handler → service → repo → models → policy stack. But not every
module has the same *shape* of problem.

This document defines the four canonical module types. The type governs which YAML
files are added beyond the base contract, what conventions the backend layers follow,
and how the factory scaffolds the module.

---

## The `type` Field

Every module declares its type in `module.yaml`:

```yaml
# app/modules/my_module/module.yaml
name: my_module
type: standard   # standard | messaging | workflow | transactional
```

The type is a scaffolding and convention signal, not a runtime enforcement. The
platform does not gate behaviour on the type field today. The factory uses it to
select the right template and YAML additions. Treat it as a required declaration
from day one.

---

## Type Overview

| Type | Core Nature | Signature Problem |
|---|---|---|
| `standard` | Request/response CRUD | Shared logic, listings, profiles, settings |
| `messaging` | Stateful, real-time, event-driven | Threads, DMs, presence, unread tracking |
| `workflow` | State machine, multi-party, time-bound | Proposals, approvals, voting, review flows |
| `transactional` | Atomic, ledger-like, audit trail | Wallets, payments, asset issuance |

All four types share the same Python layer structure. The differences are in YAML
additions, naming conventions, and how events are shaped.

---

## `standard`

The default type. Use it unless one of the other three is a clear fit.

### When to use

- Shared page backing logic (listings, directories, registries)
- User-facing CRUD (profiles, preferences, content)
- Integration bridges that expose read/write actions
- Any module whose actions are one-shot request/response

### Base contract (all types inherit this)

```text
{module_name}/
├── module.yaml
├── events.yaml
├── subscriptions.yaml
├── notifications.yaml
├── settings.yaml
├── admin.yaml
└── backend/
    ├── __init__.py
    ├── handler.py     # thin dispatch, one method per declared action
    ├── service.py     # all business logic and event emission
    ├── repo.py        # MongoDB access only, no logic
    ├── models.py      # TypedDict shapes + helper functions
    └── policy.py      # query scoping for multi-tenancy
```

No additional YAML files beyond the base contract.

### Backend conventions

- `service.py` calls `ctx.emit()` after every state-changing action
- `repo.py` methods accept only primitives; no domain objects cross the repo boundary
- `models.py` uses TypedDict for all document shapes; no ORM

### Examples

`app_registry`, `hosting`, `investor_marketplace`

---

## `messaging`

Use for modules whose core concern is communication between participants — threads,
direct messages, group channels, or any surface where read/unread state and
real-time delivery matter.

### When to use

- User-to-user or user-to-group communication
- Thread-based discussion where read state is per-participant
- Any feature where a sent message needs to *reach* a recipient, not just be stored

### Additional YAML

```text
{module_name}/
├── ...base contract...
└── channels.yaml      # transport and delivery channel definitions
```

`channels.yaml` declares the named channels this module can push events into and
the delivery contract for each (websocket, push notification, email digest). This
is what lets the hosting layer route a `message.sent` event to a live WebSocket
without the module owning the transport.

```yaml
# channels.yaml
channels:
  - name: thread_updates
    event: message.sent
    transport: websocket
    payload_fields: [thread_id, sender_id, body_preview, sent_at]
  - name: thread_notifications
    event: message.sent
    transport: push
    when: recipient_is_offline
```

### Backend conventions

- Every message document carries a `thread_id` and a `sender_id`
- Thread documents carry a `participants` array with per-member read state:
  `last_read_message_id`, `last_read_at`, `unread_count`
- `repo.py` provides an `upsert_read_state` method; unread counts are never
  computed inline in the service
- Events emitted by `service.py` include enough payload for real-time rendering
  without a follow-up fetch (sender name, avatar, body preview, timestamps)
- Soft delete is per-participant, not global: a participant can leave a thread
  without destroying it for others

### Module boundary rule

If the discussion is *attached to* another entity (e.g., comments on a governance
proposal), that belongs in the module that owns the entity — not in a `messaging`
module. A `messaging` module owns standalone communication surfaces.

### Examples

`communications` (DMs, threads, announcements)

---

## `workflow`

Use for modules whose core concern is a process that moves through defined states,
involves multiple parties, and may be time-bound.

### When to use

- Approval or review flows with explicit accept/reject/revise states
- Voting or proposal systems where participation windows matter
- Any feature where the current *state* of a record drives what actions are available
  and who can take them

### Additional YAML

```text
{module_name}/
├── ...base contract...
├── states.yaml        # state machine definition
└── transitions.yaml   # who can trigger which transition and under what conditions
```

`states.yaml` declares the named states and which are terminal:

```yaml
# states.yaml
states:
  - name: draft
    terminal: false
  - name: active
    terminal: false
  - name: passed
    terminal: true
  - name: rejected
    terminal: true
  - name: expired
    terminal: true
initial_state: draft
```

`transitions.yaml` declares valid transitions, the required role or condition, and
the event emitted on success:

```yaml
# transitions.yaml
transitions:
  - from: draft
    to: active
    action: submit_proposal
    requires_role: member
    emits: proposal.submitted
  - from: active
    to: passed
    action: close_vote
    requires: vote_threshold_met
    emits: proposal.passed
  - from: active
    to: rejected
    action: close_vote
    requires: vote_threshold_not_met
    emits: proposal.rejected
  - from: active
    to: expired
    trigger: deadline_passed
    emits: proposal.expired
```

### Backend conventions

- Every document managed by the module carries a `state` field
- `policy.py` exposes a `validate_transition(current_state, action, actor_role)`
  helper; the service calls this before any state change
- `service.py` never mutates state directly — it always goes through the transition
  validator
- Events emitted on transition include `from_state`, `to_state`, and the actor

### Examples

`governance`, `app_review`, `hosting` (deployment approval flow)

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
- `models.py` defines separate TypedDicts for the balance projection (derived) and
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

Does the module manage a process that has named states
and moves through them based on rules and roles?
  → workflow

Does the module move money, tokens, or assets where
the history of movements is the source of truth?
  → transactional

Everything else
  → standard
```

When in doubt, start with `standard`. Promoting a module to a more specific type
later is straightforward — it means adding YAML files and tightening backend
conventions, not restructuring the Python layers.

---

## Factory Behaviour

The factory reads `type` from `module.yaml` and:

- selects the matching scaffold template for `backend/`
- adds the type-specific YAML files for `messaging` and `workflow`
- uses type-aware prompts for action and event generation

Structured output validation and CI enforcement per type are roadmap items.
For now, the type declaration is the contract — honour it in your implementation
even when the tooling does not yet enforce it.

---

## Cross References

- [canonical-app-structure.md](canonical-app-structure.md)
- [event-contracts.md](event-contracts.md)
- [app-bundle-declaratives.md](app-bundle-declaratives.md)
- [framework-capability-classification.md](framework-capability-classification.md)
