# Persistent Widget

The persistent widget is the floating chat entry point that appears on every route except the chat page itself. It is the mechanism by which the "session layer beneath the app" becomes tangible to the user.

---

## What it is

A fixed-position button in the bottom-right corner. Tapping it expands into a compact chat panel (26rem wide, 70vh tall) anchored above the button. Tapping the collapse tab or the button again minimizes it.

It is rendered in two places:

| Context | Rendered by | When |
|---------|------------|------|
| Non-chat routes | `GlobalChatWidgetWrapper` | Any route that does not match `/chat` or `/app/:id/:workflow` |
| ChatPage `view` mode | `ArtifactPanel` via `floatingWidget` prop | When `layoutMode === 'view'` |

In both cases the same `PersistentChatWidget` component is rendered. The distinction is only in who mounts it.

---

## Context-adaptive display

The widget does not always show ask-mode messages. It shows whatever the user was last doing.

When the widget expands, it checks whether a workflow is currently active. A workflow is considered active if any of these are true:

- `workflowStatus` in context is not `idle`
- `workflowMessages` array has content
- `activeChatId` is set

If a workflow is active, the widget shows `workflowMessages` by default. This means a user who navigates to a dashboard mid-workflow, opens the widget, and sees their agent conversation exactly where they left it — without navigating back to the chat page.

If no workflow is active, the widget defaults to `askMessages` — the general Q&A history.

The user can switch the widget's local display context using the left header button (see below).

---

## Header layout — always two buttons, never three

The expanded widget header has a strict maximum of two buttons. This is a deliberate constraint to keep the header clean on both mobile and desktop.

### Left button — context-adaptive

| Current display | Left button label | Action |
|-----------------|------------------|--------|
| Showing workflow messages | "Ask Mode" | Switch widget display to ask context inline — no navigation |
| Showing ask messages | "MozaiksAI" / "Chat Station" | Navigate to ChatPage in ask mode |

The left button never navigates away from the current page unless the user is already in ask context, at which point tapping it takes them to the full chat experience.

### Right button — "Back to workspace"

Only rendered when a workflow is active (`hasActiveWorkflow === true`). Navigates to ChatPage in workflow mode, passing `chat_id` and `workflow` as query params so the page resumes the exact session.

Hidden when there is no active workflow — this avoids showing a button that would navigate to an empty workflow page.

---

## Sub-header strip

A thin strip immediately below the header row. It contains one of two things:

| Display context | Sub-header content |
|-----------------|-------------------|
| Workflow messages showing | Workflow name label (e.g. "ActionPlan") — read-only, small uppercase |
| Ask messages showing | `+ New conversation` text button |

The sub-header keeps the header row clean at exactly two buttons while still providing the compose affordance and workflow context label.

---

## Compose: new conversation

Tapping `+ New conversation` in the sub-header:

1. Generates a new local ask session ID (`ask_<uuid>`)
2. Sets `activeGeneralChatId` in context to that ID
3. Clears `askMessages` in context
4. Switches the widget display to ask context

The new session ID signals to ChatPage that when the user next navigates to the chat page, a fresh general chat session should be opened rather than resuming the previous one. The session itself is created lazily on the first WebSocket connection — there is no separate `POST /api/general_chats/start` call.

---

## Sending messages from the widget

### In workflow context

`api.sendMessageToWorkflow()` is called with the active `chat_id` and `workflow_name`. This method checks `api._chatConnections` for an existing live WebSocket registered by ChatPage.

If ChatPage's WebSocket is still open (the user navigated away recently, but the connection has not been garbage collected), the message goes through. If the connection is gone, the call logs a warning: "no live connection — navigate to chat to reconnect." The user's message is still appended optimistically so the UI does not appear to freeze.

### In ask context

Ask messages in the widget are written to the `askMessages` cache locally. The general chat WebSocket lives inside ChatPage, not in the widget. A prompt nudges the user to open the full chat for a live session. The message cache is waiting for them when they get there.

---

## Unread badge

A small dot badge (`w-3.5 h-3.5`) renders on the minimized button when `unreadChatCount > 0`.

The widget increments this count automatically: it tracks the length of `workflowMessages` and `askMessages` in a `useEffect`. When either array grows while the widget is collapsed, the count increases by the number of new messages. The count resets to zero when the user expands the widget.

`unreadChatCount` and `setUnreadChatCount` are both exposed on `ChatUIContext` so any part of the app (including ChatPage's `handleIncoming` callback) can increment the count when a message arrives while the user is elsewhere.

---

## Route suppression

`GlobalChatWidgetWrapper` handles suppression. It reads the current route from React Router and returns `null` on:

- `/chat` and all sub-paths
- `/app/:id/:workflow` and all sub-paths

On all other routes it renders `PersistentChatWidget` with `activeChatId`, `activeWorkflowName`, and `conversationMode` passed as props from context.
