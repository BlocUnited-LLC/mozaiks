# Mobile Architecture

Mozaiks mobile best practice is a true native client built on top of the shared chat/runtime core.

Use the portable logic from `@mozaiks/chat-ui/core`, inject host-specific behavior through `@mozaiks/chat-ui/platform`, and build a dedicated React Native renderer on top.

## How It Works

The runtime backend stays the same whether the client is web or native:

- same FastAPI routes
- same WebSocket contract
- same workflow orchestration
- same auth semantics

The only thing that changes is the render layer.

## Recommended Structure

- Keep the current web app for browser/desktop use.
- Add a dedicated React Native client for mobile.
- Share transport, session state, auth token lookup, and workflow logic through `@mozaiks/chat-ui/core`.
- Keep mobile-specific UI in native components instead of wrapping the browser UI in a WebView shell.

For users, the main mobile configuration surface is `platform/app.json`.

That file owns mobile platform behavior such as:

- whether mobile is enabled
- mobile auth provider selection
- native redirect settings and scopes

App-level AI boot behavior is separate and lives in `platform/config/ai.json`.

That file owns:

- `chat.startup_mode`
- `workflows.entry_point`

Example:

```json
{
    "chat": {
        "startup_mode": "ask"
    },
    "workflows": {
        "entry_point": "GreenRoom"
    }
}
```

Do not move workflow boot defaults into `platform/app.json`.

The `clients/mobile` directory is repo-owned implementation. It exists so the repo can generate and maintain the native iOS/Android shell, but most users should not need to edit it directly.

## Why This Is The Best-Practice Path

- It keeps web and mobile renderers intentionally separate.
- It avoids shipping the browser UI through a wrapper when the mobile UX needs native behavior.
- It preserves one backend/runtime contract instead of splitting application logic.
- It matches the current chat-ui architecture, which now has an explicit portable core boundary.

## Designing Custom Workflow UI Components for Mobile

Workflows can emit custom UI components via `chat.tool_call` events — for example a results card, a form, or a data visualisation rendered inline in the chat.

!!! warning "Building for mobile? Write your UI components in React Native."
    If your app targets iOS or Android, author all custom workflow UI components in React Native primitives (`View`, `Text`, `TextInput`, etc.) rather than browser JSX. Components written in React Native work across web (via React Native Web) and native — components written in browser JSX only work in the browser.

    If you add a browser JSX component and later add mobile targets, that component will display as a fallback card on mobile — functional, but unstyled.

    **Practical rule:** decide on your targets before you start building UI components. The core chat loop, WebSocket hooks, and agent logic require no changes regardless of target — only the visual components are affected.

## Next Steps

- [React Native Core Integration](05-react-native-core.md) — shared core + native renderer path

## Keycloak vs OIDC

If the terminology is unfamiliar, use this shortcut:

- **Keycloak** = the auth product/server
- **OIDC** = the protocol it uses

If your mobile app signs in directly against Keycloak using a native flow, treat that as `keycloak-native` in `platform/app.json`.

That does not change which workflow the app boots into. The app-level default
workflow remains `platform/config/ai.json -> workflows.entry_point`.

---

!!! tip "Keep one runtime contract"
    The clean architectural goal is shared backend/runtime logic with separate renderer layers, not a second mobile-specific backend path.

