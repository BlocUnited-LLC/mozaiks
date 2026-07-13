# Prompt Middleware Deep Dive

## Overview

Mozaiks uses `middleware.yaml` for AG2 1.0 beta prompt injection. Runtime execution
is AG2 1.0 beta middleware, not prior hook registration style.

Canonical declarations live in:

- `workflows/{workflow}/middleware.yaml`

Builder workflows use the same contract under
`factory_app/workflows/{workflow}/middleware.yaml`.

Workflow middleware JSON files are not part of the runtime contract.

## Supported Entries

`middleware.yaml` supports prompt middleware declarations only.

## Declarative Contract

```yaml
prompt_middleware:
  - agent: PlannerAgent
    filename: hook_inject_plan.py
    function: inject_plan_state
```

Fields are required per entry:

- `agent`
- `filename`
- `function`

## Runtime Registration Model

1. `load_prompt_middleware_entries()` reads and validates `middleware.yaml`.
2. `create_agents()` resolves prompt middleware functions before beta agent
   construction.
3. `build_prompt_middleware()` registers a `MozaiksPromptMiddleware`
   factory on the beta `Agent`.
4. The middleware runs during `on_llm_call` and mutates `Context.prompt` for the
   current turn.
5. If middleware calls `agent.update_system_message(...)`, that message becomes the
   prompt for the current model call.

## Execution Timing

Prompt middleware runs before the beta model call for an agent turn. It is
for context injection, prompt guards, and deterministic runtime guidance.
Message transforms, output validation, persistence, and side effects belong in
structured outputs, lifecycle tools, runtime validators, or ordinary tools.

## Troubleshooting

If middleware does not fire:

1. Verify the `middleware.yaml` entry validates.
2. Confirm `agent` exactly matches the runtime agent name.
3. Confirm `filename` and `function` resolve to an importable callable.
4. Confirm the middleware function calls `agent.update_system_message(...)` when it needs to
   change the current-turn prompt.



