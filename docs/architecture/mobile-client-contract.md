# Mobile Client Contract (OSS Runtime)

This document defines the runtime contract a mobile client (iOS/Android) must follow to work with Mozaiks OSS core.

Scope:
- Runtime transport and auth contract.
- Workflow/chat lifecycle contract.
- Event streaming and resume contract.
- Shared-core frontend integration contract for non-browser hosts.

Out of scope:
- Platform-only services (hosted builds, billing, marketing, proprietary control plane).
- Native UI framework choices (SwiftUI, Kotlin, React Native, Flutter, etc.).

## 1) Core Contract Invariants

Every runtime call is scoped by these identifiers:
- `app_id`: tenant/application boundary.
- `user_id`: authenticated principal boundary.
- `chat_id`: run/session boundary.
- `workflow_name`: execution strategy/workflow boundary.

Never reuse identifiers across tenants. All client state and caches should be keyed by at least `app_id + user_id + chat_id`.

## 2) Auth Contract

HTTP:
- Send `Authorization: Bearer <token>` on protected routes.

WebSocket:
- Connect with `access_token` query parameter:
  - `ws://<host>/ws/{workflow_name}/{app_id}/{chat_id}/{user_id}?access_token=<jwt>`

Auth-disabled local mode (`AUTH_ENABLED=false`) is supported for local development, but production clients should always run with auth enabled.

## 3) Required HTTP Endpoints

## Workflow discovery
- `GET /api/workflows`
  - Returns workflow config map keyed by `workflow_name`.
  - Use `entry_point: true` when present for default launch selection. Canonical source is `app/config/ai.json`.

## Start chat
- `POST /api/chats/{app_id}/{workflow_name}/start`
- Body:
  - `user_id` (required in body; validated against auth principal)
  - `client_request_id` (recommended for idempotency)
  - `force_new` (optional)
- Response includes:
  - `chat_id`
  - `websocket_url` (path form)
  - `cache_seed`
  - `reused` boolean

## Workflow transport metadata
- `GET /api/workflows/{workflow_name}/transport`
- Returns canonical WS/input route patterns.

## User message (HTTP fallback)
- `POST /chat/{app_id}/{chat_id}/{user_id}/input`
- Body:
  - `message`
  - `workflow_name`

## UI tool response
- `POST /api/tool-call/respond`
- Body:
  - `event_id`
  - `response_data`

## Attachment upload
- `POST /api/chat/upload`
- Multipart form fields:
  - `file`, `appId`, `userId`, `chatId`
  - optional: `intent`, `bundle_path`

## Chat meta (restore/rejoin)
- `GET /api/chats/meta/{app_id}/{workflow_name}/{chat_id}`
- Returns `cache_seed`, `last_artifact`, status, and artifact restore metadata.

## 4) WebSocket Contract

## Connect
- Endpoint:
  - `/ws/{workflow_name}/{app_id}/{chat_id}/{user_id}`

After connection, runtime emits initial chat metadata event so clients can align local cache and artifact state.

## Client → Runtime messages

## User input
```json
{
  "type": "user.input.submit",
  "chat_id": "<chat_id>",
  "text": "Hello",
  "context": {}
}
```

Optional fields:
- none

## UI tool response
```json
{
  "type": "tool_call_response",
  "tool_call_id": "<tool_call_id>",
  "response": {}
}
```

## Resume request
```json
{
  "type": "client.resume",
  "chat_id": "<chat_id>",
  "lastClientIndex": 42
}
```

## Mode/workflow control (supported)
- `chat.switch_workflow`
- `chat.enter_general_mode`
- `chat.start_general_chat`
- `chat.start_workflow`
- `chat.start_workflow_batch`

## Runtime → Client envelope

Runtime streams JSON envelopes of this form:
```json
{
  "type": "chat.text",
  "data": {},
  "timestamp": "2026-03-08T00:00:00Z"
}
```

Common `type` values:
- `chat.text`
- `chat.print`
- `chat.input_ack`
- `chat.tool_call`
- `chat.tool_call_complete`
- `chat.tool_call_dismiss`
- `chat.tool_response`
- `chat.usage_delta`
- `chat.usage_summary`
- `chat.run_complete`
- `chat.resume_boundary`
- `chat.custom_event`
- `chat.error`
- `chat.attachment_uploaded`
- Control/ack events such as `ack.input`, `ack.tool_call_response`, `chat.mode_changed`, `chat.workflow_started`

