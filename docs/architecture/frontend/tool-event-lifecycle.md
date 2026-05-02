# Tool Event Lifecycle

This document maps the live Mozaiks workflow UI transport to AG-UI and
CopilotKit.

It focuses on the actual implementation path in this repo:

- how `use_ui_tool(...)` becomes frontend UI
- where `chat.tool_call` is canonical
- how the frontend preserves `tool_call` semantics through rendering
- what Mozaiks should keep
- what should be normalized next

## Current live lifecycle

### 1. Interactive workflow UI

Current interactive UI tools follow this runtime path:

```text
workflow tool
  -> use_ui_tool(...)
  -> _emit_tool_call_core(...)
  -> transport.send_tool_call_event(...)
  -> kind=tool_call
  -> UnifiedEventDispatcher maps to chat.tool_call
  -> ChatPage detects component_type
  -> dynamicUIHandler annotates the event with onResponse(...)
  -> ChatPage stores a `toolCall` render object in message/artifact state
  -> WorkflowUIRouter resolves the workflow component
  -> generated React component renders and calls onResponse(...)
  -> websocket tool_call_response
  -> submit_tool_call_response(...)
  -> pending future resolves inside use_ui_tool(...)
```

This is the live path today.

The important point is that the wire contract is `chat.tool_call`, and the
frontend now preserves that shape through rendering instead of inventing a
second event type.

### 2. Auto-tool and fire-and-forget surfaces

Auto-tool execution uses the same render lane, but without waiting for user
response:

- producer: `AutoToolEventHandler`
- transport: `chat.tool_call`
- required hint: `awaiting_response=false`
- completion lane: `chat.tool_response`

This is closer to generated status/artifact rendering than to human-in-the-loop.

### 3. Input requests after normalization

Runtime-generated AG2 input requests now normalize onto the same workflow UI
lane:

- transport event: `chat.tool_call`
- discriminator: `interaction_type=input_request`
- generic fallback component: `UserInputRequest`

That means response-required UI from `InputRequestEvent` now follows the same
render lane as `use_ui_tool(...)`.

`chat.input_request` is not emitted to browser clients for runtime-managed
interactive input.

AG2's generic "Please give feedback to chat_manager..." compatibility prompt is
still the actual response handoff signal. The runtime suppresses that raw prompt
text, but keeps the pending input request live so the user can answer through
the normal workflow composer or `tool_call_response`.

When the AG2 run slice ends after emitting one of these input requests, the
runtime emits `chat.run_complete` with `status=0` and
`reason=awaiting_user_input`. Terminal workflow completion uses `status=1`.

## The key difference from AG-UI and CopilotKit

AG-UI and CopilotKit make the tool call itself the UI interaction contract.

Mozaiks often emits UI from *inside* a workflow tool implementation. That means
there are two related but distinct things:

- the underlying workflow tool invocation
- the UI interaction request emitted by that tool

That is why Mozaiks has historically leaned on a separate UI event id (`corr` /
`toolCallId`) instead of relying only on the original AG2 tool `call_id`.

This is also why AG-UI examples feel cleaner: they collapse those two ideas into
one protocol lifecycle.

## Side-by-side lifecycle mapping

