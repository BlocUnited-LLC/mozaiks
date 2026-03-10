# UI Runtime Architecture — Consolidated Reference

**Status**: Canonical Source of Truth  
**Last Verified**: February 27, 2026  

**Goal**: Single authoritative reference for all UI runtime behavior in mozaiks-core — surface state, layout, artifact rendering, widget continuity, event mapping, AG-UI alignment status, and open gaps.

---

## 1 — Visual Architecture Reference

These diagrams reflect the actual built implementation as of February 27, 2026.

---

### 1.1 — Overall Model

The UI has one conceptual toggle: **widget visible** (user is "away" from chat) or **widget gone** (user is inside the chat). `layoutMode='view'` is the in-session equivalent of being on a non-chat route — the artifact is fullscreen, the chat UI is hidden, and the floating widget is pinned bottom-right. When the user clicks a widget button and navigates to ChatPage in full/split/minimized mode, the widget disappears because they are now inside the chat.

```
╔══════════════════════════════════════════════════════════════════════════════╗
║               MOZAIKS UI — ONE SESSION, TWO VISIBLE STATES                  ║
╠══════════════════════════════════════════════════════════════════════════════╣
║                                                                              ║
║  STATE: WIDGET VISIBLE  (user is "away" from chat)                           ║
║  ════════════════════════════════════════════════                            ║
║                                                                              ║
║  A) Non-chat route  (/dashboard, /settings, /workflows, …)                  ║
║     [Host app page content — full screen]          ┌──────────────┐         ║
║                                                    │   [M logo]   │         ║
║                                                    │   80 × 80px  │         ║
║                                                    └──────────────┘         ║
║     GlobalChatWidgetWrapper renders PersistentChatWidget                     ║
║                                                                              ║
║  B) ChatPage — layoutMode='view'  (identical visual)                         ║
║     [ArtifactPanel — 100% width]                   ┌──────────────┐         ║
║                                                    │   [M logo]   │         ║
║                                                    │   80 × 80px  │         ║
║                                                    └──────────────┘         ║
║     Host passes PersistentChatWidget as ArtifactPanel `floatingWidget` prop  ║
║     Session stays live — no navigation, no WebSocket restart                 ║
║                                                                              ║
║  In both cases: click widget → expands → BOTH buttons always present:        ║
║     🧠 Chat Station → navigate('/chat?mode=ask')                             ║
║     logo  Resume   → navigate('/chat?mode=workflow&…')                       ║
║                                                                              ║
╠══════════════════════════════════════════════════════════════════════════════╣
║                                                                              ║
║  STATE: WIDGET GONE  (user is inside the chat)                               ║
║  ═════════════════════════════════════════════                               ║
║                                                                              ║
║  ChatPage — layoutMode = full / split / minimized                            ║
║  ┌──────────────────────────────────────────────────────────────────────┐   ║
║  │  Chat UI is visible. Widget is suppressed by GlobalChatWidgetWrapper. │   ║
║  │                                                                      │   ║
║  │   full        — chat 100%, no artifact (Ask mode)                    │   ║
║  │   split       — chat 50% + artifact 50%                              │   ║
║  │   minimized   — chat 10% sidebar + artifact 90%                      │   ║
║  └──────────────────────────────────────────────────────────────────────┘   ║
║                                                                              ║
╚══════════════════════════════════════════════════════════════════════════════╝
```

Shared state across all modes via `ChatUIContext`:
- `conversationMode` / `layoutMode` / `surfaceMode`
- `activeChatId` / `activeWorkflowName`
- `askMessages` / `workflowMessages` (per-mode caches, survive navigation)
- Same WebSocket session — changing modes never restarts the run

---

### 1.2 — Widget: Minimized → Expanded → Full Chat

```
MINIMIZED                     EXPANDED                        CHATPAGE (full/split/minimized)
─────────                     ────────                        ──────────────────────────────

 fixed bottom-right            fixed bottom-right              /chat route — widget is GONE
 visible on: non-chat routes   w-[26rem] h-[70vh]              GlobalChatWidgetWrapper
 + ChatPage view mode                                          suppresses it here — user
                                                               is now inside the chat
┌────────────┐                ┌──────────────────────┐
│            │                │  ╲__________╱  tab   │
│  [M logo]  │  ──click──►    ├──────────────────────┤
│  80 × 80px │                │ 🧠 MozaiksAI  [logo] │
│            │                │ Chat Station  [btn]  │
└────────────┘                ├──────────────────────┤
 Mozaiks logo                 │                      │
 rounded 2xl                  │  ChatInterface       │
 gradient bg                  │  (no header,         │
 shadow glow                  │   offline-capable)   │
                              │                      │
                              └──────────────────────┘
                               rounded-2xl, rounded-tr-none

                              Left header btn (🧠):           Right header btn (logo):
                              → navigate('/chat?mode=ask')    → navigate('/chat?mode=workflow
                                                                 &chat_id=...&workflow=...')
                              Widget disappears on arrival.   Resumes persisted run.
```

