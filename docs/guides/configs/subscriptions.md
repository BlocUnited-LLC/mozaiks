# Subscriptions

`app/config/subscriptions.yaml` is the app-level catalog for paid access,
capability gates, token wallets, token allowances, top-ups, add-ons, and
pricing-page groups.

Add this file when the app is a SaaS app or when module actions need
entitlement gates. Apps without paid access can omit it.

`subscriptions.yaml` does not provision infrastructure by itself. If a paid
resource is managed by the host, the plan or add-on only grants the right to
request it; the actual provisioning decision belongs to the host module or a
module-owned commercial/service boundary.

## How It Works

1. Plans grant capability ids.
2. Module actions declare an `entitlement_gate`.
3. At startup, Mozaiks loads `app/config/subscriptions.yaml`.
4. The runtime checks the current assignment store before dispatching gated
   module actions.

Payment providers, invoices, taxes, payouts, and settlement stay behind app or
managed-capability integrations. The subscriptions file defines the app's
provider-neutral access contract.

For generated apps, SubscriptionContractDesigner selects plan features from the
injected approved pricing feature inventory. Module feature IDs have the form
`module.<module_id>.<action_id>` and identify approved `owned_mutations`,
code-constructed canonical `create_<entity>` / `update_<entity>` /
`delete_<entity>` writes, or declared `custom_reads`. Workflow feature IDs have
the form `workflow.<approved_surface_id>` and appear only when the approved
concept has an agentic capability. The model selects these IDs in
`subscription_config_file.plans[].included_features`; it does not invent
capability IDs or action gate mappings. The factory derives stable
`feature.<feature_id>` capability IDs (normalizing each ID segment to lowercase
snake case when needed), runtime `plans[].capabilities`, and
`module_contract_updates`, then applies the gates to `module.yaml` during task
admission, assembly, and repair. The persisted contract also records
`selected_features_by_plan` for downstream review. It derives
`workflow_contract_updates` for selected workflow features; AgentGenerator
resolves the concrete workflow name later. Generated add-ons may select
`required_feature` from the same inventory, which code translates to the
runtime `required_capability` field.

For example, when the approved `tasks` module has `create_task`, `update_task`,
`delete_task`, and a custom `summarize_tasks` read, a limited Free plan selects
the three core write features and sets a usage limit with
`feature_id: module.tasks.create_task`. Pro selects those features plus
`module.tasks.summarize_tasks`. Canonical `list_tasks` and `get_tasks` reads are
available to both plans without a gate. A paid view needs a declared custom
read; canonical collection list/get actions and managed-pack facade actions
are excluded from the feature inventory. Plan browsing, checkout, upgrades,
the billing portal, usage, and token access remain available to free users.

Selecting an unavailable feature produces feedback listing the valid inventory
and the option to remove it from the plan. If the product needs a missing
custom read or write, DesignDocs must approve it before subscription design
can use it. Generated YAML cannot override or remove the derived gate mapping.
Writing a gated manifest, including replacing an existing gated manifest with
an ungated repair, requires the approved subscription contract. Missing
approval fails closed; an explicit approved no-subscription decision can
remove old gates.

## Multi-Product Catalog

Manually authored apps can use the v2 product catalog shape when they have more
than one paid line, such as platform access, AI usage, hosting, domains, or
marketing placement. SubscriptionContractDesigner emits v1 with optional
`pricing_catalog` display groups.

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

add_on_products:
  - add_on_id: hero_weekly
    label: Hero Placement - Weekly
    kind: marketplace_placement
    billing_mode: one_time
    required_capability: marketplace.placements.order
    capability_groups: [marketplace.placements]
    duration_days: 7
    price:
      amount_cents: 9900
      currency: usd
      display: "$99/week"

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
    - group_id: marketing
      label: Marketing
      kind: add_on
      add_on_ids: [hero_weekly]
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
| Non-token add-ons | `add_on_products[]` |
| Pricing-page grouping | `pricing_catalog.groups[]` or `products[].pricing_catalog_group` |

## Module Gate Example

```yaml
actions:
  - action_id: summarize_tasks
    handler_method: summarize_tasks
    entitlement_gate: feature.module.tasks.summarize_tasks
```

The factory derives this gate from the selected `module.tasks.summarize_tasks`
feature. Any active plan that grants it allows the action to run.

## Custom Money Rules

Subscription access and provider-neutral add-on product definitions belong in
`app/config/subscriptions.yaml`.

Module-owned commercial behavior belongs with the owning module. Examples:
marketplace placement rules, usage fees, campaign terms, payout policy, or
revenue-share display metadata can live in
`app/modules/{module_id}/contracts/commercial.yaml` when that module owns the
behavior.

Use a module `service.yaml` or `commercial.yaml` when a capability needs
managed provisioning, BYOK fallback, or provider-specific fulfillment rules.
Keep the app-level subscription file focused on entitlement and catalog
presentation.

Use `add_on_products[]` for the app-level purchasable add-on catalog shown by
pricing and billing surfaces. Do not put provider product ids, provider price
ids, checkout sessions, order state, fulfillment state, inventory policy,
invoices, taxes, or settlement records in `subscriptions.yaml`.

## Read Next

- [Module Contracts](module-contracts.md)
- [Add a Module](../adding-modules/01-overview.md)
- [Monetization Contract](../../architecture/mozaiksai/monetization-contract.md)
