# Subscriptions

`app/config/subscriptions.yaml` is the app-level catalog for paid access,
capability gates, token wallets, token allowances, top-ups, and pricing-page
groups.

Add this file when the app is a SaaS app or when module actions need
entitlement gates. Apps without paid access can omit it.

## How It Works

1. Plans grant capability ids.
2. Module actions declare an `entitlement_gate`.
3. At startup, Mozaiks loads `app/config/subscriptions.yaml`.
4. The runtime checks the current assignment store before dispatching gated
   module actions.

Payment providers, invoices, taxes, payouts, and settlement stay behind app or
managed-capability integrations. The subscriptions file defines the app's
provider-neutral access contract.

## Multi-Product Catalog

Use the product catalog shape when the app has more than one paid line, such as
platform access, AI usage, hosting, domains, or marketing placement.

```yaml
schema_version: mozaiks.subscriptions.v2
label: Mozaiks Platform
default_product_id: platform
products:
  - product_id: platform
    label: Platform
    default_plan_id: starter
    assignment_store:
      data_alias: billing.platform_subscriptions
    plans:
      - plan_id: starter
        label: Starter
        capabilities:
          - apps.create
          - apps.publish
      - plan_id: builder
        label: Builder
        capabilities:
          - apps.create
          - apps.publish
          - ai.chat

  - product_id: ai
    label: AI
    default_plan_id: included
    assignment_store:
      data_alias: billing.ai_subscriptions
    token_wallets:
      - wallet_id: ai_tokens
        label: AI token balance
        unit: tokens
        usage_meter_id: ai_tokens
        scope: user
        auto_debit_usage: true
    plans:
      - plan_id: included
        label: Included
        capabilities:
          - ai.chat
        token_allowances:
          - wallet_id: ai_tokens
            amount: 100000
            cadence: monthly
      - plan_id: ai_plus
        label: AI Plus
        capabilities:
          - ai.chat
          - ai.workflow.priority
        token_allowances:
          - wallet_id: ai_tokens
            amount: 1000000
            cadence: monthly

pricing_catalog:
  default_group_id: platform
  groups:
    - group_id: platform
      label: Platform
      kind: subscription
      plan_ids: [starter, builder]
    - group_id: ai
      label: AI
      kind: subscription
      plan_ids: [included, ai_plus]
```

## What Goes Here

| Concern | Field |
|---------|-------|
| Product line | `products[].product_id` |
| Plans | `products[].plans[]` |
| Capability gates | `products[].plans[].capabilities[]` |
| Current assignment store | `products[].assignment_store` |
| Usage caps | `products[].plans[].usage_limits[]` |
| Included token grants | `products[].plans[].token_allowances[]` |
| Token wallet metadata | `products[].token_wallets[]` |
| Top-up products | `products[].top_up_products[]` |
| Pricing-page grouping | `pricing_catalog.groups[]` or `products[].pricing_catalog_group` |

## Module Gate Example

```yaml
actions:
  - action_id: export_report
    handler_method: export_report
    entitlement_gate: reports.export
```

Any active plan that grants `reports.export` allows the action to run.

## Custom Money Rules

Subscription access belongs in `app/config/subscriptions.yaml`.

Module-owned commercial behavior belongs with the owning module. Examples:
marketplace placement rules, usage fees, campaign terms, payout policy, or
revenue-share display metadata can live in
`app/modules/{module_id}/contracts/commercial.yaml` when that module owns the
behavior.

## Read Next

- [Module Contracts](module-contracts.md)
- [Add a Module](../adding-modules/01-overview.md)
- [Monetization Contract](../../architecture/mozaiksai/monetization-contract.md)
