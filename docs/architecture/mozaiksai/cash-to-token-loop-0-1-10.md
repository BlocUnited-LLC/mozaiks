# Cash-to-Token Loop Plan for 0.1.10

## Purpose

`0.1.10` should close the gap between the existing token runtime primitives and
the complete product loop:

```text
customer pays cash -> verified billing event -> token wallet credit
-> generated app LLM usage -> AG2 token guard/debit -> depleted balance UX
```

The loop must be OSS-owned and provider-neutral. MozaiksPay should be the first
managed adapter path, not the source of truth for wallet balances, entitlement
gates, subscription assignments, or depletion behavior.

## Current State

The core runtime already has the primitives needed for the spending side:

- `mozaiksai/core/usage/middleware.py` attaches AG2 middleware and runs
  `TokenUsageGuard` before each AG2 LLM call.
- `mozaiksai/core/tokens/guard.py` loads `app/config/subscriptions.yaml`, checks
  `token_wallets[].auto_debit_usage`, materializes plan allowances when needed,
  and raises `TokenUsageDenied` with `INSUFFICIENT_TOKENS` when balance is too
  low.
- `mozaiksai/core/tokens/usage_ingest.py` consumes `chat.usage_delta` and
  records wallet debits after usage events.
- `mozaiksai/core/tokens/wallet.py` owns append-only token wallet entries,
  projected balances, allowance materialization, idempotency, and
  insufficient-balance rejection.
- `mozaiksai/core/runtime/app/subscriptions_loader.py` loads
  `app/config/subscriptions.yaml` with plans, token wallets, token allowances,
  usage limits, and charge-estimate policies.
- `mozaiksai/core/runtime/app/entitlements.py` enforces
  `actions[].entitlement_gate` through `ConfiguredEntitlementAdapter`.
- `mozaiksai/hosts/platform.py` exposes `/api/me/usage`, `/api/me/tokens`,
  `/api/me/tokens/sync`, and `/api/me/tokens/ledger`.

The missing part is the fulfillment side: a verified payment, subscription, or
manual grant should become a canonical plan assignment and/or token wallet
credit without provider-specific logic leaking into generated apps or runtime
token accounting.

## MozaiksPay Reality Check

`mozaiks-app/docs/mozaikspay-handoff-and-readiness.md` confirms that MozaiksPay
is already a hosted-product implementation, not a stub. The hosted side owns:

- hosted checkout in `mozaiks-app/app/modules/mozaikspay_checkout/`
- subscription billing and generated-app provider API in
  `mozaiks-app/app/modules/hosted_billing/`
- merchant onboarding in `mozaiks-app/app/modules/mozaikspay_merchant/`
- API key issuance in `mozaiks-app/app/modules/mozaikspay_api_keys/`
- wallet and payout surfaces in `mozaiks-app/app/modules/wallet/`
- backing payment-provider adapters under
  `mozaiks-app/app/services/adapters/payments/`

The most relevant hosted module for this `0.1.10` loop is
`hosted_billing`. It already declares:

- `assign_plan`
- `cancel_subscription`
- `expire_subscription`
- `get_billing_status`
- `create_subscription_checkout_session`
- `create_billing_portal_session`
- `create_mozaikspay_client`
- `rotate_mozaikspay_client_secret`
- `revoke_mozaikspay_client`
- `get_monetization_readiness`
- `check_token_availability`
- `get_token_usage`

It also mounts:

```text
POST /webhooks/mozaikspay/billing
GET  /api/mozaikspay/v1/subscription/status
POST /api/mozaikspay/v1/billing-portal/session
```

Important observation: `hosted_billing` already reaches into the OSS
`TokenWalletLedger` for token allowance sync and token availability/status. That
means the proprietary hosted side is already proving the shape of the bridge,
but the bridge is not formalized as a reusable OSS fulfillment contract yet.

## Gap From Current MozaiksPay to the 0.1.10 Loop

The gap is not "build MozaiksPay." The gap is making the handoff between
MozaiksPay and OSS runtime canonical.

Current generated-app provider API:

- generated app can ask MozaiksPay for subscription status
- generated app can ask MozaiksPay for a billing portal session
- generated app can read runtime usage and token balances from its own runtime

