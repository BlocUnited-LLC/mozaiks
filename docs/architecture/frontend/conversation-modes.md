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

### Initial mode on first load

The bootstrap effect in ChatPage determines the starting mode using this priority:

1. **URL query param** — `?mode=ask` or `?mode=workflow` always wins (used by widget navigation and deep links)
2. **`startup_mode` from `navigation.json`** — `"ask"` or `"workflow"`. This is the app-level default that controls what the user sees when they first open the app
3. **Fallback** — if `startup_mode` is not set, the app defaults to `workflow`

The HelloWorld example ships with `startup_mode: "ask"`, so the app opens in ask mode. The user can toggle to workflow mode at any time, and the entry_point workflow (HelloWorld) will start automatically.

### Mode changes after load

After the initial bootstrap, the mode is changed by:

- The user clicking the Ask / Workflow toggle in the UI
- A URL query param (`?mode=ask` or `?mode=workflow`) on navigation to ChatPage
- The widget's left button, which navigates to ChatPage with the appropriate mode set
- Backend events that signal a workflow has started (`agui.lifecycle.RunStarted`)

---

## Switching from Ask to Workflow mode

When the user clicks the workflow toggle from ask mode, the handler (`handleConversationModeChange`) follows this sequence:

1. Checks for an existing IN_PROGRESS workflow session (`GET /api/sessions/oldest/{appId}/{userId}`)
2. **If one exists** — resumes it (restores the chat_id, reconnects the WebSocket)
3. **If none exists** — looks up the entry_point workflow (the workflow with `entry_point: true` in its `orchestrator.yaml`) and starts a fresh session for it
4. **If no entry_point workflow is configured** — the toggle does nothing (logs a warning)

The toggle is always safe to click. It either picks up where the user left off or starts a clean session for the designated entry_point workflow.

---

## How `startup_mode` and `entry_point` relate

These are two separate settings that work together:

- **`startup_mode`** (in `navigation.json`) — decides which **mode** the ChatPage opens in. It doesn't know or care about specific workflows.
- **`entry_point`** (in a workflow's `orchestrator.yaml`) — decides which **workflow** runs when the app needs one. It doesn't know or care about modes.

The connection: when `startup_mode` is `"ask"` and the user toggles to workflow mode, the app needs to know *which* workflow to start — that's where `entry_point` comes in. When `startup_mode` is `"workflow"`, the entry_point workflow connects immediately on load.

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
