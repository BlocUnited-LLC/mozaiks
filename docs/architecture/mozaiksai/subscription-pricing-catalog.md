# Subscription Pricing Catalog

Mozaiks apps use `app/config/subscriptions.yaml` as the canonical subscription
and entitlement contract. The contract answers:

- which plans exist
- which plan is the default
- which capabilities each plan grants
- which usage limits and token allowances apply
- which provider-neutral usage charge policies apply
- where active subscription assignments are read from

Public pricing pages often need a different shape than enforcement. A hosted
product may sell platform access, token packs, hosting, domains, marketing, or
other service lines on one page. Those service lines should not become separate
entitlement files unless they are genuinely separate apps.

## Contract Shape

`mozaiks.subscriptions.v1` supports optional `pricing_catalog` display metadata:

```yaml
schema_version: mozaiks.subscriptions.v1
label: Example SaaS
default_plan_id: free

pricing_catalog:
  default_group_id: platform
  groups:
    - group_id: platform
      label: Platform
      description: Core app access and collaboration.
      kind: subscription
      plan_ids: [free, pro, enterprise]
      capability_groups: [platform, billing]
    - group_id: tokens
      label: AI Tokens
      description: Included managed AI usage by plan.
      kind: service
      plan_ids: [free, pro, enterprise]
      capability_groups: [ai_tokens]
    - group_id: marketing
      label: Marketing
      description: Optional marketplace promotion add-ons.
      kind: add_on
      add_on_ids: [hero_weekly, hero_monthly]
```

`pricing_catalog` is display metadata only. It does not grant access, create
prices, create payment-provider products, or replace `plans[].capabilities`.

Usage-based customer charge policy belongs beside the plan contract, not in the
provider pricing catalog:

```yaml
usage_charge_policies:
  - meter_id: ai_tokens
    label: AI usage
    source: runtime_llm_usage
    basis: provider_cost_usd
    markup_percent: 35
    rounding: cent
```

This policy estimates what the app charges its users from measured runtime
usage. It does not store provider price IDs, checkout URLs, invoices, tax, or
payment settlement state.

Provider-cost overrides are separate from subscriptions. If an operator has
negotiated OpenAI/Anthropic rates, internal model rates, or a temporary patch for
the generated provider catalog, they should use
`MOZAIKS_USAGE_PRICING_OVERRIDE_PATH`. Do not put provider-cost overrides in
`subscriptions.yaml`; keep `subscriptions.yaml` focused on customer-facing
plans, usage allowances, and markup policy.

## Rules

- Keep one canonical `subscriptions.yaml` for plan enforcement.
- Do not create `app/config/pricing.yaml` or hardcode plan data in page YAML or
  JSX when a subscription contract exists.
- Put customer-facing usage markup in `usage_charge_policies[]`.
- Keep provider model costs in the runtime usage-pricing catalog, refreshed from
  the generated provider reference plus local overrides.
- Use `pricing_catalog.groups[]` for pricing tabs or service selectors.
- Every `group.plan_ids[]` entry must reference a declared `plans[].plan_id`.
- Add-ons can be referenced by `add_on_ids[]`, but add-on pricing and checkout
  behavior remain app-owned or provider-pack-owned.
- Provider-specific price IDs, checkout URLs, invoices, settlement, and hosted
  product policy must not live in the OSS subscription enforcement schema.
- Generated pages should render groups when present and fall back to a single
  plan-card group when absent.

## Generation

`SubscriptionContractDesigner` owns the semantic plan decision. It reads upstream
context from `concept_overview`, `concept_blueprint`, `backend_design_document`,
`design_surface_map`, `experience_spec`, `monetization_enabled`, and
`builder_options`, then emits:

- `subscription_config_file`: the canonical `app/config/subscriptions.yaml`
  payload
- `pricing_catalog`: optional display groups inside that same payload
- `usage_charge_policies`: optional app-level usage markup or fixed token
  pricing policy inside that same payload
- `plan_design_rationale`: traceable reasons that map upstream signals to plan,
  entitlement, quota, and pricing group decisions

`AppGenerator` and `AgentGenerator` consume the saved contract. They may choose
different UI primitive variants for the pricing surface, but the data source
must remain the generated app billing module's `get_plans` action reading
`config/subscriptions.yaml`.

## Boundary

OSS owns:

- loading and validating `subscriptions.yaml`
- provider-neutral entitlements, usage limits, and token allowances
- provider-neutral customer usage charge estimate policy
- provider-neutral pricing catalog display grouping
- generic UI primitives that can render grouped plan/add-on cards

Apps and hosted products own:

- commercial copy and exact prices
- payment provider adapters and price identifiers
- checkout, portal, invoice, refund, and settlement actions
- service-specific policies such as marketplace placements or managed hosting