Missing or unclear generated-app/customer loop:

- generated app needs a public top-up or subscription checkout action that
  routes through the app-owned `billing_portal` facade
- MozaiksPay needs a provider-side path that turns verified checkout/webhook
  facts into an OSS `BillingFulfillmentCommand`
- OSS runtime needs one canonical service that applies that command to
  assignment store records and token wallet entries
- denial from `TokenUsageGuard` needs structured recovery metadata so the
  generated UI can route the user to billing/top-up
- tests need to prove the loop without using real payment secrets

The bridge should reuse MozaiksPay's existing hosted billing facts, provider
client auth, webhook verification, and checkout session creation. It should
remove duplicated ad hoc wallet-credit behavior over time by moving the common
effects into OSS fulfillment code.

## Architectural Decision

OSS owns the cash-to-token contract.

Payment providers own payment fulfillment only. MozaiksPay is the default
managed provider adapter, but Stripe, Paddle, manual invoice, enterprise
contract billing, or a custom app-owned integration should be able to feed the
same runtime contract.

Do not add a second wallet ledger, usage ledger, plan catalog, entitlement
system, or billing state machine. The `0.1.10` work should connect verified
billing events to the existing subscription and wallet primitives.

## Target Runtime Loop

1. Generated app declares plans and wallets in `app/config/subscriptions.yaml`.
2. Generated app exposes pricing, usage, billing, and top-up surfaces.
3. User starts checkout/top-up through an app-owned billing facade.
4. Provider adapter creates a checkout or billing portal session.
5. Provider confirms payment or subscription state through a verified event.
6. Runtime applies a provider-neutral fulfillment command:
   - create or update active subscription assignment
   - credit token wallet
   - materialize plan allowance
   - debit/refund-adjust wallet on refund or chargeback
   - fall back to free/default plan on cancellation
7. User runs app workflows or LLM-backed actions.
8. AG2 usage middleware calls `TokenUsageGuard` before each LLM call.
9. Runtime records factual usage and debits the wallet from `chat.usage_delta`.
10. When balance is depleted, the next call is blocked with a structured
    `INSUFFICIENT_TOKENS` response that generated app UI can turn into an
    upgrade/top-up action.

## Provider-Neutral Fulfillment Contract

Add a runtime-level contract, tentatively:

```text
mozaiksai/core/billing/fulfillment.py
```

The contract should accept normalized fulfillment commands, not raw provider
webhook payloads:

```python
class BillingFulfillmentCommand(BaseModel):
    command_id: str
    app_id: str
    user_id: str | None = None
    tenant_id: str | None = None
    workspace_id: str | None = None
    source: Literal["mozaikspay", "stripe", "manual", "custom"]
    event_type: Literal[
        "subscription_activated",
        "subscription_updated",
        "subscription_cancelled",
        "token_top_up_paid",
        "token_credit_granted",
        "refund_applied",
        "chargeback_applied",
    ]
    plan_id: str | None = None
    status: str | None = None
    token_wallet_id: str = "ai_tokens"
    token_amount: int | None = None
    occurred_at: datetime | None = None
    provider_reference: str | None = None
    metadata: dict[str, Any] = {}
```

Rules:

- `command_id` is the idempotency key across all fulfillment effects.
- Metadata must use the existing token wallet safe-metadata policy: no secrets,
  raw credentials, API keys, bearer tokens, private keys, customer payment
  identifiers, or checkout session secrets.
- Provider adapters verify signatures or auth before creating commands. The
  fulfillment service must not trust raw webhook payloads.
- The service must be deterministic and replay-safe.
- A command can perform multiple effects only when they are part of one product
  fact. Example: `subscription_activated` may upsert assignment and materialize
  plan allowance.

## Fulfillment Effects

### Subscription Assignment

Use the configured `assignment_store` from `app/config/subscriptions.yaml`.

For `subscription_activated` and `subscription_updated`:

- Upsert one active assignment record at the configured data alias.
- Store `app_id`, user/tenant/workspace scope, `plan_id`, `status`,
  `starts_at`, `expires_at`, `granted_capabilities`, and `plan_snapshot` using
  the field names declared by `assignment_store`.
