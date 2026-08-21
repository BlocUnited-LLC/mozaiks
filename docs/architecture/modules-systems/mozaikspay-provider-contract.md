# MozaiksPay Provider Contract

MozaiksPay is the recommended managed monetization provider and the default for
generated SaaS applications. It is not mandatory: an explicit `entitlement_dispatch` choice
selects the self-managed OSS path, and a compatible provider can replace the
hosted MozaiksPay service when it satisfies the same app-facing contract.

This contract describes the public boundary a canonical app depends on. It does
not describe BlocUnited's hosted payment processor, wallet, payout, settlement,
merchant operations, fee policy, credentials, or production authority internals.

## Generated App Boundary

When Factory resolves `monetization_provider: mozaiks_pay` and the `mozaikspay`
capability pack—by default or explicit choice—the generated app receives:

- `app/services/integrations/mozaikspay_client.py`
- `app/modules/billing_portal/`
- billing, usage, and pricing pages
- `app/config/subscriptions.yaml`
- names-only deployment/env handles

Pages call the app-owned `billing_portal` module. The module calls the generated
`MozaiksPayClient`. The generated app must not call hosted product modules,
payment-provider SDKs, wallet internals, payout internals, or settlement systems
directly.

```text
generated page
  -> billing_portal module action
  -> services/integrations/mozaikspay_client.py
  -> MozaiksPay-compatible provider API
```

## Selection And Replacement Semantics

Factory defaults MozaiksPay for apps that need SaaS subscriptions, billing
portal redirects, token top-ups, usage status, or paid feature gates. Selection
is not activation: no account is created and the connector remains unconfigured
until an operator supplies credentials. Non-subscription and explicitly
self-managed apps receive no MozaiksPay artifacts.

The executable provider choices for subscription assignment are:

- `mozaiks_pay` — the managed MozaiksPay-compatible provider path.
- `entitlement_dispatch` — the self-managed OSS subscription assignment writer.

These choices are mutually exclusive because only one path may own subscription
assignment writes for `config/subscriptions.yaml`.

Replacement is supported at the provider boundary. A self-hosted or alternative
provider must preserve the generated app contract:

- keep the `billing_portal` facade behavior compatible
- serve the provider API paths used by `MozaiksPayClient`
- return the documented response fields
- avoid provider-internal identifiers in app-facing responses
- verify payment/subscription facts before applying provider-neutral effects
- preserve `app/config/subscriptions.yaml` as the app plan and entitlement input

Replacing the provider does not imply compatibility with BlocUnited-private
wallet, payout, settlement, merchant, or hosted billing operations.

## Configuration

The generated client resolves configuration from the `mozaikspay` connector when
available, with environment variable fallback for self-hosted/local operation.

Public/app-facing handles:

- `MOZAIKSPAY_API_BASE`
- `MOZAIKS_APP_URL`

Secret handles:

- `MOZAIKSPAY_API_KEY`
- `MOZAIKSPAY_CLIENT_ID`
- `MOZAIKSPAY_CLIENT_SECRET`

Generated artifacts may declare these names. They must not contain raw API keys,
client secrets, payment-provider credentials, private keys, connection strings,
or hosted product credential topology.

## Provider API Surface

The machine-readable source of truth is:

```text
factory_app/build_context/mozaikspay/provider_api_contract.yaml
```

The current public endpoints consumed by generated apps are:

- `GET /api/mozaikspay/v1/subscription/status`
- `POST /api/mozaikspay/v1/billing-portal/session`
- `POST /api/mozaikspay/v1/subscription/checkout-session`
- `POST /api/mozaikspay/v1/tokens/top-up-session`
- `GET /api/mozaikspay/v1/health`

The generated client authenticates with a MozaiksPay API key when configured, or
with the compatibility client-credentials headers described in
`provider_api_contract.yaml`.

Runtime usage status is read from the app runtime through `MOZAIKS_APP_URL`; it
is not a hosted-provider ledger API.

## Facade Actions

The generated `billing_portal` module exposes the app-facing actions:

- `get_subscription_status`
- `get_usage_status`
- `get_token_status`
- `list_plans`
- `start_subscription_checkout`
- `start_token_top_up`
- `open_billing_portal`

Read actions require `billing_portal.read`. Checkout, top-up, and billing portal
actions require `billing_portal.manage`.

## Entitlement And Fulfillment Boundary

Generated apps express plans, limits, token allowances, token top-up products,
and assignment storage in `app/config/subscriptions.yaml`.

A compatible provider owns only the external verified fact source. After a
provider verifies a checkout, subscription change, or top-up, effects must cross
into the app/runtime through provider-neutral fulfillment and entitlement
boundaries such as `BillingFulfillmentCommand`, `EntitlementPort`, subscription
assignment records, and the OSS token wallet ledger.

Provider callbacks must fail closed when malformed or unsigned. Replayed events
must reuse the same idempotency key so `BillingFulfillmentService` can replay or
reject duplicate fulfillment commands deterministically.

The generated app should not embed provider product IDs, provider customer IDs,
payment processor IDs, wallet ledgers, payout ledgers, webhook handlers, or
payment processor SDK calls.

## Error Semantics

Provider responses must follow the response and error shapes in
`provider_api_contract.yaml`.

Stable expectations:

- authentication failures use HTTP `401`
- authorization failures use HTTP `403`
- invalid app requests use HTTP `400`
- missing app/customer/subscription facts use HTTP `404`
- application-level failures return `success: false` with safe error details

Responses must not expose raw secrets, provider customer IDs, provider
subscription IDs, payment provider IDs, session IDs, secret hashes, or internal
credential fields.
