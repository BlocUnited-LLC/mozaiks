# Token Management

How Mozaiks measures, records, and surfaces LLM token usage across workflow runs.

## Design Stance

Token tracking in Mozaiks is an **observability and cost-attribution layer**, not
a billing authority. The runtime ledger records factual measurements. Subscription
enforcement and budget gates are application-level concerns, surfaced through
`app/config/subscriptions.yaml` and the `EntitlementPort` — not hardcoded into
the ledger.

Token logic must not leak into workflow authoring or module business logic unless
a workflow explicitly needs budget-aware behavior.

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
- `app_id`, `user_id`, `chat_id` from workflow context variables
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
from `app/config/subscriptions.yaml`:

```json
{
  "events": [...],
  "total_tokens": 12400,
  "by_model": {"gpt-4o": {"total_tokens": 12400, "estimated_cost_usd": 0.18}},
  "subscription_usage": {
    "plan_id": "pro",
    "limits": {...}
  }
}
```

Authentication: requires bearer token via `require_any_auth`.

---

## Cost Estimation

`mozaiksai/core/usage/pricing.py` provides `estimate_token_cost(model, tokens)`.
Estimates are informational only — not authoritative billing data.

---

## Subscription Limits

`app/config/subscriptions.yaml` (SaaS apps only) declares plan-level capability
grants. The usage ledger data can be combined with these limits to drive UI
quota displays or soft-enforcement gates via `EntitlementPort`.

The ledger itself does not enforce subscription limits.

---

## Where Not to Put Token Logic

- Do not put budget enforcement in `RuntimeUsageLedger` — that is the platform
  or app's responsibility.
- Do not emit usage events from workflow tools directly — use the middleware.
- Do not hardcode model pricing — use `pricing.py` as the single source.
- Do not query the usage collection directly from module handlers — use
  `get_runtime_usage_ledger()` or the `/api/me/usage` endpoint.

---

## Related Architecture

- [API Reference](api-reference.md) — `/api/me/usage` endpoint
- [Transport and Streaming](transport-and-streaming.md)
- [AG2 Ownership Boundary](../../architecture/workflows/ag2-ownership-boundary.md)
