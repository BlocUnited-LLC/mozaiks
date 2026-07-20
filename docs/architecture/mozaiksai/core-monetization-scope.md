# Core Monetization Scope

Mozaiks uses `monetized` as an intent signal, not as a build route. The OSS
factory keeps first-class monetization intentionally small: subscription access,
token/usage billing, and provider-neutral fulfillment. Other money flows remain
modular app-owned or hosted-product behavior.

## Core Routes

| `revenue_model` | Use when | Subscription contract |
| --- | --- | --- |
| `free` | No paid access, usage billing, token billing, checkout, or custom money flow is requested. | Not required |
| `subscriptions` | Recurring access, seats, paid feature gates, premium reports, account-level quotas, credits, token wallets, or token allowances. | Required |
| `usage_based` | Pay per AI token, API call, export, render, job, credit, or metered unit. | Conditional |
| `custom` | Ecommerce, bookings, one-time checkout, marketplace, sponsorship, contributions, campaign funding, revenue-share, payout policy, or any other app/operator-specific money flow. | Not required unless the same app also sells access tiers, quotas, credits, token wallets, or token allowances |
| `hybrid` | A first-class subscription/usage route plus a custom money-flow route. | Conditional |

Aliased values are mapped by agents:

- `pay_per_use` -> `usage_based`
- `one_time_purchase` -> `custom`

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

Do not create `config/subscriptions.yaml` for custom money flows unless the same
app also sells access tiers, quotas, credits, token wallets, or token
allowances. Custom flows may declare app-owned modules, policy hooks, managed
facades, or external adapters, but they do not expand the OSS subscription
runtime.

## Factory Flow

1. `ValueEngine` may emit `concept_blueprint.monetization_intent` as an advisory
   hint.
2. `AppGenerator.AppPlanAgent` resolves the final core `revenue_model` and optional
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

OSS Mozaiks owns the core routes, subscription contract shape, entitlement
gates, token usage primitives, token wallet primitives, fulfillment command
shape, and generated facade rules.

Hosted product policy remains app-owned or operator-owned. For example, campaign
terms, paid-placement approval, payout rules, investment review, settlement,
regulatory handling, and marketplace promotion admin are not generic OSS
subscription primitives.

See [Monetization Contract](monetization-contract.md) for the durable boundary:
MozaiksPay is the default managed adapter for supported generated-app
monetization surfaces, while provider mechanics, hosted commercial policy,
payouts, campaign terms, and proprietary revenue distribution stay outside OSS.