Widget rendered by `GlobalChatWidgetWrapper` on non-chat routes, and via the `floatingWidget`
prop on `ArtifactPanel` when `layoutMode='view'`. Suppressed by `GlobalChatWidgetWrapper` on
`/chat` and `/app/:id/:workflow` routes (user is inside the chat — no widget needed).

---

### 1.3 — ChatPage: 4-Mode Fluid Layout (Desktop)

`FluidChatLayout` renders these four modes with smooth CSS transitions (`transition-all duration-500`):

```
layoutMode = 'full'                      layoutMode = 'split'
surfaceMode = ASK (or no artifact)       surfaceMode = WORKFLOW
─────────────────────────────────        ────────────────────────────────────────
┌────────────────────────────────┐       ┌─────────────────┬──────────────────┐
│                                │       │                 │                  │
│                                │       │  ChatInterface  │  ArtifactPanel   │
│       ChatInterface            │       │                 │                  │
│       (100% width)             │       │    50% width    │    50% width     │
│                                │       │                 │                  │
│                                │       │                 │                  │
└────────────────────────────────┘       └─────────────────┴──────────────────┘


layoutMode = 'minimized'                 layoutMode = 'view'
surfaceMode = WORKFLOW (artifact focus)  surfaceMode = VIEW (fullscreen artifact)
─────────────────────────────────        ────────────────────────────────────────
┌──┬─────────────────────────────┐       ┌────────────────────────────────────┐
│  │                             │       │                                    │
│M │                             │       │                                    │
│o │     ArtifactPanel           │       │         ArtifactPanel              │
│z │                             │       │         (100% width)               │
│a │      90% width              │       │                                    │
│i │                             │       │                                    │
│k │                             │       │                  ┌───────────────┐ │
└──┴─────────────────────────────┘       │                  │  floatingWidget│ │
 10%                                     │                  │  (bottom-right)│ │
 Chat sidebar shows vertical             └──────────────────┴───────────────┘ ┘
 "MozaiksAI" text + "M" avatar           Chat is fully hidden (0% width).
 (click to expand)                       Host passes PersistentChatWidget as the
                                         `floatingWidget` prop on ArtifactPanel.
                                         Pinned at absolute bottom-0 right-0 z-60.
                                         Widget ALWAYS shows both buttons:
                                           🧠 → Ask full-screen
                                           logo → Workflow resume
                                         User is never stranded — both exits
                                         are always present in view mode.
```

Gap between panes: `gap-2 p-2` in split/minimized, `gap-0 p-0` in view mode.

---

### 1.4 — Mobile: Drawer Model

`MobileArtifactDrawer` replaces the side-by-side layout on mobile. Chat always occupies the primary screen; the artifact drawer slides up from the bottom.

```
DRAWER STATE = 'hidden'          DRAWER STATE = 'peek'            DRAWER STATE = 'expanded'
────────────────────────         ────────────────────────         ───────────────────────────
┌────────────────────┐           ┌────────────────────┐           ┌────────────────────┐
│                    │           │                    │           │                    │
│                    │           │  ChatInterface     │           │                    │
│  ChatInterface     │           │                    │           │  ArtifactPanel     │
│  (full screen)     │           ├────────────────────┤           │  (near full-height)│
│                    │           │  [≡ Artifact  ▲]  │           │  h-[calc(100vh-5r)]│
│                    │           │  peek strip h-20   │           │                    │
└────────────────────┘           └────────────────────┘           └────────────────────┘
 Drawer height = 0              Drawer height = h-20,             Drawer height = expanded
                                rounded-[2rem] pill shape         rounded-t-3xl

viewMode = true (layoutMode = 'view'):
────────────────────────
┌────────────────────┐
│                    │
│  ArtifactPanel     │   h-screen, drawer toggle disabled,
│  (full screen)     │   rounded-none, rounded-t-3xl
│                    │
└────────────────────┘
```

