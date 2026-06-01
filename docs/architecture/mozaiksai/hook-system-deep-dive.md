# Hook System Deep Dive

## Overview

Mozaiks uses `hooks.yaml` for AG2 beta prompt injection. The only canonical
workflow hook type is `update_agent_state`.

Canonical declarations live in:

- `workflows/{workflow}/hooks.yaml`

Builder workflows use the same contract under
`factory_app/workflows/{workflow}/hooks.yaml`.

Workflow hook JSON files are not part of the runtime contract.

## Supported Hook Types

`hooks.yaml` supports `update_agent_state` only.

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
2. `create_agents()` resolves `update_agent_state` functions before beta agent
   construction.
3. `MozaiksHookPolicy` runs those functions before each AG2 `agent.ask()` call.
4. If a hook calls `agent.update_system_message(...)`, that message becomes the
   prompt for the current turn.

## Execution Timing

`update_agent_state` runs before agent reply generation. It is for context
injection, prompt guards, and deterministic runtime guidance. Message
transforms, output validation, persistence, and side effects belong in
structured outputs, lifecycle tools, runtime validators, or ordinary tools.

## Troubleshooting

If a hook does not fire:

1. Verify the `hooks.yaml` entry validates.
2. Confirm `hook_agent` exactly matches the runtime agent name.
3. Confirm `filename` and `function` resolve to an importable callable.
4. Confirm the hook calls `agent.update_system_message(...)` when it needs to
   change the current-turn prompt.


