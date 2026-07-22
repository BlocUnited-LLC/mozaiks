# Usage and Token Display

This document covers how AI token usage and wallet balance are surfaced to the
two audiences that care about them — **end users** of a generated app and
**operators** (admins) of that app — and separately how **creators** using the
mozaiks-app platform track their own consumption.

These are three distinct concerns. Do not conflate them.

---

## Background: What the OSS Runtime Tracks

The OSS runtime maintains two independent stores:

| Store | What it records | Key |
|-------|----------------|-----|
| **Usage ledger** | LLM calls, prompt tokens, completion tokens consumed | `app_id` + `user_id` |
| **Token wallet ledger** | Credits granted/purchased minus debits (the balance) | `app_id` + `wallet_id` + `user_id` (or `tenant_id`) |

Both are keyed by `app_id` so they are fully multi-tenant. Both are
provider-neutral — the ledgers do not know whether tokens were purchased via
MozaiksPay, minted by a free plan allowance, or granted by any other means.

**Existing platform endpoints:**

| Endpoint | Returns | Auth |
|----------|---------|------|
| `GET /api/me/usage` | Personal usage + wallet summaries + subscription limits | user |
| `GET /api/me/tokens` | Personal wallet balances + plan allowances | user |
| `GET /api/me/tokens/ledger` | Personal wallet entry history | user |

---

## The Three Audiences

### 1. End User of a Generated App (`/me` profile)

An end user wants to know: **how many tokens do I have left?**

This should appear automatically on the user profile page (`/me`) for any
generated app whose `app/config/subscriptions.yaml` declares `token_wallets`.
No module declaration is required — the platform injects it.

**Implementation target:**

`platform.py` `GET /api/me/profile-tabs` injects a built-in **Tokens** tab
when `app.state.subscriptions_config.token_wallets` is non-empty. The tab
returns data sourced from the same ledger as `/api/me/tokens` — balance +
plan allowance per wallet. `ProfilePage.jsx` already renders whatever tabs
come back; no `chat-ui` change is needed.

```
/me
└── [Tokens tab — injected by platform when app has token_wallets]
    ├── AI Tokens  ████████░░░░  8,450 remaining
    └── 1,550 of 10,000 used this period
```

The tab does not appear in apps without `token_wallets` in
`subscriptions.yaml`. Non-monetised apps are unaffected.

**This is OSS-only work. No MozaiksPay dependency.**

---

### 2. Operator / Admin of a Generated App (Admin Portal)

An operator wants to know two things — keep them in separate admin panels:

#### 2a. Their own personal usage
Same data as the end-user view above but surfaced inside the admin portal.
Sourced from `/api/me/tokens` + `/api/me/usage`. No new endpoint needed.

#### 2b. App-wide aggregate usage across all end users
The operator wants to see: total tokens consumed, active users, wallet
depletion rates — across the whole app, not scoped to their own account.

**Implementation target:** a new `GET /api/admin/usage` endpoint in
`platform.py` that queries the usage ledger by `app_id` only (no `user_id`
filter). Returns:

```json
{
  "period": "all_time",
  "total_tokens": 1240000,
  "total_llm_calls": 4300,
  "active_users": 87,
  "wallet_totals": [
    {
      "wallet_id": "ai_tokens",
      "label": "AI Tokens",
      "total_balance_remaining": 823500,
      "total_credited": 10000000,
      "total_debited": 9176500
    }
  ]
}
```

This endpoint requires admin role. It is an OSS runtime endpoint — it has no
knowledge of payment providers.

**Canonical admin panel declaration** (in `billing_portal/contracts/admin.yaml`
when the mozaikspay pack is included):

```yaml
schema_version: mozaiks.admin.v1
panels:
  - id: my-usage
    title: My Usage
    section: usage
    order: 10
    kind: metrics
    action: get_token_status
    fields:
      - id: token_wallets
        label: Token Wallets
        type: object
  - id: app-usage
    title: App Usage
    section: usage
    order: 20
    kind: metrics
    api_endpoint: /api/admin/usage
```

**This is OSS work. `/api/admin/usage` is a new endpoint to build.**

---

### 3. Creator Using mozaiks-app (Studio / BlocUnited Platform)

A creator building apps on the mozaiks-app platform is consuming **BlocUnited's
own token wallet** — not the wallet of any generated app they are building.
This is a separate billing relationship entirely.

**This is mozaiks-app work, not OSS work.** The existing `WalletPage`
(`/wallet`) on mozaiks-app already shows the creator's financial balance and
token top-up history. The token balance panel (`TokenBalancePanel`) added in
PR #47 will show their AI token balance on that same page.

For finer-grained Studio usage visibility (e.g. "how many tokens did I spend
on this build run?"), that is a mozaiks-app Studio feature, not an OSS
platform feature.

---

## Boundary Rules

These rules prevent the three concerns from bleeding into each other:

- **Do not** add creator-facing billing UI to OSS `chat-ui` or `platform.py`.
  Creator billing is mozaiks-app only.
- **Do not** add MozaiksPay-specific logic to the OSS `/api/me/profile-tabs`
  injection or `/api/admin/usage` endpoint. Those endpoints are
  provider-neutral.
- **Do not** expose raw wallet entry IDs, payment provider customer IDs, or
  checkout session IDs in any of these surfaces. Display only balance, label,
  unit, and computed usage percentages.
- **Do not** merge the app-wide operator view with the personal user view.
  They are different queries and different audiences.
- The `billing_portal` module facade (from the mozaikspay build context) may
  contribute admin panels via `contracts/admin.yaml` — but it must call the
  OSS `/api/admin/usage` endpoint for aggregate data, not duplicate the
  ledger query in module code.

---

## Build Order

| Step | What | Repo | Effort |
|------|------|------|--------|
| 1 | `GET /api/admin/usage` endpoint in `platform.py` | mozaiks OSS | small |
| 2 | Platform injects Tokens tab into `/api/me/profile-tabs` when app has token_wallets | mozaiks OSS | small |
| 3 | `billing_portal/contracts/admin.yaml` with my-usage + app-usage panels | mozaiks OSS (mozaikspay pack) | small |
| 4 | Creator Studio usage visibility (build run token cost) | mozaiks-app | separate initiative |

Steps 1–3 are independent and can be done in any order. Step 4 is a
mozaiks-app initiative that does not block or depend on steps 1–3.

---

## What This Does Not Cover

- Payment processing, checkout, or subscription management — see
  `docs/architecture/app/` billing docs and the mozaikspay build context
  `contract.yaml`
- App product analytics (page views, conversion, retention) — see
  `docs/architecture/app/app-metrics.md`
- Multi-tenant org-level aggregation — out of scope until org/workspace
  scoping is first-class in the token wallet ledger
- Per-user breakdown in the admin view — the `/api/admin/usage` endpoint
  returns app-wide aggregates only; per-user drill-down is a future extension