---

### 1.5 — Ask vs Workflow vs View Layout Comparison

NOTE: ASK and WORKFLOW are `conversationMode` values. VIEW is a `layoutMode` value — it is a layout state inside ChatPage, not a separate surface. A workflow can still be active when `layoutMode='view'`; the floating widget always provides an exit to both Ask and Workflow.

```
╔═══════════════════════════════╦════════════════════════════════╦══════════════════════════════════╗
║   ASK  (conversationMode)     ║   WORKFLOW  (conversationMode) ║   VIEW  (layoutMode='view')      ║
╠═══════════════════════════════╬════════════════════════════════╬══════════════════════════════════╣
║ layoutMode    full            ║ split / minimized              ║ view                             ║
║ surfaceMode   ASK             ║ WORKFLOW                       ║ VIEW                             ║
║ Chat pane     100% visible    ║ 50% or 10% visible             ║ hidden (0%)                      ║
║ Artifact pane hidden          ║ 50% or 90% visible             ║ 100% visible                     ║
║ Floating widget  none         ║ none                           ║ ALWAYS present — both buttons    ║
║                               ║                                ║ 🧠 Ask  +  logo Workflow resume  ║
╠═══════════════════════════════╬════════════════════════════════╬══════════════════════════════════╣
║ Quick Q&A, general chat       ║ Agentic runs + artifact panel  ║ Fullscreen artifact presentation ║
║ Layout guard: non-full banned ║ No layout guard                ║ Workflow may still be active     ║
║ Widget btn → navigate here    ║ Logo btn → navigate here       ║ Widget always exits to ask OR    ║
║                               ║                                ║ workflow — never a dead-end      ║
╚═══════════════════════════════╩════════════════════════════════╩══════════════════════════════════╝
```

---

## 2 — Table of Contents

- Section 3: State model — the three domains, derivation rules, hard invariants
- Section 4: Layout mode semantics — desktop widths and mobile drawer behavior
- Section 5: Canonical state transitions — the full action table
- Section 6: Event-to-surface mapping — what backend events drive what reducer actions
- Section 7: Core artifact primitives — the `core.*` rendering pipeline
- Section 8: Ask / Workflow / Widget continuity — shared context and persistent widget
- Section 9: `ChatUIContext` API surface — what the context exposes to consumers
- Section 10: Backend event model — how the Python runtime produces domain events
- Section 11: AG-UI alignment status — what is done, what gaps remain
- Section 12: Artifact actions status — frontend done, backend gap
- Section 13: Core vs App ownership rules
- Section 14: Capability inventory — do not rebuild what already exists

---

## 3 — Canonical State Model

### State Domains

| Domain | Allowed Values | Authoritative File |
|--------|----------------|--------------------|
| `conversationMode` | `ask`, `workflow` | `packages/frontend/chat-ui/src/state/uiSurfaceReducer.js` |
| `layoutMode` | `full`, `split`, `minimized`, `view` | `packages/frontend/chat-ui/src/state/uiSurfaceReducer.js` |
| `surfaceMode` | `ASK`, `WORKFLOW`, `VIEW` | `packages/frontend/chat-ui/src/state/uiSurfaceReducer.js` |
| `artifact.status` | `inactive`, `active`, `stale` | `packages/frontend/chat-ui/src/state/uiSurfaceReducer.js` |
| `workflowStatus` | `idle`, `running`, `completed`, `error` | `packages/frontend/chat-ui/src/state/uiSurfaceReducer.js` |

### Derivation Rules (implemented in `deriveSurfaceMode`)

1. `surfaceMode = VIEW` whenever `layoutMode === 'view'`.
2. Otherwise, `surfaceMode = ASK` when `conversationMode === 'ask'`.
3. Otherwise, `surfaceMode = WORKFLOW`.

### Hard Invariants

1. Ask mode cannot render split/minimized/view — guarded in `SET_LAYOUT_MODE` and `SET_CONVERSATION_MODE`.
2. Artifact panel is considered open whenever `layoutMode !== 'full'`.
3. `ARTIFACT_EMITTED` always forces `conversationMode = 'workflow'` and sets artifact to active.
4. `display=view|fullscreen` maps to `layoutMode='view'` (full artifact surface, chat hidden).
5. `ARTIFACT_CLEARED` always resets to `layoutMode='full'` regardless of conversation mode.
6. `previousLayoutMode` is never set to `'view'` — it only tracks non-view states for restoration.