Note:
- A `chat_meta` event type is currently emitted (not namespaced as `chat.chat_meta`).
- Response-required runtime UI should expect `chat.tool_call` with
  `awaiting_response=true` and possibly `interaction_type=input_request`, and
  should answer with `tool_call_response`. If the tool call carries
  `display=composer`, a chat-style client should route the user's normal
  composer submission into that response instead of opening a second text box.

## 5) Resume and Replay Contract

Use `client.resume` after reconnect with the last applied client index.

Runtime replies with replayed events followed by a `chat.resume_boundary` summary containing:
- `replayed_messages`
- `last_message_index`
- `total_messages`
- `resume_state`

Client behavior:
- Treat replay as authoritative for message timeline.
- Apply events idempotently.
- Replace local optimistic state if boundary indicates divergence.

## 6) Error Contract

Errors are emitted as:
```json
{
  "type": "chat.error",
  "data": {
    "message": "...",
    "error_code": "..."
  }
}
```

Clients should:
- Show user-safe message.
- Log `error_code`.
- Keep WS session alive unless server closes the socket.

## 7) Mobile Implementation Guidance

Recommended startup flow:
1. Fetch workflows.
2. Select workflow (`entry_point` if present).
3. Start chat (`/start`) with `client_request_id`.
4. Open WS with token.
5. Consume events and render UI.
6. On disconnect, reconnect and send `client.resume`.

Recommended reliability policy:
- Exponential reconnect backoff.
- Idempotent chat start (`client_request_id`).
- Local pending action queue for transient network failures.
- Strict tenant-safe keying (`app_id + user_id + chat_id`).

## Shared Chat Core For React Native

The `chat-ui` package now exposes a portable shared-core surface for non-browser hosts.

Use:

- `@mozaiks/chat-ui/core` for shared transport/state/hooks/providers.
- `@mozaiks/chat-ui/platform` to inject host-specific behavior.

Required host responsibilities:

- Provide synchronous key-value storage.
- Provide access token lookup.
- Provide canonical HTTP and WebSocket base URLs.
- Render native equivalents for any workflow UI tools used by the app.

Important constraint:

- Do not import browser-only adapters such as Keycloak or mock browser auth into a React Native host.
- The shared core is portable; the default web renderer is not.

Minimal startup shape:

```js
import { configurePlatform } from '@mozaiks/chat-ui/platform';
import { ChatUIProvider } from '@mozaiks/chat-ui/core';

configurePlatform({
  storage: {
    getItem: (key) => mmkv.getString(key) ?? null,
    setItem: (key, value) => mmkv.set(key, value),
    removeItem: (key) => mmkv.delete(key),
  },
  auth: {
    getAccessToken: () => tokenStore.currentToken ?? null,
  },
  getBaseUrls: () => ({
    httpUrl: 'https://api.example.com',
    wsUrl: 'wss://api.example.com',
  }),
});
```

The React Native host then mounts its own native UI around the shared provider/hook layer.

## 8) Important Caveat: UI Tool Renderers

Workflows and runtime orchestration do not need to change for mobile.

However, when runtime emits `chat.tool_call` events for UI tools, mobile clients must provide renderer mappings for the emitted component identifiers (for example `ActionPlan`, `AgentAPIKeyInput`, etc.).

Implications:
- Backend/workflow logic stays the same.
- Mobile UI must implement equivalent renderers for any workflow-specific UI tool components used by the app.
- If a component is unknown on mobile, client should degrade safely (fallback card, prompt-only flow, or explicit "unsupported component" UI).

## 9) Repo Strategy (Recommended)

Mobile code does not need a separate backend repo. You have two common options:

1. Monorepo (recommended initially)
- Keep mobile client under this repo (for example `clients/mobile`).
- Pros: shared contracts/docs, easier synchronized changes, simpler OSS onboarding.

2. Separate mobile repo
- Use when mobile team/release cadence is fully independent.
- Requires stronger versioning and CI checks against runtime contract changes.

Recommended default for Mozaiks right now:
- Keep runtime + web + mobile in one repo first.
- Split only when release or ownership boundaries force it.
