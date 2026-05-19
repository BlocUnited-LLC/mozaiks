# Capability Pack Model

This document defines the simplest useful model for optional reusable product
capabilities in Mozaiks.

The goal is to keep the runtime and shared builder clean while still allowing:

- strong greenfield foundations for generated apps
- optional first-party feature packs such as notifications or messaging
- private hosted integrations such as generic hosted analytics

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
search, notifications, or hosted analytics.

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

- generic hosted analytics
- managed search integration
- external notification delivery integration

These packs should live in the private hosted product repo, not in the public
framework repo.

Recommended root in a private hosted product workspace:

```text
capability_packs/licensed/
```

The private hosted service logic should live separately under product service
roots such as:

```text
services/hosted_analytics/
services/managed_search/
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

Examples: `generic_hosted_analytics`, `managed_search`.

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

Examples: generic external webhook receiver, notification bridge, search index sync adapter.

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
- `audit`
- `search`
- `generic_hosted_analytics`

### `source_pack_id`

This is provenance metadata.

Example:

- the Analytics page exists because the `generic_hosted_analytics` pack added it

If this field creates more confusion than value during the first implementation,
it can remain internal planning metadata.

### `event_flows`

This is not an AI concept. It is just the map from domain facts to reactions.

Example:

- `domain.audit.record_created`
- update an activity summary
- notify the user
- refresh an audit view
- optionally trigger a workflow

The important rule is: the app backend emits domain facts, not workflow names.

## Recommended Pack Manifest Shape

This is the recommended v1 manifest shape for both public and private packs.

```yaml
schema_version: mozaiks.capability_pack.v1
capability_pack_id: notifications
label: Notifications
summary: In-app notification records, delivery preferences, and event-driven delivery hooks.
visibility: public

pack_type: notification_pack
implementation_mode: declarative_module
delivery_mode: app_embedded

requires:
  licensed_services: []
  connectors: []

contributes:
  modules:
    - notifications
    - notification_preferences
  pages:
    - notifications
  admin_sections: []
  workflows: []
  events:
    - domain.notifications.notification_created
    - domain.notifications.notification_read

hard_constraints:
  - Keep handler.py thin.
  - Publish only declared domain events.
  - Use persisted notification records for delivery and read-state semantics.
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

Hosted and external packs may also provide build-time metadata:

| Field | Meaning |
| --- | --- |
| `capability_source` | Ownership source such as `framework_pack`, `hosted_pack`, `generated_module`, or `external_adapter` |
| `surfaces` | Named UI/API surfaces the pack may enable, with status and route hints |
| `supported_domains` | Provider-neutral app domains where the pack is relevant |
| `branding` | Optional display metadata, never implementation logic |
| `generation_rules` | Builder constraints that agents must follow when composing the app |
| `supersedes` | Pack ids this pack replaces when both are available |
| `adapter_template` | Optional backend integration template copied into `backend/integrations/` |

### Hosted-pack facade path

Hosted packs must be consumed through generated app-owned facades:

```text
hosted_pack
  -> backend/integrations/{pack_id}_client.py
  -> app-owned facade module
  -> ui/pages bind to the facade module
```

Provider-neutral example:

```text
hosted_analytics
  -> backend/integrations/hosted_analytics_client.py
  -> modules/analytics_dashboard/
  -> ui/pages/analytics.yaml
  -> /api/modules/analytics_dashboard/get_metrics
```

Pages must not call hosted pack internals directly. They call the generated
facade module, and the facade module calls the integration client.

## Public Pack Example In This Repo

Recommended location:

```text
factory_app/capability_packs/public/notifications/
```

Recommended shape:

```text
factory_app/capability_packs/public/notifications/
├── manifest.yaml
├── app_overlay/
│   ├── modules/
│   │   ├── notifications/
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
│   │   └── notification_preferences/
│   │       └── ...
│   ├── ui/
│   │   ├── pages/
│   │   │   └── notifications.yaml
│   │   └── components/
│   │       └── NotificationList.jsx
│   └── workflows/
│       └── NotificationDigest/
│           └── ...
└── tests/
    └── test_notifications_pack.py
```

Notes:

- `app_overlay/` is not a full standalone app. It is a promotable fragment that maps onto the canonical app root.
- A public pack may include workflows, but only when the feature really needs AI behavior.
- Notifications are primarily deterministic module logic, not a workflow-first feature.

## Hosted Pack Example Outside OSS

Recommended locations:

```text
capability_packs/licensed/generic_hosted_analytics/
services/hosted_analytics/
```

Recommended shape:

```text
hosted-product-workspace/
├── capability_packs/
│   └── licensed/
│       └── generic_hosted_analytics/
│           ├── manifest.yaml
│           ├── app_overlay/
│           │   ├── modules/
│           │   │   ├── analytics_dashboard/
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
│           │   │   │       ├── repo.py
│           │   │   │       ├── policy.py
│           │   │   │       ├── schemas.py
│           │   │   │       └── analytics_client.py
│           │   └── ui/
│           │       └── pages/
│           │           └── analytics.yaml
│           ├── build_extensions/
│           │   └── inject_hosted_analytics_context.py
│           └── tests/
│               └── test_hosted_analytics_pack.py
└── services/
    └── hosted_analytics/
        ├── api/
        ├── aggregation/
        ├── reports/
        └── callbacks/
```

Notes:

- The integration pack lives with the builder-facing hosted product assets.
- The real hosted analytics engine lives in the private service layer.
- Generated apps receive app-side facade modules, pages, and admin wiring.
- Generated apps do **not** receive the private hosted service engine source code.

## Hosted Operator Surfaces Are Optional

A shell or admin system may know how to place optional operator surfaces, but
that does **not** mean every app carries implementation logic for those surfaces.

Recommended rule:

- a capability pack decides whether a hosted operator surface is populated
- public packs fill public surfaces with embedded app logic
- hosted packs fill hosted surfaces through facade modules and external adapters
- standard generated app bundles must not include hosted-only operator surfaces unless the host context explicitly provides the pack

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
  - hosted_analytics
```

With that input:

- `notifications` is allowed because it is public
- `generic_hosted_analytics` is allowed because `hosted_analytics` is licensed
- the real hosted analytics engine still remains private

## What The Agents Should Generate Versus Reuse

### Build once and reuse

- notifications
- settings
- files/media
- audit/activity
- search

### Generate per app

- app-specific domain modules
- app-specific workflows
- page composition
- workflow touchpoints
- app-specific integration wiring

### Keep private and integrate

- hosted analytics engines
- managed search engines
- external provider services

## Naming Clarity — reactions.yaml vs External Event Subscriptions

Keep event reactions and external event subscriptions separate:

**`contracts/reactions.yaml`**

An optional module contract file. It declares which domain events a module
reacts to. It does not declare persistent external connections or hosted
service subscriptions.

```yaml
# modules/audit/contracts/reactions.yaml
reactions:
  - id: audit.on_notification_created
    event_type: domain.notifications.notification_created
    target:
      kind: handler
      handler_method: record_notification_event
```

**External event subscription**

An external event subscription is runtime integration behavior. Model it through
a module-owned `runtime_extensions.yaml` startup service or API router only when
normal module actions are insufficient.

Example:

```yaml
# modules/audit/runtime_extensions.yaml
schema_version: mozaiks.runtime_extensions.v1
extensions:
  - kind: startup_service
    entrypoint: backend.audit_subscriber:AuditSubscriber
```

`contracts/reactions.yaml` routes already-committed domain events between
modules. `runtime_extensions.yaml` connects a module to host startup behavior.

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