### Initial State

`conversationMode` is restored from `localStorage('mozaiks.conversation_mode')` on mount. Defaults to `'workflow'`. The initial `layoutMode` is `'split'` for workflow and `'full'` for ask.

---

## 4 — Layout Mode Semantics

### Desktop Layout Contract

Implemented in `packages/frontend/chat-ui/src/components/chat/FluidChatLayout.jsx`.

| `layoutMode` | Chat Width | Artifact Width | Chat Visible | Artifact Visible | Floating Widget | Intended Use |
|-------------|------------|----------------|--------------|------------------|-----------------|--------------|
| `full` | 100% | 0% | yes | no | no | Ask-only conversation or no active artifact |
| `split` | 50% | 50% | yes | yes | no | Main workflow chat + artifact side-by-side |
| `minimized` | 10% | 90% | yes (sidebar) | yes | no | Artifact-focused workflow editing |
| `view` | 0% | 100% | no | yes | yes — via `floatingWidget` prop on `ArtifactPanel` | Fullscreen artifact; host passes chat widget as `floatingWidget` |

### Mobile Layout Contract

Implemented in `packages/frontend/chat-ui/src/components/chat/MobileArtifactDrawer.jsx`.

Mobile uses a drawer/panel model instead of fixed widths:

| Drawer State | Behavior |
|-------------|----------|
| `hidden` | Drawer not visible |
| `peek` | Drawer header visible as strip at bottom |
| `expanded` | Drawer open to near full-height |
| `viewMode=true` | Forces `h-screen`, bypasses toggle — used when `layoutMode='view'` |

`ArtifactPanel` accepts `isMobile`, `isEmbedded`, and `viewMode` props for correct responsive rendering.

---

## 5 — Canonical State Transitions

Implemented in `packages/frontend/chat-ui/src/state/uiSurfaceReducer.js`.

| Trigger | Reducer Action | Guaranteed Outcome |
|--------|----------------|--------------------|
| User selects Ask | `SET_CONVERSATION_MODE('ask')` | `layoutMode='full'`, `surfaceMode='ASK'`, panel closed, active artifact → stale |
| User selects Workflow | `SET_CONVERSATION_MODE('workflow')` | Restores `previousLayoutMode` (or `'split'`), panel opens |
| Host requests layout | `SET_LAYOUT_MODE(mode)` | Normalized; ask-mode guard forces `'full'` if conversation is ask |
| User opens artifact panel | `SET_ARTIFACT_PANEL_OPEN(true)` | If `full`, switches to `split`; panel opens |
| User closes artifact panel | `SET_ARTIFACT_PANEL_OPEN(false)` | Switches to `full`; panel closes |
| Toggle artifact panel | `TOGGLE_ARTIFACT_PANEL` | Delegates to `SET_ARTIFACT_PANEL_OPEN(!current)` |
| Artifact/tool event | `ARTIFACT_EMITTED` | `conversationMode='workflow'`, artifact active, panel open |
| Artifact requests fullscreen | `ARTIFACT_EMITTED(display='view'|'fullscreen')` | `layoutMode='view'`, `surfaceMode='VIEW'` |
| Workflow ends or errors | `WORKFLOW_STATUS('completed'|'error')` | Artifact status → `'stale'` |
| Artifact cleared | `ARTIFACT_CLEARED` | Artifact inactive, panel closed, `layoutMode='full'` |
| Widget mode toggle | `SET_WIDGET_MODE(value)` | Updates `widget.isInWidgetMode` |
| Widget visibility | `SET_WIDGET_VISIBILITY(value)` | Updates `widget.isWidgetVisible` |
| Chat overlay | `SET_CHAT_OVERLAY_OPEN(value)` | Updates `widget.isChatOverlayOpen` |
| Widget overlay | `SET_WIDGET_OVERLAY_OPEN(value)` | Updates `widget.widgetOverlayOpen` |

---

## 6 — Event-to-Surface Mapping

`mapSurfaceEventToAction(event)` is exported from `uiSurfaceReducer.js` and used by `ChatUIContext.dispatchSurfaceEvent()`.

It maps inbound runtime events to reducer actions:

