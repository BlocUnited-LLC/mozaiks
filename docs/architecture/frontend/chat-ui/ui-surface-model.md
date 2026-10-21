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

`navigation/shellRoutes.js` selects the routes shared by `RouteRenderer` and the
widget wrapper. A declared routable `/` replaces the default `ChatPage`; an
explicit non-root `landing_spot` still redirects `/` first. The wrapper uses
React Router's matching precedence to suppress the launcher on the selected
`ChatPage`, including declared chat aliases. Custom app pages retain the
launcher, including root pages and static routes that outrank the core
`/chat/*` or `/app/*` fallback. Route authorization remains in `RouteWrapper`.
The wrapper hides during authentication loading, on login/callback surfaces,
and when the selected page requires a user or role the viewer lacks. Explicit
public pages (`meta.requiresAuth: false`) keep their launcher without a user.

## Event-driven surface changes

Frontend surfaces are not changed by ad hoc component logic alone. The reducer
reacts to event classes:

| Event family | Effect on surface |
| --- | --- |
| `chat.tool_call` with `display=artifact|view|fullscreen` | opens artifact surface |

This mapping lives in `mapSurfaceEventToAction(...)`.

## Session continuity

Navigation must not discard the active session.

Current implementation:

- `ChatUIContext` survives route changes
- `ChatPage` restores cached `askMessages` and `workflowMessages`
- artifact state is cached for restoration
- the widget shares the selected general conversation and message cache, using
  its own Ask carrier connection to restore that conversation from the server

A user can leave `/chat`, browse elsewhere, and reopen the same general
conversation in the widget. Workflow sessions remain in full ChatPage.

## Widget contract

The widget is the session entry point outside the full chat surface.

### Where it renders

| Context | Mounted by |
| --- | --- |
| non-chat routes | `GlobalChatWidgetWrapper` |
| ChatPage `view` layout | `ArtifactPanel` |

### What it shows

The widget is ask-mode only. It always renders `askMessages` from its own
general-mode connection; workflow sessions never render inside the widget.
Its WebSocket declares `transport_purpose=ask_carrier` at connect time, so the
backend never binds the widget's carrier chat to a workflow session,
auto-starts a workflow on it, or replays workflow history into it. Each
message sends the current route's `page_context` (the page's declared
description) and `page_path` (the route pattern). The backend uses
`page_path` to resolve the page's declared `meta.ask_context` actions —
read-only module actions whose results ground the ask agent's answers in live
page data. The client only ever names the page; the declarations and dispatch
are server-side.

The platform host resolves a workflow name only for workflow transport. An
explicit Ask carrier does not require any app-local workflow to exist; its
authentication, app scope and general-mode checks still apply.

Both schema-native `AppPageMeta` and custom-route metadata support the same
`AppAskContextAction` declarations. App loading checks declared module/action
references and ask eligibility; Factory acceptance checks the actual saved
module contracts, including custom-route manifest metadata. Unknown references,
actions without `ask_context_safe: true`, and actions requiring permissions
fail before promotion. Plan-only action names cannot authorize ask context.
Eligibility is independent of `api_surface`; it never makes an action public.
Runtime dispatch remains best-effort for operational failures, not a substitute
for this artifact validation. No user permissions or identity are added by it.

### Header contract

The expanded widget keeps a fixed header:

- left button (app display name, then theme brand name, then "Assistant"):
  opens the full ask chat page
- support button (🛟): opens the operator support form
- right logo button (same brand logo as the collapsed toggle): returns to the
  active workflow workspace when one exists — resolved from app/user-scoped
  session storage or the server's owned session snapshot/list. With no session,
  it opens the app's declared `meta.freshStart` entrypoint. Apps declaring neither
  a resumable session nor an entrypoint omit this button; unscoped storage does
  not authorize a guessed workflow destination.

The compose affordance for ask mode lives in the sub-header as `+ New conversation`.
It sends the existing `chat.start_general_chat` command and changes the visible
conversation only after `chat.general_session_created` acknowledges its server ID.
Older queued input must finish before starting another conversation; input typed
while the new acknowledgement is pending waits for the new conversation.

### Saved Ask history

`useWidgetAskWS` restores the server-acknowledged general conversation through
the authenticated `fetchGeneralChatTranscript` API. The widget and full ChatPage
share `session/generalTranscript.js` for message presentation. Carrier storage is
scoped by app and user; a stored general ID is only a request to the server, not
authority to display another user's transcript.

Queued input waits for successful history restoration. A missing, unavailable or
wrong-scope response shows **History unavailable** with **Retry history**, distinct
from a valid empty conversation. A closed socket offers **Retry connection**.
Late responses from an old identity, connection or selected conversation cannot
replace the current messages. Minimize/reopen keeps the same connection and does
not reload history. Live additions and optimistic messages survive a pending
restore; overlaps use the persisted ID forwarded as `metadata.general_message_id`,
never equal text or the carrier's transport sequence. Unscoped stream chunks are
ignored; accepted sends remain visibly pending until their scoped completions.
Queued drafts stay in the existing in-memory queue under their acknowledged
conversation ID. Switching conversations hides those drafts and holds their
delivery until that same conversation is selected again. Changing app/user
clears the queue; no new draft persistence is introduced.

### Route suppression

`GlobalChatWidgetWrapper` returns `null` for the selected full `ChatPage` route,
including the default root fallback, core `/chat/*` and `/app/*` fallbacks, and
declared chat aliases. A more specific declared app page uses its own widget.
Unresolved routes and authentication surfaces also suppress the launcher.

That keeps the widget from competing with the full chat surface.

## Canonical implementation files

- `chat-ui/src/state/uiSurfaceReducer.js`
- `chat-ui/src/context/ChatUIContext.jsx`
- `chat-ui/src/pages/ChatPage.js`
- `chat-ui/src/widget/GlobalChatWidgetWrapper.jsx`
- `chat-ui/src/components/chat/PersistentChatWidget.jsx`
