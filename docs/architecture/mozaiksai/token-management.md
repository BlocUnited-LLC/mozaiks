# Token Management

How Mozaiks measures, records, and surfaces LLM token usage across workflow runs.

## Design Stance

Token tracking in Mozaiks has three runtime layers:

- `RuntimeUsageLedger` records factual LLM measurements.
- `TokenWalletLedger` records provider-neutral token allocations, credits,
  debits, refunds, and balance projections.
- `RuntimeTokenBudgetAlertLedger` records AG2 observer alerts for live run
  budget warnings and critical thresholds.

Neither layer owns payment providers, checkout, invoices, or hosted-product
pricing. Subscription intent is declared in `app/config/subscriptions.yaml`;
verified fulfillment is normalized into OSS `BillingFulfillmentCommand` records
before it mutates subscription assignment state or token wallet balances.
Payment collection remains app-owned or hosted-product behavior.

Token logic must not leak into workflow authoring or module business logic unless
a workflow explicitly needs budget-aware behavior. Modules and payment adapters
should call the wallet primitive after their own authorization or payment checks
succeed.

Factory builds use `factory_app/workflows/SubscriptionContractDesigner` to
produce the generated app's provider-neutral subscription/token contract. That
workflow decides whether `config/subscriptions.yaml` should exist, what
`token_wallets` and `token_allowances` are declared, which module actions need
`entitlement_gate`, and which generated workflows should carry metering
declarations. It does not create payment-provider resources or mutate token
balances.

MozaiksPay is the default managed adapter path for hosted checkout and token
top-ups. It is not the canonical owner of subscription state, entitlement
state, token balances, usage records, or wallet ledgers. After MozaiksPay or
another adapter verifies a payment/subscription fact, it submits a provider-
neutral fulfillment command to the OSS runtime.

For the broader app monetization boundary, see
[Monetization Contract](monetization-contract.md). The short rule is that OSS
defines the subscription, token, entitlement, facade, and fulfillment contracts;
hosted products or app-owned adapters verify money movement and submit the
provider-neutral effects.

---

## How Tokens Are Captured

### AG2 Usage Middleware

`MozaiksUsageMiddleware` in `mozaiksai/core/usage/middleware.py` is a
`BaseMiddleware` subclass registered with AG2's conversation machinery. On each
LLM response, it extracts token counts from AG2's usage data and writes a usage
event to the `RuntimeUsageLedger`.

```python
# Factory helper — used at AG2 agent initialization
from mozaiksai.core.usage.middleware import build_ag2_usage_middleware
```

The middleware captures:
- `input_tokens` / `output_tokens` / `total_tokens`
- `model` identifier
- `app_id`, `tenant_id`, `workspace_id`, `user_id`, `chat_id` from workflow
  context variables when present
- estimated cost via `mozaiksai/core/usage/pricing.py`

Before each LLM call, the middleware runs `TokenUsageGuard`. The default
preflight requirement is one token so a depleted wallet is blocked. Hosts or
workflows can tighten this by setting one of these context variables:

- `token_preflight_required_tokens`
- `token_watchdog_required_tokens`
- `token_budget_required_tokens`

The process-wide fallback is `MOZAIKS_TOKEN_PREFLIGHT_REQUIRED_TOKENS`.

### AG2 Token Watchdog

`mozaiksai/core/usage/watchdog.py` attaches AG2's built-in `TokenMonitor` to
runtime-created agents and adds a Mozaiks alert bridge observer. AG2 owns the
observer mechanics and emits `ObserverAlert` events. Mozaiks records those
alerts as `chat.token_budget_alert` events with app/run/user scope.

Thresholds resolve in this order:

1. Run context: `token_watchdog_warn_tokens`, `token_watchdog_alert_tokens`
2. Run context aliases: `token_budget_warn_tokens`, `token_budget_alert_tokens`
3. Run context maximum: `token_budget_max_tokens` for the alert threshold
4. Environment defaults:
   - `MOZAIKS_TOKEN_WATCHDOG_WARN_TOKENS`
   - `MOZAIKS_TOKEN_WATCHDOG_ALERT_TOKENS`