| Inbound Event Type | Condition | Dispatched Action |
|-------------------|-----------|-------------------|
| `ui_tool_event` / `UI_TOOL_EVENT` | `display` is `artifact`, `view`, or `fullscreen` | `ARTIFACT_EMITTED` with resolved display + eventId |
| `tool_call` / `chat.tool_call` | `display` is `artifact`, `view`, or `fullscreen` | `ARTIFACT_EMITTED` with resolved display + eventId |
| `agui.lifecycle.RunStarted` | — | `WORKFLOW_STATUS('running')` |
| `agui.lifecycle.RunFinished` | — | `WORKFLOW_STATUS('completed')` |
| `agui.lifecycle.RunError` | — | `WORKFLOW_STATUS('error')` |
| Anything else | — | `null` (no-op) |

Display field resolution checks: `event.display`, `event.display_type`, `event.mode`, `event.data.display`, `event.data.payload.display` (in order). Valid display values: `artifact`, `inline`, `view`, `fullscreen`.

---

## 7 — Core Artifact Primitives

All implemented. Files in `packages/frontend/chat-ui/src/primitives/`.

| Artifact Type | Component | Description |
|--------------|-----------|-------------|
| `core.card` | `CoreCard.jsx` | Single content card with optional actions |
| `core.list` | `CoreList.jsx` | Ordered/unordered list items |
| `core.table` | `CoreTable.jsx` | Tabular data display |
| `core.form` | `CoreForm.jsx` | Input form with validation |
| `core.composite` | `CoreComposite.jsx` | Nested container of other primitives |
| `core.markdown` | `CoreMarkdown.jsx` | Rendered markdown content |

`PrimitiveRenderer.jsx` routes payloads to the correct component by inspecting `artifact_type`. When `isCoreArtifactPayload()` returns true, `ArtifactActionsBar` is suppressed (core primitives handle their own action rendering).

---

## 8 — Ask / Workflow / Widget Continuity

Ask and Workflow are two modes over **one shared runtime context**. The widget exposes the same AI session from any route.

### Shared Context

`ChatUIContext` (at `packages/frontend/chat-ui/src/context/ChatUIContext.jsx`) is the single provider. It persists:
- `conversationMode` / `layoutMode` / `surfaceMode` — the full surface FSM
- `activeChatId` / `activeWorkflowName`
- `askMessages` / `workflowMessages` — per-mode message caches that survive navigation
- `currentArtifactContext` — currently rendered artifact `{ type, payload, id }`
- Widget state: `isInWidgetMode`, `isWidgetVisible`, `isChatOverlayOpen`, `widgetOverlayOpen`

### Widget Continuity

| Component | File | Role |
|-----------|------|------|
| `PersistentChatWidget` | `src/components/chat/PersistentChatWidget.jsx` | Minimized/floating chat that persists across routes |
| `useWidgetMode` | `src/hooks/useWidgetMode.jsx` | Hook managing widget open/close and mode transitions |
| `GlobalChatWidgetWrapper` | `src/widget/GlobalChatWidgetWrapper.jsx` | Renders persistent widget outside primary chat routes |

### Widget Chat Behavior (Current)

When the widget is open (view mode or non-chat route), it renders a `ChatInterface` wired to `askMessages` from `ChatUIContext`. This means:

- The widget **always shows the most recent Ask conversation** the user was in — it persists across route changes and view mode transitions.
- The user can **continue chatting** in the widget inline without navigating anywhere.
- The left header button (🧠) navigates to full Ask mode on ChatPage — widget disappears.
- The right header button (logo) navigates to the most recent Workflow — widget disappears.

### GAP: No "New Ask Chat" from Widget

Currently, there is no way to **start a fresh ask conversation** from within the widget. The widget always resumes the last ask session. To create a new ask chat, the user must navigate to the full Ask mode ChatPage.

This is a known UX gap. The widget header has room for a third action (e.g., a compose/new button). Recommended design:
- Add a "New chat" icon button to the widget header
- Calls `setAskMessages([])` and optionally `setActiveChatId(null)` to clear the session
- Does NOT navigate — user stays in widget/view mode with a clean slate
- The new chat is promoted to a full ask session if the user later navigates to ChatPage

See §12 for gap tracking.

### Ask vs Workflow

| Surface | `conversationMode` | Typical Layout | Artifact Panel |
|---------|-------------------|----------------|----------------|
| Ask | `ask` | `full` | never shown |
| Workflow | `workflow` | `split` or `minimized` | shown |
| View | either | `view` | fullscreen; workflow may still be active; widget shows both exit buttons + inline ask chat |

