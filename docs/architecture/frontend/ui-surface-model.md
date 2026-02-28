# UI Surface Model

The entire Mozaiks UI is built around a single mental model:

> **The user is either inside the chat — or they are somewhere else with the chat running in the background.**

There are not two chat experiences. There is one session. The difference is only whether the full chat interface is visible or a compact floating widget is visible in its place.

---

## Two visible states

### State A — Widget visible

The user is on a non-chat route (a dashboard, settings page, workflow gallery, anything), or they are on the chat page but have expanded the artifact to fullscreen (`view` mode). Either way:

- The full chat UI is **hidden**
- A floating 80×80px widget is **pinned to the bottom-right of the screen**
- The underlying WebSocket session is still **connected and running**
- Any messages or workflow output arriving from the backend are **cached in context** and waiting for the user

Tapping the widget expands it into a compact chat panel directly above the button — no navigation required. The user can read messages, send a reply, and collapse it again without ever leaving the page they were on.

### State B — Widget gone

The user navigated to the chat page (route `/chat`) or to a workflow page (`/app/:id/:workflow`). The `GlobalChatWidgetWrapper` detects these routes and **renders nothing** — the floating button disappears entirely. The user is now inside the full chat interface, which owns the screen.

---

## Why `view` mode is the same as a non-chat route

`view` mode is a layout mode where the artifact panel expands to 100% of the screen width and the chat column disappears. Visually it looks exactly like a non-chat route: there is a fullscreen piece of content and a widget in the corner.

The difference is technical, not visual:

| | Non-chat route | ChatPage `view` mode |
|---|---|---|
| Route | `/dashboard`, `/settings`, etc. | `/chat` |
| Widget mounted by | `GlobalChatWidgetWrapper` | `ArtifactPanel` via `floatingWidget` prop |
| WebSocket | Stays connected | Stays connected |
| Session | Same `chat_id` in context | Same `chat_id` in context |
| User experience | Identical | Identical |

Both scenarios look and behave the same to the user. The distinction only matters to the code that decides *where* to mount the widget.

---

## Session continuity across navigation

One of the deliberate design constraints is that **navigation never restarts the session**.

When the user navigates from a dashboard to the chat page, the `ChatUIContext` — which holds `activeChatId`, `workflowMessages`, `askMessages`, and `workflowStatus` — persists in memory. The chat page reads those values rather than starting from scratch.

When the user navigates away from the chat page, the context stays alive. The WebSocket may close or stay open depending on whether the chat page unmounts, but the `chat_id` and message caches survive.

The result: opening the widget on a non-chat route shows the same conversation the user was in before. There is no "start over" unless the user explicitly composes a new conversation.

---

## What is and is not a surface

The Mozaiks UI provides **two rendered surfaces**, not three:

| Surface | Route context | Owner |
|---------|--------------|-------|
| Full chat interface | `/chat`, `/app/:id/:workflow` | `ChatPage.js` |
| Floating widget | All other routes + `view` mode | `PersistentChatWidget.jsx` |

There is no third surface. The `view` mode within ChatPage is a layout state, not a separate surface — the artifact is fullscreen but the session is unchanged and the widget provides the same entry point the floating button provides on non-chat routes.
