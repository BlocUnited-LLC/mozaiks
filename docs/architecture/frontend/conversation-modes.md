# Conversation Modes

Every session in Mozaiks operates in one of two conversation modes. The mode controls what kind of chat the user is having, which layout is used, and which message cache is active.

---

## The two modes

### `ask` mode

A lightweight, general-purpose Q&A conversation — think of it like a floating assistant that can answer questions about anything related to the user's context. There is no artifact panel, no structured workflow, and no visual output beyond the chat messages themselves.

- Layout is always `full` (chat takes 100% of the screen)
- The artifact panel is hidden
- Messages are stored in `askMessages` in context
- Driven by the `GeneralChatSessions` collection on the backend

### `workflow` mode

A structured agentic run. The user is working through a defined workflow — a multi-agent process that produces visual output (a form, a table, a card, a composite artifact). The artifact panel is open alongside the chat.

- Default layout is `split` (chat 50% + artifact 50%)
- The artifact panel is visible and receives streaming updates
- Messages are stored in `workflowMessages` in context
- Driven by the `ChatSessions` collection and the AG2 workflow runner on the backend

---

## How the mode is set

The mode is stored in `ChatUIContext` as `conversationMode` and persisted to `localStorage` under `mozaiks.conversation_mode` so it survives page reloads.

It is changed by:

- The user clicking an "Ask Mode" or "Workflow Mode" toggle in the UI
- A URL query param (`?mode=ask` or `?mode=workflow`) on navigation to ChatPage
- The widget's left button, which navigates to ChatPage with the appropriate mode set
- Backend events that signal a workflow has started (`agui.lifecycle.RunStarted`)

---

## Mode and layout are separate

Conversation mode and layout mode are two different things that happen to influence each other. The rules:

| Situation | Enforced layout |
|-----------|----------------|
| `conversationMode === 'ask'` | Always `full` — ask mode has no artifact panel |
| `conversationMode === 'workflow'` | Default `split`; user can change to `minimized` or `view` |
| `view` layout requested in `ask` mode | Blocked — `ask` mode is locked to `full` |

This enforcement happens inside `uiSurfaceReducer.js`. When a `SET_CONVERSATION_MODE` action fires, the reducer also resets the layout to the appropriate default for that mode.

---

## Derived value: `surfaceMode`

`surfaceMode` is a read-only derived value computed from the combination of `conversationMode` and `layoutMode`. It represents the semantic meaning of the current state in terms a component can act on without needing to reason about both values.

| `conversationMode` | `layoutMode` | `surfaceMode` |
|--------------------|-------------|--------------|
| `ask` | `full` | `ASK` |
| `workflow` | `split` or `minimized` | `WORKFLOW` |
| any | `view` | `VIEW` |

`surfaceMode` is exposed on `ChatUIContext` and is used primarily by the `ArtifactPanel` and `FluidChatLayout` to decide what to render.

---

## Message caches

Each mode has its own message array in context. They are independent and both survive navigation:

```js
const {
  askMessages,      // Q&A messages — active in ask mode
  workflowMessages, // Workflow agent messages — active in workflow mode
} = useChatUI();
```

Switching modes does not clear the other mode's messages. If the user was in a workflow conversation, switches to ask mode to ask a question, then switches back — both histories are intact.
