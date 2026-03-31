# Hook System Deep Dive

## Overview

mozaiksai uses AG2 hook points plus declarative registration from `hooks.yaml`.

Canonical hook declarations live in:

- `platform/workflows/{workflow}/hooks.yaml`

No workflow hook JSON files are used in the canonical runtime contract.

## Supported Hook Types

`hooks.yaml` supports:

- `process_message_before_send`
- `update_agent_state`
- `process_last_received_message`
- `process_all_messages_before_reply`

## Declarative Contract

```yaml
hooks:
  - hook_type: update_agent_state
    hook_agent: PlannerAgent
    filename: hook_inject_plan.py
    function: inject_plan_state
```

Fields are required per entry:

- `hook_type`
- `hook_agent`
- `filename`
- `function`

## Runtime Registration Model

1. `load_hook_entries()` reads and validates `hooks.yaml`.
2. For `update_agent_state` entries:
   - functions are pre-loaded before `ConversableAgent(...)` construction.
   - hooks are passed through `update_agent_state_before_reply`.
3. Other hook types are registered via `register_hook(...)` post-construction.

This split exists because AG2 `update_agent_state_before_reply` behavior is
bound during agent setup.

## Execution Timing

- `update_agent_state`: before agent reply generation.
- `process_all_messages_before_reply`: before reply using full message list.
- `process_last_received_message`: before reply, focused on latest message.
- `process_message_before_send`: right before outbound send.

## Troubleshooting

If a hook does not fire:

1. Verify the `hooks.yaml` entry validates.
2. Confirm `hook_agent` exactly matches the runtime agent name.
3. Confirm `filename` and `function` resolve to an importable callable.
4. Check agent hook snapshots from runtime logs (`register_hooks` diagnostics).
