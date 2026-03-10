# Token Manager Integration Plan

_Last updated: 2025-11-22_

## 1. Objectives & Personas

| Persona | Needs | Token Responsibilities |
| --- | --- | --- |
| **Mozaiks Platform Operator** | Keep generator workflow usage in-budget, enforce subscription plans, surface balances in MozaiksPay | Provision wallets, enforce runtime gates (start + per-turn), send UI nudges, expose analytics |
| **Generated App Owner** | Ship automations that can meter their own customers and optionally monetize | Opt-in token context vars, configure free-trial or max-turn rules per workflow, wire token exhaustion into handoffs |
| **End Users of Generated Apps** | Predictable limits, clear upgrade upsells, no silent failures | Receive inline warnings, see balance/usage, resume chats after top-up |

Success criteria:
1. Token accounting remains centralized (MozaiksPay + PersistenceManager) with no per-workflow duplication.
2. Chats pause safely when wallet balance is insufficient, with resumable state and UI prompts.
3. Generator workflows gain declarative hooks (`free_trial_enabled`, `max_consecutive_auto_reply`, token handoffs) without custom coding.

---

## 2. Runtime Integration Strategy

### 2.1 TokenManager Component

Create `core/tokens/manager.py` responsible for:
- `ensure_can_start_chat(user_id, app_id, workflow_name, estimate_tokens=None)`
   - Checks `MONETIZATION_ENABLED`, `FREE_TRIAL_ENABLED`, wallet balance, plan concurrency.
  - Returns approval, warning payload, or raises `InsufficientTokensError`.
- `handle_turn_usage(chat_id, usage_snapshot)`
  - Receives prompt/completion totals each agent turn (integration point: `AG2PersistenceManager.update_session_metrics`).
  - Debits tokens via `PersistenceManager.debit_tokens`.
  - Emits `runtime.token.warning` or `runtime.token.exhausted` events when thresholds reached.
  - Sets `ChatSessions.paused=true` + `pause_reason="insufficient_tokens"` when exhausted.
- `resume_after_topup(chat_id)`
  - Clears pause flag, emits `runtime.token.resumed`, resumes workflow execution via `SimpleTransport`.
- `handle_auto_reply_limit(chat_id, limit)`
   - Mirrors the generator-provided `max_consecutive_auto_reply` value. When the runtime detects the chat hit that bound it writes diagnostics, nudges the UI, and yields control back to AG2 so the conversation ends naturally.

### 2.2 Lifecycle Hooks

1. **Start Gate** – `shared_app.start_chat()`
   - Call `TokenManager.ensure_can_start_chat` before chat_id creation.
   - If blocked, raise HTTP 402 equivalent with payload for Chat UI to show paywall.
2. **In-Chat Gate** – `AG2PersistenceManager.update_session_metrics`
   - Replace inline debit logic with `TokenManager.handle_turn_usage`.
   - When event -> pause, push `token_exhausted` through WebSocket (existing `token_exhausted` handling in `ChatPage.js`).
3. **Resume Flow**
   - Add `/api/chats/{chat_id}/resume` endpoint that verifies wallet top-up and calls `resume_after_topup`.

### 2.3 Events & Transport

- Event types (via `unified_event_dispatcher`):
  - `runtime.token.warning`
  - `runtime.token.exhausted`
  - `runtime.token.resumed`
   - `runtime.token.auto_reply_limit`
- `SimpleTransport` listens and forwards as WebSocket payloads with `type: token_warning|token_exhausted|token_resume|auto_reply_limit`.
- Chat UI already disables composer on `token_exhausted`; extend to show CTA button wired to MozaiksPay link (payload from event).

### 2.4 Persistence & Analytics

- `ChatSessions` already has `paused`, `pause_reason`, `paused_at`; ensure TokenManager updates these fields.
- `WorkflowStats` gains `token_warnings`, `token_pauses`, `auto_reply_limit`, and `auto_reply_hits` counters for dashboards.
- Wallet debits remain atomic via `PersistenceManager.debit_tokens`; extend metadata to include `reason` (runtime_pause/auto_reply_limit).