Set `MOZAIKS_TOKEN_WATCHDOG_ENABLED=false` to disable observer attachment.
Watchdog alerts are live observability signals. They do not create invoices,
grant plans, or replace token-wallet debits.

### RuntimeUsageLedger

`mozaiksai/core/usage/ledger.py` owns the persistence layer.

```python
from mozaiksai.core.usage import get_runtime_usage_ledger

ledger = get_runtime_usage_ledger()
await ledger.record_usage_delta(payload)           # write a usage event
usage = await ledger.query_usage(app_id=..., user_id=..., limit=500)  # read
```

Usage events are persisted to MongoDB under:
- Database: `SYSTEM_DATABASE` (the shared runtime system database)
- Collection: `RuntimeUsageEvents`

Indexes: `event_id` (unique), `(app_id, event_ts)`, `(app_id, user_id, event_ts)`,
`(app_id, chat_id)`.

Budget alerts are persisted separately under `RuntimeTokenBudgetAlerts` with
the same app/user/chat indexing pattern.

### TokenWalletLedger

`mozaiksai/core/tokens/wallet.py` owns provider-neutral token accounting.

```python
from mozaiksai.core.tokens import get_token_wallet_ledger

ledger = get_token_wallet_ledger()
await ledger.credit(
    app_id="app_1",
    user_id="user_1",
    wallet_id="ai_tokens",
    amount=100000,
    idempotency_key="payment:checkout_session_123",
)
await ledger.debit(
    app_id="app_1",
    user_id="user_1",
    wallet_id="ai_tokens",
    amount=1200,
    idempotency_key="usage:usage_event_123",
)
balance = await ledger.query_balance(app_id="app_1", user_id="user_1")
```

Wallet entries are idempotent by key and persisted under:

- `RuntimeTokenWalletEntries` — append-only operation records
- `RuntimeTokenWalletBalances` — projected balance per app/wallet/scope

Balances are scoped by `app_id`, `wallet_id`, and either user, tenant, or app
scope. Debits reject by default when the balance is insufficient; wallets can
explicitly allow negative balances for overage-style products.

Workspace context participates in active-plan resolution and usage event
metadata. It does not create a separate wallet balance scope: token balances
still follow each wallet's declared `scope` value (`user`, `tenant`, or app
fallback).

### BillingFulfillmentService

`mozaiksai/core/billing/fulfillment.py` owns the provider-neutral bridge from a
verified billing fact to runtime state. It accepts `BillingFulfillmentCommand`
objects from trusted adapters and applies deterministic effects:

- `subscription_activated` / `subscription_updated`: upsert the configured
  assignment-store record and materialize any plan token allowances.
- `subscription_cancelled`: mark the assignment inactive/cancelled without
  deleting history.
- `token_top_up_paid` / `token_credit_granted`: credit the configured token
  wallet idempotently.
- `refund_applied` / `chargeback_applied`: debit or reject the reversal without
  hiding the failed clawback.

The platform ingress is:

```text
POST /api/billing/fulfillment/apply
GET  /api/admin/billing/fulfillment
```

The apply route requires an internal API key or billing-admin authorization.
Generated app pages should not call it. Generated apps request checkout or
top-up sessions through an app-owned billing facade; only a trusted adapter
submits fulfillment after verification.

Generated app runtime acceptance tests cover this ingress as part of the full
chain: `POST /api/billing/fulfillment/apply` writes the configured
`assignment_store`, `ConfiguredEntitlementAdapter` grants the gated action,
plan allowances appear in `/api/me/tokens`, and depleted wallets raise
`INSUFFICIENT_TOKENS` before the LLM provider call.

### summarize_usage_events

`summarize_usage_events(events)` in `ledger.py` aggregates a list of raw events
into totals by model, returning:
```python
{
    "total_tokens": int,
    "input_tokens": int,
    "output_tokens": int,
    "estimated_cost_usd": float,
    "by_model": {...},
    "event_count": int,
}
```

---

