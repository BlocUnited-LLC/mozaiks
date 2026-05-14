# MFJ Strict Resume Contract

## Why This Contract Exists

AG2 resume is history-driven. Even if runtime requests a specific resume agent, speaker selection can continue from the previous execution frontier.

To keep MFJ fan-in deterministic in production-style flows, use a strict two-agent resume profile:

1. Resume at a dedicated `resume_entry_agent` (router).
2. Router hands off to the final presenter (`resume_agent`) using runtime-injected context keys.

This keeps orchestration logic in runtime and routing logic in declarative handoffs.

---

## Baseline Runtime Contract (Current Defaults)

For every `mid_flight_journeys[].fan_in` block:

- `resume_agent` is required (final presenter after fan-in).
- `resume_entry_agent` is optional and defaults to `resume_agent`.
- `inject_as` is optional. If omitted, runtime derives a stable `mfj_*` key from the journey/stage id.
- `aggregation_strategy` is optional and defaults to `collect_all`.

Baseline minimal fan-in:

```json
{
  "fan_in": {
    "resume_agent": "HostAgent"
  }
}
```

---

## Strict Resume Profile (Recommended)

When the workflow has staged MFJs, human checkpoints, or multiple resume targets, set resume routing explicitly:

```json
{
  "fan_in": {
    "resume_agent": "HostAgent",
    "resume_entry_agent": "ResumeRouterAgent",
    "inject_as": "mfj_roast_outputs"
  }
}
```

Strict-profile guidance:

- Treat `resume_entry_agent` as a router-only role.
- Keep `inject_as` explicit and stable so downstream prompts can reference it.
- Keep advanced knobs (`on_partial_failure`, timeout, contracts) out of baseline authoring and track them in internal roadmap notes.

---

## Runtime-Injected Context Keys

At fan-in completion, runtime writes merged data and the following keys into parent chat session state:

- `_mfj_resume_pending` (bool)
- `_mfj_resume_target_agent` (string)
- `_mfj_resume_entry_agent` (string)
- `_mfj_resume_nonce` (string)
- `_mfj_resume_consumed_nonce` (string, set by workflow/tool after consumption)
- `_mfj_resume_trigger_id` (string)
- `_mfj_resume_cycle` (int)
- `_mfj_resume_inject_as` (string)
- `_mfj_resume_succeeded_count` (int)
- `_mfj_resume_failed_count` (int)
- `_mfj_resume_timestamp` (ISO string)

Runtime resumes parent at `resume_entry_agent`.

---

## Handoff Template (Required Pattern)

Use expression-based pre-reply conditions from router to each possible target.

```yaml
handoff_rules:
  - source_agent: ResumeRouterAgent
    target_agent: HostAgent
    handoff_type: condition
    condition_type: expression
    condition_scope: pre
    condition: ${_mfj_resume_pending} == true and ${_mfj_resume_target_agent} == "HostAgent" and ${_mfj_resume_nonce} != ${_mfj_resume_consumed_nonce}

  - source_agent: ResumeRouterAgent
    target_agent: user
    handoff_type: after_work
```

If you support multiple presenters, add one conditional rule per target agent with the same predicate shape and a different target string.

---

## Consumption Tool Template

After the presenter consumes the MFJ payload (`inject_as` key), mark the nonce consumed.

```python
from mozaiksai.core.workflow.pack.resume_contract import mark_resume_consumed

async def consume_mfj_resume(context_variables=None):
    ctx = context_variables or {}
    updates = mark_resume_consumed(ctx)
    return {"status": "ok", "updates": updates}
```

Bind this as an agent tool and call it before the presenter hands off to user or continues.

---

## Strict-Profile Checklist

- Every strict-profile MFJ has an explicit `resume_entry_agent`.
- Every strict-profile MFJ has an explicit `inject_as` starting with `mfj_`.
- Workflow has a router agent with expression handoffs using `_mfj_*` keys.
- Presenter calls `mark_resume_consumed` (or equivalent) exactly once per nonce.
- Router includes fallback `after_work -> user` to avoid dead-end loops.
