# Frontend Architecture

The authoritative target architecture constitution for the native UI framework is
[Mozaiks UI v1](mozaiks-ui-v1.md). Read it first for surface kinds, layer
ownership, registries, the event constitution, security invariants, and its
current-versus-target table.

Mozaiks frontend docs are split into two contracts:

- **Chat UI runtime**: the app-agnostic shell, chat session, widget, layout,
  workflow UI transport, and admin surfaces implemented in `chat-ui`.
- **Generated UI system**: the primitive catalog, page schema contract,
  transition UI contract, and quality gates used by AppGenerator and
  AgentGenerator.

Keeping these separate matters. Chat UI is runtime infrastructure. Generated UI
is the authoring contract agents use to create app surfaces.

## Chat UI Runtime

The `chat-ui` runtime is built around one persistent session layer that follows
the user across routes. Whether the user is in the full chat page, a fullscreen
artifact, or a separate app route, the conversation remains resumable.

Read these when changing the shell, chat page, widget, workflow transport, or
runtime UI event handling.

| Doc | Scope |
| --- | --- |
| [UI Surface Model](chat-ui/ui-surface-model.md) | Surface state machine, widget ownership, and session continuity |
| [Conversation Modes](chat-ui/conversation-modes.md) | `ask` mode versus structured workflow runs |
| [Layout Modes](chat-ui/layout-modes.md) | Chat/artifact layout behavior |
| [Shell System](chat-ui/shell-system.md) | Shell actions, navigation ownership, shortcuts, and route chrome modes |
| [Tool Event Lifecycle](chat-ui/tool-event-lifecycle.md) | `use_ui_tool(...)`, `chat.tool_call`, response handling, and browser rendering |
| [Admin Observability Contract](chat-ui/admin-observability-contract.md) | Framework-owned admin data contract |
| [AG-UI Comparison](chat-ui/ag-ui-copilotkit-comparison.md) | Where Mozaiks should converge with or differ from AG-UI/CopilotKit |

## Generated UI System

The generated UI system is the contract that keeps AppGenerator and
AgentGenerator output deterministic, branded, and production-shaped.

Read these when changing page schemas, primitive exports, workflow UI planning,
transition components, or generated UI quality checks.

| Doc | Scope |
| --- | --- |
| [Generated Frontend Surface Contract](ui-system/generated-frontend-surface-contract.md) | Persistent app UI, workflow UI, transition UI, and bounded custom UI |
| [Workflow UI Primitive Catalog](ui-system/workflow-ui-primitive-catalog.md) | Canonical workflow interaction primitives and shell-owned workflow status |
| [Transition UI Primitives](ui-system/transition-ui-primitives.md) | Transition components used by workflow routing |
| [UI System Quality Gates](ui-system/ui-system-quality-gates.md) | AG2 and browser acceptance gates for generated UI |
| [Event System](../foundations/events-and-data/event-system.md) | How domain, runtime, `chat.*`, `chat.tool_call`, and `ui.*` events stay separate |

## Code editor dependencies

AppGenerator's `CodeEditorPane` loads its local Monaco ESM bundle and workers
when the editor mounts. `monacoEditor.js` configures the React loader before
mounting it, so opening code does not fetch Monaco from a CDN.

Monaco also ships a copied DOMPurify implementation that npm overrides cannot
replace. The shell's Vite resolver redirects only the copied sanitizer import
from Monaco's `domSanitize.js` to `chat-ui/src/utils/monacoDomPurify.js`. This uses the
locked DOMPurify dependency with its own instance, keeping Monaco's temporary
hooks separate from chat sanitization. Monaco is excluded from dependency
prebundling so the same resolver runs in dev and production. Any Monaco upgrade
must pass `tests/monacoEditor.browser.test.js`, including local worker execution,
actual sanitizer version, hook isolation, and absence of CDN requests.

The adapter ships with the shared UI sources, including Android's existing
`chat-ui/src/**` framework snapshot selection.

## Key Files

| File | Role |
| --- | --- |
| `chat-ui/src/state/uiSurfaceReducer.js` | Surface and layout state machine |
| `chat-ui/src/context/ChatUIContext.jsx` | Shared provider for session state |
| `chat-ui/src/pages/ChatPage.js` | Primary chat surface and WebSocket lifecycle |
| `chat-ui/src/widget/GlobalChatWidgetWrapper.jsx` | Widget mounting outside chat routes |
| `chat-ui/src/ui/page-renderer/SectionRenderer.jsx` | Declarative persistent page action execution |
| `chat-ui/src/ui/hooks/useAppEventBus.js` | In-process typed `ui.*` event bus |
| `chat-ui/src/core/WorkflowUIRouter.js` | Dynamic resolver for workflow-owned UI components |
