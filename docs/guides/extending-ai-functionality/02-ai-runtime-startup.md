# AI Runtime Startup

`app/config/ai.json` owns runtime startup for `ask`, `chat`, and `workflows`.
It does not carry control-plane policy.

Use it for:

- `ask.ask_mode_prompt`
- `ask.ask_context_variables`
- `chat.chat_startup_mode`
- `workflows.entry_point`
- `workflows.resume_policy`

Example:

```json
{
  "ask": {
    "ask_mode_prompt": "You are the Mozaiks assistant. Help users shape, generate, connect, and refine apps in Mozaiks Studio using the shared builder workflows.",
    "ask_context_variables": null
  },
  "chat": {
    "chat_startup_mode": "ask"
  },
  "workflows": {
    "entry_point": "ValueEngine",
    "resume_policy": "last_active_then_oldest_then_entry_point"
  }
}
```

Keep `ask`, `chat`, and workflow startup here. Do not move startup behavior into
`control_plane/config/control_plane.yaml`.
