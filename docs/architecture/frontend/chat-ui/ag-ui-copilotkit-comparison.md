# AG-UI and CopilotKit Comparison

This document compares the current Mozaiks frontend/runtime interaction model
against AG-UI and CopilotKit with AG2.

For the exact local runtime mapping of `use_ui_tool(...)`, `chat.tool_call`,
frontend render state, and response submission, see
[Tool Event Lifecycle](tool-event-lifecycle.md).

It is not a marketing comparison. It is a contract and implementation
comparison intended to answer:

- where Mozaiks is overcomplicated
- where Mozaiks is stronger than AG-UI/CopilotKit
- what should be simplified
- what should stay because it supports generated apps better

## Inputs

External references:

- [AG2 AG-UI overview](https://docs.ag2.ai/docs/user-guide/ag-ui/)
- [AG2 CopilotKit quickstart](https://docs.ag2.ai/docs/user-guide/ag-ui/copilotkit-quickstart/)
- [AG2 backend deep dive](https://docs.ag2.ai/docs/user-guide/ag-ui/backend-deepdive/)
- [AG-UI overview](https://docs.ag-ui.com/introduction)
- [AG-UI events](https://docs.ag-ui.com/concepts/events)
- [AG-UI state management](https://docs.ag-ui.com/concepts/state)

These references are research inputs only. They do not make AG-UI or CopilotKit a
Mozaiks dependency, canonical authority, or adapter roadmap. Any future
interoperability adapter requires a separate ADR and explicit product decision.

Mozaiks implementation references:

- `mozaiksai/core/workflow/ui_tools.py`
- `mozaiksai/core/transport/ui_tools.py`
- `mozaiksai/core/transport/simple_transport.py`
- `mozaiksai/core/events/unified_event_dispatcher.py`
- `mozaiksai/core/workflow/workflow_manager.py`
- `chat-ui/src/pages/ChatPage.js`
- `chat-ui/src/core/WorkflowUIRouter.js`
- `chat-ui/src/core/dynamicUIHandler.js`
- `chat-ui/src/ui/hooks/useAppEventBus.js`
- `chat-ui/src/state/uiSurfaceReducer.js`
- `factory_app/workflows/AgentGenerator/agents.yaml`
- `factory_app/workflows/AppGenerator/agents.yaml`

## Executive Summary

AG-UI and CopilotKit are cleaner because they center everything on one protocol
and one frontend contract:

- one streamed event model
- one tool lifecycle
- one shared state model
- one interrupt model
- one renderer registration model

Mozaiks currently has comparable capabilities, but they are split across too
many parallel paths:

- `chat.*`
- `chat.tool_call`
- typed `ui.*`
- reducer-driven chat/artifact render state built from `tool_call`
- reducer-driven layout state
- widget/session cache behavior
- workflow-local UI registry

That does not mean Mozaiks is worse overall.

Mozaiks is solving a larger problem:

- generated workflow UI
- generated app pages
- persistent pages that can launch workflow sessions without becoming workflow UI
- deterministic module contracts
- domain events and workflow triggers
- artifact-aware shell layout
- hosted product extension surfaces

The conclusion is:

- AG-UI/CopilotKit is better as a transport and frontend interaction model.
- Mozaiks is stronger as an app-generation and app-composition framework.
- Mozaiks should borrow protocol discipline where it fits without committing to
  AG-UI/CopilotKit adapters or throwing away its app/module/workflow composition
  model.

## Side-by-Side Mapping

| Concept | AG-UI / CopilotKit | Current Mozaiks | Assessment | Recommended direction |
| --- | --- | --- | --- | --- |
| Wire protocol | AG-UI standard event stream over HTTP SSE or compatible transport | Custom `chat.*` transport plus direct `ui.*` events and `toolCall` render state in the shell | Mozaiks is more fragmented | Use AG-UI as design pressure for clearer lifecycle semantics; do not adopt an adapter or external wire standard without an ADR |
| Text streaming | `RUN_STARTED`, `TEXT_MESSAGE_*`, `RUN_FINISHED` | `chat.text`, `chat.run_complete`, resume events | Comparable capability | Keep, but normalize naming/structure toward AG-UI |
| Tool lifecycle | `TOOL_CALL_START`, `TOOL_CALL_ARGS`, `TOOL_CALL_END`, `TOOL_CALL_RESULT` | `chat.tool_call`, `chat.tool_response`, completion/dismiss side events | Mozaiks is less explicit | Split tool lifecycle more cleanly or emulate AG-UI chunk/result semantics |
| Frontend tools | UI advertises tools in `RunAgentInput.tools`; frontend executes and renders them | Runtime-owned `use_ui_tool(...)` emits workflow-local components through transport | Ownership model differs | Keep Mozaiks workflow-local generation, but simplify protocol semantics |
| Generative UI | CopilotKit `useComponent`, `useRenderTool`, `useDefaultRenderTool` | `WorkflowUIRouter`, component registry, workflow-local `ui/index.js` barrels | Mozaiks is more generator-friendly, less ergonomic | Keep workflow-local registry, add a simpler top-level render contract |
| Shared state | `STATE_SNAPSHOT`, `STATE_DELTA`, `MESSAGES_SNAPSHOT`; CopilotKit `useCoAgent`/`useAgent` | `ChatUIContext` caches plus artifact/session state in the chat shell | Mozaiks has shell/session state but no canonical AG-UI-style runtime shared-state stream | If shared runtime state is needed, add it as a first-class protocol instead of another ad hoc frontend cache path |
| HITL | Explicit interrupt/pause semantics; CopilotKit `renderAndWaitForResponse`, `useInterrupt` | `use_ui_tool(...)` plus normalized `chat.tool_call` input interactions; the "Awaiting Workflow Reply" banner is suppressed for bare AG2 conversational turns — only shown when the agent explicitly sets a prompt or requests a named component | Mozaiks supports HITL; visual noise for routine AG2 turns is eliminated; pause/resume semantics are still under-modeled | Keep one response-required lane and model pause/resume more explicitly |
| Tool transparency | Built-in default tool renderer and wildcard renderer | Requires workflow UI or custom handling; partial fallback behavior | CopilotKit is cleaner | Add a default renderer for all tool calls |
| Session persistence | AG-UI thread/run model; CopilotKit thread support | `activeChatId`, `askMessages`, `workflowMessages`, artifact cache, widget re-entry | Mozaiks is stronger at shell/session UX | Keep as differentiator |
| App shell/layout | Usually chat-centric with optional custom UI | First-class `ask/workflow/view` layouts and persistent widget | Mozaiks is stronger | Keep |
| Persistent page -> workflow seam | Usually app-specific glue or custom frontend composition | Declarative persistent pages can now launch workflow sessions through `action_type: workflow` while workflow-local React stays on the `chat.tool_call` lane | Mozaiks has the stronger generated-app story here | Keep this split; do not collapse pages into tool renderers |
| Domain event integration | Usually outside the UI framework contract | Built-in module events, reactions, notifications, capability-triggered workflows | Mozaiks is much stronger | Keep |
| Generator fit | Clean for hand-authored apps and UI-defined frontend tools | Strong fit for AgentGenerator/AppGenerator and workflow-local codegen | Mozaiks is stronger for generated apps | Keep |

## Detailed Comparison

### 1. Wire protocol

AG-UI is explicit that the protocol is the standard, lightweight, event-based
connection between user-facing apps and agentic backends. AG2 recommends AG-UI
when you want streaming UI, tool rendering, shared state sync, and a reusable
client ecosystem.

Current Mozaiks equivalent:

- `UnifiedEventDispatcher.build_outbound_event_envelope(...)`
- `SimpleTransport.send_event_to_ui(...)`
- direct typed `ui.*` delivery through `useCoreWebSocket` and `useAppEventBus`

Assessment:

- Mozaiks has the capability.
- Mozaiks does not have one obvious canonical protocol story.

Recommendation:

- Keep `chat.*` as the transport namespace if desired, but make the Mozaiks-owned
  event model lifecycle-explicit. AG-UI semantics are a comparison point, not the
  source of canonical identity.

### 1b. Generated app surface scope

AG-UI and CopilotKit are centered on workflow/session interaction.

Mozaiks also has to define how generated product pages and generated workflow UI
fit together without collapsing into one abstraction.

Current direction:

- persistent app pages stay declarative
- workflow-local React stays on the `chat.tool_call` lane
- persistent pages may launch workflow sessions through `action_type: workflow`
- event-driven workflow starts still use workflow capability ids in
  `event_flows` and `reactions.yaml`

Assessment:

- This separation is stronger for generated apps than a pure CopilotKit-style
  tool-rendering model.
- The important requirement is to keep the identifier boundary clean:
  workflow registry ids for page launches, workflow capability ids for event
  wiring.

### 2. Tool lifecycle

AG-UI has a clean tool model:

- start
- args stream
- end
- result

CopilotKit then layers renderers over that lifecycle.

Current Mozaiks equivalent:

- `mozaiksai/core/transport/ui_tools.py` emits `kind=tool_call`
- `UnifiedEventDispatcher` maps that to `chat.tool_call`
- `ChatPage` inserts artifact or inline UI state
- `WorkflowUIRouter` resolves the component
- `submit_tool_call_response(...)` resolves a pending future

Assessment:

- Mozaiks has a solid interaction primitive in `use_ui_tool(...)`.
- The lifecycle is under-modeled compared with AG-UI.
- `chat.tool_call` currently carries both rendering intent and interaction
  control in one envelope.

Recommendation:

- Keep `use_ui_tool(...)`.
- Refactor transport so response-bearing UI tools follow a stricter lifecycle
  contract.

### 3. Frontend tool ownership

AG2’s AG-UI backend deep dive defines frontend tools as UI-defined tools sent in
`RunAgentInput.tools`. The frontend advertises them, the agent calls them, and
the frontend executes or renders them.

Mozaiks instead makes workflow authors and AgentGenerator declare UI tools in
`tools.yaml`, then generates:

- a Python tool function using `use_ui_tool(...)`
- a workflow-local React component mounted by `WorkflowUIRouter`

Assessment:

- CopilotKit is simpler for app developers.
- Mozaiks is better for deterministic code generation because the generator owns
  both halves of the contract.

Recommendation:

- Keep Mozaiks’ generated two-half contract.
- Borrow CopilotKit’s ergonomics:
  a simpler renderer registration surface and clearer top-level ownership rules.

### 4. Shared state

AG-UI state uses a snapshot/delta pattern, with JSON Patch deltas recommended
for incremental changes.

Current repo reality:

- I could not find any runtime producer emitting `agui.state.*`.
- I could not find any runtime producer emitting `agui.lifecycle.*`.
- The stale frontend consumer hooks from a previous AG-UI attempt were removed.

Mozaiks also has a second shared-state layer in `ChatUIContext`:

- `askMessages`
- `workflowMessages`
- layout and widget state
- artifact cache

Assessment:

- There is no active AG-UI-style shared-state integration in the current
  frontend/runtime contract.
- Mozaiks shell state is real, but it is not the same thing as a protocol-level
  agent state stream.

Recommendation:

- If shared runtime state matters, add it back deliberately as a full
  producer-and-consumer contract.
- Treat `ChatUIContext` as the shell/session layer on top, not as a substitute
  for protocol-level state sync.

### 5. Human-in-the-loop

AG-UI supports interrupt-aware runs. CopilotKit separates:

- graph-enforced interrupts
- LLM-initiated pauses through human-in-the-loop tool flows

Current Mozaiks equivalent:

- `use_ui_tool(...)` waits on a pending response future
- AG2 input requests now normalize onto `chat.tool_call` with
  `interaction_type=input_request`
- completion and dismiss behavior is partly tool-specific
- the "Awaiting Workflow Reply" composer banner is suppressed for bare AG2
  conversational turns — those where component type is `UserInputRequest` and
  no explicit prompt was set; the `tool_call_response` callback remains
  registered regardless

Assessment:

- Mozaiks supports HITL.
- The render lane is now canonical; visual noise for routine AG2 turns is
  eliminated.
- Pause/resume semantics are still not modeled as explicitly as AG-UI.

Recommendation:

- Keep routing response-required interactions through one workflow UI lane.
- Model pauses and resumes more explicitly at the protocol layer.

### 6. Generative UI rendering

CopilotKit gives three clean layers:

- zero-config wildcard tool renderer
- per-tool renderer
- tool-based generative UI where the component itself is the tool

Current Mozaiks equivalent:

- workflow-local component lookup in `WorkflowUIRouter`
- dynamic insertion via `dynamicUIHandler`
- artifact/inline mode handling in `ChatPage`
- primitive `ui.*` events for updating existing widgets

Assessment:

- Mozaiks is more powerful for generated workflow-local UI.
- CopilotKit is significantly easier to reason about.

Recommendation:

- Keep workflow-local generated React surfaces.
- Add a default catch-all renderer for all tool calls.
- Make the renderer path singular:
  reduce `dynamicUIHandler` to a thin response/dispatch bridge.

### 7. Session, shell, and layout

This is where Mozaiks is better than AG-UI/CopilotKit.

Mozaiks has a first-class shell model for:

- `ask`
- `workflow`
- `view`
- widget re-entry
- artifact panel behavior
- chat history restoration

This is not part of AG-UI’s core concern, and CopilotKit examples are usually
chat-first rather than shell-first.

Recommendation:

- Keep this as a Mozaiks differentiator.
- Do not regress to a generic sidebar-chat architecture.

### 8. App generation fit

AgentGenerator is already training the system toward a strong declarative UI
contract:

- Python side must use `use_ui_tool(...)`
- React side must receive `payload`, `onResponse`, `onCancel`
- generated React stays workflow-local
- generated apps also have deterministic page/module/event contracts

This is a bigger and more structured system than CopilotKit’s normal “register a
component and let the agent call it” model.

Recommendation:

- Keep the generator-owned two-part UI contract.
- Simplify the runtime and frontend around it.

## What Mozaiks should borrow directly

Borrow these ideas with minimal resistance:

1. One canonical event stream.
2. One canonical tool lifecycle.
3. One canonical shared-state model.
4. One explicit interrupt/resume model.
5. A default tool renderer so tool activity is never invisible.

## What Mozaiks should keep as differentiators

Keep these because AG-UI/CopilotKit does not replace them:

1. Workflow-local generated React surfaces.
2. Module/domain event integration.
3. Artifact-aware multi-surface shell layout.
4. Deterministic app-generation contracts across pages, modules, workflows, and admin.

## What should be simplified or removed

### Remove as first-class architecture

- extra frontend-only event taxonomies layered on top of `chat.tool_call`

### Collapse into one model

- `use_ui_tool(...)` response flow
- `chat.tool_call`

They should feel like one coherent runtime/frontend protocol, not two separate
ideas.

### Make strictly secondary

- typed `ui.*` primitive bus should remain for primitive refresh/update behavior
  only
- it should not compete with workflow UI tool rendering

## Recommended target architecture

### Layer 1: Protocol

Use Mozaiks-owned semantics informed by AG-UI-style discipline for the canonical
stream contract:

- lifecycle
- text
- tool lifecycle
- state snapshot/delta
- interrupt-aware completion

### Layer 2: Mozaiks runtime integration layer

Mozaiks runtime keeps ownership of:

- workflow declaratives
- module event routing
- artifact persistence
- chat/session persistence
- capability-triggered workflow launch

### Layer 3: Mozaiks shell

Mozaiks frontend keeps ownership of:

- `ask/workflow/view` surfaces
- artifact tray and widget behavior
- app pages and module UI
- workflow-local generated React surfaces

### Layer 4: Generators

AgentGenerator and AppGenerator stay responsible for:

- generated workflow-local UI tools
- generated app pages
- module/event contracts
- keeping UI emission and UI rendering deterministic

## Strict Keep / Merge / Delete Map

| Current Mozaiks concept | Decision | Why |
| --- | --- | --- |
| `use_ui_tool(...)` | Keep | Strong generator contract |
| `emit_ui_surface(...)` | Keep | Good one-way artifact/status primitive |
| `chat.tool_call` | Keep but normalize | Should become the canonical workflow UI transport |
| AG-UI-style shared-state stream | Add only if needed | No active producer/consumer path exists now |
| `ChatUIContext` shell/session state | Keep | Needed for Mozaiks shell UX |
| `WorkflowUIRouter` | Keep | Good workflow-local UI registry seam |
| `ui.*` primitive bus | Keep but narrow | Useful for local primitive updates only |
| second frontend event taxonomy for workflow UI | Delete | `chat.tool_call` already owns the workflow UI lane |
| `dynamicUIHandler` as first-class architecture | Merge downward | Transitional bridge, not the target model |
| second browser-facing response lane | Delete | The canonical lane should stay `chat.tool_call` |

## Code Paths To Change First

If Mozaiks is simplified toward clearer tool-lifecycle semantics, these are the
first change targets:

1. `chat-ui/src/pages/ChatPage.js`
   - keep event handling centered on `chat.tool_call`
2. `chat-ui/src/core/dynamicUIHandler.js`
   - reduce to a thin response/dispatch bridge or remove
3. `chat-ui/src/core/WorkflowUIRouter.js`
   - keep as the single workflow UI mount path
4. `chat-ui/src/ui/hooks/useAppEventBus.js`
   - keep only for typed primitive `ui.*` and routing events
5. `mozaiksai/core/transport/ui_tools.py`
   - align emitted UI tool lifecycle more tightly with AG-UI semantics
6. `mozaiksai/core/workflow/ui_tools.py`
   - preserve generator-friendly helpers while narrowing the transport contract
7. `factory_app/workflows/AgentGenerator/agents.yaml`
   - simplify generator instructions so they target one canonical frontend path

## Final Judgment

Mozaiks is not fundamentally inferior to AG-UI/CopilotKit.

It is currently less elegant at the runtime/frontend contract boundary.

If the goal were only “chat agent plus UI tools”, AG-UI/CopilotKit would be the
better architecture.

But Mozaiks is trying to be:

- an app generator
- a workflow generator
- a module/event platform
- a shell with artifact-aware UX

That broader goal is legitimate.

The right move is not to replace Mozaiks with CopilotKit.

The right move is to make Mozaiks feel more like AG-UI/CopilotKit at the
protocol and frontend contract layer while preserving Mozaiks’ stronger
app-generation model.