- Prefer the static plan capabilities from `subscriptions.yaml` for the
  snapshot unless the command carries a safe, validated plan snapshot.

For `subscription_cancelled`:

- Mark the assignment inactive/cancelled.
- Do not delete the record.
- Effective entitlement behavior falls back through
  `ConfiguredEntitlementAdapter.current_plan_id()` to the default plan.

### Token Credits

For `token_top_up_paid` and `token_credit_granted`:

- Call `TokenWalletLedger.credit()`.
- Use idempotency key:

```text
billing_fulfillment:{command_id}:credit:{wallet_id}
```

- `source` should be `billing_fulfillment`.
- `reason` should be short and user-safe, such as `Token top-up` or
  `Subscription token grant`.

For `subscription_activated` and `subscription_updated`:

- Call `TokenWalletLedger.ensure_plan_allowances()`.
- Let existing monthly and one-time allowance idempotency rules prevent
  duplicate credits.

For `refund_applied` and `chargeback_applied`:

- Use `TokenWalletLedger.debit()` with operation `debit`.
- Use idempotency key:

```text
billing_fulfillment:{command_id}:refund:{wallet_id}
```

- If the wallet does not allow negative balances and the debit is rejected, keep
  the rejected ledger entry and return a structured partial-failure result for
  operator review. Do not silently mutate subscription state to hide the failed
  clawback.

## Runtime API Shape

The platform host exposes provider-neutral internal/runtime endpoints for
trusted fulfillment ingress and command-log review:

```text
POST /api/billing/fulfillment/apply
GET  /api/admin/billing/fulfillment?app_id=...
```

`POST /api/billing/fulfillment/apply` requires a runtime/admin/provider auth
boundary, not ordinary end-user auth. In hosted deployments, MozaiksPay can call
this route after verifying its own payment provider event. In OSS/local tests, a
manual/test adapter can call the same service without real payment secrets.

Avoid exposing raw wallet mutation endpoints to generated app pages. Generated
apps should request checkout/top-up through their app-owned billing facade; only
verified fulfillment applies credits.

## MozaiksPay Bridge Contract

MozaiksPay should bridge to OSS through normalized fulfillment commands after
it has verified provider facts.

Hosted billing event mapping:

| MozaiksPay hosted fact | OSS command |
| --- | --- |
| Subscription checkout completed | `subscription_activated` |
| Subscription changed plan/status | `subscription_updated` |
| Subscription cancelled/deleted | `subscription_cancelled` |
| Token top-up checkout paid | `token_top_up_paid` |
| Operator/manual token grant | `token_credit_granted` |
| Refund or chargeback accepted | `refund_applied` or `chargeback_applied` |

The generated app should never call `hosted_billing.assign_plan` directly.
MozaiksPay owns checkout, webhook verification, provider customer mapping, and
client credential auth. OSS owns the canonical effect application once those
facts are trusted.

Provider API additions to consider in `mozaiks-app`:

```text
POST /api/mozaikspay/v1/subscription/checkout-session
POST /api/mozaikspay/v1/tokens/top-up-session
GET  /api/mozaikspay/v1/tokens/status
```

Generated app facade additions to consider in OSS templates:

```text
billing_portal.start_subscription_checkout
billing_portal.start_token_top_up
billing_portal.get_token_status
```

These names are intentionally app-owned facade actions. They can call
MozaiksPay by default or another selected billing provider later.

## AG2 Token Guard Integration

The existing AG2 guard path is the enforcement point. `0.1.10` should preserve
that boundary and improve the UX contract around denial.

Current path:

```text
AG2 LLM call
-> MozaiksUsageMiddleware.on_llm_call()
-> TokenUsageGuard.check_or_raise()
-> TokenUsageDenied(error_code="INSUFFICIENT_TOKENS")
```

Required improvements:

- Keep preflight checks before provider calls so depleted users do not consume
  provider tokens.
- Include structured denial metadata:
  - `error_code`
  - `wallet_id`
  - `balance`
  - `required_tokens`
  - `app_id`
  - `user_id` or tenant/workspace scope when safe
  - optional `recovery_action: "top_up_tokens"`
  - optional `billing_route: "/billing"` or generated route from shell config