### 2.5 Configuration Matrix

| Env Var | Purpose | Default |
| --- | --- | --- |
| `MONETIZATION_ENABLED` | Enables TokenManager gating + MozaiksPay debits | `true` |
| `FREE_TRIAL_ENABLED` | Platform-level flag surfaced to generators | `true` |
| `TOKEN_WARNING_THRESHOLD` | Percentage balance or absolute tokens to warn | `0.2` (20%) |
| `TOKENS_API_URL` | Upgrade/top-up endpoint that Chat UI links to | env-specific |

---

## 3. Generator / Workflow Touchpoints

Runtime token counts stay entirely server-side. Generators only need to know whether monetization is enabled and whether the chat is operating under a free trial.

### 3.1 Context Variables & Structured Outputs

- Always inject `free_trial_enabled` value into `ContextVariablesPlan` (state/config, sourced from `FREE_TRIAL_ENABLED`). Even when monetization is disabled we keep the variable for consistency; its value simply evaluates to `false`.
- Keep the existing platform flags (`MONETIZATION_ENABLED`, `app_id`, `user_id`) available the same way other permanent context variables are threaded into `orchestration_patterns.py`.
- No `token_balance`, `token_warning_threshold`, or `turns_consumed` variables should be surfaced to agents. Those remain runtime-only signals.
- Structured outputs that previously referenced token metrics should instead gate logic on `free_trial_enabled` or `monetization_enabled` booleans.

### 3.2 Agent Guidance Updates

- `agents.json` entries for planning-focused agents (notably `WorkflowImplementationAgent`) must include a directive to **determine where the free-trial split occurs** whenever `MONETIZATION_ENABLED=true`. This includes naming the phase/agent that owns the split and providing any metadata the runtime can later map to AG2 settings.
- Update prompts so ActionPlan/Implementation steps describe *how* the free-trial branch is enforced. Remove all references to token quantities, balance checks, or premium tiers.
- The generator output must tag phases with `monetization_scope` (`"free_trial"`, `"paid"`, or `"shared"`) and set `free_trial_entry=true` on the phase where the split occurs. These tags live inside the existing ActionPlan semantic wrapper so UI and runtime logic can read them without guessing.
- `update_agent_state_pattern.py` remains a generator-only helper: it can keep deriving state variables for the free-trial branch but must not leak into runtime. Future workflows that skip this generator function should still operate because the runtime does not depend on it.
- When the WorkflowImplementationAgent defines the free-trial split it should also assign a declarative `max_consecutive_auto_reply` value (see §5.1) that we can extract and hand directly to AG2 without inventing new runtime switches.

### 3.3 Handoffs & Auto Reply Limits

- Handoffs should reference the free-trial branch (e.g., `condition: free_trial_enabled == true`) only when the workflow explicitly branches. Do **not** add derived conditions that reference token balances or runtime pause states.
- Runtime-based pauses (insufficient tokens, max usage) never terminate the workflow through handoff logic; TokenManager will pause/resume independently. Handoff definitions should stay focused on business logic transitions.
- `max_consecutive_auto_reply` values defined by the generator flow will be translated into AG2 `GroupChat` parameters so we still respect conversation bounds without inventing `${turns_consumed}` variables.

### 3.4 Free Trial Flow in Generated Apps

- If `MONETIZATION_ENABLED=false`, agents ignore the free-trial directives; the runtime still sends the context variable but it evaluates to `false`.
- If `MONETIZATION_ENABLED=true` and `free_trial_enabled=true`, the ActionPlan must describe which phases or tools execute for free users and where the paid path begins. This information feeds both WorkflowImplementationAgent prompts and the Handoff agent so downstream logic can reference the same branch labels.
- Generator-produced UI components (CTA, upsell modals) continue to use existing `token_top_up_prompt` scaffolding, but their activation is tied purely to the free-trial branch decisions rather than token math.

### 3.5 Free-Trial Split Tagging & ActionPlan UI