## Querying Usage

### Platform API Endpoint

```
GET /api/me/usage?app_id=<id>&limit=500
```

Returns the current user's usage ledger entries plus declared subscription limits
from `app/config/subscriptions.yaml` and token wallet summaries:

```json
{
  "events": [...],
  "total_tokens": 12400,
  "by_model": {"gpt-4o": {"total_tokens": 12400, "estimated_cost_usd": 0.18}},
  "subscription_usage": {
    "plan_id": "pro",
    "limits": {...}
  },
  "token_budget_alerts": [
    {
      "severity": "warning",
      "workflow_name": "AppGenerator",
      "agent_name": "PlannerAgent",
      "total_tokens": 50000
    }
  ],
  "token_wallets": {
    "wallets": [
      {
        "wallet_id": "ai_tokens",
        "balance": {"balance": 87600}
      }
    ]
  }
}
```

Authentication: requires bearer token via `require_any_auth`.

Additional token endpoints:

- `GET /api/me/tokens` — current user's token wallet balances
- `POST /api/me/tokens/sync` — idempotently materialize current subscription
  allowances
- `GET /api/me/tokens/ledger?wallet_id=ai_tokens` — current user's wallet
  ledger entries

---

## Cost Estimation

`mozaiksai/core/usage/pricing.py` provides `estimate_token_cost(model, tokens)`.
Estimates are informational only — not authoritative billing data.

Production deployments should keep an operator-maintained JSON or YAML catalog.
When `ai-pricing/catalogs/usage-pricing.generated.json` exists, the runtime uses it
automatically unless `MOZAIKS_USAGE_PRICING_CATALOG_PATH` points somewhere else.
Installed wheels also include the same generated catalog under the runtime
package so source checkouts and package installs get the same default coverage.
Mozaiks can generate that catalog from LiteLLM's maintained model-price
reference:

```powershell
python scripts\update_usage_pricing_catalog.py `
  --output ai-pricing\catalogs\usage-pricing.generated.json
```

The GitHub workflow `.github/workflows/update-usage-pricing-catalog.yml` runs
the same refresh weekly and opens a normal PR when provider reference prices
change. AG2 intentionally does not maintain model prices; Mozaiks treats
provider pricing as a runtime catalog concern so model launches and provider
price changes do not require framework code changes.

The updater is intentionally CI/PR based instead of runtime-live fetching. This
keeps cost estimates reproducible, avoids startup dependency on GitHub, and
lets upstream schema changes fail in automation before they affect operators.
Each generated catalog records the upstream revision plus a content SHA-256, and
the updater fails if normalization produces an unexpectedly small catalog or a
large row-count drop from the existing generated catalog.

Example:

```yaml
schema_version: mozaiks.usage_pricing.v1
models:
  gpt-5-nano:
    input_per_1m_usd: 0.05
    cached_input_per_1m_usd: 0.005
    output_per_1m_usd: 0.40
  claude-sonnet-4.6:
    input_per_1k_usd: 0.003
    cached_input_per_1k_usd: 0.0003
    output_per_1k_usd: 0.015
  default:
    input_per_1k_usd: 0.001
    cached_input_per_1k_usd: 0.0001
    output_per_1k_usd: 0.004
