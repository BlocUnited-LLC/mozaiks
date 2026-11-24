# Layout Modes

Layout modes control the visual split between the chat panel and the artifact panel on the chat page. There are four modes. Transitions between them are animated via CSS.

---

## The four modes

| Mode | Chat width | Artifact width | When it applies |
|------|-----------|---------------|----------------|
| `full` | 100% | 0% (hidden) | Ask mode or a manually collapsed workflow canvas |
| `split` | 50% | 50% | Default for workflow mode |
| `minimized` | 10% | 90% | User wants to focus on the artifact |
| `view` | 0% (hidden) | 100% | Artifact fullscreen; widget appears |

---

## `full`

Ask mode opens with the conversation at full width. In workflow mode, selecting the logo toggle collapses or reopens the canvas. A previously opened artifact stays mounted while hidden so its iframe and local interaction state survive reopening.

`ask` mode is locked to `full`. Requesting any other layout while in `ask` mode is silently ignored by the reducer.

---

## `split`

The shared workflow screen opens with a conversation on the left and an equally wide artifact canvas on the right. Before the first output, the canvas shows the app logo. As the workflow emits artifacts, the same canvas renders them under its header. The active app supplies the logo and optional chat background through `app/brand/theme_config.json`.

The normal `/chat` route provides this screen to Factory, App Zero and generated apps. A page embedding a single workflow can use the public `WorkflowChat` component from `@mozaiks/chat-ui`. When a host explicitly handles artifacts through `onArtifact`, it supplies the destination for those outputs. Workflow tools provide the contents of an artifact through the component registry.

On desktop, `FluidChatLayout` sets each panel's width to 50%. Below 768px,
the artifact uses the mobile drawer presentation instead of side-by-side columns.

---

## `minimized`

Set when the user wants the artifact to take up as much space as possible. The chat panel collapses to a 10% sidebar — narrow enough to show agent status indicators without taking up reading space. The artifact gets 90%.

The user expands the conversation to use its composer. The minimized rail
does not replace or reset the mounted conversation.

---

## `view`

The artifact occupies 100% of the screen. The chat panel is fully hidden. From the user's perspective this is identical to navigating away from the chat page — there is a fullscreen piece of content and the floating widget is pinned bottom-right.

The key difference from `minimized`: in `view` mode the chat column has zero width and the widget is explicitly rendered by `ArtifactPanel` as a `floatingWidget` prop. This allows the user to access the conversation without any layout gymnastics.

`view` mode is requested by artifacts that declare `display_mode: view` or `fullscreen` in their event payload, or by user action via the `ArtifactActionsBar`.

---

## Transitions

Layout mode transitions are managed entirely inside `uiSurfaceReducer.js`. No component sets `layoutMode` directly — they dispatch a `SET_LAYOUT_MODE` action and the reducer decides whether the transition is allowed.

```js
// Dispatch from any component
dispatchSurfaceAction({ type: 'SET_LAYOUT_MODE', mode: 'split' });
```

The reducer enforces the rules:

- `ask` mode → layout is forced to `full` regardless of what is requested
- `view` → sets `previousLayoutMode` so the UI can return to `split` or `minimized` on dismiss
- Invalid mode strings → silently ignored, current mode preserved

`ChatPage` passes the current `layoutMode` to `FluidChatLayout`. React updates
layout props and CSS widths; the chat and artifact retain their component
identity across presentation changes.

---

## `previousLayoutMode`

When entering `view` mode, the reducer saves the current mode to `previousLayoutMode` in state. The "exit fullscreen" button in the artifact panel restores to `previousLayoutMode` rather than always defaulting back to `split`. This means a user who was in `minimized` before going fullscreen returns to `minimized`, not `split`.

---

## Layout on mobile

`FluidChatLayout` owns one persistent chat/artifact composition at every screen
width. `MobileArtifactDrawer` changes presentation inside that composition;
crossing the 768px breakpoint must not replace the artifact subtree or reload
an iframe. No DOM reparenting or second hidden preview is used.

On small screens the artifact appears in a drawer over the conversation.
Opening the drawer moves keyboard focus to its visible Collapse control;
closing it restores the prior trigger or composer after the conversation is
interactive again. If that control is disabled or hidden, focus moves to the
visible conversation pane or its desktop Expand control after a resize.
Collapsing it preserves the preview while removing its controls from pointer,
keyboard and accessibility navigation. Returning to the conversation preserves
an unsent message. Changing or removing the actual artifact may replace its
content; resizing or toggling its presentation must not.