- Ensure general mode, workflow transport, and module-triggered LLM paths all
  preserve `TokenUsageDenied` as a user-actionable error rather than a generic
  failure.
- Do not move billing decisions into AG2 middleware. AG2 middleware should only
  ask the runtime guard whether the next LLM call may proceed.

Post-call debit remains event-driven:

```text
ModelResponse.usage
-> TokenManager.emit_usage_delta()
-> UnifiedEventDispatcher chat.usage_delta handlers
-> RuntimeUsageLedger.record_usage_delta()
-> TokenWalletUsageIngestClient.handle_usage_delta()
-> TokenWalletLedger.record_usage_debit()
```

This keeps measurement, wallet spending, and UI streaming separate.

## Generated App Requirements

When AppGenerator produces a monetized AI app, the generated bundle should have
all app-side pieces needed to use the runtime contract:

- `app/config/subscriptions.yaml`
  - `assignment_store`
  - `plans`
  - `usage_limits`
  - `token_wallets`
  - `token_allowances`
  - optional `usage_charge_policies`
- Plan-gated module actions with exact `actions[].entitlement_gate`.
- Billing facade module, such as `modules/billing_portal`, that:
  - lists safe plan metadata
  - starts checkout/top-up through the selected provider adapter
  - reads subscription status
  - reads `/api/me/usage` and `/api/me/tokens`
- Usage page that shows:
  - current balance
  - plan allowance
  - recent usage
  - estimated/billable usage when `usage_charge_policies` exists
- Depleted-balance UI path:
  - recognizes `INSUFFICIENT_TOKENS`
  - shows the current balance if available
  - links to the app billing/top-up route
  - does not retry automatically

Generated apps must not:

- Import payment provider SDKs directly from module business logic unless the
  selected build explicitly owns that provider adapter.
- Create their own usage ledger or wallet ledger.
- Store raw provider customer IDs, payment IDs, secrets, or checkout session
  secrets in app artifacts.
- Bind pages directly to `/api/modules/mozaikspay/...`.

## MozaiksPay Adapter Boundary

MozaiksPay should implement the provider side of the OSS fulfillment contract.

Generated app side:

- Uses `services/integrations/mozaikspay_client.py`.
- Calls `billing_portal` facade actions.
- Receives safe subscription and portal session fields.
- Never sees provider internals.

Hosted/provider side:

- Owns payment provider integration, checkout creation, webhook signature
  verification, provider customer mapping, invoices, refunds, taxes, settlement,
  and credential storage.
- Emits provider-neutral `BillingFulfillmentCommand` to OSS runtime after
  verifying payment facts.

This keeps MozaiksPay valuable as the easiest managed provider while keeping the
cash-to-token loop portable.

## Test Plan

### Unit Tests

- Fulfillment command validation:
  - rejects missing `command_id`
  - rejects negative token amounts
  - rejects secret-shaped metadata
  - rejects unknown event types
- Subscription assignment effect:
  - upserts active assignment
  - cancels assignment without deleting it
  - preserves configured field names from `assignment_store`
- Token credit/debit effect:
  - credits wallet idempotently
  - does not double-credit replayed commands
  - records rejected refund debit when balance is insufficient
- AG2 token guard denial metadata:
  - includes `INSUFFICIENT_TOKENS`
  - includes wallet and balance fields
  - blocks before provider call

### Integration Tests

- Generated SaaS app runtime acceptance:
  - load app with `subscriptions.yaml`
  - apply test fulfillment command
  - verify `/api/me/tokens` balance
  - execute LLM path
  - verify `chat.usage_delta` debits balance
  - exhaust wallet
  - verify next LLM call is denied before provider call
- MozaiksPay adapter smoke:
  - fake verified MozaiksPay payment event
  - translate to fulfillment command
  - apply command
  - verify token wallet credit and plan assignment
- Manual/local adapter smoke:
  - apply a test credit without external payment provider
  - prove OSS loop works without MozaiksPay secrets

### Runtime Smoke

Extend the existing subscription-token runtime smoke so it covers the full
cash-to-token path:

```text
start runtime + Mongo
-> load generated SaaS app
-> apply test billing fulfillment command
-> verify balance
-> run LLM request
-> verify debit
-> spend down balance
-> verify INSUFFICIENT_TOKENS before provider call
```

## Implementation Sequence

1. Completed: add `mozaiksai/core/billing/fulfillment.py` with strict command models and a
   service that applies assignment and wallet effects.
2. Completed: add tests for command validation, idempotency, assignment writes, credit
   writes, cancellation, and refund adjustment behavior.
3. Completed: add a platform/internal route and durable command log for
   applying verified commands.
4. Completed: extend token denial metadata in `TokenUsageDecision` and the transport error
   handling path.
5. Completed: update AppGenerator and `SubscriptionContractDesigner` guidance so monetized
   AI apps generate top-up/billing UX and depleted-balance handling.
6. Completed: update the MozaiksPay build context pack so its facade exposes
   subscription checkout, token status, and token top-up actions without
   directly mutating token wallets from generated app code.
7. Completed: add an OSS route fixture and runtime smoke for local fulfillment
   without real payment-provider secrets.
8. Completed: add architecture docs and an Unreleased changelog entry for
   `0.1.10`.

## Technical Completion Checklist

Remove checklist items as they are completed and covered by tests. Keep the
remaining list honest so `0.1.10` scope does not expand into a full payments
platform.

### OSS Runtime

- [x] Add `mozaiksai/core/billing/fulfillment.py`.
- [x] Define strict `BillingFulfillmentCommand` and result models.
- [x] Reuse existing `TokenWalletLedger`; do not create a second ledger.
- [x] Implement assignment-store upsert/cancel behavior using
  `SubscriptionsConfig.assignment_store`.
- [x] Implement `subscription_activated` and `subscription_updated` effects:
  assignment upsert plus plan allowance materialization.
- [x] Implement `subscription_cancelled` effect: mark inactive/cancelled and
  fall back to default plan through existing entitlement adapter behavior.
- [x] Implement `token_top_up_paid` and `token_credit_granted` effects through
  `TokenWalletLedger.credit()`.
- [x] Implement `refund_applied` and `chargeback_applied` effects through
  `TokenWalletLedger.debit()` with structured partial-failure output when the
  debit is rejected.
- [x] Add idempotency based on `command_id` for wallet effects and replay-safe
  assignment upserts.
- [x] Add safe metadata validation using the same secret-shape policy as token
  wallet entries.
- [x] Add an internal service entrypoint or route for applying verified
  fulfillment commands.
- [x] Add audit/event records for applied, replayed, rejected, and partial
  fulfillment commands.

### AG2 Token Guard and Depletion UX

- [x] Extend `TokenUsageDecision` with user-actionable metadata:
  `recovery_action`, `billing_route`, and safe scope fields.
- [x] Ensure `MozaiksUsageMiddleware` still blocks before provider LLM calls.
- [x] Ensure general mode preserves `INSUFFICIENT_TOKENS` and recovery metadata.
- [x] Ensure workflow transport surfaces token-denial events distinctly from
  generic run failures.
- [x] Ensure generated app/client UI can detect `INSUFFICIENT_TOKENS` and route
  to billing/top-up without retrying automatically.
- [x] Add tests proving provider LLM calls are not made after a preflight denial.

### Factory and Generated Apps

- [x] Update `SubscriptionContractDesigner` to require top-up/billing UX when
  a monetized AI app declares `token_wallets[].auto_debit_usage`.
- [x] Update AppGenerator prompts so billing pages include depleted-balance
  recovery behavior.
- [x] Extend `factory_app/build_context/mozaikspay` templates with app-owned
  facade actions for subscription checkout/top-up if MozaiksPay is selected.
- [x] Ensure generated pages bind to `billing_portal` facade actions, never
  `/api/modules/mozaikspay/...` or hosted internals.
- [x] Ensure generated `subscriptions.yaml` includes token wallets and
  allowances only when the app sells AI usage, credits, quotas, or token packs.
- [x] Add scanner checks that reject app-local wallet ledgers, usage ledgers,
  raw payment provider imports, and direct hosted internals.

### MozaiksPay Hosted Bridge

- [x] Map `hosted_billing` subscription activation/update outcomes to
  `BillingFulfillmentCommand`.