```

Precedence:

1. Provider/runtime-supplied explicit cost, if present in the usage event
2. Model-specific env vars, such as
   `MOZAIKS_USAGE_GPT_5_NANO_INPUT_PER_1K_USD`
3. Global env vars, `MOZAIKS_USAGE_INPUT_PER_1K_USD` and
   `MOZAIKS_USAGE_OUTPUT_PER_1K_USD`
4. `MOZAIKS_USAGE_PRICING_OVERRIDE_PATH` for negotiated/custom provider rates
5. `MOZAIKS_USAGE_PRICING_CATALOG_PATH`, usually the generated LiteLLM catalog
   or the repo default `ai-pricing/catalogs/usage-pricing.generated.json`
6. Built-in non-authoritative fallback table for known historical models

Operators that need negotiated rates, internal models, or temporary upstream
patches should create a real override file and point
`MOZAIKS_USAGE_PRICING_OVERRIDE_PATH` at it. The repo-local
`ai-pricing/catalogs/usage-pricing.overrides.json` path is ignored by Git and
excluded from package manifests so private rates do not get committed or
published accidentally.

Override file shape:

```json
{
  "schema_version": "mozaiks.usage_pricing.v1",
  "models": {
    "custom-provider/private-model": {
      "input_per_1m_usd": 0.5,
      "cached_input_per_1m_usd": 0.05,
      "output_per_1m_usd": 1.5
    }
  }
}
```

The usage ledger stores both `estimated_cost_usd` and `cost_source` on each
event. Dashboards should show costs from `catalog`, `estimated`, or `provided`
as configured estimates and treat `default_table` as local/dev fallback data.
Set `MOZAIKS_USAGE_PRICING_DISABLE_DEFAULT_CATALOG=true` only for tests or
diagnostics that need to prove behavior without the generated default catalog.

### Pricing Health

`/api/me/usage` includes a `pricing_health` object derived from the event
`cost_source` values and the active pricing catalogs:

```json
{
  "status": "ready",
  "catalog_model_count": 2486,
  "catalog_updated_at": "2026-07-11T05:41:51.909060+00:00",
  "used_model_count": 2,
  "unpriced_model_count": 0,
  "priced_event_count": 18,
  "unpriced_event_count": 0,
  "coverage_percent": 100.0,
  "cost_source_counts": {
    "catalog": 18
  }
}
```

Statuses:

- `ready` — active catalog/override pricing covered the measured events.
- `unpriced_models` — one or more events had `cost_source: not_configured`.
- `fallback_prices` — at least one event used the built-in fallback table.
- `catalog_unavailable` — a configured catalog path was missing or invalid.
- `not_configured` — no catalog, override, env rate, or measured usage exists.

Studio `/usage` and app-specific usage pages surface this health instead of
showing silent `$0.00` averages for unpriced model traffic.

---

## Customer Markups and Billable Estimates

Provider prices and customer prices are different facts:

- Provider prices answer: "What did OpenAI, Anthropic, or another provider
  charge the operator?"
- Customer markups answer: "What does this app charge its users for that
  measured usage?"

Provider prices belong in the runtime usage-pricing catalog. Customer markups
belong in the app's `app/config/subscriptions.yaml` because two apps can use
the same model and charge users differently.

Example app-level policy:

```yaml
schema_version: mozaiks.subscriptions.v1
label: Example SaaS
default_plan_id: pro

usage_charge_policies:
  - meter_id: ai_tokens
    label: AI usage
    source: runtime_llm_usage
    basis: provider_cost_usd
    markup_percent: 35
    minimum_charge_usd: 0
    rounding: cent

plans:
  - plan_id: pro
    label: Pro
    capabilities: [ai.chat]
```

`basis: provider_cost_usd` estimates customer charge from the runtime's provider
cost estimate plus markup. `basis: tokens` uses a declared
`unit_price_usd_per_1k` instead:

```yaml
usage_charge_policies:
  - meter_id: ai_tokens
    label: AI usage
    source: runtime_llm_usage
    basis: tokens
    unit_price_usd_per_1k: 0.01
    markup_percent: 20
    rounding: micro_usd
```

`/api/me/usage` enriches the current user's usage with
`billable_amount_usd` when a `runtime_llm_usage` charge policy is configured.
This is still an estimate. Payment provider prices, checkout sessions, invoices,
refunds, tax, and settlement remain app-owned or hosted-product behavior.

## Subscription Limits and Allowances

`app/config/subscriptions.yaml` (SaaS apps only) declares plan-level capability
grants. The usage ledger data can be combined with these limits to drive UI
quota displays or soft-enforcement gates via `EntitlementPort`.

Plans may also declare token wallets and allowances:

```yaml
schema_version: mozaiks.subscriptions.v1
default_plan_id: pro

