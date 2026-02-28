# Event-Driven State

All UI state in Mozaiks is derived from events. No component owns its own layout or surface state — they dispatch actions, the reducer decides the next state, and components re-render from context.

---

## The state machine

`uiSurfaceReducer.js` is a standard React reducer. It manages a single state tree called `surfaceState` that covers:

- `conversationMode` — `ask` | `workflow`
- `layoutMode` — `full` | `split` | `minimized` | `view`
- `previousLayoutMode` — the mode before entering `view`
- `surfaceMode` — derived: `ASK` | `WORKFLOW` | `VIEW`
- `workflowStatus` — `idle` | `running` | `completed` | `error`
- `artifact.panelOpen` — whether the artifact panel is rendered
- `artifact.status` — `inactive` | `active` | `stale`
- `widget.isWidgetVisible` — whether the floating button should appear
- `widget.widgetOverlayOpen` — whether the expanded widget overlay is open

`ChatUIContext` wraps this reducer and exposes the derived fields as flat values (e.g. `layoutMode`, `workflowStatus`, `surfaceMode`) so components can destructure just what they need.

---

## Two ways to change state

### 1. Dispatch a direct action

For known state transitions that the UI triggers explicitly:

```js
const { dispatchSurfaceAction } = useChatUI();

// User clicked "maximize artifact"
dispatchSurfaceAction({ type: 'SET_LAYOUT_MODE', mode: 'view' });

// User switched to ask mode
dispatchSurfaceAction({ type: 'SET_CONVERSATION_MODE', mode: 'ask' });
```

Available actions:

| Action type | Payload | Effect |
|-------------|---------|--------|
| `SET_CONVERSATION_MODE` | `mode: 'ask' \| 'workflow'` | Changes mode; enforces layout constraints |
| `SET_LAYOUT_MODE` | `mode: 'full' \| 'split' \| 'minimized' \| 'view'` | Changes layout; saves previous if entering `view` |
| `SET_PREVIOUS_LAYOUT_MODE` | `mode` | Restores the saved pre-`view` layout |
| `SET_ARTIFACT_PANEL_OPEN` | `open: boolean` | Opens or closes the artifact panel |
| `SET_WIDGET_MODE` | `value: boolean` | Marks the app as being in widget mode |
| `SET_WIDGET_VISIBILITY` | `value: boolean` | Shows or hides the widget button |
| `SET_CHAT_OVERLAY_OPEN` | `value: boolean` | Toggles the chat overlay |
| `SET_WIDGET_OVERLAY_OPEN` | `value: boolean` | Toggles the widget expanded overlay |
| `WORKFLOW_STATUS` | `status: string` | Updates workflow run status |
| `ARTIFACT_EMITTED` | artifact metadata | Opens artifact panel, sets display mode |
| `ARTIFACT_CLEARED` | — | Closes artifact panel, resets to `full` layout |

### 2. Dispatch from an incoming WebSocket event

For state changes driven by the backend. The `dispatchSurfaceEvent` helper maps an incoming event's `type` field to the appropriate reducer action:

```js
const { dispatchSurfaceEvent } = useChatUI();

// Called inside ChatPage's handleIncoming callback for every WS message
dispatchSurfaceEvent(data); // data = parsed WebSocket event
```

`mapSurfaceEventToAction` in `uiSurfaceReducer.js` performs the mapping:

| Incoming event type | Action dispatched | Effect |
|--------------------|------------------|--------|
| `agui.lifecycle.RunStarted` | `WORKFLOW_STATUS` `running` | Status indicator shows active |
| `agui.lifecycle.RunFinished` | `WORKFLOW_STATUS` `completed` | Status indicator shows done |
| `agui.lifecycle.RunError` | `WORKFLOW_STATUS` `error` | Status indicator shows error |
| `artifact.created` | `ARTIFACT_EMITTED` | Opens artifact panel, switches to `split` layout |
| `artifact.cleared` | `ARTIFACT_CLEARED` | Closes artifact panel, resets to `full` |
| `transport.snapshot` | internal | Hydrates state from a resume snapshot |
| `transport.replay_boundary` | internal | Marks end of replayed events, start of live stream |

Events that have no mapping are silently ignored by `mapSurfaceEventToAction`. This means the WebSocket stream can carry any event type without risk of crashing the state machine.

---

## How an artifact opening works end-to-end

This is the most common non-trivial transition:

1. Backend workflow emits an `artifact.created` event over WebSocket
2. `ChatPage.handleIncoming` receives the event and calls `dispatchSurfaceEvent(data)`
3. `mapSurfaceEventToAction` maps it to `{ type: 'ARTIFACT_EMITTED', ... }`
4. The reducer sets `artifact.status = 'active'`, `artifact.panelOpen = true`, and (if in `full` layout) transitions `layoutMode` to `split`
5. `ChatUIContext` provides the new `surfaceState` to all subscribers
6. `FluidChatLayout` reads `layoutMode` from context and applies the new CSS custom properties — the artifact panel slides in from the right via a CSS transition
7. `ArtifactPanel` reads `artifact.status` and renders the artifact content

The entire path from WebSocket event to visible layout change is: event → reducer → context → CSS transition. React never re-renders the full layout tree for this — only the components that subscribed to the values that changed.

---

## Initial state

`ChatUIContext` initializes `surfaceState` by reading `mozaiks.conversation_mode` from `localStorage`. If the user was in `workflow` mode when they last left, the app opens in workflow mode. If unset, the default is `workflow`.

```js
const [surfaceState, surfaceDispatch] = useReducer(
  uiSurfaceReducer,
  null,
  () => {
    let initialMode = 'workflow';
    try {
      const stored = localStorage.getItem('mozaiks.conversation_mode');
      if (stored === 'ask' || stored === 'workflow') initialMode = stored;
    } catch (_) {}
    return createInitialSurfaceState(initialMode);
  }
);
```

`createInitialSurfaceState` sets the appropriate default layout for the mode (`full` for `ask`, `split` for `workflow`) so the first render is already in the correct state.

---

## Reading state in a component

Every piece of surface state is available via `useChatUI()`:

```js
import { useChatUI } from '../context/ChatUIContext';

function MyComponent() {
  const {
    conversationMode,   // 'ask' | 'workflow'
    layoutMode,         // 'full' | 'split' | 'minimized' | 'view'
    surfaceMode,        // 'ASK' | 'WORKFLOW' | 'VIEW' (derived)
    workflowStatus,     // 'idle' | 'running' | 'completed' | 'error'
    isArtifactOpen,     // boolean
    dispatchSurfaceAction,
    dispatchSurfaceEvent,
  } = useChatUI();
}
```

Components should read from context and dispatch actions — they should never manage layout or surface state locally.