- [x] Add or update provider API routes for generated app checkout/top-up
  session creation.
- [x] Keep provider API responses free of customer IDs, subscription IDs,
  checkout session IDs, provider price IDs, salts, hashes, and secrets.
- [x] Ensure `create_mozaikspay_client`/connector provisioning gives generated
  apps enough config to call the provider API but not enough to mutate runtime
  wallets directly.
- [x] Replace hosted billing subscription activation/update token-credit logic
  with calls to the OSS fulfillment service where practical.
- [x] Preserve MozaiksPay ownership of payment-provider webhooks, checkout
  creation, billing portal creation, provider customer mapping, refunds,
  disputes, taxes, and settlement.
- [x] Wire hosted checkout payment success for token top-ups to emit a verified
  OSS `BillingFulfillmentCommand(event_type="token_top_up_paid")` before the
  provider API returns a usable top-up checkout route.

### Tests and Smoke

- [x] Unit-test fulfillment command validation and secret-shaped metadata
  rejection.
- [x] Unit-test assignment upsert/cancel with a custom `assignment_store`.
- [x] Unit-test token credit/debit idempotency and rejected refund debit.
- [x] Runtime-smoke the cash-to-token enforcement path: apply fulfillment
  command, verify balance, pass token guard, debit wallet, exhaust wallet, and
  receive `INSUFFICIENT_TOKENS`.
- [x] Integration-test generated SaaS app: apply fulfillment command, verify
  balance, run LLM, debit wallet, exhaust wallet, receive
  `INSUFFICIENT_TOKENS`.
- [x] Add MozaiksPay bridge tests in `mozaiks-app` using fake verified hosted
  billing events.
- [x] Add an OSS manual/test adapter smoke so the loop works without real
  MozaiksPay or payment-provider secrets.
- [x] Extend the existing subscription-token Docker/Mongo smoke to include
  fulfillment before usage.

### Documentation and Release

- [x] Update `docs/architecture/mozaiksai/token-management.md` with the final
  fulfillment service once implemented.
- [x] Update MozaiksPay handoff docs in `mozaiks-app` to reference the OSS
  fulfillment contract.
- [x] Add `0.1.10` release notes describing the provider-neutral cash-to-token
  loop.
- [x] Document local/manual top-up smoke instructions.
- [x] Document that MozaiksPay is the default adapter, not the canonical owner
  of wallet or subscription state.

## Non-Goals for 0.1.10

- No full payment platform in OSS.
- No direct Stripe/Paddle implementation unless explicitly selected as a small
  adapter example.
- No invoice, tax, settlement, dispute, payout, or provider customer lifecycle
  engine in OSS runtime.
- No second token ledger.
- No generated app-local usage ledger.
- No AG2-owned billing policy.

## Definition of Done

`0.1.10` is complete when:

- A generated app can declare paid AI token usage through
  `app/config/subscriptions.yaml`.
- A verified provider-neutral fulfillment command can credit the user's token
  wallet.
- A subscription activation can create/update the runtime-readable plan
  assignment.
- AG2 LLM calls are blocked before provider spend when the wallet is depleted.
- Runtime usage events debit token wallets after successful calls.
- Generated app UI has a clear route from `INSUFFICIENT_TOKENS` to top-up or
  upgrade.
- The complete loop works in an OSS smoke test without real payment secrets.
- MozaiksPay remains an adapter path, not the canonical owner of wallet or
  subscription state.

## Release-Candidate Gate

Before publishing any release artifact, run the OSS production gate and the
clean package smoke locally:

```powershell
python scripts\production_readiness_gate.py --quick --skip-frontend
python -m pytest tests\test_release_packaging_contract.py -q --no-cov
```

When Docker Mongo is available, add the real runtime smoke:

```powershell
python scripts\production_readiness_gate.py --quick --skip-frontend --include-docker-smoke
```

For a clean wheel install check, build a wheel, install it into a fresh virtual
environment, and run the same installed-wheel contract smoke used by CI from a
directory outside the source checkout so local files cannot shadow packaged
resources. This is preparation only; do not tag, publish, or create a GitHub
release until the release hold is explicitly lifted.
