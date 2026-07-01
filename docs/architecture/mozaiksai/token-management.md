# Token Management

How Mozaiks measures, records, and surfaces LLM token usage across workflow runs.

## Design Stance

Token tracking in Mozaiks has two runtime layers:

- `RuntimeUsageLedger` records factual LLM measurements.
- `TokenWalletLedger` records provider-neutral token allocations, credits,
  debits, refunds, and balance projections.

Neither layer owns payment providers, checkout, invoices, or hosted-product
pricing. Subscription intent is declared in `app/config/subscriptions.yaml`;
payment fulfillment remains app-owned or hosted-product behavior.

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

---

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
MongoDB process. It uses a stub LLM provider and does not spend provider tokens.

```powershell
docker start mozaiksai-mongo
python scripts\smoke_subscription_token_runtime_e2e.py `
  --mongo-uri mongodb://localhost:27017/mozaiks_subscription_token_smoke `
  --require-docker
```

The smoke verifies:

- factory-shaped `config/subscriptions.yaml` loading
- real Mongo subscription assignment lookup
- token allowance materialization into `RuntimeTokenWallet*` collections
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

---

## Where Not to Put Token Logic

- Do not put payment provider behavior in `RuntimeUsageLedger` or
  `TokenWalletLedger`.
- Do not emit usage events from workflow tools directly — use the middleware.
- Do not hardcode model pricing — use `pricing.py` as the single source.
- Do not query the usage collection directly from module handlers — use
  `get_runtime_usage_ledger()` or the `/api/me/usage` endpoint.
- Do not create app-local token balance ledgers when the OSS token wallet ledger
  covers the use case.

---

## Related Architecture

- [API Reference](api-reference.md) — `/api/me/usage` endpoint
- [Transport and Streaming](transport-and-streaming.md)
- [AG2 Ownership Boundary](../../architecture/workflows/ag2-ownership-boundary.md)