Ask and Workflow share the same WebSocket session. Switching modes does not create a new session — it only changes how the surface renders.

---

## 9 — `ChatUIContext` API Surface

Everything exposed by `useChatUI()`:

```js
// User
user, setUser, loading, initialized
agentSystemInitialized, workflowsInitialized

// Persistent chat
activeChatId, setActiveChatId
activeWorkflowName, setActiveWorkflowName
chatMinimized, setChatMinimized
unreadChatCount, setUnreadChatCount

// Surface / layout FSM (all backed by uiSurfaceReducer)
layoutMode, setLayoutMode
previousLayoutMode, setPreviousLayoutMode
conversationMode, setConversationMode
surfaceMode                        // derived (read-only)
workflowStatus                     // 'idle' | 'running' | 'completed' | 'error'
surfaceState                       // full raw reducer state (read-only)
isArtifactOpen, setIsArtifactOpen
dispatchSurfaceAction(action)      // raw reducer dispatch
dispatchSurfaceEvent(event)        // maps event via mapSurfaceEventToAction then dispatches

// Widget state
isInWidgetMode, setIsInWidgetMode
isWidgetVisible, setIsWidgetVisible
isChatOverlayOpen, setIsChatOverlayOpen
widgetOverlayOpen, setWidgetOverlayOpen

// Artifact context
currentArtifactContext, setCurrentArtifactContext  // { type, payload, id }

// Message caches (per-mode, navigate-safe)
askMessages, setAskMessages
workflowMessages, setWorkflowMessages

// Chat sessions
activeGeneralChatId, setActiveGeneralChatId
generalChatSummary, setGeneralChatSummary
generalChatSessions, setGeneralChatSessions
workflowSessions, setWorkflowSessions

// Navigation
pendingNavigationTrigger, setPendingNavigationTrigger

// Host-provided services
config            // resolved from uiConfig prop
auth              // authAdapter instance
api               // apiAdapter instance
uiToolRenderer    // host-provided UI tool renderer function

// Actions
logout()
```

`localStorage` keys managed by the context:
- `mozaiks.conversation_mode` — persists mode across page reloads
- `mozaiks.current_chat_id`
- `mozaiks.current_workflow_name`

---

## 10 — Backend Event Model

The Python backend has been fully refactored. The old `SimpleTransport` / `event_serialization.py` / `use_ui_tool()` stack no longer exists.

### Current Architecture

| Layer | Package | Key File | Responsibility |
|-------|---------|----------|----------------|
| Contracts | `mozaiks_kernel` | `contracts/events.py` | `DomainEvent`, `EventEnvelope` |
| Contracts | `mozaiks_kernel` | `contracts/runner.py` | `RunRequest`, `ResumeRequest` |
| Port | `mozaiks_kernel` | `ports/ai_runner.py` | `AIWorkflowRunnerPort` (Protocol) |
| Runtime surface | `mozaiks_core` | `api/app.py` | Single FastAPI factory, run lifecycle endpoints |
| AI bridge | `mozaiks_core` | `engine/facade.py` | `AIEngineFacade` — dynamic import of `mozaiks_ai` |
| Streaming | `mozaiks_core` | `streaming/hub.py` | `RunStreamHub` — WebSocket pub/sub |
| Persistence | `mozaiks_core` | `persistence/store.py` | `EventStore` |
| Checkpoints | `mozaiks_core` | `persistence/checkpoints.py` | `CheckpointStore` |
| Runner | `mozaiks_ai` | `runner.py` | `KernelAIWorkflowRunner` |
| Adapters | `mozaiks_ai` | `adapters/mock.py`, `adapters/ag2.py` | Mock and AG2 runners |

### Domain Event Types (canonical)

The runner emits `DomainEvent` objects. Current event types from `KernelAIWorkflowRunner`:

| `event_type` | When Emitted |
|-------------|-------------|
| `Run.Started` | Top of every `run()` call |
| `Run.Cancelled` | If run_id was cancelled before execution |
| `Run.Completed` | Successful completion |
| `Run.Failed` | Runner or adapter error |

These map to AG-UI lifecycle semantics (see Section 10).

### What No Longer Exists

