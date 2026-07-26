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
- Runtime extensions live at `app/modules/{module_id}/runtime_extensions.yaml`.

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
