# Capability Pack Model

This document defines the simplest useful model for optional reusable product
capabilities in Mozaiks.

The goal is to keep the runtime and shared builder clean while still allowing:

- strong greenfield foundations for generated apps
- optional first-party feature packs such as notifications or messaging
- private hosted integrations such as MozaiksPay

## Core Decision

Mozaiks should treat reusable optional product features as **capability packs**.

A capability pack is a packaged feature family that may contribute:

- deterministic modules
- page or admin UI
- optional workflows
- event contracts
- integration wiring
- build-time constraints

The capability pack is the reusable feature unit.

The runtime is **not** the place to store app-specific business features such as
payments, messaging, or investor distributions.

## V1 Simplicity Rule

Do not start with both `builder_profile` and `capability_pack`.

For v1, use only:

- selected `capability_packs`
- small build-time policy inputs such as `host_mode` and `licensed_services`

That is enough to answer:

- which optional features the app wants
- whether licensed/private packs are allowed for this build

If host policy becomes more complex later, Mozaiks may add a richer
`builder_profile` contract. It is not required for the first implementation.

## The Four Ownership Zones

### 1. Runtime and platform substrate

Owns generic framework behavior:

- execution runtime
- transport
- sessions
- module hosting
- workflow execution
- app shell and admin shell primitives

Examples:

- `mozaiksai/`
- `chat-ui/`
- `mozaiksai.hosts.platform`

This layer must not own product-specific packs.

### 2. Public framework capability packs

Owns reusable optional capabilities the framework wants to ship to many apps.

Examples:

- notifications
- settings
- files/media
- audit/activity
- messaging/community
- entitlements (SaaS plan/tier management, feature gates, trial lifecycle)

These packs should live in this repo because they are part of the public Mozaiks
value proposition.

Recommended root:

```text
factory_app/capability_packs/public/
```

### 3. Private hosted product capability packs

Owns licensed or proprietary integrations that depend on private hosted product
services.

Examples:

- payments integration
- investor distribution integration
- marketplace settlement integration

These packs should live in the private hosted product repo, not in the public
framework repo.

Recommended root in `mozaiks-app`:

```text
capability_packs/licensed/
```

The private hosted service logic should live separately under product service
roots such as:

```text
services/mozaikspay/
services/investor_distribution/
```

### 4. Generated app-specific output

Owns the app-specific composition for one generated app.

Examples:

- selected capability pack overlays promoted into the app workspace
- app-specific workflows
- app-specific pages
- app-specific domain modules

Generated output consumes packs. It should not become the canonical owner of a
shared reusable capability.

## Capability Ownership Classification

Every module or feature capability belongs to one of five ownership classes.
This determines who generates it, who consumes it, and whether OSS apps may include it.

| Class | Owner | Generation | OSS apps |
|-------|-------|------------|----------|
| `host_universal` | Runtime/Platform | Never generate — always present | Yes, automatic |
| `framework_pack` | Mozaiks framework | Select from pack catalog — don't regenerate | Yes, opt-in |
| `hosted_pack` | Mozaiks App (proprietary) | Not generated — licensed integration only | No |
| `generated_module` | App-specific | AppGenerator generates contracts + stubs | Yes, per app |
| `external_adapter` | External service | AppGenerator generates wiring + facade only | Adapter yes; engine no |

### `host_universal`

Built into the runtime or platform. Every app gets it automatically.

Examples: WebSocket transport, event dispatch, session management, AG2 orchestration,
admin shell, notification storage, user identity.

**Rule:** Never generate these. If a build plan includes auth, websocket, notification
infrastructure, or user management as a module to build — the plan is wrong.

### `framework_pack`

Optional reusable packs published by the Mozaiks framework. Apps select them from
the catalog; AppGenerator does not regenerate pack internals.

Examples: `notifications`, `messaging`, `files`, `audit`.

**Rule:** Reference the pack; expand only the app-specific overlay (wiring, page
composition, event flow declarations).

### `hosted_pack`

Licensed packs that depend on private Mozaiks App hosted services.
OSS apps must not include these.

Examples: `payments_integration`, `investor_distribution_integration`.

**Rule:** Generate the integration facade and wiring; the hosted service engine
lives in the private product repo.

### `generated_module`

App-specific deterministic business logic. AppGenerator generates the full module
contract and backend stubs.

Examples: `orders`, `inventory`, `profiles`, `campaigns`.

**Rule:** Generate `module.yaml`, `contracts/events.yaml`, `contracts/reactions.yaml`,
`contracts/notifications.yaml`, `backend/handler.py`, `backend/service.py`,
`backend/repo.py`, `backend/policy.py`, `backend/schemas.py`.

