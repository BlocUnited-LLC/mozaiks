# Layout Modes

Layout modes control the visual split between the chat panel and the artifact panel on the chat page. There are four modes. Transitions between them are animated via CSS.

---

## The four modes

| Mode | Chat width | Artifact width | When it applies |
|------|-----------|---------------|----------------|
| `full` | 100% | 0% (hidden) | Ask mode; workflow not yet started |
| `split` | 50% | 50% | Default for workflow mode |
| `minimized` | 10% | 90% | User wants to focus on the artifact |
| `view` | 0% (hidden) | 100% | Artifact fullscreen; widget appears |

---

## `full`

The default state for `ask` mode. The artifact panel does not exist. The user is having a conversation with no visual output. This is also the starting state for a new workflow session before the first artifact event arrives — the chat occupies the full width and the artifact panel slides in when the first `artifact.created` event fires.

`ask` mode is locked to `full`. Requesting any other layout while in `ask` mode is silently ignored by the reducer.

---

## `split`

The default state for `workflow` mode. The screen is divided 50/50 between the chat panel on the left and the artifact panel on the right. This is the layout where most of the workflow interaction happens — the user can read agent output in the chat and see the live artifact update on the right simultaneously.

There are no fixed pixel breakpoints for the 50/50 split — it is a CSS flex proportion set via `--chat-width` and `--artifact-width` CSS custom properties that `FluidChatLayout` writes on the root element.

---

## `minimized`

Set when the user wants the artifact to take up as much space as possible. The chat panel collapses to a 10% sidebar — narrow enough to show agent status indicators without taking up reading space. The artifact gets 90%.

The user typically requests this by clicking a "maximize artifact" toggle in the `ArtifactActionsBar`. The chat input is still accessible in the 10% sidebar, so the user can send messages without switching modes.

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

`FluidChatLayout` reads the current `layoutMode` from context and applies the corresponding CSS custom properties. All resizing is a CSS transition — React does not re-render the layout tree when modes change.

---

## `previousLayoutMode`

When entering `view` mode, the reducer saves the current mode to `previousLayoutMode` in state. The "exit fullscreen" button in the artifact panel restores to `previousLayoutMode` rather than always defaulting back to `split`. This means a user who was in `minimized` before going fullscreen returns to `minimized`, not `split`.

---

## Layout on mobile

On small screens, `FluidChatLayout` and `MobileArtifactDrawer` handle the layout differently — the split/minimized paradigm does not apply because the screen is too narrow for side-by-side panels. Instead, the artifact is presented as a bottom sheet drawer that overlays the chat. The `layoutMode` state is still set the same way; the visual implementation of `split` and `minimized` differs between desktop and mobile.