token_wallets:
  - wallet_id: ai_tokens
    label: AI tokens
    unit: tokens
    usage_meter_id: ai_tokens
    scope: user
    auto_debit_usage: true

usage_charge_policies:
  - meter_id: ai_tokens
    label: AI usage
    source: runtime_llm_usage
    basis: provider_cost_usd
    markup_percent: 35

plans:
  - plan_id: pro
    label: Pro
    capabilities: [ai.chat]
    usage_limits:
      - meter_id: ai_tokens
        unit: tokens
        monthly_limit: 100000
    token_allowances:
      - wallet_id: ai_tokens
        amount: 100000
        cadence: monthly
```

When `auto_debit_usage` is true, the runtime usage event dispatcher
materializes the active plan allowance and debits the wallet from factual
`chat.usage_delta` events. This is accounting, not payment processing.
If the app declares `assignment_store.workspace_id_field`, the active plan is
resolved with app/user/tenant/workspace scope before allowances are
materialized.

Generated apps should treat this file as declarative infrastructure. They may
display balances through `/api/me/usage` and `/api/me/tokens`, and may declare
future metering intent for expensive module/workflow actions, but they must not
create parallel usage or wallet collections.

---

## Docker-Backed Runtime Smoke

Use this smoke when validating the full subscription/token path against a real
MongoDB process. It applies a test fulfillment command, uses a stub LLM
provider, and does not spend provider tokens.

```powershell
docker start mozaiksai-mongo
python scripts\smoke_subscription_token_runtime_e2e.py `
  --mongo-uri mongodb://localhost:27017/mozaiks_subscription_token_smoke `
  --require-docker
```

The smoke verifies:

- factory-shaped `config/subscriptions.yaml` loading
- real Mongo subscription assignment lookup
- provider-neutral fulfillment into the assignment store and
  `RuntimeTokenWallet*` collections
- an allowed stub LLM boundary call while balance is sufficient
- idempotent usage debit
- depleted-balance denial before the provider client is invoked

The pytest wrapper is opt-in:

```powershell
$env:MOZAIKS_RUN_SUBSCRIPTION_TOKEN_DOCKER_SMOKE="1"
$env:MONGO_URI="mongodb://localhost:27017/mozaiks_subscription_token_smoke"
pytest -q tests/test_subscription_token_runtime_real_mongo.py
```

The smoke creates a unique test database and cleans up its app data and runtime
token wallet records before exit.

### Local Manual Top-Up Smoke

For local/manual credit tests, use the same fulfillment service with
`source="test"` or `source="manual"` and no payment secrets:

```python
from mozaiksai.core.billing.fulfillment import (
    BillingFulfillmentCommand,
    BillingFulfillmentService,
)

service = BillingFulfillmentService(app_root="./my-app/app")
await service.apply_durable(
    BillingFulfillmentCommand(
        command_id="manual_top_up_001",
        event_type="token_credit_granted",
        source="test",
        app_id="my-app",
        user_id="local-user",
        wallet_id="ai_tokens",
        token_amount=10000,
    )
)
```

Then verify:

```powershell
curl http://localhost:8000/api/me/tokens -H "Authorization: Bearer <local-token>"
```

This is the supported manual top-up path for OSS/local smoke testing. Do not add
ad hoc wallet mutation endpoints or generated app-local ledger writes.

---

## Where Not to Put Token Logic

- Do not put payment provider behavior in `RuntimeUsageLedger` or
  `TokenWalletLedger`.
- Do not emit usage events from workflow tools directly — use the middleware.
- Do not hardcode model pricing — use `pricing.py` as the single source.
- Do not put customer markups in the provider pricing catalog.
- Do not query the usage collection directly from module handlers — use
  `get_runtime_usage_ledger()` or the `/api/me/usage` endpoint.
- Do not create app-local token balance ledgers when the OSS token wallet ledger
  covers the use case.

---

## Related Architecture

- [API Reference](api-reference.md) — `/api/me/usage` endpoint
- [Transport and Streaming](transport-and-streaming.md)
- [AG2 Ownership Boundary](../../architecture/workflows/ag2-ownership-boundary.md)
