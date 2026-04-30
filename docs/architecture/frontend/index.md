# Architecture Overview

Mozaiks is built around one idea: **a single persistent session that follows the user everywhere**.

Whether the user is inside the full chat page, looking at a fullscreen artifact, or browsing a dashboard on a completely separate route — the conversation is always alive, always resumable, and always one tap away. No page reloads. No reconnects. No lost context.

This section documents the UI runtime model that makes that possible.

---

## What makes it unusual

Most chat-based products treat the chat as a page. You navigate to it, you use it, you leave it. Mozaiks treats the chat as a **session layer** that sits beneath the entire application.

The practical difference:

| Conventional approach | Mozaiks approach |
|-----------------------|-----------------|
| Chat is a route you visit | Chat is a session running in the background |
| Leaving the chat page stops the conversation | Leaving the chat page hides the UI, keeps the session |
| One UI surface: the chat page | Two visible surfaces: the full chat page + a floating widget |
| "Open chat" = navigate | "Open chat" = resume the same session state |

---

## The four concepts

The architecture builds from four layered concepts. Read them in order.

| Concept | One-line summary |
|---------|-----------------|
| [UI Surface Model](ui-surface-model.md) | Surface state machine, widget ownership, and session continuity |
| [Conversation Modes](conversation-modes.md) | `ask` mode (general Q&A) vs `workflow` mode (structured agentic run) |
| [Layout Modes](layout-modes.md) | Four visual layouts that control how chat and artifact panels share the screen |
| [Event System](../foundations/event-system.md) | How domain, runtime, `chat.*`, `chat.tool_call`, and `ui.*` events stay separate |
| [AG-UI Comparison](ag-ui-copilotkit-comparison.md) | Where Mozaiks should keep its architecture and where it should converge toward AG-UI/CopilotKit |

---

## Key files

| File | Role |
|------|------|
| `chat-ui/src/state/uiSurfaceReducer.js` | The state machine — all layout and surface transitions live here |
| `chat-ui/src/context/ChatUIContext.jsx` | Single shared provider — exposes all session state to every component |
| `chat-ui/src/pages/ChatPage.js` | The primary chat surface — owns the WebSocket connection lifecycle |
| `chat-ui/src/widget/GlobalChatWidgetWrapper.jsx` | Mounts the widget on non-chat routes; suppresses it on chat routes |
| `chat-ui/src/components/chat/PersistentChatWidget.jsx` | The floating widget component |
| `chat-ui/src/components/chat/FluidChatLayout.jsx` | Animates between layout modes (full / split / minimized / view) |
| `chat-ui/src/ui/hooks/useAppEventBus.js` | In-process bus for typed `ui.*` primitive events and `routing.transition.*` |
| `chat-ui/src/core/WorkflowUIRouter.js` | Dynamic resolver for workflow-owned UI tool components |
