# Monetization Taxonomy

Mozaiks uses `monetized` as an intent signal, not as a build route. A monetized
app must resolve to a concrete `revenue_model` before the factory decides which
modules, pages, provider packs, entitlement gates, or subscription contracts to
generate.

## Canonical Models

| `revenue_model` | Use when | Subscription contract |
| --- | --- | --- |
| `free` | No revenue, checkout, contribution, paid placement, or paid access flow. | Not required |
| `subscriptions` | Recurring access, seats, paid feature gates, premium reports, account-level quotas, credits, or token allowances. | Required |
| `usage_based` | Pay per AI token, API call, export, render, job, credit, or metered unit. | Conditional |
| `transactional` | Products, bookings, paid downloads, services, invoices, events, or one-time checkout. | Not required |
| `marketplace` | Multi-party buyer/seller, creator/customer, provider/client, commission, payout, dispute, or platform-fee flows. | Conditional |
| `sponsored` | Sponsored listings, boosted placement, ad slots, paid visibility, campaign budgets, or marketing packages. | Not required |
| `donations` | Tips, donations, pledges, optional contributions, or patron support. | Conditional |
| `community_funded` | Campaign-style backing, milestone funding, community contribution pools, or app/operator-specific funding policy. | Conditional |
| `hybrid` | More than one money flow, such as subscriptions plus ecommerce or marketplace fees plus seller tiers. | Conditional |

Aliased values are mapped by agents:

- `pay_per_use` -> `usage_based`
- `one_time_purchase` -> `transactional`

## Subscription Contract Rule

`app/config/subscriptions.yaml` exists only when the app needs the OSS
subscription/runtime entitlement primitives:

- recurring plans
- seats
- paid feature gates
- account-level quotas
- prepaid usage credits
- token wallets or token allowances
- usage budgets shown on billing/usage surfaces

Do not create `config/subscriptions.yaml` for ordinary ecommerce checkout,
marketplace orders, seller payouts, sponsored listings, ad campaigns, tips,
donations, or campaign backing unless the same app also sells access tiers,
quotas, credits, or token allowances.

## Factory Flow

1. `ValueEngine` may emit `concept_blueprint.monetization_intent` as an advisory
   hint.
2. `AppGenerator.AppPlanAgent` resolves the final `revenue_model` and optional
   `monetization_plan`.
3. `SubscriptionContractDesigner` independently decides whether
   `contract_required=true`.
4. If `contract_required=true`, AppGenerator emits exactly one
   `config/subscriptions.yaml` task and gates module actions with declared
   capability ids.
5. If `contract_required=false`, AppGenerator must not create
   `config/subscriptions.yaml`, billing modules, token wallets, or metering
   declarations unless the requested change explicitly adds that scope.

## Boundary

OSS Mozaiks owns the generic taxonomy, subscription contract shape,
entitlement gates, token usage primitives, token wallet primitives, and commerce
pack boundaries.

Hosted product policy remains app-owned or operator-owned. For example,
campaign funding terms, sponsored placement approval, payout rules, investment
review, settlement, regulatory handling, and marketplace promotion admin are not
generic OSS subscription primitives.
