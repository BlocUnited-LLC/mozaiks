# AI Startup

`app/config/ai.json` owns runtime startup for ask mode, chat mode, and workflow
entry. It answers what the AI runtime should open first.

Use `app/config/ai.json` for:

- the ask-mode prompt
- initial ask context variables
- the chat startup mode
- the default workflow entry point

Do not put refinement model policy, refinement routing, provider secrets, or
workflow definitions in this file.

## Starter

```json
{
  "ask": {
    "ask_mode_prompt": "You are the Support Desk assistant. Help users triage requests, draft replies, and update ticket records.",
    "ask_context_variables": null
  },
  "chat": {
    "chat_startup_mode": "ask"
  },
  "workflows": {
    "entry_point": "SupportIntake"
  }
}
```

## Field Notes

| Field | Purpose |
|-------|---------|
| `ask.ask_mode_prompt` | System prompt for ordinary ask/chat startup |
| `ask.ask_context_variables` | Optional initial context variables for ask mode |
| `chat.chat_startup_mode` | Startup mode such as `ask` |
| `workflows.entry_point` | Default workflow folder under `workflows/` |

`app/config/ai.json` does not resume old workflow sessions by itself. Resuming a
previous session requires an explicit session or chat id from the runtime.

## Refinement Boundary

Refinement uses two separate files:

- `app/config/refinement_policy.yaml` for app-local Refinement Engine policy and
  model profile selection.
- `refinement_harness/config/harness.yaml` for optional app-local refinement
  sequences, tools, prompts, and promotion policy.

Keep startup in `ai.json`; keep refinement policy in
`refinement_policy.yaml`.

See also [Extending AI Functionality: Startup Config](../extending-ai-functionality/02-ai-runtime-startup.md).