### `external_adapter`

A facade to an outside system. Generate the integration wiring only — not the
external system itself.

Examples: Stripe webhook receiver, Slack notification bridge, C# settlement adapter.

**Rule:** Generate the facade and event bridge. Use `runtime_extensions.yaml api_router`
for inbound webhooks. The real system lives outside Mozaiks.

---

## What A Capability Pack Actually Means

Plain English:

- `capability_pack_id` means “which optional feature family is this?”
- `source_pack_id` means “which pack most directly caused this surface to exist?”
- `event_flows` means “which committed business facts should trigger downstream reactions?”

Only the first term is essential to the v1 mental model.

### `capability_pack_id`

This is the real unit you should think about.

Examples:

- `notifications`
- `messaging`
- `payments_integration`
- `investor_distribution_integration`

### `source_pack_id`

This is provenance metadata.

Example:

- the Billing page exists because the `payments_integration` pack added it

If this field creates more confusion than value during the first implementation,
it can remain internal planning metadata.

### `event_flows`

This is not an AI concept. It is just the map from domain facts to reactions.

Example:

- `domain.payments.payment_succeeded`
- update subscription state
- notify the user
- refresh a billing view
- optionally trigger a workflow

The important rule is: the app backend emits domain facts, not workflow names.

## Recommended Pack Manifest Shape

This is the recommended v1 manifest shape for both public and private packs.

```yaml
schema_version: mozaiks.capability_pack.v1
capability_pack_id: messaging
label: Messaging
summary: Direct messages, threads, channels, read state, and notification hooks.
visibility: public

pack_type: messaging_pack
implementation_mode: declarative_module
delivery_mode: app_embedded

requires:
  licensed_services: []
  connectors: []

contributes:
  modules:
    - threads
    - messages
    - announcements
  pages:
    - messages
  admin_sections: []
  workflows: []
  events:
    - domain.messaging.thread_created
    - domain.messaging.message_sent

hard_constraints:
  - Keep handler.py thin.
  - Publish only declared domain events.
  - Use thread/member read-state records for delivery semantics.
```

Recommended field meanings:

| Field | Meaning |
| --- | --- |
| `capability_pack_id` | Stable feature family id |
| `visibility` | `public`, `licensed`, or `private` |
| `pack_type` | Family taxonomy already used by AppGenerator planning |
| `implementation_mode` | `declarative_module`, `agentic_workflow`, `hybrid`, or `external_integration` |
| `delivery_mode` | Whether the pack embeds app logic locally or integrates to a hosted/private service |
| `requires.licensed_services` | Which private product services must be licensed for this pack to be legal |
| `contributes.*` | What the pack adds to an app workspace |
| `hard_constraints` | Rules the builder and downstream agents must not violate |

## Public Pack Example In This Repo

Recommended location:

```text
factory_app/capability_packs/public/messaging/
```

Recommended shape:

```text
factory_app/capability_packs/public/messaging/
├── manifest.yaml
├── app_overlay/
│   ├── modules/
│   │   ├── threads/
│   │   │   ├── module.yaml
│   │   │   ├── contracts/
│   │   │   │   ├── events.yaml
│   │   │   │   ├── reactions.yaml
│   │   │   │   ├── notifications.yaml
│   │   │   │   ├── settings.yaml
│   │   │   │   └── admin.yaml
│   │   │   └── backend/
│   │   │       ├── handler.py
│   │   │       ├── service.py
│   │   │       ├── repo.py
│   │   │       ├── policy.py
│   │   │       └── schemas.py
│   │   └── messages/
│   │       └── ...
│   ├── ui/
│   │   ├── pages/
│   │   │   └── messages.yaml
│   │   └── components/
│   │       └── MessageComposer.jsx
│   └── workflows/
│       └── MessageModeration/
│           └── ...
└── tests/
    └── test_messaging_pack.py
```

Notes:

- `app_overlay/` is not a full standalone app. It is a promotable fragment that maps onto the canonical app root.
- A public pack may include workflows, but only when the feature really needs AI behavior.
- Messaging itself is primarily deterministic module logic, not a workflow-first feature.

## Private Pack Example In `mozaiks-app`

Recommended locations:

```text
capability_packs/licensed/payments_integration/
services/mozaikspay/
```

Recommended shape:

```text
mozaiks-app/
├── capability_packs/
│   └── licensed/
│       └── payments_integration/
│           ├── manifest.yaml
│           ├── app_overlay/
│           │   ├── modules/
│           │   │   ├── payments/
│           │   │   │   ├── module.yaml
│           │   │   │   ├── contracts/
│           │   │   │   │   ├── events.yaml
│           │   │   │   │   ├── reactions.yaml
│           │   │   │   │   ├── notifications.yaml
│           │   │   │   │   ├── settings.yaml
│           │   │   │   │   └── admin.yaml
│           │   │   │   └── backend/
│           │   │   │       ├── handler.py
│           │   │   │       ├── service.py
│           │   │   │       └── client.py
│           │   └── ui/
│           │       └── pages/
│           │           └── billing.yaml
│           ├── build_extensions/
│           │   └── inject_payments_context.py
│           └── tests/
│               └── test_payments_integration_pack.py
└── services/
    └── mozaikspay/
        ├── api/
        ├── ledger/
        ├── payouts/
        ├── settlement/
        └── webhooks/
```

Notes:

- The **integration pack** lives with the builder-facing hosted product assets.
- The real payment engine lives in the private service layer.
- Generated apps receive app-side integrations, pages, modules, and admin wiring.
- Generated apps do **not** receive the private payout or settlement engine source code.

## Billing Should Be First-Class Optional, Not Mandatory

Billing is a valid first-class admin section in the shell.

That does **not** mean every app must carry billing logic.

Recommended rule:

- the admin shell may know what a Billing section is
- a capability pack decides whether Billing is populated for a given app
- a public subscription pack may fill basic billing/usage surfaces
- a private payments integration pack may fill Billing with proprietary hosted integrations

## Recommended Build Path

For v1, keep the build path simple:

1. The host or control plane supplies:
   - `host_mode`
   - `licensed_services`
2. App planning selects `capability_packs`.
3. Validation rejects any selected pack whose `requires.licensed_services` are unavailable.
4. The builder expands each selected pack into known build tasks and app overlay files.
5. Generated app-specific logic is layered on top only where the pack intentionally leaves room for customization.

Example policy input:

```yaml
host_mode: oss
licensed_services:
  - mozaikspay
```

With that input:

- `messaging` is allowed because it is public
- `payments_integration` is allowed because `mozaikspay` is licensed
- the real `MozaiksPay` ledger still remains private

## What The Agents Should Generate Versus Reuse

### Build once and reuse

- notifications
- settings
- files/media
- audit/activity
- messaging/community
- public subscription and billing shell behavior

### Generate per app

- app-specific domain modules
- app-specific workflows
- page composition
- workflow touchpoints
- app-specific integration wiring

### Keep private and integrate

- payment rails
- settlement
- payouts
- investor distributions
- campaign revenue allocation

## Naming Clarity — reactions.yaml vs SaaS Subscriptions

Keep event reactions and SaaS subscriptions separate:

**`contracts/reactions.yaml`**

An optional module contract file. It declares which domain events a module
reacts to. It has nothing to do with billing, plans, or SaaS subscriptions.

```yaml
# modules/orders/contracts/reactions.yaml
reactions:
  - id: orders.on_payment_succeeded
    event_type: hosted.billing.payment_succeeded
    target:
      kind: handler
      handler_method: handle_payment_succeeded
```

**`entitlements` capability pack**

A framework pack for SaaS plan/tier management and feature access control.
Owns the plan catalog, user plan assignment, trial lifecycle, and feature gates.
Connects to hosted payment capabilities via domain events.

An app selects this pack when it needs to gate features by plan, manage
free/paid tiers, or run trials. SaaS subscriptions are modeled by billing or
subscription modules plus entitlements, not by event reactions.

```yaml
# app/app.json (simplified)
capability_packs:
  - entitlements
  - payments_integration
```

An entitlements module may use `contracts/reactions.yaml` internally to react to
`hosted.billing.payment_succeeded` and upgrade plan state. That is event
reaction routing, not the SaaS subscription model itself.

---

## Decision Test

Ask these questions in order.

### Does the feature make sense with AI turned off?

If yes, it probably starts as a deterministic capability pack.

### Do many apps need it?

If yes, it probably belongs in a reusable public or private pack instead of being regenerated from scratch every time.

### Is it financially sensitive or proprietary?

If yes, it probably belongs in a private hosted service with a thin integration pack.

### Does it mainly add reasoning, review, or orchestration?

If yes, it may need a workflow component in addition to any deterministic pack.

## Cross References

- [framework-capability-classification.md](framework-capability-classification.md)
- [../foundations/core-product-app-bundle-boundary.md](../foundations/core-product-app-bundle-boundary.md)
- [../foundations/distribution-and-workspace-model.md](../foundations/distribution-and-workspace-model.md)
- [../app/app-bundle-declaratives.md](../app/app-bundle-declaratives.md)