| Concern | AG-UI / CopilotKit | Current Mozaiks | Judgment |
| --- | --- | --- | --- |
| Render request | Tool lifecycle starts on the wire and the frontend renders directly from that tool call | `use_ui_tool(...)` emits `chat.tool_call`; frontend stores a `toolCall` render object in chat/artifact state | Mozaiks should keep the runtime-owned authoring model |
| Tool args | Explicit tool-call argument lifecycle in AG-UI | Fully materialized `payload` is sent in one `chat.tool_call` envelope | Fine for generated app UI, less reusable as a general protocol |
| Response wait | CopilotKit `renderAndWaitForResponse`, AG-UI interrupts/resume | `awaiting_response=true` plus `tool_call_response`; transport resolves either a pending tool-call future or a pending AG2 input callback | Capability is present, but the pause/resume model is still implicit rather than first-class |
| Result delivery | Explicit tool result event | `chat.tool_response` for tool results, plus `chat.tool_call_dismiss` for artifact close | Good enough, but less explicit than AG-UI lifecycle phases |
| Shared state | AG-UI snapshot/delta stream; CopilotKit `useCoAgent` / `useAgent` | No active protocol-level shared-state stream; shell state lives in `ChatUIContext` | Mozaiks should not pretend shell cache equals runtime shared state |
| Frontend registration | Frontend-defined tools and renderer hooks | Workflow-local React component registry through `WorkflowUIRouter` | This is a Mozaiks strength and should stay |
| Primitive UI bus | Usually not the main generative UI lane | Separate `ui.*` primitive lane through `useAppEventBus` | Good separation; keep it separate from workflow UI |

## Current canonical contract

For workflow-owned UI, the current canonical contract should be treated as:

### Authoring contract

- Python side uses `use_ui_tool(...)` for response-bearing UI
- Python side uses `emit_ui_surface(...)` for fire-and-forget UI
- React side is mounted by `WorkflowUIRouter`
- Generated React responds through `onResponse(...)`

### Wire contract

Workflow-owned UI renders through `chat.tool_call`.

Current required fields for workflow UI transport:

```yaml
type: chat.tool_call
data:
  tool_call_id: ui_123
  corr: ui_123
  tool_name: request_api_key
  component_type: AgentAPIKeysBundleInput
  workflow_name: AgentGenerator
  interaction_type: ui_tool
  awaiting_response: true
  display: artifact
  display_type: artifact
  payload:
    workflow_name: AgentGenerator
    interaction_type: ui_tool
    display: artifact
```

Current rules:

- `component_type` is required for workflow UI rendering
- `tool_call_id` is the primary UI interaction id
- `corr` remains the compatibility alias for existing consumers
- `workflow_name` should be available at the top level and in `payload`
- `interaction_type` should be one of `ui_tool`, `ui_surface`, `auto_tool`, or `input_request`
- `awaiting_response=true` means the frontend is expected to answer
- `display` controls `inline`, `artifact`, or `view` behavior

Generic tool invocations that do not carry `component_type` are not workflow UI
surfaces. They are transport/telemetry events, not component-mount requests.

### Response contract

Generated React components answer through:

- websocket `tool_call_response`
- or REST `POST /api/tool-call/respond`

The response is correlated by the UI interaction id, not by raw message text.

This response contract is only for workflow-owned UI.
Persistent app pages that launch workflows do so through declarative page
actions (`action_type: workflow`), not through `tool_call_response`.

## Recommended next normalization

Mozaiks should not switch to CopilotKit wholesale.

It should keep its stronger generated-app model:

- runtime-owned `use_ui_tool(...)`
- workflow-local component registry
- artifact-aware shell layouts
- app/workflow/module composition

But it should normalize the contract around those strengths:

1. `chat.tool_call` stays the only workflow UI render lane.
2. The frontend should keep `tool_call_id` / `tool_name` as the canonical identifiers all the way through render state.
3. `ui.*` stays limited to primitive refresh/update/open/close behavior.
4. response-required interactions should not introduce any second browser-facing lane
   instead of remaining a parallel primary HITL lane.
5. If Mozaiks later needs protocol reuse beyond its own shell, it should evolve
   `chat.tool_call` toward AG-UI-style explicit phases rather than adding more
   side channels.

## Recommended future evolution

The next protocol step should be:

- keep the current authoring model
- keep the `chat.*` namespace if desired
- make the wire lifecycle more AG-UI-shaped

That means:

- explicit call lifecycle fields or events
- one canonical interrupt/resume model
- one canonical shared-state model if shared runtime state is needed
- no overlap between workflow UI rendering and primitive `ui.*` events

The goal is not “become CopilotKit.”

The goal is:

- AG-UI-grade protocol discipline
- plus Mozaiks-grade generated app composition
