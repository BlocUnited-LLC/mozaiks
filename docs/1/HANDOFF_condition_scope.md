# Handoff Condoition Scope Implementation Guide

## Problem
Handoff conditions using `after_works` were evaluated immediately after an agent's turn, before user interactions could update context variables. When a user clicked "Approve Plan", the UI tool set `action_plan_acceptance = "accepted"`, but the handoff condition had already been evaluated and missed the state change.

## Solution
Use AG2's `context_conditions` with `condition_scope: "pre"` to evaluate handoff conditions **before** each agent's reply, ensuring context changes from user interactions are caught.

## Implementation

### Handoff Configuration (workflows/Generator/handoffs.json)
```json
{
  "source": "user",
  "targets": [
    {
      "agent": "ContextVariablesAgent",
      "condition": "${action_plan_acceptance} == \"accepted\"",
      "condition_type": "expression",
      "condition_scope": "pre"
    },
    {
      "agent": "ActionPlanArchitect",
      "condition": "When the user requests changes, revisions, or adjustments to the action plan",
      "condition_type": "llm"
    }
  ]
}
```

**Key Points:**
- Source is `user` (evaluates when user acts, not when ProjectOverviewAgent finishes)
- First condition: Expression-based, checks approval state, `condition_scope: "pre"` ensures pre-reply evaluation
- Second condition: LLM-based, naturally understands revision intent without keyword parsing

### Context Variables (workflows/Generator/context_variables.json)
Only `action_plan_acceptance` is needed:
```json
{
  "name": "action_plan_acceptance",
  "type": "string",
  "default": "pending",
  "description": "Tracks whether user has accepted the action plan"
}
```

**Removed:** `revision_requested` variable (LLM handles intent naturally)

### Lifecycle Tools (workflows/Generator/tools.json)
Only `collect_api_keys` remains. 

**Removed:** `detect_revision_request` tool (LLM condition replaced keyword detection)

## How It Works

### Approval Flow
1. ProjectOverviewAgent shows diagram → hands off to user
2. User clicks "Approve Plan" → UI tool sets `action_plan_acceptance = "accepted"`
3. AG2's `_run_oncontextconditions` hook runs before user's next turn
4. Condition `${action_plan_acceptance} == "accepted"` evaluates to True
5. Auto-transfer to ContextVariablesAgent → workflow continues

### Revision Flow
1. ProjectOverviewAgent shows diagram → hands off to user
2. User types "Can you add an approval layer?"
3. AG2 evaluates LLM condition: "When the user requests changes..."
4. LLM recognizes revision intent → evaluates to True
5. Auto-transfer to ActionPlanArchitect → revises plan → updates diagram → back to user

## Testing
1. Clear logs: `Remove-Item logs\logs\mozaiks.log -Force`
2. Start backend: `.\start-dev.ps1`
3. Watch for handoff registration logs:
   - `✅ [HANDOFFS] Added PRE-REPLY context handoff user->ContextVariablesAgent`
   - `✅ [HANDOFFS] user: llm=1 ctx_pre=1`
4. Complete intake → approve plan → verify auto-transfer
5. Try revision request → verify transfer to ActionPlanArchitect

## AG2 Architecture Notes
- **context_conditions**: Evaluated by `_run_oncontextconditions` hook before agent replies
- **llm_conditions**: Evaluated during reply generation
- **after_works**: Evaluated after agent completes turn (snapshot-based, doesn't re-evaluate)
- **condition_scope**: `"pre"` = pre-reply, `"post"` = after-reply (default)

## Benefits
- ✅ Catches UI interactions that update context variables
- ✅ Natural language intent detection (no keyword parsing)
- ✅ AG2-native patterns (no custom machinery)
- ✅ 50+ lines of complexity removed
