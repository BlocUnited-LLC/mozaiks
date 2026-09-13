# AI Startup

`app/config/ai.json` starts the app's ask, chat, and workflow behavior.

Use it for:

- the ask-mode system prompt
- initial ask context variables
- the chat startup mode
- the default workflow entry point
- support widget entry points when the app includes operator support

## Minimal Shape

```json
{
  "ask": {
    "ask_mode_prompt": "You help users operate this app.",
    "ask_context_variables": null
  },
  "chat": {
    "chat_startup_mode": "ask"
  },
  "workflows": {
    "entry_point": "ValueEngine"
  }
}
```

## Field Guide

| Field | Purpose |
|-------|---------|
| `ask.ask_mode_prompt` | System prompt for ordinary ask mode. The runtime appends workspace context at call time: app id, user id, the user's current workflow session (from the session router), the current screen's `page_context` when the client sends it, and any host-provided workspace summary from the platform `ask_context` hook (Studio contributes app-registry counts and recent apps). |
| `ask.ask_context_variables` | Optional default context values for ask mode. |
| `chat.chat_startup_mode` | Which chat mode opens first, usually `ask`. |
| `workflows.entry_point` | Default workflow id when the app launches workflow mode. |
| `support.enabled` | Enables support surfaces when the app declares support behavior. |

`app/config/ai.json` is startup config. Refinement model profiles live in
`app/config/refinement_policy.yaml`, and refinement routes live under
`refinement_harness/config/`.

## Read Next

- [Refinement](refinement.md)
- [Add Workflows](../adding-workflows/01-overview.md)
- [Workflow Registry](../extending-ai-functionality/05-workflow-sequences.md)
