# Subscriptions

`app/config/subscriptions.yaml` is the canonical generated-app subscription
contract. Add it only when the app sells access tiers, quotas, credits, token
packs, token allowances, or paid feature gates.

Non-SaaS apps omit this file. When it is absent, the runtime uses the no-op
entitlement adapter and module entitlement gates pass.

## What It Owns

Use `app/config/subscriptions.yaml` for:

- plan ids and labels
- the default plan
- capability ids granted by each plan
- usage limits such as monthly AI token limits
- provider-neutral token wallets
- plan token allowances
- top-up products and usage charge policy when the app sells token packs or
  usage credits
- `assignment_store` metadata when active subscription assignments are stored
  in app data

Do not put provider credentials, checkout session code, payment processor SDK
state, invoices, settlement records, marketplace fees, or hosted operator
policy in this file.

## Minimal Access Plan

```yaml
schema_version: mozaiks.subscriptions.v1
label: Support Desk Plans
default_plan_id: free
plans:
  - plan_id: free
    label: Free
    capabilities: []
  - plan_id: pro
    label: Pro
    capabilities:
      - tickets.export
      - analytics.dashboard
```

Module actions can reference these capability ids with `entitlement_gate` in
`app/modules/{module_id}/module.yaml`.

## Assignment Store

Use `assignment_store` when the app stores active subscription assignments in
app data and the runtime should enforce those assignments.

```yaml
schema_version: mozaiks.subscriptions.v1
label: Support Desk Plans
default_plan_id: free
assignment_store:
  data_alias: billing.subscriptions
  user_id_field: user_id
  active_statuses: [active, trialing]
plans:
  - plan_id: free
    label: Free
    capabilities: []
  - plan_id: pro
    label: Pro
    capabilities: [tickets.export]
```

If a generated app declares `assignment_store`, it also needs a deterministic
write path for assignment records. That write path can be a selected managed
capability pack that declares `subscription_write_path`, or the OSS
`entitlement_dispatch` module archetype.

## Token Wallets

Use token wallets only when the app sells AI usage, credits, quotas, token
packs, or token allowances.

```yaml
schema_version: mozaiks.subscriptions.v1
label: AI Support Plans
default_plan_id: pro
token_wallets:
  - wallet_id: ai_tokens
    label: AI token balance
    unit: tokens
    usage_meter_id: ai_tokens
    scope: user
    auto_debit_usage: true
plans:
  - plan_id: pro
    label: Pro
    capabilities: [ai.chat]
    usage_limits:
      - meter_id: ai_tokens
        label: AI tokens
        unit: tokens
        monthly_limit: 100000
        capability_id: ai.chat
    token_allowances:
      - wallet_id: ai_tokens
        amount: 100000
        cadence: monthly
```

Token usage is measured by the runtime. Generated modules should not create a
second usage ledger.

## Commercial Metadata Boundary

`app/modules/{module_id}/contracts/commercial.yaml` is optional module-owned
metadata for custom money-flow terms outside app subscription gates. Use it for
things like fee display, service terms, marketplace placement metadata, or
module-owned commercial policy.

That file does not grant entitlements, write subscription assignments, process
payments, or replace `app/config/subscriptions.yaml`.

## Not Canonical

Do not author `app/modules/{module_id}/contracts/subscriptions.yaml`.

Do not use root `hosted_services.yaml` or `monetization.yaml` files as the
source of truth for generated app plans. Hosted products may derive operator
summaries, but generated app subscription logic belongs in
`app/config/subscriptions.yaml`.

See also [Entitlement Dispatch Archetype](../entitlement/entitlement-dispatch-archetype.md)
and [Core Monetization Scope](../../architecture/mozaiksai/core-monetization-scope.md).