- **Data tagging** – The ActionPlan wrapper should include:
   - `workflow.free_trial_split`: `{ phase_index, identifier, rationale }` describing where the split is enforced.
   - Each `phase` gains `monetization_scope` (`"free_trial"`, `"paid"`, `"shared"`) plus an optional `free_trial_entry` boolean when that phase terminates the free tier.
   - Agent rows inherit the surrounding phase scope so prompts and downstream artifacts stay in sync without inventing separate schemas.
- **UI surfacing** – `ChatUI/src/workflows/Generator/components/ActionPlan.js` already renders chips for trigger/pattern metadata. Extend the phase header rendering so a `SemanticChip` (or new badge) calls out `Free Trial` vs `Paid` using the new `monetization_scope` field, and add a small divider or icon when `free_trial_entry=true` so product teams immediately see where the split happens.
- **Handoff alignment** – The same tags feed the HandoffsAgent so it can reference `monetization_scope` instead of brittle text parsing. UI and runtime remain consistent because they read the identical flag.

---

## 4. Implementation Checklist

1. **TokenManager module**
   - Add `core/tokens/manager.py` with `ensure_can_start_chat`, `handle_turn_usage`, `handle_auto_reply_limit`, and `resume_after_topup` APIs.
   - Persist pause metadata via `AG2PersistenceManager` and emit `runtime.token.*` events through `unified_event_dispatcher`.
2. **Runtime wiring**
   - `shared_app.start_chat` calls `ensure_can_start_chat` when `MONETIZATION_ENABLED=true`.
   - `AG2PersistenceManager.update_session_metrics` invokes `handle_turn_usage` and forwards AG2 usage snapshots.
   - Add `/api/chats/{chat_id}/resume` route that clears pauses once MozaiksPay confirms payment.
3. **Transport + analytics**
   - Extend `SimpleTransport` to fan out `token_warning`, `token_exhausted`, `token_resume`, and `auto_reply_limit` payloads.
   - Update `WorkflowStats` + dashboards with `auto_reply_limit`/`auto_reply_hits` counters and MozaiksPay metadata.
4. **Chat UI (ChatPage + ActionPlan)**
   - Chat composer listens for the new transport events and links to `TOKENS_API_URL` for upsells.
   - `ChatUI/src/workflows/Generator/components/ActionPlan.js` renders free-trial vs paid badges using `phase.monetization_scope` and highlights the split when `free_trial_entry=true`.
5. **Generator + prompts**
   - Ensure `ContextVariablesPlan` always includes `free_trial_enabled` + `MONETIZATION_ENABLED`.
   - Update `agents.json`, WorkflowImplementationAgent, HandoffsAgent, and `update_agent_state_pattern.py` to emit the new tagging metadata plus `max_consecutive_auto_reply` assignments.
   - Structured outputs and orchestration templates consume the tags without referencing raw token counts.
6. **QA / validation**
   - Smoke test: free-trial workflow (monetization on/off), auto-reply limit enforcement, paywall/resume flow, Ask Mozaiks availability while workflow chat is paused.

---

## 5. Decisions & Clarifications

1. **max_consecutive_auto_reply Mapping** – The generator (preferably the WorkflowImplementationAgent while defining the free-trial split) will assign a declarative `max_consecutive_auto_reply` value. Runtime simply forwards that value into AG2 `GroupChat`/agent configs so the limit is enforced without inventing new counters.
2. **Token Provider** – All runtime debits flow through MozaiksPay; we will not expose an abstract provider hook until a concrete need arrives. This keeps enforcement centralized and prevents partially implemented billing paths.
3. **Ask Mozaiks Availability** – Even when workflow chats are paused for token reasons, the Ask Mozaiks widget must stay active across every page (outside `ChatPage.js`). Transport guards will therefore block only workflow chat sessions while leaving Ask Mozaiks untouched.

---

## 6. Next Steps

1. Review/approve this plan.
2. Implement TokenManager + runtime wiring.
3. Update generator artifacts (context vars, handoffs, structured outputs).
4. Polish UI flows and add e2e tests for pause/resume + max turns.
