# Module Contracts

Module contracts live under `app/modules/{module_id}/contracts/`. They describe
the companion behavior for one deterministic module.

Only `module.yaml` and `backend/handler.py` are required. Add companion
contracts when the module needs that surface.

## Contract Files

| File | Use it for |
|------|------------|
| `events.yaml` | Domain events this module may publish. |
| `reactions.yaml` | Event reactions owned by this module. |
| `notifications.yaml` | Notification rules derived from events. |
| `settings.yaml` | User or app settings schema for this module. |
| `admin.yaml` | Module admin panels mounted inside `/admin`. |
| `profile.yaml` | User profile panels contributed by this module. |
| `relationships.yaml` | Cross-module relationship metadata. |
| `policy_hooks.yaml` | Declared policy hook entry points. |
| `service.yaml` | Stable service boundary metadata for generated apps, operators, external clients, or other modules. |
| `commercial.yaml` | Module-owned commercial metadata such as fees, placement terms, payout rules, or service terms. |

## Ownership Rules

- `module.yaml` declares actions, permissions, capabilities, and entitlement
  gates.
- `contracts/events.yaml` declares events the module publishes.
- `contracts/reactions.yaml` declares how the module responds to events.
- `contracts/commercial.yaml` is for module-specific commercial behavior, not
  app-wide SaaS plan catalogs.
- App-wide products, plans, usage limits, and capability grants live in
  `app/config/subscriptions.yaml`.
- If a module owns managed infrastructure or BYOK fulfillment, model the
  entitlement in `app/config/subscriptions.yaml` and the provisioning rules in
  the module's `service.yaml` or `commercial.yaml`. Do not use subscriptions
  as a hidden infrastructure provisioning layer.
- Runtime extensions live at `app/modules/{module_id}/runtime_extensions.yaml`.

## Starting workflows from events

Each app chooses its own event names and workflow capability IDs. For example,
an order-review action can emit `domain.orders.review_requested`. Its module
declares the event in `contracts/events.yaml` and lists it in the action's
`emits`. A subscribing module declares the following reaction.

```yaml
reactions:
  - id: start_order_review
    event_type: domain.orders.review_requested
    target: {kind: capability, capability_id: orders.review}
```

The workflow named by the module's `kind: workflow` capability declares how
it starts in `workflows/OrderReview/orchestrator.yaml`.

```yaml
triggers:
  - type: event
    event: domain.orders.review_requested
    capability_id: orders.review
```

DesignDocs captures the intended event. AgentGenerator writes the workflow
trigger. AppGenerator uses each generated workflow's event and capability to
plan the emitting action and write the module contracts. Two workflows may
subscribe to the same event through distinct capabilities. AppGenerator's
acceptance gate checks each generated pair before export.

For a `platform.*` or `hosted.*` event, the external publisher and its
delivery into this app belong to the host integration. The generic app build
can check the receiver. Its current contract carries no publisher-delivery
evidence, so acceptance reports an unverified external producer and blocks
export even if a generated module lists the event in `emits`.

At runtime the platform resolves the reaction with the event type and capability
ID. A unique matching declaration can open a workflow session with the source
event and payload in its declared context. A separate, explicitly unqualified
event trigger can supply a default in hand-authored workflows. Equally specific
matches leave the capability unresolved.

## Notification Target Scope

Notification rules default to `audience.scope: event`, retaining their source
event's tenant/workspace ownership. An approved cross-workspace personal notice
can declare an app target after its owning service validates every recipient:

```yaml
schema_version: mozaiks.notifications.v1
notifications:
  - id: task_ready
    event_type: domain.tasks.ready
    audience: {scope: app, user_id_field: recipient_id}
    source_event: reference
    template: {title: Task ready, body: Open the task to read it.}
    context_fields: [task_id]
```

App scope requires that direct field and rejects roles, permissions, resolver
and broadcast audiences. Missing or malformed recipients create no record.
It does not change the source event or permit choosing a different app.
`source_event: reference` independently keeps only the event id/type/optional
related type and sets the notification actor to null. Explicit templates and
context still retain the values they select; old records are not rewritten.
See [Event System](../../architecture/foundations/events-and-data/event-system.md)
for ownership validation and default support notification isolation.

## Service Boundary Example

```yaml
schema_version: mozaiks.module.service.v1
service_id: reports
label: Reports
provides:
  - capability_id: reports.export
    label: Export reports
consumes:
  - capability_id: ai.chat
```

## Commercial Metadata Example

```yaml
schema_version: mozaiks.module.commercial.v1
commercial_id: marketplace_placement
label: Marketplace placement
terms:
  - term_id: featured_listing
    label: Featured listing
    basis: placement
    unit: week
```

Keep these contracts names-first and metadata-first. Provider credentials,
payment processors, settlement logic, and durable lifecycle state belong behind
the owning module, integration, or managed capability.

## Read Next

- [Subscriptions](subscriptions.md)
- [Add a Module](../adding-modules/01-overview.md)
- [Module System](../../architecture/modules-systems/module-system.md)
