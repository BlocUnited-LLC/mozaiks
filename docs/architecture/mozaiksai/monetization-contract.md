# Monetization Contract

Mozaiks treats monetization as a first-class app capability, but OSS Mozaiks
does not implement payment processing. The OSS contract defines how an app
declares what is paid, what is gated, what usage is measured, and what runtime
state changes after a trusted billing fact is verified.

MozaiksPay is the default managed adapter when the `mozaikspay` capability pack
is available and the user has not explicitly requested another provider. That
default is still modular: generated apps receive app-owned facades and thin
MozaiksPay clients, while payment-provider mechanics remain outside the
generated bundle.

## Product Boundary

| Concern | OSS `mozaiks` owns | Hosted/product layer owns |
| --- | --- | --- |
| Revenue model taxonomy | `free`, `subscriptions`, `usage_based`, `transactional`, `marketplace`, `sponsored`, `donations`, `community_funded`, `hybrid` | Which models a hosted product enables, prices, bundles, or promotes |
| Subscription contract | `app/config/subscriptions.yaml`, plan capabilities, usage limits, token wallets, token allowances | Paid plan packaging, checkout enablement, subscriber lifecycle policy |
| Runtime enforcement | `EntitlementPort`, `ConfiguredEntitlementAdapter`, `actions[].entitlement_gate`, token guard | Which users receive paid assignments after commercial events |
| Token accounting | `TokenWalletLedger`, usage ingest, `INSUFFICIENT_TOKENS` recovery metadata | Payment confirmation, refunds, chargebacks, support policy |
| Fulfillment bridge | `BillingFulfillmentCommand` and deterministic effect application | Adapter verification, webhook validation, provider customer mapping |
| Managed adapter wiring | Build-context pack, generated facade module, generated integration client, env names | Hosted API implementation, API-key issuance, commercial policy, payouts, settlement |

The invariant is:

```text
commercial event verified outside OSS
-> provider-neutral fulfillment command
-> OSS subscription assignment and token effects
-> runtime entitlement and token enforcement
```

## Canonical Monetization Spine

A monetized generated app should fit this sequence:

1. The factory resolves `monetization_enabled` into one concrete
   `revenue_model`.
2. If the model needs paid access, quotas, credits, or token allowances,
   `SubscriptionContractDesigner` emits `app/config/subscriptions.yaml`.
3. AppGenerator adds plan-gated `entitlement_gate` values to module actions.
4. AppGenerator selects a managed provider pack when one is available. For SaaS
   billing and AI token top-ups, the default managed pack is `mozaikspay`.
5. Generated UI calls app-owned facade actions such as `billing_portal.*`.
6. The facade calls a thin integration client under
   `app/services/integrations/`.
7. The hosted adapter verifies payment or subscription facts.
8. The adapter submits a `BillingFulfillmentCommand`.
9. Runtime writes subscription assignment and token wallet effects.
10. AG2 usage middleware and module action dispatch enforce the result.

This keeps the generated app deterministic without forcing a single payment
provider into OSS.

## MozaiksPay Default, Not Provider SDK Default

OSS generator guidance should prefer MozaiksPay by name for supported managed
monetization surfaces:

- subscriptions
- billing portal
- subscription checkout
- runtime usage display
- token status
- token top-up checkout

The generated app should see MozaiksPay-branded configuration, such as:

```text
MOZAIKSPAY_API_BASE
MOZAIKSPAY_CLIENT_ID
MOZAIKSPAY_CLIENT_SECRET
MOZAIKSPAY_API_KEY
```

It should never see raw payment-provider imports, provider price IDs, checkout
secrets, webhook secrets, customer IDs, payout account IDs, or hosted internal
module paths.

When a user explicitly asks for another provider, OSS may scaffold a
provider-neutral `external_adapter` boundary. That boundary should produce
facade actions and adapter stubs, not a second OSS payment platform.

## What OSS Can Publish

Public OSS docs and prompts may describe:

- monetization taxonomy
- subscription and entitlement contracts
- token wallets and token allowances
- top-up product declarations
- fulfillment command shape
- managed-capability facade rules
- MozaiksPay as the default managed adapter
- how to swap to an explicitly selected external adapter

Public OSS docs and prompts must not describe:

- hosted commercial fee policy
- proprietary distribution or settlement policies
- campaign commercial policy
- payout operating rules
- provider-specific webhook mechanics
- provider customer mapping
- hosted API-key issuance internals
- hosted product plan pricing or margin strategy

Those details belong in the hosted product repo or in an operator's private app
workspace.

## Modular Adapter Rule

MozaiksPay is the preferred managed adapter because it gives generated apps a
ready billing and token-purchase path. It is not the only possible adapter.

Every payment or billing provider must cross the OSS boundary through the same
small set of contracts:

| Adapter responsibility | OSS boundary |
| --- | --- |
| Create hosted checkout or billing portal session | App-owned facade action and integration client |
| Confirm subscription activation | `BillingFulfillmentCommand(event_type="subscription_activated")` |
| Confirm plan change | `BillingFulfillmentCommand(event_type="subscription_updated")` |
| Confirm cancellation | `BillingFulfillmentCommand(event_type="subscription_cancelled")` |
| Confirm paid token top-up | `BillingFulfillmentCommand(event_type="token_top_up_paid")` |
| Apply manual/test credit | `BillingFulfillmentCommand(event_type="token_credit_granted")` |
| Apply refund or chargeback | `BillingFulfillmentCommand(event_type="refund_applied" | "chargeback_applied")` |

The runtime should not care whether the command came from MozaiksPay, an
enterprise invoice system, a custom provider adapter, or a local smoke test.
The adapter must verify the commercial fact before it submits the command.

## Revenue Models And Ownership

Use `app/config/subscriptions.yaml` only for access and usage contracts:

- recurring plans
- seats
- feature gates
- quotas
- prepaid credits
- token wallets
- token allowances

Do not use `subscriptions.yaml` to model:

- ordinary ecommerce orders
- marketplace commercial policy
- campaign backing terms
- advertising inventory
- community revenue settlement
- payouts
- refunds
- provider reconciliation

Those flows can still be modular Mozaiks apps, but their business state belongs
in app-owned or hosted-product modules. If they need payment collection, they
call a selected checkout facade. If payment confirmation should affect runtime
entitlements or token balances, they emit a fulfillment command.

## Self-Hosted Posture

Self-hosters can use Mozaiks in three ways:

1. Use the managed MozaiksPay pack and call the hosted MozaiksPay API.
2. Bring a different provider by implementing the same app-owned facade and
   fulfillment command boundary.
3. Use manual/test fulfillment for local or enterprise invoice workflows.

OSS should make all three routes possible, but MozaiksPay remains the default
managed route because it gives generated apps the least custom billing work.

## Related Architecture

- [Monetization Taxonomy](monetization-taxonomy.md)
- [Token Management](token-management.md)
- [Build Context Packs](../workflows/build-context-packs.md)
