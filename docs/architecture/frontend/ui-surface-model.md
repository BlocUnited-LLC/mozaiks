# UI Surface Model

The frontend runtime is organized around one persistent session layer and a
small state machine that decides which surface is visible.

The canonical owner is `chat-ui/src/state/uiSurfaceReducer.js`. `ChatUIContext`
stores the reducer state plus the session caches that survive navigation:

- `activeChatId`
- `activeWorkflowName`
- `askMessages`
- `workflowMessages`
- `workflowStatus`

## Core rule

There is one session, not separate chat products.

The user is either:

- on the full chat surface
- on another route with the same session available through the widget
- in workflow `view` layout, where the artifact takes over the screen and the
  widget becomes the re-entry point back into chat

## Visible surfaces

| Surface | When visible | Owner |
| --- | --- | --- |
| Full chat surface | `/chat` and workflow chat routes | `chat-ui/src/pages/ChatPage.js` |
| Floating widget | non-chat routes | `chat-ui/src/widget/GlobalChatWidgetWrapper.jsx` + `chat-ui/src/components/chat/PersistentChatWidget.jsx` |
| Floating widget in `view` layout | fullscreen artifact inside ChatPage | `ArtifactPanel` via `floatingWidget` |

`view` is not a third conversation product. It is a workflow layout state.

## Surface state machine

The reducer tracks three related concepts:

### Conversation mode

- `ask` means general chat
- `workflow` means an active workflow run

`ask` always collapses to `full` layout because it does not own an artifact
panel.

### Layout mode

- `full` means chat only
- `split` means chat plus artifact
- `minimized` means chat compressed while artifact remains visible
- `view` means fullscreen artifact with widget re-entry

### Widget state

The reducer also tracks widget visibility separately from route ownership:

- `isInWidgetMode`
- `isWidgetVisible`
- `isChatOverlayOpen`
- `widgetOverlayOpen`

`GlobalChatWidgetWrapper` suppresses widget rendering on primary chat routes and
enables it on other routes.

## Event-driven surface changes

Frontend surfaces are not changed by ad hoc component logic alone. The reducer
reacts to event classes:

| Event family | Effect on surface |
| --- | --- |
| `chat.tool_call` with `display=artifact|view|fullscreen` | opens artifact surface |
| legacy `ui_tool_event` with artifact/view display | same as above, compatibility path |

This mapping lives in `mapSurfaceEventToAction(...)`.

## Session continuity

Navigation must not discard the active session.

Current implementation:

- `ChatUIContext` survives route changes
- `ChatPage` restores cached `askMessages` and `workflowMessages`
- artifact state is cached for restoration
- the widget reads the same shared caches instead of opening a second session

That is why a user can leave `/chat`, browse elsewhere, and still reopen the
same run from the widget.

## Widget contract

The widget is the session entry point outside the full chat surface.

### Where it renders

| Context | Mounted by |
| --- | --- |
| non-chat routes | `GlobalChatWidgetWrapper` |
| ChatPage `view` layout | `ArtifactPanel` |

### What it shows

The widget follows the active context:

- if a workflow is active, it defaults to `workflowMessages`
- otherwise it shows `askMessages`

The user can switch to ask context inline without navigating.

### Header contract

The expanded widget keeps a two-button header:

- left button: switch to ask context or open the full ask chat
- right button: return to the active workflow chat when one exists

The compose affordance for ask mode lives in the sub-header as `+ New conversation`.

### Route suppression

`GlobalChatWidgetWrapper` returns `null` on:

- `/chat`
- `/chat/*`
- `/app/:id/:workflow`

That keeps the widget from competing with the full chat surface.

## Canonical implementation files

- `chat-ui/src/state/uiSurfaceReducer.js`
- `chat-ui/src/context/ChatUIContext.jsx`
- `chat-ui/src/pages/ChatPage.js`
- `chat-ui/src/widget/GlobalChatWidgetWrapper.jsx`
- `chat-ui/src/components/chat/PersistentChatWidget.jsx`