The following from the Jan 2026 platform alignment response are **gone**:
- `SimpleTransport`, `event_serialization.py`
- `use_ui_tool()`, `UIToolEvent`, `emit_tool_progress_event()`
- `unified_event_dispatcher.py`, `BusinessLogEvent`
- `chat.*` event namespace on the wire
- `orchestration.*` event namespace

Do not reference these in new code or docs.

---

## 11 — AG-UI Alignment Status

The frontend already handles `agui.lifecycle.*` event types in `mapSurfaceEventToAction`. The backend does not yet emit them by name — the runner emits `Run.Started`, `Run.Completed`, `Run.Failed`.

### Current Alignment

| AG-UI Event | Frontend Handles? | Backend Emits? | Gap |
|-------------|------------------|----------------|-----|
| `agui.lifecycle.RunStarted` | Yes (`WORKFLOW_STATUS('running')`) | No (emits `Run.Started`) | Backend needs `event_agui_adapter.py` or rename |
| `agui.lifecycle.RunFinished` | Yes (`WORKFLOW_STATUS('completed')`) | No (emits `Run.Completed`) | Same |
| `agui.lifecycle.RunError` | Yes (`WORKFLOW_STATUS('error')`) | No (emits `Run.Failed`) | Same |
| `TextMessageStart` / `TextMessageEnd` | Not handled | Not emitted | Future streaming work |
| `ToolCallStart` / `ToolCallEnd` | Not handled | Not emitted | Future streaming work |
| `StateSnapshot` / `StateDelta` | Not handled | Not emitted | Future work |
| `MessagesSnapshot` | Not handled | Not emitted (replayed from DB) | Future work |

### Recommended Approach (Not Yet Built)

Add `event_agui_adapter.py` in `mozaiks_core` that wraps `DomainEvent` emissions into `agui.*` namespaced envelopes. This keeps the backend contract stable and gives the frontend a predictable `agui.*` event namespace.

**Do not rename existing `DomainEvent.event_type` values** — that is a kernel contract change requiring coordination.

### View Mode

View mode (`layoutMode='view'`) is **fully implemented** in the frontend. No gap.

---

## 12 — Artifact Actions Status

### Frontend — Partially Implemented

`ArtifactActionsBar.jsx` (`packages/frontend/chat-ui/src/components/actions/ArtifactActionsBar.jsx`) is implemented and wired into `ArtifactPanel`.

What works:
- Reads `actions[]` from artifact payload (filtered to `scope !== 'row'`)
- Renders buttons with styles: `primary`, `secondary`, `ghost`, `danger`
- Sizes: `sm`, `md`, `lg`; `dense` mode
- `confirm` prop triggers `window.confirm()` before calling
- Calls `onArtifactAction(action, contextData)` on click
- Tracks action status via `actionStatusMap` with `pending` / `started` loading state
- Shows loading spinner while action is in-flight

What does not exist yet:
- `onArtifactAction` backend routing — no `artifact.action` WebSocket message type
- No backend handler for initiating a tool call from a rendered artifact after the originating `use_ui_tool` has already returned
- No `artifact.action.started` / `artifact.action.completed` / `artifact.action.failed` events from server
- No optimistic update / rollback mechanism

### Design Decisions Still Open

1. **Who executes the action tool?** Recommended: a stateless action-handler agent that runs outside the original agent loop.
2. **How does the result update the artifact?** Recommended: server emits a new `ARTIFACT_EMITTED` event with updated payload.
3. **Optimistic updates?** Frontend `ArtifactActionsBar` supports loading state today; JSON Patch rollback is future work.

---

### GAP: New Ask Chat from Widget

The widget always resumes the last ask session (`askMessages` from context). There is currently no way to start a fresh ask conversation from within the widget in view mode or on non-chat routes.

**Expected experience:** User is in view mode (or on a non-chat route), opens the widget, wants a clean chat — not a continuation of a previous session.

**Recommended implementation (frontend-only, no backend change needed):**
- Add a "compose" / "new chat" icon button to the `PersistentChatWidget` header (between or alongside the two nav buttons)
- On click: `setAskMessages([])` to clear the inline chat; optionally `setActiveChatId(null)`
- Does NOT navigate — user stays in widget/view mode
- If the user later navigates to full Ask mode, ChatPage bootstraps a new session from the cleared state

**Owner:** `PersistentChatWidget.jsx` (frontend-only change, no backend or contract impact)

---

## 13 — Core vs App Ownership

### Core-Owned (stays in mozaiks-core)

- Surface state machine (`uiSurfaceReducer.js`, `ChatUIContext`)
- Desktop layout widths (`FluidChatLayout`)
- Mobile responsive primitives (`MobileArtifactDrawer`, `ArtifactPanel`)
- Event normalization (`mapSurfaceEventToAction`)
- Core primitive rendering pipeline (`PrimitiveRenderer`, `core.*` components)
- AG-UI event adapter (when built: `event_agui_adapter.py`)
- Transport events: `transport.snapshot`, `transport.replay_boundary`
- Widget runtime: `PersistentChatWidget`, `GlobalChatWidgetWrapper`, `useWidgetMode`

### App-Owned / Platform-Owned

- App-specific workflow components and artifacts
- Product navigation shells and branded compositions
- Domain-specific tools (e.g., calendar, billing artifacts)
- Workflow definitions and routing
- Theme tokens and branding
- Native distribution pipelines (iOS/Android, store submission)

**Rule:** If it is about *how* to render primitives or *how* to manage UI state → Core. If it is about *what* to show or *which* workflows exist → App.

---

## 14 — Capability Inventory (Do Not Rebuild)

All items verified against actual source files as of February 27, 2026.

| Capability | Status | Authoritative File |
|------------|--------|--------------------|
| Surface reducer (ask/workflow/view) | Done | `packages/frontend/chat-ui/src/state/uiSurfaceReducer.js` |
| Desktop layout widths + view mode | Done | `packages/frontend/chat-ui/src/components/chat/FluidChatLayout.jsx` |
| Mobile artifact drawer + fullscreen | Done | `packages/frontend/chat-ui/src/components/chat/MobileArtifactDrawer.jsx` |
| `ArtifactPanel` (mobile + desktop + view) | Done | `packages/frontend/chat-ui/src/components/chat/ArtifactPanel.jsx` |
| Core primitives (`core.*`) | Done — all 6 | `packages/frontend/chat-ui/src/primitives/` |
| `PrimitiveRenderer` dispatch | Done | `packages/frontend/chat-ui/src/primitives/PrimitiveRenderer.jsx` |
| `ChatUIContext` shared provider | Done | `packages/frontend/chat-ui/src/context/ChatUIContext.jsx` |
| `PersistentChatWidget` | Done | `packages/frontend/chat-ui/src/components/chat/PersistentChatWidget.jsx` |
| `useWidgetMode` | Done | `packages/frontend/chat-ui/src/hooks/useWidgetMode.jsx` |
| `GlobalChatWidgetWrapper` | Done | `packages/frontend/chat-ui/src/widget/GlobalChatWidgetWrapper.jsx` |
| Artifact action buttons (frontend) | Done | `packages/frontend/chat-ui/src/components/actions/ArtifactActionsBar.jsx` |
| Artifact action routing (backend) | **GAP** | Not yet built |
| New ask chat from widget (no navigate) | **GAP** | `PersistentChatWidget.jsx` — compose button not yet added |
| AG-UI event adapter | **GAP** | Not yet built (`event_agui_adapter.py` recommended) |
| `agui.lifecycle.*` frontend handling | Done | `uiSurfaceReducer.js` → `mapSurfaceEventToAction` |
| Run lifecycle (create/resume/get) | Done | `packages/python/mozaiks_core/mozaiks_core/api/app.py` |
| Event streaming (WebSocket) | Done | `packages/python/mozaiks_core/mozaiks_core/streaming/hub.py` |
| Event persistence + sequencing | Done | `packages/python/mozaiks_core/mozaiks_core/persistence/store.py` |
| Checkpoint save/load | Done | `packages/python/mozaiks_core/mozaiks_core/persistence/checkpoints.py` |
| `AIEngineFacade` (dynamic bridge) | Done | `packages/python/mozaiks_core/mozaiks_core/engine/facade.py` |
| Mock + AG2 runners | Done | `packages/python/mozaiks_ai/mozaiks_ai/adapters/` |

---

## Cross-References

- `docs/architecture/source-of-truth/WORKFLOW_ARCHITECTURE.md`
- `docs/architecture/source-of-truth/PROCESS_AND_EVENT_MAP.md`
- `docs/architecture/source-of-truth/EVENT_TAXONOMY.md`
- `docs/contracts/CORE_RUNTIME_CLIENT_CONTRACT_V1.md`

